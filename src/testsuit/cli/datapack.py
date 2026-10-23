
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
    parser.add_argument('--verify',action='store_true',help="check the checksums of the given datapack zip")
    parser.add_argument('--baseline',help="expected baseline YAML file: report how the new datapack differs from it")
    parser.add_argument('--check-baseline',metavar='EXPECTED',help="check the baseline of the given datapack zip against this expected baseline")
    args = parser.parse_args()

    if args.check_baseline:
        import yaml
        from testsuit.datatools.datapack.baseline import check_baseline
        from testsuit.datatools.datapack.datapack_tools import read_baseline
        baseline = read_baseline(expandPath(args.file))
        if baseline is None:
            print(Fore.RED+"ERROR: the datapack has no baseline.yml"+Style.RESET_ALL)
            sys.exit(1)
        with open(expandPath(args.check_baseline), encoding="utf-8") as f:
            expected = yaml.safe_load(f) or {}
        problems = check_baseline(baseline, expected, parts=("datapack", "sources", "tools"))
        if not baseline.get("datapack", {}).get("conform"):
            problems.insert(0, "the datapack is not conform in itself (built with --nocheck, or from untagged or modified sources)")
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
        datapack(expandPath(args.file), args.nocheck, args.output_dir, args.setup_config, expected)
    except DatapackError as e:
        print(Fore.RED+"ERROR: "+str(e)+Style.RESET_ALL)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
