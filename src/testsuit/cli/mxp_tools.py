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

"""Sub-commands of ``mxp`` helping to write scenarios: ``mxp describe``, ``mxp lint`` and ``mxp utest``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

SUBCOMMANDS = ("describe", "lint", "utest")


def _parse_set(values: list[str]) -> dict:
    """'key=value' arguments (value read as YAML, as datapack --set does), as a dict."""
    keys = {}
    for item in values or ():
        if "=" not in item:
            raise SystemExit(f"--set expects key=value, got '{item}'")
        key, value = item.split("=", 1)
        keys[key.strip()] = yaml.safe_load(value) if value.strip() else ""
    return keys


def describe_main(argv: list[str]) -> int:
    from testsuit.exploit.mexploit.describe import describe
    parser = argparse.ArgumentParser(prog="mxp describe", description=
        "Describe what scenarios can use, as JSON: the criteria (built-in, from plugins, from scenario "
        "libraries) with the JSON Schema of their entries, the helpers of expressions, and the JSON Schema "
        "of a scenario file.")
    parser.add_argument("--critlib", action="append", default=[], help="a library of criteria (Python file) to load too")
    parser.add_argument("--scenario", help="a scenario folder or file: the libraries it declares are loaded too")
    parser.add_argument("--schema", action="store_true", help="print only the JSON Schema of a scenario file "
                        "(for an editor's YAML extension)")
    parser.add_argument("-o", "--output", help="write to this file rather than to the standard output")
    args = parser.parse_args(argv)
    info = describe(args.critlib, args.scenario)
    text = json.dumps(info["scenario_schema"] if args.schema else info, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


def lint_main(argv: list[str]) -> int:
    from testsuit.exploit.mexploit.lint import has_errors, lint
    parser = argparse.ArgumentParser(prog="mxp lint", description=
        "Check scenario files without running them: YAML once rendered with the keys, fields of each "
        "criterion, expressions (names, computed parameters, helper calls), and the parameters named "
        "literally against a data folder. Returns 1 when there is an error, 0 otherwise (warnings only).")
    parser.add_argument("scenario", help="scenario folder or file")
    parser.add_argument("-k", "--dictionary", action="append", default=[], help="dictionary of datapack keys to render the files with")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="a key set by hand (value read as YAML)")
    parser.add_argument("-d", "--data", action="append", default=[], help="data folder whose parameters the names are checked against")
    parser.add_argument("--critlib", action="append", default=[], help="a library of criteria (Python file) to load too")
    parser.add_argument("--json", action="store_true", help="print the problems as JSON")
    args = parser.parse_args(argv)
    problems = lint(args.scenario, args.dictionary, _parse_set(args.set), args.data, args.critlib)
    if args.json:
        print(json.dumps([p.as_dict() for p in problems], indent=2))
    else:
        for p in problems:
            print(p)
        nbErrors = sum(p.severity == "error" for p in problems)
        print(f"{nbErrors} error(s), {len(problems) - nbErrors} warning(s)", file=sys.stderr)
    return 1 if has_errors(problems) else 0


def utest_main(argv: list[str]) -> int:
    from testsuit.exploit.mexploit.utest import UtestError, run_utests
    parser = argparse.ArgumentParser(prog="mxp utest", description=
        "Run the unit tests of scenarios: each case of a utests.yml file runs the scenario on its data, and "
        "each check must give the status expected. Prints the checks x cases matrix and the coverage. "
        "Returns 1 when an expectation is not met (or, with --require-coverage, a check is not covered).")
    parser.add_argument("utests", nargs="+", help="utests.yml file(s)")
    parser.add_argument("-o", "--output", default="./mxp_utest_result", help="where the cases' results are written "
                        "(default ./mxp_utest_result)")
    parser.add_argument("-c", "--case", action="append", default=[], help="run only this case (repeatable)")
    parser.add_argument("--require-coverage", action="store_true", help="also fail unless every check that can "
                        "fail is covered: expected and seen both succeeding and failing")
    parser.add_argument("--json", help="also write the results as JSON in this file")
    args = parser.parse_args(argv)
    rc, reports = 0, []
    for path in args.utests:
        try:
            output = Path(args.output) / Path(path).resolve().parent.name if len(args.utests) > 1 else Path(args.output)
            report = run_utests(path, output, args.case or None)
        except UtestError as e:
            print(f"{path}: {e}", file=sys.stderr)
            rc = 1
            continue
        reports.append(report)
        print(f"\n{path}  ({report.utests.scenario})")
        print(report.matrix_text())
        for case in report.cases.values():
            for mismatch in case.mismatches():
                print(f"  [{case.case}] {mismatch}")
        verdict = "OK" if report.ok else "FAILED"
        coverage = "every check covered" if report.fully_covered else "some checks not covered"
        print(f"{verdict}: {sum(c.ok for c in report.cases.values())}/{len(report.cases)} case(s) as expected, {coverage}")
        if not report.ok or (args.require_coverage and not report.fully_covered):
            rc = 1
    if args.json:
        Path(args.json).write_text(json.dumps([r.as_dict() for r in reports], indent=2) + "\n", encoding="utf-8")
    return rc


def main(argv: list[str]) -> int:
    """Run the sub-command named by argv[0]."""
    return {"describe": describe_main, "lint": lint_main, "utest": utest_main}[argv[0]](argv[1:])
