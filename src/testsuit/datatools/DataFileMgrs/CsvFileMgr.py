
from __future__ import annotations

import re,subprocess,sys
import pandas as pd
import numpy as np
from datetime import datetime,timezone

import threading
from typing import Callable

from testsuit.misc.logger import get_logger
from testsuit.misc.MonitorProgress import MonitorProgress

from testsuit.datatools.DataFileMgrs.AFileMgr import AFileMgr
from testsuit.datatools.DataframeToHdf5 import DataframeToHdf5
from testsuit.datatools.datatoolbox import getDateParser

from unidecode import unidecode

RE_DETECT_HEADER=re.compile(r"time|date", re.IGNORECASE)
MAX_CSV_COLS=512

DEFAULT_COL_IDX=0

def GetCsvFileType(fileName: str) -> str:
    
    with open(fileName) as f:
        first_line = f.readline().strip('\n')

        # influxdb
        if first_line.startswith("#datatype"):
            return "influxdb"

        # channels log
        if "rawVal;engVal;" in first_line:
            return "channels-pcap-recorder"

        if "date;channel;" in first_line:
            return "channels"


    return "Generic"

# prepare all header from unaccepted chars
def cleanCsvHeaderLine(lineStr: str) -> str:
        cleanedHeaderLine=unidecode(lineStr.replace(" ","_") \
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
        return cleanedHeaderLine

class CsvFileMgr(AFileMgr):

    def __init__(self,filename: str,fileIdx: int) -> None:
        super().__init__(filename,fileIdx)
        self.__separator=None
        self.__headerLine=None
        
    def getFileType(self) -> str:
        return "csv"

    def getCsvFileType(self) -> str:
        return "Generic"
    
    def getHeaderLine(self) -> str:
        if self.__headerLine==None:
            with open(self.getFileName()) as f:
                self.__headerLine=f.readline()
                if self.__headerLine==None or len(self.__headerLine)==0:
                    raise Exception(f"Empty CSV file or empty header line: {self.getFileName()}")

        return self.__headerLine


    def getDateParserFunc(self,indexColPos: int) -> Callable | None:

        with open(self.getFileName()) as f:
            # get a data line
            line=f.readline()

            # read first data line (not comment nor header line)
            while (line.startswith("#")): line=f.readline()
            line=f.readline().strip()
            
            cols=line.split(self.getSeparator())
            dateValue=cols[indexColPos]

            try:
                return getDateParser(dateValue)               
            except Exception as e:
                raise Exception(f"Unable to parse date format '{dateValue}' in CSV file '{self.getFileName()}' at column {indexColPos+1} in line: {line}")
                

    def getSeparator(self) -> str:

        with open(self.getFileName()) as f:
            line=f.readline()
            if line.strip().startswith('"') and line.strip().endswith('"') and len(line.split('"'))==3:
                raise Exception(f"Please remove quotes at begin and end of all lines: {self.getFileName()}")

        if self.__separator==None:
            if len(self.getHeaderLine().split(';'))>1:
                return ';'
            elif len(self.getHeaderLine().split(','))>1:
                return ','
            elif len(self.getHeaderLine().split('\t'))>1:
                return '\t'
            else:
                raise Exception(f"Unable to detect CSV separator in CSV file '{self.getFileName()}'")
        return self.__separator
    
    def getNbEntries(self) -> int:
        if self._nbEntries == None:
            self._nbEntries = 0
            with open(self.getFileName(), "rbU") as f:
                self._nbEntries = sum(1 for _ in f)

        return self._nbEntries


    def getFileInfo(self) -> list:

        fileInfo=super().getFileInfo()
        sepStr=self.getSeparator()
        if sepStr=='\t': sepStr="<TAB>"
        return fileInfo+[f"CSV-type: {self.getCsvFileType()}",
                        "Separator: '"+str(sepStr)+"'"]
    
    def toHtmlTbl(self) -> str:
        
        sepStr=self.getSeparator()
        if sepStr=='\t': sepStr="&lt;TAB&gt;"

        htmlTbl=super().toHtmlTbl()
        htmlTbl += "<tr><th>"+"CSV Type"+"</th><td>"+self.getCsvFileType()+"</td></tr>"
        htmlTbl += "<tr><th>"+"Separator"+"</th><td>'"+sepStr+"'</td></tr>"
                
        if len(self.getFieldNames())>MAX_CSV_COLS:
            htmlTbl += "<tr><td colspan=2 >"+"<div style='color:red'><b>Too many columns (&gt;"+str(MAX_CSV_COLS)\
                +"), you will have to select a subset of parameters to extract ..."+"</b></div></td></tr>"

        return htmlTbl

    def getFieldNames(self) -> list:
        if self._fieldNamesList==None:
            self._fieldNamesList=[]
            self._fieldNamesList=self.getHeaderLine().replace('"',"").strip().split(self.getSeparator())            
            if len(self._fieldNamesList)<2:
                raise Exception(self.getBaseName()+": Found only "+str(len(self._fieldNamesList))\
                                +" column in CSV header line: '"+self.getHeaderLine()+"'")
            
        return self._fieldNamesList

    # prepare / adapt header (col names mainly) to keep it compatible with main 3rd party tools
    # to be called explicitly
    def prepareFile(self,monitorProgress: MonitorProgress) -> str | None:

        if ".clean" in self.getFileName():
            get_logger().error("Cowardly refusing to clean and already cleaned file : "+self.getBaseName())
            return None
        
        newFileName=self.getFileName().replace(".csv",".clean.csv")
        totalNbLines = self.getNbEntries()
        
        with monitorProgress.child("prepareFile",totalNbLines) as subMp:
                
            f = open(self.getFileName(),encoding='utf-8')
            fo = open(newFileName,'w')
            lineNb = 1
            for lineStr in f:
                keepLine,fixedLine = self.__checkCsvLine(lineStr,lineNb)
                if keepLine:
                    fo.write(fixedLine)
                if lineNb % 100 == 0:
                    subMp.complete_n(lineNb)
                lineNb += 1
        
        f.close()
        fo.close()
        
        return newFileName

    def loadParams(self,
                   paramNamesList: list,
                   indexNamesList: list | None=None,
                   monitorProgress: MonitorProgress | None=None,
                   abortEvent: threading.Event | None=None,
                   minDateSec: float | None=None,
                   maxDateSec: float | None=None, 
                   callback: Callable | None=None,
                   shiftDateSec: float | str | pd.DataFrame | None=None,
                   shiftDateRegex: str | None=None,
                   shiftDateInverted: bool | None=None,
                   silent: bool=False) -> list:

        # retrieve positions of requested params in CSV
        rst=[]
        
        if len(paramNamesList)==0:
            raise Exception(self.getBaseName()+": list of params to load is empty")

        monitorProgress.set_total_items(len(paramNamesList))
        
        # determining which col to use as index
        if not indexNamesList:
            indexColName=None
            for pName in self.getFieldNames():
                if re.search(r"(time|date)",pName, re.IGNORECASE):
                    indexColName=pName
                    break
            if not indexColName:
                indexColName=paramNamesList[DEFAULT_COL_IDX]
            indexNamesList=[]
            for i in range(0,len(paramNamesList)):
                indexNamesList+=[indexColName]

        paramIdx=0
        totalNbParams=len(paramNamesList)
        
        for requestedFieldname in paramNamesList:

            if abortEvent and abortEvent.is_set():
                raise Exception("Received abort event, CSV params extraction interrupted")
            
            indexName=indexNamesList[paramIdx]
            indexColPos=self.getFieldNames().index(indexName)
            
            # do not extract index col as a parameter (already extracted)
            if requestedFieldname==indexName:
                totalNbParams-=1
                monitorProgress.complete_n(1)
                continue
            
            (not silent) and monitorProgress.msg(msg=[ f"extracting {requestedFieldname}", 
                                            f"source file: {self.getBaseName()}",
                                            f"source type: {self.getFileType()} {self.getCsvFileType()}",
                                            f"timestamp: col '{indexName}' at pos '{indexColPos}'"])                     
            paramIdx+=1       

            paramColPos = self.getFieldNames().index(requestedFieldname)
            colsList=[indexColPos,paramColPos]
            colsList.sort()
            index_col_pos_in_sublist=colsList.index(indexColPos)
            date_parser_func=self.getDateParserFunc(indexColPos)

            read_csv_kwargs = {
                'index_col': index_col_pos_in_sublist,
                'usecols': colsList,
                'encoding': 'utf-8',
                'sep': self.getSeparator(),
                'comment': '#',
            }
            
            dfParam = pd.read_csv(self.getFileName(),**read_csv_kwargs)            
            
            if date_parser_func is not None and not isinstance(dfParam.index, pd.DatetimeIndex):
                dfParam.index = dfParam.index.map(date_parser_func)
            if isinstance(dfParam.index, pd.DatetimeIndex):
                if dfParam.index.tz is None:
                    dfParam.index = dfParam.index.tz_localize('UTC')
                else:
                    dfParam.index = dfParam.index.tz_convert('UTC')

                # Pandas <2.0 has only ns reoslution (and as_unit is not present)
                try: dfParam.index = dfParam.index.as_unit('ns').view('int64') / 1e9
                except: dfParam.index = dfParam.index.view('int64') / 1e9
            
            rst.append(self.finalizeParam(dfParam,
                                            name=requestedFieldname,
                                            indexName="Timestamps/"+indexName,
                                            origin=self.getFileName(),
                                            minDateSec=minDateSec,maxDateSec=maxDateSec,
                                            shiftDateSec=shiftDateSec,shiftDateRegex=shiftDateRegex,shiftDateInverted=shiftDateInverted,
                                            monitorProgress=monitorProgress.child(f"finalize {requestedFieldname}"),
                                            silent=silent, callback=callback))

        return rst
  

class CsvFileMgrInfluxDb(CsvFileMgr):

    def __init__(self,filename: str,fileIdx: int=0) -> None:
        super().__init__(filename,fileIdx)
        get_logger().debug("CSV-InfluxDB File - "+self.getBaseName())
        self.df=None
        self.colDataTypes={} 

        
    def getCsvFileType(self) -> str:
        return "influxdb"

    def loadFile(self) -> None:

        datesColIdx=None
        datesType=None
        measColIdx=None

        with open(self.getFileName()) as f:
            first_line = f.readline().strip('\n')
            second_line = f.readline().strip('\n')
            colTypes=first_line.replace("#datatype ","").split(self.getSeparator())
            colNames=second_line.split(self.getSeparator())

            colIdx=0
            for colType in colTypes:
                if "dateTime" in colType:
                    datesColIdx=colIdx
                    datesType=colType.split(":")[1]
                elif colType=="measurement":
                    measColIdx=colIdx
                else:
                    self.colDataTypes[colIdx]=colType
                colIdx+=1

        if measColIdx==None:
            raise Exception("missing 'measurement' column")
        
        colsList=[datesColIdx,measColIdx]+list(self.colDataTypes.keys())

        colsList.sort()
        index_col_pos_in_sublist=colsList.index(datesColIdx)

        try:
            date_parser_func = self.getDateParserFunc(datesColIdx)
            self.df = pd.read_csv(
                self.getFileName(),
                index_col=index_col_pos_in_sublist,
                usecols=colsList,
                encoding='utf-8',
                sep=self.getSeparator(),
                comment='#')
            if date_parser_func is not None and not isinstance(self.df.index, pd.DatetimeIndex):
                self.df.index = self.df.index.map(date_parser_func)
        except Exception as e:            
            raise Exception(f"Unable to read CSV file {self.getFileName()} (datesColIdx={datesColIdx}): "+str(e))
    
    def getFieldNames(self) -> list:
        if self._fieldNamesList==None:
            if self.df is None:
                self.loadFile()

            measurements=list(self.df["measurement"].unique())
            params=[i for i in self.df.columns if i!="measurement" and "time" not in i.lower() and "date" not in i.lower() ]

            self._fieldNamesList=[]
            for meas in measurements:
                for param in params:
                    self._fieldNamesList+=[meas+"."+param]

        return self._fieldNamesList

    def getNbEntries(self) -> int:
        if self.df is None:
            self.loadFile()

        return len(self.df)

    def prepareFile(self,monitorProgress: MonitorProgress) -> str:
        """do nothing"""
   
        return self.getFileName()

    
    def loadParams(self,paramNamesList: list,
                   indexNamesList: list | None=None,
                   monitorProgress: MonitorProgress | None=None,
                   abortEvent: threading.Event | None=None,
                   minDateSec: float | None=None,
                   maxDateSec: float | None=None, 
                   callback: Callable | None=None,
                   shiftDateSec: float | str | pd.DataFrame | None=None,
                   shiftDateRegex: str | None=None,
                   shiftDateInverted: bool | None=None,
                   silent: bool=False) -> list:

        # retrieve positions of requested params in CSV
        rst=[]
        paramIdx=0
        monitorProgress.set_total_items(len(paramNamesList))
        if len(paramNamesList)==0:
            raise Exception(self.getBaseName()+": list of params to load is empty")

        if indexNamesList!=None and len(indexNamesList)>0:
            raise Exception(f"data index already enforced in InfluxDB-CSV format, cannot set it explicitly as '{indexNamesList}'")
        
        for requestedFieldname in paramNamesList:

            if abortEvent and abortEvent.is_set():
                raise Exception("Received abort event, InfluxDB-CSV params extraction interrupted")            
            
            (not silent) and monitorProgress.msg(msg=[ f"extracting {requestedFieldname}", 
                                f"source file: {self.getBaseName()}",
                                f"source type: {self.getFileType()} {self.getCsvFileType()}"])
            
            paramIdx+=1
            try:                

                measName=requestedFieldname.split(".")[0]
                paramName=requestedFieldname.split(".")[1]

                dfParam = self.df[self.df["measurement"]==measName][paramName].to_frame()
                dfParam.index=dfParam.index.astype(np.float64)/1e9 # convet date to seconds
                
                rst.append(self.finalizeParam(dfParam,
                                                name=requestedFieldname,
                                                columns=[requestedFieldname],
                                                indexName="Timestamps",
                                                origin=self.getFileName(),
                                                minDateSec=minDateSec,maxDateSec=maxDateSec,
                                                shiftDateSec=shiftDateSec,shiftDateRegex=shiftDateRegex,shiftDateInverted=shiftDateInverted,
                                                monitorProgress=monitorProgress.child(f"finalize {paramName}"),
                                                silent=silent, callback=callback))

            except Exception as e:
                raise Exception("Unable to extract param '"+requestedFieldname+"' from InfluxDB-CSV file '"+self.getFileName()+"' : "+str(e))                

        return rst



class CsvFileMgrChannels(CsvFileMgr):

    def __init__(self,filename: str,fileIdx: int=0) -> None:
        super().__init__(filename,fileIdx)
        get_logger().debug("CSV-Channels File - "+self.getBaseName())
        self.df=None
        self.colDataTypes={} 
        self.fieldsFullNames={}
        
    def getCsvFileType(self) -> str:
        return "channels"

    def channelsType2pdType(self,channelsType: str) -> str:
        if channelsType=="DT_NONE": return "string"
        if channelsType=="DT_CHAR": return "string"
        if channelsType=="DT_INT8": return "int"
        if channelsType=="DT_UINT8": return "int"
        if channelsType=="DT_INT16": return "int"
        if channelsType=="DT_UINT16": return "int"
        if channelsType=="DT_INT32": return "int"
        if channelsType=="DT_UINT32": return "int"
        if channelsType=="DT_INT64": return "int64"
        if channelsType=="DT_UINT64": return "int64"
        if channelsType=="DT_FLOAT": return "float"
        if channelsType=="DT_DOUBLE": return "float64"
        if channelsType=="DT_BOOLEAN": return "bool"
        if channelsType=="DT_STRING": return "string"
        if channelsType=="DT_STRING_VIEW": return "string"
        if channelsType=="DT_BYTE_STREAM": return "string"
        if channelsType=="DT_OBJECT": return "string"

        raise Exception(f"While loading file {self.getFileName()} : Unknown Channels dtype '{channelsType}'.")
        
    def loadFile(self) -> None:

        datesColIdx=None
        datesType="float64"
        chNameColIdx=None
        channelNameType="string"
        valueColIdx=None

        with open(self.getFileName()) as f:
            first_line = f.readline().strip('\n')
            second_line = f.readline().strip('\n')
            colNames=first_line.replace("#","").split(self.getSeparator())
            colTypes=second_line.replace("#","").split(self.getSeparator())

            colIdx=0
            for colName in colNames:
                if colName=="date":
                    datesColIdx=colIdx
                elif colName=="channel":
                    chNameColIdx=colIdx
                elif colName=="value":
                    valueColIdx=colIdx
                colIdx+=1

        colsList=[datesColIdx,chNameColIdx,valueColIdx]        
        colsList.sort()
        index_col_pos_in_sublist=colsList.index(datesColIdx)

        date_parser_func = self.getDateParserFunc(datesColIdx)
        self.df = pd.read_csv(
            self.getFileName(),
            index_col=index_col_pos_in_sublist,
            usecols=colsList,
            names=colNames,
            encoding='utf-8',
            sep=self.getSeparator(),
            skiprows=[1],
            comment='#')
        if date_parser_func is not None and not isinstance(self.df.index, pd.DatetimeIndex):
            self.df.index = self.df.index.map(date_parser_func)
        
        
    def getFieldNames(self) -> list:
        if self._fieldNamesList==None:
            if self.df is None:
                self.loadFile()

            self._fieldNamesList=[]        
            fullNames=list(self.df["channel"].unique())
            for fullName in fullNames:
                shortName=fullName
                if "=" in fullName:
                    shortName=fullName.split("=")[1]
                
                self.fieldsFullNames[shortName]=fullName
                self._fieldNamesList+=[shortName]

        return self._fieldNamesList

    def getNbEntries(self) -> int:
        if self.df is None:
            self.loadFile()

        return len(self.df)

    def prepareFile(self,monitorProgress: MonitorProgress) -> str:
        """do nothing"""
   
        return self.getFileName()

    
    def loadParams(self,paramNamesList: list,
                   indexNamesList: list | None=None,
                   monitorProgress: MonitorProgress | None=None,
                   abortEvent: threading.Event | None=None,
                   minDateSec: float | None=None,
                   maxDateSec: float | None=None, 
                   callback: Callable | None=None,
                   shiftDateSec: float | str | pd.DataFrame | None=None,
                   shiftDateRegex: str | None=None,
                   shiftDateInverted: bool | None=None,
                   silent: bool=False) -> list:

        # retrieve positions of requested params in CSV
        rst=[]
        paramIdx=0
        monitorProgress.set_total_items(len(paramNamesList))
        if len(paramNamesList)==0:
            raise Exception(self.getBaseName()+": list of params to load is empty")

        if indexNamesList!=None and len(indexNamesList)>0:
            raise Exception(f"data index already enforced in Channels-CSV format, cannot set it explicitly as '{indexNamesList}'")

        for requestedFieldname in paramNamesList:

            if abortEvent and abortEvent.is_set():
                raise Exception("Received abort event, Channels-CSV params extraction interrupted")            
            
            
            (not silent) and monitorProgress.msg(msg=[ f"extracting {requestedFieldname}", 
                                f"source file: {self.getBaseName()}",
                                f"source type: {self.getFileType()} {self.getCsvFileType()}"])
      
            paramIdx+=1
            try:                

                paramFullName=self.fieldsFullNames[requestedFieldname]
                dfParam = self.df[self.df["channel"]==paramFullName]["value"].to_frame()                
                dfParam.columns=[requestedFieldname]
                firstValue = str(dfParam[requestedFieldname].iloc[0])
                # float
                if re.match(r"\d+.\d+",firstValue):
                    dfParam[requestedFieldname]=dfParam[requestedFieldname].astype(np.float64)
                # int
                elif re.match(r"\d+",firstValue):
                    dfParam[requestedFieldname]=dfParam[requestedFieldname].astype(np.int64)
                # hex -> int
                elif re.match(r"[a-fA-F]+",firstValue):
                    dfParam[requestedFieldname]=dfParam[requestedFieldname].apply(int, base=16)
                # string
                else:
                    pass                
                
                dfParam.index=dfParam.index.astype(np.float64)/1e6 # convert date microsecs to seconds
                rst.append(self.finalizeParam(dfParam,
                                              name=requestedFieldname,
                                              indexName="Timestamp",
                                              origin=self.getFileName(),
                                              minDateSec=minDateSec,maxDateSec=maxDateSec,
                                              shiftDateSec=shiftDateSec,shiftDateRegex=shiftDateRegex,shiftDateInverted=shiftDateInverted,
                                              monitorProgress=monitorProgress.child(f"finalize {requestedFieldname}"),
                                              silent=silent, callback=callback))

            except Exception as e:
                raise Exception("Unable to extract param '"+requestedFieldname+"' from Channels-CSV file '"+self.getFileName()+"' : "+str(e))

        return rst


class CsvFileMgrChannelsPcapRecorder(CsvFileMgr):

    def __init__(self,filename: str,fileIdx: int=0) -> None:
        super().__init__(filename,fileIdx)
        get_logger().debug("CSV-Channels-PcapRecorder File - "+self.getBaseName())
        self.paramFullName=None
        self.paramShortName=None
        
        with open(self.getFileName()) as f:
            curLine=f.readline().strip('\n')
            while "Parameter :" not in curLine: curLine=f.readline().strip('\n')
            self.paramFullName=curLine.replace("# Parameter : ","")
            self.paramShortName=self.paramFullName.split(".")[-1]
                   
    def getCsvFileType(self) -> str:
        return "channels-pcap-recorder"
     
                    
    def getFieldNames(self) -> list:
        if self._fieldNamesList==None:
            super().getFieldNames()
            self._fieldNamesList+=[self.paramShortName]
            self._fieldNamesList.remove("")
        return self._fieldNamesList

    def getNbEntries(self) -> int:
        if self._nbEntries==None:
            with open(self.getFileName()) as f:
                self._nbEntries=len(f.readlines())
                
        return self._nbEntries

    
    def loadParams(self,paramNamesList: list,
                   indexNamesList: list | None=None,
                   monitorProgress: MonitorProgress | None=None,
                   abortEvent: threading.Event | None=None,
                   minDateSec: float | None=None,
                   maxDateSec: float | None=None, 
                   callback: Callable | None=None,
                   shiftDateSec: float | str | pd.DataFrame | None=None,
                   shiftDateRegex: str | None=None,
                   shiftDateInverted: bool | None=None,
                   silent: bool=False) -> list:
        
        # monitorProgress count is done in loadParams of parent class invoked hereunder
        
        replacedParamIdx=None
        if self.paramShortName in paramNamesList:
            replacedParamIdx=paramNamesList.index(self.paramShortName)
            paramNamesList[replacedParamIdx]="engVal"

        if not indexNamesList or len(indexNamesList)==0:
            indexNamesList=["date_pcap_us"]
            
        monitorProgress.set_total_items(2)
        
        dfList=super().loadParams(paramNamesList=paramNamesList,indexNamesList=indexNamesList,
                                                    monitorProgress=monitorProgress.child("load data"),abortEvent=abortEvent)


        if replacedParamIdx!=None:
            dfList[replacedParamIdx].columns=paramNamesList
            dfList[replacedParamIdx].name=self.paramShortName

        return dfList

#################### PRIVATE METHODS ####################


    # clean/fix lines
    def __checkCsvLine(self,lineStr: str,lineNb: int) -> tuple[bool, str | None]:
        
        # remove empty line
        if len(lineStr)==0 \
        or len(lineStr)==1 and lineStr=="\n" \
        or len(lineStr)==2 and lineStr=="\r\n" :
                return False,None
            
        # remove useless header line
        if lineStr.startswith("Formules simples"):
            return False,None
        
        # clean header line from special characters
        if lineNb<=3 and RE_DETECT_HEADER.match(lineStr):
            return True,cleanCsvHeaderLine(lineStr)
        
        return True,lineStr
