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

"""Requirements coverage of tests, datapacks, results and campaigns (sequencer SPEC section 5.4).

A requirement is **linked** when a test definition or one of its checks lists it, and **verified** by a run:

| status | for one link | for a requirement (its links rolled up) |
|---|---|---|
| ``passed`` | the check met (SUCCESS, or ACCEPTED by review); the run succeeded for a test definition link | every link passed |
| ``failed`` | the check not met (FAILED, or REJECTED by review); the run failed | a link failed |
| ``error`` | the check, its step or the run in error | a link in error, none failed |
| ``partial`` | | some links passed, the others skipped or not run |
| ``skipped`` | the exploit step was skipped | no link passed, one skipped |
| ``not run`` | no result: a test definition or a datapack, or a run that did not reach it | no result at all |
| ``uncovered`` | | no link (requirements under the paths asked for) |

A test with an older version of a requirement than the current one is **stale** for it.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from testsuit.reqs.backend import ReqError
from testsuit.reqs.snapshot import SNAPSHOT, snapshot_of_testdef
from testsuit.reqs.sources import Sources, find_sources_file

PASSED, FAILED, ERROR, PARTIAL, SKIPPED, NOT_RUN, UNCOVERED = (
    "passed", "failed", "error", "partial", "skipped", "not run", "uncovered")
STATUSES = (PASSED, FAILED, ERROR, PARTIAL, SKIPPED, NOT_RUN, UNCOVERED)
_CHECK = {"SUCCESS": PASSED, "ACCEPTED": PASSED, "FAILED": FAILED, "REJECTED": FAILED, "ERROR": ERROR}
_VERDICT = {"succeeded": PASSED, "failed": FAILED, "error": ERROR}


def rollup(statuses: list[str]) -> str:
    """The status of a set of links (or of requirements)."""
    s = set(statuses)
    if not s:
        return NOT_RUN
    if FAILED in s:
        return FAILED
    if ERROR in s:
        return ERROR
    if s == {PASSED}:
        return PASSED
    if PASSED in s or PARTIAL in s:
        return PARTIAL
    if SKIPPED in s:
        return SKIPPED
    return NOT_RUN


@dataclass
class Link:
    req: str
    level: str                       # 'testdef', 'check' or 'input' (a @tr of an input file)
    status: str = NOT_RUN
    step: str = ""
    criteria: str = ""
    scenario: str = ""
    file: str = ""
    check: str = ""
    line: int = 0
    uid: str = ""                    # the test case (@uid) of an input file's link

    @property
    def where(self) -> str:
        if self.level == "testdef":
            return "test definition"
        if self.level == "input":
            return (f"{self.uid} ({self.file}:{self.line})" if self.uid else f"{self.file}:{self.line}")
        folder = self.criteria if self.criteria.rstrip("/").rsplit("/", 1)[-1] == self.scenario \
            else f"{self.criteria}/{self.scenario}"
        return f"{self.step}: {folder}/{self.file}::{self.check}"


@dataclass
class Test:
    key: str                         # unique column key
    testdef: str                     # its id
    name: str = ""
    origin: str = ""                 # the file it was read from
    run: str = ""                    # run id, for results
    verdict: str = ""
    reports: dict[str, str] = field(default_factory=dict)   # exploit step -> report path in the run folder
    links: list[Link] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)  # requirement -> version it was linked against
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    context: dict[str, dict] = field(default_factory=dict)  # source -> its mandatory variables (branch, version...)

    @property
    def context_text(self) -> str:
        """``alm: branch=main, version=3.2``, a source by line."""
        return "; ".join(f"{source}: " + ", ".join(f"{k}={v}" for k, v in values.items())
                         for source, values in self.context.items())

    @property
    def label(self) -> str:
        return self.testdef + (f" ({self.run})" if self.run else "")

    def status(self, req: str) -> str | None:
        found = [lk.status for lk in self.links if lk.req == req]
        return rollup(found) if found else None


@dataclass
class Coverage:
    tests: list[Test] = field(default_factory=list)
    requirements: dict[str, dict] = field(default_factory=dict)   # key -> requirement as fetched
    current: dict[str, str] = field(default_factory=dict)         # key -> current version (live sources)
    universe: list[str] | None = None                             # requirements under the paths asked for
    problems: list[str] = field(default_factory=list)
    tag_colors: dict[str, str] = field(default_factory=dict)      # dynamic tag -> its color, when the source gives one

    def linked(self) -> list[str]:
        return list(dict.fromkeys(lk.req for t in self.tests for lk in t.links))

    def rows(self) -> list[str]:
        """The requirements of the matrix: the linked ones, and those under the paths asked for."""
        keys = list(dict.fromkeys([*(self.universe or []), *self.linked()]))
        return sorted(keys, key=lambda k: (str(self.requirements.get(k, {}).get("path", "")), k))

    def status(self, req: str) -> str:
        found = [lk.status for t in self.tests for lk in t.links if lk.req == req]
        return rollup(found) if found else UNCOVERED

    def stale(self, req: str, test: Test) -> bool:
        cur, tested = self.current.get(req), test.versions.get(req)
        return bool(cur and tested and str(cur) != str(tested))

    def uncovered(self) -> list[str]:
        return [k for k in self.rows() if self.status(k) == UNCOVERED]

    def by_path(self) -> dict[str, dict[str, int]]:
        """Count of each status per node of the paths of the requirements, rolled up to the root ('')."""
        out: dict[str, dict[str, int]] = {}
        for key in self.rows():
            path = str(self.requirements.get(key, {}).get("path", "")).strip("/")
            parts = path.split("/") if path else []
            status = self.status(key)
            for i in range(len(parts) + 1):
                node = "/".join(parts[:i])
                counts = out.setdefault(node, {})
                counts[status] = counts.get(status, 0) + 1
        return dict(sorted(out.items()))

    def as_dict(self) -> dict:
        return {
            "requirements": [{"key": k, **self.requirements.get(k, {}), "coverage": self.status(k),
                "tests": {t.key: {"status": t.status(k), **({"changed": True} if self.stale(k, t) else {})}
                          for t in self.tests if t.status(k) is not None}} for k in self.rows()],
            "tests": [{"key": t.key, "testdef": t.testdef, "name": t.name, "origin": t.origin,
                       **({"run": t.run, "verdict": t.verdict, "reports": t.reports} if t.run else {}),
                       "links": [{"req": lk.req, "where": lk.where, "level": lk.level, "step": lk.step,
                                  **({"file": lk.file, "line": lk.line} if lk.level == "input" else {}),
                                  "status": lk.status, **({"changed": True} if self.stale(lk.req, t) else {})}
                                 for lk in t.links],
                       **({"context": t.context} if t.context else {}),
                       **({"problems": t.problems} if t.problems else {}),
                       **({"warnings": t.warnings} if t.warnings else {})} for t in self.tests],
            "by_path": self.by_path(),
            **({"uncovered": self.uncovered()} if self.universe is not None else {}),
            **({"problems": self.problems} if self.problems else {}),
            **({"tag_colors": self.tag_colors} if self.tag_colors else {}),
        }


# ── reading the targets ───────────────────────────────────────────────────

def _links_from_snapshot(snap: dict) -> list[Link]:
    links = [Link(r, "testdef") for r in snap.get("testdef") or []]
    for c in snap.get("checks") or []:
        links += [Link(r, "check", step=c.get("step", ""), criteria=c.get("criteria", ""), scenario=c.get("scenario", ""),
                       file=c.get("file", ""), check=c.get("check", "")) for r in c.get("reqs") or []]
    for lk in snap.get("inputs") or []:
        links += [Link(r, "input", file=str(lk.get("file", "")), line=int(lk.get("line") or 0), uid=str(lk.get("uid") or ""))
                  for r in lk.get("reqs") or []]
    return links


def _record(steps: list[dict], step_id: str) -> dict | None:
    exact = [s for s in steps if s.get("id") == step_id]
    if exact:
        return exact[0]
    return next((s for s in steps if str(s.get("id", "")).endswith("." + step_id)), None)


def apply_run(test: Test, run: dict) -> None:
    """Set the status of each link of a test from its run record (run.yml)."""
    test.run = str(run.get("run") or "")
    test.verdict = str(run.get("verdict") or "")
    steps = run.get("steps") or []
    for s in steps:
        if (s.get("exploit") or {}).get("report"):
            test.reports[str(s.get("id"))] = s["exploit"]["report"]
    for lk in test.links:
        if lk.level in ("testdef", "input"):
            lk.status = _VERDICT.get(test.verdict, NOT_RUN)
            continue
        rec = _record(steps, lk.step)
        if rec is None:
            lk.status = NOT_RUN
            continue
        if rec.get("state") == "skipped":
            lk.status = SKIPPED
            continue
        checks = (rec.get("exploit") or {}).get("checks") or []
        match = next((c for c in checks if c.get("check") == lk.check and c.get("scenario") == lk.scenario
                      and c.get("criteria") == lk.criteria), None) \
            or next((c for c in checks if c.get("check") == lk.check and c.get("scenario") == lk.scenario), None)
        if match is not None:
            lk.status = _CHECK.get(str(match.get("status", "")).upper(), ERROR)
        else:
            lk.status = ERROR if rec.get("state") in ("error", "failed") else NOT_RUN


def _member(archive: zipfile.ZipFile, rel: str) -> Any:
    names = [n for n in archive.namelist() if n.count("/") == rel.count("/") + 1 and n.endswith("/" + rel)]
    return yaml.safe_load(archive.read(names[0])) if names else None


def read_zip(path: Path) -> tuple[dict, Test] | None:
    """(snapshot, test) of a datapack or results zip; None when it holds no requirements snapshot."""
    with zipfile.ZipFile(path) as archive:
        snap = _member(archive, f"definition/{SNAPSHOT}")
        if not isinstance(snap, dict):
            return None
        info = _member(archive, "datapack.yml") or {}
        run = _member(archive, "run.yml")
    testdef = info.get("testdef") or {}
    test = Test(key=path.stem, testdef=str(testdef.get("id") or path.stem), name=str(testdef.get("name") or ""),
                origin=str(path), links=_links_from_snapshot(snap), problems=list(snap.get("problems") or []),
                warnings=list(snap.get("warnings") or []))
    if isinstance(run, dict):
        apply_run(test, run)
    return snap, test


def read_testdef(path: Path) -> tuple[dict, Test] | None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    snap = snapshot_of_testdef(path, raw)
    if snap is None:
        return None
    test = Test(key=str(raw.get("id") or path.parent.name), testdef=str(raw.get("id") or path.parent.name),
                name=str(raw.get("name") or ""), origin=str(path), links=_links_from_snapshot(snap),
                problems=list(snap.get("problems") or []), warnings=list(snap.get("warnings") or []))
    return snap, test


def _expand(targets: list[Path]) -> list[Path]:
    from testsuit.datatools.datapack.campaign import is_campaign_file, load_campaign
    out: list[Path] = []
    for t in targets:
        if t.is_dir():
            out += sorted(p for p in t.rglob("*.zip"))
        elif is_campaign_file(t):
            campaign = load_campaign(t)
            out += [(t.parent / e.testdef).resolve() for e in campaign.testdefs]
        else:
            out.append(t)
    return out


def add_test(cov: Coverage, snap: dict, test: Test, keys: dict[str, int] | None = None) -> None:
    """Add a test and the requirements of its snapshot (a test key already there gets a #<n> suffix)."""
    taken = {t.key for t in cov.tests}
    base, n = test.key, 1
    while test.key in taken:
        n += 1
        test.key = f"{base}#{n}"
    for source in snap.get("sources") or []:
        if isinstance(source, dict) and source.get("context"):
            test.context[str(source.get("name"))] = dict(source["context"])
    for key, req in (snap.get("requirements") or {}).items():
        cov.requirements.setdefault(key, req)
        test.versions[key] = str(req.get("version", ""))
    cov.tests.append(test)


def add_sources(cov: Coverage, sources: Sources, paths: list[str] | None = None, children: bool = False,
                live: bool = True) -> None:
    """The requirements under paths as rows (inactive ones left out), and the current version of every row (live)."""
    cov.tag_colors.update(sources.tag_colors())
    if paths:
        cov.universe = []
        for p in paths:
            source, sep, node = p.partition(":")
            if not sep or source not in sources.names:
                source, node = sources.default, p
            for key, req in sources.list(node, children, source).items():
                if req.inactive:                # deleted or rejected: nothing to cover
                    continue
                cov.universe.append(key)
                cov.requirements[key] = req.as_dict()
    if live:
        refs = {k: sources.ref(k) for k in cov.rows()}
        try:
            current = sources.get(refs.values(), brief=True)          # versions: no details calls
        except Exception as e:
            cov.problems.append(f"current requirements not read: {e}")
            current = {}
        for key, ref in refs.items():
            req = current.get(ref)
            if req is not None:
                cov.current[key] = req.version
                cov.requirements[key] = {**req.as_dict(), **{k: v for k, v in cov.requirements.get(key, {}).items()
                                                           if k in ("pinned", "changed")}}


def coverage(targets: list[Path], sources_file: Path | None = None, paths: list[str] | None = None,
             children: bool = False, live: bool = True) -> Coverage:
    """Coverage of test definitions, campaign files, datapack and results zips, and folders of zips.

    :param sources_file: the sources file (default: the one of the tests repository of the current folder, else of the
        first target), for the current versions (changed since) and for ``paths``
    :param paths: requirement paths (``[<source>:]<path>``) whose requirements are rows even when not linked
    :param children: with paths, their descendants too
    :param live: read the current requirements from the sources (False: only the snapshots)
    """
    cov = Coverage()
    for path in _expand([Path(t) for t in targets]):
        try:
            found = read_zip(path) if path.suffix == ".zip" else read_testdef(path)
        except (OSError, zipfile.BadZipFile, yaml.YAMLError, ValueError) as e:
            cov.problems.append(f"{path}: {e}")
            continue
        if found is None:
            cov.problems.append(f"{path}: no requirement linked")
            continue
        add_test(cov, *found)

    if live or paths:
        file = sources_file or find_sources_file(Path.cwd()) or (find_sources_file(Path(targets[0])) if targets else None)
        if file is None:
            if paths:
                raise ReqError("no requirements.yml found: give the sources file (--sources)")
            return cov
        add_sources(cov, Sources(file), paths, children, live)
    return cov


# ── runs of the sequencer ─────────────────────────────────────────────────

def snapshot_of_run(run: dict) -> dict | None:
    """The requirements snapshot of a run (sequencer run record): from its run folder, else from its results zip."""
    folder = Path(run["dir"]) if run.get("dir") else None
    if folder is not None and (folder / "definition" / SNAPSHOT).is_file():
        try:
            snap = yaml.safe_load((folder / "definition" / SNAPSHOT).read_text(encoding="utf-8"))
            return snap if isinstance(snap, dict) else None
        except (OSError, yaml.YAMLError):
            return None
    if run.get("results") and Path(run["results"]).is_file():
        try:
            with zipfile.ZipFile(run["results"]) as archive:
                snap = _member(archive, f"definition/{SNAPSHOT}")
            return snap if isinstance(snap, dict) else None
        except (OSError, zipfile.BadZipFile, yaml.YAMLError):
            return None
    return None


def test_of_run(run: dict, snap: dict) -> Test:
    """A run as a test: its links with their status (a run not ended: its verdict is not known yet)."""
    test = Test(key=str(run.get("testdef") or run["id"]), testdef=str(run.get("testdef") or ""), origin=str(run["id"]),
                links=_links_from_snapshot(snap), problems=list(snap.get("problems") or []),
                warnings=list(snap.get("warnings") or []))
    ended = run.get("state") in ("done", "cancelled")
    apply_run(test, {"run": run["id"], "verdict": (run.get("verdict") or "error") if ended else "",
                     "steps": run.get("steps") or []})
    return test


def coverage_of_runs(runs: list[dict]) -> Coverage:
    """Coverage of sequencer runs (a run, or the runs of a campaign run), from their snapshots only."""
    cov = Coverage()
    for run in runs:
        snap = snapshot_of_run(run)
        if snap is not None:
            add_test(cov, snap, test_of_run(run, snap))
    return cov
