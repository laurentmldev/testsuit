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
    component_types:
      pilot_app:
        extends: arduino_unoq             # for the test sequencer: the lab type it derives from
        configurations:
          standard:
            inputs:
              - source: templates/pilot.yml               # rendered with Jinja2 (method: template)
                target: pilot.yml
              - source: "{{ toytrain_sw.path }}/pilot/"
                target: app/
                method: copy
        commands: ...                     # for the test sequencer, not rendered
    configurations:
      sim_bench:
        stages:                           # for the test sequencer: logical stages needed
          train: {type: toytrain}
        components:
          pilot: {name: CALC-Pilot, type: pilot_app, configuration: standard, stage: train, role: pilot}
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    method: Literal["template", "copy"] = "template"
    optional: bool = False
    postprocess: list[str] = Field(default_factory=list)


class ComponentConfiguration(_Model):
    inputs: list[InputFile] = Field(default_factory=list)


class ComponentType(_Model):
    configurations: dict[str, ComponentConfiguration]
    # for the test sequencer
    extends: str = ""
    commands: dict[str, Any] = Field(default_factory=dict)


class Component(_Model):
    name: str = ""
    type: str
    configuration: str
    # for the test sequencer: logical stage and role it is bound by
    stage: str = ""
    role: str = ""


class SetupConfiguration(_Model):
    components: dict[str, Component]
    # for the test sequencer: logical stages needed, by id
    stages: dict[str, Any] = Field(default_factory=dict)


class Setup(_Model):
    id: str
    name: str = ""
    dictionaries: list[str] = Field(default_factory=list)
    datarepositories: list[DataRepository] = Field(default_factory=list)
    component_types: dict[str, ComponentType] = Field(default_factory=dict)
    configurations: dict[str, SetupConfiguration]
