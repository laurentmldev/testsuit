"""Schemas of the datapack files: test definition and setup.

Strings of these files may contain Jinja2 expressions (see templating); the models validate the
files once rendered. Unknown fields are errors, to catch typos.

Test definition::

    id: acme_acceptance
    name: ACME bench acceptance
    setup_file: ../setup/acme_setup.yml
    setup_config: nominal
    dictionaries: [versions.yml]          # relative to this file
    pipeline: ...                         # for the test sequencer, kept as is in the datapack

Setup::

    id: acme_bench
    dictionaries: [globals.yml]           # relative to this file
    datarepositories:
      - id: acme_data
        path: "{{ testdef.path }}/../.work/datarepo"     # where datasets are imported
        datasources:
          - id: calib_store
            importer: acme-archive
            source: "{{ testdef.path }}/../archive_store"
            datasets:
              - id: bench_calib
                version: "{{ bench_calib_version }}"
                postprocess: [finalize]
    component_types:
      bench_controller:
        configurations:
          standard:
            inputs:
              - source: templates/bench_config.yml       # rendered with Jinja2 (method: template)
                target: bench_config.yml
              - source: "{{ bench_calib.path }}/calibration.yml"
                target: calibration.yml
                method: copy
        commands: ...                     # for the test sequencer, kept as is in the datapack
    configurations:
      nominal:
        components:
          bench_a: {name: Bench A controller, type: bench_controller, configuration: standard}
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
    commands: dict[str, Any] = Field(default_factory=dict)


class Component(_Model):
    name: str = ""
    type: str
    configuration: str


class SetupConfiguration(_Model):
    components: dict[str, Component]


class Setup(_Model):
    id: str
    name: str = ""
    dictionaries: list[str] = Field(default_factory=list)
    datarepositories: list[DataRepository] = Field(default_factory=list)
    component_types: dict[str, ComponentType] = Field(default_factory=dict)
    configurations: dict[str, SetupConfiguration]
