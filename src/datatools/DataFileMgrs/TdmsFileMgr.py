
import sys,os,threading
from time import sleep
from concurrent.futures import ThreadPoolExecutor,wait
from typing import Any, Callable, List, Optional

import numpy as np 
import pandas as pd

# add testsuit path (relative path to this module)
sys.path.append(os.path.realpath(os.path.dirname( __file__[:-1] if __file__.endswith('.pyc') else __file__ ) +os.sep+"npTDMS.egg"))
from nptdms import TdmsFile
from nptdms.tdmsinfo import tdmsinfo
from nptdms.utils import normalizeColumnName

from misc.MonitorProgress import MonitorProgress
from datatools.DataFileMgrs.AFileMgr import AFileMgr
from datatools.DataframeToHdf5 import DataframeToHdf5

NB_MAX_WORKERS=6

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
        if self._nbEntries == None:
            self._nbEntries = 1
            
        return self._nbEntries

    def toHtmlTbl(self) -> str:
        htmlTbl=super().toHtmlTbl()
        
        return htmlTbl

    def getFieldNames(self) -> List[str]:
        if self._fieldNamesList==None:
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
                paramNamesList: List[str],
                indexNamesList: Optional[List[str]] = None,
                monitorProgress: Optional[MonitorProgress] = None,
                abortEvent: Optional[threading.Event] = None,
                minDateSec: Optional[float] = None,
                maxDateSec: Optional[float] = None,
                callback: Optional[Callable[..., Any]] = None,
                shiftDateSec: Optional[float] = None,
                shiftDateRegex: Optional[str] = None,
                shiftDateInverted: Optional[bool] = None,
                silent: bool = False) -> List[pd.DataFrame]:
        
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
                dfParam = fullTdmsfile.as_dataframe(channels_to_export_str=[paramName],time_index=True, absolute_time=True, scaled_data=True, arrow_dtypes=False)
                
                # convert datetime to nb seconds since epoch
                dfParam.index=dfParam.index.values.astype("float64")/1e9                
                if dfParam.index[0]==0:
                    if "Date Created" in fullTdmsfile.properties:
                        creationDate=fullTdmsfile.properties["Date Created"]
                        creationDateSec=(creationDate - np.datetime64('1970-01-01T00:00:00Z'))/ np.timedelta64(1, 's')
                        dfParam.index+=creationDateSec
                        if self.showWarningDateOrigin==False:
                            self.showWarningDateOrigin=True
                            monitorProgress.msg(msg=[self.getBaseName(),"Could not detect dates origin, used 'Date Created' property instead."])
                    else:
                        if self.showWarningDateOrigin==False:
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