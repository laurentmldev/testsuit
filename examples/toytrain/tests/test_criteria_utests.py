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

"""The unit tests of the toy-train scenarios (criteria_utests/ of each test definition), run with
mxp utest: every case gives what it expects, and every check that can fail is covered. Also checks
the scenarios with mxp lint, on the sample recordings."""
from pathlib import Path

import pytest

from testsuit.exploit.mexploit.lint import lint
from testsuit.exploit.mexploit.utest import COVERED, run_utests

TESTDEFS = Path("datapack/testdef")


@pytest.mark.parametrize("utests", sorted(str(p) for p in TESTDEFS.glob("*/criteria_utests/*/utests.yml")))
def test_scenario_utests(utests, tmp_path):
    report = run_utests(utests, tmp_path)
    assert report.ok, {c: r.mismatches() for c, r in report.cases.items()}
    assert set(report.coverage().values()) == {COVERED}, report.matrix_text()


def test_there_are_utests():
    assert list(TESTDEFS.glob("max_speed/criteria_utests/*/utests.yml"))


@pytest.mark.parametrize("testdef", ["max_speed", "front_contact"])
def test_scenarios_lint_clean(testdef):
    problems = lint(TESTDEFS / testdef / "criteria", dictionaries=[TESTDEFS / testdef / "scenario.yml"],
                    data_folders=[Path("sample/data") / testdef])
    assert problems == [], [str(p) for p in problems]
