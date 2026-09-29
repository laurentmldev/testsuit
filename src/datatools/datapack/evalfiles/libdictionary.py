
##  @package libdictionary.py
# Various tool functions for dictionary management
#

import os,os.path

from datatools.datapack.evalfiles import evalincludes

from datetime import datetime

try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

import re

def expandPath(path,relPrefix="."):
    path=os.path.expanduser(path)
    if path[0]!="/":
        path=relPrefix+os.sep+path
    return os.path.realpath(path)


## provide a standard seconds-based timestamp
# @param date Python date object (like the one returned by 'datetime.datetime.now()')
# @return "%Y-%m-%d %H:%M:%S"
def getTimestamp(date=datetime.now()):
	return date.strftime("%Y-%m-%d %H:%M:%S")

## provide a filename optimized seconds-based timestamp
# @param date Python date object (like the one returned by 'datetime.datetime.now()')
# @return "%Y%m%d_%H-%M-%S"
def getFileTimestamp(date=datetime.now()):
	return date.strftime("%Y%m%d_%H-%M-%S")


    

KEYS_ORIGIN_SUFFIX=".keys"

KEY_DEF_REGEX=r"^\s*([^#=:]+)(=|:=?)\s*([^#]*)\s*"
KEY_DEF_REGEX_OBJ = re.compile(KEY_DEF_REGEX)
MULTILINE_START_CHAR=">"

_global_dico=None
_latest_key=""

def _process_key_def(match):
	global _global_dico
	global _latest_key
	
	key=match.group(1)
	val=match.group(3)
	_global_dico[key]=val
	_latest_key=key

## load keys defined in given file (and included dicos)
def loadDicoEntries(file):
	global _global_dico
	global _latest_key
	_global_dico={}
	
	lines,errorDetected,nbIncludes=evalincludes.expandFileIncludes(file)
	for line in lines:
		if line.startswith(MULTILINE_START_CHAR):
			_global_dico[_latest_key]+=evalkeys.NEW_LINE_MARKER+line.lstrip(MULTILINE_START_CHAR)
		else:
			KEY_DEF_REGEX_OBJ.sub(_process_key_def,line)
	newdico={}
	newdico.update(_global_dico)

	return _global_dico


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
	keysorigin={}

	for key in resultDico:
		keysorigin[key]=os.path.abspath(expandPath(file))

	# check if given dico has its keysorigin files
	keysoriginfile=getKeysOriginFileName(file)
	if (os.access(keysoriginfile,os.R_OK)):
		existingkeysorigin=loadDicoEntries(keysoriginfile)
		for key in existingkeysorigin:
			keysorigin[key]=existingkeysorigin[key]
		
	return resultDico,keysorigin

## load given dico files into a Python dictionary object
# @return dictionary object
def loadDicos(dicoFiles):
	mergedKeysValues={}
	mergedKeysOrigin={}
	# building merged dictionary
	for curDicoFile in reversed(dicoFiles):
		curDico,curKeysOrigin=loadDico(curDicoFile)

		# detect and trace overriden values
		for curkey in curKeysOrigin.keys():

			# If key already defined, that means it has been overriden by the new dico file
			# We then add a <key>.overriden=<previous key origin> entry, in order to easily detect this override later on.
			try:
				# if entry does not exist, an exception will be raised
				# hum maybe there is an 'exists' accessor, this owuld be cleaner...
				previousValue=mergedKeysValues[curkey]
				
				try:
					previousOrigin=mergedKeysOrigin[curkey]
				except KeyError:
					previousOrigin="???"

				if previousValue != curDico[curkey] and previousOrigin != curKeysOrigin[curkey]:				
					try:
						mergedKeysValues[curkey+".overrides.files"]=previousOrigin+"; "+mergedKeysValues[curkey+".overrides.files"]					
						mergedKeysValues[curkey+".overrides.values"]=previousValue+"; "+mergedKeysValues[curkey+".overrides.values"]					
					except KeyError:
						mergedKeysValues[curkey+".overrides.files"]=previousOrigin
						mergedKeysValues[curkey+".overrides.values"]=previousValue

								
					mergedKeysOrigin[curkey+".overrides.files"]=previousOrigin									
					mergedKeysOrigin[curkey+".overrides.values"]=previousOrigin									

					#print("[overriding with "+curkey+"="+curDico[curkey]+" from "+curKeysOrigin[curkey]+" (previous value was '"+previousValue+"' from "+previousOrigin +"]")

			except KeyError:
				# no entry defined, this is not a key override, so we can ignore it
				pass

		mergedKeysValues.update(curDico)
		mergedKeysOrigin.update(curKeysOrigin)

	#print("[Imported "+str(len(mergedKeysValues))+" keys from "+str(dicoFiles)+"]")
	return mergedKeysValues,mergedKeysOrigin

# return value corresponding to given key
def getkeyval(key, dico):
	if not key in dico:
		#log.warning("unknown key '"+key+"'")
		return None
	else :
		return dico[key]


# return index of given str in ';'-separated string of key
def getIndexIn(searchedStr, key, dico):	
	strVal = getkeyval(key, dico)
	if not strVal:
		return None

	strCols=strVal.split(";")
	index=0
	for strCol in strCols:		
		if strCol==searchedStr :
			return index
		index=index+1
	
	return None

# return subkeys defined one step ('.'-separated) deeper of given one
def getChildrenSubkeys(refkey, dico):
	subkeys=[]
	for curkey in dico:
		if len(curkey)>len(refkey) and refkey in curkey: 
			curkey=curkey.replace(refkey,"")
			curSubkey=curkey.split(".")[1]
			if curSubkey not in subkeys:
				subkeys.append(curSubkey)
	return subkeys

# return all full keys deeper than given one
def getChildrenKeys(refkey, dico):
	childkeys=[]
	for curkey in dico:
		if len(curkey)>len(refkey) and refkey in curkey: 
			childkeys.append(curkey)
	return childkeys

# find keys matching given regex
def findKeys(regexPattern, dico):
	result=[]
	regex=re.compile(regexPattern)
	for curkey in dico:
		if regex.match(curkey): 
			result.append(curkey)
	return result

# say if given keybase exists (i.e. if there are some keys containing given string ..)
def isKeybaseDefined(keybase,dico):
	for curkey in dico:
		if keybase in curkey: 
			return True
	return False

# return parent key (up one step '.'-separated)
def getParentKey(key):
	return re.sub(r"\.[^.]+$","",key)

## Create dico file from given data, and associated auth. data
# @param diconame name of the new dico file to generate
# @param dicoEntries dictionary entries of the dico 
# @param dicoKeysOrigin keys origin for traceability. If not defined here, a key is traced a coming from <diconame> in the .<diconame>.keysorigin file
# @return true if success, false otherwise
def createDicoFile(diconame, dicoEntries, dicoKeysOrigin={},deps=[]):
	fileout=open(diconame, "wt")
	fileout.write("# This dictionary is a merge from : "+str(deps)+"\n")
	nbentries=0
	for key in dicoEntries:
		fileout.write(key+"="+dicoEntries[key]+"\n")
		nbentries+=1
	fileout.close()

	# create keys origin file
	keysOriginfile=getKeysOriginFileName(diconame)
	fileout=open(keysOriginfile, "wt")
	for key in dicoEntries:
		if not key in dicoKeysOrigin:
			fileout.write(key+"="+diconame+"\n")
		else:
			fileout.write(key+"="+dicoKeysOrigin[key]+"\n")	

	fileout.close()
		
	log.info("Generated "+diconame+" with "+str(nbentries)+" entries")
	
	return True

## generate a new dictionary out of the given dicos
# generate also associated auth. data
# @param newDicoName file name of the new dictionary
# @param inputDicosList list of input dicos files
# @param deps list of dependency files
# @return True if successful, false otherwise
def mergeDicos(newDicoName, inputDicosList,deps=[]):
	resultDico, keysorigin=loadDicos(inputDicosList)
	# generating dico file
	return createDicoFile(newDicoName, resultDico, keysorigin, inputDicosList)
