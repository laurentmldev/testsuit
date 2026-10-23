"""Binding and scheduling (README sections 8.1 and 9).

Binding maps the logical stages and components a phase needs onto free concrete ones of the lab:
- a logical stage binds a concrete stage of its type that has a component for each role it needs;
- a logical component of a stage binds the component with its role in that stage;
- an ad hoc logical component binds a free ad hoc component with its role;
- a component must be of the lab type its datapack type ``extends`` (or derive from it).

The scheduler walks the queue by priority, then submission time. A lower-priority run may start
while a higher one waits, but only on resources the waiting one cannot use (backfilling).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable

from testsuit.sequencer.inventory import Component, Stage
from testsuit.sequencer.lab import Lab
from testsuit.sequencer.model import Needs, Phase

UNAVAILABLE_STATES = ("broken", "offline")


@dataclass
class Binding:
    stages: dict[str, str] = field(default_factory=dict)          # logical stage -> concrete stage
    components: dict[str, str] = field(default_factory=dict)      # logical component -> concrete component

    def locks(self, lab: Lab) -> list[tuple[str, str, str]]:
        """(kind, resource, mode) to take for this binding: whole stages, and the ad hoc components."""
        wanted: dict[tuple[str, str], str] = {}
        inv = lab.inventory
        for stage_id in self.stages.values():
            wanted[("stage", stage_id)] = "exclusive"
            for comp_id in inv.stages[stage_id].components:
                mode = inv.components[comp_id].lock
                if mode != "none":
                    key = ("component", comp_id)
                    wanted[key] = "exclusive" if "exclusive" in (mode, wanted.get(key)) else "shared"
        for comp_id in self.components.values():
            comp = inv.components[comp_id]
            if comp.adhoc and comp.lock != "none":
                key = ("component", comp_id)
                wanted[key] = "exclusive" if "exclusive" in (comp.lock, wanted.get(key)) else "shared"
        return [(k, r, m) for (k, r), m in wanted.items()]

    def resources(self, lab: Lab) -> set[tuple[str, str]]:
        return {(k, r) for k, r, _ in self.locks(lab)} | {("component", c) for c in self.components.values()}

    def as_dict(self) -> dict:
        return {"stages": dict(self.stages), "components": dict(self.components)}


@dataclass
class Availability:
    """What binding needs to know about the lab right now."""
    states: dict[str, str] = field(default_factory=dict)                    # component -> monitored state
    maintenance: set[tuple[str, str]] = field(default_factory=set)          # (kind, id) in maintenance or held
    holders: dict[tuple[str, str], list[tuple[str, str]]] = field(default_factory=dict)  # -> [(run, mode)]
    usage: dict[str, dict] = field(default_factory=dict)

    def component_usable(self, comp: Component) -> str | None:
        """Why a component cannot be used at all now (None when it can)."""
        if ("component", comp.id) in self.maintenance:
            return f"{comp.id} in maintenance"
        state = self.states.get(comp.id, "unknown")
        if state in UNAVAILABLE_STATES:
            return f"{comp.id} {state}"
        return None

    def component_free(self, comp: Component, run_id: str) -> str | None:
        if comp.lock == "none":
            return None
        for holder, mode in self.holders.get(("component", comp.id), []):
            if holder != run_id and (comp.lock == "exclusive" or mode == "exclusive"):
                return f"{comp.id} held by run {holder}"
        return None


def _type_ok(lab: Lab, needs: Needs, logical_type: str, concrete: Component) -> bool:
    base = logical_type
    seen = set()
    while base in needs.component_types and base not in seen:
        seen.add(base)
        base = (needs.component_types[base] or {}).get("extends") or ""
    if not base:
        return True
    try:
        return lab.types.is_a(concrete.type, base)
    except Exception:
        return False


def phase_needs(phases: list[Phase]) -> tuple[list[str], list[str]]:
    stages: list[str] = []
    adhoc: list[str] = []
    for phase in phases:
        stages += [s for s in phase.stages if s not in stages]
        adhoc += [c for c in phase.components if c not in adhoc]
    return stages, adhoc


def find_binding(lab: Lab, needs: Needs, phases: list[Phase], avail: Availability, run_id: str,
                 fixed: Binding | None = None, avoid: set[tuple[str, str]] | None = None,
                 testdef: str = "", check_free: bool = True) -> tuple[Binding | None, list[str]]:
    """Bind what the phases need to concrete resources.

    :param fixed: what the run already holds (kept as it is)
    :param avoid: resources reserved for higher-priority waiting runs (backfilling)
    :param check_free: False to find what could be used, ignoring locks (for backfilling and validation)
    :return: (binding, None) or (None, reasons why not)
    """
    inv = lab.inventory
    fixed = fixed or Binding()
    avoid = avoid or set()
    logical_stages, adhoc = phase_needs(phases)
    reasons: list[str] = []

    def stage_candidates(logical: str) -> list[Stage]:
        if logical in fixed.stages:
            return [inv.stages[fixed.stages[logical]]]
        stage_type = needs.stages[logical]
        found = []
        stages = inv.stages_of_type(stage_type)
        if not stages:
            reasons.append(f"no stage of type '{stage_type}'")
        for stage in stages:
            why = None
            if ("stage", stage.id) in avail.maintenance:
                why = f"{stage.id} in maintenance or held"
            elif ("stage", stage.id) in avoid:
                why = f"{stage.id} kept for a higher-priority run"
            for lc in needs.in_stage(logical):
                if why:
                    break
                comp = inv.component_in(stage, lc.role)
                if comp is None:
                    why = f"{stage.id} has no '{lc.role}'"
                elif not _type_ok(lab, needs, lc.type, comp):
                    why = f"{stage.id}: {comp.id} ({comp.type}) cannot run '{lc.type}'"
            for comp_id in stage.components:
                if why:
                    break
                comp = inv.components[comp_id]
                why = avail.component_usable(comp) or (avail.component_free(comp, run_id) if check_free else None)
                if why is None and ("component", comp_id) in avoid and comp.lock == "exclusive":
                    why = f"{comp_id} kept for a higher-priority run"
            if why is None and check_free:
                for holder, _ in avail.holders.get(("stage", stage.id), []):
                    if holder != run_id:
                        why = f"{stage.id} held by run {holder}"
            if why:
                reasons.append(why)
            else:
                found.append(stage)

        def preference(stage: Stage):
            usage = avail.usage.get(stage.id, {})
            return (0 if testdef in usage.get("by_testdef", {}) else 1, usage.get("count", 0), stage.id)
        return sorted(found, key=preference)

    def adhoc_candidates(logical: str) -> list[Component]:
        if logical in fixed.components:
            return [inv.components[fixed.components[logical]]]
        lc = needs.components[logical]
        found = []
        pool = [c for c in inv.adhoc if c.role == lc.role]
        if not pool:
            reasons.append(f"no ad hoc component with role '{lc.role}'")
        for comp in pool:
            why = None
            if not _type_ok(lab, needs, lc.type, comp):
                why = f"{comp.id} ({comp.type}) cannot run '{lc.type}'"
            why = why or avail.component_usable(comp) or (avail.component_free(comp, run_id) if check_free else None)
            if why is None and ("component", comp.id) in avoid and comp.lock == "exclusive":
                why = f"{comp.id} kept for a higher-priority run"
            if why:
                reasons.append(why)
            else:
                found.append(comp)
        return sorted(found, key=lambda c: (avail.usage.get(c.id, {}).get("count", 0), c.id))

    stage_options = [stage_candidates(s) for s in logical_stages]
    adhoc_options = [adhoc_candidates(c) for c in adhoc]
    for stage_choice in itertools.product(*stage_options):
        if len({s.id for s in stage_choice}) != len(stage_choice):
            continue
        for comp_choice in itertools.product(*adhoc_options):
            exclusive = [c.id for c in comp_choice if c.lock == "exclusive"]
            if len(set(exclusive)) != len(exclusive):
                continue
            binding = Binding(stages=dict(fixed.stages), components=dict(fixed.components))
            for logical, stage in zip(logical_stages, stage_choice):
                binding.stages[logical] = stage.id
                for lc in needs.in_stage(logical):
                    binding.components[lc.id] = inv.component_in(stage, lc.role).id
            for logical, comp in zip(adhoc, comp_choice):
                binding.components[logical] = comp.id
            return binding, []
    unique = list(dict.fromkeys(reasons)) or ["no combination of distinct free resources"]
    return None, unique


def candidate_resources(lab: Lab, needs: Needs, phases: list[Phase], avail: Availability) -> set[tuple[str, str]]:
    """Every resource a run could use, locks ignored: what backfilling keeps away from lower priorities."""
    inv = lab.inventory
    result: set[tuple[str, str]] = set()
    logical_stages, adhoc = phase_needs(phases)
    for logical in logical_stages:
        for stage in inv.stages_of_type(needs.stages[logical]):
            if all(inv.component_in(stage, lc.role) for lc in needs.in_stage(logical)):
                if all(avail.component_usable(inv.components[c]) is None for c in stage.components):
                    result.add(("stage", stage.id))
                    result.update(("component", c) for c in stage.components)
    for logical in adhoc:
        lc = needs.components[logical]
        for comp in inv.adhoc:
            if comp.role == lc.role and avail.component_usable(comp) is None:
                result.add(("component", comp.id))
    return result


def effective_priority(run: dict, aging: tuple[int, float] | None, current: float) -> int:
    """Priority with aging: +increment per period waited, capped at 100."""
    priority = int(run["priority"])
    if aging and run.get("submitted_at"):
        from datetime import datetime
        waited = current - datetime.fromisoformat(run["submitted_at"]).timestamp()
        priority = min(100, priority + int(waited // aging[1]) * aging[0])
    return priority


def order_queue(runs: list[dict], aging: tuple[int, float] | None, current: float) -> list[dict]:
    return sorted(runs, key=lambda r: (-effective_priority(r, aging, current), r["submitted_at"]))


Activate = Callable[[dict, Binding], None]
