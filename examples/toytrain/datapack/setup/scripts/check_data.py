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

"""Python step of the exploit phase (sequencer README, section 8.3): check that the telemetry the test run
collected is complete before exploit_runner judges it, so that a lost recording reads as a data problem,
not as a speed criterion failure.

    python: {script: "{{ slot.input_dir }}/check_data.py",
             args: ["{{ slot.input_dir }}/data", "{{ slot.output_dir }}"]}

`keys` holds the datapack keys (the sequencer gives them to every python step). Standard library only, so
it runs with any Python 3 on the target. Exit 0: the data is complete; 1: it is not (the step fails).
"""
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

data, out = Path(sys.argv[1]), Path(sys.argv[2])
expected = float(keys["scenario"]["duration_s"])      # noqa: F821 (given by the sequencer)
problems = []

telemetry = data / "telemetry.csv"
rows = []
if telemetry.is_file():
    with open(telemetry, newline="") as f:
        lines = [line for line in f if not line.startswith("#")]
    rows = list(csv.DictReader(lines, delimiter=";"))
else:
    problems.append("telemetry.csv was not collected")

times = [datetime.fromisoformat(r["Time"].replace("Z", "+00:00")) for r in rows if r.get("Time")]
span = (max(times) - min(times)).total_seconds() if len(times) > 1 else 0.0
if telemetry.is_file() and span < 0.9 * expected:
    problems.append(f"telemetry covers {span:.1f} s of the {expected:g} s scenario")
for name in ("propulsion.propj", "piezo.csv"):
    if not (data / name).is_file():
        problems.append(f"{name} was not collected")

report = {"rows": len(rows), "span_s": round(span, 3), "expected_s": expected, "problems": problems}
out.mkdir(parents=True, exist_ok=True)
(out / "data_check.json").write_text(json.dumps(report, indent=1) + "\n")
print(json.dumps(report))
sys.exit(1 if problems else 0)
