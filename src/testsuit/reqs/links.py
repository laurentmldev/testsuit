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

"""The requirements a test lists: ``reqs`` of its test definition, and ``reqs`` of each check of the criteria its
exploit steps run (sequencer SPEC section 5.4).

A link on the test definition is verified by the run verdict; a link on a check by the check's status.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

SCENARIO = re.compile(r".*\.mexploit\.ya?ml$")


def as_reqs(value: Any, where: str = "") -> list[str]:
    """A ``reqs`` value: a reference or a list of them."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out = []
    for item in items:
        if not isinstance(item, (str, int)) or not str(item).strip():
            raise ValueError(f"{where + ': ' if where else ''}'reqs' holds requirement references, found {item!r}")
        out.append(str(item).strip())
    return out


def _is_scenario(path: Path) -> bool:
    if path.is_file():
        return bool(SCENARIO.match(path.name))
    return path.is_dir() and any(p.is_file() and SCENARIO.match(p.name) for p in path.iterdir())


def scenarios(criteria: Path) -> list[Path]:
    """The scenarios (test runs) of a criteria entry, as exploit_runner finds them: the entry itself, or its
    children that are scenarios."""
    if not criteria.exists():
        return []
    if _is_scenario(criteria):
        return [criteria]
    return sorted(p for p in criteria.iterdir() if _is_scenario(p))


def scenario_name(path: Path) -> str:
    return re.sub(r"\.mexploit\.ya?ml$", "", path.name) if path.is_file() else path.name


def _render(path: Path, keys: Mapping[str, Any]) -> str:
    """The file rendered leniently: unknown keys render empty (the step's ``with`` keys are not known here)."""
    import jinja2
    env = jinja2.Environment(loader=jinja2.FileSystemLoader([str(path.parent)]), undefined=jinja2.ChainableUndefined,
                             keep_trailing_newline=True)
    return env.from_string(path.read_text(encoding="utf-8")).render(**dict(keys))


def scenario_checks(scenario: Path, keys: Mapping[str, Any] | None = None) -> tuple[list[dict], list[str]]:
    """``[{file, check, reqs}]`` for each check of a scenario that lists requirements, and the problems met."""
    files = [scenario] if scenario.is_file() else sorted(p for p in scenario.rglob("*")
                                                          if p.is_file() and SCENARIO.match(p.name))
    found, problems = [], []
    for path in files:
        rel = path.name if scenario.is_file() else path.relative_to(scenario).as_posix()
        try:
            data = yaml.safe_load(_render(path, keys or {})) or {}
        except Exception as e:
            try:                                   # a file that does not render may still be YAML as written
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                problems.append(f"{path}: cannot read the requirements of its checks ({e})")
                continue
        if not isinstance(data, dict):
            continue
        for name, entry in data.items():
            if isinstance(entry, dict) and entry.get("reqs") is not None:
                try:
                    reqs = as_reqs(entry["reqs"], f"{rel}, check '{name}'")
                except ValueError as e:
                    problems.append(str(e))
                    continue
                found.append({"file": rel, "check": str(name), "reqs": reqs})
    return found, problems


def check_links(pipeline: Any, definition_dir: Path, keys: Mapping[str, Any] | None = None,
                locate=None) -> tuple[list[dict], list[str]]:
    """``[{step, criteria, scenario, file, check, reqs}]``: the checks of the criteria the exploit steps of a
    pipeline name that list requirements. ``locate(name) -> Path`` finds a criteria entry (default: relative to
    ``definition_dir``)."""
    from testsuit.sequencer.exploitstep import pipeline_exploit_steps
    out, problems = [], []
    for step in pipeline_exploit_steps(pipeline if isinstance(pipeline, Mapping) else None, definition_dir):
        for name in step["criteria"]:
            folder = locate(name) if locate else definition_dir / name
            for scenario in scenarios(folder):
                checks, found = scenario_checks(scenario, keys)
                problems += found
                out += [{"step": step["id"], "criteria": name, "scenario": scenario_name(scenario), **c} for c in checks]
    return out, problems


def testdef_links(testdef: Mapping[str, Any]) -> list[str]:
    return as_reqs(testdef.get("reqs"), "test definition")


def all_refs(testdef_reqs: list[str], checks: list[dict]) -> list[str]:
    """Every reference, once, in order of appearance."""
    seen: dict[str, None] = {}
    for r in testdef_reqs:
        seen.setdefault(r)
    for c in checks:
        for r in c["reqs"]:
            seen.setdefault(r)
    return list(seen)
