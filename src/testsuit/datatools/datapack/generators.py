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

"""Generators: slot-specific processing that produces an input file (``method: generate``,
sequencer README section 5.1)::

    inputs:
      - source: templates/profile.yml
        target: profile.bin
        method: generate
        generator: toytrain-profile-compiler   # registered by a plugin
        options: {step_mps: "{{ train.pilot_profile.step_mps }}"}

A generator is either registered with ``register_generator(name, function)``, the function being
called as ``function(source: Path, target: Path, keys: Mapping, options: dict, slot: dict)``,
or the path (relative to the setup folder) of a script, run as ``<script> <source> <target> <keys.json>``
(``.py`` with the current Python, ``.sh`` with bash, anything else executed directly). The JSON file
holds every resolved key, plus ``options`` and ``slot`` (with ``input_dir`` and the inputs of
the slots it needs, ``slots.<id>.input_dir``).

``keys`` is a lazy view of the keys: the ones a registered generator reads are recorded in
``datapack.yml`` like those of templates. A script gets them all, so none is recorded for it.
"""
from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from testsuit.datatools.datapack.procs import run_logged

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


def run_generator(name: str, source: Path, target: Path, keys: Mapping, options: dict, slot: dict,
                  scripts_folder: Path, all_keys: Callable[[], dict]) -> bool:
    """Run a generator; return True if it was a registered one (whose key reads are tracked)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if name in _generators:
        try:
            _generators[name](Path(source), Path(target), keys, dict(options), dict(slot))
        except GeneratorError:
            raise
        except Exception as e:
            raise GeneratorError(f"generator '{name}' failed on '{source}': {type(e).__name__}: {e}") from e
        return True
    script = (scripts_folder / name).resolve()
    if not script.is_file():
        raise GeneratorError(f"unknown generator '{name}': not registered (registered: "
                             f"{', '.join(sorted(_generators)) or 'none'}) and no script '{script}'")
    data = {**all_keys(), "options": options, "slot": slot}
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
        # its output (stderr included) in the log as it comes; never waiting for an input
        returncode, tail = run_logged(command + [str(source), str(target), keys_file])
    except OSError as e:
        raise GeneratorError(f"generator script '{name}' cannot be run: {e}") from e
    finally:
        Path(keys_file).unlink(missing_ok=True)
    if returncode != 0:
        last = tail.strip().splitlines()[-1] if tail.strip() else "no output"
        raise GeneratorError(f"generator script '{name}' failed on '{source}' (exit status {returncode}): {last}")
    if not target.exists():
        raise GeneratorError(f"generator script '{name}' did not write '{target}'")
    return False
