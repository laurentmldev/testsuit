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

BUILTIN_ACTIONS = ("do", "run", "python", "http", "exploit", "manual", "push", "pull", "delay", "wait_for", "uses")
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
    exploit: dict | None = None                  # criteria checked on outputs of the run (exploitstep.py)
    manual: dict | None = None                   # an operation a person does on the bench (manualstep.py)
    push: dict | None = None
    pull: dict | None = None
    delay: str | float | None = None
    wait_for: dict | None = None
    uses: str | None = None
    # the parameters of a sub-pipeline (uses) or a lab command (do), the keys of exploit criteria (exploit)
    with_: dict = Field(default_factory=dict, alias="with")
    # options
    needs: list[str] = Field(default_factory=list)
    if_: str | None = Field(None, alias="if")
    expect: dict | None = None
    timeout: str | float | None = None
    retries: int = 0
    detach: bool | None = None
    # True: steps that need it run even when it does not succeed, and it is left out of the phase verdict.
    # None (not written): False, except for an exploit step, whose dependants run anyway while it still counts.
    continue_on_error: bool | None = None
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
    def goes_on(self) -> bool:
        """Steps that need this one run even when it does not succeed."""
        if self.continue_on_error is not None:
            return self.continue_on_error
        return self.exploit is not None

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
        if action == "exploit":
            from testsuit.sequencer.exploitstep import check_exploit
            check_exploit(self.exploit, f"step '{self.id}'")
            if self.slot or self.for_each is not None:
                raise SequencerError(f"step '{self.id}': an 'exploit' step runs on the sequencer host, it has no "
                                     "'slot' nor 'for_each' (its 'data' names the slots' files)")
        if action == "manual":
            from testsuit.sequencer.manualstep import check_manual
            check_manual(self.manual, f"step '{self.id}'")
            if self.slot or self.for_each is not None:
                raise SequencerError(f"step '{self.id}': a 'manual' step waits on the sequencer host for the "
                                     "operator, it has no 'slot' nor 'for_each'")
        if action in ("do", "push", "pull") and not (self.slot or self.for_each):
            raise SequencerError(f"step '{self.id}': '{action}' needs a 'slot' or 'for_each'")
        if self.slot and self.for_each is not None:
            raise SequencerError(f"step '{self.id}': give 'slot' or 'for_each', not both")
        if action == "uses" and (self.slot or self.for_each is not None):
            raise SequencerError(f"step '{self.id}': a 'uses' step has no slot")
        if self.with_ and action not in ("do", "uses", "exploit"):
            raise SequencerError(f"step '{self.id}': 'with' gives the parameters of a command (do), of a sub-pipeline "
                                 f"(uses) or the keys of exploit criteria, not of a '{action}' step")
        if action == "do":
            bad = [str(k) for k in self.with_ if not str(k).isidentifier()]
            if bad:
                raise SequencerError(f"step '{self.id}': 'with' names the parameters of command '{self.do}' "
                                     f"({', '.join(bad)} is not a parameter name): a step never overrides a plain key "
                                     "for a command")


class Comment(_Model):
    """A comment card of the pipeline editor (a sticky note on the graph of a phase or a sub-pipeline): free text
    for the people who read the pipeline (Markdown). Never run nor rendered. A card is free, or pinned to a step of
    its phase or sub-pipeline (``step``, a step id as written there), when it follows that step on the graph.
    ``x`` and ``y`` only say where the editor draws it (pixels): on the graph for a free card, from the step for a
    pinned one; a card without them is placed by the editor. ``width`` is its width."""
    text: str = ""
    step: str | None = None
    x: float | None = None
    y: float | None = None
    width: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _plain(cls, data):
        return {"text": data} if isinstance(data, str) else data      # a card given as its text only


def comment_problems(pipeline: Pipeline) -> list[str]:
    """Comment cards pinned to a step their phase or sub-pipeline does not have: shown free, said as a warning
    (a note never stops a run)."""
    problems = []
    holders = [(f"phase '{p.id}'", p.comments, p.steps + p.finally_) for p in pipeline.phases]
    holders += [(f"sub-pipeline '{n}'", s.comments, s.steps) for n, s in pipeline.subpipelines.items()]
    for where, comments, steps in holders:
        ids = {s.id for s in steps}
        for i, c in enumerate(comments, 1):
            if c.step and c.step not in ids:
                problems.append(f"{where}: comment {i} is pinned to unknown step '{c.step}' (it is shown free)")
    return problems


class SubPipeline(_Model):
    params: dict[str, Any] = Field(default_factory=dict)
    steps: list[Step]
    comments: list[Comment] = Field(default_factory=list)


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
    comments: list[Comment] = Field(default_factory=list)      # comment cards: notes, never run

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
    slot_class: str                              # its slot class (from the setup's 'slot_classes')
    name: str = ""
    stage_slot: str = ""                              # its stage slot, '' for an ad hoc slot
    role: str = ""                               # a type of the lab: binds to a lab component, which gives the commands
    tags: list[str] = field(default_factory=list)  # tags the lab component must have


@dataclass
class Needs:
    stage_slots: dict[str, str]                       # stage_slot -> lab stage type
    slots: dict[str, LogicalSlot]
    slot_classes: dict[str, dict]                  # slot classes (setup file)
    stage_slot_tags: dict[str, list[str]] = field(default_factory=dict)   # tags its lab stage must have

    def in_stage_slot(self, stage_slot: str) -> list[LogicalSlot]:
        return [c for c in self.slots.values() if c.stage_slot == stage_slot]


def check_sequencer_fields(setup: dict) -> None:
    """Former names, and the fields the role replaced (the lab role gives the commands)."""
    former = former_names(setup)
    if former:
        raise SequencerError("; ".join(former))
    for name, t in (setup.get("slot_classes") or {}).items():
        for key in ("extends", "commands"):
            if isinstance(t, dict) and key in t:
                raise SequencerError(f"setup slot class '{name}': '{key}' is gone, the slots' role gives "
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
        slots[slot_id] = LogicalSlot(id=slot_id, slot_class=slot.get("slot_class", ""), name=slot.get("name", ""),
                                     stage_slot=stage_slot, role=slot["role"],
                                     tags=tag_list(slot.get("tags"), f"setup configuration '{configuration}': slot '{slot_id}'"))
    return Needs(stage_slots=stage_slots, slots=slots, slot_classes=setup.get("slot_classes") or {},
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


def finally_order(steps: list[FlatStep]) -> list[FlatStep]:
    """The finally steps as they run: one without needs waits for the one written before it (in the order
    written), unless that one already waits for it, directly or not (it then runs first, with no needs)."""
    needs = {fs.id: list(fs.needs) for fs in steps}

    def waits_for(start: str, target: str) -> bool:
        seen, todo = set(), [start]
        while todo:
            current = todo.pop()
            if current == target:
                return True
            if current not in seen:
                seen.add(current)
                todo.extend(needs.get(current, []))
        return False

    ordered = []
    for i, fs in enumerate(steps):
        if not fs.needs and i and not waits_for(steps[i - 1].id, fs.id):
            needs[fs.id] = [steps[i - 1].id]
            fs = FlatStep(id=fs.id, step=fs.step, needs=[steps[i - 1].id], params=fs.params, group=fs.group)
        ordered.append(fs)
    return ordered


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


def redo_problems(fs: FlatStep, flat: FlatPhase, phase_id: str) -> None:
    """The steps a manual operation can be redone from: earlier steps of its phase (not finally) that it needs,
    directly or not."""
    if fs in flat.finally_:
        raise SequencerError(f"phase '{phase_id}': finally step '{fs.id}': a finally step cannot be redone from earlier steps")
    by_id = {s.id: s for s in flat.steps}
    ancestors: set[str] = set()
    todo = list(fs.needs)
    while todo:
        current = todo.pop()
        if current in by_id and current not in ancestors:
            ancestors.add(current)
            todo += by_id[current].needs
    namespace = fs.id.rpartition(".")[0]
    for target in fs.step.manual.get("redo_from") or []:
        found = target if target in by_id else f"{namespace}.{target}" if namespace else target
        if found not in ancestors:
            raise SequencerError(f"phase '{phase_id}': step '{fs.id}': redo_from '{target}' is not an earlier step it "
                                 "needs, directly or not, in its phase")


def exploit_data_problems(fs: FlatStep, phase: Phase, phase_ids: list[str], needs: Needs) -> None:
    """The data an exploit step reads: the phase and slot of each entry exist (templates are checked at run time)."""
    from testsuit.sequencer.exploitstep import as_list, parse_data
    for entry in as_list(fs.step.exploit.get("data")):
        if "{{" in str(entry):
            continue
        source_phase, slot, _ = parse_data(str(entry))
        if source_phase and source_phase not in phase_ids:
            raise SequencerError(f"phase '{phase.id}': step '{fs.id}': exploit data '{entry}' names unknown phase "
                                 f"'{source_phase}'")
        if slot != "*" and slot != "_sequencer" and slot not in needs.slots:
            raise SequencerError(f"phase '{phase.id}': step '{fs.id}': exploit data '{entry}' names unknown slot "
                                 f"'{slot}' (setup slots: {', '.join(needs.slots) or 'none'})")


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
            if fs.step.exploit is not None:
                exploit_data_problems(fs, phase, phase_ids, needs)
            if fs.step.manual is not None and fs.step.manual.get("redo_from"):
                redo_problems(fs, flat, phase.id)
            outside = [t for t in targets if t not in held]
            if outside:
                raise SequencerError(f"phase '{phase.id}': step '{fs.id}' targets slot(s) {outside} "
                                     f"outside the phase (it holds: {', '.join(held) or 'none'})")
        flats.append(flat)
    return flats
