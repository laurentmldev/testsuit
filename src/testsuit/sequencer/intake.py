"""Intake (README section 10): verify a datapack zip, unpack it, read what it needs and how to run it.

Everything is read from the zip only: the pipeline from ``definition/testdef.yml``, the resources
from ``definition/setup.yml``, the frozen keys from ``context.yml``, the baseline from ``baseline.yml``.
"""
from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from testsuit.datatools.datapack.datapack_tools import verify_datapack
from testsuit.sequencer.lab import Lab
from testsuit.sequencer.model import FlatPhase, Needs, Pipeline, load_pipeline, needs_from_setup, validate_pipeline
from testsuit.sequencer.util import SequencerError, load_yaml


@dataclass
class Datapack:
    dir: Path                                   # unpacked bag folder
    name: str
    manifest: dict                              # datapack.yml
    context: dict                               # context.yml: every key, resolved
    testdef: dict                               # definition/testdef.yml, as written
    setup: dict                                 # definition/setup.yml, as written
    configuration: str
    pipeline: Pipeline
    needs: Needs
    phases: list[FlatPhase]
    baseline: dict | None = None
    expected_baseline: dict | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def testdef_id(self) -> str:
        return str((self.manifest.get("testdef") or {}).get("id") or self.testdef.get("id") or self.name)

    @property
    def conform(self) -> bool:
        if self.baseline is not None and "datapack" in self.baseline:
            return bool(self.baseline["datapack"].get("conform", False))
        return not self.manifest.get("nocheck", True)


def unpack(zip_path: Path, target: Path) -> Path:
    """Verify the datapack zip and unpack it into target; return the bag folder."""
    if not zipfile.is_zipfile(zip_path):
        raise SequencerError(f"'{zip_path}' is not a zip file")
    problems = verify_datapack(zip_path)
    if problems:
        raise SequencerError("datapack checksums do not match:\n  " + "\n  ".join(problems))
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        root = archive.namelist()[0].split("/", 1)[0]
        for member in archive.infolist():
            destination = (target / member.filename).resolve()
            if not destination.is_relative_to(target.resolve()):
                raise SequencerError(f"datapack member outside the bag: '{member.filename}'")
        archive.extractall(target)
    for path in (target / root).rglob("*"):           # the datapack tool makes dictionaries read-only
        if path.is_file():
            path.chmod(0o644)
    return target / root


def load_datapack(bag: Path) -> Datapack:
    """Read an unpacked datapack and validate its pipeline against its own resources."""
    def read(name: str, required: bool = True):
        path = bag / name
        if not path.is_file():
            if required:
                raise SequencerError(f"datapack '{bag.name}': missing {name}")
            return None
        return load_yaml(path)

    manifest = read("datapack.yml") or {}
    context = read("context.yml") or {}
    testdef = read("definition/testdef.yml") or {}
    setup = read("definition/setup.yml") or {}
    configuration = str((manifest.get("setup") or {}).get("configuration") or testdef.get("setup_config") or "")
    pipeline = load_pipeline(testdef.get("pipeline"), bag / "definition")
    needs = needs_from_setup(setup, configuration)
    phases = validate_pipeline(pipeline, needs)
    expected = None
    if pipeline.baseline:
        path = bag / "definition" / pipeline.baseline
        if not path.is_file():
            path = bag / "definition" / Path(pipeline.baseline).name
        if path.is_file():
            expected = load_yaml(path)
    return Datapack(dir=bag, name=str(manifest.get("name") or bag.name), manifest=manifest, context=context,
                    testdef=testdef, setup=setup, configuration=configuration, pipeline=pipeline, needs=needs,
                    phases=phases, baseline=read("baseline.yml", required=False), expected_baseline=expected)


def check_against_lab(datapack: Datapack, lab: Lab) -> list[str]:
    """What the lab cannot provide: stage types, roles, component types, commands used by the pipeline."""
    problems = []
    inv = lab.inventory
    for logical_stage, stage_type in datapack.needs.stages.items():
        stages = inv.stages_of_type(stage_type)
        if not stages:
            problems.append(f"no stage of type '{stage_type}' in the lab (logical stage '{logical_stage}')")
            continue
        for comp in datapack.needs.in_stage(logical_stage):
            if not any(inv.component_in(s, comp.role) for s in stages):
                problems.append(f"component '{comp.id}': no component with role '{comp.role}' in the "
                                f"'{stage_type}' stages ({', '.join(s.id for s in stages)})")
    for comp in datapack.needs.components.values():
        if not comp.stage and not [c for c in inv.adhoc if c.role == comp.role]:
            problems.append(f"component '{comp.id}': no ad hoc component with role '{comp.role}' in the lab")
        if comp.type and comp.type not in datapack.needs.component_types:
            problems.append(f"component '{comp.id}': type '{comp.type}' not in the setup's component types")
            continue
        base = comp.type
        seen = set()
        while base in datapack.needs.component_types and base not in seen:
            seen.add(base)
            base = (datapack.needs.component_types[base] or {}).get("extends") or ""
        if base and base not in lab.types.types:
            problems.append(f"component '{comp.id}': its type extends '{base}', unknown in the lab type library")
    return problems
