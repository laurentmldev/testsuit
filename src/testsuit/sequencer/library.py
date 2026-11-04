"""The lab library (README section 7): lab roles, lab components, and how a component's commands resolve.

- A **lab role** is abstract: the commands a component with that role offers (names and what they do),
  inheriting those of the role it ``extends``. Datapacks bind their logical components by role, and use
  only the commands of the role.
- A **lab component** is concrete: it implements commands (``run``, ``signal``, ``wait_for``...), its
  ``status`` probe, its ``selftest`` checks and its transport, inheriting those of the component it
  ``extends``. Reusable components are defined in the library files; every host of the inventory is a
  component too, which extends a library component or another host (``tsq_extends``).

Built-in roles and components: ``component`` (the abstract root, no command) and ``linux_system``
(deploy, collect and kill, built in). Library files hold ``roles:`` and/or ``components:``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from testsuit.sequencer import registry
from testsuit.sequencer.inventory import Component, Inventory, Stage
from testsuit.sequencer.util import SequencerError, load_yaml

ROLE_KEYS = {"extends", "description", "commands"}
COMPONENT_KEYS = {"extends", "description", "transport", "commands", "status", "selftest", "collect"}
HOST_KEYS = {"tsq_commands": "commands", "tsq_status": "status", "tsq_selftest": "selftest", "tsq_collect": "collect"}

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


@dataclass
class RoleDef:
    name: str
    extends: str = ""
    description: str = ""
    commands: dict[str, str] = field(default_factory=dict)     # name -> what it does
    source: str = "<builtin>"

    @classmethod
    def from_dict(cls, name: str, data: Any, source: str) -> "RoleDef":
        if not isinstance(data, dict):
            data = {} if data is None else data
        if not isinstance(data, dict):
            raise SequencerError(f"{source}: role '{name}' must be a mapping")
        unknown = set(data) - ROLE_KEYS
        if unknown:
            raise SequencerError(f"{source}: role '{name}': unknown keys {sorted(unknown)} (a role only lists "
                                 "commands: their implementation goes in the components)")
        commands = data.get("commands") or {}
        if isinstance(commands, list):
            commands = {str(c): "" for c in commands}
        if not isinstance(commands, dict) or any(isinstance(v, dict) for v in commands.values()):
            raise SequencerError(f"{source}: role '{name}': 'commands' lists names, each with what it does "
                                 "(`start: start the application`), not how it is done: implementations go in the lab components")
        return cls(name=name, extends=str(data.get("extends") or ""), description=str(data.get("description") or ""),
                   commands={str(k): str(v or "") for k, v in commands.items()}, source=source)


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
            if "python" in spec:
                from testsuit.sequencer.model import check_python
                check_python(spec["python"], f"{source}: component '{name}': command '{cmd}'")
        selftest = data.get("selftest") or []
        if not isinstance(selftest, list) or not all(isinstance(c, dict) and c.get("id") for c in selftest):
            raise SequencerError(f"{source}: component '{name}': 'selftest' must be a list of checks with an 'id'")
        return cls(name=name, extends=str(data.get("extends") or ""),
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
    commands: dict[str, dict]
    command_sources: dict[str, str]
    status: dict | None
    selftest: list[dict]
    collect: dict


class Library:

    def __init__(self, roles: dict[str, RoleDef], components: dict[str, ComponentDef], strict: bool = True):
        self.roles = roles
        self.components = components
        for name in roles if strict else []:
            self.role_chain(name)          # unknown parents and cycles
        for name in components if strict else []:
            self.component_chain(name)

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
                if not isinstance(data, dict) or not ({"roles", "components"} & set(data)) \
                        or set(data) - {"roles", "components"}:
                    raise SequencerError(f"'{path}': expected 'roles' and/or 'components' (a lab library file)")
                for kind, pool, builtins, make in (("roles", roles, BUILTIN_ROLES, RoleDef.from_dict),
                                                   ("components", components, BUILTIN_COMPONENTS, ComponentDef.from_dict)):
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
                commands[cmd] = {"description": text or commands.get(cmd, {}).get("description", ""), "from": role.name}
        return commands

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
            label = f"host {c.name}" if c.host else f"component {c.name}"
            for cmd, spec in c.commands.items():
                commands[cmd] = dict(spec)
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
                commands[cmd] = dict(spec)
                sources[cmd] = f"stage {stage.id}"
        for cmd, spec in own.commands.items():
            commands[cmd] = dict(spec)
            sources[cmd] = f"host {component.id}"
        if own.status is not None:
            status = dict(own.status)
        if own.collect:
            collect = {**collect, **own.collect}
        for check in own.selftest:
            selftest[:] = [x for x in selftest if x["id"] != check["id"]] + [dict(check)]
        transport = str(component.vars.get("tsq_transport") or transport or "ssh")
        return Resolved(role=component.role, chain=[c.name for c in chain], transport=transport, commands=commands,
                        command_sources=sources, status=status, selftest=selftest, collect=collect)

    def check_host(self, component: Component, stage: Stage | None, hosts: dict[str, ComponentDef]) -> tuple[Resolved, list[str], list[str]]:
        """A host against its role: (resolved, commands of the role it does not implement, commands it
        implements that its role does not list)."""
        resolved = self.resolve(component, stage, hosts)
        if not resolved.role:
            raise SequencerError(f"component '{component.id}' has no role (tsq_role)")
        offered = self.role_commands(resolved.role)
        missing = sorted(set(offered) - set(resolved.commands))
        extra = sorted(set(resolved.commands) - set(offered))
        return resolved, missing, extra
