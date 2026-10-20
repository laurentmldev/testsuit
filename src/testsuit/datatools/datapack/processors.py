"""Processors: steps run on an imported dataset folder, or on a generated input file.

A processor is named in a setup file, with optional arguments separated by spaces::

    datasets:
      - id: bench_calib
        version: v1.2
        postprocess: [finalize]
    inputs:
      - source: templates/bench_config.yml
        target: bench_config.yml
        postprocess: ["replace s/bench-A/bench-B/g"]

A name is either a registered processor, or the path (relative to the setup folder) of a script,
run with the target path followed by the arguments (``.py`` with the current Python, ``.sh`` with
bash, anything else executed directly).

Built-in processors:

- ``finalize``: on a dataset folder, records the import date in its ``dataset.yml``
  (``<dataset id>.importdate``) and makes the folder read-only.
- ``replace s/<regex>/<replacement>/<flags>``: on a file, or every file of a folder, a sed-like
  substitution (flags: ``g`` all occurrences, ``i`` ignore case, ``m`` multiline, ``s`` dot matches
  newlines).

An external library adds its own with ``register_processor(name, function)``, the function being
called as ``function(target: Path, *args: str)`` (see testsuit.plugins).
"""
from __future__ import annotations

import re
import shlex
import stat
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import yaml

Processor = Callable[..., None]

_processors: dict[str, Processor] = {}

DATASET_KEYS_FILE = "dataset.yml"


class ProcessorError(Exception):
    """A processor failed or is unknown."""


def register_processor(name: str, function: Processor) -> None:
    """Declare a processor, usable as ``postprocess: [<name> args...]`` in setup files."""
    _processors[name] = function


def unregister_processor(name: str) -> None:
    _processors.pop(name, None)


def get_processors() -> list[str]:
    return list(_processors)


def run_processor(spec: str, target: Path, scripts_folder: Path) -> None:
    """Run the processor described by spec ('name arg1 arg2...') on target."""
    try:
        name, *args = shlex.split(spec)
    except ValueError as e:
        raise ProcessorError(f"invalid processor '{spec}': {e}") from e
    if name in _processors:
        try:
            _processors[name](Path(target), *args)
        except ProcessorError:
            raise
        except Exception as e:
            raise ProcessorError(f"processor '{name}' failed on '{target}': {e}") from e
        return

    script = (scripts_folder / name).resolve()
    if not script.is_file():
        raise ProcessorError(f"unknown processor '{name}' (registered: {', '.join(sorted(_processors))}; "
                             f"no script '{script}')")
    if script.suffix == ".py":
        command = [sys.executable, str(script)]
    elif script.suffix == ".sh":
        command = ["bash", str(script)]
    else:
        command = [str(script)]
    result = subprocess.run(command + [str(target)] + args, check=False)
    if result.returncode != 0:
        raise ProcessorError(f"script '{script}' failed on '{target}' (exit status {result.returncode})")


########################## built-in processors ##########################

def _finalize(target: Path) -> None:
    dataset_id = target.parent.name
    keys_file = target / DATASET_KEYS_FILE
    keys = {}
    if keys_file.is_file():
        keys = yaml.safe_load(keys_file.read_text(encoding="utf-8")) or {}
    now = datetime.now()
    keys.setdefault(dataset_id, {}).update(
        {"importdate": now.strftime("%Y-%m-%d %H:%M:%S"), "importdate_sec": int(now.timestamp())})
    keys_file.write_text(yaml.safe_dump(keys, sort_keys=False), encoding="utf-8")

    read_only = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
    for path in sorted(target.rglob("*"), reverse=True):
        if not path.is_symlink():
            path.chmod(read_only | (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH if path.is_dir() else 0))
    target.chmod(read_only | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _replace(target: Path, expression: str) -> None:
    match = re.match(r"^s/(.*)/(.*)/([gimsx]*)$", expression)
    if not match:
        raise ProcessorError(f"replace: invalid expression '{expression}', expected s/<regex>/<replacement>/<flags>")
    pattern, replacement, flags = match.groups()
    re_flags = (re.IGNORECASE if "i" in flags else 0) | (re.MULTILINE if "m" in flags else 0) \
        | (re.DOTALL if "s" in flags else 0) | (re.VERBOSE if "x" in flags else 0)
    regex = re.compile(pattern, re_flags)
    files = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.is_file())
    for file in files:
        text = file.read_text(encoding="utf-8")
        file.write_text(regex.sub(replacement, text, count=0 if "g" in flags else 1), encoding="utf-8")


register_processor("finalize", _finalize)
register_processor("replace", _replace)
