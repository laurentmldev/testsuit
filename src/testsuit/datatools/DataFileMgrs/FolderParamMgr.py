
from __future__ import annotations

import os,re
import pandas as pd
from testsuit.misc.logger import get_logger
import concurrent.futures

import threading
from typing import Callable

from testsuit.datatools import datatoolbox
from  datatools.DataFileMgrs.AFileMgr import AFileMgr
from testsuit.misc.MonitorProgress import MonitorProgress


class FolderParamMgr(AFileMgr):
    """Extract parameters values as Pandas dataframes out of a all files from given folder.
All parameters from various files with same name will be merged as if they were from a single file (if timestamps do not overlap).
This is also here than global clock drifting param (if provided as 'shiftDateSec' constructor arg) is loaded
and then applied within function AFileMgr::finalizeParam() invoked by each concrete sub-ParamMgr."""

    def __init__(self, sourceFileOrFolder: str | list, 
             fileIdx: int=0, supportedExtensions: list | None=["." + fileExt for fileExt in datatoolbox.SUPPORTED_DATAFILE_EXTENSIONS],
             minDateSec: float | None=None,maxDateSec: float | None=None,
             shiftDateSec: float | str | pd.DataFrame | None=None,shiftDateRegex: str | None=None,
             shiftDateInverted: bool=False,
             ignoreCorruptedFile: bool=False,
             continueOnError: bool=False) -> None:
        
        # Normalize to list internally
        if isinstance(sourceFileOrFolder, list):
            self.__sourcePaths = sourceFileOrFolder
            super().__init__(sourceFileOrFolder[0] if sourceFileOrFolder else "", fileIdx)
        else:
            self.__sourcePaths = [sourceFileOrFolder]
            super().__init__(sourceFileOrFolder, fileIdx)
            
        self.__supportedExtensions=supportedExtensions
        self.__filesList=None
        self.__fileMgrs=[]
        self._ignoreCorruptedFile=ignoreCorruptedFile
        self._continueOnError=continueOnError
        self._buildFilesList()
        self._loadFileMgrs()
        self._minDateSec=minDateSec
        self._maxDateSec=maxDateSec
        self._shiftDateSec=shiftDateSec
        self._shiftDateRegex=shiftDateRegex
        self._shiftDateInverted=shiftDateInverted
        self.__clockDriftDf={}

        if supportedExtensions==None:
            self.__supportedExtensions=["." + fileExt for fileExt in datatoolbox.SUPPORTED_DATAFILE_EXTENSIONS]
            #raise Exception("### Received non supported extentions")

    def _buildFilesList(self) -> None:
        """Walk over each provided sources, if its a folder, find usable data files based on file extension."""
        self.__filesList=[]
        self.__fileMgrs=[]

        for source_path in self.__sourcePaths:
            if not os.path.exists(source_path):
                raise FileNotFoundError("Provided path not reachable: '"+source_path+"'")
            
            if os.path.isfile(source_path):
                self.__filesList.append(source_path)
            else:
                for root, subdirs, files in os.walk(source_path):
                    for file in files:
                        for extension in self.getSupportedFileExtensions():
                            if file.lower().endswith(extension):
                                self.__filesList.append(root+os.sep+file)

        if len(self.__filesList)==0:
            raise FileNotFoundError("No file matching provided extensions "+str(self.getSupportedFileExtensions())\
                                                                +" at provided path(s): '"+str(self.__sourcePaths)+"'")

    def _loadFileMgrs(self) -> None:
        """Factory which identifying which FileMgr class for which file."""
        self.__fileMgrs=[]

        fileIdx=0
        corruptedFileErrors={}
        for filename in self.getFilesList():
            try:
                fileMgr=None
                if filename.lower().endswith(".csv") or filename.lower().endswith(".log") or filename.lower().endswith(".txt"):
                    from testsuit.datatools.DataFileMgrs.CsvFileMgr import GetCsvFileType,CsvFileMgr,CsvFileMgrInfluxDb,CsvFileMgrChannels,CsvFileMgrChannelsPcapRecorder
                    csvFileType = GetCsvFileType(filename)
                    if csvFileType=="influxdb":
                        fileMgr=CsvFileMgrInfluxDb(filename,fileIdx)  
                    elif csvFileType=="channels-pcap-recorder":
                        fileMgr=CsvFileMgrChannelsPcapRecorder(filename,fileIdx)  
                    elif csvFileType=="channels":
                        fileMgr=CsvFileMgrChannels(filename,fileIdx)   
                    else:
                        fileMgr=CsvFileMgr(filename,fileIdx)  
                elif filename.lower().endswith(".dxd") or filename.endswith(".d7d"):
                    from testsuit.datatools.DataFileMgrs.DxdFileMgr import DxdFileMgr
                    fileMgr=DxdFileMgr(filename,fileIdx)
                elif filename.lower().endswith(".tdms"):
                    from testsuit.datatools.DataFileMgrs.TdmsFileMgr import TdmsFileMgr
                    fileMgr=TdmsFileMgr(filename,fileIdx)
                elif filename.lower().endswith(".mdf") or filename.lower().endswith(".mf4"):
                    from testsuit.datatools.DataFileMgrs.MdfFileMgr import MdfFileMgr
                    fileMgr=MdfFileMgr(filename,fileIdx)
                elif filename.lower().endswith(".h5") or filename.lower().endswith(".hdf5") :
                    from testsuit.datatools.DataFileMgrs.H5FileMgr import GetH5FileType,H5FileMgr,H5FileMgrDewesoft,H5FileMgrFES,H5FileMgrChannels
                    h5FileType = GetH5FileType(filename)
                    if h5FileType=="Dewesoft":
                        fileMgr=H5FileMgrDewesoft(filename,fileIdx)  
                    elif h5FileType=="FES":
                        fileMgr=H5FileMgrFES(filename,fileIdx)  
                    elif h5FileType=="channels":
                        fileMgr=H5FileMgrChannels(filename,fileIdx)
                    else:
                        fileMgr=H5FileMgr(filename,fileIdx)  
                elif filename.lower().endswith(".influxdbv3.yml") or filename.lower().endswith(".influxdbv3.yaml") :
                    from testsuit.datatools.DataFileMgrs.InfluxDbFileMgr import InfluxDbV3FileMgr
                    fileMgr=InfluxDbV3FileMgr(filename,fileIdx)  
                elif filename.lower().endswith(".influxdbv2.yml") or filename.lower().endswith(".influxdbv2.yaml") :
                    from testsuit.datatools.DataFileMgrs.InfluxDbFileMgr import InfluxDbV2FileMgr
                    fileMgr=InfluxDbV2FileMgr(filename,fileIdx)  
                # Gantner .dat files
                elif filename.lower().endswith(".dat") and re.match(r"^.*_\d+_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d{6}",os.path.basename(filename)):                                
                    from testsuit.datatools.DataFileMgrs.UdbfFileMgr import UdbfFileMgr
                    fileMgr=UdbfFileMgr(filename,fileIdx)
                else:
                    raise Exception("[FolderParamMgr] unhandle file type: "+filename)
                fileIdx+=1
                                
                fileMgr.setContinueOnError(self._continueOnError)
                
                self.__fileMgrs.append(fileMgr)
                
            except Exception as e:
                import traceback
                traceback.print_stack()
                corruptedFileErrors[filename]=str(e)
                        
        if len(corruptedFileErrors)>0:
            errorsStr=""
            for fileName in corruptedFileErrors.keys():
                errorStr=corruptedFileErrors[fileName]
                errorsStr+=f"\n - {fileName}: {errorStr}"
            msg=f"Unable to load {len(corruptedFileErrors)}/{len(self.getFilesList())} file(s): {errorsStr}"
            if self._ignoreCorruptedFile==True:
                get_logger().warning(msg)
            else:
                raise Exception(msg)
        
    def getFileInfo(self) -> list:
        """See AFileMgr."""
        rst=[]
        for fileMgr in self.getFileMgrs():
            rst+=["File: "+fileMgr.getBaseName()]
        return rst
    
    def getFileType(self) -> str:
        """See AFileMgr."""
        rst=""
        for fileMgr in self.getFileMgrs():
            if len(rst)==0:
                rst=fileMgr.getFileType()
            elif fileMgr.getFileType() not in rst:
                rst=","+fileMgr.getFileType()
            
        return rst
    
    def toHtmlTbl(self):
        """See AFileMgr."""
        htmlTbl=""        
        if len(self.getFileMgrs())==1:
            htmlTbl+=self.getFileMgrs()[0].toHtmlTbl()
        else:            
            for fileMgr in self.getFileMgrs():
                htmlTbl += "</table><table style='width:30%;margin-top:1rem' ><tr><th class='secondary' style='width:20%' >"+"Folder File"+"</th><td class='secondary'>"+fileMgr.getBaseName()+"</td></tr>"
                htmlTbl+=fileMgr.toHtmlTbl()

        return htmlTbl
    
    def getNbEntries(self) -> int | None:
        """Cumulate nb entries of each file."""
        rst=0
        for fileMgr in self.getFileMgrs():
            val=fileMgr.getNbEntries()
            if val==None:
                return None
            rst+=val

        return rst
        
    def getFileMgrs(self) -> list:
        return self.__fileMgrs
    
    def dumpTreeHtml(self) -> str:
        """See AFileMgr."""
        rst=""
        for fileMgr in self.getFileMgrs():
            rst+=fileMgr.dumpTreeHtml()
        return rst
    
    def getSelectedFieldsNames(self) -> list:
        """See AFileMgr."""
        rst=[]
        for fileMgr in self.getFileMgrs():
            fileNameStr=fileMgr.getBaseName()+"::" if len(self.getFileMgrs())>0 else ""
            for fieldName in fileMgr.getSelectedFieldsNames() :
                rst.append(fileNameStr+fieldName)
        return rst
    
    def getFieldNames(self) -> list:
        """See AFileMgr."""
        rst=[]
        for fileMgr in self.getFileMgrs():
            fileNameStr=fileMgr.getBaseName()+"::" if len(self.getFileMgrs())>0 else ""
            for fieldName in fileMgr.getFieldNames():
                rst.append(fileNameStr+fieldName)

        return rst
    

    def getSelectedFieldsIdx(self) -> list:
        """See AFileMgr."""
        rst=[]
        curFieldIdxOffset=0
        for fileMgr in self.getFileMgrs():
            for idx in fileMgr.getSelectedFieldsIdx():
                rst+=[idx+curFieldIdxOffset]
            curFieldIdxOffset+=len(fileMgr.getFieldNames())
    
        return rst
     
    def addSelectedFieldIdx(self,fieldIdx: int) -> None:
        """See AFileMgr."""
        curFieldIdxOffset=0
        for fileMgr in self.getFileMgrs():
            if fieldIdx-curFieldIdxOffset<len(fileMgr.getFieldNames()) and fieldIdx>=curFieldIdxOffset:
                fileMgr.addSelectedFieldIdx(fieldIdx-curFieldIdxOffset)            
            curFieldIdxOffset+=len(fileMgr.getFieldNames())        
    
    def clearSelectedFieldsIdx(self) -> None:
        """See AFileMgr."""
        for fileMgr in self.getFileMgrs():
            fileMgr.clearSelectedFieldsIdx()
        
    def getFilesList(self) -> list:
        if self.__filesList==None:
            self.buildFilesList()
        return self.__filesList
    
    def getSupportedFileExtensions(self) -> list:
        return self.__supportedExtensions
    
    def dumpParamsTxt(self,key: str | None=None,
                      item: object=None,
                      depth: int=0,
                      path: str="") -> str:
        """See AFileMgr."""
        rst=""
        for fileMgr in self.getFileMgrs():
            rst+="#### "+fileMgr.getBaseName()+" ####"
            rst+=fileMgr.dumpParamsTxt()
        
        return rst
    
    def mergeParams(self,dfListToMerge: list) -> list:
        """Merge parameters with same name from various source files"""

        if len(dfListToMerge)==1: 
            return dfListToMerge
        
        timeranges={}
        mergedDfMap={}
        for df in dfListToMerge:
            if df is None:
                continue
            #get_logger().debug(f"merging {df.name} [ {df.index[0]:.6f}s , {df.index[-1]:.6f}s ] from "+df.origin)
            assert hasattr(df,"name"), "retrieved df has no name"
            assert hasattr(df,"origin"), f"retrieved df '{df.name}' has no origin"
            
            dfparamName=re.sub(".*::","",datatoolbox.getDfName(df))
            
            if dfparamName not in mergedDfMap:
                mergedDfMap[dfparamName]=df
                # keep timeranges to detect segments overlaps
                if len(df)>0:
                    timeranges[dfparamName]=[{"minDate":df.index[0],"maxDate":df.index[-1]}]
                else:
                    timeranges[dfparamName]=None
            else:
                # ensure there is no segment overlap
                overlapOk=True
                for timerange in timeranges[dfparamName]:
                    if df.index[0]>timerange["minDate"] and df.index[0]<timerange["maxDate"]\
                    or df.index[-1]<timerange["maxDate"] and df.index[-1]>timerange["minDate"]:
                        overlapOk=False
                        break
                    
                if overlapOk:
                    mergedDfMap[dfparamName]=pd.concat([mergedDfMap[dfparamName], df]).sort_index()
                    mergedDfMap[dfparamName].origin=self.getFileName()
                    mergedDfMap[dfparamName].name="merged_"+dfparamName                    
                    timeranges[dfparamName].append({"minDate":df.index[0],"maxDate":df.index[-1]})
                    mergedDfMap[dfparamName]=datatoolbox.squeezeData(mergedDfMap[dfparamName])    
                else:
                    get_logger().warning("parameter found in several files and timestamps overlap: '"+datatoolbox.getDfName(df)+f"':\n"
                                            +f"  Already Found: "+str(timeranges[dfparamName])+f" (from {mergedDfMap[dfparamName].origin})\n"
                                            +f"  Newly Found:   "+str({"minDate":df.index[0],"maxDate":df.index[-1]})+f" (from {df.origin})")
                    mergedDfMap[df.origin+"::"+datatoolbox.getDfName(df)]=df

        return list(mergedDfMap.values())

    def _getClockDrift(self,shiftDateSec: float | str,
                       monitorProgress: MonitorProgress) -> float | str | pd.DataFrame:
        
        # if shiftDateSec is a string, it is considered as a parameter to be loaded.
        if isinstance(shiftDateSec,str):
            shiftDateParamName=shiftDateSec
            if shiftDateParamName not in self.__clockDriftDf:
                try:
                    # do not try to apply shiftDateSec when loading shiftDateSec param ... so we use this _date_processing=False flag  
                    shiftDateDfList=self.findParams(shiftDateParamName,_date_processing=False,silent=True,monitorProgress=monitorProgress)
                    if shiftDateDfList==None:
                        raise Exception(f"Unable to get clock drift data '{shiftDateParamName}' (returned None)")
                    if len(shiftDateDfList)==0:
                        raise Exception(f"No matching param for clock drift data '{shiftDateParamName}'")
                    if len(shiftDateDfList)>1:
                        raise Exception(f"Too many matching params ({len(shiftDateDfList)}) for clock drift data '{shiftDateParamName}', only 1 expected.")
                    if len(shiftDateDfList[0])==0:
                        raise Exception(f"Retrieved clock drift data '{shiftDateParamName}' is empty")
                except Exception as e:
                    raise Exception(f"Error while getting clock drift data '{shiftDateParamName}' : "+str(e))

                self.__clockDriftDf[shiftDateParamName]=shiftDateDfList[0]
                
            return self.__clockDriftDf[shiftDateParamName]
        
        else:
            monitorProgress.set_total_items(1)
            monitorProgress.complete_item("litteral clock drift")
            return shiftDateSec

    def _findParamsInFile(self, 
                          fileMgr: AFileMgr, 
                          fileBasenameRegex: str | None, 
                          fileBasenameExcludeRegex: dict,
                          paramPathRegex: str, 
                          indexPathRegex: str | None, 
                          dryRun: bool, 
                          monitorProgress: MonitorProgress,
                          abortEvent: threading.Event | None, 
                          mergeParams: bool, 
                          callback: Callable | None,
                          minDateSec: float | None, 
                          maxDateSec: float | None, 
                          shiftDateSecValue: float | str | pd.DataFrame | None,
                          shiftDateRegex: str | None, 
                          shiftDateInverted: bool | None, 
                          silent: bool,
                          paramSearchRegex: str) -> list:
        """Find params in a single file (designed to be called from a thread pool)."""
        # do not attempt to find param if it does not match our search/exclude patterns
        if (fileBasenameRegex and not re.search(fileBasenameRegex, fileMgr.getBaseName())):
            monitorProgress.complete_n(1)
            return []

        excludeParamsRegex = []
        for fileRegex in fileBasenameExcludeRegex.keys():
            if re.search(fileRegex, fileMgr.getBaseName()):
                excludeParamsRegex += fileBasenameExcludeRegex[fileRegex]

        subMp = monitorProgress.child(f"[{fileMgr.getFileType()}] {fileMgr.getBaseName()}",renameIfExist=True)

        curCb = callback if not mergeParams else None

        curRst = fileMgr.findParams(paramPathRegex, indexPathRegex, dryRun=dryRun,
                                    monitorProgress=subMp,
                                    abortEvent=abortEvent,
                                    excludeParamsRegex=excludeParamsRegex,
                                    minDateSec=minDateSec, maxDateSec=maxDateSec,
                                    shiftDateSec=shiftDateSecValue,
                                    shiftDateRegex=shiftDateRegex,
                                    shiftDateInverted=shiftDateInverted,
                                    silent=silent, callback=curCb)

        if curRst == None:
            raise LookupError(f"Error while finding params matching include regex '{paramSearchRegex}' and exclude regex '{excludeParamsRegex}' in file '"+fileMgr.getFileName()+"'")

        if abortEvent and abortEvent.is_set():
            raise Exception("Received abort event, params scanning interrupted")

        return curRst
    
    def findParams(self, 
                   paramSearchRegex: str | None,
                   indexPathRegex: str | None=None,
                   dryRun: bool=False,
                   monitorProgress: MonitorProgress | None=None,
                   abortEvent: threading.Event | None=None,
                   excludeParamsRegex: list | str=["/timestamp"], 
                   minDateSec: float | None=None,
                   maxDateSec: float | None=None,
                   shiftDateSec: float | str | pd.DataFrame | None=None,
                   shiftDateRegex: str | None=None, 
                   shiftDateInverted: bool | None=None,
                   _date_processing: bool=True, 
                   silent: bool=False,
                   callback: Callable | None=None, 
                   mergeParams: bool=True) -> list:
        """Retrieve params matching provided path regex in each of handled files.
        Params with same name from various files and not overlapping in dates are merged together in a single Dataframe.
        If a callback is provided and mergeParams==True (default), cb is invoked on merged params (but then require to load all the params before invoking the cb).
        
        See AFileMgr for arguments description.
        
        Param regexes might have to parts separated by '::': first part is a regex for file basename, while second part would be parma name regex (general behaviour),
        for ex: 'some_file::some_param'
        
        :param _date_processing (bool): if false, ignore min/max and date shift. (internal usage only)        
        
        :return: a list of Pandas data series matching requested parameters or list of values returned by callback on each loaded param.
        """
        
        if not minDateSec: minDateSec=self._minDateSec if _date_processing else None
        if not maxDateSec: maxDateSec=self._maxDateSec if _date_processing else None
        if shiftDateSec is None: shiftDateSec=self._shiftDateSec if _date_processing else None
        if not shiftDateRegex: shiftDateRegex=self._shiftDateRegex if _date_processing else None
        if not shiftDateInverted: shiftDateInverted=self._shiftDateInverted if _date_processing else None
        
        if paramSearchRegex==None:
            paramSearchRegex=".*"
        
        m=re.match("((.*)::)?(.*)",paramSearchRegex)
        fileBasenameRegex=m.group(2)
        paramPathRegex=m.group(3)
        
        # a list of param regex for each file regex found
        fileBasenameExcludeRegex={}
        
        if excludeParamsRegex:
            if isinstance(excludeParamsRegex,str):
                excludeParamsRegex=[excludeParamsRegex]
                
            for regex in excludeParamsRegex:
                m=re.match("((.*)::)?(.*)",regex)
                fileRegex=m.group(2)
                paramRegex=m.group(3)
                if fileRegex==None: fileRegex=".*"
                if paramRegex==None: paramRegex=".*"
                
                if fileRegex not in fileBasenameExcludeRegex:
                    fileBasenameExcludeRegex[fileRegex]=[]
                fileBasenameExcludeRegex[fileRegex].append(paramRegex)
                     
        # either df list, or callbacks results
        resultList=[]
   
        nbSteps= len(self.getFileMgrs())
        if shiftDateSec: nbSteps+=1 # + 1 for _getClockDrift
        if mergeParams and callback: nbSteps+=1 # if mergeParams, callback is invoked here after merging
        
        monitorProgress.set_total_items(nbSteps) 
        shiftDateSecValue=self._getClockDrift(shiftDateSec,monitorProgress.child("_getClockDrift")) if shiftDateSec else None
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(
                    self._findParamsInFile,
                    fileMgr,
                    fileBasenameRegex,
                    fileBasenameExcludeRegex,
                    paramPathRegex,
                    indexPathRegex,
                    dryRun,
                    monitorProgress,
                    abortEvent,
                    mergeParams,
                    callback,
                    minDateSec,
                    maxDateSec,
                    shiftDateSecValue,
                    shiftDateRegex,
                    shiftDateInverted,
                    silent,
                    paramSearchRegex,
                ): fileMgr
                for fileMgr in self.getFileMgrs()
            }
            for future in concurrent.futures.as_completed(futures):
                resultList += future.result()
        
        # if mergeParams, callbacks have not been executed yet: we want to run them on mergeParams
        if mergeParams:
            resultList=self.mergeParams(resultList)            
            if callback:
                mpCbMerge=monitorProgress.child("callbacks-after-merge")
                mpCbMerge.set_total_items(len(resultList))
                cbResults=[]
                for df in resultList:
                    if df is not None:                        
                        curMpCbMerge = mpCbMerge.child(f"merged-callback {df.name}")
                        curMpCbMerge.set_total_items(1)
                        cbResults.append(self._invokeCbIfAny(df,callback,curMpCbMerge))
                resultList=cbResults
             
        return resultList

    def loadParams(self, 
                   paramNamesList: list,
                   indexNamesList: list=[],
                   monitorProgress: MonitorProgress | None=None,
                   abortEvent: threading.Event | None=None, 
                   callback: Callable | None=None,
                   minDateSec: float | None=None,
                   maxDateSec: float | None=None, 
                   shiftDateSec: float | str | pd.DataFrame | None=None,
                   shiftDateRegex: str | None=None,
                   shiftDateInverted: bool | None=None,
                   silent: bool=False) -> list:        
        """Retrieve params matching provided path regexes list.
Params with same name from various files (and regex) are merged together in a single Dataframe.
        
        :param paramPath (list): list of exact param names to retrieve (not a regex).
        :param indexPath (list): list of path of explicitly index-param to retrieve (otherwise try its best to automatically get corresponding index)

        :return: a list of Pandas data series matching requested parameters.
"""
                
        if not minDateSec: minDateSec=self._minDateSec
        if not maxDateSec: maxDateSec=self._maxDateSec
        if not shiftDateSec: shiftDateSec=self._shiftDateSec
        if not shiftDateRegex: shiftDateRegex=self._shiftDateRegex
        if not shiftDateInverted: shiftDateInverted=self._shiftDateInverted
        
        dfList=[]
        
        monitorProgress.set_total_items(len(self.getFileMgrs())+1) # + 1 for _getClockDrift
        shiftDateSecValue=self._getClockDrift(shiftDateSec,monitorProgress.child("_getClockDrift"))
        
        for fileMgr in self.getFileMgrs():
            paramsListForThisFile=[]
            indicesForThisFile=[]
            idx=0
            for paramName in paramNamesList:
                m=re.match("((.*)::)?(.*)",paramName)
                fileName=m.group(2)
                paramPath=m.group(3)

                if fileName==None or fileName==fileMgr.getBaseName():
                    paramsListForThisFile+=[paramPath]
                    if indexNamesList:
                        indicesForThisFile+=[indexNamesList[idx]]

                idx+=1

            if len(indicesForThisFile)==0:
                indicesForThisFile=None
            if len(paramsListForThisFile)>0:
                subMp = monitorProgress.child(f"[{fileMgr.getFileType()}] {fileMgr.getBaseName()}")
                dfList += fileMgr.loadParams(paramsListForThisFile,indicesForThisFile,monitorProgress=subMp,abortEvent=abortEvent,
                                                                        minDateSec=minDateSec,maxDateSec=maxDateSec, callback=callback,
                                                                        shiftDateSec=shiftDateSecValue,
                                                                        shiftDateRegex=shiftDateRegex,shiftDateInverted=shiftDateInverted,
                                                                        silent=silent)
            else:
                monitorProgress.complete_item(f"[{fileMgr.getFileType()}] {fileMgr.getBaseName()}")
                
            if abortEvent and abortEvent.is_set():
                raise Exception("Received abort event, params scanning interrupted")            

        if dfList==None:
            raise LookupError("No param matching given list'"+str(paramNamesList)+"' in '"+self.getFileName()+"'")
        
        return self.mergeParams(dfList)

