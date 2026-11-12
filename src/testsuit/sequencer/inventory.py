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

"""Lab inventory loader (README section 6): the Ansible YAML inventory format, read without Ansible.

- a group with ``tsq_stage_type`` is a **stage**, its hosts being the stage's components;
- a host in several stage groups is a shared component;
- a host directly under ``all`` (in no stage) is an **ad hoc component**;
- variables follow Ansible precedence: host over group (child over parent) over ``all``;
- a host is a lab component: ``tsq_extends`` is the library component (or the other host) it inherits its
  implementation from, ``tsq_role`` its role (the commands datapacks can use) when that component supports
  several roles or none (section 7); the lab resolves it;
- ``all.vars.tsq_hosts`` is the lab's host names: a table ``name: address`` a host's ``ansible_host`` can use. The
  sequencer translates the name itself wherever it uses the address (nothing is written to ``/etc/hosts``).
- a variable can hold nested keys (``custom_grp: {keyA: v}``), also written as a dotted name (``custom_grp.keyA: v``);
  groups, stages and the host merge them key by key, and templates read them as ``component.vars.custom_grp.keyA``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from testsuit.sequencer.util import SequencerError, deep_merge, load_yaml
from testsuit.sequencer.transports.ssh import pinned_host_keys

LOCK_MODES = ("exclusive", "shared", "none")

# The pictures the Lab view has for a component (``tsq_icon``); any other value is an image file of the lab folder.
ICONS = ("server", "workstation", "switch", "router", "tap", "embedded", "board", "sensor", "instrument", "power",
         "simulator", "storage", "network", "camera", "generic")
ICON_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")


@dataclass
class Component:
    id: str
    vars: dict[str, Any]                         # all < groups < host (stage variables not included)
    stages: list[str] = field(default_factory=list)

    @property
    def settings(self) -> dict[str, Any]:
        """Its variables whatever the stage, as its transport and the Lab view use them (``ansible_host``,
        ``ansible_user``...): all < its stage groups (the values they agree on) < host."""
        return deep_merge(deep_merge(self.vars, self._stage_vars), self._host_tree)

    @property
    def host(self) -> str:
        """Its address: ``ansible_host``, on the host, its stage group or above; else its name. A name of the lab's
        ``tsq_hosts`` table is translated to the address it stands for."""
        return self._hosts.get(self._address, self._address)

    @property
    def host_alias(self) -> str:
        """The name of the lab's ``tsq_hosts`` table its address is given by ('' when it gives the address
        itself)."""
        return self._address if self._address in self._hosts else ""

    @property
    def _address(self) -> str:
        return str(self.settings.get("ansible_host", self.id))

    @property
    def role(self) -> str:
        """The role it plays (a lab role of the library): the commands datapacks can use on it. Its tsq_role,
        or the role of its component, once the lab has resolved it (``Library.host_role``)."""
        return self._role or str(self.vars.get("tsq_role", ""))

    @property
    def extends(self) -> str:
        """The component it inherits its implementation from: a library component or another host."""
        return str(self.vars.get("tsq_extends") or "linux_system")

    @property
    def lock(self) -> str:
        return str(self.vars.get("tsq_lock", "exclusive"))

    @property
    def label(self) -> str:
        return str(self.vars.get("tsq_label", self.id))

    @property
    def description(self) -> str:
        """Its own ``tsq_description`` (the lab's and its stages' are not inherited)."""
        return str(self.vars.get("tsq_description") or "")

    @property
    def icon(self) -> str:
        """``tsq_icon``: how the Lab view draws it, one of ``ICONS`` or an image file relative to the inventory
        (empty: a plain box)."""
        return str(self.vars.get("tsq_icon") or "")

    @property
    def links(self) -> list[dict]:
        """``tsq_links``: the components it is wired to that its commands do not show (a tap between a switch and a
        board), each ``{"to": <host>, "label": <text>}``."""
        return _links(self.vars.get("tsq_links"))

    @property
    def adhoc(self) -> bool:
        return not self.stages

    @property
    def tags(self) -> list[str]:
        """Its id and its ``tsq_tags``: what a slot's ``tags`` select it by."""
        return [self.id, *_tags(self.vars.get("tsq_tags"))]

    def vars_in(self, stage: "Stage | None") -> dict[str, Any]:
        """Variables of the component as seen in a stage: all < stage group < host, nested keys merged key by key."""
        if stage is None:
            return dict(self.vars)
        return deep_merge(deep_merge(self.vars, _own(stage.vars)), self._host_tree)

    _host_vars: set = field(default_factory=set, repr=False)      # the names of the variables the host itself gives
    _host_tree: dict = field(default_factory=dict, repr=False)    # the variables the host itself gives
    _stage_vars: dict = field(default_factory=dict, repr=False)   # the variables of its stage groups that agree
    _role: str = field(default="", repr=False)
    _hosts: dict = field(default_factory=dict, repr=False)        # the lab's tsq_hosts: name -> address


@dataclass
class Stage:
    id: str
    type: str
    components: list[str]
    vars: dict[str, Any]                         # group variables (tsq_commands included)
    description: str = ""                        # its own tsq_description (not inherited by its components)

    @property
    def commands(self) -> dict[str, Any]:
        return dict(self.vars.get("tsq_commands") or {})

    @property
    def tags(self) -> list[str]:
        """Its id and its ``tsq_tags``: what a stage slot's ``tags`` select it by."""
        return [self.id, *_tags(self.vars.get("tsq_tags"))]


def _links(value: Any) -> list[dict]:
    """``tsq_links`` as a list of ``{to, label}``: a host, a list of hosts or of ``{to, label}``, or a mapping
    host -> label."""
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    if isinstance(value, dict):
        value = [{"to": k, "label": v} for k, v in value.items()]
    if not isinstance(value, list):
        raise SequencerError("tsq_links must be a host, a list of hosts or of {to, label}, or a mapping host: label")
    links = []
    for item in value:
        if isinstance(item, dict):
            if not item.get("to") or set(item) - {"to", "label"}:
                raise SequencerError(f"tsq_links: {item} must have 'to' (a host) and an optional 'label'")
            links.append({"to": str(item["to"]), "label": str(item.get("label") or "")})
        elif isinstance(item, (str, int)) and str(item):
            links.append({"to": str(item), "label": ""})
        else:
            raise SequencerError(f"tsq_links: {item!r} is not a host")
    return links


def _tags(value: Any) -> list[str]:
    if not value:
        return []
    return [str(value)] if isinstance(value, str) else [str(v) for v in value]


@dataclass
class Inventory:
    file: Path
    vars: dict[str, Any]                         # all.vars
    components: dict[str, Component]
    stages: dict[str, Stage]

    @property
    def description(self) -> str:
        """``all.vars.tsq_description``: what this lab is."""
        return str(self.vars.get("tsq_description") or "")

    @property
    def hosts(self) -> dict[str, str]:
        """``all.vars.tsq_hosts``: the lab's host names, each with the address it stands for."""
        return host_names(self.vars.get("tsq_hosts"))

    @property
    def library_folders(self) -> list[Path]:
        if "tsq_types" in self.vars:
            raise SequencerError(f"inventory '{self.file}': 'tsq_types' is gone: 'tsq_library' gives the folders of "
                                 "the lab library (roles and components)")
        folders = self.vars.get("tsq_library") or []
        if isinstance(folders, str):
            folders = [folders]
        return [(self.file.parent / Path(f).expanduser()).resolve() for f in folders]

    @property
    def adhoc(self) -> list[Component]:
        return [c for c in self.components.values() if c.adhoc]

    def stages_of_type(self, stage_type: str) -> list[Stage]:
        return [s for s in self.stages.values() if s.type == stage_type]


def _mapping(value: Any, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SequencerError(f"inventory: {where} must be a mapping")
    return value


def nested_vars(variables: dict, where: str) -> dict:
    """Variables with their dotted names read as nested keys: ``custom_grp.keyA: v`` is ``custom_grp: {keyA: v}``,
    in the variable's own sub-keys too. The ``tsq_`` variables keep their content as given (a ``tsq_hosts`` or
    ``tsq_links`` name may hold dots) and cannot be dotted themselves."""
    result: dict = {}
    for key, value in variables.items():
        parts = str(key).split(".")
        if any(not p for p in parts):
            raise SequencerError(f"inventory: {where}: '{key}' is not a variable name (an empty part between dots)")
        if parts[0].startswith("tsq_"):
            if len(parts) > 1:
                raise SequencerError(f"inventory: {where}: '{key}': a tsq_ variable is given whole, not by a dotted name")
        elif isinstance(value, dict):
            value = nested_vars(value, f"{where}: {parts[0]}")
        node = result
        for i, part in enumerate(parts[:-1]):
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise SequencerError(f"inventory: {where}: '{key}': '{'.'.join(parts[:i + 1])}' is a value, "
                                     "it cannot hold keys")
        last = parts[-1]
        if last in node and (isinstance(node[last], dict) != isinstance(value, dict)):
            raise SequencerError(f"inventory: {where}: '{key}' is given both as a value and as keys")
        node[last] = deep_merge(node[last], value) if isinstance(value, dict) and last in node else value
    return result


def _common(trees: list[dict]) -> dict:
    """The variables all the trees give the same value, nested keys compared one by one."""
    first, *others = trees
    result: dict = {}
    for key, value in first.items():
        if isinstance(value, dict) and value and all(isinstance(o.get(key), dict) for o in others):
            common = _common([value, *[o[key] for o in others]])
            if common:
                result[key] = common
        elif all(o.get(key, object()) == value for o in others):
            result[key] = value
    return result


def _own(variables: dict) -> dict:
    """Variables as inherited by a sub-group or a host: without the ``tsq_description`` of the level above,
    each level (the lab, a stage, a component) having its own."""
    return {k: v for k, v in variables.items() if k != "tsq_description"}


def host_names(value: Any) -> dict[str, str]:
    """``tsq_hosts`` as ``{name: address}``: a mapping of names to the address (IP or DNS name) each stands for."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SequencerError("tsq_hosts must be a mapping name: address")
    table: dict[str, str] = {}
    for name, address in value.items():
        if not isinstance(name, str) or not name.strip() or any(c.isspace() for c in name):
            raise SequencerError(f"tsq_hosts: {name!r} is not a name (a text without spaces)")
        if not isinstance(address, str) or not address.strip() or any(c.isspace() for c in address.strip()):
            raise SequencerError(f"tsq_hosts: '{name}' must have an address (an IP address or a DNS name)")
        table[name] = address.strip()
    for name, address in table.items():
        if address in table and address != name:
            raise SequencerError(f"tsq_hosts: '{name}' gives '{address}', another name of the table: give its address")
    return table


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
        own_vars = nested_vars(_mapping(group.get("vars"), f"vars of group '{name}'"), f"vars of group '{name}'")
        group_vars = deep_merge(_own(inherited), own_vars) if depth else {}
        hosts = _mapping(group.get("hosts"), f"hosts of group '{name}'")
        is_stage = depth > 0 and "tsq_stage_type" in _mapping(group.get("vars"), name)
        if depth and "tsq_hosts" in _mapping(group.get("vars"), name):
            raise SequencerError(f"inventory '{path}': group '{name}': tsq_hosts is the lab's table: it goes in all.vars")
        if is_stage:
            if name in stage_defs:
                raise SequencerError(f"inventory '{path}': stage '{name}' defined twice")
            own = _mapping(group.get("vars"), name).get("tsq_description")
            stage_defs[name] = {"vars": group_vars, "hosts": [], "description": str(own or "")}
        for host, hvars in hosts.items():
            host = str(host)
            host_vars[host] = deep_merge(host_vars.get(host, {}), nested_vars(_mapping(hvars, f"host '{host}'"), f"host '{host}'"))
            host_groups.setdefault(host, [])
            if depth:
                host_groups[host].append((name, group_vars))
            if is_stage:
                stage_defs[name]["hosts"].append(host)
        for child, sub in _mapping(group.get("children"), f"children of group '{name}'").items():
            visit(str(child), sub, group_vars, depth + 1)

    root_vars = nested_vars(_mapping(root.get("vars"), "all.vars"), "all.vars")
    try:
        names = host_names(root_vars.get("tsq_hosts"))
    except SequencerError as e:
        raise SequencerError(f"inventory '{path}': {e}") from None
    visit("all", root, {}, 0)

    components: dict[str, Component] = {}
    for host, hvars in host_vars.items():
        if "tsq_hosts" in hvars:
            raise SequencerError(f"inventory '{path}': component '{host}': tsq_hosts is the lab's table: it goes in "
                                 "all.vars")
        merged = {k: v for k, v in _own(root_vars).items() if k != "tsq_hosts"}
        for group, gvars in host_groups[host]:
            if group not in stage_defs:                       # plain groups: their variables apply
                merged = deep_merge(merged, _own(gvars))
        merged = deep_merge(merged, hvars)
        stages = sorted({g for g, _ in host_groups[host] if g in stage_defs})
        component = Component(id=host, vars=merged, stages=stages)
        component._host_vars = set(hvars)
        component._host_tree = hvars
        component._hosts = names
        if stages:                     # a shared component: only the values all its stage groups give
            component._stage_vars = _common([_own(stage_defs[s]["vars"]) for s in stages])
        if "tsq_type" in merged:
            raise SequencerError(f"inventory '{path}': component '{host}': 'tsq_type' is gone: give 'tsq_role' (a lab "
                                 f"role: its commands) and 'tsq_extends' (the component implementing them, "
                                 f"'{merged['tsq_type']}' moved to the library's components)")
        if component.lock not in LOCK_MODES:
            raise SequencerError(f"inventory '{path}': component '{host}': tsq_lock '{component.lock}' "
                                 f"is not one of {', '.join(LOCK_MODES)}")
        try:
            pinned_host_keys(merged.get("tsq_ssh_host_key"))
            component.links
        except SequencerError as e:
            raise SequencerError(f"inventory '{path}': component '{host}': {e}") from None
        if not isinstance(merged.get("tsq_icon") or "", str):
            raise SequencerError(f"inventory '{path}': component '{host}': tsq_icon must be one of {', '.join(ICONS)} "
                                 "or an image file")
        components[host] = component

    stages = {name: Stage(id=name, type=str(d["vars"]["tsq_stage_type"]), components=d["hosts"], vars=d["vars"],
                          description=d["description"])
              for name, d in stage_defs.items()}
    for component in components.values():
        for link in component.links:
            if link["to"] not in components or link["to"] == component.id:
                raise SequencerError(f"inventory '{path}': component '{component.id}': tsq_links: '{link['to']}' is "
                                     "not another host of the inventory")
    return Inventory(file=path.resolve(), vars=root_vars, components=components, stages=stages)
