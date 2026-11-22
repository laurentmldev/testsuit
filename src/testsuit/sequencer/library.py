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

"""The lab library (README section 7): lab roles, lab components, and how a component's commands resolve.

- A **lab role** is abstract: the commands a component with that role offers (names and what they do),
  inheriting those of the role it ``extends``. Datapacks bind their slots by role, and use
  only the commands of the role.
- A **lab component** is concrete: it implements commands (``run``, ``signal``, ``wait_for``...), its
  ``status`` probe, its ``selftest`` checks and its transport, inheriting those of the component it
  ``extends``. Reusable components are defined in the library files; every host of the inventory is a
  component too, which extends a library component or another host (``tsq_extends``).

Built-in roles and components: ``component`` (the abstract root, no command) and ``linux_system``
(deploy, collect and kill, built in). Library files hold ``roles:`` and/or ``component_classes:``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from testsuit.sequencer import registry
from testsuit.sequencer.cmdparams import PARAMS, declared_params, default_params, undeclared_reads
from testsuit.sequencer.inventory import Component, Inventory, Stage
from testsuit.sequencer.util import SequencerError, load_yaml

ROLE_KEYS = {"extends", "description", "commands", "inputs", "outputs"}
INPUT_KEYS = {"name", "type", "optional", "description"}
INPUT_TYPES = ("file", "folder")
COMPONENT_KEYS = {"extends", "roles", "description", "transport", "commands", "status", "selftest", "collect"}
HOST_KEYS = {"tsq_commands": "commands", "tsq_status": "status", "tsq_selftest": "selftest", "tsq_collect": "collect"}
COMMENTS = "comments"        # free notes of a command or a status probe: for the people, never run


def without_comments(spec: Any) -> Any:
    """A command or status probe definition without its ``comments``: what runs. The comments are notes for
    the people who maintain it (why it is done this way...): the sequencer never runs nor renders them."""
    if isinstance(spec, dict) and COMMENTS in spec:
        return {k: v for k, v in spec.items() if k != COMMENTS}
    return spec


def _check_comments(spec: dict, where: str) -> None:
    if spec.get(COMMENTS) is not None and not isinstance(spec[COMMENTS], str):
        raise SequencerError(f"{where}: 'comments' must be text")

BUILTIN_ROLES: dict[str, dict] = {
    "component": {
        "description": "Abstract root: anything the sequencer drives or only monitors (no command).",
        "commands": {},
    },
    "linux_system": {
        "extends": "component",
        "description": "A Linux computer reachable over SSH (or local).",
        "commands": {"deploy": "push the inputs to the component", "collect": "pull the outputs back",
                     "kill": "kill the processes the run started"},
    },
}

BUILTIN_COMPONENTS: dict[str, dict] = {
    "component": {
        "description": "Abstract root: no command; its self-test is its status probe.",
        "commands": {},
        "selftest": [{"id": "status", "builtin": "status"}],
    },
    "linux_system": {
        "extends": "component",
        "description": "A Linux computer reachable over SSH (or local): deploy, collect and kill built in.",
        "commands": {
            "deploy": {"builtin": "deploy"},
            "collect": {"builtin": "collect"},
            "kill": {"builtin": "kill"},
        },
        "status": {"builtin": "linux_system"},
        "selftest": [{"id": "login", "builtin": "login"},
                     {"id": "remote_root", "builtin": "remote_root"},
                     {"id": "disk", "builtin": "disk"},
                     {"id": "clock", "builtin": "clock"}],
    },
}

DEFAULT_EXTENDS = "linux_system"           # what a host extends when it gives no tsq_extends


def _role_inputs(value: Any, where: str) -> list[dict]:
    """The inputs a role expects (section 7.1): each a file or a folder of the component's input folder, by name
    (its path there, a folder ending with /), optional or not, with how to make it (Markdown)."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise SequencerError(f"{where}: 'inputs' is a list of {{name, type, optional, description}}")
    inputs, names = [], set()
    for i, item in enumerate(value, 1):
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            raise SequencerError(f"{where}: input {i} needs a 'name' (its path in the input folder)")
        unknown = set(item) - INPUT_KEYS
        if unknown:
            raise SequencerError(f"{where}: input '{item['name']}': unknown keys {sorted(unknown)}")
        kind = str(item.get("type") or ("folder" if str(item["name"]).endswith("/") else "file"))
        if kind not in INPUT_TYPES:
            raise SequencerError(f"{where}: input '{item['name']}': type '{kind}' is not file or folder")
        name = str(item["name"]).strip()
        name = name.rstrip("/") + "/" if kind == "folder" else name
        if name in names:
            raise SequencerError(f"{where}: input '{name}' listed twice")
        names.add(name)
        inputs.append({"name": name, "type": kind, "optional": bool(item.get("optional")),
                       "description": str(item.get("description") or "")})
    return inputs


def _role_outputs(value: Any, where: str) -> list[dict]:
    """The outputs a role foresees (SPEC section 4.2): each a file or a folder of the component's output folder, by
    name or glob pattern (a folder ending with /). Mandatory unless optional: a collect without it fails."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise SequencerError(f"{where}: 'outputs' is a list of {{name, type, optional, description}}")
    outputs, names = [], set()
    for i, item in enumerate(value, 1):
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            raise SequencerError(f"{where}: output {i} needs a 'name' (its path or pattern in the output folder)")
        unknown = set(item) - INPUT_KEYS
        if unknown:
            raise SequencerError(f"{where}: output '{item['name']}': unknown keys {sorted(unknown)}")
        kind = str(item.get("type") or ("folder" if str(item["name"]).endswith("/") else "file"))
        if kind not in INPUT_TYPES:
            raise SequencerError(f"{where}: output '{item['name']}': type '{kind}' is not file or folder")
        name = str(item["name"]).strip().lstrip("/")
        if ".." in name.split("/"):
            raise SequencerError(f"{where}: output '{name}' must stay in the output folder")
        name = name.rstrip("/") + "/" if kind == "folder" else name
        if name in names:
            raise SequencerError(f"{where}: output '{name}' listed twice")
        names.add(name)
        outputs.append({"name": name, "type": kind, "optional": bool(item.get("optional")),
                        "description": str(item.get("description") or "")})
    return outputs


def missing_outputs(outputs: list[dict], folder: Path) -> list[str]:
    """The mandatory outputs that a collected output folder does not hold (a pattern: no match of its type)."""
    missing = []
    for item in outputs:
        if item.get("optional"):
            continue
        pattern = item["name"].rstrip("/")
        found = [p for p in folder.glob(pattern)] if folder.is_dir() else []
        if not any(p.is_dir() if item["type"] == "folder" else p.is_file() for p in found):
            missing.append(item["name"])
    return missing


def _override(base: dict | None, spec: dict) -> dict:
    """A command implementation over the one it overrides: the new one runs, the default values of the
    parameters are merged (the new ones win), so an override only gives those it changes."""
    result = dict(spec)
    inherited = (base or {}).get(PARAMS)
    if isinstance(inherited, dict) and inherited:
        result[PARAMS] = {**inherited, **(spec.get(PARAMS) or {})}
    return result


@dataclass
class RoleDef:
    name: str
    extends: str = ""
    description: str = ""
    commands: dict[str, str] = field(default_factory=dict)     # name -> what it does
    # name -> its parameters (name -> what each is for), set by the steps (with:), defaults in the classes
    params: dict[str, dict[str, str]] = field(default_factory=dict)
    inputs: list[dict] = field(default_factory=list)           # what a component of the role expects as inputs
    outputs: list[dict] = field(default_factory=list)          # what it is foreseen to produce (collected)
    source: str = "<builtin>"

    @classmethod
    def from_dict(cls, name: str, data: Any, source: str) -> "RoleDef":
        if not isinstance(data, dict):
            data = {} if data is None else data
        if not isinstance(data, dict):
            raise SequencerError(f"{source}: role '{name}' must be a mapping")
        unknown = set(data) - ROLE_KEYS
        if unknown:
            raise SequencerError(f"{source}: role '{name}': unknown keys {sorted(unknown)} (a role lists commands "
                                 "and the inputs it expects: their implementation goes in the components)")
        commands = data.get("commands") or {}
        if isinstance(commands, list):
            commands = {str(c): "" for c in commands}
        if not isinstance(commands, dict):
            raise SequencerError(f"{source}: role '{name}': 'commands' lists names, each with what it does")
        texts: dict[str, str] = {}
        params: dict[str, dict[str, str]] = {}
        for cmd, value in commands.items():
            where = f"{source}: role '{name}': command '{cmd}'"
            if isinstance(value, dict):        # {description, params}: what it does, and the parameters it takes
                unknown = set(value) - {"description", PARAMS}
                if unknown:
                    raise SequencerError(f"{where}: unknown keys {sorted(unknown)} (a role gives what a command does "
                                         "and its parameters, not how it is done: implementations go in the lab components)")
                texts[str(cmd)] = str(value.get("description") or "")
                declared = declared_params(value.get(PARAMS), where)
                if declared:
                    params[str(cmd)] = declared
            else:
                texts[str(cmd)] = str(value or "")
        return cls(name=name, extends=str(data.get("extends") or ""), description=str(data.get("description") or ""),
                   commands=texts, params=params,
                   inputs=_role_inputs(data.get("inputs"), f"{source}: role '{name}'"),
                   outputs=_role_outputs(data.get("outputs"), f"{source}: role '{name}'"), source=source)


@dataclass
class ComponentDef:
    name: str
    extends: str = ""
    description: str = ""
    transport: str | None = None
    commands: dict[str, Any] = field(default_factory=dict)
    status: dict | None = None
    selftest: list[dict] = field(default_factory=list)
    collect: dict | None = None
    source: str = "<builtin>"
    host: bool = False                       # a host of the inventory (not a library component)
    roles: list[str] | None = None           # the roles it supports (None: those of the component it extends)

    @classmethod
    def from_dict(cls, name: str, data: Any, source: str, host: bool = False) -> "ComponentDef":
        data = {} if data is None else data
        if not isinstance(data, dict):
            raise SequencerError(f"{source}: component '{name}' must be a mapping")
        unknown = set(data) - COMPONENT_KEYS
        if unknown:
            raise SequencerError(f"{source}: component '{name}': unknown keys {sorted(unknown)}")
        commands = data.get("commands") or {}
        if not isinstance(commands, dict):
            raise SequencerError(f"{source}: component '{name}': 'commands' must be a mapping")
        for cmd, spec in commands.items():
            if not isinstance(spec, dict):
                raise SequencerError(f"{source}: component '{name}': command '{cmd}' must be a mapping")
            _check_comments(spec, f"{source}: component '{name}': command '{cmd}'")
            default_params(spec, f"{source}: component '{name}': command '{cmd}'")
            if "python" in spec:
                from testsuit.sequencer.model import check_python
                check_python(spec["python"], f"{source}: component '{name}': command '{cmd}'")
        if isinstance(data.get("status"), dict):
            _check_comments(data["status"], f"{source}: component '{name}': status")
        selftest = data.get("selftest") or []
        if not isinstance(selftest, list) or not all(isinstance(c, dict) and c.get("id") for c in selftest):
            raise SequencerError(f"{source}: component '{name}': 'selftest' must be a list of checks with an 'id'")
        roles = data.get("roles")
        if isinstance(roles, str):
            roles = [roles]
        if roles is not None and (not isinstance(roles, list) or not all(isinstance(r, str) and r for r in roles)):
            raise SequencerError(f"{source}: component '{name}': 'roles' must be a list of role names")
        return cls(name=name, extends=str(data.get("extends") or ""), roles=list(roles) if roles is not None else None,
                   description=str(data.get("description") or ""), transport=data.get("transport"),
                   commands=dict(commands), status=data.get("status"), selftest=list(selftest),
                   collect=data.get("collect"), source=source, host=host)

    @classmethod
    def from_host(cls, comp: Component, source: str) -> "ComponentDef":
        """A host as a component: its own tsq_ keys (the variables of its groups are not inherited by others)."""
        data = {key: comp.vars[var] for var, key in HOST_KEYS.items() if var in comp._host_vars}
        data["extends"] = comp.extends
        return cls.from_dict(comp.id, data, source, host=True)


@dataclass
class Resolved:
    """What a bound component does: its role, commands after every override (section 7.4), status, self-test."""
    role: str
    chain: list[str]                         # the components it inherits from, base first, itself last
    transport: str
    commands: dict[str, dict]                # with their default parameter values merged along the chain (params)
    command_sources: dict[str, str]
    status: dict | None
    selftest: list[dict]
    collect: dict
    # the parameters its role declares for each command (name -> what each is for)
    command_params: dict[str, dict[str, str]] = field(default_factory=dict)


class Library:

    def __init__(self, roles: dict[str, RoleDef], components: dict[str, ComponentDef], strict: bool = True):
        self.roles = roles
        self.components = components
        for name in roles if strict else []:
            self.role_chain(name)          # unknown parents and cycles
        for name in components if strict else []:
            self.component_chain(name)
            for role, missing in self.check_component(name).items():
                if missing:
                    raise SequencerError(f"class '{name}' ({components[name].source}) does not implement "
                                         f"{', '.join(missing)}, listed by its role '{role}'")

    @classmethod
    def load(cls, folders: list[Path], strict: bool = True) -> "Library":
        roles = {name: RoleDef.from_dict(name, data, "<builtin>") for name, data in BUILTIN_ROLES.items()}
        components = {name: ComponentDef.from_dict(name, data, "<builtin>") for name, data in BUILTIN_COMPONENTS.items()}
        for name, data in registry.roles.items():
            roles[name] = RoleDef.from_dict(name, data, "<plugin>")
        for name, data in registry.components.items():
            components[name] = ComponentDef.from_dict(name, data, "<plugin>")
        seen: dict[tuple[str, str], str] = {}
        for folder in folders:
            if not folder.is_dir():
                raise SequencerError(f"lab library folder not found: '{folder}'")
            for path in sorted(list(folder.rglob("*.yml")) + list(folder.rglob("*.yaml"))):
                data = load_yaml(path) or {}
                for former in ("components", "models"):
                    if isinstance(data, dict) and former in data:
                        raise SequencerError(f"'{path}': '{former}' is now 'component_classes': rename the key")
                if not isinstance(data, dict) or not ({"roles", "component_classes"} & set(data)) \
                        or set(data) - {"roles", "component_classes"}:
                    raise SequencerError(f"'{path}': expected 'roles' and/or 'component_classes' (a lab library file)")
                for kind, pool, builtins, make in (("roles", roles, BUILTIN_ROLES, RoleDef.from_dict),
                                                   ("component_classes", components, BUILTIN_COMPONENTS, ComponentDef.from_dict)):
                    entries = data.get(kind) or {}
                    if not isinstance(entries, dict):
                        raise SequencerError(f"'{path}': '{kind}' must be a mapping, by name")
                    for name, definition in entries.items():
                        what = kind[:-1]
                        if (kind, name) in seen:
                            raise SequencerError(f"{what} '{name}' defined twice: '{seen[(kind, name)]}' and '{path}'")
                        if name in builtins:
                            raise SequencerError(f"'{path}': '{name}' is a built-in {what} and cannot be redefined")
                        seen[(kind, name)] = str(path)
                        pool[name] = make(name, definition, str(path))
        return cls(roles, components, strict)

    # ── roles ──

    def role_chain(self, name: str) -> list[RoleDef]:
        """The role and the roles it extends, base first."""
        result: list[RoleDef] = []
        current = name
        while current:
            if current not in self.roles:
                raise SequencerError(f"unknown role '{current}'" + (f" (extended by '{result[0].name}')" if result else ""))
            if any(r.name == current for r in result):
                raise SequencerError(f"role '{name}': 'extends' cycle through '{current}'")
            result.insert(0, self.roles[current])
            current = self.roles[current].extends
        return result

    def role_commands(self, name: str) -> dict[str, dict]:
        """The commands a role offers, with what each does and the role that lists it."""
        commands: dict[str, dict] = {}
        for role in self.role_chain(name):
            for cmd, text in role.commands.items():
                commands[cmd] = {"description": text or commands.get(cmd, {}).get("description", ""), "from": role.name,
                                 "params": {**commands.get(cmd, {}).get("params", {}), **role.params.get(cmd, {})}}
        return commands

    def command_params(self, role: str) -> dict[str, dict[str, str]]:
        """The parameters of each command a role offers (name -> what each is for), its parents' first."""
        if role not in self.roles:
            return {}
        return {cmd: info["params"] for cmd, info in self.role_commands(role).items()}

    def roles_params(self, roles: list[str]) -> dict[str, dict[str, str]]:
        """The parameters of each command the given roles offer (a class supporting several: any of them)."""
        out: dict[str, dict[str, str]] = {}
        for role in roles:
            for cmd, params in self.command_params(role).items():
                out[cmd] = {**out.get(cmd, {}), **params}
        return out

    def param_problems(self, commands: dict[str, dict], params: dict[str, dict[str, str]], where: str) -> list[str]:
        """Commands of a role that read, or give a default to, a parameter the role does not declare."""
        problems = []
        for cmd, spec in commands.items():
            if cmd not in params:
                continue                                  # not a command of its roles: its own (extra_commands)
            bad = undeclared_reads(spec, params[cmd])
            if bad:
                declared = ", ".join(params[cmd]) or "none"
                problems.append(f"{where}: command '{cmd}' reads or sets parameter(s) {', '.join(bad)} that its role "
                                f"does not declare (it declares: {declared}); a command reads its parameters as params.*, "
                                "declared by the role")
        return problems

    def role_outputs(self, name: str) -> list[dict]:
        """The outputs a role foresees, its parents' first (a role redefines one by name), with the role listing it."""
        outputs: dict[str, dict] = {}
        for role in self.role_chain(name):
            for item in role.outputs:
                outputs[item["name"]] = {**item, "from": role.name}
        return list(outputs.values())

    def role_inputs(self, name: str) -> list[dict]:
        """The inputs a role expects, its parents' first (a role redefines one by name), with the role listing it."""
        inputs: dict[str, dict] = {}
        for role in self.role_chain(name):
            for item in role.inputs:
                inputs[item["name"]] = {**item, "from": role.name}
        return list(inputs.values())

    # ── components ──

    def component_chain(self, name: str, hosts: dict[str, ComponentDef] | None = None) -> list[ComponentDef]:
        """The component and those it extends, base first. With hosts (the inventory's, by id), name is a host;
        a host extends another host or a library component (hosts first), a library component only library ones."""
        hosts = hosts or {}
        current: ComponentDef | None = hosts.get(name) if hosts else self.components.get(name)
        if current is None:
            raise SequencerError(f"unknown component '{name}'")
        result: list[ComponentDef] = []
        while current is not None:
            if any(c is current for c in result):
                raise SequencerError(f"component '{name}': 'extends' cycle through '{current.name}'")
            result.insert(0, current)
            parent = current.extends
            if not parent:
                break
            nxt = hosts.get(parent) if current.host and parent != current.name else None
            nxt = nxt or self.components.get(parent)
            if nxt is None:
                raise SequencerError(f"unknown component '{parent}' (extended by '{current.name}')")
            current = nxt
        return result

    def component_roles(self, name: str, hosts: dict[str, ComponentDef] | None = None) -> list[str]:
        """The roles a component supports: those it lists, else those of the nearest component it extends
        that lists some (none: a base, or a host that must give its role with tsq_role)."""
        for c in reversed(self.component_chain(name, hosts)):
            if c.roles is not None:
                return list(c.roles)
        return []

    def check_component(self, name: str) -> dict[str, list[str]]:
        """A library component against the roles it supports: the commands of each it does not implement."""
        chain = self.component_chain(name)
        implemented = {cmd for c in chain for cmd in c.commands}
        result = {}
        for role in self.component_roles(name):
            if role not in self.roles:
                raise SequencerError(f"component '{name}' ({chain[-1].source}): unknown role '{role}'")
            result[role] = sorted(set(self.role_commands(role)) - implemented)
        params = self.roles_params(self.component_roles(name))
        for c in chain:
            problems = self.param_problems(c.commands, params, f"class '{c.name}' ({c.source})")
            if problems:
                raise SequencerError("; ".join(problems))
        return result

    def host_role(self, component: Component, hosts: dict[str, ComponentDef] | None = None) -> str:
        """The role of a host: its tsq_role, which must be one its component supports when it lists some;
        without tsq_role, the one role its component supports."""
        hosts = dict(hosts or {})
        hosts.setdefault(component.id, ComponentDef.from_host(component, "<inventory>"))
        roles = self.component_roles(component.id, hosts)
        given = str(component.vars.get("tsq_role") or "")
        if given:
            if roles and given not in roles:
                raise SequencerError(f"role '{given}' (tsq_role) is not one its class '{component.extends}' "
                                     f"supports: {', '.join(roles)}")
            return given
        if len(roles) == 1:
            return roles[0]
        if roles:
            raise SequencerError(f"its class '{component.extends}' supports several roles ({', '.join(roles)}): "
                                 "pick one with tsq_role")
        raise SequencerError(f"no role: give tsq_role, or extend a component that lists its roles "
                             f"('{component.extends}' lists none)")

    def host_defs(self, inventory: Inventory) -> dict[str, ComponentDef]:
        return {c.id: ComponentDef.from_host(c, str(inventory.file)) for c in inventory.components.values()}

    def resolve(self, component: Component, stage: Stage | None = None,
                hosts: dict[str, ComponentDef] | None = None) -> Resolved:
        """Resolve a host of the inventory: the components it extends (base first), then itself, the stage
        ``tsq_commands`` and its own ``tsq_commands``, last wins. Inline ``run:`` in a step comes on top, in
        the engine.

        :param hosts: the inventory's hosts as components (``host_defs``), for a host extending another host
        """
        hosts = dict(hosts or {})
        own = ComponentDef.from_host(component, "<inventory>")
        hosts[component.id] = own
        chain = self.component_chain(component.id, hosts)
        commands: dict[str, dict] = {}
        sources: dict[str, str] = {}
        status = None
        selftest: list[dict] = []
        collect: dict = {}
        transport = None
        for c in chain:
            if c is own:
                continue                                      # its own commands come after the stage's
            label = f"host {c.name}" if c.host else f"class {c.name}"
            for cmd, spec in c.commands.items():
                commands[cmd] = _override(commands.get(cmd), spec)
                sources[cmd] = label
            if c.status is not None:
                status = dict(c.status)
            if c.transport:
                transport = c.transport
            if c.collect:
                collect = {**collect, **c.collect}
            for check in c.selftest:
                selftest[:] = [x for x in selftest if x["id"] != check["id"]] + [dict(check)]
        if stage is not None:
            for cmd, spec in stage.commands.items():
                commands[cmd] = _override(commands.get(cmd), spec)
                sources[cmd] = f"stage {stage.id}"
        for cmd, spec in own.commands.items():
            commands[cmd] = _override(commands.get(cmd), spec)
            sources[cmd] = f"host {component.id}"
        if own.status is not None:
            status = dict(own.status)
        if own.collect:
            collect = {**collect, **own.collect}
        for check in own.selftest:
            selftest[:] = [x for x in selftest if x["id"] != check["id"]] + [dict(check)]
        transport = str(component.settings.get("tsq_transport") or transport or "ssh")
        return Resolved(role=component.role, chain=[c.name for c in chain], transport=transport, commands=commands,
                        command_sources=sources, status=status, selftest=selftest, collect=collect,
                        command_params=self.command_params(component.role))

    def check_host(self, component: Component, stage: Stage | None, hosts: dict[str, ComponentDef]) -> tuple[Resolved, list[str], list[str]]:
        """A host against its role: (resolved, commands of the role it does not implement, commands it
        implements that no role of it lists, ``extra_commands``)."""
        resolved = self.resolve(component, stage, hosts)
        if not resolved.role:
            raise SequencerError(f"component '{component.id}' has no role (tsq_role)")
        from testsuit.sequencer.httpcall import normalize
        for name, spec in resolved.commands.items():
            if isinstance(spec, dict) and "http" in spec:
                normalize(spec["http"], f"command '{name}' (from {resolved.command_sources.get(name, '?')})")
        offered = self.role_commands(resolved.role)
        missing = sorted(set(offered) - set(resolved.commands))
        problems = self.param_problems(resolved.commands, resolved.command_params, f"component '{component.id}'")
        if problems:
            raise SequencerError("; ".join(problems))
        return resolved, missing, self.extra_commands(component, resolved, hosts)

    def extra_commands(self, component: Component, resolved: Resolved,
                       hosts: dict[str, ComponentDef] | None = None) -> list[str]:
        """The commands a host implements that no role of it lists: neither its role, nor any other role its
        component supports (a component with several roles implements the commands of each, whichever its
        hosts pick)."""
        hosts = dict(hosts or {})
        hosts.setdefault(component.id, ComponentDef.from_host(component, "<inventory>"))
        try:
            roles = self.component_roles(component.id, hosts)
        except SequencerError:
            roles = []
        listed = set()
        for role in dict.fromkeys([resolved.role, *roles]):
            if role in self.roles:
                listed |= set(self.role_commands(role))
        return sorted(set(resolved.commands) - listed)
