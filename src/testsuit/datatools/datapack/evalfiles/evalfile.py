
##  @package evalfile
# Evaluate given file both for includes and keys replacement, based on given dictionnaries.
# This script is based on 'evalkeys.py' and 'evalincludes.py' algorithms.
#
# Performs several replacement and includes as long as some replacements are done, which means that key value can be a reference to another key,
# and an include can contain a key value.
#
# See the 'evalfile' command (testsuit.cli.evalfile).
#

import os
import re
import sys

from testsuit.datatools.datapack.evalfiles import evalkeys
from testsuit.datatools.datapack.evalfiles import evalincludes
from testsuit.datatools.datapack.evalfiles import libdictionary

# detection of what is left to evaluate (key detection is case-sensitive here, unlike in evalkeys)
_KEY_PATTERN=re.compile(evalkeys.KEY_REGEX)
_INCLUDE_PATTERN=re.compile(evalincludes.INCLUDE_REGEX)
_INCLUDE_WITH_PARAMS_PATTERN=re.compile(evalincludes.INCLUDE_WITH_PARAMS_REGEX)

HTML_SUFFIX=".html"

# store used keys when genertating Html version of evaluated file
GlobalUsedKeys={}

def _expandPath(pathStr):
	return os.path.normpath(os.path.expanduser(os.path.expandvars(pathStr)))

def _hasKeysOrIncludes(lines):
	"""True if some of the lines still contain a key or an include to evaluate."""
	for line in lines:
		finalizedLine=evalkeys.finalizeLine(line)
		if _KEY_PATTERN.search(finalizedLine) \
			or _INCLUDE_PATTERN.search(finalizedLine) \
			or _INCLUDE_WITH_PARAMS_PATTERN.search(finalizedLine):
			return True
	return False

def dumpLines(lines):
	return "".join(evalkeys.finalizeLine(line)+"\n" for line in lines)

def addFileKeys(targetFileName,dico,keysorigin):
	"""Add the _FILE_xxx_ keys, describing the file being evaluated."""
	fileKeys={
		"_FILE_NAME_": targetFileName,
		"_FILE_BASENAME_": os.path.basename(targetFileName),
		"_FILE_DIRNAME_": os.path.dirname(targetFileName),
		"_FILE_EXTENSION_": os.path.splitext(targetFileName)[1],
		"_FILE_PURENAME_": os.path.splitext(os.path.basename(targetFileName))[0],
	}
	dico.update(fileKeys)
	keysorigin.update(dict.fromkeys(fileKeys,targetFileName))

## perform includes/keys expansion of given lines, based on given dictionary
#
# Performs several rounds of keys replacement and includes as long as something is left to evaluate,
# since a key value can reference another key, and an included file can contain keys (or an include path a key).
#
# @param targetFile name of processed file, for logging purpose only
# @param lines lines to process
# @param dico dictionary object to be used
# @param dicosFiles unused
# @param partialEval don't worry if some keys are not defined (yet)
# @return tuple 'result lines, used keys origin'
def evallines(targetFile,lines,dico,keysorigin,dicosFiles=None,partialEval=True):

	mykeysorigin={}

	while _hasKeysOrIncludes(lines):

		# keys first, since include paths may contain keys
		evalkeys.setDico(dico,keysorigin)
		lines,usedkeys,undefinedkeys=evalkeys.replaceKeys(targetFile,lines,True)
		mykeysorigin.update(usedkeys)

		# includes always with partial eval, since they might contain keys
		# defined only in the included lines
		lines, errorsDetected, nbIncludes=evalincludes.expandIncludes(targetFile,lines,True)

		# then keys of included lines
		evalkeys.setDico(dico,keysorigin)
		lines,usedkeys,undefinedkeys=evalkeys.replaceKeys(targetFile,lines,partialEval)
		mykeysorigin.update(usedkeys)
		if undefinedkeys and not partialEval:
			print(evalkeys.getUndefinedKeysStr(undefinedkeys))

		# nothing changed during this round, no use to try again
		if len(usedkeys)==0 and nbIncludes==0:
			break

	return lines,mykeysorigin

## load dictionnary context and perform file evaluation
def evalfile(targetFile, dicosNames, partialEval=False):
	dico,keysorigin=libdictionary.loadDicos(dicosNames)
	return doFileEvaluation(targetFile, dico, keysorigin, dicosNames, partialEval)


## perform includes/keys expansion of given file, based on given dictionary
# Resets the evalkeys error counters, which are then those of this file.
# @param targetFile file to process
# @param dico, keysorigin dictionary to be used, and keys origins
# @param dicosNames unused
# @param partialEval don't worry if some keys are not defined (yet)
# @param targetFileName name of the result file, for the _FILE_xxx_ keys (default: targetFile)
# @return result lines of process, used keys origin
def doFileEvaluation(targetFile, dico, keysorigin, dicosNames, partialEval=False, targetFileName=None):

	with open(targetFile,  encoding='utf-8', errors='replace') as f:
		lines=f.readlines()

	evalkeys.resetCounters()
	addFileKeys(targetFileName or targetFile,dico,keysorigin)

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
_UNKNOWN_KEY_PATTERN=re.compile(re.escape(evalkeys.KEYMARK+evalkeys.UNKNOWN_KEY_MARKER))
_UNKNOWN_INCLUDE_PATTERN=re.compile(re.escape(evalincludes.KEYMARK+evalincludes.UNKNOWN_INCLUDE_MARKER))
_UNKNOWN_INCLUDE_WITH_KEY_PATTERN=re.compile(re.escape(evalincludes.KEYMARK+evalincludes.UNKNOWN_INCLUDE_MARKER)+r"\s+src=(\"|')[^\"']*"+evalkeys.KEYMARK+r"\([^\(\)]+\)[^\"']*(\"|')")

def restoreUnknownKeysAndLinks(lines) :
	for idx,line in enumerate(lines):
		line=_UNKNOWN_KEY_PATTERN.sub(evalkeys.KEYMARK,line)
		# don't restore includes which have no key : they have no chance to work better later
		if _UNKNOWN_INCLUDE_WITH_KEY_PATTERN.search(line):
			line=_UNKNOWN_INCLUDE_PATTERN.sub(evalincludes.KEYMARK,line)
		lines[idx]=line
	return lines

## write the evaluated lines (without replacement marks)
# @param evaluatedLines,usedkeys result of evalfile
# @param restoreUnknownKeys restore unknown keys and includes as they were (for a partial evaluation)
# @param outputFile file to write, along with its '.<file>.html' and '.<file>.keys' files. If None, lines are written on stdout
def finalizeLines(evaluatedLines, usedkeys, restoreUnknownKeys=False, outputFile=None) :

	if restoreUnknownKeys :
		restoreUnknownKeysAndLinks(evaluatedLines)

	if not outputFile:
		for line in evaluatedLines:
			sys.stdout.write(evalkeys.finalizeLine(line)+"\n")
		return

	with open(outputFile, "w") as fileout, open(_getHtmlFileName(outputFile), "w") as filehtml:
		for line in evaluatedLines:
			fileout.write(evalkeys.finalizeLine(line)+"\n")
			filehtml.write(finalizeHtmlLine(line)+"\n")

	with open(libdictionary.getKeysOriginFileName(outputFile), "w") as keysfile:
		for key,origin in usedkeys.items():
			keysfile.write(key+"="+origin+"\n")
