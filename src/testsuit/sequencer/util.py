"""Small helpers shared by the sequencer modules: durations, sizes, templates, YAML."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jinja2
import yaml

_DURATION = re.compile(r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m|min|h|d)?\s*$")
_DURATION_UNITS = {None: 1.0, "ms": 0.001, "s": 1.0, "m": 60.0, "min": 60.0, "h": 3600.0, "d": 86400.0}
_SIZE = re.compile(r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[KMGTP]i?B|B)?\s*$", re.IGNORECASE)
_SIZE_UNITS = {"": 1, "B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12, "PB": 10**15,
               "KIB": 2**10, "MIB": 2**20, "GIB": 2**30, "TIB": 2**40, "PIB": 2**50}


class SequencerError(Exception):
    """A configuration, inventory, datapack or pipeline the sequencer cannot use."""


def parse_duration(value: Any) -> float | None:
    """Seconds from '30s', '2m', '1.5h', '250ms' or a number of seconds; None stays None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _DURATION.match(str(value))
    if not match:
        raise SequencerError(f"invalid duration '{value}' (expected for example 30s, 2m, 1h)")
    return float(match["value"]) * _DURATION_UNITS[match["unit"]]


def parse_size(value: Any) -> int | None:
    """Bytes from '20GB', '200MB', '1GiB' or a number of bytes; None stays None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = _SIZE.match(str(value))
    if not match:
        raise SequencerError(f"invalid size '{value}' (expected for example 200MB, 20GB)")
    return int(float(match["value"]) * _SIZE_UNITS[(match["unit"] or "").upper()])


def format_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1000 or unit == "TB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1000
    return str(size)


def now() -> str:
    """Current UTC time, ISO 8601, second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_yaml(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except OSError as e:
        raise SequencerError(f"cannot read '{path}': {e}") from e
    except yaml.YAMLError as e:
        raise SequencerError(f"invalid YAML in '{path}': {e}") from e


def dump_yaml(path: Path, data: Any, header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def deep_merge(base: dict, override: dict) -> dict:
    """A new dict: override merged into base, mappings merged key by key."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ── templates ───────────────────────────────────────────────────────────────
# Commands, step fields and conditions are rendered at run time with the datapack's frozen keys
# (context.yml) and the run-time keys (run, phase, component, components, stages, params...).

_SINGLE_EXPRESSION = re.compile(r"^\s*\{\{(?P<expr>(?:(?!\}\}).)*)\}\}\s*$", re.DOTALL)

_env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
_env.filters["shquote"] = lambda s: "'" + str(s).replace("'", "'\"'\"'") + "'"


def render(value: Any, keys: dict, where: str = "") -> Any:
    """Render the Jinja2 templates of a value (strings, lists and dicts, recursively).

    A string made of a single expression keeps the type of its result ("{{ params.others }}" gives
    a list), like the datapack keys.
    """
    if isinstance(value, str):
        if "{{" not in value and "{%" not in value:
            return value
        try:
            match = _SINGLE_EXPRESSION.match(value)
            if match:
                return _env.compile_expression(match["expr"], undefined_to_none=False)(**keys)
            return _env.from_string(value).render(**keys)
        except jinja2.TemplateError as e:
            raise SequencerError(f"{where + ': ' if where else ''}cannot render '{value}': {e}") from e
    if isinstance(value, list):
        return [render(v, keys, where) for v in value]
    if isinstance(value, dict):
        return {k: render(v, keys, where) for k, v in value.items()}
    return value


def evaluate(expression: str, keys: dict, where: str = "") -> Any:
    """Evaluate a Jinja2 expression ('if' conditions), braces optional."""
    expression = expression.strip()
    match = _SINGLE_EXPRESSION.match(expression)
    if match:
        expression = match["expr"]
    try:
        return _env.compile_expression(expression, undefined_to_none=False)(**keys)
    except jinja2.TemplateError as e:
        raise SequencerError(f"{where + ': ' if where else ''}cannot evaluate '{expression}': {e}") from e
