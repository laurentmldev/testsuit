# Copyright (C) 2026 Laurent Millet-Lacombe
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of testsuit.
# testsuit is free software: you can redistribute it and/or modify it under the terms of the GNU Affero
# General Public License as published by the Free Software Foundation, either version 3 of the License,
# or (at your option) any later version.
# testsuit is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General
# Public License for more details.
# You should have received a copy of the GNU Affero General Public License along with testsuit. If not,
# see <https://www.gnu.org/licenses/>.

"""Issues bound to the results of the sequencer's runs (SPEC section 11.3).

A **binding** ties an issue of an issue source (testsuit.issues) to a target of a test definition: a check, a step
or the verdict, by its fingerprint (testsuit.issues.results.fingerprint), so that it is a **known issue** of every
run of it that fails there, until it is unbound. Bindings are the sequencer's, not the tests repository's: they
change as issues are opened and closed, without commits. ``storage.results/issues/bindings.yml``::

    bindings:
      - {id: 1, fingerprint: 3f2a..., testdef: short, target: {kind: check, step: exploit:criteria, ...},
         issue: {source: bench, id: '42', key: 'bench#42', title, state, url}, run: <run it was bound from>,
         created: true, by, at, removed: {by, at, reason}}

The issue sources of a run are those of its datapack (``definition/issues.yml``, written at generation): its
credentials are references, read on the sequencer's host. Issue states are read from the tools at most every few
minutes (a cache), and kept in the bindings as last seen.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import logging
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from testsuit.sequencer import review as rv
from testsuit.sequencer.util import SequencerError, now
from testsuit.issues import results as iss
from testsuit.issues.model import OPEN, Issue, IssueError
from testsuit.issues.backend import IssueBackend, reporting
from testsuit.issues.filter import parse_time
from testsuit.netguard import is_down_error
from testsuit.issues.sources import SNAPSHOT, IssueSources

log = logging.getLogger("testsuit.sequencer")
TTL = 300.0                                  # seconds an issue's state is taken as known
BINDINGS = "bindings.yml"
LIST_LIMIT = 10000                               # issues of a source listed at most


FULL_EVERY = 6 * 3600.0                          # seconds: a list read whole again (issues deleted or moved)
SINCE_MARGIN = 120                               # seconds before the last reading a refresh starts from


def _hhmm(iso: str | None) -> str:
    at = parse_time(iso or "")
    return at.astimezone().strftime("%H:%M") if at else "?"
ERROR_RETRY = 30.0                               # seconds before a list that failed is read again (unless asked)


class _List:
    """The issues of a source as read (by id), and how its reading goes; kept on disk for a restart."""

    def __init__(self, key: tuple) -> None:
        self.key = key
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.done.set()
        self.issues: dict[str, Issue] = {}
        self.read_at: str | None = None          # when the last complete reading started (ISO, UTC)
        self.full_at = 0.0                       # when the last whole reading ended (epoch)
        self.checked = 0.0                       # when it was last read, or failed (monotonic)
        self.complete = False
        self.loading = False
        self.read, self.total, self.exact = 0, None, True
        self.mode = ""                           # of the reading going on: first, whole, or since (what changed)
        self.error = ""
        self.down = False                        # the error: its server down
        self.version = 0

    def state(self) -> dict[str, Any]:
        issues = sorted(self.issues.values(), key=lambda i: str(i.updated or ""), reverse=True)
        return {"issues": issues, "loading": self.loading, "read": self.read, "total": self.total, "exact": self.exact,
                "complete": self.complete, "mode": self.mode, "read_at": self.read_at, "error": self.error, "version": self.version}

    @property
    def store(self) -> Path:
        from testsuit.netguard import LastKnown
        return LastKnown("issue-lists", self.key).path

    def load(self) -> None:
        try:
            data = json.loads(self.store.read_text())
            self.issues = {str(d["id"]): Issue.from_dict(d) for d in data.get("issues") or []}
            self.read_at, self.full_at = data.get("read_at"), float(data.get("full_at") or 0)
            self.complete = bool(self.read_at)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("issue list %s not read: %s", self.store, e)

    def save(self) -> None:
        with self.lock:
            data = {"read_at": self.read_at, "full_at": self.full_at,
                    "issues": [i.as_dict(body=True) for i in self.issues.values()]}
        store = self.store
        try:
            store.parent.mkdir(parents=True, exist_ok=True)
            tmp = store.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data, default=str))
            tmp.replace(store)
        except OSError as e:
            log.warning("issue list %s not written: %s", store, e)


class Issues:
    """The bindings of the sequencer, and the calls to the issue trackers of its runs."""

    def __init__(self, seq) -> None:
        self.seq = seq
        self._lock = threading.Lock()
        self._issues: dict[tuple[str, str], tuple[float, Issue | None]] = {}     # (backend identity, id)
        self._lists: dict[tuple[str, str, str], _List] = {}                     # (backend identity, state, filter)
        self.stale: dict[str, str] = {}          # source -> why its list is the one last read (its server down)
        self._sources: dict[str, IssueSources | None] = {}                     # run id

    # ── bindings ───────────────────────────────────────────────────────
    @property
    def path(self) -> Path:
        return self.seq.config.storage_path("results") / "issues" / BINDINGS

    def bindings(self, removed: bool = False) -> list[dict]:
        old = self.path.parent.parent / "tickets" / BINDINGS          # where they were before the rename
        if not self.path.exists() and old.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            old.rename(self.path)
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return []
        except (OSError, yaml.YAMLError) as e:
            raise SequencerError(f"cannot read {self.path}: {e}") from e
        out = [b for b in data.get("bindings") or [] if isinstance(b, dict)]
        return out if removed else [b for b in out if not b.get("removed")]

    def _save(self, bindings: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump({"bindings": bindings}, sort_keys=False, allow_unicode=True), encoding="utf-8")
        tmp.replace(self.path)

    def known(self, testdef: str, target: dict) -> list[dict]:
        fp = iss.fingerprint(testdef, target)
        return [b for b in self.bindings() if b.get("fingerprint") == fp]

    # ── the sources of a run ───────────────────────────────────────────
    def sources(self, run: dict) -> IssueSources | None:
        """The issue sources of a run's datapack (None: it has none)."""
        if run["id"] not in self._sources:
            data = self.seq.read_run_file(run, f"definition/{SNAPSHOT}")
            if data is None:                     # a datapack generated before the rename
                data = self.seq.read_run_file(run, "definition/tickets.yml")
            found = None
            if data is not None:
                try:
                    # file: credentials relative to tsq.yml's folder (on the sequencer's host)
                    found = IssueSources(yaml.safe_load(data) or {}, self.seq.config.base_dir,
                                          f"run {run['id']}: definition/{SNAPSHOT}")
                except (IssueError, yaml.YAMLError) as e:
                    raise SequencerError(str(e)) from e
            self._sources[run["id"]] = found
        return self._sources[run["id"]]

    def _require_sources(self, run: dict) -> IssueSources:
        sources = self.sources(run)
        if sources is None:
            raise SequencerError(f"run {run['id']}: its datapack names no issue source (no issues.yml in its tests "
                                 "repository when it was generated)")
        return sources

    # ── issues, cached ─────────────────────────────────────────────────
    def issue(self, sources: IssueSources, source: str, id: str, refresh: bool = False) -> Issue | None:
        backend = sources.backend(source)
        key = (backend.identity, str(id))
        hit = self._issues.get(key)
        if hit and not refresh and time.monotonic() - hit[0] < TTL:
            return hit[1]
        try:
            found = backend.get([str(id)]).get(str(id))
        except IssueError as e:
            if hit and is_down_error(e):            # its server down: as last read
                return hit[1]
            raise
        if found is not None:
            found.source = source
        self._issues[key] = (time.monotonic(), found)
        return found

    def _list(self, sources: IssueSources, source: str, state: str | None) -> tuple[_List, IssueBackend]:
        backend = sources.backend(source)
        f = sources.filters.get(source)
        key = (backend.identity, str(state), json.dumps(f.spec if f else {}, sort_keys=True, default=str))
        with self._lock:
            lst = self._lists.get(key)
            if lst is None:
                lst = self._lists[key] = _List(key)
                lst.load()                           # as last read, before a restart
        return lst, backend

    def listing(self, sources: IssueSources, source: str, state: str | None = OPEN, refresh: bool = False,
                limit: int = LIST_LIMIT) -> dict[str, Any]:
        """The issues of a source as known now, without waiting: read in the background when not read yet, or read
        more than TTL ago (then only those updated since), or ``refresh``. ``{issues, loading, read, total, exact,
        complete, read_at, error, version}``; ``issues`` grow while the first reading goes."""
        lst, backend = self._list(sources, source, state)
        with lst.lock:
            due = refresh or not lst.complete or time.monotonic() - lst.checked > TTL
            if due and not lst.loading and not (lst.error and not refresh and time.monotonic() - lst.checked < ERROR_RETRY):
                lst.loading = True
                lst.done.clear()
                since = lst.read_at if (not refresh and lst.complete and lst.read_at and backend.supports_since
                                        and time.time() - lst.full_at < FULL_EVERY) else None
                lst.mode = "first" if not lst.complete else "since" if since else "whole"
                lst.read, lst.total, lst.exact = 0, None, True
                if not lst.complete:
                    lst.issues = {}
                threading.Thread(target=self._read, args=(lst, backend, source, state, limit, since),
                                 name=f"issues-{source}", daemon=True).start()
            out = lst.state()
        out["issues"] = [_renamed(i, source) for i in out["issues"]]
        if lst.error and lst.complete and lst.down:
            self.stale[source] = f"{lst.error} (the list shown was read at {_hhmm(lst.read_at)})"
        else:
            self.stale.pop(source, None)
        return out

    def listed(self, sources: IssueSources, source: str, state: str | None = OPEN, refresh: bool = False,
               limit: int = LIST_LIMIT) -> list[Issue]:
        """The issues of a source (open ones: the default), waiting for them when not read yet (or ``refresh``); a list
        read before is given at once (and read again in the background when older than TTL)."""
        lst, _ = self._list(sources, source, state)
        first = self.listing(sources, source, state, refresh, limit)
        if first["loading"] and (refresh or not first["complete"]):
            lst.done.wait()
            first = self.listing(sources, source, state, False, limit)
        if first["error"] and not first["complete"]:
            raise IssueError(first["error"])
        return first["issues"]

    def _read(self, lst: _List, backend: IssueBackend, source: str, state: str | None, limit: int,
              since: str | None) -> None:
        """Read a list: whole (the first time, every FULL_EVERY, or asked), else only the issues updated since the last
        reading (closed, or out of the filter since, they leave the list)."""
        start = datetime.now(timezone.utc)
        fill = lst.mode == "first"                    # the first reading: shown as it goes

        def page(issues: list[Issue], read: int, total: int | None, exact: bool = True) -> None:
            with lst.lock:
                lst.read, lst.total, lst.exact = read, total, exact
                if fill:
                    for i in issues:
                        lst.issues[i.id] = i
                    lst.version += 1

        try:
            with reporting(page):
                if since:
                    after = (parse_time(since) - timedelta(seconds=SINCE_MARGIN)).isoformat(timespec="seconds")
                    raw = getattr(backend, "raw_search", backend.search)
                    f = getattr(backend, "filter", None)
                    changed = raw(state=None, limit=limit, since=after)
                else:
                    found = backend.search(state=state, limit=limit)
        except Exception as e:                       # noqa: BLE001 - told to whoever looks at the list
            with lst.lock:
                lst.error, lst.down = str(e), is_down_error(e)
                lst.checked = time.monotonic()
                lst.loading = False
                lst.version += 1
            log.warning("issue source '%s': %s", source, e)
            lst.done.set()
            return
        with lst.lock:
            if since:
                for i in changed:
                    if (state and i.state != state) or (f is not None and not f.match(i)):
                        lst.issues.pop(i.id, None)
                    else:
                        lst.issues[i.id] = i
                lst.read = len(changed)
            else:
                lst.issues = {i.id: i for i in found}
                lst.full_at = time.time()
                lst.read = lst.total = len(found)
                lst.exact = True
            lst.read_at = start.isoformat(timespec="seconds")
            lst.complete, lst.error, lst.loading = True, "", False
            lst.checked = time.monotonic()
            lst.version += 1
            items = list(lst.issues.values())
        now_ = time.monotonic()
        for i in items:
            self._issues[(backend.identity, i.id)] = (now_, i)
        lst.save()
        lst.done.set()

    def _refreshed(self, binding: dict, sources: IssueSources | None, refresh: bool = False) -> dict:
        """A binding with its issue as the tool gives it now (as last seen when the tool cannot be reached)."""
        brief = binding.get("issue") or {}
        if sources is None or brief.get("source") not in sources.names:
            return binding
        try:
            found = self.issue(sources, brief["source"], brief["id"], refresh)
        except IssueError as e:
            return {**binding, "error": str(e)}
        if found is None:
            return {**binding, "issue": {**brief, "state": "missing"}}
        fresh = found.brief() | {"assignees": found.assignees, "labels": found.labels}
        if fresh.get("state") != brief.get("state") or fresh.get("title") != brief.get("title"):
            with self._lock:                         # kept as last seen
                all_ = self.bindings(removed=True)
                for b in all_:
                    if b.get("id") == binding.get("id"):
                        b["issue"] = {k: fresh[k] for k in ("source", "id", "key", "title", "state", "url")}
                        b["seen_at"] = now()
                self._save(all_)
        return {**binding, "issue": fresh}

    # ── a run's targets and their issues ───────────────────────────────
    def targets(self, run: dict) -> list[dict]:
        """What of a run (as reviewed) can have an issue: its checks not met, steps failed or in error, its verdict
        when not succeeded; each with its status and fingerprint."""
        testdef = str(run.get("testdef") or run.get("name") or "")
        out = []
        verdict = run.get("verdict") or run.get("state")
        if run.get("state") in rv.ENDED and verdict != "succeeded":
            out.append({"target": {"kind": "run"}, "status": verdict})
        for s in run.get("steps") or []:
            state = s.get("state")
            for c in (s.get("exploit") or {}).get("checks") or []:
                status = str(c.get("status") or "").upper()
                if status not in ("SUCCESS", "ACCEPTED"):
                    out.append({"target": rv.check_target(s, c), "status": status.lower()})
            if state in ("failed", "error"):
                out.append({"target": {"kind": "step", "step": rv.step_key(s)}, "status": state})
        for t in out:
            t["key"] = rv.target_key(t["target"])
            t["label"] = rv.target_label(t["target"])
            t["fingerprint"] = iss.fingerprint(testdef, t["target"])
        return out

    def run_view(self, run_id: str, refresh: bool = False) -> dict:
        """The issues of a run: its sources, its targets that failed with their known issues (bound to the same
        check, step or verdict of its test definition, in this run or another), and every binding of its test
        definition."""
        run = self.seq.reviewed(self.seq.require_run(run_id))
        testdef = str(run.get("testdef") or run.get("name") or "")
        out: dict[str, Any] = {"run": run["id"], "testdef": testdef, "sources": [], "targets": [], "bindings": []}
        try:
            sources = self.sources(run)
        except SequencerError as e:
            sources, out["error"] = None, str(e)
        if sources is not None:
            out["sources"] = [sources.card(n) for n in sources.names]
            out["default"] = sources.default
        bindings = [b for b in self.bindings() if b.get("testdef") == testdef]
        fresh = {b["id"]: self._refreshed(b, sources, refresh) for b in bindings}
        by_fp: dict[str, list[dict]] = {}
        for b in fresh.values():
            by_fp.setdefault(b["fingerprint"], []).append(b)
        targets = self.targets(run)
        seen = {t["fingerprint"] for t in targets}
        for t in targets:
            t["issues"] = by_fp.get(t["fingerprint"], [])
            if sources is not None:
                t["route"] = sources.route({"testdef": testdef, **t["target"]})
        out["targets"] = targets
        out["bindings"] = [{**b, "label": rv.target_label(b.get("target") or {}),
                            "failed_here": b["fingerprint"] in seen} for b in fresh.values()]
        return out

    def known_brief(self, run: dict) -> list[dict]:
        """The known issues of a run's failures, as last seen (no call to the tools): the campaign page's."""
        testdef = str(run.get("testdef") or run.get("name") or "")
        fps = {t["fingerprint"]: t["label"] for t in self.targets(run)}
        return [{**(b.get("issue") or {}), "binding": b["id"], "target": fps[b["fingerprint"]]}
                for b in self.bindings() if b.get("testdef") == testdef and b.get("fingerprint") in fps]

    # ── drafting, creating, binding ────────────────────────────────────
    def _target(self, run: dict, target: Any) -> tuple[dict, str]:
        """(target, its status in the run as reviewed)."""
        target = rv.normalize_target(target)
        status = rv.raw_status(run, target)
        if status is None:
            raise SequencerError(f"run {run['id']} has no {rv.target_label(target)}")
        if target["kind"] == "check":
            c = rv.find_check(run, target)
            status = str((c or {}).get("status") or status)
        elif target["kind"] == "step":
            status = str((rv.find_record(run, target["step"]) or {}).get("state") or status)
        else:
            status = str(run.get("verdict") or status)
        return target, status

    def _links(self, run: dict, target: dict, base: str) -> dict[str, str | None]:
        from testsuit.sequencer.reportlinks import OnlineLinks
        to = OnlineLinks(base or None)
        step = target.get("step")
        rec = rv.find_record(run, step) if step else None
        return {"run": to.run(run["id"]), "report": to.run_report(run["id"]),
                "campaign": to.campaign(run["campaign_run"]) if run.get("campaign_run") else None,
                "exploit": to.exploit(run["id"], step) if rec and rec.get("exploit") else None,
                "log": to.log(run["id"], step) if rec and rec.get("log") else None}

    def _reqs(self, run: dict, target: dict) -> list[str]:
        """The requirements a target verifies (derived from the run's coverage)."""
        try:
            cov = self.seq.run_requirements(run["id"])
        except Exception:
            return []
        b = {"testdef": cov.tests[0].testdef if cov.tests else "", "target": target}
        return sorted(iss.derived_requirements(b, cov.tests))

    def draft(self, run_id: str, target: Any, base: str = "", by: str = "", source: str | None = None) -> dict:
        """A new issue about a target of a run, pre-filled: source (its route), title, body, labels; and the
        issues that already tell of it (bound to it, or open with its fingerprint)."""
        run = self.seq.reviewed(self.seq.require_run(run_id))
        sources = self._require_sources(run)
        target, status = self._target(run, target)
        testdef = str(run.get("testdef") or run.get("name") or "")
        source = source or sources.route({"testdef": testdef, **target})
        if source not in sources.names:
            raise SequencerError(f"unknown issue source '{source}' (sources: {', '.join(sources.names)})")
        rec = rv.find_record(run, target["step"]) if target.get("step") else None
        log_text = ""
        if rec and rec.get("log"):
            data = self.seq.read_run_file(run, rec["log"])
            log_text = iss.excerpt(data.decode("utf-8", "replace")) if data else ""
        reqs = self._reqs(run, target)
        try:
            markup = sources.backend(source).markup
        except IssueError:                      # a missing credential: told when creating
            markup = "jira" if str(sources.specs[source]["backend"]).lower() == "jira" else "markdown"
        d = iss.draft(run, target, status, self._links(run, target, base), reqs, log_text, markup, by)
        rules = sources.req_rules(source)
        labels = [str(x) for x in sources.specs[source].get("labels") or []] + iss.req_labels(rules, reqs)
        known = [self._refreshed(b, sources) for b in self.known(testdef, target)]
        duplicates = []
        try:
            for i in sources.backend(source).search(text=d["fingerprint"], state=OPEN, limit=5):
                if iss.mark_of(i) == d["fingerprint"] and not any(b["issue"].get("key") == f"{source}#{i.id}" for b in known):
                    duplicates.append(_renamed(i, source).brief())
        except IssueError as e:
            log.info(f"issues: looking for an issue of {d['fingerprint']}: {e}")
        projects: list[str] = []
        if sources.card(source)["group"]:
            try:
                projects = sources.backend(source).projects()
            except IssueError as e:
                log.info(f"issues: projects of '{source}': {e}")
        return {"run": run["id"], "target": target, "label": rv.target_label(target), "status": status,
                "source": source, "sources": [sources.card(n) for n in sources.names], "projects": projects,
                "title": d["title"], "body": d["body"], "labels": labels, "fingerprint": d["fingerprint"],
                "requirements": reqs, "known": known, "duplicates": duplicates}

    def _bind(self, run: dict, target: dict, issue: Issue, by: str, created: bool, note: str = "") -> dict:
        testdef = str(run.get("testdef") or run.get("name") or "")
        fp = iss.fingerprint(testdef, target)
        with self._lock:
            bindings = self.bindings(removed=True)
            for b in bindings:
                if not b.get("removed") and b.get("fingerprint") == fp and (b.get("issue") or {}).get("key") == issue.key:
                    raise SequencerError(f"{issue.key} is already bound to {rv.target_label(target)} of {testdef}")
            binding = {"id": max([int(b.get("id") or 0) for b in bindings] + [0]) + 1, "fingerprint": fp,
                       "testdef": testdef, "target": target, "issue": issue.brief(), "run": run["id"],
                       **({"created": True} if created else {}), **({"note": note} if note else {}),
                       "by": by or "", "at": now()}
            bindings.append(binding)
            self._save(bindings)
        self.seq.notify("issue", {"run": run["id"], "campaign_run": run.get("campaign_run"), "action": "bound",
                                   "issue": issue.key, "target": rv.target_label(target), "by": by or ""})
        return binding

    def create(self, run_id: str, body: dict, by: str = "", base: str = "") -> dict:
        """Create an issue about a target of a run and bind it: {target, source, project, title, body, labels,
        token (the person's own: created in their name; never kept)}."""
        run = self.seq.reviewed(self.seq.require_run(run_id))
        sources = self._require_sources(run)
        target, _ = self._target(run, body.get("target"))
        source = str(body.get("source") or sources.route({"testdef": run.get("testdef"), **target}))
        title, text = str(body.get("title") or "").strip(), str(body.get("body") or "")
        if not title:
            raise SequencerError("an issue needs a title")
        backend = sources.backend(source)
        if "create" not in backend.capabilities:
            raise SequencerError(f"issue source '{source}' cannot create issues")
        if body.get("token"):
            backend = backend.with_token(str(body["token"]))
        try:
            issue = backend.create(title, text, [str(x) for x in body.get("labels") or []], body.get("project") or None)
        except IssueError as e:
            raise SequencerError(str(e)) from e
        issue.source = source
        links = self._links(run, target, base)
        if links.get("report") and "remote_link" in backend.capabilities:
            try:
                backend.remote_link(issue.id, links["report"], f"tsq run {run['id']}: {run.get('testdef')}")
            except IssueError as e:
                log.info(f"issues: link of {issue.key} to the run report: {e}")
        return self._bind(run, target, issue, by, True)

    def bind(self, run_id: str, body: dict, by: str = "") -> dict:
        """Bind an existing issue (a reference or its URL) to a target of a run: {target, issue, note}."""
        run = self.seq.reviewed(self.seq.require_run(run_id))
        sources = self._require_sources(run)
        target, _ = self._target(run, body.get("target"))
        try:
            source, id = sources.parse(str(body.get("issue") or ""))
            issue = self.issue(sources, source, id, refresh=True)
        except IssueError as e:
            raise SequencerError(str(e)) from e
        if issue is None:
            raise SequencerError(f"no issue {source}#{id}")
        return self._bind(run, target, issue, by, False, str(body.get("note") or ""))

    def unbind(self, binding_id: int, reason: str, by: str = "") -> dict:
        if not str(reason or "").strip():
            raise SequencerError("say why the issue is unbound (reason)")
        with self._lock:
            bindings = self.bindings(removed=True)
            found = next((b for b in bindings if b.get("id") == int(binding_id) and not b.get("removed")), None)
            if found is None:
                raise SequencerError(f"no binding {binding_id}")
            found["removed"] = {"by": by or "", "at": now(), "reason": str(reason)}
            self._save(bindings)
        self.seq.notify("issue", {"run": found.get("run"), "action": "unbound", "issue": (found.get("issue") or {}).get("key"),
                                   "target": rv.target_label(found.get("target") or {}), "by": by or ""})
        return found

    def search(self, run_id: str, source: str | None = None, text: str = "", state: str | None = OPEN) -> list[dict]:
        run = self.seq.require_run(run_id)
        sources = self._require_sources(run)
        names = [source] if source else sources.names
        out = []
        for name in names:
            try:
                found = sources.backend(name).search(text=text, state=state, limit=30) if text else \
                    self.listed(sources, name, state)[:30]
            except IssueError as e:
                raise SequencerError(str(e)) from e
            out += [_renamed(i, name).as_dict() for i in found]
        return out


def _renamed(issue: Issue, source: str) -> Issue:
    if issue.source == source:
        return issue
    from dataclasses import replace
    return replace(issue, source=source)
