"""Naming convention (README section 2): ids are in lower case, lab ids start with ``lab_``
(``lab_calc_pilot``), and lab names (component labels, ``tsq_label``) are in capitals (``CALC-PILOT``).

Lab side: stages, stage types, components (inventory hosts), roles and lab component types. The
built-in types (``component``, ``linux_system``) keep their names. Test side: setups, test
definitions, configurations, logical stages and components, datapack types, phases and steps.
Breaking the convention is a warning, never an error: existing labs and datapacks still load and run.
"""
from __future__ import annotations

from typing import Any, Iterable

LAB_PREFIX = "lab_"


def is_lab_id(value: str) -> bool:
    return value.startswith(LAB_PREFIX) and len(value) > len(LAB_PREFIX) and value == value.lower()


def lab_warnings(inventory, types=None) -> list[str]:
    """Lab ids of the inventory and its type library not lab_ then lower case, and component labels not
    in capitals."""
    found: list[str] = []

    def check(kind: str, value: Any) -> None:
        if value and not is_lab_id(str(value)):
            found.append(f"{kind} '{value}' should be '{LAB_PREFIX}' then lower case, like '{LAB_PREFIX}"
                         f"{str(value).lower().removeprefix(LAB_PREFIX)}' (the convention for lab ids)")

    for stage in inventory.stages.values():
        check("stage", stage.id)
    for stage_type in sorted({s.type for s in inventory.stages.values()}):
        check("stage type", stage_type)
    for comp in inventory.components.values():
        check("component", comp.id)
    for comp in inventory.components.values():
        label = comp.vars.get("tsq_label")
        if label and str(label) != str(label).upper():
            found.append(f"component '{comp.id}': label '{label}' should be in capitals (the convention for lab names)")
    for role in sorted({c.role for c in inventory.components.values() if c.role}):
        check("role", role)
    if types is not None:
        for t in types.types.values():
            if t.source != "<builtin>":
                check("lab type", t.name)
    return found


def test_warnings(setup: dict | None = None, configuration: str | None = None, testdef: dict | None = None,
                  phases: Iterable | None = None) -> list[str]:
    """Test ids of a setup (one configuration, or all), a test definition and its phases that are not in
    lower case, or use the lab_ prefix kept for lab ids."""
    found: list[str] = []

    def check(kind: str, value: Any) -> None:
        if not value:
            return
        value = str(value)
        if value.lower().startswith(LAB_PREFIX):
            found.append(f"{kind} '{value}' starts with '{LAB_PREFIX}', the prefix kept for lab ids")
        elif value != value.lower():
            found.append(f"{kind} '{value}' should be in lower case (the convention for test ids)")

    if setup:
        check("setup", setup.get("id"))
        for name in (setup.get("component_types") or {}):
            check("datapack type", name)
        for name, cfg in (setup.get("configurations") or {}).items():
            if configuration and name != configuration:
                continue
            check("setup configuration", name)
            cfg = cfg if isinstance(cfg, dict) else {}
            for stage in cfg.get("stages") or {}:
                check("logical stage", stage)
            for comp in cfg.get("components") or {}:
                check("logical component", comp)
    if testdef:
        check("test definition", testdef.get("id"))
    for flat in phases or []:
        phase = getattr(flat, "phase", flat)
        check("phase", phase.id)
        for part in (getattr(flat, "steps", None) or getattr(phase, "steps", []),
                     getattr(flat, "finally_", None) or getattr(phase, "finally_", None) or []):
            for step in part:
                check("step", getattr(step, "id", None))
    return found
