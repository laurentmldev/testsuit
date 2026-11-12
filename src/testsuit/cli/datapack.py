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


import argparse
import sys

from colorama import Fore, Style

from testsuit.misc.files import expandPath
from testsuit.datatools.datapack.datapack_tools import DatapackError, datapack, verify_datapack


# override the parsing error message using logger
class _HelpParser(argparse.ArgumentParser):
    def error(self, message):
        print(Fore.RED+"ERROR: Input Arguments Error : "+message+Style.RESET_ALL)
        sys.exit(1)


## the main function
def main():
    parser = _HelpParser(description=
    """Generate the datapack of a test definition file: the input files of every component of its setup,
rendered with Jinja2 from YAML dictionaries, packaged as a zip (BagIt layout).

With --verify, check the checksums of an existing datapack zip instead.
With --check-baseline EXPECTED, check the baseline of an existing datapack zip against an expected baseline.

Return 1 if something went wrong, 0 otherwise
    """,
    formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('file',help="the test definition YAML file (or, with --verify, the datapack zip)")
    parser.add_argument('--nocheck',action='store_true',help="Ignore warnings and consistency checks (git tags etc.) for test definition and setup folders.")
    parser.add_argument('--output-dir','-o',help="where to write the datapack zip (default: key 'datapack.output_dir')")
    parser.add_argument('--setup-config','-c',help="setup configuration to use instead of the test definition's 'setup_config'")
    parser.add_argument('--lenient',action='store_true',help="Skip, with a warning, the inputs whose source is missing or empty, or whose target is empty (implies --nocheck).")
    parser.add_argument('--set',dest='set_keys',action='append',default=[],metavar='KEY=VALUE',help="set a key by hand, over the dictionaries (repeatable; nested: a.b=value; the value is read as YAML).\nSetting a key no dictionary defines keeps the datapack conform; overriding a defined key makes it non-conform.")
    parser.add_argument('--verify',action='store_true',help="check the checksums of the given datapack zip")
    parser.add_argument('--baseline',help="expected baseline YAML file: report how the new datapack differs from it")
    parser.add_argument('--check-baseline',metavar='EXPECTED',help="check the baseline of the given datapack zip against this expected baseline")
    args = parser.parse_args()

    if args.check_baseline:
        import yaml
        from testsuit.datatools.datapack.baseline import check_baseline, nonconform_reasons
        from testsuit.datatools.datapack.datapack_tools import read_baseline
        baseline = read_baseline(expandPath(args.file))
        if baseline is None:
            print(Fore.RED+"ERROR: the datapack has no baseline.yml"+Style.RESET_ALL)
            sys.exit(1)
        with open(expandPath(args.check_baseline), encoding="utf-8") as f:
            expected = yaml.safe_load(f) or {}
        problems = check_baseline(baseline, expected, parts=("datapack", "sources", "tools"))
        problems = nonconform_reasons(baseline) + problems
        for problem in problems:
            print(Fore.YELLOW+"NON-CONFORM: "+problem+Style.RESET_ALL)
        if not problems:
            print(Fore.LIGHTGREEN_EX+"datapack is conform to the expected baseline"+Style.RESET_ALL)
        sys.exit(1 if problems else 0)

    if args.verify:
        problems = verify_datapack(expandPath(args.file))
        for problem in problems:
            print(Fore.RED+"ERROR: "+problem+Style.RESET_ALL)
        if not problems:
            print(Fore.LIGHTGREEN_EX+"datapack is intact"+Style.RESET_ALL)
        sys.exit(1 if problems else 0)

    try:
        expected = None
        if args.baseline:
            import yaml
            with open(expandPath(args.baseline), encoding="utf-8") as f:
                expected = yaml.safe_load(f) or {}
        from testsuit.datatools.datapack.templating import TemplateError, parse_key_value
        try:
            manual_keys = dict(parse_key_value(item) for item in args.set_keys)
        except TemplateError as e:
            raise DatapackError(f"--set: {e}") from e
        datapack(expandPath(args.file), args.nocheck, args.output_dir, args.setup_config, expected, lenient=args.lenient,
                 manual_keys=manual_keys)
    except DatapackError as e:
        print(Fore.RED+"ERROR: "+str(e)+Style.RESET_ALL)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
