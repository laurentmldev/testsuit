"""Component types (README section 7): the type library, ``extends`` chains and command resolution.

A component's role (``tsq_role``) is a type of the library. A type gives the components of that role their commands, its ``status`` probe, its ``selftest`` checks and
its transport. The built-in base types are ``component`` (abstract root) and ``linux_system``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from testsuit.sequencer import registry
from testsuit.sequencer.inventory import Component, Stage
from testsuit.sequencer.util import SequencerError, load_yaml

TYPE_KEYS = {"extends", "transport", "commands", "status", "selftest", "collect", "description"}

BUILTIN_TYPES: dict[str, dict] = {
    "component": {
        "description": "Abstract root: anything the sequencer drives or only monitors (no built-in command).",
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


@dataclass
class TypeDef:
    name: str
    extends: str = ""
    transport: str | None = None
    commands: dict[str, Any] = field(default_factory=dict)
    status: dict | None = None
    selftest: list[dict] = field(default_factory=list)
    collect: dict | None = None
    source: str = "<builtin>"
    description: str = ""

    @classmethod
    def from_dict(cls, name: str, data: dict, source: str) -> "TypeDef":
        if not isinstance(data, dict):
            raise SequencerError(f"{source}: component type '{name}' must be a mapping")
        unknown = set(data) - TYPE_KEYS
        if unknown:
            raise SequencerError(f"{source}: component type '{name}': unknown keys {sorted(unknown)}")
        commands = data.get("commands") or {}
        if not isinstance(commands, dict):
            raise SequencerError(f"{source}: component type '{name}': 'commands' must be a mapping")
        for cmd, spec in commands.items():
            if not isinstance(spec, dict):
                raise SequencerError(f"{source}: component type '{name}': command '{cmd}' must be a mapping")
            if "python" in spec:
                from testsuit.sequencer.model import check_python
                check_python(spec["python"], f"{source}: component type '{name}': command '{cmd}'")
        selftest = data.get("selftest") or []
        if not isinstance(selftest, list) or not all(isinstance(c, dict) and c.get("id") for c in selftest):
            raise SequencerError(f"{source}: component type '{name}': 'selftest' must be a list of checks with an 'id'")
        return cls(name=name, extends=str(data.get("extends") or ""), transport=data.get("transport"),
                   commands=dict(commands), status=data.get("status"), selftest=list(selftest),
                   collect=data.get("collect"), source=source, description=str(data.get("description") or ""))


@dataclass
class ResolvedType:
    """What a bound component does: commands after every override (section 7.3), status, self-test."""
    lab_type: str
    chain: list[str]
    transport: str
    commands: dict[str, dict]
    command_sources: dict[str, str]
    status: dict | None
    selftest: list[dict]
    collect: dict


class TypeLibrary:

    def __init__(self, types: dict[str, TypeDef], strict: bool = True):
        self.types = types
        for name in types if strict else []:
            self.chain(name)          # unknown parents and cycles

    @classmethod
    def load(cls, folders: list[Path], strict: bool = True) -> "TypeLibrary":
        types = {name: TypeDef.from_dict(name, data, "<builtin>") for name, data in BUILTIN_TYPES.items()}
        for name, data in registry.component_types.items():
            types[name] = TypeDef.from_dict(name, data, "<plugin>")
        seen: dict[str, str] = {}
        for folder in folders:
            if not folder.is_dir():
                raise SequencerError(f"type library folder not found: '{folder}'")
            for path in sorted(list(folder.rglob("*.yml")) + list(folder.rglob("*.yaml"))):
                data = load_yaml(path) or {}
                if not isinstance(data, dict) or not isinstance(data.get("component_types"), dict):
                    raise SequencerError(f"'{path}': expected a 'component_types' mapping")
                for name, definition in data["component_types"].items():
                    if name in seen:
                        raise SequencerError(f"component type '{name}' defined twice: '{seen[name]}' and '{path}'")
                    if name in BUILTIN_TYPES:
                        raise SequencerError(f"'{path}': '{name}' is a built-in type and cannot be redefined")
                    seen[name] = str(path)
                    types[name] = TypeDef.from_dict(name, definition, str(path))
        return cls(types, strict)

    def chain(self, name: str, extra: dict[str, TypeDef] | None = None) -> list[TypeDef]:
        """The type and its ancestors, base first."""
        pool = {**self.types, **(extra or {})}
        result: list[TypeDef] = []
        current = name
        while current:
            if current not in pool:
                raise SequencerError(f"unknown component type '{current}'" + (f" (extended by '{result[0].name}')" if result else ""))
            if any(t.name == current for t in result):
                raise SequencerError(f"component type '{name}': 'extends' cycle through '{current}'")
            result.insert(0, pool[current])
            current = pool[current].extends
        return result

    def is_a(self, name: str, ancestor: str) -> bool:
        return any(t.name == ancestor for t in self.chain(name))

    def resolve(self, component: Component, stage: Stage | None = None) -> ResolvedType:
        """Resolve a concrete component's type: its role's chain, then the stage and host overrides.

        Order, last wins: role type chain (base first), stage ``tsq_commands``, host ``tsq_commands``.
        Inline ``run:`` in a step comes on top, in the engine.
        """
        lab_chain = self.chain(component.role)
        commands: dict[str, dict] = {}
        sources: dict[str, str] = {}
        status = None
        selftest: list[dict] = []
        collect: dict = {}
        transport = None

        for t in lab_chain:
            for cmd, spec in t.commands.items():
                commands[cmd] = dict(spec)
                sources[cmd] = f"type {t.name}"
            if t.status is not None:
                status = dict(t.status)
            if t.transport:
                transport = t.transport
            if t.collect:
                collect = {**collect, **t.collect}
            for check in t.selftest:
                selftest[:] = [c for c in selftest if c["id"] != check["id"]] + [dict(check)]

        if stage is not None:
            for cmd, spec in stage.commands.items():
                commands[cmd] = dict(spec)
                sources[cmd] = f"stage {stage.id}"
        for cmd, spec in (component.vars.get("tsq_commands") if component.id and
                          "tsq_commands" in component._host_vars else {}).items():
            commands[cmd] = dict(spec)
            sources[cmd] = f"host {component.id}"

        transport = str(component.vars.get("tsq_transport") or transport or "ssh")
        return ResolvedType(lab_type=component.role, chain=[t.name for t in lab_chain], transport=transport,
                            commands=commands, command_sources=sources, status=status, selftest=selftest,
                            collect=collect)
