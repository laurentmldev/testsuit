
import sys,os,getpass,stat,traceback
from pathlib import Path

import yaml

import subprocess

from testsuit.datatools.datapack.evalfiles.libdictionary import loadDicos
from testsuit.datatools.datapack.evalfiles.libdictionary import getkeyval
from testsuit.datatools.datapack.evalfiles.evalfile import evalfile,evalkeys,evalincludes,finalizeLines
from testsuit.datatools.datapack.evalfiles.evalpath import evalPath

from testsuit.datatools.datapack.GitImporter import GitImporter
from testsuit.datatools.datapack.importers import create_data_importer
from testsuit.plugins import load_plugins

from testsuit import misc
from testsuit.misc.files import checksumFolder

import socket
from datetime import datetime

import shutil
from colorama import Fore, Style
from colorama import init as colorama_init
colorama_init()

PROCESSORS_PATH=Path(os.path.dirname(os.path.realpath(__file__))+os.sep+"processors")
if not PROCESSORS_PATH.exists():
    raise Exception(f"processors folder not reachable: {PROCESSORS_PATH}")

def _yaml_root(data):
    """Extract the single root dictionary from a YAML file structure."""
    if isinstance(data, dict) and len(data) == 1:
        return list(data.values())[0]
    return data

def runScript(path,argsTbl):

    cmdsTbl=argsTbl

    if path.name.endswith(".py"):
        cmdsTbl=["python",str(path)]+cmdsTbl
    elif path.name.endswith(".sh"):        
        cmdsTbl=["bash",str(path)]+cmdsTbl

    #print("running cmd: "+str(cmdsTbl))
    
    try:
        rst = subprocess.run(cmdsTbl,check=False)
        if rst.returncode!=0:
            print(Fore.RED+"ERROR: error (exit status = "+str(rst.returncode)+") while running command :"+str(path)+"'"+Style.RESET_ALL+", with args:")
            print(argsTbl)
            sys.exit(1)
    except Exception as e:
        traceback.print_stack()
        print(Fore.RED+"ERROR: unable to run cmd '"+str(path)+"' :"+str(e)+Style.RESET_ALL)
        sys.exit(1)

    colorama_init() # call to subprocess breaks shell colors. TODO: find better wau to fix it ...
    

def evalFile(srcFile,targetFile,dicosListTbl,partialOk=True):

    lines,usedkeys=evalfile(srcFile, dicosListTbl,partialEval=partialOk)

    if lines is None:
        print(Fore.RED+"ERROR: unable to eval file : '"+srcFile+"'"+Style.RESET_ALL)        
        sys.exit(3)
    
    finalizeLines(lines, usedkeys, outputFile=targetFile)

    if evalkeys.nbInfinateRecursion>0 or evalincludes.nbCircularRecursions>0:
        print(Fore.RED+"ERROR: infinate recursion while evaluating file : '"+srcFile+"'"+Style.RESET_ALL)
        sys.exit(2)
    if evalincludes.nbNotFoundIncludes>0:
        print(Fore.RED+"ERROR: unreachable include while evaluating file : '"+srcFile+"'"+Style.RESET_ALL)
        sys.exit(1)
    if not partialOk and evalkeys.nbUndefined>0:
        print(Fore.RED+"ERROR: some undefined keys while evaluating file : '"+srcFile+"'"+Style.RESET_ALL)
        sys.exit(1)

    #print(Fore.LIGHTBLACK_EX+"[Generated "+targetFile+"]"+Style.RESET_ALL)
            
# expand given file path, and if options given, also evaluate contents
# against given dictionaries
# Optionally eval target file, putting result file into 'evalTargetFolder' folder
def expandPath(path,evalTargetFolder=None,evalDicosList=None,evalPartialOk=False):

    respath=misc.files.expandPath(path)    
    if evalTargetFolder is not None:
        assert(evalDicosList is not None)
        evalFileName=evalTargetFolder+os.sep+os.path.basename(respath)
        #print("         3> "+respath)        
        evalFile(respath,evalFileName,evalDicosList,partialOk=evalPartialOk)
        return evalFileName
    
    return respath
    
def runDataprocessor(arguments, processorName):

    if not isinstance(arguments, list):
        arguments = [arguments]

    # retrieve some potential specific extra arguments for the processor
    if len(processorName.split(" ")) > 1:
        procNameAndArgs = processorName.split(" ")
        processorName = procNameAndArgs.pop(0)
        extraArgs = procNameAndArgs
        arguments += extraArgs
    # arguments: first argument must be targetFolder, other ones are free
    print(Fore.LIGHTBLACK_EX + "        [POSTPROCESS] " + processorName + Style.RESET_ALL)

    # Direct import and execution of known Python processors
    processorModule = processorName.replace(".py", "")
    knownProcessors = ["filterkeys", "finalize", "replace", "basickeys"]

    if processorModule in knownProcessors:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            processorModule, PROCESSORS_PATH / processorName
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main(arguments)
    else:
        runScript(PROCESSORS_PATH / processorName, arguments)

    
def normalizeDatasetPath(path):
    normalizedPath= path.replace("/",".").replace("@","_").replace(" ","_")
    if normalizedPath.startswith("."):
        normalizedPath=normalizedPath[1:]
    if normalizedPath.endswith("."):
        normalizedPath=normalizedPath[:-1]

    return normalizedPath

def loadDataset(repoPath,datasourceImporter,datasourcePath,datasetNode,contextFileHdl,nocheck=False):

    datasetId=datasetNode["id"]
    datasetPath=datasetNode["path"]
    datasetPostscript=datasetNode.get("postprocess", "").split(",")   
    datasetFullLocalPath=repoPath+os.sep+datasetId+os.sep+normalizeDatasetPath(datasetPath)
    Path(repoPath+os.sep+datasetId).mkdir(parents=True, exist_ok=True)

    if len(datasetPath)==0:
        contextFileHdl.write("      [DATASET] "+datasetId+" -> skipped\n")
        print(Fore.YELLOW+"     [DATASET] "+Style.RESET_ALL+datasetId+Fore.LIGHTBLACK_EX+" -> "+Style.RESET_ALL+"'' ["+Fore.RED+"skipped"+Style.RESET_ALL+"]")
        return []

    contextFileHdl.write("      [DATASET] "+datasetId+" -> "+datasetPath+"\n")

    try:
        dataImporter=create_data_importer(datasourceImporter,datasetFullLocalPath,datasourcePath,datasetPath)
    except KeyError as e:
        print(Fore.RED+"ERROR: "+str(e.args[0])+" for dataset '"+datasetId+"'"+Style.RESET_ALL)
        sys.exit(1)

    if os.access(datasetFullLocalPath,os.X_OK):   
        print(Fore.LIGHTBLUE_EX+"     [DATASET] "+Style.RESET_ALL+datasetId+Fore.LIGHTBLACK_EX+" -> "+Fore.CYAN+datasetPath+Style.RESET_ALL +" ["+Fore.GREEN+"found"+Style.RESET_ALL+"]")
        if not dataImporter.checkVersion(datasetPath):
            if nocheck:
                print(Fore.YELLOW+"WARNING: for dataset '"+datasetId+"', could not confirm compliance with expected version '"+datasetPath+"'"+Style.RESET_ALL+", ignored.")
                contextFileHdl.write("WARNING: for dataset '"+datasetId+"', could not confirm compliance with expected version '"+datasetPath+"'\n")
                changesList=dataImporter.getChanges()
                if len(changesList)>0:
                    print("Following unexpected changes have been detected:")
                    for change in changesList:
                        print("  - "+change)
            else:
                print(Fore.RED+"ERROR: for dataset '"+datasetId+"', could not confirm compliance with expected version '"+datasetPath+"'"+Style.RESET_ALL)
                sys.exit(1)
        
    else:
        print(Fore.LIGHTBLUE_EX+"     [DATASET] "+Style.RESET_ALL+datasetId+Fore.LIGHTBLACK_EX+" -> "+Fore.CYAN+datasetPath+Style.RESET_ALL +" ["+Fore.LIGHTYELLOW_EX+"importing"+Style.RESET_ALL+"]")
        if not dataImporter.retrieve():
            print(Fore.RED+"ERROR: unable to import data, aborting."+Style.RESET_ALL)
            sys.exit(1)
        
        for postscriptName in datasetPostscript:
            if len(postscriptName)>0:
                runDataprocessor(datasetFullLocalPath,postscriptName)
         
    return [datasetFullLocalPath+os.sep+"dataset.dico"]

def loadDatasource(repoPath,datasourceNode,contextFileHdl,nocheck=False):

    datasourceId=datasourceNode["id"]
    datasourceName=datasourceNode["name"]
    datasourceImporter=datasourceNode["importer"]
    datasourcePath=datasourceNode["path"]
    print(Fore.BLUE+" [DATASOURCE] "+Style.RESET_ALL+datasourceId+Fore.LIGHTBLACK_EX+" ("+datasourceImporter+") -> "+Fore.CYAN+datasourcePath+Style.RESET_ALL)
    contextFileHdl.write("  [DATASOURCE] "+datasourceId+" ("+datasourceImporter+") -> "+datasourcePath+"\n")
    dicosList=[]
    # import related datasets (if missing)
    for dataset in datasourceNode.get('dataset', []):
        dicosList+=loadDataset(repoPath,datasourceImporter,datasourcePath,dataset,contextFileHdl,nocheck)

    return dicosList

def loadRepo(repositoryNode,contextFileHdl,nocheck=False):

    # create folder
    repoId=repositoryNode["id"]
    repoName=repositoryNode["name"]
    repoPath=expandPath(repositoryNode["path"])
    print("\n"+Fore.BLUE+"[REPO] "+Style.RESET_ALL+repoId+Fore.LIGHTBLACK_EX+" -> "+Fore.CYAN+repoPath+Style.RESET_ALL)
    contextFileHdl.write("[REPO] "+repoId+" -> "+repoPath+"\n")

    Path(repoPath).mkdir(parents=True, exist_ok=True)
    
    dicosList=[]

    # import related datasources (if missing)
    for datasource in repositoryNode.get('datasource', []):
        dicosList+=loadDatasource(repoPath,datasource,contextFileHdl,nocheck)

    return dicosList

def getEvaluatedFileName(fileName):
    return os.path.dirname(fileName)+os.sep+"."+os.path.basename(fileName)

def findRefFile(testdefFolder,targetCompsFolder,setupFolder,templateFileName,optional):

    #print("testdefFolder="+testdefFolder)
    #print("targetCompsFolder="+targetCompsFolder)
    #print("setupFolder="+setupFolder)
    #print("templateFileName="+templateFileName)

    templateFileNameOri=templateFileName
    if '*' in templateFileName:
        templateFileName=templateFileName.split('*')[0]
        
    datapackLocation=expandPath(targetCompsFolder+os.sep+"..")

    # relative from testdef
    if os.access(testdefFolder+os.sep+templateFileName,os.R_OK): 
        return testdefFolder+os.sep+templateFileNameOri
    # relative from setup folder
    if os.access(setupFolder+os.sep+templateFileName,os.R_OK):
        return setupFolder+os.sep+templateFileNameOri
    # relative from testdef folder
    if os.access(datapackLocation+os.sep+templateFileName,os.R_OK):
        return datapackLocation+os.sep+templateFileNameOri
    # absolute path or relative from PWD
    if  os.access(expandPath(templateFileName),os.R_OK): 
        return expandPath(templateFileNameOri)
    
    if optional==True:
        return None
    print(Fore.RED+"ERROR: template file required in component conf is not reachable: '"+templateFileName+"'"+Style.RESET_ALL)
    print("Tried following solutions:")
    print(" - relative path from testdef folder: "+testdefFolder)
    print(" - relative path from setup folder: "+setupFolder)
    print(" - relative path from datapack location: "+datapackLocation)
    print(" - absolute path or relative to current user directory")
    sys.exit(1)

def genDataPackComponent(targetCompsFolder,componentNode,componentTypeDesc,dico,keysorigin,dicosList,setupFolder,testdefFolder,nocheck):
    
    compId=componentNode["id"]
    compName=componentNode["name"]
    compType=componentNode["type"]
    compConfig=componentNode["configuration"]

    print(Fore.LIGHTBLUE_EX+"\n["+compId+"] "+Fore.LIGHTBLACK_EX+" config '"+Style.RESET_ALL+compConfig+Fore.LIGHTBLACK_EX+"'"+Style.RESET_ALL)

    Path(targetCompsFolder+os.sep+compId).mkdir(parents=True, exist_ok=False)
    Path(targetCompsFolder+os.sep+compId+os.sep+"input").mkdir(parents=True, exist_ok=False)
    Path(targetCompsFolder+os.sep+compId+os.sep+"output").mkdir(parents=True, exist_ok=False)

    compConfigNode = next((c for c in componentTypeDesc.get('configuration', []) if c["id"] == compConfig), None)
    if compConfigNode is None:
        print(Fore.RED+"ERROR: no such config '"+compConfig+"' for component type '"+compType+"."+Style.RESET_ALL)
        for config in componentTypeDesc.get('configuration', []):
            print(config)
        sys.exit(1)
   
    for inputFile in compConfigNode.get('input', []):
        targetPath=targetCompsFolder+os.sep+compId+os.sep+"input"+os.sep+inputFile["value"]
        templateFileSearch=inputFile["template"]
        method=inputFile["method"]
        postscripts=[]
        if "postprocess" in inputFile:
            postscripts=inputFile["postprocess"].split(",")
        optional=False
        if inputFile.get("optional", False):
            optional=True
        templateFile=findRefFile(testdefFolder,targetCompsFolder,setupFolder,templateFileSearch,optional)        
        
        if templateFile is None:
            assert(optional==True)
            print(Fore.YELLOW+"    [OPTIONAL] "+Fore.LIGHTBLACK_EX+templateFileSearch+Style.RESET_ALL+" --> "\
                +Fore.LIGHTBLACK_EX+compId+os.sep+"input/"+inputFile["value"]+" "+Fore.LIGHTRED_EX+"X"+Style.RESET_ALL)
            continue
            
        if method=="copy":
            print(Fore.MAGENTA+"    [COPY] "+Fore.LIGHTBLACK_EX+templateFile+Style.RESET_ALL+" --> "\
                +Fore.WHITE+compId+os.sep+"input"+os.sep+inputFile["value"]+" "+Fore.LIGHTGREEN_EX+'\u2713'+Style.RESET_ALL)
        elif method=="eval":
            print(Fore.LIGHTMAGENTA_EX+"    [EVAL] "+Fore.LIGHTBLACK_EX+templateFile+Style.RESET_ALL+" --> "\
                +Fore.WHITE+compId+os.sep+"input"+os.sep+inputFile["value"]+" "+Fore.LIGHTGREEN_EX+'\u2713'+Style.RESET_ALL)
        elif method=="exec":
            print(Fore.LIGHTMAGENTA_EX+"    [EXEC] "+Fore.LIGHTBLACK_EX+templateFile+Style.RESET_ALL+" --> "\
                +Fore.WHITE+compId+os.sep+"input"+os.sep+inputFile["value"]+" "+Fore.LIGHTGREEN_EX+'\u2713'+Style.RESET_ALL)
        else:
            raise Exception(f"unknown file evaluation method '{method}' (copy|eval|exec)")
        
        success,compressed=evalPath(targetPath,templateFile, method, dico, keysorigin, dicosList)                
        if not success:
            print(Fore.RED+"ERROR: unable to generate properly datapack entries for '"+compId+"', sorry."+Style.RESET_ALL)
            print("Maybe ensure that your setup files are consistent ("+setupFolder+") and all required keys are available in used dictionaries:")
            print(dicosList)
            sys.exit(1)

        for postscriptName in postscripts:
            if len(postscriptName)>0:
                runDataprocessor([targetPath],postscriptName)
    


def finalizeDatapack(setupId, testId, userId, timestamp, datapackFolder, datapacks,nochecks=False):

    datatpackName=testId+"_"+setupId+"_"+userId+"_"+timestamp
    if nochecks==True:
        datatpackName=datatpackName+"_DRAFT"
    
    print(Fore.LIGHTBLACK_EX+"[generating datapack folder]"+Style.RESET_ALL)
    shutil.copytree(datapackFolder,datapacks+os.sep+datatpackName)    
    print(Fore.LIGHTBLACK_EX+"[computing checksums and dups]"+Style.RESET_ALL)
    checksumFolder(datapacks+os.sep+datatpackName)
    #runScript(CHECKSUM_TOOL,["-u","-s", datapacks+os.sep+datatpackName])
    shutil.copytree(datapacks+os.sep+datatpackName,datapacks+os.sep+datatpackName+os.sep+".ori")
    shutil.move(datapacks+os.sep+datatpackName+".sha256",datapacks+os.sep+datatpackName+os.sep+".inputs.sha256")    

    finalFileName=shutil.make_archive(datapacks+os.sep+datatpackName, 'zip', root_dir=datapacks,base_dir=datatpackName)

    try:
        shutil.rmtree(datapacks+os.sep+datatpackName,onerror=del_rw)
    except OSError:
        print(Fore.YELLOW+f"WARNING: unable to clean temporary folder {datapacks+os.sep+datatpackName}."+Style.RESET_ALL)            
    
    return finalFileName

def datapack(testdef_file,nocheck=False):

    # importers declared by external libraries
    load_plugins()

    testdefFolder=os.path.dirname(testdef_file)
    if len(testdefFolder)==0:
        testdefFolder="."
    targetDatapackFolder=testdefFolder+os.sep+".current_datapack"
    datapackContextFile=targetDatapackFolder+os.sep+"context.log"

    # create/clean temporary datapack folder
    if os.access(targetDatapackFolder,os.R_OK): 
        print(Fore.LIGHTBLACK_EX+"[cleaning existing '"+targetDatapackFolder+"' folder]"+Style.RESET_ALL)
        shutil.rmtree(targetDatapackFolder,onerror=del_rw)        
        
    Path(targetDatapackFolder).mkdir(parents=True, exist_ok=True)
    
    # create folder where to store config files evaluated based on provided dicos
    # (it is inside datapack so that we can keep track of with which data exactly it has been generated)
    processingFolder=targetDatapackFolder+os.sep+".processing"
    if os.access(processingFolder,os.R_OK): 
        shutil.rmtree(processingFolder,onerror=del_rw)
    Path(processingFolder).mkdir(parents=True, exist_ok=False)  
    print(Fore.LIGHTBLACK_EX+"[processing files located in '"+processingFolder+"']"+Style.RESET_ALL)

    # list of all dicos involved in files evaluation
    # this list is enriched as the config files are loaded
    dicosList=[]

    # load original test definition YAML file to get dicos list
    # import testdef dicos data
    with open(testdef_file) as f:
        testdefData = yaml.safe_load(f)
    testdefRoot = _yaml_root(testdefData)
    for dicofileNode in testdefRoot.get('dictionary', []):
        dicoFilePath=dicofileNode    
        dicoFullPath=expandPath(testdefFolder+os.sep+dicoFilePath)
        if not os.access(dicoFullPath,os.R_OK): 
            print(Fore.RED+"ERROR: Dico file from test dev '"+testdef_file+"'  not reachable: '"+dicoFullPath+"'"+Style.RESET_ALL)
            return False
        dicosList+=[expandPath(dicoFullPath)]

    # evaluate testdef file based on dicos listed inside
    evaluatedTestDefFilePath=processingFolder+os.sep+os.path.basename(testdef_file)
    evalFile(testdef_file,evaluatedTestDefFilePath,dicosList)

    # load contents of evaluated testdef file
    with open(evaluatedTestDefFilePath) as f:
        testdefData = yaml.safe_load(f)
    testdefRoot = _yaml_root(testdefData)

    # read basic infos of testdef    
    testId=testdefRoot["id"]
    testName=testdefRoot["name"]
    setupFile=expandPath(testdefFolder+os.sep+testdefRoot["setup_file"])
    setupFolder=os.path.dirname(setupFile)
    setupConfig=testdefRoot["setup_config"]
    testDate=datetime.now()

    # start log file summarizing testdef infos
    contextFileHdl=open(datapackContextFile, "w+")
    contextFileHdl.write("--- Datapack Generation Context ---"+"\n")
    contextFileHdl.write("Date: "+testDate.strftime("%Y-%m-%d %H:%M:%S")+"\n")
    contextFileHdl.write("User: "+getpass.getuser()+"\n")
    contextFileHdl.write("Host: "+socket.gethostname()+"\n")
    contextFileHdl.write("PWD: "+os.getenv("PWD","")+"\n")
    contextFileHdl.write("Test Procedure: "+expandPath(testdef_file)+"\n")
    contextFileHdl.write("setup: "+expandPath(setupFile)+"\n")
    contextFileHdl.write("tools: "+expandPath(__file__)+"\n")    
    contextFileHdl.write("nocheck: "+str(nocheck)+"\n")        
    contextFileHdl.write("-----------------------------------"+"\n\n")

    # create a dictionary with testdef infos, to be potentially used by other 
    # files to be evaluated later
    testdefDico=processingFolder+os.sep+"testdef.dico"
    testdefDicoFileHdl=open(testdefDico, "w+")
    testdefDicoFileHdl.write("# dico generated by msp_datapack.py tool, from datapack.xml"+"\n")
    testdefDicoFileHdl.write("testdef.id="+testId+"\n")
    testdefDicoFileHdl.write("testdef.path="+expandPath(testdefFolder)+"\n")
    testdefDicoFileHdl.write("testdef.name="+testName+"\n")
    testdefDicoFileHdl.write("testdef.setup.file="+setupFile+"\n")
    testdefDicoFileHdl.write("testdef.setup.path="+setupFolder+"\n")
    testdefDicoFileHdl.write("testdef.setup.conf="+setupConfig+"\n")
    testdefDicoFileHdl.write("testdef.timestamp="+testDate.strftime("%Y-%m-%d %H:%M:%S")+"\n")
    testdefDicoFileHdl.write("testdef.pwd="+os.getenv("PWD","")+"\n")
    testdefDicoFileHdl.write("testdef.host="+socket.gethostname()+"\n")
    testdefDicoFileHdl.write("testdef.tools="+expandPath(__file__)+"\n")
    testdefDicoFileHdl.write("testdef.nocheck="+str(nocheck)+"\n")
    testdefDicoFileHdl.close()
    dicosList+=[testdefDico]

    # ensure testdef and Setup folders are clean and tagged
    if nocheck==True:
        print(Fore.YELLOW+"\n/!\\ *** WARNING *** 'Ignore Checks' options activated : checks and tags inconsistencies will be ignored /!\\"+"\n"+Style.RESET_ALL)
        print(Fore.LIGHTBLACK_EX+"\n"+"Test Definition folder: "+Style.RESET_ALL+testdefFolder)
        if not GitImporter(testdefFolder).isClean():
            print(Fore.YELLOW+"WARNING: testdef folder is not a clean tag: '"+testdefFolder+"'"+Style.RESET_ALL)
            contextFileHdl.write("WARNING: testdef folder is not a clean tag\n")
        print(Fore.LIGHTBLACK_EX+"\n"+"Setup folder: "+Style.RESET_ALL+setupFolder)
        if not GitImporter(setupFolder).isClean():
            print(Fore.YELLOW+"WARNING: Setup folder is not a clean tag: '"+setupFolder+"'"+Style.RESET_ALL)
            contextFileHdl.write("WARNING: Setup folder is not a clean tag\n")
    else:
        print(Fore.LIGHTBLACK_EX+"\n"+"Test Definition folder: "+Style.RESET_ALL+testdefFolder)
        if not GitImporter(testdefFolder).isClean():
            print(Fore.RED+"ERROR: aborting, env is not clean. Try again with option '--nocheck' to force."+Style.RESET_ALL)
            return False
        print(Fore.LIGHTBLACK_EX+"\n"+"Setup folder: "+Style.RESET_ALL+setupFolder)
        if not GitImporter(setupFolder).isClean():
            print(Fore.RED+"ERROR: aborting, env is not clean. Try again with option '--nocheck' to force."+Style.RESET_ALL)
            return False
    

    # test access to setup file
    if not os.access(setupFile,os.R_OK): 
        print(Fore.RED+"ERROR: Datapacks Setup file from test dev '"+testdef_file+"' not reachable: '"+setupFile+"'"+Style.RESET_ALL)
        return False
    
    # preeval Setup file and load dicos listed in it
    setupFileEval=expandPath(setupFolder+os.sep+os.path.basename(setupFile),\
                                evalTargetFolder=processingFolder,\
                                evalDicosList=dicosList,\
                                evalPartialOk=True)    
    try:
        with open(setupFileEval) as f:
            setupData = yaml.safe_load(f)
        setupXmlRoot = _yaml_root(setupData)
    except Exception as e:
        print(Fore.RED+"ERROR: while parsing file '"+setupFile+"' : "+str(e)+Style.RESET_ALL)
        sys.exit(1)

    for dicofileNode in setupXmlRoot.get('dictionaries', []):
        dicoFilePath=dicofileNode
        if not os.access(expandPath(setupFolder+os.sep+dicoFilePath),os.R_OK): 
            print(Fore.RED+"ERROR: Dico file from datapack config '"+str(setupXmlRoot)+"' not reachable: '"+dicoFilePath+"'"+Style.RESET_ALL)
            return False
        expandPath(setupFolder+os.sep+dicoFilePath,evalTargetFolder=processingFolder,evalDicosList=dicosList)
        dicosList+=[expandPath(setupFolder+os.sep+dicoFilePath,evalTargetFolder=processingFolder,evalDicosList=dicosList) ]
    
    # reevaluate setup file with also local dicos listed inside it
    setupFileEval=expandPath(setupFolder+os.sep+os.path.basename(setupFile),\
                                        evalTargetFolder=processingFolder,\
                                        evalDicosList=dicosList,\
                                        evalPartialOk=True)    
    
    print(Fore.GREEN+"\n *** [1] Building Baseline ***"+Style.RESET_ALL)
    contextFileHdl.write("--------------Baseline-------------"+"\n")
    # each repo has its "dataset.dico" file generated during import process
    # path to this dico is returned by the 'loadRepo' function
    for datarepository in setupXmlRoot.get('datarepositories', []):      
        dicosList+=loadRepo(datarepository,contextFileHdl,nocheck)
    contextFileHdl.write("-----------------------------------"+"\n\n")

    # reevaluate setup file with also baseline dicos listed inside it
    setupFileEval=expandPath(setupFolder+os.sep+os.path.basename(setupFile),evalTargetFolder=processingFolder,evalDicosList=dicosList)    
    with open(setupFileEval) as f:
        setupData = yaml.safe_load(f)
    setupXmlRoot = _yaml_root(setupData)        
    setupId=setupXmlRoot["setup_id"]

    colorama_init() # call to subprocess breaks shell colors. TODO: find better way to fix it ...
    print(Fore.GREEN+"\n *** [2] Generating Files for Setup '"+Fore.LIGHTGREEN_EX+setupId+Fore.GREEN+"' in conf '"+Fore.LIGHTGREEN_EX+setupConfig+Fore.GREEN+"' ***"+Style.RESET_ALL)
    contextFileHdl.write("--------------Setup-------------"+"\n")
    contextFileHdl.write("setup_id: "+setupId+"\n")
    contextFileHdl.write("setup_conf: "+setupConfig+"\n")
    contextFileHdl.write("-----------------------------------"+"\n\n")
        
    # evaluate and load components YAML files based on dictionaries contents
    componentsDefs={}
    for cmptDefNode in setupXmlRoot.get('components', {}).get('component_def', []):
        componentsDefs[cmptDefNode["type"]]=cmptDefNode
    
    # generate corresponding data       
    configs = setupXmlRoot.get('configurations', {}).get('configuration', [])
    setupConfigNode = next((c for c in configs if c["id"] == setupConfig), None)
    if setupConfigNode is None:
        print(Fore.RED+"ERROR: no such config '"+setupConfig+"' in given file '"+setupFile+"'."+Style.RESET_ALL)
        for config in configs:
            print(config)
        return False    
    
    # loading dicos contents, expected by our "evalTargetFolder" tool (called by genDataPackComponent)
    dico,keysorigin=loadDicos(dicosList)
    # here are actually generated all input files of our datapack, for each declared component
    for component in setupConfigNode.get('component', []):
        compType=component["type"]
        if compType not in componentsDefs:
            print(Fore.RED+"ERROR: no such component type '"+compType+"' declared. Please check your components files listed in setup '"+setupFile+"'."+Style.RESET_ALL)
            return False
        genDataPackComponent(targetDatapackFolder,component,componentsDefs[compType],dico,keysorigin,dicosList,setupFolder,testdefFolder,nocheck)

    contextFileHdl.close()

    
    print(Fore.GREEN+"\n *** [3] Packaging ***\n"+Style.RESET_ALL)
        
    datapacksPath=getkeyval("datapacks.path",dico)
    if not datapacksPath:
        print(Fore.RED+"ERROR: key 'datapacks.path' undefined, don't know where to generate resulting datapack"+Style.RESET_ALL)
        return False

    datapacks_path=expandPath(datapacksPath)

    datapackFullName=finalizeDatapack(setupId, testId,getpass.getuser(), testDate.strftime("%Y%m%d_%H%M%S"), targetDatapackFolder, datapacks_path,nochecks=nocheck)
    
    print(Fore.LIGHTBLACK_EX+"\nGenerated Datapack: "+Style.RESET_ALL+datapackFullName+"\n")
    
    if nocheck==True:
        print(Fore.YELLOW+"Be careful, option 'nocheck' was activated, this datapack shall not be used for a production run."+Style.RESET_ALL)
    
    print(Fore.LIGHTGREEN_EX+"Done"+Style.RESET_ALL+", bye bye ^^\n")

    return True

# from https://stackoverflow.com/questions/2656322/shutil-rmtree-fails-on-windows-with-access-is-denied
def del_rw(action, name, exc):
    os.chmod(name, stat.S_IWRITE)
    shutil.rmtree(name)#,onerror=del_rw)