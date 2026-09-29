

##  @package evalfile
# Evaluate given file both for includes and keys replacement, based on given dictionaries.
# This script is based on 'evalkeys.py' and 'evalincludes.py' algorithms.
#
# Performs several replacement and includes as long as some replacements are done, which means that key value can be a reference to another key,
# and include can contain a key value.
#
# Result lines are displayed on STDOUT.
#
#
# USAGE: evalfile.py [-h] targetfile dico [dico ...]
#
#
import sys,os

# add deps folder (relative path to this module)
sys.path.append(os.path.realpath(os.path.dirname( __file__[:-1] if __file__.endswith('.pyc') else __file__ ) +os.sep+".."+os.sep+"src"))


import argparse
import os.path

from datatools.datapack.evalfiles.evalfile import evalfile,finalizeLines
from datatools.datapack.evalfiles import evalkeys
from datatools.datapack.evalfiles import evalincludes


## check if the given file is accessible
def _isInputReadable(f):
    if not os.access(f,os.R_OK):
        raise argparse.ArgumentTypeError("{0} does not exist or is not readable".format(f))
    return f

# override the parsing error message using logger
class _HelpParser(argparse.ArgumentParser):
    def error(self, message):
        print("Input Arguments Error : "+message)
        sys.exit(1)

## the main function
if __name__ == '__main__':
	parser = _HelpParser(description=
"""Evaluate given file for includes and keys replacement, based on given dictionnaries.
This script is based on 'evalkeys' and 'evalincludes' algorithms.

Performs several processing cycles as long as some replacements are done.
This means that key value can be a reference to another key, and an include can contain a key value.

When option '-o' is used, generates also an HTML view of performed key replacements and a '.<file>.keys' file containing the list of used keys and their provenance.
Otherwise result lines are displayed on STDOUT by default.

Result is displayed in <stdout>.

Return:
	3 a problem occured during processing
	2 if circular inclusion or key reference detected, 
	1 if unreacheable included file or key referene (and no circular include or key ref. detected)
	0 if okay""",
	formatter_class=argparse.RawTextHelpFormatter)
	parser.add_argument('targetfile',help="the text file to be processed",type=_isInputReadable,metavar="targetfile")
	parser.add_argument('dico',nargs='+', help="dictionary file(s) to be used for keys replacement, most important one first",type=_isInputReadable)	
	parser.add_argument('--output', '-o',help="output in the given file, generating also the '.<file>.html' and '.<file>.keys' associated files")
	parser.add_argument('--partial','-p',action='store_true',help="Partial replace : ignore unknown keys, process only defined ones")


	args = parser.parse_args()

	lines, usedkeys=evalfile(args.targetfile, args.dico, args.partial)
	if not lines :
		sys.exit(3)

	finalizeLines(lines, usedkeys, args.partial, args.output)

	if args.output != None:
		print("generated "+str(args.output)+" and associated 'keys' and 'html' files.")

	if evalkeys.nbInfinateRecursion>0 or evalincludes.nbCircularRecursions>0:
		sys.exit(2)
	if evalincludes.nbNotFoundIncludes>0:
		sys.exit(1)
	if not args.partial and evalkeys.nbUndefined>0:
		sys.exit(1)

	sys.exit(0)


