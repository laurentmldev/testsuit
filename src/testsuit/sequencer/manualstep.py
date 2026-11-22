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

"""The ``manual`` action of a pipeline step (SPEC section 6.9): an operation a person does on the bench, such as
plugging a cable, that the run waits for::

    - id: plug_cable
      manual:
        title: Plug cable A on pin F             # optional, the step id by default
        text: |                                  # Markdown, templates allowed
          Plug **cable A** on pin F of {{ slots.dut.name }}.

          ![Pin F](images/pin_f.jpg)             # relative to the test definition: copied into the datapack
        checks:                                  # each acknowledged by the operator before "Done"
          - Cable A plugged on pin F
          - Power LED green
        redo_from: [power_on]                    # optional: earlier steps the operator may run again from
      timeout: 30m                               # optional: error when nobody answered by then

The step runs on the sequencer host (no slot) and waits: the Run page opens a dialog with the text, its pictures and
its checks, and the All runs list flags the run as expecting a manual operation. The operator ticks each check, then
answers **Done** (succeeded, once every check is ticked) or **Failed** (failed, with a note). This module is the shared
part: the checks of the step, the pictures it shows (for the datapack), and the state the engine keeps of it.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from testsuit.sequencer.util import SequencerError, now

MANUAL_KEYS = ("title", "text", "checks", "redo_from")
# a Markdown picture: ![alt](path "title"), the path without spaces
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
DONE, FAILED = "done", "failed"


def check_manual(spec: Any, where: str) -> None:
    """A ``manual`` action: a mapping with a text or checks, its checks a list of texts."""
    if not isinstance(spec, Mapping):
        raise SequencerError(f"{where}: 'manual' takes a mapping with 'text' and/or 'checks'")
    unknown = sorted(set(map(str, spec)) - set(MANUAL_KEYS))
    if unknown:
        raise SequencerError(f"{where}: unknown 'manual' key(s) {', '.join(unknown)} (known: {', '.join(MANUAL_KEYS)})")
    checks = spec.get("checks", [])
    if not isinstance(checks, list) or not all(isinstance(c, (str, int, float)) and str(c).strip() for c in checks):
        raise SequencerError(f"{where}: 'manual.checks' is a list of non-empty texts")
    redo = spec.get("redo_from", [])
    if not isinstance(redo, list) or not all(isinstance(r, str) and r.strip() for r in redo):
        raise SequencerError(f"{where}: 'manual.redo_from' is a list of ids of earlier steps of its phase")
    if not str(spec.get("text") or "").strip() and not checks:
        raise SequencerError(f"{where}: 'manual' needs a 'text' or 'checks' telling the operator what to do")
    for path in images(spec):
        if not is_local(path):
            continue
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts:
            raise SequencerError(f"{where}: picture '{path}': give a path relative to the test definition, "
                                 "inside its folder (no '..'), or a web address")


def images(spec: Mapping) -> list[str]:
    """The pictures the text of a manual step shows, each once."""
    out: list[str] = []
    for path in IMAGE_RE.findall(str(spec.get("text") or "")):
        if path not in out:
            out.append(path)
    return out


def is_local(path: str) -> bool:
    """A picture shipped with the datapack (not a web address nor inline data)."""
    return not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", path) and not path.startswith("//") and "{{" not in path


def pipeline_manual_images(pipeline: Mapping | None, definition_dir: Path | None = None) -> list[str]:
    """The local pictures the manual steps of a pipeline show (its phases, its sub-pipelines and those of its
    included files), relative to the test definition, each once."""
    from testsuit.sequencer.exploitstep import _included
    if not isinstance(pipeline, Mapping):
        return []
    found: list[str] = []

    def steps_of(steps: Any) -> None:
        for step in steps if isinstance(steps, list) else []:
            if isinstance(step, Mapping) and isinstance(step.get("manual"), Mapping):
                found.extend(p for p in images(step["manual"]) if is_local(p) and p not in found)

    for phase in pipeline.get("phases") or []:
        if isinstance(phase, Mapping):
            steps_of(phase.get("steps"))
            steps_of(phase.get("finally"))
    subs = dict(pipeline.get("subpipelines") or {}) if isinstance(pipeline.get("subpipelines"), Mapping) else {}
    for name in pipeline.get("include") or []:
        subs.update({k: v for k, v in _included(str(name), definition_dir).items() if k not in subs})
    for sub in subs.values():
        if isinstance(sub, Mapping):
            steps_of(sub.get("steps"))
    return found


# ── at run time ─────────────────────────────────────────────────────────────

def waiting_record(step_id: str, spec: Mapping) -> dict:
    """What a step record keeps of a manual operation it waits for (the Run page's dialog)."""
    return {"title": str(spec.get("title") or step_id), "text": str(spec.get("text") or ""),
            "checks": [{"text": str(c), "done": False, "bypassed": False, "comment": ""} for c in spec.get("checks") or []],
            "base": "definition/", "since": now(), "outcome": None, "note": "",
            **({"redo_from": [str(r) for r in spec["redo_from"]]} if spec.get("redo_from") else {})}


def settled(check: Mapping) -> bool:
    """A check that no longer holds Done back: ticked, or bypassed with a reason."""
    return bool(check.get("done") or check.get("bypassed"))


def acknowledge(manual: dict, body: Mapping, by: str = "") -> str | None:
    """Apply an operator's answer to a manual operation still waiting. On one check (``check``: its index):
    ``done`` ticks it or not, ``bypass`` bypasses it or not (a bypass needs a ``comment``, its reason), ``comment``
    sets its comment, ``nonconform`` with a bypass makes the run non-conform once the operation is done. On the
    operation: ``note`` sets the global comment, ``outcome`` ends it (``done`` once every
    check is ticked or bypassed, or ``failed``). Returns the outcome when it ends."""
    if manual.get("outcome"):
        raise SequencerError(f"this manual operation is already {manual['outcome']}")
    checks = manual["checks"]
    if "check" in body:
        try:
            index = int(body["check"])
            check = checks[index]
        except (TypeError, ValueError, IndexError):
            raise SequencerError(f"no check {body.get('check')!r}: the operation has {len(checks)}") from None
        if "comment" in body:
            check["comment"] = str(body.get("comment") or "").strip()
        if body.get("bypass"):
            if not check.get("comment"):
                raise SequencerError(f"check '{check['text']}': give a comment saying why it is bypassed")
            # the operator says whether going on without it makes the run non-conform
            check.update(bypassed=True, done=False, nonconform=bool(body.get("nonconform")))
        elif "bypass" in body:
            check["bypassed"] = False
            check.pop("nonconform", None)
        if "done" in body or not ({"comment", "bypass"} & set(body)):
            check["done"] = bool(body.get("done", True))
            if check["done"]:
                check["bypassed"] = False
                check.pop("nonconform", None)
        check.update({"by": by, "at": now()} if settled(check) or check.get("comment") else {"by": None, "at": None})
    if "note" in body:
        manual["note"] = str(body.get("note") or "").strip()
    outcome = body.get("outcome")
    if outcome is None:
        return None
    if outcome not in (DONE, FAILED):
        raise SequencerError(f"outcome '{outcome}': '{DONE}' or '{FAILED}'")
    if outcome == DONE and not all(settled(c) for c in checks):
        left = [c["text"] for c in checks if not settled(c)]
        raise SequencerError(f"tick or bypass every check before 'done' ({len(left)} left: {', '.join(left)})")
    manual.update(outcome=outcome, by=by, at=now())
    return outcome


def bypassed(manual: Mapping) -> list[dict]:
    """The checks of a manual operation that were bypassed."""
    return [c for c in manual.get("checks") or [] if c.get("bypassed")]


def awaiting(steps: list[dict] | None) -> list[dict]:
    """The manual operations a run expects now, from its step records: ``{step, title, since}``."""
    out = []
    for s in steps or []:
        manual = s.get("manual")
        if s.get("state") == "running" and manual and not manual.get("outcome"):
            key = f"{s.get('phase')}:{s.get('id')}" + (f"[{s['slot']}]" if s.get("slot") else "")
            out.append({"step": key, "title": manual.get("title"), "since": manual.get("since")})
    return out
