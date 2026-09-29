# -*- coding: utf-8 -*-

import sys,os

# add deps folder (relative path to this module)
sys.path.append(os.path.realpath(os.path.dirname( __file__[:-1] if __file__.endswith('.pyc') else __file__ ) +os.sep+".."+os.sep+"src"))

import argparse
import os,os.path,getpass,stat
from pathlib import Path

import xml.etree.ElementTree as ET

import subprocess

from datatools.datapack.evalfiles.libdictionary import loadDicos
from datatools.datapack.evalfiles.libdictionary import getkeyval
from datatools.datapack.evalfiles.evalfile import evalfile,evalkeys,evalincludes,finalizeLines
from datatools.datapack.evalfiles.evalpath import evalPath

from datatools.datapack.evalfiles.libdictionary import loadDicos

from datatools.datapack.datapack_tools import datapack

import misc.files
from misc.files import checksumFolder

import socket
from datetime import datetime

from colorama import Fore, Back, Style

import shutil
import sys
import platform

## check if the given file is accessible
def _isInputReadable(f):
    if not os.access(misc.files.expandPath(f),os.R_OK):
        raise argparse.ArgumentTypeError("{0} does not exist or is not reachable".format(misc.files.expandPath(f)))
    return f

# override the parsing error message using logger
class _HelpParser(argparse.ArgumentParser):
    def error(self, message):
        print(Fore.RED+"ERROR: Input Arguments Error : "+message+Style.RESET_ALL)
        sys.exit(1)
    
## the main function
if __name__ == '__main__':
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


