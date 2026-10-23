"""Lab inventory loader (README section 6): the Ansible YAML inventory format, read without Ansible.

- a group with ``tsq_stage_type`` is a **stage**, its hosts being the stage's components;
- a host in several stage groups is a shared component;
- a host directly under ``all`` (in no stage) is an **ad hoc component**;
- variables follow Ansible precedence: host over group (child over parent) over ``all``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from testsuit.sequencer.util import SequencerError, deep_merge, load_yaml

LOCK_MODES = ("exclusive", "shared", "none")


@dataclass
class Component:
    id: str
    vars: dict[str, Any]                         # all < groups < host (stage variables not included)
    stages: list[str] = field(default_factory=list)

    @property
    def host(self) -> str:
        return str(self.vars.get("ansible_host", self.id))

    @property
    def type(self) -> str:
        return str(self.vars.get("tsq_type", "linux_system"))

    @property
    def role(self) -> str:
        return str(self.vars.get("tsq_role", ""))

    @property
    def lock(self) -> str:
        return str(self.vars.get("tsq_lock", "exclusive"))

    @property
    def label(self) -> str:
        return str(self.vars.get("tsq_label", self.id))

    @property
    def adhoc(self) -> bool:
        return not self.stages

    def vars_in(self, stage: "Stage | None") -> dict[str, Any]:
        """Variables of the component as seen in a stage: all < stage group < host."""
        if stage is None:
            return dict(self.vars)
        return {**self.vars, **{k: v for k, v in stage.vars.items() if k not in self._host_vars}}

    _host_vars: set = field(default_factory=set, repr=False)


@dataclass
class Stage:
    id: str
    type: str
    components: list[str]
    vars: dict[str, Any]                         # group variables (tsq_commands included)

    @property
    def commands(self) -> dict[str, Any]:
        return dict(self.vars.get("tsq_commands") or {})


@dataclass
class Inventory:
    file: Path
    vars: dict[str, Any]                         # all.vars
    components: dict[str, Component]
    stages: dict[str, Stage]

    @property
    def type_folders(self) -> list[Path]:
        folders = self.vars.get("tsq_types") or []
        if isinstance(folders, str):
            folders = [folders]
        return [(self.file.parent / Path(f).expanduser()).resolve() for f in folders]

    @property
    def adhoc(self) -> list[Component]:
        return [c for c in self.components.values() if c.adhoc]

    def stages_of_type(self, stage_type: str) -> list[Stage]:
        return [s for s in self.stages.values() if s.type == stage_type]

    def component_in(self, stage: Stage, role: str) -> Component | None:
        for comp_id in stage.components:
            if self.components[comp_id].role == role:
                return self.components[comp_id]
        return None


def _mapping(value: Any, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SequencerError(f"inventory: {where} must be a mapping")
    return value


def load_inventory(path: Path) -> Inventory:
    """Read an Ansible YAML inventory file."""
    data = load_yaml(path)
    if not isinstance(data, dict) or "all" not in data:
        raise SequencerError(f"inventory '{path}': expected an Ansible YAML inventory with a top-level 'all' group")
    root = _mapping(data["all"], "all")

    host_vars: dict[str, dict] = {}          # merged host-level variables
    host_groups: dict[str, list[tuple[str, dict]]] = {}   # host -> [(group, inherited group vars)]
    stage_defs: dict[str, dict] = {}         # stage -> {vars, hosts}

    def visit(name: str, group: dict, inherited: dict, depth: int) -> None:
        group = _mapping(group, f"group '{name}'")
        unknown = set(group) - {"vars", "hosts", "children"}
        if unknown:
            raise SequencerError(f"inventory '{path}': unknown keys {sorted(unknown)} in group '{name}'")
        group_vars = deep_merge(inherited, _mapping(group.get("vars"), f"vars of group '{name}'")) if depth else {}
        hosts = _mapping(group.get("hosts"), f"hosts of group '{name}'")
        is_stage = depth > 0 and "tsq_stage_type" in _mapping(group.get("vars"), name)
        if is_stage:
            if name in stage_defs:
                raise SequencerError(f"inventory '{path}': stage '{name}' defined twice")
            stage_defs[name] = {"vars": group_vars, "hosts": []}
        for host, hvars in hosts.items():
            host = str(host)
            host_vars[host] = deep_merge(host_vars.get(host, {}), _mapping(hvars, f"host '{host}'"))
            host_groups.setdefault(host, [])
            if depth:
                host_groups[host].append((name, group_vars))
            if is_stage:
                stage_defs[name]["hosts"].append(host)
        for child, sub in _mapping(group.get("children"), f"children of group '{name}'").items():
            visit(str(child), sub, group_vars, depth + 1)

    root_vars = _mapping(root.get("vars"), "all.vars")
    visit("all", root, {}, 0)

    components: dict[str, Component] = {}
    for host, hvars in host_vars.items():
        merged = dict(root_vars)
        for group, gvars in host_groups[host]:
            if group not in stage_defs:                       # plain groups: their variables apply
                merged = deep_merge(merged, gvars)
        merged = deep_merge(merged, hvars)
        stages = sorted({g for g, _ in host_groups[host] if g in stage_defs})
        component = Component(id=host, vars=merged, stages=stages)
        component._host_vars = set(hvars)
        if component.lock not in LOCK_MODES:
            raise SequencerError(f"inventory '{path}': component '{host}': tsq_lock '{component.lock}' "
                                 f"is not one of {', '.join(LOCK_MODES)}")
        components[host] = component

    stages = {name: Stage(id=name, type=str(d["vars"]["tsq_stage_type"]), components=d["hosts"], vars=d["vars"])
              for name, d in stage_defs.items()}
    for stage in stages.values():
        roles = [components[c].role for c in stage.components if components[c].role]
        duplicates = sorted({r for r in roles if roles.count(r) > 1})
        if duplicates:
            raise SequencerError(f"inventory '{path}': stage '{stage.id}' has several components with role(s) {duplicates}")
    return Inventory(file=path.resolve(), vars=root_vars, components=components, stages=stages)
