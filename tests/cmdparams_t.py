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

"""Every tracked source file carries the AGPL license notice (CONTRIBUTING.md, "License notice")."""
"""Parameters of lab commands (roles declare them, classes give defaults, do: steps set them with with:) and the
keys exploit steps give their criteria (README sections 7.1, 8.3)."""
import pytest

from testsuit.sequencer.cmdparams import (command_params, criteria_missing_keys, declared_params, exploit_keys,
                                          exploit_with_problems, params_read, step_param_problems)
from testsuit.sequencer.library import ComponentDef, Library, RoleDef
from testsuit.sequencer.model import Step
from testsuit.sequencer.util import SequencerError

ROLES = {
    "builder": {"extends": "linux_system", "commands": {
        "compile": {"description": "Builds one product.", "params": {"product": "The product to build",
                                                                     "flags": "Compiler flags"}},
        "clean": "Cleans the build folder."}},
    "fast_builder": {"extends": "builder", "commands": {
        "compile": {"params": {"jobs": "Parallel jobs"}}}},
}


def library(classes: dict) -> Library:
    roles = Library.load([]).roles
    roles.update({n: RoleDef.from_dict(n, d, "roles.yml") for n, d in ROLES.items()})
    components = Library.load([]).components
    components.update({n: ComponentDef.from_dict(n, d, "classes.yml") for n, d in classes.items()})
    return Library(roles, components)


def test_role_declares_params():
    lib = library({})
    assert lib.command_params("builder") == {"compile": {"product": "The product to build", "flags": "Compiler flags"},
                                             "clean": {}, "deploy": {}, "collect": {}, "kill": {}}
    # a sub-role adds parameters to a command it inherits
    assert lib.command_params("fast_builder")["compile"] == {"product": "The product to build", "flags": "Compiler flags",
                                                             "jobs": "Parallel jobs"}
    assert lib.role_commands("fast_builder")["compile"]["description"] == "Builds one product."
    assert declared_params(["a", "b"], "x") == {"a": "", "b": ""}
    with pytest.raises(SequencerError, match="default value goes in the component classes"):
        declared_params({"product": {"default": "x"}}, "x")
    with pytest.raises(SequencerError, match="plain name"):
        declared_params({"a.b": ""}, "x")
    with pytest.raises(SequencerError, match="unknown keys"):
        RoleDef.from_dict("r", {"commands": {"c": {"run": "true"}}}, "roles.yml")


def test_class_gives_defaults_and_reads_declared_params_only():
    good = {"gcc": {"extends": "linux_system", "roles": ["builder"], "commands": {
        "compile": {"run": "make {{ params.product }} CFLAGS={{ params.flags }}", "params": {"flags": "-O2"}},
        "clean": {"run": "make clean"}}}}
    lib = library(good)
    assert lib.check_component("gcc") == {"builder": []}
    bad = {"gcc": {**good["gcc"], "commands": {**good["gcc"]["commands"],
                                                "compile": {"run": "make {{ params.target }}", "params": {"flags": "-O2"}}}}}
    with pytest.raises(SequencerError, match="parameter\\(s\\) target that its role does not declare"):
        library(bad)
    unknown = {"gcc": {**good["gcc"], "commands": {**good["gcc"]["commands"],
                                                    "compile": {"run": "make", "params": {"opt": "-O2"}}}}}
    with pytest.raises(SequencerError, match="opt"):
        library(unknown)
    with pytest.raises(SequencerError, match="default value of each parameter"):
        ComponentDef.from_dict("x", {"commands": {"c": {"run": "true", "params": ["a"]}}}, "classes.yml")


def test_params_read():
    assert params_read({"run": "make {{ params.product }} {{ params['flags'] }}", "env": {"J": "{{ params.jobs | default(1) }}"}}) \
        == ["product", "flags", "jobs"]
    assert params_read("{{ product }}") == []


def test_step_params():
    declared = {"product": "", "flags": ""}
    assert step_param_problems("s", "compile", {"product": "a"}, declared, {"flags": "-O2"}) == []
    [unknown] = step_param_problems("s", "compile", {"product": "a", "speed": 1}, declared)
    assert "has no parameter 'speed'" in unknown
    [missing] = step_param_problems("s", "compile", {}, declared, {"flags": "-O2"})
    assert "needs a value for 'product'" in missing
    # values are templates, rendered with the step's keys (run-time keys too); with: wins over the default
    keys = {"slot": {"id": "builder"}, "params": {"from_group": "g"}}
    assert command_params("s", "compile", {"product": "{{ slot.id }}-{{ params.from_group }}"}, declared,
                          {"flags": "-O2", "product": "x"}, keys) == {"product": "builder-g", "flags": "-O2"}
    with pytest.raises(SequencerError, match="needs a value"):
        command_params("s", "compile", {"flags": "1"}, declared, {}, keys)


def test_with_checked_on_the_step():
    Step(id="a", slot="x", do="compile", **{"with": {"product": "p"}}).check()
    Step(id="e", exploit={"criteria": "c", "data": "dut/out.csv"}, **{"with": {"params.limit": 1}}).check()
    with pytest.raises(SequencerError, match="not of a 'run' step"):
        Step(id="r", run="true", **{"with": {"a": 1}}).check()
    with pytest.raises(SequencerError, match="never overrides a plain key"):
        Step(id="a", slot="x", do="compile", **{"with": {"testdef.speed": 1}}).check()


def test_exploit_keys():
    context = {"min_count": 3, "testdef": {"id": "t"}}
    errors, warnings = exploit_with_problems("s", {"params.limit": 1, "extra": 2}, context)
    assert errors == [] and len(warnings) == 1 and "'extra' is not a 'params.*' key" in warnings[0]
    errors, _ = exploit_with_problems("s", {"min_count": 1, "testdef.id": "x"}, context)
    assert len(errors) == 2 and "does not override it" in errors[0]
    keys = exploit_keys("s", {"params.limit": "{{ min_count * 2 }}", "extra.deep": 1}, {**context, "params": {"g": 1}})
    assert keys["params"] == {"g": 1, "limit": 6} and keys["extra"] == {"deep": 1} and keys["min_count"] == 3
    with pytest.raises(SequencerError, match="does not override"):
        exploit_keys("s", {"min_count": 1}, context)


def test_criteria_missing_keys(tmp_path):
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "a.mexploit.yml").write_text("x:\n  max: {{ params.limit }}\n  min: {{ min_count }}\n")
    assert criteria_missing_keys(tmp_path / "c", {"min_count": 1}) == [("a.mexploit.yml", "params.limit")]
    assert criteria_missing_keys(tmp_path / "c", {"min_count": 1, "params": {"limit": 2}}) == []
