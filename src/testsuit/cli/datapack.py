
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

Return 1 if something went wrong, 0 otherwise
    """,
    formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('file',help="the test definition YAML file (or, with --verify, the datapack zip)")
    parser.add_argument('--nocheck',action='store_true',help="Ignore warnings and consistency checks (git tags etc.) for test definition and setup folders.")
    parser.add_argument('--output-dir','-o',help="where to write the datapack zip (default: key 'datapack.output_dir')")
    parser.add_argument('--setup-config','-c',help="setup configuration to use instead of the test definition's 'setup_config'")
    parser.add_argument('--verify',action='store_true',help="check the checksums of the given datapack zip")
    args = parser.parse_args()

    if args.verify:
        problems = verify_datapack(expandPath(args.file))
        for problem in problems:
            print(Fore.RED+"ERROR: "+problem+Style.RESET_ALL)
        if not problems:
            print(Fore.LIGHTGREEN_EX+"datapack is intact"+Style.RESET_ALL)
        sys.exit(1 if problems else 0)

    try:
        datapack(expandPath(args.file), args.nocheck, args.output_dir, args.setup_config)
    except DatapackError as e:
        print(Fore.RED+"ERROR: "+str(e)+Style.RESET_ALL)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
