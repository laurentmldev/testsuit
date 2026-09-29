
##  @package evalkeys
# Perform keys replacement based on given dictionnaries.
# @warn A call to 'setDico' routine must be performed prior to execute the 'replaceKeys' routine, in order to set the replacement context.
# @warn this package is not robust to multithread, because it uses some global variables to store state through replacement recursive calls.
#
# @warn lines returned by 'replaceKeys' routine must be post-processed using the 'finalizeLine' in order to have the final result
# @see replaceKeys
# @see setDico
# @see finalizeLine
#
# Performs several replacement runs as long as some replacements are done, which means that key value can be a reference to another key.
#
# Features undefined keys and circular infinate recursions detection.
#



import os,os.path,getpass,sys,re,socket,argparse
from pathlib import Path
from testsuit.datatools.datapack.evalfiles import libdictionary


## max runs before arbitrary stop in case of undetected cyclic reference
MAX_RUNS=15

## Global variable : position of the currently replaced line in the target file

curLineNb=0

## Global variable : total amount of replacements
nbRpl=0

# The string used to detect the key refs, i.e. "_K_" in "_K_(myKey)"
KEYMARK="_K_"
XMLMARK="_key_"
KEY_REGEX=r"(\<"+XMLMARK+" src=(\"|\')|"+KEYMARK+r'\('+r')\s*([^())]+)\s*(\)|(\"|\')\s*\/\>)'
MATCH_GROUP=3 # start from 1
MATCH_KEYREF_MARKER=re.compile(KEYMARK+r"\(([^)]+)")
UNKNOWN_KEY_MARKER="?"
CYCLIC_KEY_MARKER="!"

# When replacing, we actually do not purely insert the value, but we insert as '_rpl_(key)__the value___rpl_'
# so that we can keep track of various replacements performed and build values usage traceability.
# Routine finalizeLine remove this replacement marking to obtain purely the inserted value
# @see finalizeLine
RPL_MARKER="_rpl_"
FINALIZE_RPL_START=re.compile(RPL_MARKER+r"\{[^}]+\}__")
RPL_MATCH_REGEX=RPL_MARKER+r"\{([^}]+)\}__(.*?)__"+RPL_MARKER
NEW_LINE_MARKER="__CR__"


## Global variable : our dictionary
dico ={}
## Global variable :  for each replacekeys, we want to know the list of the keys actually replaced, and their origin (aka from which dictionary)
usedkeys={}
## Global variable :  for each key of the dico, trace from where it comes from
keysorigin={}
## Global variable :  remember the successive keys replaced at a given 'line.position'
rpltraces={}
## Global variable : name of the currently processed file, for logging purposes only
processedFile=""
## Global variable : name of the currently processed file, for logging purposes only
undefinedKeys=[]

## Global variable :  number of unmatched keys
nbUndefined=0
## Global variable :  number of recursion errors
nbInfinateRecursion=0

## Global variable :  if set to true, just ignore missing keys.
ignoreMissing=False


## check if the given file is accessible
def _isInputFileReadable(f):
    if not os.access(f,os.R_OK):
        raise argparse.ArgumentTypeError(f"{f} does not exist or is not readable")
    return f

# override the parsing error message using logger
class _HelpParser(argparse.ArgumentParser):
    def error(self, message):
        print("Input Arguments Error : "+message)
        sys.exit(1)


def getUndefinedKeysStr(undefinedKeysData) :
	errorMsg=""
	for curkey in undefinedKeysData :
		errorMsg="undefined key '"+curkey+"' :"
		for missingDetails in undefinedKeysData[curkey] :
			file=missingDetails['file']
			position=missingDetails['position']
			errorMsg+="\n\t-> "+file+":"+position
	return errorMsg

## called during regex performing substitution based on our dictionary
# it uses package global variable to keep track of the context (available dico, traces of replacements etc.)
# @todo find a solution for not needing such global variables
#
# @return replaced line and key used to get value. Insert special marks when undefined key or infinate recursion are detected
def _replaceKeys_substitution(matchObject):
	global dico
	global rpltraces
	global nbRpl
	global curLineNb
	global nbInfinateRecursion
	global nbUndefined
	global ignoreMissing
	global processedFile

	value=""
	
	
	# extract _K_(keyname) -> keyname
	keyname=finalizeLine(matchObject.group(MATCH_GROUP))

	# the ID used to check all the replaced keys at this position
	# this is used to detect circular infinate references
	rplpos=str(curLineNb)+":"+str(matchObject.start())
	if rplpos not in rpltraces:
		rpltraces[rplpos] = []

	#print(matchObject.group(MATCH_GROUP)+"@"+rplpos)
	#print("rpltraces:"+str(rpltraces))
	
	# if the current key has already been replaced once, that means we are doing infinate replacement, then we leave
	if keyname in rpltraces[rplpos]:
		value=KEYMARK+CYCLIC_KEY_MARKER+"("+keyname+")"
		print("WARNING: "+processedFile+":"+rplpos+"\t: infinate recursion during replacement. Keys are "+str(rpltraces[rplpos]))
		nbInfinateRecursion+=1
	else:	
		if keyname in dico:
			value=dico[keyname]
			# add a KEY-replaced marker only if it's a final evaluation, i.e. if value does not contain itself a keyref
			if not MATCH_KEYREF_MARKER.match(value):
				value=RPL_MARKER+"{"+keyname+"}__"+dico[keyname]+"__"+RPL_MARKER
		
			#print(keyname+" -> "+value)
			rpltraces[rplpos].append(keyname)
			nbRpl+=1
			if keyname in keysorigin:
				usedkeys[keyname]=keysorigin[keyname]
			else:
				usedkeys[keyname]=""
		else:
			if keyname not in undefinedKeys :
				undefinedKeys[keyname]=[]
			undefinedKeys[keyname].append({'file':processedFile, 'position':rplpos})			
	
			if not ignoreMissing:
				value=KEYMARK+UNKNOWN_KEY_MARKER+"("+keyname+")"	
				nbUndefined+=1				
			else:
				value=KEYMARK+"("+keyname+")"				
	
	return value



## the 'replaceKeys' routine generate lines containing still a reference to the key used for replacement. This method allow to remove this ref and have the purely replaced values
# @see replaceKeys
def finalizeLine(line):	
	return FINALIZE_RPL_START.sub("",line).replace("__"+RPL_MARKER,"").replace(NEW_LINE_MARKER,"\n").rstrip()
	
## Replace the keys in the given file by corresponding value defined in the dictionary file(s)
# @warn The package dictionary must have been populated before in order to build a data context for this replacement. This can be done with a call to 'setDico(mydico)' routine, typically : 'evalkeys.setDico(libdictionary.loadDicos(args.dicos)'
#
# @warn in order to keep replacement traceability, returned lines contain also the key used to perform replacement, and so they need to be post-processed with 'finalizeLine' routine for final line result..
# @see finalizeLine
# @see setDico
# @param filePath the processed file name, used for proper log messages
# @param lines lines to process
# @param doIgnoreMissing ignore undefined keys, let them as they are. This is used for sections processing when we just replace keys defined in the section scope.
# @return tuple 'resulting lines list, used keys origin dico', or 'None, None' if error occured. See 'finalizeLine' routine to build final lines from returned lines.
def  replaceKeys(filePath,lines,doIgnoreMissing=False):
	global processedFile
	global dico
	global ignoreMissing
	global usedkeys
	global undefinedKeys
	processedFile=filePath
	ignoreMissing=doIgnoreMissing

	#print("		### ignoreMissing="+str(ignoreMissing))
	# store the list of keys actually used for populating given lines
	usedkeys={}
	undefinedKeys={}
		
	global nbRpl
	global curLineNb
	oldNbRpl=0 # used to detect that no replacement occured during previous run, so no need to start another one
	nbRuns=0 # the amount of times we did the process

	#print("\n###		<<<Lines replace "+filePath+" >>>")
	# while replacement are possible we process the file
	while ((nbRpl==0 or nbRpl!=oldNbRpl) and nbRuns<=MAX_RUNS):
		oldNbRpl=nbRpl
		nbRuns+=1
		curLineNb=0 # curLineNb counter
		for line in lines:
				curLineNb+=1
				lines[curLineNb-1]=re.sub(KEY_REGEX,_replaceKeys_substitution,lines[curLineNb-1],flags=re.IGNORECASE)
				#print("		### >>> "+lines[curLineNb-1])
	
	#print("[Performed "+str(nbRpl)+" replacements over "+str(nbRuns)+" runs, "+str(nbUndefined)+" undefined key(s), "+str(nbInfinateRecursion)+" infinate recursion(s)]")	

	return lines,usedkeys,undefinedKeys

## return a dico,origins with a set of environment keys
def _getDefaultKeysDico():

	evalkeys_file=os.path.realpath(__file__)
	defaultKeysDico={}
	defaultKeysOrigin={}

	defaultKeysDico["_ENV_USER_"]=getpass.getuser()
	defaultKeysOrigin["_ENV_USER_"]=evalkeys_file

	defaultKeysDico["_ENV_HOME_"]=Path.home()
	defaultKeysOrigin["_ENV_HOME_"]=evalkeys_file
	

	userid=defaultKeysDico["_ENV_USER_"]
	defaultKeysDico["_ENV_USERID_"]=userid
	defaultKeysOrigin["_ENV_USERID_"]=evalkeys_file

	defaultKeysDico["_ENV_PWD_"]=os.getenv("PWD","")
	defaultKeysOrigin["_ENV_PWD_"]=evalkeys_file
	
	defaultKeysDico["_ENV_HOSTNAME_"]=socket.gethostname()
	defaultKeysOrigin["_ENV_HOSTNAME_"]=evalkeys_file

	defaultKeysDico["_ENV_TIMESTAMP_"]=libdictionary.getTimestamp()
	defaultKeysOrigin["_ENV_TIMESTAMP_"]=evalkeys_file

	return defaultKeysDico,defaultKeysOrigin

## Set current replacement context to the given dictionary. This routine must be called prior to invoking 'replaceKeys' routine.
# Set also few environment keys
# @see replaceKeys
def setDico(replaceDictionary, mykeysorigin=None):
	global dico
	global nbUndefined
	global nbInfinateRecursion
	defaultKeysDico,defaultKeysOrigin=_getDefaultKeysDico()
	dico.clear()
	dico.update(replaceDictionary)
	dico.update(defaultKeysDico)	
	rpltraces.clear()
	keysorigin.update(mykeysorigin or {})
	keysorigin.update(defaultKeysOrigin)

def getDico():
	global dico
	global keysorigin
	return dico,keysorigin

## the main function
if __name__ == '__main__':

	parser = _HelpParser(description=
"""Replace in the given file all """+KEYMARK+"""(keyname) by the corresponding value found in given dictionary (declared as "keyname=this is replacement text"). 

This tool can safely handle undefined keys and most of circular infinite recursions.

Result lines are displayed in <stdout>.

Return:
	2 if circular references detected
	1 if undefined reference detected (and not circular references)
	0 if okay""",
	formatter_class=argparse.RawTextHelpFormatter)
	parser.add_argument('targetfile',help="the text file where to replace keys",type=_isInputFileReadable,metavar="targetfile")
	parser.add_argument('dicos',nargs='+', help="dictionary file(s) to be used, most important one first",type=_isInputFileReadable)	
	parser.add_argument('--ignoreundef',action='store_true',help="ignore undefined keys")
	
	args = parser.parse_args()		

	f=open(args.targetfile)
	lines=f.readlines()
	f.close()

	resultDico,keysorigin=libdictionary.loadDicos(args.dicos)
	setDico(resultDico,keysorigin)
	rlines, usedkeys, undefinedKeys=replaceKeys(args.targetfile,lines,args.ignoreundef)

	if len(undefinedKeys) > 0 :
		if not args.ignoreundef :
			print(getUndefinedKeysStr(undefinedKeys))
		else :
			#print("(ignored) "+getUndefinedKeysStr(undefinedKeys))
			pass

	for line in rlines:
		sys.stdout.write(finalizeLine(line)+"\n")
	sys.stdout.write("\n")
	
	if (nbInfinateRecursion>0):
		sys.exit(2)
	if (nbUndefined>0 and not args.ignoreundef):
		sys.exit(1)
	sys.exit(0)


	# unexpected
	sys.exit(255)

