
##  @package fileinclude
# Recursively expand 'include' statements found into given file
#
#	XML include :		&lt;include src="path/to/file" /&gt;
#	Plain text include : [include src="path/to/file" ]
# Result is displayed into STDOUT
#
# USAGE:  fileinclude.py targetfile
#
#
# Return :
#	2 if circular inclusion detected, 
#	1 if unreacheable included file (and no circular inclusion detected)
#	0 if okay

import argparse
import os,os.path
from os.path import dirname

import sys
from testsuit.datatools.datapack.evalfiles import evalkeys

import re

KEYMARK_ATTR_ONCE="once=\"true\""
KEYMARK="_include_"

UNKNOWN_INCLUDE_MARKER="?"
CYCLIC_INCLUDE_MARKER="!"

INCLUDE_REGEX=r"(<|\[)"+KEYMARK+r"(\s+"+KEYMARK_ATTR_ONCE+r")?\s+src=(\"|')\s*(.*)\s*(\"|')\s*(/>|\])"
MATCH_GROUP_INCLUDE_SRC=4

INCLUDE_WITH_PARAMS_REGEX=r"(<)"+KEYMARK+r"(\s+"+KEYMARK_ATTR_ONCE+r")?\s+src=(\"|')\s*(.*)\s*(\"|')\s*(>)"
INCLUDE_WITH_PARAMS_REGEX_END="</"+KEYMARK+">"
INCLUDE_PARAM_REGEX=r"^\s*<param\s+name=(\"|')\s*(.*)\s*(\"|')\s*>\s*(.*)\s*</param>"
MATCH_GROUP_PARAM_NAME=2
MATCH_GROUP_PARAM_VALUE=4

DETECT_KEY_REGEX=None

# nb of processed includes
nbIncludes=0

# nb of detected circular recursions
nbCircularRecursions=0

# nb of not found included files
nbNotFoundIncludes=0

# used to know if processed includ has been successful
includeFoundInLatestEval=False

## trace the (normalized) path and names of included file, and number of inclusions.
# This is used to detect circular recursions (there is a max number of inclusions authorized)
includedFiles={}
MAX_ALLOWED_INCLUSIONS=50

partialEval=False

## the current path during processing
curPath= [ os.getcwd()]

## check if the given file is accessible
def isInputFileReadable(f):
    if not os.access(f,os.R_OK):
        raise argparse.ArgumentTypeError("{0} does not exist or is not readable".format(f))
    return f


## override the parsing error message
class HelpParser(argparse.ArgumentParser):
    def error(self, message):
        print("Input Arguments Error : "+message)
        sys.exit(1)

## normalize, and expand user (~, ~m026761) and vars ($VAR) from the given path
def _expandPath(pathStr):
	return os.path.normpath(os.path.expanduser(os.path.expandvars(pathStr)))

## retrieve param name and value from corresponding XML node
def _parseParam(match):
	paramName=match.group(MATCH_GROUP_PARAM_NAME)	
	paramValue=match.group(MATCH_GROUP_PARAM_VALUE)	

	return paramName+"="+paramValue

## replace the detected regex by contents of the included file
# circular includes detection is based on detection of files already included
# this means that for a given file, a subfile can only be included once ...
def _includeStep(match,includeOnce,curParametersDico):
	global nbIncludes
	global nbCircularRecursions
	global nbNotFoundIncludes
	global partialEval
	global includeFoundInLatestEval
	matchText=match.group(MATCH_GROUP_INCLUDE_SRC)	
	includeFoundInLatestEval=True
	# if file path has been processed with a key replacement, we
	# need to finalize line for getting a clean file path
	includedFilePath=_expandPath(evalkeys.finalizeLine(matchText))
	if (not os.path.isabs(includedFilePath)):		
		includedFilePath=curPath[-1]+os.sep+includedFilePath	
		if (len(curPath[-1])==0):
			includedFilePath="."+includedFilePath
	includedFilePath=os.path.abspath(includedFilePath)	

	includeOnceStr=""
	if includeOnce :
		includeOnceStr=" once=\"true\" "

	if not os.path.isfile(includedFilePath):
		includeFoundInLatestEval=False
		if not DETECT_KEY_REGEX.search(includedFilePath) or partialEval==False:
		#if not evalkeys.MATCH_KEYREF_MARKER.match(includedFilePath) or partialEval==False:	
			nbNotFoundIncludes+=1
			#print("##### includedFilePath '"+includedFilePath+"' match ="+str(DETECT_KEY_REGEX.match(includedFilePath)))
			print("ERROR: Included file '"+includedFilePath+"' not reachable.")
			return "<"+KEYMARK+UNKNOWN_INCLUDE_MARKER+includeOnceStr+" src=\""+matchText+"\" ></_include_>\n"		
		else:
			
			paramsStr=""
			if len(curParametersDico)>0:
				paramsStr="\n"
				for paramName in curParametersDico :
					paramsStr+="<param name=\""+paramName+"\">"+curParametersDico[paramName]+"</param>\n"
				paramsStr+="\n"
			return "<"+KEYMARK+includeOnceStr+" src=\""+matchText+"\" >"+paramsStr+"</_include_>\n"		
			
	else:
		if includedFilePath in includedFiles and includeOnce:
			return ""
		else:
			if includedFilePath in includedFiles and includedFiles[includedFilePath]>=MAX_ALLOWED_INCLUSIONS:
					nbCircularRecursions+=1
					print("WARNING: File '"+includedFilePath+"' has been included more than "+str(MAX_ALLOWED_INCLUSIONS)+". This is considered as a circular reference.")
					return "["+KEYMARK+CYCLIC_INCLUDE_MARKER+" src=\""+matchText+"\"]\n"		
			else:		
				#print("including '"+includedFilePath+"'")
				#print("\nincluding ## "+includedFilePath+" ## includeOnce="+str(includeOnce)+" files : "+str(includedFiles))
				#print("\nincluding ## "+includedFilePath+" ## curParametersDico="+str(curParametersDico))
				nbIncludes+=1
				if includedFilePath not in includedFiles :
					includedFiles[includedFilePath]=0
				includedFiles[includedFilePath]+=1
				f=open(includedFilePath, "rt")
				newlines=f.readlines()
				f.close()
				return _processExpandIncludes(includedFilePath,newlines,curParametersDico)

## return given lines, where keys have been evaluated with given dico (when defined)
def evalIncludeLinesWithParams(lines,includedico,targetFile):

	resultline=""
	tmpDico={}	
	curDico,curKeysOri=evalkeys.getDico()

	# update set evaluaiton context for include
	tmpDico.update(curDico)
	tmpDico.update(includedico)

	#print("		<<<<< evaluating included text : \n"+lines)
	evalkeys.setDico(tmpDico,curKeysOri)
	# keys here are always checked with partial evals
	resultlines,usedkeys,undefinedkeys=evalkeys.replaceKeys(targetFile,[lines],True)
	#print("		>>>>>\n")

	# restore evaluation context
	evalkeys.setDico(curDico,curKeysOri)
	for line in resultlines:
		resultline+=line+"\n"

	return resultline


def _replace_last_occ(s, old, new):
    return (s[::-1].replace(old[::-1],new[::-1], 1))[::-1]

## process given lines
# @param filePath the processed file name, used to keep track current position in file system, needed for "relative paths" processing
# @param lines lines to process
# @param parentParamsDico local params given parent include of this one (include within an include)
# @return a big line (separated with '\n' chars)
def _processExpandIncludes(curFilePath,lines, parentParamsDico={}):
	global curPath
	global includeFoundInLatestEval
	resultLine=""
	nbLine=0
	curPath.append(dirname(curFilePath))

	curParametrizedIncludedTxt=None
	curParametersDico=parentParamsDico
	
	for line in lines:
		# clean line blanks in the end, but not on the beginning in order to keep indentation
		line=line.rstrip()
		
		# add a newline if it's not the first line
		lineSep="\n"
		if (nbLine==0):
			lineSep=""		

		#print("		>>> "+line)

		# performing one-line include (so without params)
		if re.search(INCLUDE_REGEX,line) \
			or re.search(INCLUDE_WITH_PARAMS_REGEX,line) and re.search(INCLUDE_WITH_PARAMS_REGEX_END,line) and not re.search(INCLUDE_PARAM_REGEX,line) :
			
			includeOnce=False
			if re.search(KEYMARK_ATTR_ONCE,line):
				includeOnce=True			
			
			if re.search(INCLUDE_REGEX,line):				
				includedTxt=re.sub(INCLUDE_REGEX,lambda m : _includeStep(m,includeOnce,curParametersDico),line)
				#print("		--- INCLUDE simple:"+line)
				#print("-->\n"+includedTxt)
				if len(includedTxt)>0:
					resultLine+=lineSep+includedTxt		
			else:
				curParametrizedIncludedTxt=re.sub(INCLUDE_WITH_PARAMS_REGEX,lambda m : _includeStep(m,includeOnce,curParametersDico),line)
				#print("		--- INCLUDE with params:"+line)
				#print("-->\n"+curParametrizedIncludedTxt)
				# remove remaining closing '</_include_>' XML node if no error detected
				if len(curParametrizedIncludedTxt)>0:
					resultLine+=lineSep+evalIncludeLinesWithParams(curParametrizedIncludedTxt,curParametersDico,curFilePath)
					#print("		--- include : includeFoundInLatestEval="+str(includeFoundInLatestEval)+" resultLine="+resultLine)

					# if all includes could be evaluated we remove the 'include' end tag
					if includeFoundInLatestEval:
						resultLine=_replace_last_occ(resultLine,INCLUDE_WITH_PARAMS_REGEX_END,"")					

				curParametrizedIncludedTxt=None
				curParametersDico=parentParamsDico

		# starting include with params
		elif re.search(INCLUDE_WITH_PARAMS_REGEX,line) :
			includeOnce=False
			if re.search(KEYMARK_ATTR_ONCE,line):
				includeOnce=True
			
			curParametrizedIncludedTxt=re.sub(INCLUDE_WITH_PARAMS_REGEX,lambda m : _includeStep(m,includeOnce,curParametersDico),line)
			#print("		--- include with params : includeFoundInLatestEval="+str(includeFoundInLatestEval)+" \n"+curParametrizedIncludedTxt)

		# retrieving include params
		elif curParametrizedIncludedTxt!=None and re.search(INCLUDE_PARAM_REGEX,line) :
			paramstr=re.sub(INCLUDE_PARAM_REGEX,_parseParam,line)
			paramData=paramstr.split("=")
			paramName=paramData[0]
			paramVal=paramData[1]
			curParametersDico[paramName]=paramVal			

		# include 'end' node : performing include with its params
		elif curParametrizedIncludedTxt!=None and re.search(INCLUDE_WITH_PARAMS_REGEX_END,line) :
			if len(curParametrizedIncludedTxt)>0:
				
				resultLine+=lineSep+evalIncludeLinesWithParams(curParametrizedIncludedTxt,curParametersDico,curFilePath)
				resultLine=_replace_last_occ(resultLine,INCLUDE_WITH_PARAMS_REGEX_END,"")
				#print("		--- end include : includeFoundInLatestEval="+str(includeFoundInLatestEval))
				if not includeFoundInLatestEval:
					paramsDefXml=""
					for paramName in curParametersDico:
						resultLine+="<param name=\""+paramName+"\">"+curParametersDico[paramName]+"</param>\n"
					resultLine+="</_include_>"

			curParametrizedIncludedTxt=None
			curParametersDico=parentParamsDico
		else:
			resultLine+=lineSep+line
		nbLine+=1		

	curPath.pop()
	return resultLine

## check each given line to perform inclusions if needed
# @param filePath the processed file name, used to keep track current position in file system, needed for "relative paths" processing
# @param lines lines to process
# @param withPartialEval skip silently includes containing unknown keys 
# @return list of expanded lines, and errorFlag==True if some fialures are detected
def expandIncludes(filePath,lines, withPartialEval=False):	
	global partialEval
	global DETECT_KEY_REGEX
	global nbIncludes
	global nbNotFoundIncludes
	global nbCircularRecursions

	nbIncludes=0
	nbNotFoundIncludes=0
	nbCircularRecursions=0

	DETECT_KEY_REGEX=re.compile(evalkeys.KEYMARK+r"\(([^)]+)")

	partialEval=withPartialEval

	lines=_processExpandIncludes(filePath,lines,{}).split("\n")
	# remove (useless?) blanks in the end of the included text
	#print("[Performed "+str(nbIncludes)+" includes, "+str(nbNotFoundIncludes)+" unreachable include(s), "+str(nbCircularRecursions)+" circular include(s) detected]")

	# cleaning list of already included files used for circular inclusion detection
	includedFiles.clear()

	return lines,(nbNotFoundIncludes>0 or nbCircularRecursions>0), nbIncludes

## check each line of the given file to perform inclusions if needed
# @param filePath the processed file name
# @return list of expanded lines
def expandFileIncludes(filePath):		
	f=open(filePath, "rt")
	lines=f.readlines()
	f.close()
	return expandIncludes(filePath,lines)

## the main function
if __name__ == '__main__':

	parser = HelpParser(description=
"""Recursively expand 'include' statements found in given file. 
If relative, paths are relative to the location of the file they are declared in.

	XML include :		<_include_ src="path/to/file" ><param name="myKey.name">My Local Include Data</param></_include>
	Plain text include : [_include_ src="path/to/file" ]

Result is displayed in stdout.

Return :
	2 if circular inclusion detected, 
	1 if unreacheable included file (and no circular inclusion detected)
	0 if okay"""
	,formatter_class=argparse.RawTextHelpFormatter)
	parser.add_argument('targetfile',help="the file to be processed",type=isInputFileReadable,metavar="target_file")
	args = parser.parse_args()		

	rlines,errorDetected,nbIncludes=expandFileIncludes(args.targetfile)
	for line in rlines:
		sys.stdout.write(evalkeys.finalizeLine(line))
		sys.stdout.write("\n")
	sys.stdout.write("\n")

	if (nbCircularRecursions>0):
		sys.exit(2)
	if (nbNotFoundIncludes>0):
		sys.exit(1)
	sys.exit(0)
