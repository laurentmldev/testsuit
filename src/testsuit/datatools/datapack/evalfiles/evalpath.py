
##  @package evalpath
# build (evaluate) given target file given source path and evaluation method.
# The datapack tool uses it to generate the components input files from their templates.
#

import argparse
import glob
import os
import shutil
import sys
from pathlib import Path

from testsuit.datatools.datapack.evalfiles import evalfile
from testsuit.datatools.datapack.evalfiles import evalkeys
from testsuit.datatools.datapack.evalfiles import evalincludes
from testsuit.datatools.datapack.evalfiles import libdictionary


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

def evalMultiSourceFiles(method, sourcePaths, targetFolder, dico, keysorigin, dicosNames):
	"""Evaluate the files and folders matching the given ','-separated glob patterns into targetFolder.

	Evaluation errors of the files are printed, but don't fail (see evalSourceFolder).

	:return: success (False only if targetFolder cannot be created), list of the source files
	"""
	try:
		Path(targetFolder).mkdir(parents=True, exist_ok=True)
	except OSError as e:
		print("ERROR: unable to create target folder '"+targetFolder+"' " + str(e))
		return False,[]

	sourceFilesList=[]
	for curPath in sourcePaths.rstrip(",").split(","):
		for source in glob.glob(curPath):
			target=targetFolder+os.sep+os.path.basename(source)
			if os.path.isdir(source):
				_,sourceFiles=evalSourceFolder(method, source, target, dico, keysorigin, dicosNames)
				sourceFilesList+=sourceFiles
			else:
				evalSourceFile(method, source, target, dico, keysorigin, dicosNames)
				sourceFilesList.append(source)

	return True, sourceFilesList


def evalSourceFolder(method, sourceFolder, targetFolder, dico, keysorigin, dicosNames):
	"""Copy the folder (hidden files and empty folders included), then evaluate each of its files.

	Evaluation errors of the files are printed, but don't fail the folder: exploit_runner relies on it
	(its test scenarios have an undefined key).

	:return: True, list of the source files
	"""
	try:
		shutil.copytree(sourceFolder, targetFolder, ignore_dangling_symlinks=True, dirs_exist_ok=True)
	except OSError as e:
		print("ERROR: unable to create target folder '"+targetFolder+"' " + str(e))

	sourceFilesList=[]
	for subdir, dirs, files in os.walk(sourceFolder):
		relPath=subdir.replace(sourceFolder,"")
		for file in files:
			sourceFile=subdir+os.sep+file
			sourceFilesList.append(sourceFile)
			evalSourceFile(method, sourceFile, targetFolder+os.sep+relPath+os.sep+file, dico, keysorigin, dicosNames)

	return True,sourceFilesList



def evalSourceFile(method, sourceFile, targetFile, dico, keysorigin, dicosNames):
	"""Generate targetFile from sourceFile: 'copy' it, or 'eval' its keys and includes.

	:return: success, list of the source files
	"""
	if method=='copy':
		# if files are the same then nothing to do
		if os.path.realpath(sourceFile) != os.path.realpath(targetFile) :
			if not os.path.isfile(sourceFile) :
				print("ERROR: source file unreachable : '"+sourceFile+"', unable to copy it as '"+targetFile+"' ")
				sys.exit(1)
			Path(os.path.dirname(targetFile)).mkdir(parents=True, exist_ok=True)
			# copy2 : keeps file metadata identical to orignal one (same than shell command 'cp -p')
			shutil.copy2(sourceFile,targetFile)

	elif method=='eval':
		lines, usedkeys = evalfile.doFileEvaluation(libdictionary.expandPath(sourceFile), dico, keysorigin, dicosNames,
			partialEval=False, targetFileName=targetFile)
		evalfile.finalizeLines(lines, usedkeys, restoreUnknownKeys=False, outputFile=targetFile)

		errors=[(evalkeys.nbInfinateRecursion,"infinate recursion(s)"),
			(evalincludes.nbNotFoundIncludes,"unresolved file include(s)"),
			(evalkeys.nbUndefined,"undefined key(s)")]
		for nbErrors,errorKind in errors:
			if nbErrors>0:
				print("while evaluating file '"+targetFile+"' : "+str(nbErrors)+" "+errorKind+" detected")
				return False,[]

	elif method=='exec':
		# TODO syscall to run command 'sourceFile' giving it, catch lines from stdout, eval lines and write targetFile
		print("while evaluating file '"+targetFile+"' : 'exec' method not implemented yet (sorry)")
		return False,[]

	else:
		raise Exception(f"unknown file evaluation method '{method}' (copy|eval|exec)")

	return True,[sourceFile]



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

	if os.path.isdir(libdictionary.expandPath(sourcepath)):
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

	if os.path.isfile(libdictionary.expandPath(sourcepath)):
		#print("### file source")
		return "file"

	print("unable to recognize type of given source-path : '"+sourcepath+"'")
	sys.exit(1)

## generate targetpath from sourcepath (a file, a folder, or ','-separated glob patterns)
# and compress the result if targetpath is a .zip/.tgz/.tar.gz
# @return success, compressed
def evalPath(targetpath,sourcepath, method, dico, keysorigin, dicosPaths):

	dicosNames=[libdictionary.expandPath(dicoName) for dicoName in dicosPaths]
	sourcepath=str(sourcepath)
	targetpath=str(targetpath)

	# 1- evaluate target file(s)
	sourceType=getSourcePathType(sourcepath)
	evalFunction={"file": evalSourceFile, "folder": evalSourceFolder, "multi": evalMultiSourceFiles}[sourceType]
	success,_=evalFunction(method, sourcepath, targetpath, dico, keysorigin, dicosNames)

	# 2- compress result if required (i.e. if original source was not already a compressed file)
	compressed=False
	for extensions,archiveFormat in ARCHIVE_FORMATS:
		if targetpath.endswith(extensions) and not sourcepath.endswith(extensions):
			_archive(targetpath, sourcepath, sourceType, extensions, archiveFormat)
			compressed=True
			break

	return success,compressed


# target extensions of archives, and shutil.make_archive format
ARCHIVE_FORMATS=[((".tgz",".tar.gz"),"gztar"), ((".zip",),"zip")]

def _removeExtensions(path, extensions):
	for extension in extensions:
		path=path.replace(extension,"")
	return path

## replace the generated targetpath by an archive of it
def _archive(targetpath, sourcepath, sourceType, extensions, archiveFormat):
	targetname_withoutext=_removeExtensions(targetpath,extensions)
	shutil.move(targetpath,targetname_withoutext)

	if sourceType=="folder":
		# the archive contains the folder, under its source name
		folder_targetname_withoutext=os.path.dirname(targetname_withoutext)+"/"+os.path.basename(_removeExtensions(sourcepath,extensions))
		shutil.move(targetname_withoutext,folder_targetname_withoutext)
		targetname_withoutext=folder_targetname_withoutext
		arch_rootdir=os.path.dirname(targetname_withoutext)
		archive_baseDir=os.path.basename(targetname_withoutext)
	else:
		arch_rootdir=targetname_withoutext
		archive_baseDir=None

	archiveFile=shutil.make_archive(targetname_withoutext, archiveFormat, arch_rootdir, archive_baseDir)
	shutil.rmtree(targetname_withoutext)
	shutil.move(archiveFile,targetpath)

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


