
from __future__ import annotations

import os,math,abc,re

import pandas as pd
import inspect
import threading
from typing import Any, Callable
from misc.MonitorProgress import MonitorProgress

from datatools.datatoolbox import getDfName

pd.set_option('display.float_format', lambda x: '%.6f' % x)

def _pretty_str(self: pd.DataFrame | pd.Series) -> str:
    """helper to provided detailed contents of a DataFrame or Series in exploitation logs"""
    
    nbNan=0
    if isinstance(self, pd.DataFrame):
        nbNan = self.isna().sum().sum()
    else:
        nbNan = self.isna().sum()
        
    durationSec=self.index[-1]-self.index[0]
    contentsStr=None
    if self.index.dtype == 'float64':
        df_copy = self.copy(deep=False)
        df_copy.index = pd.to_datetime(df_copy.index, unit='s')
        contentsStr=df_copy.__repr__()    
    else: contentsStr=self.__repr__()
    
    def _fmt_ts(ts):
        if self.index.dtype == 'float64':
            return pd.to_datetime(ts, unit='s').strftime('%Y-%m-%d %H:%M:%S.%f')
        return str(ts)
        
    finalStr=f"\n-----------------------------------------"
    
    finalStr+=f"\n{contentsStr}"
    finalStr+=f"\nDuration: {durationSec:0.6f}s"
    finalStr+=f"\nTime Range: [ {self.index[0]:0.6f}s , {self.index[-1]:0.6f}s ]"
    if nbNan>0:
        finalStr+=f"\nNb NaN: {nbNan}"
    if durationSec>0:
        avgSampleRate=len(self)/(self.index[-1]-self.index[0])
        finalStr+=f"\nAvg Sample Rate: {avgSampleRate:0.2f} Hz"
    if hasattr(self,"origin"):
        finalStr+=f"\nOrigin: {self.origin}"
    if len(self)>1 and nbNan!=len(self): # no mean to compute min/max/mean for a single value
        dtype_to_check = self.iloc[:, 0].dtype if isinstance(self, pd.DataFrame) else self.dtype
        if pd.api.types.is_numeric_dtype(dtype_to_check):
            minVal = self.min()
            maxVal = self.max()
            meanVal = self.mean()
            idxMin = self.idxmin()
            idxMax = self.idxmax()
            
            # Flatten Series to scalars (handles single-column DataFrames)
            if isinstance(minVal, pd.Series):
                minVal = minVal.iloc[0]
                maxVal = maxVal.iloc[0]
                meanVal = meanVal.iloc[0]
                idxMin = idxMin.iloc[0]
                idxMax = idxMax.iloc[0]            
                
            finalStr+=f"\nmean= {meanVal:.6f}"
            finalStr+=f"\nmin= {minVal:.6f} @ {_fmt_ts(idxMin)}"
            finalStr+=f"\nmax= {maxVal:.6f} @ {_fmt_ts(idxMax)}"
            
    
    finalStr+=f"\n-----------------------------------------\n"
    return finalStr

pd.DataFrame.pstr = _pretty_str  
pd.Series.pstr = _pretty_str  

from misc.logger import get_logger

from datatools import datatoolbox

class AFileMgr(metaclass=abc.ABCMeta):
    """Exploitation needs to handle several types of data. This class defines common API."""
    
    def __init__(self,filename: str,fileIdx: int,continueOnError: bool=False) -> None:
        self.__filename=filename
        self._nbEntries=None
        self._fieldNamesList=None
        self.__fileIdx=fileIdx
        self.__selectedFieldsIdxList=[]
        self._continueOnError=continueOnError
    
    def getNbEntries(self) -> int | None:
        """Size of data"""      
        return self._nbEntries

    def getFileName(self) -> str:
        """Full file name"""
        return self.__filename
    
    def getBaseName(self) -> str:
        """File basename"""
        return os.path.basename(self.__filename)
    
    def getFileInfo(self) -> list:
        """Some metadata as array of string"""
        return ["File: "+self.getBaseName()]
    
    def getFileIdx(self) -> int:
        """Position of the file in the list files list use by applicative layer. (needed in Jupyter GUI)"""
        return self.__fileIdx
    
    def getFileType(self) -> str:
        """Some name of the file type (ex: CSV, H5, ...)"""
        return "???"
    
    def setContinueOnError(self,continueOnError: bool) -> None:
        """In some cases, tell if processing shall continue as far as possible"""
        self._continueOnError=continueOnError
        
    def toHtmlTbl(self) -> str:
        """Return HTML table rows describing the file contents (used by Jupyter GUI)"""
        htmlTbl = "<tr><th>"+"Nb Fields"+"</th><td>"+str(len(self.getFieldNames()))+"</td></tr>"
        htmlTbl += "<tr><th>"+"Nb Entries"+"</th><td>"+str(math.floor(self.getNbEntries())) if self.getNbEntries()!=None else "?"+"</td></tr>"
        return htmlTbl

    def getFieldNames(self) -> list:
        """Return list of all fields contained in this file, so that user can pick ones he want."""
        return self._fieldNamesList
    
    def getSelectedFieldsIdx(self) -> list:
        """Position in the list of fields currently selected (used by Jupyter GUI)"""
        return self.__selectedFieldsIdxList
       
    def getSelectedFieldsNames(self) -> list:
        """Names of fields currently selected (used by Jupyter GUI)"""
        rst=[]
        for fieldIdx in self.getSelectedFieldsIdx():
            rst.append(self.getFieldNames()[fieldIdx])
        return rst
    
    def addSelectedFieldIdx(self,fieldIdx: int) -> None:
        """Add field at given position in selected fields list (used by Jupyter GUI)"""
        if fieldIdx not in self.__selectedFieldsIdxList:
            self.__selectedFieldsIdxList.append(fieldIdx)
    
    def clearSelectedFieldsIdx(self) -> None:
        """Clear elected fields list (used by Jupyter GUI)"""
        self.__selectedFieldsIdxList.clear()

    def prepareFile(self,progressCb: MonitorProgress) -> str | None:
        """Do some preliminary processing, for ex cleaning CSV file header"""
        raise Exception("unimplemented method 'prepareFile' for file '"+self.getFileName()+"'")

    # if dryRun: return dummy result, just to fake real process but faster
    def findParams(self, paramPathRegex: str | None,indexPathRegex: str | None=None,
                   dryRun: bool=False,
                   monitorProgress: MonitorProgress | None=None,
                   abortEvent: threading.Event | None=None,
                   excludeParamsRegex: list | str=["/timestamp"], 
                   minDateSec: float | None=None,
                   maxDateSec: float | None=None, 
                   shiftDateSec: float | str | pd.DataFrame | None=None,
                   shiftDateRegex: str | None=None,
                   shiftDateInverted: bool | None=None,
                   silent: bool=False,
                   callback: Callable | None=None) -> list:
        
        """Try to find a parameter matching provided paramPathRegex (using regex) and returns it as Pandas dataframe.
        
        :param paramPathRegex (str): Regex of path or name of the parameter to retrieve inside the file. Can be a regex or a partial path.
        :param indexPathRegex (str): [optional] path of the parameter to retrieve as index, inside this file. Can be a regex or a partial path.
        :param dryRun (bool): if true, return real list of params but with empty contents (skip data extraction)
        :param monitorProgress (misc/MonitorProgress): progress tracker to known if we have time to get a coffee
        :param abortEvent(threading.Event): thread abort object, to catch some abort request from applicative layer
        :param excludeParamsRegex (str): regex to exclude params matching them
        :param minDateSec (float): minimal date in seconds since epoch 1970-01-01
        :param maxDateSec (float): maximal date in seconds since epoch 1970-01-01
        :param callback (function): instead of returning the df itself, invoke provided callback(df) and return its result
        :param silent (bool): minimize log traces
        :param shiftDateSec (float|str): litteral or param name to apply a date offset.
                             Interpolation is used to apply proper correction to actual dates of our param, but only on overlapping segment.
                             Values outside overlapping range are discarded.
        :param shiftDateRegex (str): regex to select params on which the date offset shall be actually applied
        :param shiftDateInverted (bool): if True, consider 'shiftDateSec' param has "ref dates" as index, and opposite correction to apply (in seconds) as value. The algo will then transform it as expected (i.e. the opposite).
            
        :return: List of matching dataframes (list might be empty if no match), None in case of error. If callback, returns result of callabck for each param
        """   
    
        paramNamesList=[]
        indexNamesList=[]

        # compile param and index path as a regex
        regexParam=None
        regexParamExclude=[]
        regexIndex=None

        if paramPathRegex!=None:            
            try:
               regexParam=datatoolbox.getFileParamMatchRegex(paramPathRegex)
            except Exception as e:
                    raise Exception("unable to compile paramPathRegex regular exception '"+str(paramPathRegex)+"' : "+str(e))                
        
        if excludeParamsRegex!=None:
            if isinstance(excludeParamsRegex,str):
                excludeParamsRegex=[excludeParamsRegex]
            try:
                for regex in excludeParamsRegex:
                    regexParamExclude.append(datatoolbox.getFileParamMatchRegex(regex))
            except Exception as e:
                    raise Exception("unable to compile excludeParamsRegex regular exception '"+str(excludeParamsRegex)+"' : "+str(e))                
            
        if indexPathRegex!=None:
            try:
                regexIndex=datatoolbox.getFileParamMatchRegex(indexPathRegex)            
            except Exception as e:
                raise Exception("unable to compile indexPathRegex regular exception '"+str(indexPathRegex)+"' : "+str(e))
        
        # find all matching parameters and indices
        for fieldName in self.getFieldNames():
            paramIsMatching=False
            if (regexParam==None or regexParam.match(fieldName)):
                paramIsMatching=True
                for regex in regexParamExclude:
                    if regex.match(fieldName):
                        paramIsMatching=False
                        
                if paramIsMatching:
                    paramNamesList.append(fieldName)
                    
            if indexPathRegex!=None and regexIndex.match(fieldName):
                indexNamesList.append(fieldName)

        if len(paramNamesList)==0:
            monitorProgress.set_total_items(1)
            monitorProgress.complete_n(1)
            return []
        
        
        if indexPathRegex!=None and len(indexNamesList)!=len(paramNamesList):
            raise Exception("["+self.getBaseName()+"] "+str(len(paramNamesList))+" param(s) matching for expression '"
                            +paramPathRegex+"', while "+str(len(indexNamesList))+" param(s) matching for explicit indices '"+indexPathRegex+"'")
            
        if dryRun:
            monitorProgress.set_total_items(len(paramNamesList))
            dataframes=[]            
            for param in paramNamesList:
                df=pd.DataFrame({ param: []}, index = [])                
                df.name=param
                df.origin=self.getFileName()                
                dataframes.append(self._invokeCbIfAny(df,callback,monitorProgress))                 
            return dataframes

        rst = self.loadParams(paramNamesList,indexNamesList,monitorProgress=monitorProgress,abortEvent=abortEvent,
                                    minDateSec=minDateSec,maxDateSec=maxDateSec,
                                    shiftDateSec=shiftDateSec,shiftDateRegex=shiftDateRegex,shiftDateInverted=shiftDateInverted,
                                    silent=silent,callback=callback
                                    )

        if not callback:
            for df in rst:
                if df is not None:
                    df.match=paramPathRegex
        
        return rst
        
    def dumpParamsTxt(self,key: str | None=None,item: Any=None,depth: int=0,path: str="") -> str:
        """return a str list of fieldnames.
        TODO: only used by datatoolbox:loadDataframeFromFile, to be removed (applicative code)"""
        strTxt=""
        for fieldname in self.getFieldNames():
            strTxt+="\t"+fieldname+"\n"

        return strTxt
    

    def _invokeCbIfAny(self,df: pd.DataFrame,callback: Callable | None,monitorProgress: MonitorProgress) -> Any:
        """invoke user's cb functions if provided.
        Exception here: monitorProgress set_total_items shall be done before calling this.
        Reason is that various usecases dryRun/not dryRun for ex make it a bit harder to respect standard monitorProgress flow"""
        if callback:     
            if 'monitorProgress' in inspect.signature(callback).parameters:                    
                return callback(df,monitorProgress=monitorProgress.child(f"callback {df.name}", renameIfExist=True))
            else:
                rst=callback(df)                
                monitorProgress.complete_n(1)
                return rst   

        else:
            monitorProgress.complete_n(1)
            return df
            
    def finalizeParam(self,dfParam: pd.DataFrame | pd.Series | None,name: str,indexName: str,origin: str,
                  columns: list | None=None, callback: Callable | None=None,
                  minDateSec: float | None=None,maxDateSec: float | None=None,
                  shiftDateSec: float | str | pd.DataFrame | None=None,shiftDateRegex: str | None=None,shiftDateInverted: bool | None=None,silent: bool=False,
                  monitorProgress: MonitorProgress | None=None) -> Any:
        """Once data is loaded, apply common operation to it.
        
        min/max date is applied **after** date shifting.
        
        See findParams doc for other arguments.
        
        :param columns (list[str]): custom names to give to each column
        :param shiftDateSec (pd.DataFrame|float): if original shiftDateSec is a string, at this stage shall have been already loaded as a DataFrame (see FolderParamMgr).
        """
             
        monitorProgress.set_total_items(1)
        
        if dfParam is None: 
            return None
        
        # apply date shift (check corresponding file/param name regex if any provided)
        if shiftDateSec is not None:
            isShiftDateActive=True        
            if shiftDateRegex:
                isShiftDateActive=False
                m=re.match("((.*)::)?(.*)?",shiftDateRegex)
                fileBasenameRegex=m.group(2)
                paramPathRegex=m.group(3)
            
                if fileBasenameRegex is None or re.search(fileBasenameRegex,self.getBaseName()):
                    if paramPathRegex is None or re.search(paramPathRegex,name):
                        isShiftDateActive=True
                        
            if isShiftDateActive:
                
                # if it's a DataFrame, than apply time drift to each index value (interpolating)
                # The DataFrame containing date correction has been previously loaded by FolderParamMgr typically.
                if isinstance(shiftDateSec,pd.DataFrame):
                    #not silent and get_logger().info(f"\n[applying clock drift correction to '{name}':\n---------------------------\nOriginal:\n{dfParam}\nClock Drift:\n{shiftDateSec}")
                                        
                    # we need to get index of shiftDateSec to be matching dates of dfParam, to compute overlaping segment
                    # to do so we inverse index/valye
                    if shiftDateInverted:
                        shiftDateSec=shiftDateSec.copy()
                        # align index range to opposite side
                        shiftDateSec.index+=shiftDateSec[shiftDateSec.columns[0]]    
                        # opposite clock compensation value
                        shiftDateSec[shiftDateSec.columns[0]]=-shiftDateSec[shiftDateSec.columns[0]]
                        
                        #get_logger().info(f"\n#### Inverted Clock\n{shiftDateSec}")                    

                    #get_logger().info(f"\n#### PARAM before:\n{dfParam}")
                    alignedDfParam,alignedDatesDriftSec=datatoolbox.overlap(dfParam,shiftDateSec)
                    if alignedDfParam is None:
                        if 'timestamp' in name.lower():
                            get_logger().info(f"[skipped clock correction of param {name} (it is itself a timestamp)]")
                            return dfParam
                        else:                                       
                            msg=f"clock data is not overlapping dates of param {name}, unable to apply clock correction"
                            print(f"\n{dfParam.pstr()}\n{shiftDateSec.pstr()}")
                            if self._continueOnError:
                                monitorProgress.msg(msg=msg,msgSeverity="error")
                                return None
                            else:
                                raise Exception(msg)
                        
                    dfParam=alignedDfParam
                    dfParam.index+=alignedDatesDriftSec[alignedDatesDriftSec.columns[0]]
                    dfParam.attrs["clock corrected"]=getDfName(shiftDateSec)+f" avg={shiftDateSec.mean().iloc[0]:.6f}"
                    
                    #not silent and get_logger().info(f"applied clock drift correction to '{name}':\nFinal:\n{dfParam}\n---------------------------]")
                    
                # else, constant add to each index value
                else:
                    not silent and get_logger().info(f"[applying clock offset (constant) correction to '{name}' ({shiftDateSec}s)]")
                    if shiftDateInverted==True: shiftDateSec=-shiftDateSec
                    dfParam.index+=shiftDateSec
                    dfParam.attrs["clock corrected"]=f"{shiftDateSec}"

        # apply min/max date if requested
        if minDateSec or maxDateSec:
            minDateSecStr = str(minDateSec) if minDateSec else "-"
            maxDateSecStr = str(maxDateSec) if maxDateSec else "-"
            orginalNbSamples=len(dfParam)
            dfParam=dfParam[minDateSec:maxDateSec]
            durationStr=""
            if minDateSec is not None and maxDateSec is not None:
                durationStr=f" (segment of {(maxDateSec - minDateSec)}s)"
            if len(dfParam)==0:
                get_logger().warning(f"no value for '{name}' within requested time range: [{minDateSecStr},{maxDateSecStr}]{durationStr} ")
            else:
                not silent and get_logger().info(f"[applied timeframe reduction to '{name}': [{minDateSecStr},{maxDateSecStr}]{durationStr} : {orginalNbSamples} samples -> {len(dfParam)} samples]")

        dfParam.name=re.sub(r"\/?tmp_[^/]+\/","/",name)
        dfParam.index.name=re.sub(r"\/?tmp_[^/]+\/","/",indexName)
        dfParam.origin=self.getFileName()
        if isinstance(dfParam,pd.DataFrame):
            if columns is not None:
                dfParam.columns=columns
            dfParam.columns = dfParam.columns.str.replace(r"\/?tmp_[^/]+\/", '/', regex=True)
        
        return self._invokeCbIfAny(dfParam,callback,monitorProgress)  
        
        
    @abc.abstractmethod
    def loadParams(self, paramNamesList: list,
                   indexNamesList: list=[],monitorProgress: MonitorProgress | None=None,
                   abortEvent: threading.Event | None=None,
                   minDateSec: float | None=None,
                   maxDateSec: float | None=None, 
                   callback: Callable | None=None,
                   shiftDateSec: float | str | pd.DataFrame | None=None,
                   shiftDateRegex: str | None=None,
                   shiftDateInverted: bool | None=None,
                   silent: bool=False) -> list:
        """Retrieve Dataframe(s) corresponding to given param list (exact names, not regex)
        
        See doc of findParams for other arguments.
        
        :param paramNamesList (str[]): full names (not regex) of params to retrieve
        :param indexNamesList (str[]): full names of indices to retrieve for each corresponding param in list index

        :return: a list of Pandas dataframes containing requested params

        """
        ...      
