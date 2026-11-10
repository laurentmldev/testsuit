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


def templates(spec: Any) -> list[str]:
    """What a command, a probe, a self-test or a variable runs, one template per command (a probe or a self-test
    of several commands gives several): its shell command, its http request line, its python script..."""
    if isinstance(spec, (list, tuple)):
        return [t for item in spec for t in templates(item)]
    if not isinstance(spec, dict):
        return [] if spec is None else [f"{spec}"]
    if isinstance(spec.get("probe"), dict):                 # a status probe: what it runs
        return templates(spec["probe"])
    if spec.get("run") is not None:
        return templates(spec["run"]) if isinstance(spec["run"], list) else [f"{spec['run']}"]
    if spec.get("http") is not None:
        http = spec["http"] if isinstance(spec["http"], dict) else {"url": spec["http"]}
        method = str(http.get("method") or "GET")
        return [f"{method if '{{' in method else method.upper()} {http.get('url', '')}"]
    if spec.get("python") is not None:
        py = spec["python"]
        return [f"python {py.get('script', '') if isinstance(py, dict) else py}"]
    for key in ("signal", "builtin"):
        if spec.get(key) is not None:
            return [f"{key} {spec[key]}"]
    skip = {"via", "id", "timeout", "states", "detach", "description", "expect"}
    return [f"{k} {v}" if not isinstance(v, (dict, list)) else f"{k} {' '.join(templates(v))}"
            for k, v in spec.items() if k not in skip]


def lab_links(lab) -> list[dict]:
    """The links between components, deduced from the lab configuration, each ``{source, target, kind, label,
    details: [{title, commands}], stages: [...]}``, the arrow going from the source to the target:

    - ``probe``: the target's status probe or some of its commands run through the source (``via``);
    - ``cmd``: the target's commands, probe, self-test or variables name the source (``stage.roles.<role>``,
      ``stage.components.<id>``, ``lab.components.<id>``);
    - ``link``: ``tsq_links`` of the source, a wiring the commands do not show (for the diagram only).

    ``details`` has one entry per probe, command or variable behind the link: its title (``status probe``,
    ``command start``...) and the templates of what it runs (``commands``); ``lab_view`` renders them with the
    keys of the lab, as the commands run by hand show them.
    """
    inv = lab.inventory
    found: dict[tuple[str, str, str], dict] = {}

    def add(source: Component | None, target: Component, kind: str, title: str, commands: list[str],
            stage: Stage | None, via: Component | None = None, label: str = ""):
        if source is None or target.id == source.id:
            return
        link = found.setdefault((source.id, target.id, kind), {"source": source.id, "target": target.id, "kind": kind,
                                                               "label": label, "details": [], "stages": []})
        if not any(d["title"] == title for d in link["details"]):
            link["details"].append({"title": title, "commands": commands, "_render": (
                target.id, stage.id if stage else None, via.id if via else None)})
        if stage is not None and stage.id not in link["stages"]:
            link["stages"].append(stage.id)
        if label and not link["label"]:
            link["label"] = label

    for comp in inv.components.values():
        for link in comp.links:
            target = inv.components.get(link["to"])
            if target is not None:
                add(comp, target, "link", "tsq_links", [link["label"]] if link["label"] else [], None,
                    label=link["label"])
        for stage in [inv.stages[s] for s in comp.stages] or [None]:
            try:
                resolved = lab.resolve(comp, stage)
            except SequencerError:
                continue
            try:
                probe_via = lab.via(comp, stage, resolved.status)
            except SequencerError:
                probe_via = None
            try:
                command_vias = lab.command_vias(comp, stage, resolved)
            except SequencerError:
                command_vias = {}
            add(probe_via, comp, "probe", "status probe", templates(resolved.status), stage, probe_via)
            for name, via in command_vias.items():
                add(via, comp, "probe", f"command {name}", templates(resolved.commands[name]), stage, via)
            parts = [(f"command {n}", spec, command_vias.get(n)) for n, spec in resolved.commands.items()]
            parts += [("status probe", resolved.status, probe_via), ("self-test", resolved.selftest, probe_via),
                      ("collect", resolved.collect, None)]
            parts += [(f"variable {k}", v, None) for k, v in comp.vars_in(stage).items()
                      if k not in ("tsq_commands", "tsq_status", "tsq_selftest", "tsq_collect", "tsq_links")]
            for what, value, via in parts:
                for text in _strings(value):
                    for m in _REFERENCE.finditer(text):
                        kind, name = m.group(1), m.group(2) or m.group(3)
                        if kind == "stage.roles":
                            source = _role_member(lab, stage, name, comp.id)
                        elif kind == "stage.components":
                            source = inv.components.get(name) if stage and name in stage.components else None
                        else:
                            source = inv.components.get(name)
                        add(source, comp, "cmd", what, templates(value), stage, via)
    return list(found.values())


def rendered_links(seq, links: list[dict]) -> list[dict]:
    """The links with each command as it would run (``segments``: its text, with the key behind each value, as
    for the commands run by hand), rendered with the keys of the lab and the remote root of each component."""
    lab = seq.lab
    inv = lab.inventory
    keys_of: dict[tuple, dict] = {}
    out = []
    for link in links:
        details = []
        for d in link["details"]:
            cid, sid, vid = d["_render"]
            if d["title"] == "tsq_links":
                details.append({"title": d["title"], "commands": [[{"text": t, "key": None}] for t in d["commands"]]})
                continue
            if (cid, sid, vid) not in keys_of:
                comp = inv.components[cid]
                stage = inv.stages.get(sid) if sid else None
                via = inv.components.get(vid) if vid else None
                try:
                    root = seq.transport_for(cid)._root
                except Exception:
                    root = str(comp.vars.get("tsq_remote_root", "/tmp/tsq"))
                keys_of[(cid, sid, vid)] = seq._manual_keys(comp, stage, root, via)
            keys = keys_of[(cid, sid, vid)]
            details.append({"title": d["title"], "commands": [seq._segments(t, keys, f"{cid} {d['title']}")
                                                              for t in d["commands"]]})
        out.append({**link, "details": details})
    return out


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
    return {"links": rendered_links(seq, lab._view_links), "icons": icons, **lab_activity(seq)}


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
