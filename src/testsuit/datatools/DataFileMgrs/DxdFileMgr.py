
import math,threading
from datetime import timezone
from concurrent.futures import ThreadPoolExecutor,wait
from typing import Any
from collections.abc import Callable

import numpy as np
import pandas as pd

import dwdatareader as dw
from testsuit.misc.MonitorProgress import MonitorProgress

from testsuit.misc.logger import get_logger

from testsuit.datatools.DataFileMgrs.AFileMgr import AFileMgr


def _loadChannel(dxdFile: dw.DWFile, channelName: str, chunkCb: Callable[[int], None]) -> pd.DataFrame:
    """Full speed values of one channel (first array element), indexed by unique time offsets in seconds."""
    channel=dxdFile[channelName]
    time,data=channel.scaled()
    chunkCb(channel.number_of_samples)
    if len(time)==0:
        return pd.DataFrame({channelName: pd.Series(dtype='object')})
    time,ix=np.unique(time,return_index=True) # use unique times
    return pd.DataFrame({channelName: pd.Series(data.reshape(-1,channel.array_size)[ix,0],index=time,name=channelName)})


class DxdFileMgr(AFileMgr):
    dxdlock=threading.Lock()
    filesLock={}
    def __init__(self, filename: str, fileIdx: int) -> None:
        super().__init__(filename,fileIdx)
        self.filename = filename
                
        with self.dxdlock:
            if filename not in self.filesLock:
                self.filesLock[filename]= {
                                            "fileHandle":dw.DWFile(filename,key=lambda channel: channel.name),
                                            "lock":threading.Lock()
                                        }
            self.__dxdfile=self.filesLock[filename]["fileHandle"]
            self.__lock=self.dxdlock # self.filesLock[filename]["lock"]
            self.__events=self.__dxdfile.events()
            get_logger().debug("DXD Dewesoft File - "+str(self.__dxdfile.info.start_store_time)+" - "+self.getBaseName())  

        
    def getFileType(self) -> str:
        return "dxd"
    
    def getNbEntries(self) -> int:
        if self._nbEntries == None:
            with self.__lock:
                self._nbEntries = round(self.__dxdfile.info.sample_rate * self.__dxdfile.info.duration)
                
        return self._nbEntries

    def getFileInfo(self) -> list[str]:
        fileInfo=super().getFileInfo()

        with self.__lock:
            rst=fileInfo+["Sample rate:"+str(math.floor(self.__dxdfile.info.sample_rate))+" Hz",
                    "Duration:"+str(self.__dxdfile.info.duration)+"s",
                    "Start:"+str(self.__dxdfile.info.start_store_time)]
        return rst
    
    def toHtmlTbl(self) -> str:
        htmlTbl=super().toHtmlTbl()
        with self.__lock:
            htmlTbl += "<tr><th>"+"Sample Rate"+"</th><td>"+str(math.floor(self.__dxdfile.info.sample_rate))+" Hz"+"</td></tr>"
            htmlTbl += "<tr><th>"+"Duration"+"</th><td>"+str(self.__dxdfile.info.duration)+" s"+"</td></tr>"
            htmlTbl += "<tr><th>"+"Start Recording Time"+"</th><td>"+str(self.__dxdfile.info.start_store_time)+"</td></tr>"
        return htmlTbl

    def getFieldNames(self) -> list[str]:
        with self.__lock:                   
            if self._fieldNamesList==None:
                self._fieldNamesList = [fieldName for fieldName in self.__dxdfile]                        
                        
        return self._fieldNamesList

    # prepare / adapt header (col names mainly) to keep it comatible with main 3rd party tools
    def prepareFile(self, monitorProgress: MonitorProgress) -> None:
        # nothing to do for DXD files
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
        
        # DXD format includes timestamps. No separate index names required.
        if not (indexNamesList==None or len(indexNamesList)==0):
            raise ValueError("No explicit index (timestamp) allowed for DXD files, sorry.")
            
        # retrieve positions of requested params 
        rstDataframes=[]
        extractedParamsLock=threading.Lock()
        nbEntries=self.getNbEntries()
    
        ### /!\ DXD lib is not threadsafe, not trivial to parallelize the work to be done
        with self.__lock:                   
            
            datesOrigin=self.__dxdfile.info.start_store_time

            #get_logger().error(self.getBaseName()+": start_store_time="+str(self.__dxdfile.info.start_store_time))

            # absolute time in days since the 30th of December, 1899
            #get_logger().error(self.getBaseName()+": _start_store_time="+str(self.__dxdfile.info._start_store_time))
    
            def loadParamFromDxdWorker(workerData: dict[str, Any]) -> None:
                nonlocal rstDataframes      
                
                paramName=workerData["paramName"]
                monitorProgress=workerData["monitorProgress"]
                
                monitorProgress.set_total_items(2)   # load contents + finalize/callback
                mpLoad=monitorProgress.child("load",nbEntries)
                mpFinalize=monitorProgress.child("finalize")    

                if abortEvent and abortEvent.is_set():
                        raise Exception("Received abort event, DXD params extraction interrupted")

                def myChunkCb(chunkSize: int) -> None:       
                    
                    if abortEvent!=None and abortEvent.is_set():
                        raise Exception("Received abort event, DXD params extraction interrupted")                                
                        
                    mpLoad.complete_n(chunkSize)

                monitorProgress and monitorProgress.msg(msg=[ f"extracting {paramName}", f"source file: {self.getBaseName()}",
                                                f"source type: {self.getFileType()}"])

                try:
                    dfParam = _loadChannel(self.__dxdfile,paramName,myChunkCb)
                    
                except Exception as e:
                    monitorProgress and monitorProgress.msg(msg=[self.getBaseName(),paramName,"unable to extract values","ERROR: "+str(e)],msgSeverity="error")
                    raise Exception(f"Aborting loading params from {self.getBaseName()}")
                
                datesOriginSec=datesOrigin.replace(tzinfo=timezone.utc).timestamp()
                dxdShiftDateSec=datesOriginSec if shiftDateSec is None else datesOriginSec + shiftDateSec
                with extractedParamsLock:
                    rstDataframes.append(self.finalizeParam(dfParam,
                            name=paramName,
                            indexName="Timestamps/timestamp",
                            origin=self.getFileName(),
                            minDateSec=minDateSec,maxDateSec=maxDateSec,
                            shiftDateSec=dxdShiftDateSec,shiftDateRegex=shiftDateRegex,shiftDateInverted=shiftDateInverted,
                            monitorProgress=mpFinalize,
                            silent=silent, callback=callback))
                    

                #get_logger().error(self.getBaseName()+": absdates=["+str(dfParam.index[0])+" , "+str(dfParam.index[dfParam.size-1])+" ]")


            #for requestedFieldname in paramNamesList:
            workersData=[]    
            paramPos=0
            monitorProgress.set_total_items(len(paramNamesList))
            for paramName in paramNamesList:  
                workersData.append({"paramName":paramName,"monitorProgress":monitorProgress.child(f"loading {paramName}", renameIfExist=True)})
                paramPos+=1  
                    
            # crashes ... 
            #nbWorkers=len(paramNamesList)
            nbWorkers=1
            with ThreadPoolExecutor(max_workers=nbWorkers) as executor:
                futures = [executor.submit(loadParamFromDxdWorker, workerData) for workerData in workersData]
                wait(futures)
                for fut in futures:
                    fut.result()
            
        return rstDataframes