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

"""Schemas of the datapack files: test definition and setup.

Strings of these files may contain Jinja2 expressions (see templating); the models validate the
files once rendered. Unknown fields are errors, to catch typos. Fields marked "for the test
sequencer" are kept in the datapack as written, for the sequencer to use at run time.

Test definition::

    id: toytrain_max_speed
    name: Max speed
    setup_file: ../../setup/toytrain_setup.yml
    setup_config: sim_bench
    dictionaries: [scenario.yml]          # relative to this file
    pipeline: ...                         # for the test sequencer, not rendered

Setup::

    id: toytrain_setup
    dictionaries: [globals.yml]           # relative to this file
    datarepositories:
      - id: toytrain
        path: "{{ setup.path }}/../../.work/datarepo"   # where datasets are imported
        datasources:
          - id: software
            importer: toytrain-release
            source: "{{ setup.path }}/../release_store/toytrain-sw"
            datasets:
              - id: toytrain_sw
                version: "{{ toytrain_sw_version }}"
                postprocess: [finalize]
    slot_types:
      pilot_app:                          # what a slot gets as inputs (its commands: its lab role)
        configurations:
          standard:
            inputs:
              - source: templates/pilot.yml               # rendered with Jinja2 (method: template)
                target: pilot.yml
              - source: "{{ toytrain_sw.path }}/pilot/"
                target: app/
                method: copy
              - source: templates/profile.yml             # a registered generator, or a script of the setup folder
                target: profile.bin
                method: generate
                generator: toytrain-profile-compiler
                options: {step_mps: 0.05}
    configurations:
      sim_bench:
        stage_slots:                           # for the test sequencer: slots bound together in one lab stage
          train: {type: toytrain}         # of that stage type (tsq_stage_type)
        slots:                            # each bound to a lab component at run time
          pilot: {name: CALC-Pilot, type: pilot_app, configuration: standard, stage_slot: train, role: pilot}
                                          # role: for the test sequencer, a lab role (binding and commands)
          telemetry:
            type: telemetry_app
            configuration: standard
            needs: [pilot]                # generated after pilot: read_input('pilot/pilot.yml'), slots.pilot.input_dir

Names: a datapack has *slots* (of *slot types*), some grouped in *stage slots*; at run time each slot is bound
to a lab *component*, and each stage slot to a lab *stage*. The former names (``component_types``, a
configuration's ``components``, ``stages`` and ``realms``, a slot's ``stage`` and ``realm``) are refused, naming
the current one.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# former names, refused with the current one (no aliases: README section 8)
FORMER_SETUP = {"component_types": "slot_types"}                                          # setup, top level
FORMER_CONFIG = {"components": "slots", "stages": "stage_slots", "realms": "stage_slots"}  # a setup configuration
FORMER_SLOT = {"stage": "stage_slot", "realm": "stage_slot"}                             # a slot of a setup configuration
FORMER_STEP = {"component": "slot"}                          # a pipeline step
FORMER_PHASE = {"components": "slots", "stages": "stage_slots", "realms": "stage_slots"}   # a pipeline phase


def former_names(setup: Any) -> list[str]:
    """The former names a setup still uses, each with its current name."""
    if not isinstance(setup, dict):
        return []
    found = [f"setup: '{old}' is now '{new}'" for old, new in FORMER_SETUP.items() if old in setup]
    for name, config in (setup.get("configurations") or {}).items():
        if isinstance(config, dict):
            found += [f"configuration '{name}': '{old}' is now '{new}'" for old, new in FORMER_CONFIG.items() if old in config]
            for slot_id, slot in (config.get("slots") or {}).items():
                if isinstance(slot, dict):
                    found += [f"configuration '{name}', slot '{slot_id}': '{old}' is now '{new}'"
                              for old, new in FORMER_SLOT.items() if old in slot]
    return found


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Testdef(_Model):
    __test__ = False  # not a pytest test class

    id: str
    name: str
    description: str = ""
    setup_file: str
    setup_config: str
    dictionaries: list[str] = Field(default_factory=list)
    pipeline: Any = None


class Dataset(_Model):
    id: str
    version: str
    postprocess: list[str] = Field(default_factory=list)


class Datasource(_Model):
    id: str
    name: str = ""
    importer: str
    source: str
    datasets: list[Dataset] = Field(default_factory=list)


class DataRepository(_Model):
    id: str
    name: str = ""
    path: str
    datasources: list[Datasource] = Field(default_factory=list)


class InputFile(_Model):
    # a file, a folder, or a glob pattern
    source: str
    # file (or folder) name in the component 'input' folder
    target: str
    method: Literal["template", "copy", "generate"] = "template"
    optional: bool = False
    postprocess: list[str] = Field(default_factory=list)
    # method generate: registered generator, or script relative to the setup folder, and its options
    generator: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _generator_given(self):
        if self.method == "generate" and not self.generator:
            raise ValueError(f"input '{self.source}': method 'generate' needs a 'generator'")
        return self


class SlotConfiguration(_Model):
    inputs: list[InputFile] = Field(default_factory=list)


GONE = "is gone: the slot's lab role gives its commands, implemented by the lab components (test-specific commands: pipeline steps)"


class SlotType(_Model):
    configurations: dict[str, SlotConfiguration]
    description: str = ""                    # what the type is (shown in the Editor)
    role: str = ""                           # the lab role of its slots: the inputs it expects (Editor)

    @model_validator(mode="before")
    @classmethod
    def _gone(cls, data):
        for key in ("extends", "commands"):
            if isinstance(data, dict) and key in data:
                raise ValueError(f"'{key}' {GONE}")
        return data


class Slot(_Model):
    """A slot of a setup configuration: bound at run time to a lab component of its role."""
    name: str = ""
    type: str
    configuration: str
    # other slots whose generated inputs this one reads (generated first)
    needs: list[str] = Field(default_factory=list)
    # for the test sequencer: stage_slot and role it is bound by (the lab role gives its commands)
    stage_slot: str = ""
    role: str = ""
    # for the test sequencer: tags the lab component must have (its id is one of its tags)
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _gone(cls, data):
        if isinstance(data, dict) and "commands" in data:
            raise ValueError(f"'commands' {GONE}")
        return data


class SetupConfiguration(_Model):
    slots: dict[str, Slot]
    # for the test sequencer: stage_slots (slots bound in one lab stage of a type, with the tags it must have), by id
    stage_slots: dict[str, Any] = Field(default_factory=dict)


class Setup(_Model):
    id: str
    name: str = ""
    dictionaries: list[str] = Field(default_factory=list)
    datarepositories: list[DataRepository] = Field(default_factory=list)
    slot_types: dict[str, SlotType] = Field(default_factory=dict)
    configurations: dict[str, SetupConfiguration]
