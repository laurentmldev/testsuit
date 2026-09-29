
##  @package buildComponentInput
# build (evaluate) given target file given source path and evaluation method.
# This tool is mainly used by maketools/component.mk to generate components inputs from template files.
#

import argparse

import os,os.path
from pathlib import Path


import sys

from datatools.datapack.evalfiles import evalfile
from datatools.datapack.evalfiles import evalkeys
from datatools.datapack.evalfiles import evalincludes
from datatools.datapack.evalfiles import libdictionary

import glob
import random

import shutil
import stat
import re

nbTotalFiles=0

# custom version of copytree while python3.8 not available
# code from https://stackoverflow.com/questions/1868714/how-do-i-copy-an-entire-directory-of-files-into-an-existing-directory-using-pyth

# >=python3.8: shutil.copytree(fileOrFolder,targetFolder+os.sep+os.path.basename(fileOrFolder), dirs_exist_ok=True)
# <python3.8: copytree(fileOrFolder,targetFolder+os.sep+os.path.basename(fileOrFolder))
def copytree(src, dst, symlinks = True, ignore = None):
  if not os.path.exists(dst):
    os.makedirs(dst)
    shutil.copystat(src, dst)
  lst = os.listdir(src)
  if ignore:
    excl = ignore(src, lst)
    lst = [x for x in lst if x not in excl]
  for item in lst:
    s = os.path.join(src, item)
    d = os.path.join(dst, item)
    if symlinks and os.path.islink(s):
      if os.path.lexists(d):
        os.remove(d)
      os.symlink(os.readlink(s), d)
      try:
        st = os.lstat(s)
        mode = stat.S_IMODE(st.st_mode)
        os.lchmod(d, mode)
      except:
        pass # lchmod not available
    elif os.path.isdir(s):
      copytree(s, d, symlinks, ignore)
    else:
      shutil.copy2(s, d)

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

def evalMultiSourceFiles(method, sourcePaths, targetFolder, dico, keysorigin, dicosNames):

	#print("#### processing multi-paths "+sourcePaths)

	sourceFilesList=[]
	try:		
		Path(targetFolder).mkdir(parents=True, exist_ok=True)
	except OSError as e:
		print("ERROR: unable to create target folder '"+targetFolder+"' " + str(e))
		return False

	success=True

	pathslist=sourcePaths.rstrip(",").split(",")
	
	for curPath in pathslist:
		#print("#### processing (multi) path "+curPath)
		for source in glob.glob(curPath):
			if os.path.isdir(source):
				target=targetFolder+os.sep+os.path.basename(source)
				#print("adding folder '"+source+"' as '"+target+"'")
				evalOk,sourcesFiles = evalSourceFolder(method, source, target, dico, keysorigin, dicosNames)				
				success=success and evalOk
				sourceFilesList+=sourcesFiles
			else:			
				target=targetFolder+os.sep+os.path.basename(source)
				sourceFilesList+=[source]
				#print("adding file '"+source+"' as '"+target+"'")
				success=success and evalSourceFile(method, source, target, dico, keysorigin, dicosNames)
	
	#print("#### > "+str(success))	
	
	return success, sourceFilesList


def evalSourceFolder(method, sourceFolder, targetFolder, dico, keysorigin, dicosNames):

	#TMP_DIR=expandPath("$HOME/tmp/evalpath_"+getFileTimestamp()+"_"+os.path.basename(targetFolder)+"__tmp"+str(random.randint(1,1000000)))

	sourceFilesList=[]

	success=False

	try:
		Path(targetFolder).mkdir(parents=True, exist_ok=True)
		#print("### evalSourceFolder "+sourceFolder)

		for fileOrFolder in glob.iglob(os.path.join(sourceFolder, "*")):
			if os.path.isfile(fileOrFolder):
				shutil.copy2(fileOrFolder,targetFolder)				
			elif os.path.isdir(fileOrFolder):			
				Path(targetFolder+os.sep+os.path.basename(fileOrFolder)).mkdir(parents=True, exist_ok=True)
				#print("	### cp -r "+fileOrFolder+" "+targetFolder+os.sep+os.path.basename(fileOrFolder))
				#python3.8
				#shutil.copytree(fileOrFolder,targetFolder+os.sep+os.path.basename(fileOrFolder), dirs_exist_ok=True)
				copytree(fileOrFolder,targetFolder+os.sep+os.path.basename(fileOrFolder))
				
		# copy also hidden files and folders
		for fileOrFolder in glob.iglob(os.path.join(sourceFolder, ".*")):
			if os.path.isfile(fileOrFolder):
				shutil.copy2(fileOrFolder,targetFolder)				
			elif os.path.isdir(fileOrFolder):
				Path(targetFolder+os.sep+os.path.basename(fileOrFolder)).mkdir(parents=True, exist_ok=True)
				#print("	### cp -r "+fileOrFolder+" "+targetFolder+os.sep+os.path.basename(fileOrFolder))
				#python3.8
				#shutil.copytree(fileOrFolder,targetFolder+os.sep+os.path.basename(fileOrFolder),dirs_exist_ok=True)
				copytree(fileOrFolder,targetFolder+os.sep+os.path.basename(fileOrFolder))				
		
	except OSError as e:
		print("ERROR: unable to create target folder '"+targetFolder+"' " + str(e))		

	success=True

	for subdir, dirs, files in os.walk(sourceFolder):
		for file in files:
			relPath=subdir.replace(sourceFolder,"")
			sourceFile=subdir+os.sep+file
			sourceFilesList+=[sourceFile]
			targetFile=targetFolder+os.sep+relPath+os.sep+file
			#print("### evaluating folder file '"+file+"' ("+method+")")	
			#print("		targetFolder="+targetFolder)
			#print("		sourceFolder="+sourceFolder)
			#print("		subdir="+subdir)
			#print("		file="+file)
			#print("		targetFile="+targetFile)
			#print("		"+sourceFile+" -> "+targetFile)
			success=evalSourceFile(method, sourceFile, targetFile, dico, keysorigin, dicosNames) and success
	
	#print("### Folder '"+sourceFolder+"' -> '"+targetFolder+"' deps :  "+str(sourceFilesList))
	return success,sourceFilesList



def evalSourceFile(method, sourceFile, targetFile, dico, keysorigin, dicosNames):

	success=False

	#print("### evalSourceFile "+" ("+method+") "+sourceFile+" -> "+targetFile)

	if method=='copy':
		
		# if files are the same then nothing to do
		if os.path.realpath(sourceFile) != os.path.realpath(targetFile) :			
			Path(os.path.dirname(targetFile)).mkdir(parents=True, exist_ok=True)
			# copy2 : keeps file metadata identical to orignal one (same than shell command 'cp -p')	
			if not os.path.isfile(sourceFile) :
				print("ERROR: source file unreachable : '"+sourceFile+"', unable to copy it as '"+targetFile+"' ")	
				sys.exit(1)
			shutil.copy2(sourceFile,targetFile)
			success=True

		else :
			# nothing todo everything is fine
			success=True
	elif method=='eval':
	
		noPartialEval=False
		lines, usedkeys = evalfile.doFileEvaluation(libdictionary.expandPath(sourceFile), dico, keysorigin, dicosNames,noPartialEval, targetFile)		
		evalfile.finalizeLines(lines, usedkeys, restoreUnknownKeys=False, outputFile=targetFile)
		
		#print("### Evaluation of '"+sourceFile+"' : evalkeys.nbUndefined="+str(evalkeys.nbUndefined))

		if evalkeys.nbInfinateRecursion>0 :		
			print("while evaluating file '"+targetFile+"' : "+str(evalkeys.nbInfinateRecursion)+" infinate recursion(s) detected")
			return False,[]
		elif evalincludes.nbNotFoundIncludes>0 :
			print("while evaluating file '"+targetFile+"' : "+str(evalincludes.nbNotFoundIncludes)+" unresolved file include(s) detected")
			return False,[]
		elif evalkeys.nbUndefined>0 :
			print("while evaluating file '"+targetFile+"' : "+str(evalkeys.nbUndefined)+" undefined key(s) detected")
			return False,[]	

		success=True
  
	elif method=='exec':
	
		# TODO syscall to run command 'sourceFile' giving it, catch lines from stdout, eval lines and write targetFile
		print("while evaluating file '"+targetFile+"' : 'exec' method not implemented yet (sorry)")
		return False,[]	

		success=False
  
	else:
		raise Exception(f"unknown file evaluation method '{method}' (copy|eval|exec)")

	#print("### generated file '"+targetFile+"' : "+str(success))

	global nbTotalFiles
	nbTotalFiles=nbTotalFiles+1

	return success,[sourceFile]



# return string either :
#	- file : simple file
#	- zip : .zip file
#	- tar_gz  : .tar.gz file
#	- tgz : .tgz file
#	- folder : folder
#	- multi : multiple files/folders (ex: "xxx/*/*yyy.txt" )
def getSourcePathType(sourcepath):

	if "*" in sourcepath or "," in sourcepath :
		#print("### multi source")
		return "multi"

	elif os.path.isdir(libdictionary.expandPath(sourcepath)):
		#print("### folder source")
		return "folder"


# TODO maybe : when source is a zipped file, unzip->eval templates->rezip
#	elif ".zip" in sourcepath:
#		return "zip"
#		
#	elif ".tar.gz" in sourcepath:
#		return "tar_gz"
#		
#	elif ".tgz" in sourcepath:
#		return "tgz"

	elif os.path.isfile(libdictionary.expandPath(sourcepath)):
		#print("### file source")
		return "file"

	else :
		print("unable to recognize type of given source-path : '"+sourcepath+"'")
		sys.exit(1)

def evalPath(targetpath,sourcepath, method, dico, keysorigin, dicosPaths):

	dicosNames=[]
	for dicoName in dicosPaths:
		fullName=libdictionary.expandPath(dicoName)
		dicosNames.append(fullName)

	sourcepath=str(sourcepath)
	targetpath=str(targetpath)
  
	sourceType = getSourcePathType(sourcepath)
	#print("### targetpath="+targetpath+" from sourcepath="+sourcepath+" and dico:"+str(dicosPaths)+" sourceType="+sourceType)

	success=True

	sourceFilesList=None
	archive_baseDir=None
	
	# 1- evaluate target file(s)
	if sourceType=="file":
		success, sourceFilesList = evalSourceFile(method, sourcepath, targetpath, dico, keysorigin, dicosNames)

	elif sourceType=="folder":
		success, sourceFilesList = evalSourceFolder(method, sourcepath, targetpath, dico, keysorigin, dicosNames)

	elif sourceType=="multi":		
		success,sourceFilesList = evalMultiSourceFiles(method, sourcepath, targetpath, dico, keysorigin, dicosNames)

	#print("### '"+targetpath+"' deps :  "+str(sourceFilesList))
	#print("### '"+targetpath+"' from '"+sourcepath+"' =>"+str(success))	
	
	compressed=False

	# 2- compress result if required (i.e. if original source was not already a compressed file)
	if (targetpath.endswith(".tgz") or targetpath.endswith(".tar.gz")) \
		and not (sourcepath.endswith(".tgz") or sourcepath.endswith(".tar.gz")):

		targetname_withoutext=targetpath.replace(".tgz","").replace(".tar.gz","")
		shutil.move(targetpath,targetname_withoutext)

		if sourceType=="folder":
			folder_targetname_withoutext=os.path.dirname(targetname_withoutext)+"/"+os.path.basename(sourcepath.replace(".tgz","").replace(".tar.gz",""))
			shutil.move(targetname_withoutext,folder_targetname_withoutext)
			targetname_withoutext=folder_targetname_withoutext
			arch_rootdir=os.path.dirname(targetname_withoutext)	
			archive_baseDir=os.path.basename(targetname_withoutext)
				
		else:
			arch_rootdir=targetname_withoutext
			archive_baseDir=None	

		#print("### building archive "+targetpath+" from "+sourcepath+" : arch_rootdir="+arch_rootdir+" archive_baseDir="+archive_baseDir)
		shutil.make_archive(targetname_withoutext, 'gztar', arch_rootdir, archive_baseDir)
		shutil.rmtree(targetname_withoutext)
		shutil.move(targetname_withoutext+".tar.gz",targetpath)
		compressed=True		

	elif targetpath.endswith(".zip") and not sourcepath.endswith(".zip") :
		
		targetname_withoutext=targetpath.replace(".zip","")
		shutil.move(targetpath,targetname_withoutext)

		if sourceType=="folder":
			folder_targetname_withoutext=os.path.dirname(targetname_withoutext)+"/"+os.path.basename(sourcepath.replace(".zip",""))
			shutil.move(targetname_withoutext,folder_targetname_withoutext)
			targetname_withoutext=folder_targetname_withoutext
			arch_rootdir=os.path.dirname(targetname_withoutext)	
			archive_baseDir=os.path.basename(targetname_withoutext)
				
		else:
			arch_rootdir=targetname_withoutext
			archive_baseDir=None	

		#print("### building archive "+targetpath+" from "+sourcepath+" : arch_rootdir="+arch_rootdir+" archive_baseDir="+archive_baseDir)
		shutil.make_archive(targetname_withoutext, 'zip', arch_rootdir, archive_baseDir)
		shutil.rmtree(targetname_withoutext)
		shutil.move(targetname_withoutext+".zip",targetpath)
		compressed=True
		
	return success,compressed

## the main function
if __name__ == '__main__':
	parser = _HelpParser(description=
	"""Eval given path to given target. 
	 This tool is mainly used to build platform component input-files, in component.mk part of Maketools module.
	 
	 Behaviour depends on the nature of source path and target name :
	 - if the source path is a folder then copy entire folder (and evaluate files)
	 - if the source path is a file then copy/evaluate the file
	 - if the source path is several files or folders (use of '*') then input is a folder containing matching (evaluated) contents
	 - if target name ends with .zip,.tgz,.tar.gz then compress contents in the corresponding format

	Return 1 if sometginh went wrong, 0 otherwise
	""",
	formatter_class=argparse.RawTextHelpFormatter)
	parser.add_argument('targetpath',help="the name (and path) of the expected target",)
	parser.add_argument('sourcepath',help="the file or folder path to be used to build the target",)
	parser.add_argument('method',help="'eval|copy|exec")
	parser.add_argument('dicos',nargs='+', help="dictionary file(s) to be used for templates evaluation",type=_isInputReadable)	
	

	args = parser.parse_args()

	dico,keysorigin=libdictionary.loadDicos(args.dicos)

	success,compressed = evalPath(args.targetpath, args.sourcepath, args.method, dico, keysorigin, args.dicos)
	
	if success==False:
		print("Unable to produce target '"+args.targetpath+"' from '"+args.sourcepath+"'")
		sys.exit(1)
	
	sys.exit(0)


