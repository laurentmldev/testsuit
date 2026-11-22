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

"""Test campaigns: a list of test definitions generated and run together.

A campaign file (``<name>.campaign.yml``, in the tests repository)::

    id: toytrain_nightly
    name: Toy train nightly
    description: Markdown text
    priority: 60                          # default priority of its runs (else the sequencer's default)
    testdefs:                             # relative to this file
      - testdef/max_speed/testdef.yml
      - {testdef: testdef/front_contact/testdef.yml, setup_config: emulated, priority: 70}
    routing:                              # tags the lab resources of its runs must carry, over the slots' own
      stage_tags: [bench_2]               # every stage slot; or {<stage type>: [tags]}
      component_tags: {pilot: [hw_rev_b]} # the slots of that role
    datasets:                             # the version of every dataset its test definitions import
      toytrain_sw: v0.3.0
      toytrain_sim: v1.1.0
    baseline: baseline_expected.yml       # expected baseline checked on every run (instead of the test definitions')

The campaign is not templated: what it pins is written as is. Every dataset a test definition of the campaign
imports MUST be listed under ``datasets``: the campaign's version replaces the one the setup writes, for every test
definition, so a dataset has one version in the whole campaign. Each datapack records the pinned versions
(``versions_pinned_by_campaign``) and the git state of the campaign file (a source of role ``campaign``): a datapack
stays conform when the campaign file is a clean git tag and each pinned version that differs from the written one is
a tag of its source.

Generating a campaign (``datapack <file>.campaign.yml``) builds every datapack into one folder, with a
``campaign.yml`` manifest that ``tsq submit-campaign`` reads: all of them, or none when one fails.
"""
from __future__ import annotations

import getpass
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator, model_validator

from testsuit.datatools.datapack.model import _Model

CAMPAIGN_SUFFIXES = (".campaign.yml", ".campaign.yaml")
MANIFEST = "campaign.yml"
MANIFEST_FORMAT = 1


class CampaignEntry(_Model):
    """One test definition of a campaign."""
    testdef: str
    setup_config: str | None = None
    priority: int | None = Field(default=None, ge=0, le=100)


class Routing(_Model):
    """Tags added to those of the slots of every run of the campaign (sequencer SPEC, section 8.1)."""
    stage_tags: list[str] | dict[str, list[str]] = Field(default_factory=list)
    component_tags: dict[str, list[str]] = Field(default_factory=dict)

    def empty(self) -> bool:
        return not self.stage_tags and not self.component_tags


class Campaign(_Model):
    id: str
    name: str = ""
    description: str = ""
    priority: int | None = Field(default=None, ge=0, le=100)
    testdefs: list[CampaignEntry]
    routing: Routing = Field(default_factory=Routing)
    datasets: dict[str, str] = Field(default_factory=dict)
    baseline: str | None = None

    @field_validator("testdefs", mode="before")
    @classmethod
    def _entries(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [{"testdef": v} if isinstance(v, str) else v for v in value]
        return value

    @field_validator("datasets", mode="before")
    @classmethod
    def _versions(cls, value: Any) -> Any:
        if isinstance(value, dict):                  # 1.2 or 3 in YAML: a version all the same
            return {str(k): ("" if v is None else str(v)).strip() for k, v in value.items()}
        return value

    @model_validator(mode="after")
    def _check(self):
        if not self.testdefs:
            raise ValueError("a campaign lists at least one test definition (testdefs)")
        seen = set()
        for entry in self.testdefs:
            key = (entry.testdef, entry.setup_config)
            if key in seen:
                raise ValueError(f"test definition '{entry.testdef}'"
                                 + (f" (configuration {entry.setup_config})" if entry.setup_config else "")
                                 + " listed twice")
            seen.add(key)
        empty = [k for k, v in self.datasets.items() if not v]
        if empty:
            raise ValueError(f"dataset(s) without a version: {', '.join(empty)}")
        return self


class CampaignError(Exception):
    """The campaign file is invalid, or its datapacks cannot be generated."""


def is_campaign_file(path: str | Path) -> bool:
    return str(path).endswith(CAMPAIGN_SUFFIXES)


def looks_like_campaign(data: Any) -> bool:
    """A YAML mapping that is a campaign (the Editor's scan of a workspace)."""
    return isinstance(data, dict) and isinstance(data.get("testdefs"), list) and "pipeline" not in data \
        and "setup_file" not in data


def load_campaign(path: str | Path) -> Campaign:
    """Read and validate a campaign file; its test definitions must exist."""
    from pydantic import ValidationError
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise CampaignError(f"campaign '{path}': {e}") from e
    if not isinstance(data, dict):
        raise CampaignError(f"campaign '{path}': not a YAML mapping")
    try:
        campaign = Campaign.model_validate(data)
    except ValidationError as e:
        raise CampaignError(f"invalid campaign '{path}':\n{e}") from e
    missing = [e.testdef for e in campaign.testdefs if not (path.parent / e.testdef).is_file()]
    if missing:
        raise CampaignError(f"campaign '{path}': test definition(s) not found: {', '.join(missing)} "
                            "(paths are relative to the campaign file)")
    if campaign.baseline and not (path.parent / campaign.baseline).is_file():
        raise CampaignError(f"campaign '{path}': expected baseline '{campaign.baseline}' not found")
    return campaign


def build_campaign(campaign_file: str | Path, nocheck: bool = False, output_dir: str | Path | None = None,
                   lenient: bool = False, manual_keys: dict | None = None) -> Path:
    """Generate the datapack of every test definition of a campaign, into one folder with a ``campaign.yml``
    manifest. The folder is ``<output_dir>/<campaign id>_<user>_<timestamp>[_DRAFT]``; without output_dir, next
    to where the first test definition writes its datapacks (``datapack.output_dir``).

    :return: the campaign folder
    :raises CampaignError: if the campaign is invalid or one of its datapacks cannot be generated (then the
        folder is removed: a campaign is generated whole or not at all)
    """
    from testsuit.datatools.datapack.datapack_tools import (DatapackError, _check_git_state, _info, _warn, datapack,
                                                            read_baseline)
    from testsuit.plugins import load_plugins
    load_plugins()
    campaign_file = Path(campaign_file).resolve()
    campaign = load_campaign(campaign_file)
    nocheck = nocheck or lenient
    try:
        git = _check_git_state("Campaign", campaign_file.parent, nocheck)
    except DatapackError as e:
        raise CampaignError(str(e)) from e
    now = datetime.now()
    name = f"{campaign.id}_{getpass.getuser()}_{now.strftime('%Y%m%d_%H%M%S')}" + ("_DRAFT" if nocheck else "")
    expected = None
    if campaign.baseline:
        expected = yaml.safe_load((campaign_file.parent / campaign.baseline).read_text(encoding="utf-8")) or {}
    info = {"id": campaign.id, "name": campaign.name, "file": campaign_file.as_posix(), "git": git,
            "datasets": dict(campaign.datasets)}

    folder: Path | None = Path(output_dir).expanduser().resolve() / name if output_dir else None
    entries, used = [], set()
    try:
        for number, entry in enumerate(campaign.testdefs, 1):
            testdef = (campaign_file.parent / entry.testdef).resolve()
            _info(f"\n===== campaign {campaign.id}: test definition {number}/{len(campaign.testdefs)}: {entry.testdef}"
                  + (f" (configuration {entry.setup_config})" if entry.setup_config else "") + " =====")
            zip_path = datapack(testdef, nocheck, folder, entry.setup_config, expected, lenient=lenient,
                                manual_keys=manual_keys, campaign=info)
            if folder is None:                       # the first one says where the campaign goes
                folder = zip_path.parent / name
                folder.mkdir(parents=True, exist_ok=True)
                zip_path = Path(shutil.move(str(zip_path), folder / zip_path.name))
            baseline = read_baseline(zip_path) or {}
            used |= {s.get("id") for s in baseline.get("sources") or [] if s.get("role") == "dataset"}
            entries.append({"testdef": entry.testdef, "setup_config": entry.setup_config,
                            "priority": entry.priority if entry.priority is not None else campaign.priority,
                            "zip": zip_path.name, "name": zip_path.stem,
                            "conform": bool((baseline.get("datapack") or {}).get("conform"))})
    except (DatapackError, CampaignError) as e:
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)
        raise CampaignError(f"campaign '{campaign.id}': {entry.testdef}: {e}") from e
    except BaseException:
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    assert folder is not None
    unused = sorted(set(campaign.datasets) - used)
    if unused:
        _warn(f"campaign '{campaign.id}': dataset(s) pinned but imported by no test definition: {', '.join(unused)}")
    conform = not nocheck and bool(git and git.get("clean") and git.get("tag")) and all(e["conform"] for e in entries)
    manifest = {
        "format": MANIFEST_FORMAT, "name": name, "id": campaign.id, "title": campaign.name or campaign.id,
        "description": campaign.description, "file": campaign_file.as_posix(), "git": git,
        "created": now.strftime("%Y-%m-%d %H:%M:%S"), "user": getpass.getuser(), "nocheck": nocheck,
        "conform": conform, "priority": campaign.priority, "routing": campaign.routing.model_dump(),
        "datasets": dict(campaign.datasets), "unused_datasets": unused,
        "expected_baseline": expected, "datapacks": entries,
    }
    with open(folder / MANIFEST, "w", encoding="utf-8") as f:
        f.write("# a generated campaign: its datapacks, submitted together by 'tsq submit-campaign'\n")
        yaml.safe_dump(manifest, f, sort_keys=False, allow_unicode=True)
    _info(f"\nGenerated campaign: {folder.as_posix()}\n")
    if not conform:
        _warn(f"campaign '{campaign.id}' is non-conform (see the warnings above)")
    return folder


def read_manifest(folder: str | Path) -> dict:
    """The manifest of a generated campaign folder, its datapacks checked to be there."""
    folder = Path(folder)
    path = folder / MANIFEST if folder.is_dir() else folder
    try:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise CampaignError(f"generated campaign '{folder}': {e}") from e
    if not isinstance(manifest, dict) or not isinstance(manifest.get("datapacks"), list):
        raise CampaignError(f"'{path}' is not the manifest of a generated campaign")
    missing = [d.get("zip") for d in manifest["datapacks"] if not (path.parent / str(d.get("zip"))).is_file()]
    if missing:
        raise CampaignError(f"generated campaign '{path.parent}': datapack(s) missing: {', '.join(missing)}")
    return manifest


def apply_routing(stage_slots: dict[str, str], stage_slot_tags: dict[str, list[str]], slots: dict, routing: dict | None) -> None:
    """Add the routing tags of a campaign to the needs of a datapack, in place: stage_slots (stage slot -> stage
    type), stage_slot_tags (stage slot -> tags), slots (slot id -> object with ``role`` and ``tags``)."""
    if not routing:
        return
    stage = routing.get("stage_tags") or []
    for stage_slot, stage_type in stage_slots.items():
        extra = stage if isinstance(stage, list) else (stage.get(stage_type) or [])
        tags = stage_slot_tags.setdefault(stage_slot, [])
        tags += [t for t in extra if t not in tags]
    for slot in slots.values():
        extra = (routing.get("component_tags") or {}).get(slot.role) or []
        slot.tags = list(slot.tags) + [t for t in extra if t not in slot.tags]
