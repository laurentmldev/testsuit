"""Baseline of a datapack (sequencer README section 5.3): where its contents come from, and whether
it matches an expected baseline.

``baseline.yml`` lists the sources (git origin, commit, tag and cleanliness of the test definition,
setup and dictionaries; version of each dataset), the tools that processed them, and each generated
file with what produced it. The sequencer adds a ``run`` part to the baseline of the results.

An **expected baseline** has the same structure and holds only what must match. Strings are shell
patterns (``v1.4.*``), and a value starting with a comparison operator compares versions (``>=3.2``).
A mismatch never rejects anything: the datapack or the run is flagged non-conform.
"""
from __future__ import annotations

import fnmatch
import os
import re
from importlib.metadata import PackageNotFoundError, entry_points, version
from typing import Any

_OPERATOR = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*(.+)$")


def _version_tuple(text: str) -> tuple:
    parts = []
    for part in re.split(r"[.\-+]", str(text).lstrip("v")):
        parts.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(parts)


def value_matches(actual: Any, expected: Any) -> bool:
    """Whether an actual baseline value matches an expected one (pattern, comparison or equality)."""
    if isinstance(expected, bool) or expected is None or isinstance(expected, (int, float)):
        return actual == expected
    if isinstance(expected, list):
        return any(value_matches(actual, e) for e in expected)
    if actual is None:
        return False
    text = str(expected)
    match = _OPERATOR.match(text)
    if match:
        op, ref = match.groups()
        a, b = _version_tuple(str(actual)), _version_tuple(ref)
        return {">=": a >= b, "<=": a <= b, "==": a == b, "!=": a != b, ">": a > b, "<": a < b}[op]
    return fnmatch.fnmatchcase(str(actual), text)


def _entry_matches(actual: dict, expected: dict) -> list[str]:
    return [k for k, v in expected.items() if not value_matches(actual.get(k), v)]


def _check_list(section: str, actual: list[dict], expected: list[dict], keys: tuple[str, ...]) -> list[str]:
    problems = []
    for exp in expected or []:
        selector = {k: exp[k] for k in keys if k in exp}
        candidates = [a for a in actual or [] if all(value_matches(a.get(k), v) for k, v in selector.items())]
        label = f"{section} " + ", ".join(f"{k}={v}" for k, v in selector.items())
        if not candidates:
            problems.append(f"{label}: not found")
            continue
        if not any(not _entry_matches(c, exp) for c in candidates):
            best = min(candidates, key=lambda c: len(_entry_matches(c, exp)))
            diffs = ", ".join(f"{k}: {best.get(k)!r} instead of {exp[k]!r}" for k in _entry_matches(best, exp))
            problems.append(f"{label}: {diffs}")
    return problems


def check_baseline(actual: dict, expected: dict | None, parts: tuple[str, ...] = ("datapack", "sources", "tools", "run")) -> list[str]:
    """Mismatches between a baseline and an expected baseline, one line each (empty when it matches)."""
    if not expected:
        return []
    problems = []
    if "datapack" in parts and isinstance(expected.get("datapack"), dict):
        for key, value in expected["datapack"].items():
            if not value_matches((actual.get("datapack") or {}).get(key), value):
                problems.append(f"datapack {key}: {(actual.get('datapack') or {}).get(key)!r} instead of {value!r}")
    if "sources" in parts:
        problems += _check_list("source", actual.get("sources") or [], expected.get("sources") or [], ("role", "id", "path"))
    if "tools" in parts:
        problems += _check_list("tool", actual.get("tools") or [], expected.get("tools") or [], ("name",))
    if "run" in parts and isinstance(expected.get("run"), dict):
        run = actual.get("run") or {}
        for key in ("lab", "types", "sequencer"):
            exp = expected["run"].get(key)
            if isinstance(exp, dict):
                for k in _entry_matches(run.get(key) or {}, exp):
                    problems.append(f"run {key} {k}: {(run.get(key) or {}).get(k)!r} instead of {exp[k]!r}")
            elif exp is not None and not value_matches(run.get(key), exp):
                problems.append(f"run {key}: {run.get(key)!r} instead of {exp!r}")
        for comp, exp in (expected["run"].get("components") or {}).items():
            got = (run.get("components") or {}).get(comp)
            if got is None:
                problems.append(f"run component {comp}: not bound")
                continue
            for k in _entry_matches(got, exp):
                problems.append(f"run component {comp} {k}: {got.get(k)!r} instead of {exp[k]!r}")
    return problems


def tool_versions() -> list[dict]:
    """testsuit, the installed testsuit plugins (by distribution), Jinja2 and Pydantic."""
    tools = []
    for name in ("testsuit",):
        try:
            tools.append({"name": name, "version": version(name)})
        except PackageNotFoundError:
            tools.append({"name": name, "version": "unknown"})
    seen = set()
    for ep in entry_points(group="testsuit.plugins"):
        dist = getattr(ep, "dist", None)
        if dist is not None and dist.metadata["Name"] not in seen:
            seen.add(dist.metadata["Name"])
            tools.append({"name": dist.metadata["Name"], "kind": "plugin", "version": dist.version})
    for spec in os.environ.get("TESTSUIT_PLUGINS", "").split(","):
        if spec.strip():
            tools.append({"name": spec.strip(), "kind": "plugin", "version": "unknown"})
    for name in ("jinja2", "pydantic"):
        try:
            tools.append({"name": name, "version": version(name)})
        except PackageNotFoundError:
            pass
    return tools
