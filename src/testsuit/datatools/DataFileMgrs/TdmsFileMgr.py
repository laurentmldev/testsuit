
import threading
from concurrent.futures import ThreadPoolExecutor,wait
from typing import Any
from collections.abc import Callable

import numpy as np 
import pandas as pd

from nptdms import TdmsFile
from nptdms.tdmsinfo import tdmsinfo
from nptdms.timestamp import TdmsTimestamp
from unidecode import unidecode

from testsuit.misc.MonitorProgress import MonitorProgress
from testsuit.datatools.DataFileMgrs.AFileMgr import AFileMgr

NB_MAX_WORKERS=6

def normalizeColumnName(name: str) -> str:
    """Turn a TDMS channel path into a plain ASCII column name (ex: "/'Grp 1'/'Temp (°C)'" -> "Grp_1___Temp__oC")."""
    normalizedColName=unidecode(name.replace(" ","_") \
                    .replace("(","_").replace(")","_") \
                    .replace("/","_").replace("\\","_") \
                    .replace("°","o") \
                    .replace("'","_") \
                    .replace("*","_") \
                    .replace("$","_") \
                    .replace("^","_") \
                    .replace("[","_").replace("]","_") \
                    .replace("-","_") \
                    .replace(".","_") \
                    .replace("&","n") \
                    .replace(":","_") \
                    .replace("=","_") \
                    .replace("%","pct") \
                        )

    while normalizedColName.startswith("_"):
        normalizedColName=normalizedColName[1:]

    while normalizedColName.endswith("_"):
        normalizedColName=normalizedColName[:-1]

    return normalizedColName

def _absoluteTimeTrack(channel) -> np.ndarray:
    """Same as nptdms channel.time_track(absolute_time=True), but a missing 'wf_start_offset'
    or 'wf_start_time' property defaults to 0 (with a warning) instead of raising KeyError."""
    try:
        increment = channel.properties['wf_increment']
    except KeyError:
        raise KeyError("Object does not have time properties available.")

    offset = channel.properties.get('wf_start_offset')
    if offset is None:
        offset = 0
        print(f"WARNING: no 'wf_start_offset' property in TDMS object '{channel.path}'")

    relative_time = np.linspace(offset, offset + (len(channel) - 1) * increment, len(channel))

    start_time = channel.properties.get('wf_start_time')
    if start_time is None:
        start_time = 0
        print(f"WARNING: no 'wf_start_time' property in TDMS object '{channel.path}'")
    if isinstance(start_time, TdmsTimestamp):
        start_time = start_time.as_datetime64('ns')

    return start_time + (relative_time * 1e9).astype("timedelta64[ns]")

def channelsAsDataframe(tdmsFile: TdmsFile, normalizedNames: list[str]) -> pd.DataFrame:
    """DataFrame of the channels whose normalized path is in normalizedNames, indexed by absolute time,
    with columns named by normalized path."""
    columns = {}
    for group in tdmsFile.groups():
        for channel in group.channels():
            columnName = normalizeColumnName(channel.path)
            if columnName in normalizedNames:
                data = channel[:]
                if np.issubdtype(data.dtype, np.dtype('void')) and len(data.dtype) == 0:
                    data = np.empty(0, dtype='float64')
                columns[columnName] = pd.Series(data=data, index=_absoluteTimeTrack(channel))
    return pd.DataFrame.from_dict(columns)

class TdmsFileMgr(AFileMgr):
    def __init__(self, filename: str, fileIdx: int) -> None:
        super().__init__(filename,fileIdx)
        self.__tdmsfile=TdmsFile(filename,read_metadata_only=True)
        self.showWarningDateOrigin=False

    def showTdmsInfo(self):
        tdmsinfo(self.getFileName(),show_properties=True)

    def getFileType(self) -> str:
        return "tdms"
    
    def getNbEntries(self) -> int:
        if self._nbEntries is None:
            self._nbEntries = 1
            
        return self._nbEntries

    def toHtmlTbl(self) -> str:
        htmlTbl=super().toHtmlTbl()
        
        return htmlTbl

    def getFieldNames(self) -> list[str]:
        if self._fieldNamesList is None:
            self._fieldNamesList=[]
            for group in self.__tdmsfile.groups():
                for channel in group.channels():                    
                    self._fieldNamesList.append(normalizeColumnName(channel.path))
                    
        return self._fieldNamesList

    # prepare / adapt header (col names mainly) to keep it comatible with main 3rd party tools
    def prepareFile(self, progressCb: MonitorProgress) -> None:
        # nothing to do for this format
        return None

    def loadParams(self,
                paramNamesList: list[str],
                indexNamesList: list[str] | None = None,
                monitorProgress: MonitorProgress | None = None,
                abortEvent: threading.Event | None = None,
                minDateSec: float | None = None,
                maxDateSec: float | None = None,
                callback: Callable[..., Any] | None = None,
                shiftDateSec: float | None = None,
                shiftDateRegex: str | None = None,
                shiftDateInverted: bool | None = None,
                silent: bool = False) -> list[pd.DataFrame]:
        
        # retrieve positions of requested params 
        rstDataframes=[]
        extractedParamsLock=threading.Lock()        

        monitorProgress.set_total_items(len(paramNamesList))
        def loadParamFromTdmsWorker(paramName: str) -> None:
            nonlocal rstDataframes
           
            if abortEvent and abortEvent.is_set():
                    raise Exception("Received abort event, DXD params extraction interrupted")  
               
            if not silent:
                logMsg=[f"extracting {paramName}", f"source file: {self.getBaseName()}",
                        f"source type: {self.getFileType()}"]
                monitorProgress.msg(msg=logMsg)

            fullTdmsfile=TdmsFile(self.getFileName(),read_metadata_only=False)

            try:
                dfParam = channelsAsDataframe(fullTdmsfile,[paramName])
                
                # convert datetime to nb seconds since epoch
                dfParam.index=dfParam.index.values.astype("float64")/1e9                
                if dfParam.index[0]==0:
                    if "Date Created" in fullTdmsfile.properties:
                        creationDate=fullTdmsfile.properties["Date Created"]
                        creationDateSec=(creationDate - np.datetime64('1970-01-01T00:00:00'))/ np.timedelta64(1, 's')
                        dfParam.index+=creationDateSec
                        if not self.showWarningDateOrigin:
                            self.showWarningDateOrigin=True
                            monitorProgress.msg(msg=[self.getBaseName(),"Could not detect dates origin, used 'Date Created' property instead."])
                    else:
                        if not self.showWarningDateOrigin:
                            self.showWarningDateOrigin=True
                            monitorProgress.msg(msg=[self.getBaseName(),"Could not detect dates origin."])

                with extractedParamsLock:
                    rstDataframes.append(self.finalizeParam(dfParam,
                            name=paramName,
                            indexName="Timestamps/timestamp",
                            origin=self.getFileName(),
                            minDateSec=minDateSec,maxDateSec=maxDateSec,
                            shiftDateSec=shiftDateSec,shiftDateRegex=shiftDateRegex,shiftDateInverted=shiftDateInverted,
                            monitorProgress=monitorProgress.child(f"finalize {paramName}"),
                            silent=silent,callback=callback))                
                
            except Exception as e:
                raise Exception(f"unable to load param '{paramName}' from {self.getBaseName()}: "+str(e))

        workersData=[]    
        paramPos=0
        for paramName in paramNamesList:  
            workersData.append(paramName)
            paramPos+=1  
                
        nbWorkers=NB_MAX_WORKERS
        with ThreadPoolExecutor(max_workers=nbWorkers) as executor:
            futures = [executor.submit(loadParamFromTdmsWorker, workerData) for workerData in workersData]
            wait(futures)
            for fut in futures:
                fut.result()
        
        return rstDataframes