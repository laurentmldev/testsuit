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

"""The requirements of a datapack, fetched once at generation: ``definition/requirements.yml``.

Reports and coverage then work offline, on the text that was tested::

    format: 1
    default_source: reqs
    sources: [{name: reqs, backend: yaml, revision: 3f2a...},     # with its variables as resolved, the mandatory
              {name: alm, backend: rest, context: {version: '3.2'},  # ones as its context
               vars: {version: '3.2', subsystem: Power, subsystem_id: 42}}]
    testdef: [REQ-12]                       # links of the test definition (keys: no version, no default prefix)
    checks:                                 # links of the checks of its exploit steps
      - {step: analyse, criteria: criteria/max_speed, scenario: max_speed, file: max_speed.mexploit.yml,
         check: speed_never_over_limit, reqs: [REQ-12, jama:SYS-4]}
    requirements:                           # each linked requirement as fetched
      REQ-12: {id: REQ-12, source: reqs, path: SYS/BRAKE, description: ..., version: 3f2a..., pinned: 2}
    problems: [...]                         # unknown, deleted or rejected requirements (generated with --nocheck)
    warnings: [...]                         # pinned versions that changed since
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from testsuit.reqs.backend import ReqError
from testsuit.reqs.model import Ref
from testsuit.reqs.sources import SOURCES_FILE, Sources, find_sources_file

SNAPSHOT = "requirements.yml"            # in the datapack's definition/ folder
FORMAT = 1


def resolve(sources: Sources, refs: list[str]) -> tuple[dict[str, dict], dict[str, str], list[str], list[str]]:
    """Fetch the references: (requirements by key, key of each reference as written, problems, warnings)."""
    parsed: dict[str, Ref] = {}
    problems: list[str] = []
    for text in refs:
        try:
            parsed[text] = sources.ref(text)
        except ValueError as e:
            problems.append(str(e))
    keys = {text: sources.key(ref) for text, ref in parsed.items()}
    found: dict[Ref, Any] = {}
    for source in sorted({r.source for r in parsed.values()}):
        try:
            found.update(sources.get(Ref(r.source, r.id) for r in parsed.values() if r.source == source))
        except Exception as e:            # a backend that cannot be reached: each of its references is a problem
            problems.append(f"requirements source '{source}': {e}")
    requirements: dict[str, dict] = {}
    warnings: list[str] = []
    for text, ref in parsed.items():
        key = keys[text]
        bare = Ref(ref.source, ref.id)
        if bare not in found:
            continue
        req = found[bare]
        if req is None:
            problems.append(f"requirement '{key}' is unknown to source '{ref.source}'")
            continue
        if req.inactive:
            problems.append(f"requirement '{key}' is {req.status.lower()}")
        entry = requirements.setdefault(key, req.as_dict())
        if ref.version:
            if entry.get("pinned") not in (None, ref.version):
                warnings.append(f"requirement '{key}' pinned to versions {entry['pinned']!r} and {ref.version!r}")
            entry["pinned"] = ref.version
            if str(req.version) != ref.version:
                entry["changed"] = True
                warnings.append(f"requirement '{key}' changed since version {ref.version} (now {req.version})")
    return requirements, keys, problems, warnings


def build(sources_file: Path | None, testdef_reqs: list[str], checks: list[dict],
          link_problems: list[str] = (), keys: Mapping[str, Any] | None = None) -> dict:
    """The snapshot of these links (sources_file None: every link is a problem)."""
    refs = list(dict.fromkeys([*testdef_reqs, *(r for c in checks for r in c["reqs"])]))
    snap: dict[str, Any] = {"format": FORMAT}
    problems = list(link_problems)
    if sources_file is None:
        problems.insert(0, f"no {SOURCES_FILE} at the root of the tests repository: the requirements cannot be fetched")
        keys = {r: r for r in refs}
        requirements, warnings, record, default = {}, [], [], ""
    else:
        try:
            sources = Sources(sources_file, keys)
            requirements, keys, found, warnings = resolve(sources, refs)
            problems += found
            record, default = sources.record(), sources.default
        except ReqError as e:
            problems.append(str(e))
            keys, requirements, warnings, record, default = {r: r for r in refs}, {}, [], [], ""
    snap.update({"default_source": default, "sources": record,
                 "testdef": [keys.get(r, r) for r in testdef_reqs],
                 "checks": [{**c, "reqs": [keys.get(r, r) for r in c["reqs"]]} for c in checks],
                 "requirements": requirements})
    if problems:
        snap["problems"] = problems
    if warnings:
        snap["warnings"] = warnings
    return snap


def links_of_testdef(testdef_file: Path, raw: Mapping[str, Any], keys: Mapping[str, Any] | None = None,
                     locate=None) -> tuple[list[str], list[dict], list[str]]:
    """(testdef links, check links, problems) of a test definition as written."""
    from testsuit.reqs.links import check_links, testdef_links
    problems: list[str] = []
    try:
        own = testdef_links(raw)
    except ValueError as e:
        own, problems = [], [str(e)]
    checks, found = check_links(raw.get("pipeline"), testdef_file.parent, keys, locate)
    return own, checks, problems + found


def snapshot_of_testdef(testdef_file: Path, raw: Mapping[str, Any], keys: Mapping[str, Any] | None = None,
                        locate=None) -> dict | None:
    """The snapshot of a test definition's links, None when it lists no requirement."""
    own, checks, problems = links_of_testdef(testdef_file, raw, keys, locate)
    if not own and not checks and not problems:
        return None
    return build(find_sources_file(testdef_file), own, checks, problems, keys)
