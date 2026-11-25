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

A **binding** ties an issue of a tickets source (testsuit.tickets) to a target of a test definition: a check, a step
or the verdict, by its fingerprint (testsuit.tickets.issues.fingerprint), so that it is a **known issue** of every
run of it that fails there, until it is unbound. Bindings are the sequencer's, not the tests repository's: they
change as issues are opened and closed, without commits. ``storage.results/tickets/bindings.yml``::

    bindings:
      - {id: 1, fingerprint: 3f2a..., testdef: short, target: {kind: check, step: exploit:criteria, ...},
         issue: {source: bench, id: '42', key: 'bench#42', title, state, url}, run: <run it was bound from>,
         created: true, by, at, removed: {by, at, reason}}

The tickets sources of a run are those of its datapack (``definition/tickets.yml``, written at generation): its
credentials are references, read on the sequencer's host. Issue states are read from the tools at most every few
minutes (a cache), and kept in the bindings as last seen.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from testsuit.sequencer import review as rv
from testsuit.sequencer.util import SequencerError, now
from testsuit.tickets import issues as iss
from testsuit.tickets.model import OPEN, Issue, TicketError
from testsuit.tickets.sources import SNAPSHOT, TicketSources

log = logging.getLogger("testsuit.sequencer")
TTL = 300.0                                  # seconds an issue's state is taken as known
BINDINGS = "bindings.yml"


class Tickets:
    """The bindings of the sequencer, and the calls to the tickets tools of its runs."""

    def __init__(self, seq) -> None:
        self.seq = seq
        self._lock = threading.Lock()
        self._issues: dict[tuple[str, str], tuple[float, Issue | None]] = {}     # (backend identity, id)
        self._lists: dict[tuple[str, str], tuple[float, list[Issue]]] = {}     # (backend identity, state)
        self._sources: dict[str, TicketSources | None] = {}                     # run id

    # ── bindings ───────────────────────────────────────────────────────
    @property
    def path(self) -> Path:
        return self.seq.config.storage_path("results") / "tickets" / BINDINGS

    def bindings(self, removed: bool = False) -> list[dict]:
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
    def sources(self, run: dict) -> TicketSources | None:
        """The tickets sources of a run's datapack (None: it has none)."""
        if run["id"] not in self._sources:
            data = self.seq.read_run_file(run, f"definition/{SNAPSHOT}")
            found = None
            if data is not None:
                try:
                    # file: credentials relative to tsq.yml's folder (on the sequencer's host)
                    found = TicketSources(yaml.safe_load(data) or {}, self.seq.config.base_dir,
                                          f"run {run['id']}: definition/{SNAPSHOT}")
                except (TicketError, yaml.YAMLError) as e:
                    raise SequencerError(str(e)) from e
            self._sources[run["id"]] = found
        return self._sources[run["id"]]

    def _require_sources(self, run: dict) -> TicketSources:
        sources = self.sources(run)
        if sources is None:
            raise SequencerError(f"run {run['id']}: its datapack names no tickets source (no tickets.yml in its tests "
                                 "repository when it was generated)")
        return sources

    # ── issues, cached ─────────────────────────────────────────────────
    def issue(self, sources: TicketSources, source: str, id: str, refresh: bool = False) -> Issue | None:
        backend = sources.backend(source)
        key = (backend.identity, str(id))
        hit = self._issues.get(key)
        if hit and not refresh and time.monotonic() - hit[0] < TTL:
            return hit[1]
        found = backend.get([str(id)]).get(str(id))
        if found is not None:
            found.source = source
        self._issues[key] = (time.monotonic(), found)
        return found

    def listed(self, sources: TicketSources, source: str, state: str | None = OPEN, refresh: bool = False,
               limit: int = 1000) -> list[Issue]:
        """The issues of a source (open ones: the default), cached."""
        backend = sources.backend(source)
        key = (backend.identity, str(state))
        hit = self._lists.get(key)
        if hit and not refresh and time.monotonic() - hit[0] < TTL:
            return [_renamed(i, source) for i in hit[1]]
        found = backend.search(state=state, limit=limit)
        self._lists[key] = (time.monotonic(), found)
        for i in found:
            self._issues[(backend.identity, i.id)] = (time.monotonic(), i)
        return [_renamed(i, source) for i in found]

    def _refreshed(self, binding: dict, sources: TicketSources | None, refresh: bool = False) -> dict:
        """A binding with its issue as the tool gives it now (as last seen when the tool cannot be reached)."""
        brief = binding.get("issue") or {}
        if sources is None or brief.get("source") not in sources.names:
            return binding
        try:
            found = self.issue(sources, brief["source"], brief["id"], refresh)
        except TicketError as e:
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
        """The tickets of a run: its sources, its targets that failed with their known issues (bound to the same
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
            raise SequencerError(f"unknown tickets source '{source}' (sources: {', '.join(sources.names)})")
        rec = rv.find_record(run, target["step"]) if target.get("step") else None
        log_text = ""
        if rec and rec.get("log"):
            data = self.seq.read_run_file(run, rec["log"])
            log_text = iss.excerpt(data.decode("utf-8", "replace")) if data else ""
        reqs = self._reqs(run, target)
        try:
            markup = sources.backend(source).markup
        except TicketError:                      # a missing credential: told when creating
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
        except TicketError as e:
            log.info(f"tickets: looking for an issue of {d['fingerprint']}: {e}")
        projects: list[str] = []
        if sources.card(source)["group"]:
            try:
                projects = sources.backend(source).projects()
            except TicketError as e:
                log.info(f"tickets: projects of '{source}': {e}")
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
        self.seq.notify("ticket", {"run": run["id"], "campaign_run": run.get("campaign_run"), "action": "bound",
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
            raise SequencerError(f"tickets source '{source}' cannot create issues")
        if body.get("token"):
            backend = backend.with_token(str(body["token"]))
        try:
            issue = backend.create(title, text, [str(x) for x in body.get("labels") or []], body.get("project") or None)
        except TicketError as e:
            raise SequencerError(str(e)) from e
        issue.source = source
        links = self._links(run, target, base)
        if links.get("report") and "remote_link" in backend.capabilities:
            try:
                backend.remote_link(issue.id, links["report"], f"tsq run {run['id']}: {run.get('testdef')}")
            except TicketError as e:
                log.info(f"tickets: link of {issue.key} to the run report: {e}")
        return self._bind(run, target, issue, by, True)

    def bind(self, run_id: str, body: dict, by: str = "") -> dict:
        """Bind an existing issue (a reference or its URL) to a target of a run: {target, issue, note}."""
        run = self.seq.reviewed(self.seq.require_run(run_id))
        sources = self._require_sources(run)
        target, _ = self._target(run, body.get("target"))
        try:
            source, id = sources.parse(str(body.get("issue") or ""))
            issue = self.issue(sources, source, id, refresh=True)
        except TicketError as e:
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
        self.seq.notify("ticket", {"run": found.get("run"), "action": "unbound", "issue": (found.get("issue") or {}).get("key"),
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
            except TicketError as e:
                raise SequencerError(str(e)) from e
            out += [_renamed(i, name).as_dict() for i in found]
        return out


def _renamed(issue: Issue, source: str) -> Issue:
    if issue.source == source:
        return issue
    from dataclasses import replace
    return replace(issue, source=source)
