
##  @package libdictionary.py
# Various tool functions for dictionary management
#

import os,os.path

from testsuit.datatools.datapack.evalfiles import evalincludes

from datetime import datetime

import re

def expandPath(path,relPrefix="."):
    path=os.path.expanduser(path)
    if path[0]!="/":
        path=relPrefix+os.sep+path
    return os.path.realpath(path)


## provide a standard seconds-based timestamp
# @param date Python date object (default: now, evaluated at call time)
# @return "%Y-%m-%d %H:%M:%S"
def getTimestamp(date=None):
	if date is None:
		date=datetime.now()
	return date.strftime("%Y-%m-%d %H:%M:%S")

## provide a filename optimized seconds-based timestamp
# @param date Python date object (default: now, evaluated at call time)
# @return "%Y%m%d_%H-%M-%S"
def getFileTimestamp(date=None):
	if date is None:
		date=datetime.now()
	return date.strftime("%Y%m%d_%H-%M-%S")


    

KEYS_ORIGIN_SUFFIX=".keys"
# extension of dictionary files (their html view has an anchor per key)
DICO_SUFFIX=".dico"

# 'key=value' or 'key:value' or 'key:=value', '#' starting a comment
KEY_DEF_REGEX=r"^\s*([^#=:]+)(=|:=?)\s*([^#]*)\s*"
KEY_DEF_REGEX_OBJ = re.compile(KEY_DEF_REGEX)
# a line starting with this char continues the value of the previous key, on a new line
MULTILINE_START_CHAR=">"

## load keys defined in given file (and included dicos)
def loadDicoEntries(file):
	# local import: evalkeys imports this module at load time
	from testsuit.datatools.datapack.evalfiles import evalkeys

	dico={}
	latestKey=None
	lines,errorDetected,nbIncludes=evalincludes.expandFileIncludes(file)
	for line in lines:
		if line.startswith(MULTILINE_START_CHAR):
			dico[latestKey]+=evalkeys.NEW_LINE_MARKER+line.lstrip(MULTILINE_START_CHAR)
		elif match:=KEY_DEF_REGEX_OBJ.match(line):
			latestKey=match.group(1)
			dico[latestKey]=match.group(3)
	return dico


def getKeysOriginFileName(file):
	dirname=os.path.dirname(file)
	if len(dirname)==0:
		dirname="./"
	filename=os.path.basename(file)
	keysoriginFileName="."+filename+KEYS_ORIGIN_SUFFIX
	return expandPath(dirname+os.sep+keysoriginFileName)


## load given dico file into a Python dictionary object
# @return dictionary object, keysorigin object
def loadDico(file):
	resultDico=loadDicoEntries(file)
	keysorigin=dict.fromkeys(resultDico,os.path.abspath(expandPath(file)))

	# a generated dico has its keys origins in a '.<dico>.keys' file
	keysoriginfile=getKeysOriginFileName(file)
	if os.access(keysoriginfile,os.R_OK):
		keysorigin.update(loadDicoEntries(keysoriginfile))

	return resultDico,keysorigin

## load given dico files into a Python dictionary object
# The first dico has the priority. A key overriding the value of a key from another dico
# is traced by the '<key>.overrides.files' and '<key>.overrides.values' keys.
# @return dictionary object, keysorigin object
def loadDicos(dicoFiles):
	mergedKeysValues={}
	mergedKeysOrigin={}
	for curDicoFile in reversed(dicoFiles):
		curDico,curKeysOrigin=loadDico(curDicoFile)

		for curkey,curOrigin in curKeysOrigin.items():
			if curkey not in mergedKeysValues or curkey not in curDico:
				continue
			previousValue=mergedKeysValues[curkey]
			previousOrigin=mergedKeysOrigin.get(curkey,"???")
			if previousValue == curDico[curkey] or previousOrigin == curOrigin:
				continue

			filesKey=curkey+".overrides.files"
			valuesKey=curkey+".overrides.values"
			if filesKey in mergedKeysValues and valuesKey in mergedKeysValues:
				mergedKeysValues[filesKey]=previousOrigin+"; "+mergedKeysValues[filesKey]
				mergedKeysValues[valuesKey]=previousValue+"; "+mergedKeysValues[valuesKey]
			else:
				mergedKeysValues[filesKey]=previousOrigin
				mergedKeysValues[valuesKey]=previousValue
			mergedKeysOrigin[filesKey]=previousOrigin
			mergedKeysOrigin[valuesKey]=previousOrigin

		mergedKeysValues.update(curDico)
		mergedKeysOrigin.update(curKeysOrigin)

	return mergedKeysValues,mergedKeysOrigin

# return value corresponding to given key, None if undefined
def getkeyval(key, dico):
	return dico.get(key)


# return index of given str in ';'-separated string of key
def getIndexIn(searchedStr, key, dico):
	strVal = getkeyval(key, dico)
	if not strVal:
		return None
	strCols=strVal.split(";")
	return strCols.index(searchedStr) if searchedStr in strCols else None

# return subkeys defined one step ('.'-separated) deeper of given one
def getChildrenSubkeys(refkey, dico):
	subkeys=[]
	for curkey in getChildrenKeys(refkey, dico):
		curSubkey=curkey.replace(refkey,"").split(".")[1]
		if curSubkey not in subkeys:
			subkeys.append(curSubkey)
	return subkeys

# return all full keys deeper than given one
def getChildrenKeys(refkey, dico):
	return [curkey for curkey in dico if len(curkey)>len(refkey) and refkey in curkey]

# find keys matching given regex
def findKeys(regexPattern, dico):
	regex=re.compile(regexPattern)
	return [curkey for curkey in dico if regex.match(curkey)]

# say if given keybase exists (i.e. if there are some keys containing given string ..)
def isKeybaseDefined(keybase,dico):
	return any(keybase in curkey for curkey in dico)

# return parent key (up one step '.'-separated)
def getParentKey(key):
	return re.sub(r"\.[^.]+$","",key)

## Create dico file from given data, and associated auth. data
# @param diconame name of the new dico file to generate
# @param dicoEntries dictionary entries of the dico 
# @param dicoKeysOrigin keys origin for traceability. If not defined here, a key is traced a coming from <diconame> in the .<diconame>.keysorigin file
# @return true if success, false otherwise
def createDicoFile(diconame, dicoEntries, dicoKeysOrigin=None,deps=None):
	dicoKeysOrigin=dicoKeysOrigin or {}
	with open(diconame, "w") as fileout:
		fileout.write("# This dictionary is a merge from : "+str(deps or [])+"\n")
		for key,value in dicoEntries.items():
			fileout.write(key+"="+value+"\n")

	# create keys origin file
	with open(getKeysOriginFileName(diconame), "w") as fileout:
		for key in dicoEntries:
			fileout.write(key+"="+dicoKeysOrigin.get(key,diconame)+"\n")

	print("Generated "+diconame+" with "+str(len(dicoEntries))+" entries")
	return True

## generate a new dictionary out of the given dicos
# generate also associated auth. data
# @param newDicoName file name of the new dictionary
# @param inputDicosList list of input dicos files
# @param deps unused, the input dicos are recorded as dependencies
# @return True if successful, false otherwise
def mergeDicos(newDicoName, inputDicosList,deps=None):
	resultDico, keysorigin=loadDicos(inputDicosList)
	# generating dico file
	return createDicoFile(newDicoName, resultDico, keysorigin, inputDicosList)
