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
"""The Lab view of the UI (README section 13.1): how the components are linked, deduced from the lab configuration,
their pictures, and what the runs do on them now."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from testsuit.sequencer.inventory import ICON_EXTENSIONS, ICONS, Component, Stage
from testsuit.sequencer.util import SequencerError

# keys naming another component in a command, a probe or a self-test: {{ stage.roles.<role>.host }}...
_REFERENCE = re.compile(r"\b(stage\.roles|stage\.components|lab\.components)"
                        r"(?:\.([A-Za-z_][A-Za-z0-9_]*)|\[\s*['\"]([^'\"]+)['\"]\s*\])")
_ACTIVE_STEP = ("running", "ready")


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _role_member(lab, stage: Stage | None, role: str, me: str) -> Component | None:
    """The component a ``stage.roles.<role>`` key gives in that stage (the first one of that role)."""
    for cid in stage.components if stage else []:
        c = lab.inventory.components[cid]
        if c.role == role:
            return c if c.id != me else None
    return None


def one_line(spec: Any, limit: int = 120) -> str:
    """A command, a probe or a variable on one line: its shell command, its http call, its python script..."""
    if isinstance(spec, dict):
        for key in ("probe", "check"):                      # a status probe: what it runs
            if isinstance(spec.get(key), (dict, str)):
                return one_line(spec[key], limit)
        if spec.get("run") is not None:
            text = f"{spec['run']}"
        elif spec.get("http") is not None:
            http = spec["http"]
            text = (f"{str(http.get('method') or 'GET').upper()} {http.get('url', '')}" if isinstance(http, dict)
                    else f"GET {http}")
        elif spec.get("python") is not None:
            py = spec["python"]
            text = f"python {py.get('script', '') if isinstance(py, dict) else py}"
        elif spec.get("signal") is not None:
            text = f"signal {spec['signal']}"
        elif spec.get("builtin") is not None:
            text = f"builtin {spec['builtin']}"
        elif spec.get("plugin") is not None or spec.get("uses") is not None:
            text = f"{spec.get('plugin') or spec.get('uses')}"
        else:
            text = " ".join(f"{k}: {v}" for k, v in spec.items() if k != "via")
    elif isinstance(spec, (list, tuple)):
        text = " ; ".join(one_line(v, limit) for v in spec)
    else:
        text = "" if spec is None else f"{spec}"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def lab_links(lab) -> list[dict]:
    """The links between components, deduced from the lab configuration, each ``{source, target, kind, label,
    details: [{title, command}], stages: [...]}``, the arrow going from the source to the target:

    - ``probe``: the target's status probe or some of its commands run through the source (``via``);
    - ``cmd``: the target's commands, probe, self-test or variables name the source (``stage.roles.<role>``,
      ``stage.components.<id>``, ``lab.components.<id>``);
    - ``link``: ``tsq_links`` of the source, a wiring the commands do not show.

    ``details`` has one line per probe, command or variable behind the link: its title (``status probe``,
    ``command start``...) and what it runs, on one line.
    """
    inv = lab.inventory
    found: dict[tuple[str, str, str], dict] = {}

    def add(source: Component | None, target: Component, kind: str, title: str, command: str, stage: Stage | None,
            label: str = ""):
        if source is None or target.id == source.id:
            return
        link = found.setdefault((source.id, target.id, kind), {"source": source.id, "target": target.id, "kind": kind,
                                                               "label": label, "details": [], "stages": []})
        detail = {"title": title, "command": command}
        if detail not in link["details"]:
            link["details"].append(detail)
        if stage is not None and stage.id not in link["stages"]:
            link["stages"].append(stage.id)
        if label and not link["label"]:
            link["label"] = label

    for comp in inv.components.values():
        for link in comp.links:
            target = inv.components.get(link["to"])
            if target is not None:
                add(comp, target, "link", "tsq_links", link["label"], None, link["label"])
        for stage in [inv.stages[s] for s in comp.stages] or [None]:
            try:
                resolved = lab.resolve(comp, stage)
            except SequencerError:
                continue
            try:
                add(lab.via(comp, stage, resolved.status), comp, "probe", "status probe", one_line(resolved.status),
                    stage)
            except SequencerError:
                pass
            try:
                for name, via in lab.command_vias(comp, stage, resolved).items():
                    add(via, comp, "probe", f"command {name}", one_line(resolved.commands[name]), stage)
            except SequencerError:
                pass
            parts = [(f"command {n}", spec) for n, spec in resolved.commands.items()]
            parts += [("status probe", resolved.status), ("self-test", resolved.selftest), ("collect", resolved.collect)]
            parts += [(f"variable {k}", v) for k, v in comp.vars_in(stage).items()
                      if k not in ("tsq_commands", "tsq_status", "tsq_selftest", "tsq_collect", "tsq_links")]
            for what, value in parts:
                for text in _strings(value):
                    for m in _REFERENCE.finditer(text):
                        kind, name = m.group(1), m.group(2) or m.group(3)
                        if kind == "stage.roles":
                            source = _role_member(lab, stage, name, comp.id)
                        elif kind == "stage.components":
                            source = inv.components.get(name) if stage and name in stage.components else None
                        else:
                            source = inv.components.get(name)
                        add(source, comp, "cmd", what, one_line(value), stage)
    return list(found.values())


def icon_of(lab, component: Component) -> dict | None:
    """How the Lab view draws a component: ``{"builtin": name}``, ``{"file": path}`` (an image of the lab folder,
    relative to the inventory) or ``{"error": why}``; None when it has no ``tsq_icon``."""
    icon = component.icon.strip()
    if not icon:
        return None
    if icon in ICONS:
        return {"builtin": icon}
    path = icon_path(lab, component)
    if Path(icon).suffix.lower() not in ICON_EXTENSIONS:
        return {"error": f"tsq_icon '{icon}' is neither one of {', '.join(ICONS)} nor an image file "
                         f"({', '.join(ICON_EXTENSIONS)})"}
    if path is None:
        return {"error": f"tsq_icon '{icon}': no such image in the lab folder"}
    return {"file": icon}


def icon_path(lab, component: Component) -> Path | None:
    """The image file of a component's ``tsq_icon``, relative to the inventory, inside the lab folder."""
    icon = component.icon.strip()
    if not icon or icon in ICONS or Path(icon).suffix.lower() not in ICON_EXTENSIONS:
        return None
    path = (lab.inventory.file.parent / icon).resolve()
    roots = [lab.folder.resolve(), lab.inventory.file.parent.resolve()]
    if not any(path.is_relative_to(r) for r in roots) or not path.is_file():
        return None
    return path


def _step_label(step) -> str:
    if step is None:
        return ""
    if step.action == "do" and step.do:
        return str(step.do)
    if step.run:
        text = " ".join(str(step.run).split())
        return f"run {text[:40]}…" if len(text) > 40 else f"run {text}"
    if step.python:
        script = step.python.get("script") if isinstance(step.python, dict) else None
        return f"python {Path(str(script)).name}" if script else "python"
    if step.http:
        return "http"
    return str(step.uses or step.action)


def lab_activity(seq) -> dict:
    """The active runs (``runs``: id, datapack name, test definition, state, phase, bound stages and components),
    and what each component runs now (``components``: its running steps, with their command)."""
    from testsuit.sequencer.scheduler import Binding
    from testsuit.sequencer.store import ACTIVE_STATES
    runs: dict[str, dict] = {}
    activity: dict[str, list[dict]] = {}
    for run in seq.store.runs(ACTIVE_STATES, limit=1000):
        binding = Binding.from_dict(run.get("binding"))
        runs[run["id"]] = {"id": run["id"], "name": run.get("name"), "testdef": run.get("testdef"),
                           "state": run["state"], "phase": run.get("phase"), "waiting_for": run.get("waiting_for"),
                           "stages": binding.stage_slots, "slots": binding.slots}
        execution = seq.executions.get(run["id"])
        steps: dict[tuple[str, str], Any] = {}
        if execution is not None and execution.ctx is not None:
            records = [r.as_dict() for r in execution.ctx.records.values()]
            for flat in execution.datapack.phases:
                for fs in [*flat.steps, *flat.finally_]:
                    steps[(flat.phase.id, fs.id)] = fs.step
        else:                                  # a run of another process (--local): its last snapshot
            records = run.get("steps") or []
        for rec in records:
            slot = rec.get("slot") or rec.get("component") or ""
            if rec.get("state") not in _ACTIVE_STEP or slot not in binding.slots:
                continue
            activity.setdefault(binding.slots[slot], []).append({
                "run": run["id"], "phase": rec["phase"], "step": rec["id"], "slot": slot, "state": rec["state"],
                "started": rec.get("started"), "command": _step_label(steps.get((rec["phase"], rec["id"])))})
    return {"runs": runs, "components": activity}


def lab_view(seq) -> dict:
    """Everything the Lab view draws beyond the stages and components (``GET /lab/view``)."""
    lab = seq.lab
    if lab._view_links is None:                               # the lab does not change until it is reloaded
        lab._view_links = lab_links(lab)
    icons = {c.id: icon for c in lab.inventory.components.values() if (icon := icon_of(lab, c)) is not None}
    return {"links": lab._view_links, "icons": icons, **lab_activity(seq)}


# ── the arrangement of the Lab view, saved by a user for everyone ─────────────────

def layout_file(seq) -> Path:
    return seq.config.root / "lab_layout.json"


def read_layout(seq) -> dict:
    """The saved arrangement of the Lab view: ``{"components": {id: {"x", "y", "opacity"}}, "saved_at"}``, or
    ``{"components": {}}`` when it is automatic."""
    import json
    try:
        data = json.loads(layout_file(seq).read_text(encoding="utf-8"))
        return data if isinstance(data.get("components"), dict) else {"components": {}}
    except (OSError, ValueError, AttributeError):
        return {"components": {}}


def save_layout(seq, body: dict) -> dict:
    """Save the arrangement (positions and opacities of the components), checked: numbers only, known components.
    An empty one goes back to the automatic layout."""
    import json

    from testsuit.sequencer.util import now
    components = body.get("components")
    if not isinstance(components, dict):
        raise SequencerError("layout: 'components' must be a mapping component -> {x, y, opacity}")
    known = seq.lab.inventory.components
    clean: dict[str, dict] = {}
    for cid, item in components.items():
        if cid not in known or not isinstance(item, dict):
            continue
        entry = {}
        for key in ("x", "y"):
            if isinstance(item.get(key), (int, float)) and abs(item[key]) < 1e6:
                entry[key] = round(float(item[key]), 1)
        if isinstance(item.get("opacity"), (int, float)):
            entry["opacity"] = min(1.0, max(0.05, round(float(item["opacity"]), 2)))
        if entry:
            clean[cid] = entry
    path = layout_file(seq)
    if not clean:
        path.unlink(missing_ok=True)
        return {"components": {}}
    layout = {"components": clean, "saved_at": now()}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(layout, indent=1), encoding="utf-8")
    tmp.replace(path)
    return layout
