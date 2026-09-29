
#
# some various helpers related to data manipulation
#

import sys,os,re
import threading
from time import sleep

import concurrent.futures
from concurrent.futures import ThreadPoolExecutor,wait
from collections.abc import Callable

import numpy as np
import pandas as pd

from testsuit.misc.logger import get_logger
from testsuit.misc.MonitorProgress import MonitorProgress
from datetime import datetime

SUPPORTED_DATAFILE_EXTENSIONS=["h5","hdf5","dxd","d7d","csv","txt","log","tdms","mdf","mf4","influxdbV3.yml","influxdbV2.yml","dat"]
MAX_MTHREAD_WORKERS=10
VERTICAL_OFFSET_DEFAULT_MEAN_VALUE_DURATION_SEC=1

# add deps folder (relative path to this module)
sys.path.append(os.path.realpath(os.path.dirname( __file__[:-1] if __file__.endswith('.pyc') else __file__ ) +os.sep+".."))

#######################
def renameParam(paramName: str, regexMatch: str, regexReplace: str = "") -> str:
    """Rename given string using provided regexes. Replace also spaces by '_' and slashes by '.'"""

    rstParamName=paramName
    try:
        rstParamName=re.sub(regexMatch,regexReplace,paramName)  
        #print(f"renaming {paramName} '{regexMatch}' -> '{regexReplace}' {rstParamName}")
    except Exception as e:
        raise ValueError("unable to rename param '"+paramName+"' with expressions '"+regexMatch+"' / '"+regexReplace+"' : "+str(e))

    return rstParamName.replace(" ","_").replace("/",".")

#######################
def renameDfCols(df: pd.DataFrame | pd.Series, regexMatch: str | None = None, regexReplace: str | None = None) -> list[str]:
    """rename all cols of the dataframe applying provided regex

        @return list of cols names
    """
    paramNames=[]
    if isinstance(df,pd.DataFrame):
        for curParamFullPath in list(df.columns):
            curParamName=str(curParamFullPath)
            if regexMatch and len(regexMatch)>0:
                curParamName=renameParam(curParamName,regexMatch,regexReplace)            
            paramNames+=[curParamName]

    elif isinstance(df,pd.Series):
        curParamName=str(df.name)
        if regexMatch and len(regexMatch)>0:
            curParamName=renameParam(curParamName,regexMatch,regexReplace)            
        paramNames+=[curParamName]
        
    return paramNames

#######################
def getFileParamMatchRegex(regexStr: str) -> re.Pattern[str]:
    """compile given regex as a file::param regex (used to search params among several file managers)"""
    fileRegex=None
    paramRegex=regexStr
    if "::" in regexStr:
        fileRegex=regexStr.split("::")[0]
        paramRegex=regexStr.split("::")[1]

    if not paramRegex.startswith('^'):
        paramRegex=".*"+paramRegex
    if not paramRegex.endswith('$'):
        paramRegex=paramRegex+".*"

    regexStr=paramRegex

    if fileRegex:
        if not fileRegex.startswith('^'):
            fileRegex=".*"+fileRegex
        if not fileRegex.endswith('$'):
            fileRegex=fileRegex+".*"

        regexStr=fileRegex+"::"+paramRegex

    return re.compile(regexStr)

#######################
def getDfName(df: pd.DataFrame | pd.Series) -> str:
    """Get a name for given pandas dataframe or Serie"""
    if not isinstance(df,(pd.DataFrame,pd.Series)):
        return str(type(df))

    dfName=None
    if hasattr(df,"name"):
        dfName=df.name
    if dfName is None:
        if len(df.columns)==1:
            dfName=df.columns[0]
        else:
            dfName=re.sub(r"-[^-]+$","",df.columns[0])

    return dfName

#######################
def getVerticalOffset(data: pd.DataFrame, offsetExpr: str | None = None) -> float:
    """Get the vertical offset of given value. 

    :data (DataFrame): data to offset
    :offsetExpr (str): python expression of vertical value to apply. If 'autoz:xxs', then perform auto-zero based on average value over xx first seconds of the param.
    """
    
    if not offsetExpr:
            offsetExpr="0"

    offsetVal=0
    if offsetExpr!="0":            
        if "autoz" in offsetExpr:
            if offsetExpr=="autoz":
                meanValDuration=VERTICAL_OFFSET_DEFAULT_MEAN_VALUE_DURATION_SEC
            else:
                m=re.match(r"autoz:(\d+(\.\d+)?)s",offsetExpr)
                if not m:
                    raise ValueError("Auto-zero offset must respect following syntax: 'auto:XXs', where x is the amount of seconds on which computing mean value for auto-zero")
                meanValDurationStr=m.group(1)
                meanValDuration=float(meanValDurationStr)

            offsetVal=- data[data.index < data.index[0]+meanValDuration].mean()            
                    
        else:
            try:
                offsetVal=eval("float("+offsetExpr+")")
            except Exception as e:
                raise SyntaxError("Provided expression for vertical offset '"+str(offsetExpr)+"' could not be evaluated as a float number : "+str(e))

    return offsetVal


#######################
def getCoefConvToNanosec(dateUnitStr: str | None = None) -> float:
    
    dateCoefToNanosec=1e9
    if dateUnitStr:
        match dateUnitStr:
            case "ns": dateCoefToNanosec=1
            case "us": dateCoefToNanosec=1e3
            case "µs": dateCoefToNanosec=1e3
            case "ms": dateCoefToNanosec=1e6
            case "s": dateCoefToNanosec=1e9
            case _:
                raise ValueError("unexpected date unit '"+str(dateUnitStr)+"' (ns|us|ms|s)")
    else:
        get_logger().debug("using default 'dateUnit' as seconds (so dates will be x1e9 to be converted to nanoseconds in influxdb)")

    return dateCoefToNanosec
    
#######################
def getDateOffsetSec(offsetStr: str | None, timezone: str = "Europe/Paris") -> float:

    if offsetStr is None:
        return 0
    
    dateOffset=None

    if offsetStr=='now':
        dateOffset = pd.Timestamp(pd.Timestamp.today(), tz=timezone).timestamp()
    else:
        dateOffsetStr=offsetStr
        dateOffsetUnit=""
        m = re.match(r"\s*(\S+)\s*(D|h|m|s|ms|us|ns)\s*",offsetStr)
        if m:
            dateOffsetStr=float(m.group(1))
            dateOffsetUnit=m.group(2)
        try:
            dateOffset = pd.Timestamp(dateOffsetStr,unit=dateOffsetUnit).timestamp()
        except Exception as e:
            raise ValueError("wrong syntax for date offset. Expecting a 'pandas.Timestamp' syntax, "+\
                                        "got '"+str(dateOffsetStr)+"' (unit='"+str(dateOffsetUnit)+"'): "+str(e))
        
    return dateOffset

#######################
def zoomAndMerge2DData(npArraysList: list[np.ndarray], xMin: float | None = None, xMax: float | None = None,
                       yMin: float | None = None, yMax: float | None = None) -> pd.DataFrame:

    """Merge several 2D arrays into a Pandas dataframe, limited to provided min/max domain

    Parameters:
        npArraysList (list of np arrays) : a list of 2D NP arrays (x,y) horizontally stacked

    Returns:
        Pandas dataframe with optimized/merged rows, limited to given x,y domain
    """
    #print(f"zoomAndMerge2DData x=[{xMin},{xMax}] y=[{yMin},{yMax}]")
    
    finalDf=pd.DataFrame()
    serieNb=0
    for nbArray in npArraysList:
        serieNb+=1
        xy_data_filtered=nbArray
        if xMin:
            xy_data_filtered=np.where(xy_data_filtered[0]>xMin,xy_data_filtered,np.nan)
        if xMax:
            xy_data_filtered=np.where(xy_data_filtered[0]<xMax,xy_data_filtered,np.nan)
        if yMin:
            xy_data_filtered=np.where(xy_data_filtered[1]>yMin,xy_data_filtered,np.nan)
        if yMax:
            xy_data_filtered=np.where(xy_data_filtered[1]<yMax,xy_data_filtered,np.nan)
        
        # need to verticalise arrays for CSV dump (transpose)
        df = pd.DataFrame(np.transpose(xy_data_filtered),columns=["index","data_"+str(serieNb)])
        df.dropna(how='all',inplace=True) # removing empty rows
        finalDf=pd.concat([finalDf,df])

    finalDf.sort_values("index",inplace=True) # sorting by index
    finalDf=finalDf.groupby(level=0).first() # group cols sharing same index

    return finalDf


#######################
def squeezeData(param: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """remove 1-sized dimensions, squeezing (n,1) dataframes as simple Series,  which make subsequent processing smoother"""

    if isinstance(param,pd.DataFrame) and len(param.shape)>1 and param.shape[1]==1:
        
        name=param.columns[0]
        # keep track of potential param name prefix
        if "::" in param.name:
            name=param.name.split("::")[0]+"::"+name
        pdSerie=param[param.columns[0]]
        pdSerie.name=name
        if hasattr(param,"origin"): pdSerie.origin=param.origin
        
        return pdSerie

    return param
    
#######################
def timerange(paramData: pd.DataFrame | pd.Series, minDate: float | None = None, maxDate: float | None = None) -> pd.DataFrame | pd.Series:
    """restrict given data to provided min/max date

    :minDate (float): nb seconds min date of data to retrieve
    :maxDate (float): nb seconds max date of data to retrieve
    :return: original data truncated to provided time range"""

    origin=paramData.origin if hasattr(paramData,"origin") else None
    name=paramData.name if hasattr(paramData,"name") else None
    try:
        if minDate is not None:
            paramData=paramData[paramData.index >= minDate ]

        if maxDate is not None:
            paramData=paramData[paramData.index <= maxDate]

    except Exception as e:
        raise Exception("Unable to apply timerange on provided data: "+str(e))

    if origin: paramData.origin=origin
    if name: paramData.name=name 

    return squeezeData(paramData)
    

#######################
def getDfFFT(df: pd.DataFrame | pd.Series, colIdx: int = 0) -> pd.DataFrame:

    if isinstance(df,(pd.DataFrame)):
        param_name=df.columns[colIdx]
        param_values=df[param_name]
    elif isinstance(df,(pd.Series)):
        param_name=df.name
        param_values=df
        
    nbValues=len(param_values)
    dT=df.index[-1] - df.index[0]
    avgSamplingRateHz=nbValues/dT
    
    fft_vals=np.fft.fft(param_values)
    fft_freqs=np.fft.fftfreq(nbValues,d=1/avgSamplingRateHz)

    pos_freqs=fft_freqs[:nbValues // 2]
    pos_magnitude=np.abs(fft_vals[:nbValues // 2]) * (2 / nbValues)  # Normalize amplitude

    fftDf=pd.DataFrame({df.name+".fft":pos_magnitude},index=pos_freqs)
    fftDf.name=df.name+".fft"
    fftDf.columns=[param_name+".fft"]
    fftDf.index.name="Frequency (Hz)"

    return fftDf


#######################


#######################
def getDateParser(dateSample: str) -> Callable[[str], datetime] | None:
    """Return a function converting a string of similar format to a datetime object
        If date is simple float or int, returns None (for compat with pd.read_csv())"""
    from datetime import datetime, timezone

    # /!\ need explicit tzinfo for Windows env (internal error near epoch time otherwise)

    try: 
        int(dateSample) 
        return None
    except: pass
    try: 
        float(dateSample)
        return None
    except: pass
    try:
        dateFormatStr='%Y-%m-%dT%H:%M:%SZ'
        datetime.strptime(dateSample, dateFormatStr)
        return lambda x: datetime.strptime(x, dateFormatStr).replace(tzinfo=timezone.utc)
    except: pass
    try:
        dateFormatStr='%Y-%m-%dT%H:%M:%S.%fZ'
        datetime.strptime(dateSample, dateFormatStr)
        return lambda x: datetime.strptime(x, dateFormatStr).replace(tzinfo=timezone.utc)
    except: pass
    try:
        dateFormatStr='%Y-%m-%d %H:%M:%S'
        datetime.strptime(dateSample, dateFormatStr)
        return lambda x: datetime.strptime(x, dateFormatStr).replace(tzinfo=timezone.utc)
    except: pass
    try:
        dateFormatStr='%Y-%m-%d %H:%M:%S.%f'
        datetime.strptime(dateSample, dateFormatStr)
        return lambda x: datetime.strptime(x, dateFormatStr).replace(tzinfo=timezone.utc)
    except: pass

    try:
        dateFormatStr='%Y/%m/%d %H:%M:%S'
        datetime.strptime(dateSample, dateFormatStr)
        return lambda x: datetime.strptime(x, dateFormatStr).replace(tzinfo=timezone.utc)
    except: pass
    try:
        dateFormatStr='%Y/%m/%d %H:%M:%S.%f'
        datetime.strptime(dateSample, dateFormatStr)
        return lambda x: datetime.strptime(x, dateFormatStr).replace(tzinfo=timezone.utc)
    except: pass

    try:
        dateFormatStr='%Y-%m-%dZ%H:%M:%S'
        datetime.strptime(dateSample, dateFormatStr)
        return lambda x: datetime.strptime(x, dateFormatStr).replace(tzinfo=timezone.utc)
    except: pass
    try:
        dateFormatStr='%Y-%m-%dZ%H:%M:%S.%f'
        datetime.strptime(dateSample, dateFormatStr)
        return lambda x: datetime.strptime(x, dateFormatStr).replace(tzinfo=timezone.utc)
    except: pass

    try:
        dateFormatStr='%m/%d/%Y %H:%M:%S.%f'
        date=datetime.strptime(dateSample, dateFormatStr)
        return lambda x: datetime.strptime(x, dateFormatStr).replace(tzinfo=timezone.utc)
    except: pass
    
    try:
        dateFormatStr='%d/%m/%Y %H:%M:%S.%f'
        datetime.strptime(dateSample, dateFormatStr)
        return lambda x: datetime.strptime(x, dateFormatStr).replace(tzinfo=timezone.utc)
    except: pass

    # for fucking NI twisted format  02/17/2026 14:17:25.3936538
    # we truncate timestamp to microseconds
    try:                    
        dateFormatStr='%m/%d/%Y %H:%M:%S.%f'                    
        datetime.strptime(dateSample[:26], dateFormatStr)
        return lambda x: datetime.strptime(x[:26], dateFormatStr).replace(tzinfo=timezone.utc)
    except: pass

    raise Exception(f"Unable to parse date format '{dateSample}'")



def loadDataframeFromFile(sourceFolderOrFile: str,
                          paramRegexes: str | list[str] | None,
                          indices: str | list[str] | None = None,
                          extensions: list[str] = ["."+fileExt for fileExt in SUPPORTED_DATAFILE_EXTENSIONS],
                          excludeParamsRegex: str | None = None,
                          dryRun: bool = False,
                          monitorProgress: MonitorProgress | None = None,
                          abortEvent: threading.Event | None = None,
                          minDateSec: float | None = None,
                          maxDateSec: float | None = None,
                          shiftDateSec: float | str | None = None,
                          shiftDateRegex: str | None = None,
                          shiftDateInverted: bool | None = None,
                          callback: Callable | None = None,
                          mergeParams: bool = True,
                          silent: bool = False) -> list[pd.DataFrame | pd.Series]:
    """Load required paramRegexes from given data source folder or file.
    
    :param minDateSec (float): minimal date in seconds since epoch 1970-01-01
    :param maxDateSec (float): maximal date in seconds since epoch 1970-01-01
    :param shiftDateSec (float|str): float or param name to use for shifting date of loaded paramRegexes
    :param shiftDateRegex (str): regex to be used to select to which param we shall apply shiftDateSec
    """

    if monitorProgress is None:
        monitorProgress=MonitorProgress(name="loadDataframeFromFile")

    monitorProgress.msg(msg=f"scanning data sources: {sourceFolderOrFile}")
    from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr
    
    paramScanner = FolderParamMgr(sourceFolderOrFile, 
                            supportedExtensions=extensions, minDateSec=minDateSec, maxDateSec=maxDateSec,
                            shiftDateSec=shiftDateSec, shiftDateRegex=shiftDateRegex, 
                            shiftDateInverted=shiftDateInverted)
    
    # if no paramRegexes provided, we simply list parameters found in file
    if paramRegexes is None:
        for fileMgr in paramScanner.getFileMgrs():
            if len(paramScanner.getFileMgrs()) > 1:
                print("### " + fileMgr.getBaseName() + " ###")
            print(fileMgr.dumpParamsTxt())
        return []

    # if paramRegexes list found, we extract values
    if isinstance(paramRegexes, list):
        paramRegexesList = paramRegexes
    else:
        paramRegexesList = paramRegexes.split(",")
    indicesList = None

    # use provided paramRegexes as indices if length is matching
    if indices is not None and len(indices) > 0:
        indicesList = []
        if isinstance(indices, list):
            indicesList = indices
        else:
            indicesList = indices.split(",")

        if len(indicesList) != len(paramRegexesList):
            get_logger().error("Provided 'indices' argument has not same length than paramRegexes argument"
                               +f"\n{paramRegexesList}"
                               +f"\n{indicesList}")
            return []
    
    dfListRst = []
    nbParamRegexes = len(paramRegexesList)
    monitorProgress.set_total_items(nbParamRegexes)
    
    def _find_params_task(args: tuple[int, str]) -> list[pd.DataFrame | pd.Series]:
        i, paramRegex = args
        paramIndex = indicesList[i] if indicesList is not None else None

        if dryRun:
            monitorProgress.msg(msg=f"listing params for '{paramRegex}' ...")
        else:
            monitorProgress.msg(msg=f"extracting params for '{paramRegex}' ...")
        
        subMp = monitorProgress.child("Regex "+str(paramRegex))
        localDfList = paramScanner.findParams(paramRegex, paramIndex if paramIndex is not None else None,
                                            dryRun=dryRun, monitorProgress=subMp,
                                            abortEvent=abortEvent,
                                            mergeParams=mergeParams, silent=silent,
                                            excludeParamsRegex=excludeParamsRegex, callback=callback)
        
        if len(localDfList) == 0:
            monitorProgress.msg(msg=f"No param matching '{paramRegex}' found in '{sourceFolderOrFile}', sorry.", msgSeverity="warning")
        else:
            paramRegexStrMsg=f" for '{paramRegex}'" if paramRegex else ""                
            if dryRun:
                monitorProgress.msg(msg=f"found {len(localDfList)} params{paramRegexStrMsg}", msgSeverity="success")
            else:
                monitorProgress.msg(msg=f"extracted {len(localDfList)} params{paramRegexStrMsg}", msgSeverity="success")

        return localDfList

    for i, paramRegex in enumerate(paramRegexesList):
        # check abort
        if abortEvent and abortEvent.is_set():
            break

        dfListRst += _find_params_task((i, paramRegex))

    
    return dfListRst

def addPoints(paramData: pd.Series | pd.DataFrame, newIndex: pd.Index) -> pd.Series | pd.DataFrame:
    """Apply new index to given paramData, applying forward-fill to get corresponding values"""
    result = paramData.reindex(paramData.index.union(newIndex))
    result = result.loc[~result.index.isna()]
    return result.interpolate('index').sort_index()

def overlap(mainData: pd.Series | pd.DataFrame, secondaryData: pd.Series | pd.DataFrame) -> tuple[pd.Series | pd.DataFrame | None, pd.Series | pd.DataFrame | None]:
    """Align indices of given data to the overlapping time range, interpolating values of secondary if timestamps are not matching."""
    start = max(mainData.index[0], secondaryData.index[0])
    end = min(mainData.index[-1], secondaryData.index[-1])
    
    if start > end:
        return None, None
        
    main_slice = mainData[(mainData.index >= start) & (mainData.index <= end)]
    
    # Interpolate using full secondary data to preserve boundary anchor points,
    # then select only rows matching main_slice index exactly
    reduced_sec = addPoints(secondaryData, main_slice.index).loc[main_slice.index]
    
    return main_slice, reduced_sec
