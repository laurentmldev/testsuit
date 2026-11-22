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
    level: str                       # 'testdef' or 'check'
    status: str = NOT_RUN
    step: str = ""
    criteria: str = ""
    scenario: str = ""
    file: str = ""
    check: str = ""

    @property
    def where(self) -> str:
        if self.level == "testdef":
            return "test definition"
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
            "requirements": [{"key": k, **{f: v for f, v in self.requirements.get(k, {}).items() if f in (
                "title", "path", "version", "status", "url")}, "coverage": self.status(k),
                "tests": {t.key: {"status": t.status(k), **({"stale": True} if self.stale(k, t) else {})}
                          for t in self.tests if t.status(k) is not None}} for k in self.rows()],
            "tests": [{"key": t.key, "testdef": t.testdef, "name": t.name, "origin": t.origin,
                       **({"run": t.run, "verdict": t.verdict} if t.run else {}),
                       "links": [{"req": lk.req, "where": lk.where, "status": lk.status} for lk in t.links],
                       **({"problems": t.problems} if t.problems else {}),
                       **({"warnings": t.warnings} if t.warnings else {})} for t in self.tests],
            "by_path": self.by_path(),
            **({"uncovered": self.uncovered()} if self.universe is not None else {}),
            **({"problems": self.problems} if self.problems else {}),
        }


# ── reading the targets ───────────────────────────────────────────────────

def _links_from_snapshot(snap: dict) -> list[Link]:
    links = [Link(r, "testdef") for r in snap.get("testdef") or []]
    for c in snap.get("checks") or []:
        links += [Link(r, "check", step=c.get("step", ""), criteria=c.get("criteria", ""), scenario=c.get("scenario", ""),
                       file=c.get("file", ""), check=c.get("check", "")) for r in c.get("reqs") or []]
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
        if lk.level == "testdef":
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


def coverage(targets: list[Path], sources_file: Path | None = None, paths: list[str] | None = None,
             children: bool = False, live: bool = True) -> Coverage:
    """Coverage of test definitions, campaign files, datapack and results zips, and folders of zips.

    :param sources_file: the sources file (default: the one of the tests repository of the current folder, else of the
        first target), for the current
        versions (stale links) and for ``paths``
    :param paths: requirement paths (``[<source>:]<path>``) whose requirements are rows even when not linked
    :param children: with paths, their descendants too
    :param live: read the current requirements from the sources (False: only the snapshots)
    """
    cov = Coverage()
    keys: dict[str, int] = {}
    for path in _expand([Path(t) for t in targets]):
        try:
            found = read_zip(path) if path.suffix == ".zip" else read_testdef(path)
        except (OSError, zipfile.BadZipFile, yaml.YAMLError, ValueError) as e:
            cov.problems.append(f"{path}: {e}")
            continue
        if found is None:
            cov.problems.append(f"{path}: no requirement linked")
            continue
        snap, test = found
        n = keys.get(test.key, 0)
        keys[test.key] = n + 1
        if n:
            test.key = f"{test.key}#{n + 1}"
        for key, req in (snap.get("requirements") or {}).items():
            cov.requirements.setdefault(key, req)
            test.versions[key] = str(req.get("version", ""))
        cov.tests.append(test)

    if live or paths:
        file = sources_file or find_sources_file(Path.cwd()) or (find_sources_file(Path(targets[0])) if targets else None)
        if file is None:
            if paths:
                raise ReqError("no requirements.yml found: give the sources file (--sources)")
            return cov
        sources = Sources(file)
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
                current = sources.get(refs.values())
            except Exception as e:
                cov.problems.append(f"current requirements not read: {e}")
                current = {}
            for key, ref in refs.items():
                req = current.get(ref)
                if req is not None:
                    cov.current[key] = req.version
                    cov.requirements[key] = {**req.as_dict(), **{k: v for k, v in cov.requirements.get(key, {}).items()
                                                               if k in ("pinned", "changed")}}
    return cov

