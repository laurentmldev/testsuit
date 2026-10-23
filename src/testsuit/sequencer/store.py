"""State store (README section 9): runs, locks, component states, maintenance, usage, events, in SQLite.

SQLite is the arbiter between a daemon and ``tsq run --local`` runs on the same storage: every
lock is taken in one ``BEGIN IMMEDIATE`` transaction, all or nothing.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from testsuit.sequencer.util import now

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, name TEXT, testdef TEXT, zip TEXT, dir TEXT,
    priority INTEGER, state TEXT, verdict TEXT, reason TEXT,
    submitted_at TEXT, activated_at TEXT, ended_at TEXT,
    conform INTEGER, nonconform TEXT, waiting_for TEXT, attempts INTEGER DEFAULT 0,
    binding TEXT, phase TEXT, results TEXT, expected_baseline TEXT, warnings TEXT,
    steps TEXT, local INTEGER DEFAULT 0, abort TEXT, owner TEXT
);
CREATE TABLE IF NOT EXISTS locks (
    run_id TEXT, kind TEXT, resource TEXT, mode TEXT, since TEXT,
    PRIMARY KEY (run_id, kind, resource)
);
CREATE TABLE IF NOT EXISTS resources (
    kind TEXT, id TEXT, state TEXT, expected TEXT, probed_at TEXT, detail TEXT,
    maintenance INTEGER DEFAULT 0, hold INTEGER DEFAULT 0, note TEXT,
    PRIMARY KEY (kind, id)
);
CREATE TABLE IF NOT EXISTS usage (
    resource TEXT, testdef TEXT, count INTEGER, last_at TEXT,
    PRIMARY KEY (resource, testdef)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, data TEXT
);
"""

ACTIVE_STATES = ("waiting_resources", "reserved", "selftest", "running", "cleaning")
QUEUED_STATES = ("queued", "waiting_resources")
JSON_FIELDS = ("nonconform", "binding", "expected_baseline", "warnings", "steps")


class Store:

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._mutex = threading.RLock()
        with self._mutex:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _exec(self, sql: str, args: Iterable = ()) -> sqlite3.Cursor:
        with self._mutex:
            return self._db.execute(sql, tuple(args))

    # ── runs ──────────────────────────────────────────────────────────────
    @staticmethod
    def _run(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        run = dict(row)
        for name in JSON_FIELDS:
            run[name] = json.loads(run[name]) if run.get(name) else ([] if name in ("nonconform", "warnings", "steps") else None)
        run["conform"] = None if run["conform"] is None else bool(run["conform"])
        run["local"] = bool(run["local"])
        return run

    def add_run(self, **fields: Any) -> None:
        fields.setdefault("submitted_at", now())
        fields.setdefault("state", "queued")
        for name in JSON_FIELDS:
            if name in fields and fields[name] is not None:
                fields[name] = json.dumps(fields[name])
        names = ", ".join(fields)
        self._exec(f"INSERT INTO runs ({names}) VALUES ({', '.join('?' * len(fields))})", fields.values())

    def update_run(self, run_id: str, **fields: Any) -> None:
        for name in JSON_FIELDS:
            if name in fields and fields[name] is not None:
                fields[name] = json.dumps(fields[name])
        if "conform" in fields and fields["conform"] is not None:
            fields["conform"] = int(bool(fields["conform"]))
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE runs SET {sets} WHERE id = ?", [*fields.values(), run_id])

    def get_run(self, run_id: str) -> dict | None:
        row = self._exec("SELECT * FROM runs WHERE id = ?", [run_id]).fetchone()
        if row is None:              # unique prefix
            rows = self._exec("SELECT * FROM runs WHERE id LIKE ?", [run_id + "%"]).fetchall()
            row = rows[0] if len(rows) == 1 else None
        return self._run(row)

    def runs(self, states: Iterable[str] | None = None, limit: int = 200) -> list[dict]:
        if states:
            states = list(states)
            rows = self._exec(f"SELECT * FROM runs WHERE state IN ({', '.join('?' * len(states))}) "
                              "ORDER BY submitted_at DESC LIMIT ?", [*states, limit]).fetchall()
        else:
            rows = self._exec("SELECT * FROM runs ORDER BY submitted_at DESC LIMIT ?", [limit]).fetchall()
        return [self._run(r) for r in rows]

    def queue(self) -> list[dict]:
        rows = self._exec(f"SELECT * FROM runs WHERE state IN ({', '.join('?' * len(QUEUED_STATES))}) "
                          "ORDER BY priority DESC, submitted_at ASC", QUEUED_STATES).fetchall()
        return [self._run(r) for r in rows]

    # ── locks ─────────────────────────────────────────────────────────────
    def try_lock(self, run_id: str, wanted: list[tuple[str, str, str]]) -> list[str]:
        """Take every lock in wanted ((kind, resource, mode)) at once, or none.

        :return: the conflicts (empty when the locks were taken)
        """
        with self._mutex:
            db = self._db
            db.execute("BEGIN IMMEDIATE")
            try:
                conflicts = []
                for kind, resource, mode in wanted:
                    rows = db.execute("SELECT run_id, mode FROM locks WHERE kind = ? AND resource = ? AND run_id != ?",
                                      (kind, resource, run_id)).fetchall()
                    for row in rows:
                        if mode == "exclusive" or row["mode"] == "exclusive":
                            conflicts.append(f"{kind} {resource} held by run {row['run_id']}")
                    maint = db.execute("SELECT maintenance, hold FROM resources WHERE kind = ? AND id = ?",
                                       (kind, resource)).fetchone()
                    if maint is not None and (maint["maintenance"] or maint["hold"]):
                        conflicts.append(f"{kind} {resource} is {'in maintenance' if maint['maintenance'] else 'held'}")
                if conflicts:
                    db.execute("ROLLBACK")
                    return conflicts
                stamp = now()
                for kind, resource, mode in wanted:
                    db.execute("INSERT OR REPLACE INTO locks (run_id, kind, resource, mode, since) VALUES (?, ?, ?, ?, ?)",
                               (run_id, kind, resource, mode, stamp))
                db.execute("COMMIT")
                return []
            except Exception:
                db.execute("ROLLBACK")
                raise

    def release(self, run_id: str, keep: Iterable[tuple[str, str]] = ()) -> list[tuple[str, str]]:
        """Release a run's locks, except those in keep ((kind, resource)); return what was released."""
        keep = set(keep)
        held = [(r["kind"], r["resource"]) for r in self._exec("SELECT kind, resource FROM locks WHERE run_id = ?", [run_id])]
        released = [h for h in held if h not in keep]
        for kind, resource in released:
            self._exec("DELETE FROM locks WHERE run_id = ? AND kind = ? AND resource = ?", [run_id, kind, resource])
        return released

    def force_release(self, kind: str, resource: str) -> list[str]:
        runs = [r["run_id"] for r in self._exec("SELECT run_id FROM locks WHERE kind = ? AND resource = ?", [kind, resource])]
        self._exec("DELETE FROM locks WHERE kind = ? AND resource = ?", [kind, resource])
        return runs

    def locks(self, run_id: str | None = None) -> list[dict]:
        if run_id:
            return [dict(r) for r in self._exec("SELECT * FROM locks WHERE run_id = ?", [run_id])]
        return [dict(r) for r in self._exec("SELECT * FROM locks")]

    def holders(self) -> dict[tuple[str, str], list[tuple[str, str]]]:
        """(kind, resource) -> [(run_id, mode)]"""
        result: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for r in self._exec("SELECT * FROM locks"):
            result.setdefault((r["kind"], r["resource"]), []).append((r["run_id"], r["mode"]))
        return result

    # ── resources: states, maintenance, hold ───────────────────────────────
    def _ensure(self, kind: str, resource: str) -> None:
        self._exec("INSERT OR IGNORE INTO resources (kind, id) VALUES (?, ?)", [kind, resource])

    def set_state(self, resource: str, state: str, detail: str = "") -> str | None:
        """Record a probed component state; return the previous one."""
        self._ensure("component", resource)
        row = self._exec("SELECT state FROM resources WHERE kind = 'component' AND id = ?", [resource]).fetchone()
        self._exec("UPDATE resources SET state = ?, probed_at = ?, detail = ? WHERE kind = 'component' AND id = ?",
                   [state, now(), detail, resource])
        return row["state"] if row else None

    def set_expected(self, resource: str, state: str) -> None:
        self._ensure("component", resource)
        self._exec("UPDATE resources SET expected = ? WHERE kind = 'component' AND id = ?", [state, resource])

    def set_flag(self, kind: str, resource: str, flag: str, value: bool, note: str = "") -> None:
        assert flag in ("maintenance", "hold")
        self._ensure(kind, resource)
        self._exec(f"UPDATE resources SET {flag} = ?, note = ? WHERE kind = ? AND id = ?",
                   [int(value), note, kind, resource])

    def resources(self, kind: str | None = None) -> dict[tuple[str, str], dict]:
        sql, args = "SELECT * FROM resources", []
        if kind:
            sql, args = sql + " WHERE kind = ?", [kind]
        return {(r["kind"], r["id"]): dict(r) for r in self._exec(sql, args)}

    # ── usage, events ─────────────────────────────────────────────────────
    def add_usage(self, resource: str, testdef: str) -> None:
        self._exec("INSERT INTO usage (resource, testdef, count, last_at) VALUES (?, ?, 1, ?) "
                   "ON CONFLICT(resource, testdef) DO UPDATE SET count = count + 1, last_at = excluded.last_at",
                   [resource, testdef, now()])

    def usage(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for r in self._exec("SELECT * FROM usage"):
            entry = result.setdefault(r["resource"], {"count": 0, "by_testdef": {}})
            entry["count"] += r["count"]
            entry["by_testdef"][r["testdef"]] = r["last_at"]
        return result

    def add_event(self, kind: str, data: dict) -> int:
        cursor = self._exec("INSERT INTO events (ts, kind, data) VALUES (?, ?, ?)", [now(), kind, json.dumps(data, default=str)])
        return cursor.lastrowid

    def events(self, since: int = 0, limit: int = 500) -> list[dict]:
        rows = self._exec("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", [since, limit]).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "kind": r["kind"], "data": json.loads(r["data"])} for r in rows]
