
import sys,os


import argparse
import os,os.path





from testsuit.datatools.datapack.datapack_tools import datapack

from testsuit import misc


from colorama import Fore, Style

import sys

## check if the given file is accessible
def _isInputReadable(f):
    if not os.access(misc.files.expandPath(f),os.R_OK):
        raise argparse.ArgumentTypeError(f"{misc.files.expandPath(f)} does not exist or is not reachable")
    return f

# override the parsing error message using logger
class _HelpParser(argparse.ArgumentParser):
    def error(self, message):
        print(Fore.RED+"ERROR: Input Arguments Error : "+message+Style.RESET_ALL)
        sys.exit(1)
    
## the main function
def main():
    parser = _HelpParser(description=
    """Generate datapack from given test definition file.

       Dictionaries listed in test definition are applied to the test definition file itself and also to pointed datapack config.
       Dictionaries listed in datapack config are applied to datapack config itself, but *not* to test definition.
    
    Return 1 if something went wrong, 0 otherwise
    """,
    formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('testdef_file',nargs='?',default="datapack.xml",help="the YAML file containing datapack definition",type=_isInputReadable)
    parser.add_argument('--nocheck',action='store_true',help="Ignore warnings and consistency checks (git tags etc.) for test procedure and Setup folders.")
    args = parser.parse_args()

    success = datapack(misc.files.expandPath(args.testdef_file),args.nocheck)
    if success:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
