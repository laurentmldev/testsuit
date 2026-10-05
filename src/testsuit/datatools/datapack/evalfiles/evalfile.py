
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
from pathlib import Path

from testsuit.datatools.datapack.evalfiles import evalkeys
from testsuit.datatools.datapack.evalfiles import evalincludes
from testsuit.datatools.datapack.evalfiles import libdictionary

# detection of what is left to evaluate (key detection is case-sensitive here, unlike in evalkeys)
_KEY_PATTERN=re.compile(evalkeys.KEY_REGEX)
_INCLUDE_PATTERN=re.compile(evalincludes.INCLUDE_REGEX)
_INCLUDE_WITH_PARAMS_PATTERN=re.compile(evalincludes.INCLUDE_WITH_PARAMS_REGEX)

HTML_SUFFIX=".html"

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


# html views of dico files written elsewhere than next to them, by real path of the dico
_dicoHtmlViews={}

## html view of a dico file: the one written by writeDicoHtmlViews, or the '.<dico>.html' next to it, None if none
def getDicoHtmlView(dicoFile):
	htmlView=_dicoHtmlViews.get(os.path.realpath(dicoFile))
	if htmlView and os.path.exists(htmlView):
		return htmlView
	htmlView=_getHtmlFileName(dicoFile)
	return htmlView if os.path.exists(htmlView) else None

## write an html view, with an anchor per key, of the given dico files which have none yet,
# so that the links of the html views of evaluated files can jump to the definition of their keys
# @param dicoFiles dico files
# @param targetFolder where to write the views
def writeDicoHtmlViews(dicoFiles, targetFolder):
	for dicoFile in dicoFiles:
		if getDicoHtmlView(dicoFile):
			continue
		Path(targetFolder).mkdir(parents=True, exist_ok=True)
		htmlView=os.path.abspath(os.path.join(targetFolder, "."+os.path.basename(dicoFile)+HTML_SUFFIX))
		n=1
		while os.path.exists(htmlView):
			n+=1
			htmlView=os.path.abspath(os.path.join(targetFolder, "."+os.path.basename(dicoFile)+"."+str(n)+HTML_SUFFIX))
		# keys of the dico are those of its lines once includes expanded, see libdictionary.loadDicoEntries
		lines,_,_=evalincludes.expandFileIncludes(dicoFile)
		with open(htmlView, "w") as filehtml:
			for line in lines:
				filehtml.write(finalizeHtmlLine(line,keyAnchor=True)+"\n")
		_dicoHtmlViews[os.path.realpath(dicoFile)]=htmlView

## html link to the origin of the key: the html view of its dico file if there is one, else the dico file itself
# @param htmlDir folder of the html file of the link: the link to an html view is relative to it, so that it
# still works once the folder is moved (e.g. a datapack). Absolute if None.
def _keyLink(key, val, usedkeys, htmlDir=None):
	origin=usedkeys.get(key,"").split(';')[0]
	htmlView=getDicoHtmlView(origin) if origin else None
	if htmlView:
		origin=os.path.relpath(htmlView,htmlDir) if htmlDir else os.path.abspath(htmlView)
	val=val.replace(evalkeys.NEW_LINE_MARKER,"<br/>\n")
	return "<a href=\""+origin+"#"+key+"\" title=\""+key+"\" >"+val+"</a>"

## html view of a line returned by evalfile: each replaced value is a link to the origin of its key
# @param usedkeys {key: origin file} returned by evalfile
# @param keyAnchor for a dico file: a 'key=value' line starts with an anchor named by its key, target of the links
# @param htmlDir folder of the html file, see _keyLink
def finalizeHtmlLine(line, usedkeys=None, keyAnchor=False, htmlDir=None):
	usedkeys=usedkeys or {}
	anchor=""
	if keyAnchor and (keyDef:=libdictionary.KEY_DEF_REGEX_OBJ.match(evalkeys.finalizeLine(line))):
		anchor="<a id=\""+keyDef.group(1)+"\"></a>"
	htmlescapedline=line.replace("<","&lt;").replace(">","&gt;").replace("  ","&nbsp; ")+"<br/>"
	return anchor+re.sub(evalkeys.RPL_MATCH_REGEX,lambda m: _keyLink(m.group(1),m.group(2),usedkeys,htmlDir),htmlescapedline)

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
# @param outputFile file to write, along with its '.<file>.html' (with key anchors for a .dico file) and '.<file>.keys' files. If None, lines are written on stdout
def finalizeLines(evaluatedLines, usedkeys, restoreUnknownKeys=False, outputFile=None) :

	if restoreUnknownKeys :
		restoreUnknownKeysAndLinks(evaluatedLines)

	if not outputFile:
		for line in evaluatedLines:
			sys.stdout.write(evalkeys.finalizeLine(line)+"\n")
		return

	isDico=outputFile.endswith(libdictionary.DICO_SUFFIX)
	htmlFile=_getHtmlFileName(outputFile)
	with open(outputFile, "w") as fileout, open(htmlFile, "w") as filehtml:
		for line in evaluatedLines:
			fileout.write(evalkeys.finalizeLine(line)+"\n")
			filehtml.write(finalizeHtmlLine(line,usedkeys,keyAnchor=isDico,htmlDir=os.path.dirname(htmlFile))+"\n")

	with open(libdictionary.getKeysOriginFileName(outputFile), "w") as keysfile:
		for key,origin in usedkeys.items():
			keysfile.write(key+"="+origin+"\n")
