"""Binding and scheduling (README sections 8.1 and 9).

Binding maps the stage slots and slots a phase needs onto free resources of the lab:
- a stage slot binds a lab stage of its type that has a component for each role its slots need;
- a slot of a stage slot binds the lab component with its role in the lab stage bound to that stage slot;
- an ad hoc slot binds a free ad hoc lab component with its role.

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
    stage_slots: dict[str, str] = field(default_factory=dict)          # stage_slot -> lab stage
    slots: dict[str, str] = field(default_factory=dict)           # slot -> lab component

    def locks(self, lab: Lab) -> list[tuple[str, str, str]]:
        """(kind, resource, mode) to take for this binding: whole stages, and the components of the ad hoc slots."""
        wanted: dict[tuple[str, str], str] = {}
        inv = lab.inventory
        for stage_id in self.stage_slots.values():
            wanted[("stage", stage_id)] = "exclusive"
            for comp_id in inv.stages[stage_id].components:
                mode = inv.components[comp_id].lock
                if mode != "none":
                    key = ("component", comp_id)
                    wanted[key] = "exclusive" if "exclusive" in (mode, wanted.get(key)) else "shared"
        for comp_id in self.slots.values():
            comp = inv.components[comp_id]
            if comp.adhoc and comp.lock != "none":
                key = ("component", comp_id)
                wanted[key] = "exclusive" if "exclusive" in (comp.lock, wanted.get(key)) else "shared"
        return [(k, r, m) for (k, r), m in wanted.items()]

    def resources(self, lab: Lab) -> set[tuple[str, str]]:
        return {(k, r) for k, r, _ in self.locks(lab)} | {("component", c) for c in self.slots.values()}

    def as_dict(self) -> dict:
        return {"stage_slots": dict(self.stage_slots), "slots": dict(self.slots)}

    @classmethod
    def from_dict(cls, data: dict | None) -> Binding:
        """A binding as stored (a run recorded under former names keeps its 'realms' or 'stages', 'components')."""
        data = data or {}
        return cls(stage_slots=dict(data.get("stage_slots") or data.get("realms") or data.get("stages") or {}),
                   slots=dict(data.get("slots") or data.get("components") or {}))


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


def phase_needs(phases: list[Phase]) -> tuple[list[str], list[str]]:
    stages: list[str] = []
    adhoc: list[str] = []
    for phase in phases:
        stages += [s for s in phase.stage_slots if s not in stages]
        adhoc += [c for c in phase.slots if c not in adhoc]
    return stages, adhoc


def stage_problem(lab: Lab, needs: Needs, logical: str, stage: Stage, avail: Availability, run_id: str,
                  avoid: set[tuple[str, str]] = frozenset(), check_free: bool = True) -> str | None:
    """Why a lab stage cannot be bound to a stage slot now (None when it can)."""
    inv = lab.inventory
    if ("stage", stage.id) in avail.maintenance:
        return f"{stage.id} in maintenance or held"
    if ("stage", stage.id) in avoid:
        return f"{stage.id} kept for a higher-priority run"
    missing = [t for t in needs.stage_slot_tags.get(logical, []) if t not in stage.tags]
    if missing:
        return f"{stage.id} is not tagged {', '.join(missing)}"
    slots = needs.in_stage_slot(logical)
    if lab.stage_assignment(stage, slots) is None:
        return lab.stage_shortfall(stage, slots)
    for comp_id in stage.components:
        comp = inv.components[comp_id]
        why = avail.component_usable(comp) or (avail.component_free(comp, run_id) if check_free else None)
        if why is None and ("component", comp_id) in avoid and comp.lock == "exclusive":
            why = f"{comp_id} kept for a higher-priority run"
        if why:
            return why
    if check_free:
        for holder, _ in avail.holders.get(("stage", stage.id), []):
            if holder != run_id:
                return f"{stage.id} held by run {holder}"
    return None


def adhoc_problem(comp: Component, avail: Availability, run_id: str,
                  avoid: set[tuple[str, str]] = frozenset(), check_free: bool = True) -> str | None:
    """Why an ad hoc component of the right role cannot be bound now (None when it can)."""
    why = avail.component_usable(comp) or (avail.component_free(comp, run_id) if check_free else None)
    if why is None and ("component", comp.id) in avoid and comp.lock == "exclusive":
        why = f"{comp.id} kept for a higher-priority run"
    return why


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
        if logical in fixed.stage_slots:
            return [inv.stages[fixed.stage_slots[logical]]]
        stage_type = needs.stage_slots[logical]
        found = []
        stages = inv.stages_of_type(stage_type)
        if not stages:
            reasons.append(f"no stage of type '{stage_type}'")
        for stage in stages:
            why = stage_problem(lab, needs, logical, stage, avail, run_id, avoid, check_free)
            if why:
                reasons.append(why)
            else:
                found.append(stage)

        def preference(stage: Stage):
            usage = avail.usage.get(stage.id, {})
            return (0 if testdef in usage.get("by_testdef", {}) else 1, usage.get("count", 0), stage.id)
        return sorted(found, key=preference)

    def adhoc_candidates(logical: str) -> list[Component]:
        if logical in fixed.slots:
            return [inv.components[fixed.slots[logical]]]
        lc = needs.slots[logical]
        found = []
        pool = [c for c in inv.adhoc if lab.fits(c, lc.role, lc.tags)]
        if not pool:
            reasons.append(f"no ad hoc component with role '{lc.role}'"
                           + (f" tagged {', '.join(lc.tags)}" if lc.tags else ""))
        for comp in pool:
            why = adhoc_problem(comp, avail, run_id, avoid, check_free)
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
            binding = Binding(stage_slots=dict(fixed.stage_slots), slots=dict(fixed.slots))
            for logical, stage in zip(logical_stages, stage_choice):
                binding.stage_slots[logical] = stage.id
                for slot_id, comp in lab.stage_assignment(stage, needs.in_stage_slot(logical)).items():
                    binding.slots[slot_id] = comp.id
            for logical, comp in zip(adhoc, comp_choice):
                binding.slots[logical] = comp.id
            return binding, []
    unique = list(dict.fromkeys(reasons)) or ["no combination of distinct free resources"]
    return None, unique


def candidate_resources(lab: Lab, needs: Needs, phases: list[Phase], avail: Availability) -> set[tuple[str, str]]:
    """Every resource a run could use, locks ignored: what backfilling keeps away from lower priorities."""
    inv = lab.inventory
    result: set[tuple[str, str]] = set()
    logical_stages, adhoc = phase_needs(phases)
    for logical in logical_stages:
        for stage in inv.stages_of_type(needs.stage_slots[logical]):
            if set(needs.stage_slot_tags.get(logical, [])) <= set(stage.tags) \
                    and lab.stage_assignment(stage, needs.in_stage_slot(logical)) is not None:
                if all(avail.component_usable(inv.components[c]) is None for c in stage.components):
                    result.add(("stage", stage.id))
                    result.update(("component", c) for c in stage.components)
    for logical in adhoc:
        lc = needs.slots[logical]
        for comp in inv.adhoc:
            if lab.fits(comp, lc.role, lc.tags) and avail.component_usable(comp) is None:
                result.add(("component", comp.id))
    return result


def resource_overview(lab: Lab, needs: Needs, phases: list[Phase], avail: Availability, run_id: str,
                      binding: Binding | None = None) -> dict:
    """What a run expects of the lab against what the lab has: each stage slot and ad hoc slot
    the datapack needs, the phases using it, what it is bound to, and each lab candidate with why it
    is not available now (None when it is)."""
    inv = lab.inventory
    binding = binding or Binding()

    def used_by(attr: str, logical: str) -> list[str]:
        return [p.id for p in phases if logical in getattr(p, attr)]

    def comp_facts(comp: Component) -> dict:
        return {"id": comp.id, "extends": comp.extends, "role": comp.role, "tags": comp.tags, "lock": comp.lock,
                "state": avail.states.get(comp.id, "unknown"),
                "maintenance": ("component", comp.id) in avail.maintenance,
                "held_by": [h for h, _ in avail.holders.get(("component", comp.id), []) if h != run_id]}

    stages = []
    for logical, stage_type in needs.stage_slots.items():
        candidates = []
        for stage in inv.stages_of_type(stage_type):
            assigned = lab.stage_assignment(stage, needs.in_stage_slot(logical)) or {}
            roles = {lc.id: assigned[lc.id].id if lc.id in assigned else None for lc in needs.in_stage_slot(logical)}
            candidates.append({
                "id": stage.id, "tags": stage.tags, "bound": binding.stage_slots.get(logical) == stage.id,
                "why": None if binding.stage_slots.get(logical) == stage.id
                else stage_problem(lab, needs, logical, stage, avail, run_id),
                "maintenance": ("stage", stage.id) in avail.maintenance,
                "held_by": [h for h, _ in avail.holders.get(("stage", stage.id), []) if h != run_id],
                "roles": roles, "components": [comp_facts(inv.components[c]) for c in stage.components]})
        stages.append({
            "logical": logical, "type": stage_type, "phases": used_by("stage_slots", logical),
            "bound": binding.stage_slots.get(logical),
            "tags": needs.stage_slot_tags.get(logical, []),
            "slots": [{"logical": lc.id, "type": lc.type, "role": lc.role, "tags": lc.tags, "name": lc.name}
                      for lc in needs.in_stage_slot(logical)],
            "problem": None if candidates else f"no stage of type '{stage_type}' in the lab",
            "candidates": candidates})
    slots = []
    for lc in needs.slots.values():
        if lc.stage_slot:
            continue
        candidates = []
        for comp in inv.adhoc:
            if not lab.fits(comp, lc.role, lc.tags):
                continue
            bound = binding.slots.get(lc.id) == comp.id
            candidates.append({**comp_facts(comp), "bound": bound,
                               "why": None if bound else adhoc_problem(comp, avail, run_id)})
        slots.append({
            "logical": lc.id, "type": lc.type, "role": lc.role, "tags": lc.tags, "name": lc.name,
            "phases": used_by("slots", lc.id), "bound": binding.slots.get(lc.id),
            "problem": None if candidates else f"no ad hoc component with role '{lc.role}' in the lab",
            "candidates": candidates})
    return {"stage_slots": stages, "slots": slots}


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
