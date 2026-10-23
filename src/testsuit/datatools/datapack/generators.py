"""Generators: component-specific processing that produces an input file (``method: generate``,
sequencer README section 5.1)::

    inputs:
      - source: templates/profile.yml
        target: profile.bin
        method: generate
        generator: toytrain-profile-compiler   # registered by a plugin
        options: {step_mps: "{{ train.pilot_profile.step_mps }}"}

A generator is either registered with ``register_generator(name, function)``, the function being
called as ``function(source: Path, target: Path, keys: Mapping, options: dict, component: dict)``,
or the path (relative to the setup folder) of a script, run as ``<script> <source> <target> <keys.json>``
(``.py`` with the current Python, ``.sh`` with bash, anything else executed directly). The JSON file
holds every resolved key, plus ``options`` and ``component`` (with ``input_dir`` and the inputs of
the components it needs, ``components.<id>.input_dir``).

``keys`` is a lazy view of the keys: the ones a registered generator reads are recorded in
``datapack.yml`` like those of templates. A script gets them all, so none is recorded for it.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

Generator = Callable[..., None]

_generators: dict[str, Generator] = {}


class GeneratorError(Exception):
    """A generator failed or is unknown."""


def register_generator(name: str, function: Generator) -> None:
    """Declare a generator, usable as ``method: generate, generator: <name>`` in setup files."""
    _generators[name] = function


def unregister_generator(name: str) -> None:
    _generators.pop(name, None)


def get_generators() -> list[str]:
    return list(_generators)


def run_generator(name: str, source: Path, target: Path, keys: Mapping, options: dict, component: dict,
                  scripts_folder: Path, all_keys: Callable[[], dict]) -> bool:
    """Run a generator; return True if it was a registered one (whose key reads are tracked)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if name in _generators:
        try:
            _generators[name](Path(source), Path(target), keys, dict(options), dict(component))
        except GeneratorError:
            raise
        except Exception as e:
            raise GeneratorError(f"generator '{name}' failed on '{source}': {type(e).__name__}: {e}") from e
        return True
    script = (scripts_folder / name).resolve()
    if not script.is_file():
        raise GeneratorError(f"unknown generator '{name}': not registered (registered: "
                             f"{', '.join(sorted(_generators)) or 'none'}) and no script '{script}'")
    data = {**all_keys(), "options": options, "component": component}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(data, f, default=str)
        keys_file = f.name
    if script.suffix == ".py":
        command = [sys.executable, str(script)]
    elif script.suffix == ".sh":
        command = ["bash", str(script)]
    else:
        command = [str(script)]
    try:
        result = subprocess.run(command + [str(source), str(target), keys_file], capture_output=True, text=True)
    finally:
        Path(keys_file).unlink(missing_ok=True)
    if result.returncode != 0:
        raise GeneratorError(f"generator script '{name}' failed (exit {result.returncode}): "
                             f"{(result.stderr or result.stdout).strip()[-500:]}")
    if not target.exists():
        raise GeneratorError(f"generator script '{name}' did not write '{target}'")
    return False
