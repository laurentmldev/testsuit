
##  @package evalfile
# Evaluate given file both for includes and keys replacement, based on given dictionnaries.
# This script is based on 'evalkeys.py' and 'evalincludes.py' algorithms.
#
# Performs several replacement and includes as long as some replacements are done, which means that key value can be a reference to another key,
# and and include can contain a key value.
#
# Result lines are displayed on STDOUT.
#
#
# USAGE: evalfile.py [-h] targetfile dico [dico ...]
#
#

import argparse
import os,os.path

import sys

from testsuit.datatools.datapack.evalfiles import evalkeys
from testsuit.datatools.datapack.evalfiles import evalincludes
from testsuit.datatools.datapack.evalfiles import libdictionary

import re

global GlobalUsedKeys

# The string used to detect the keys, i.e. "_K_" in "_K_(myKey)"
pattern_key=None
pattern_include=None
pattern_include_with_params=None
pattern_section=None
pattern_customsection=None

HTML_SUFFIX=".html"

# store used keys when genertating Html version of evaluated file
GlobalUsedKeys={}

def _expandPath(pathStr):
	return os.path.normpath(os.path.expanduser(os.path.expandvars(pathStr)))

## check if the given file is accessible
def _isInputReadable(f):
    if not os.access(f,os.R_OK):
        raise argparse.ArgumentTypeError(f"{f} does not exist or is not readable")
    return f

# override the parsing error message using logger
class _HelpParser(argparse.ArgumentParser):
    def error(self, message):
        print("Input Arguments Error : "+message)
        sys.exit(1)



def _hasKeysOrIncludesOrCustomSections(lines):	
	global pattern_key
	global pattern_include
	global pattern_include_with_params
	global pattern_customsection


	for line in lines :
		finalizedLine=evalkeys.finalizeLine(line)

		#print "### '"+finalizedLine+"'\n\t->containsInclude("+evalincludes.INCLUDE_REGEX+")="+str(pattern_include.search(finalizedLine))
		if pattern_key.search(finalizedLine) \
			or pattern_include.search(finalizedLine) \
			or pattern_include_with_params.search(finalizedLine) :
			
			#print "			-> To evaluate! : "+finalizedLine
			return True

	return False

def dumpLines(lines):
	linesStr=""
	for line in lines:
		linesStr+=evalkeys.finalizeLine(line)+"\n"
	return linesStr

def addFileKeys(targetFileName,dico,keysorigin):

	# add few keys about file being evaluated
	dico["_FILE_NAME_"]=targetFileName
	keysorigin["_FILE_NAME_"]=targetFileName
	dico["_FILE_BASENAME_"]=os.path.basename(targetFileName)
	keysorigin["_FILE_BASENAME_"]=targetFileName
	dico["_FILE_DIRNAME_"]=os.path.dirname(targetFileName)
	keysorigin["_FILE_DIRNAME_"]=targetFileName
	dico["_FILE_EXTENSION_"]=os.path.splitext(targetFileName)[1]
	keysorigin["_FILE_EXTENSION_"]=targetFileName
	dico["_FILE_PURENAME_"]=os.path.splitext(os.path.basename(targetFileName))[0]
	keysorigin["_FILE_PURENAME_"]=targetFileName

## perform includes/keys expansion of given file, based on given dictionary
#
# Performs several replacement and includes as long as some replacements are done, 
# which means that key value can be a reference into another key, and an itself include can contain a key value.
#
# @param targetFile name of processed file, for logging purpose only
# @param lines lines to process
# @param dico dictionary object to be used
# @param partialEval don't worry if some keys are not defined (yet)
# @return tuple 'result lines, keys origin', None,None ir error occured
def evallines(targetFile,lines,dico,keysorigin,dicosFiles=None,partialEval=True):

	# contain the list of used keys for evaluation
	mykeysorigin={}

	# if True, then an undefined key will be let as is, leading to undefined loop
	# this
	ignoreMissing=False

	nbEvals=0

	# loop on the same file while expansions can be done
	while(_hasKeysOrIncludesOrCustomSections(lines)):
		nbEvals+=1
		#print("\n\n########################### Round "+str(nbEvals)+" ###########################")
		#print(dumpLines(lines))

		evalkeys.setDico(dico,keysorigin)
		lines,usedkeys,undefinedkeys=evalkeys.replaceKeys(targetFile,lines,True)
		mykeysorigin.update(usedkeys)

		# includes should be always with partial eval, since they might contain some keys to be evaluated
		# which might come themselves from the included lines etc. ... 
		lines, errorsDetected, nbIncludes=evalincludes.expandIncludes(targetFile,lines,True)
		#print("######------- includes --------->>>>>\n"+dumpLines(lines))
		
		evalkeys.setDico(dico,keysorigin)
		lines,usedkeys,undefinedkeys=evalkeys.replaceKeys(targetFile,lines,partialEval)
		mykeysorigin.update(usedkeys)
		#print("######------- post keys --------->>>>>\n"+dumpLines(lines))
		if len(undefinedkeys) > 0 :
			if not partialEval :
				print(evalkeys.getUndefinedKeysStr(undefinedkeys))
			else :
				#print("(ignored) "+evalkeys.getUndefinedKeysStr(undefinedkeys))
				pass

		# if nothing changed during last round, no use to try again...
		if len(usedkeys)==0 and nbIncludes==0:
			#print("QUIT eval of "+targetFile)
			break			

	#print("		###########################  Final Result ###########################")
	#for line in lines :
	#	print("		>>> "+line)

	return lines,mykeysorigin

## load dictionnary context and perform file evaluation
def evalfile(targetFile, dicosNames, partialEval=False):

	dico,keysorigin=libdictionary.loadDicos(dicosNames)
	
	#print("#### loaded main dico : "+str(dico))

	return doFileEvaluation(targetFile, dico, keysorigin, dicosNames, partialEval)


## perform includes/keys expansion of given file, based on given dictionary
# @param targetFile file to process
# @param dicos list of files to be used as dictionaries (ordered by priority)
# @param partialEval don't worry if some keys are not defined (yet)
# @return result lines of process, None ir error occured
def doFileEvaluation(targetFile, dico, keysorigin, dicosNames, partialEval=False, targetFileName=None):

	#print("### doFileEvaluation "+targetFile+" partialEval="+str(partialEval))

	global pattern_key
	global pattern_include
	global pattern_include_with_params
	global pattern_section
	global pattern_customsection

	if not targetFileName:
		targetFileName=targetFile

	with open(targetFile,  encoding='utf-8', errors='replace') as f:
		lines=f.readlines()		
	
	pattern_key=re.compile(evalkeys.KEY_REGEX)
	pattern_include=re.compile(evalincludes.INCLUDE_REGEX)
	pattern_include_with_params=re.compile(evalincludes.INCLUDE_WITH_PARAMS_REGEX)
	
	addFileKeys(targetFileName,dico,keysorigin)

	return evallines(targetFile,lines,dico,keysorigin, dicosNames, partialEval)


def _getHtmlFileName(file):
	dirname=os.path.dirname(file)
	if len(dirname)==0:
		dirname="./"
	filename=os.path.basename(file)
	htmlFileName="."+filename+HTML_SUFFIX
	return os.path.abspath(_expandPath(dirname+os.sep+htmlFileName))


def _generateHtml(m):
	global GlobalUsedKeys

	key=m.group(1)
	val=m.group(2)
	val=val.replace(evalkeys.NEW_LINE_MARKER,"<br/>\n")
	origin=""
	if key in GlobalUsedKeys:
		origins=GlobalUsedKeys[key].split(';')		
		firstoriginfile=origins[0]
		originhtmlfile=_getHtmlFileName(firstoriginfile)
		# if an html file exists for this file, we send to the html file,
		# otherwise we send to the original file
		if os.path.exists(originhtmlfile):
			origin=originhtmlfile
		else:
			origin=firstoriginfile


	return "<a href=\""+origin+"#"+key+"\" title=\""+key+"\" >"+val+"</a>"

## for processing purposes, generated lines keep reference to the used key name
# this method allow to remove it from line the thus to work on a clean data for furthur processing
def finalizeHtmlLine(line):	
	htmlescapedline=line.replace("<","&lt;").replace(">","&gt;").replace("  ","&nbsp; ")+"<br/>"	
	return re.sub(evalkeys.RPL_MATCH_REGEX,_generateHtml,htmlescapedline)
	
## Used to post-process result when 'partial' option is activated
# We then restore unknown/not found key refs and includes as original ones
def restoreUnknownKeysAndLinks(lines) :
	# "\\" : need to escape the '?' (UNKNOWN_KEY_MARKER) for regex ...
	restoreUnknownKeysRegex=re.compile(evalkeys.KEYMARK+"\\"+evalkeys.UNKNOWN_KEY_MARKER) 
	restoreUnknownIncludesRegex=re.compile(evalincludes.KEYMARK+"\\"+evalincludes.UNKNOWN_INCLUDE_MARKER) 
	includeSrcWithoutKey=re.compile(evalincludes.KEYMARK+"\\"+evalincludes.UNKNOWN_INCLUDE_MARKER+r"\s+src=(\"|')[^\"']*"+evalkeys.KEYMARK+r"\([^\(\)]+\)[^\"']*(\"|')")

	for idx in range(len(lines)) :
		lines[idx]=restoreUnknownKeysRegex.sub(evalkeys.KEYMARK,lines[idx])	
		
		# don't restore includes which have no key : they have no chance to work better later
		if includeSrcWithoutKey.search(lines[idx]):
			lines[idx]=restoreUnknownIncludesRegex.sub(evalincludes.KEYMARK,lines[idx])		
		
	return lines

# if outputFile=None then dislay lines on stdout
def finalizeLines(evaluatedLines, usedkeys, restoreUnknownKeys=False, outputFile=None) :

	GlobalUsedKeys=usedkeys
	
	# if partial replace option has been activated, we restore unknown keys and includes as original ones
	if restoreUnknownKeys :
		lines=restoreUnknownKeysAndLinks(evaluatedLines)

	if not outputFile:
		for line in evaluatedLines:
			sys.stdout.write(evalkeys.finalizeLine(line))
			sys.stdout.write("\n")
	else:
		
		# generate out and hmtl files
		htmlfile=_getHtmlFileName(outputFile)
		fileout=open(outputFile, "w")
		filehtml=open(htmlfile, "w")	
		for line in evaluatedLines:	
			fileout.write(evalkeys.finalizeLine(line)+"\n")
			filehtml.write(finalizeHtmlLine(line)+"\n")
		fileout.close()
		filehtml.close()

		# generate keys file
		keysfile=libdictionary.getKeysOriginFileName(outputFile)
		fileout=open(keysfile, "w")
		for key in usedkeys:
			fileout.write(key+"="+usedkeys[key]+"\n")
		fileout.close()	


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

	if args.output is not None:
		print("generated "+str(args.output)+" and associated 'keys' and 'html' files.")

	if evalkeys.nbInfinateRecursion>0 or evalincludes.nbCircularRecursions>0:
		sys.exit(2)
	if evalincludes.nbNotFoundIncludes>0:
		sys.exit(1)
	if not args.partial and evalkeys.nbUndefined>0:
		sys.exit(1)

	sys.exit(0)


