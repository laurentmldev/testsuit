"""Datapack side of the sequencer (README section 8): resources needed, pipeline, sub-pipelines.

The pipeline is read from the test definition kept in the datapack (``definition/testdef.yml``),
and the resources from its setup (``definition/setup.yml``), both as written: their templates are
rendered at run time, with the datapack's frozen keys and the run-time keys.
"""
from __future__ import annotations

import graphlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from testsuit.datatools.datapack.model import FORMER_PHASE, FORMER_STEP, former_names
from testsuit.sequencer import registry
from testsuit.sequencer.util import SequencerError, load_yaml

BUILTIN_ACTIONS = ("do", "run", "python", "http", "push", "pull", "delay", "wait_for", "uses")
PYTHON_KEYS = ("script", "code", "args", "interpreter")


def check_python(spec: Any, where: str) -> None:
    """A ``python`` action (README section 8.3, SQ-32): exactly one of ``script`` and ``code``."""
    if not isinstance(spec, dict):
        raise SequencerError(f"{where}: 'python' takes a mapping with 'script' or 'code'")
    unknown = sorted(set(spec) - set(PYTHON_KEYS))
    if unknown:
        raise SequencerError(f"{where}: unknown 'python' key(s) {', '.join(unknown)} (known: {', '.join(PYTHON_KEYS)})")
    if ("script" in spec) == ("code" in spec):
        raise SequencerError(f"{where}: 'python' takes exactly one of 'script' and 'code'")
    if not isinstance(spec.get("args", []), list):
        raise SequencerError(f"{where}: 'python.args' is a list")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _refuse_former(data: Any, former: dict[str, str], what: str) -> Any:
    """A clean break: a former key name is refused, naming the new one."""
    if isinstance(data, dict):
        found = [k for k in former if k in data]
        if found:
            where = f"{what} '{data['id']}'" if data.get("id") else what
            raise ValueError("; ".join(f"{where}: '{k}' is now '{former[k]}'" for k in found))
    return data


class Step(BaseModel):
    """One step: a target (slot or for_each), one action, and options."""
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    slot: str | None = None
    for_each: list[str] | str | None = None
    # actions (exactly one; a plugin step type is given by its name as key)
    do: str | None = None
    run: str | None = None
    python: dict | None = None
    http: dict | str | None = None
    push: dict | None = None
    pull: dict | None = None
    delay: str | float | None = None
    wait_for: dict | None = None
    uses: str | None = None
    with_: dict = Field(default_factory=dict, alias="with")
    # options
    needs: list[str] = Field(default_factory=list)
    if_: str | None = Field(None, alias="if")
    expect: dict | None = None
    timeout: str | float | None = None
    retries: int = 0
    detach: bool | None = None
    continue_on_error: bool = False
    env: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _former(cls, data):
        return _refuse_former(data, FORMER_STEP, "step")

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        if not v or "." in v or "[" in v or "/" in v:
            raise ValueError(f"step id '{v}' must be non-empty, without '.', '[' or '/'")
        return v

    @property
    def plugin_actions(self) -> dict[str, Any]:
        return {k: v for k, v in (self.model_extra or {}).items()}

    @property
    def action(self) -> str:
        actions = [a for a in BUILTIN_ACTIONS if getattr(self, a) is not None]
        actions += list(self.plugin_actions)
        if len(actions) != 1:
            raise SequencerError(f"step '{self.id}': expected exactly one action among "
                                 f"{', '.join(BUILTIN_ACTIONS)} or a plugin step type, found {actions or 'none'}")
        return actions[0]

    def check(self) -> None:
        action = self.action
        if action not in BUILTIN_ACTIONS and action.lower() not in registry.step_types:
            raise SequencerError(f"step '{self.id}': unknown key or step type '{action}'")
        if action == "python":
            check_python(self.python, f"step '{self.id}'")
        if action == "http":
            from testsuit.sequencer.httpcall import normalize
            normalize(self.http, f"step '{self.id}'")
        if action in ("do", "push", "pull") and not (self.slot or self.for_each):
            raise SequencerError(f"step '{self.id}': '{action}' needs a 'slot' or 'for_each'")
        if self.slot and self.for_each is not None:
            raise SequencerError(f"step '{self.id}': give 'slot' or 'for_each', not both")
        if action == "uses" and (self.slot or self.for_each is not None):
            raise SequencerError(f"step '{self.id}': a 'uses' step has no slot")


class SubPipeline(_Model):
    params: dict[str, Any] = Field(default_factory=dict)
    steps: list[Step]


class PhaseInput(_Model):
    from_: str = Field(alias="from")
    to: str


class Phase(_Model):
    id: str
    needs: list[str] = Field(default_factory=list)
    stage_slots: list[str] = Field(default_factory=list)
    slots: list[str] = Field(default_factory=list)
    inputs: list[PhaseInput] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    finally_: list[Step] = Field(default_factory=list, alias="finally")
    timeout: str | float | None = None

    @model_validator(mode="before")
    @classmethod
    def _former(cls, data):
        return _refuse_former(data, FORMER_PHASE, "phase")


class Pipeline(_Model):
    timeout: str | float | None = None
    include: list[str] = Field(default_factory=list)
    subpipelines: dict[str, SubPipeline] = Field(default_factory=dict)
    phases: list[Phase] = Field(default_factory=list)
    baseline: str | None = None
    reserve: Literal["phase", "all"] = "phase"
    on_state_mismatch: Literal["warn", "fail"] = "warn"

    @model_validator(mode="after")
    def _unique(self):
        ids = [p.id for p in self.phases]
        if len(ids) != len(set(ids)):
            raise ValueError(f"phase ids must be unique: {ids}")
        return self


# ── resources needed (setup configuration) ─────────────────────────────────

@dataclass
class LogicalSlot:
    id: str
    type: str                                    # slot type (setup 'slot_types')
    name: str = ""
    stage_slot: str = ""                              # its stage slot, '' for an ad hoc slot
    role: str = ""                               # a type of the lab: binds to a lab component, which gives the commands
    tags: list[str] = field(default_factory=list)  # tags the lab component must have


@dataclass
class Needs:
    stage_slots: dict[str, str]                       # stage_slot -> lab stage type
    slots: dict[str, LogicalSlot]
    slot_types: dict[str, dict]                  # slot types (setup file)
    stage_slot_tags: dict[str, list[str]] = field(default_factory=dict)   # tags its lab stage must have

    def in_stage_slot(self, stage_slot: str) -> list[LogicalSlot]:
        return [c for c in self.slots.values() if c.stage_slot == stage_slot]


def check_sequencer_fields(setup: dict) -> None:
    """Former names, and the fields the role replaced (the lab role gives the commands)."""
    former = former_names(setup)
    if former:
        raise SequencerError("; ".join(former))
    for name, t in (setup.get("slot_types") or {}).items():
        for key in ("extends", "commands"):
            if isinstance(t, dict) and key in t:
                raise SequencerError(f"setup slot type '{name}': '{key}' is gone, the slots' role gives "
                                     "the commands, implemented by the lab components (move test-specific commands to pipeline steps)")
    for config_name, config in (setup.get("configurations") or {}).items():
        for slot_id, slot in ((config or {}).get("slots") or {}).items():
            if isinstance(slot, dict) and "commands" in slot:
                raise SequencerError(f"setup configuration '{config_name}': slot '{slot_id}': 'commands' is gone, "
                                     "the lab role gives the commands (move test-specific ones to pipeline steps)")


def needs_from_setup(setup: dict, configuration: str) -> Needs:
    check_sequencer_fields(setup)
    configurations = setup.get("configurations") or {}
    if configuration not in configurations:
        raise SequencerError(f"setup has no configuration '{configuration}'")
    config = configurations[configuration] or {}
    stage_slots = {}
    stage_slot_tags = {}
    for stage_slot_id, stage_slot in (config.get("stage_slots") or {}).items():
        stage_type = stage_slot.get("type") if isinstance(stage_slot, dict) else stage_slot
        if not stage_type:
            raise SequencerError(f"setup configuration '{configuration}': stage slot '{stage_slot_id}' has no type")
        stage_slots[stage_slot_id] = str(stage_type)
        stage_slot_tags[stage_slot_id] = tag_list(stage_slot.get("tags") if isinstance(stage_slot, dict) else None,
                                                  f"setup configuration '{configuration}': stage slot '{stage_slot_id}'")
    slots = {}
    for slot_id, slot in (config.get("slots") or {}).items():
        stage_slot = slot.get("stage_slot") or ""
        if stage_slot and stage_slot not in stage_slots:
            raise SequencerError(f"setup configuration '{configuration}': slot '{slot_id}' is in unknown stage slot '{stage_slot}'")
        if not slot.get("role"):
            raise SequencerError(f"setup configuration '{configuration}': slot '{slot_id}' has no role "
                                 "(needed to bind it to a lab component)")
        slots[slot_id] = LogicalSlot(id=slot_id, type=slot.get("type", ""), name=slot.get("name", ""),
                                     stage_slot=stage_slot, role=slot["role"],
                                     tags=tag_list(slot.get("tags"), f"setup configuration '{configuration}': slot '{slot_id}'"))
    return Needs(stage_slots=stage_slots, slots=slots, slot_types=setup.get("slot_types") or {},
                 stage_slot_tags=stage_slot_tags)


def tag_list(value, where: str) -> list[str]:
    if not value:
        return []
    if not isinstance(value, list) or not all(isinstance(v, (str, int)) for v in value):
        raise SequencerError(f"{where}: 'tags' must be a list of tags")
    return [str(v) for v in value]


# ── pipeline loading, sub-pipelines, validation ────────────────────────────

def load_pipeline(raw: dict | None, definition_dir: Path | None = None) -> Pipeline:
    """The pipeline of a test definition, with its included sub-pipeline files."""
    if not raw:
        raise SequencerError("the test definition has no 'pipeline'")
    try:
        pipeline = Pipeline.model_validate(raw)
    except ValidationError as e:
        raise SequencerError(f"invalid pipeline:\n{e}") from e
    for name in pipeline.include:
        path = _find_include(name, definition_dir)
        data = load_yaml(path) or {}
        subs = data.get("subpipelines", data) if isinstance(data, dict) else None
        if not isinstance(subs, dict):
            raise SequencerError(f"included file '{path}': expected a 'subpipelines' mapping")
        for sub_name, sub in subs.items():
            if sub_name in pipeline.subpipelines:
                continue                                     # the test definition's own wins
            try:
                pipeline.subpipelines[sub_name] = SubPipeline.model_validate(sub)
            except ValidationError as e:
                raise SequencerError(f"included file '{path}': sub-pipeline '{sub_name}':\n{e}") from e
    return pipeline


def _find_include(name: str, definition_dir: Path | None) -> Path:
    candidates = []
    if definition_dir is not None:
        candidates += [definition_dir / name, definition_dir / "include" / name, definition_dir / Path(name).name,
                       definition_dir / "include" / Path(name).name]
    candidates.append(Path(name))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SequencerError(f"included sub-pipeline file not found: '{name}'")


@dataclass
class FlatStep:
    """A step after sub-pipeline expansion: namespaced id and needs, and its parameters."""
    id: str                                      # 'up.start_tm'
    step: Step
    needs: list[str]
    params: dict[str, Any]
    group: str = ""                              # innermost 'uses' step id, '' at top level


@dataclass
class FlatPhase:
    phase: Phase
    steps: list[FlatStep]
    finally_: list[FlatStep]
    groups: dict[str, list[str]]                 # 'uses' step id -> ids of all its steps


def flatten_steps(steps: list[Step], subpipelines: dict[str, SubPipeline], prefix: str = "",
                  params: dict | None = None, outer_needs: list[str] | None = None,
                  groups: dict[str, list[str]] | None = None, stack: tuple = (), group: str = "") -> list[FlatStep]:
    """Expand 'uses' steps into their sub-pipeline's steps, namespaced by the uses step id."""
    groups = {} if groups is None else groups
    params = params or {}
    local_ids = {s.id for s in steps}
    if len(local_ids) != len(steps):
        ids = [s.id for s in steps]
        raise SequencerError(f"duplicate step ids in {prefix.rstrip('.') or 'phase'}: "
                             f"{sorted({i for i in ids if ids.count(i) > 1})}")
    flat: list[FlatStep] = []
    for step in steps:
        step.check()
        needs = [prefix + n if n in local_ids else n for n in step.needs]
        if prefix:
            unknown = [n for n in step.needs if n not in local_ids]
            if unknown:
                raise SequencerError(f"step '{prefix}{step.id}': unknown needs {unknown} inside its sub-pipeline")
        if not step.needs and outer_needs:
            needs = list(outer_needs)
        full_id = prefix + step.id
        if step.uses is not None:
            if step.uses not in subpipelines:
                raise SequencerError(f"step '{full_id}': unknown sub-pipeline '{step.uses}'")
            if step.uses in stack:
                raise SequencerError(f"sub-pipeline cycle: {' -> '.join(stack + (step.uses,))}")
            sub = subpipelines[step.uses]
            unknown = set(step.with_) - set(sub.params)
            if unknown:
                raise SequencerError(f"step '{full_id}': unknown parameters {sorted(unknown)} for sub-pipeline '{step.uses}'")
            inner = flatten_steps(sub.steps, subpipelines, full_id + ".", {**sub.params, **step.with_},
                                  needs, groups, stack + (step.uses,), full_id)
            groups[full_id] = [s.id for s in inner]
            flat.extend(inner)
        else:
            flat.append(FlatStep(id=full_id, step=step, needs=needs, params=params, group=group))
    # a need on a 'uses' step waits for every step of the group
    for fs in flat:
        expanded = []
        for need in fs.needs:
            expanded.extend(groups.get(need, [need]))
        fs.needs = expanded
    return flat


def flatten_phase(phase: Phase, pipeline: Pipeline) -> FlatPhase:
    groups: dict[str, list[str]] = {}
    steps = flatten_steps(phase.steps, pipeline.subpipelines, groups=groups)
    finally_groups: dict[str, list[str]] = {}
    finally_steps = flatten_steps(phase.finally_, pipeline.subpipelines, groups=finally_groups)
    overlap = {s.id for s in steps} & {s.id for s in finally_steps}
    if overlap:
        raise SequencerError(f"phase '{phase.id}': ids used in both steps and finally: {sorted(overlap)}")
    for part, label in ((steps, "steps"), (finally_steps, "finally")):
        ids = {s.id for s in part}
        graph = {}
        for s in part:
            unknown = [n for n in s.needs if n not in ids]
            if unknown:
                raise SequencerError(f"phase '{phase.id}': step '{s.id}' needs unknown {label} step(s) {unknown}")
            graph[s.id] = set(s.needs)
        try:
            tuple(graphlib.TopologicalSorter(graph).static_order())
        except graphlib.CycleError as e:
            raise SequencerError(f"phase '{phase.id}': cycle in {label}: {' -> '.join(e.args[1])}") from e
    return FlatPhase(phase=phase, steps=steps, finally_=finally_steps, groups={**groups, **finally_groups})


def phase_slots(phase: Phase, needs: Needs) -> list[str]:
    """Slots a phase holds: those of its stage slots, then its ad hoc ones."""
    result = [c.id for r in phase.stage_slots for c in needs.in_stage_slot(r)]
    return result + [c for c in phase.slots if c not in result]


def pipeline_mismatches(pipeline: Pipeline, needs: Needs) -> list[str]:
    """Every stage_slot and slot the phases name that the setup configuration does not define
    as such (a setup changed without its pipeline, or the other way round)."""
    problems = []
    adhoc = [c.id for c in needs.slots.values() if not c.stage_slot]
    for phase in pipeline.phases:
        for stage_slot in phase.stage_slots:
            if stage_slot not in needs.stage_slots:
                problems.append(f"phase '{phase.id}': unknown stage slot '{stage_slot}' "
                                f"(setup stage_slots: {', '.join(needs.stage_slots) or 'none'})")
        for slot in phase.slots:
            if slot not in needs.slots:
                problems.append(f"phase '{phase.id}': unknown slot '{slot}' "
                                f"(setup ad hoc slots: {', '.join(adhoc) or 'none'})")
            elif needs.slots[slot].stage_slot:
                problems.append(f"phase '{phase.id}': slot '{slot}' belongs to stage slot "
                                f"'{needs.slots[slot].stage_slot}': list the stage slot instead")
    return problems


def validate_pipeline(pipeline: Pipeline, needs: Needs) -> list[FlatPhase]:
    """Structural checks done at submission: phases, needs, sub-pipelines, resources, literal targets."""
    phase_ids = [p.id for p in pipeline.phases]
    if not phase_ids:
        raise SequencerError("the pipeline has no phase")
    graph = {}
    for phase in pipeline.phases:
        unknown = [n for n in phase.needs if n not in phase_ids]
        if unknown:
            raise SequencerError(f"phase '{phase.id}' needs unknown phase(s) {unknown}")
        graph[phase.id] = set(phase.needs)
    mismatches = pipeline_mismatches(pipeline, needs)
    if mismatches:
        raise SequencerError("; ".join(mismatches))
    try:
        tuple(graphlib.TopologicalSorter(graph).static_order())
    except graphlib.CycleError as e:
        raise SequencerError(f"cycle between phases: {' -> '.join(e.args[1])}") from e
    flats = []
    for phase in pipeline.phases:
        flat = flatten_phase(phase, pipeline)
        held = phase_slots(phase, needs)
        roles = {needs.slots[c].role for c in held}
        for fs in flat.steps + flat.finally_:
            targets = []
            if fs.step.slot and "{{" not in fs.step.slot:
                targets = [fs.step.slot]
            elif isinstance(fs.step.for_each, list):
                targets = [t for t in fs.step.for_each if "{{" not in t]
            elif isinstance(fs.step.for_each, str) and "{{" not in fs.step.for_each \
                    and fs.step.for_each != "slots" and fs.step.for_each not in roles:
                hint = " ('components' is now 'slots')" if fs.step.for_each == "components" else ""
                raise SequencerError(f"phase '{phase.id}': step '{fs.id}': for_each '{fs.step.for_each}' is neither "
                                     f"'slots', a role, nor a list of slots of the phase{hint}")
            outside = [t for t in targets if t not in held]
            if outside:
                raise SequencerError(f"phase '{phase.id}': step '{fs.id}' targets slot(s) {outside} "
                                     f"outside the phase (it holds: {', '.join(held) or 'none'})")
        flats.append(flat)
    return flats
