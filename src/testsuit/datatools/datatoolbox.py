"""Helpers for data manipulation: parameter naming, date parsing, time ranges and loading data files.

Parameters are pandas Series/DataFrames indexed by dates in seconds since epoch (1970-01-01 UTC).
They may carry two extra attributes: 'name' (parameter name) and 'origin' (source file).

Note: datapack/data2db modules import this one with 'import *', so imports below are part of their namespace.
"""

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
from datetime import datetime, timezone

SUPPORTED_DATAFILE_EXTENSIONS=["h5","hdf5","dxd","d7d","csv","txt","log","tdms","mdf","mf4","influxdbV3.yml","influxdbV2.yml","dat"]
MAX_MTHREAD_WORKERS=10
VERTICAL_OFFSET_DEFAULT_MEAN_VALUE_DURATION_SEC=1

# date formats recognized by getDateParser(), tried in this order
DATE_FORMATS=[
    '%Y-%m-%dT%H:%M:%SZ',
    '%Y-%m-%dT%H:%M:%S.%fZ',
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M:%S.%f',
    '%Y/%m/%d %H:%M:%S',
    '%Y/%m/%d %H:%M:%S.%f',
    '%Y-%m-%dZ%H:%M:%S',
    '%Y-%m-%dZ%H:%M:%S.%f',
    '%m/%d/%Y %H:%M:%S.%f',
    '%d/%m/%Y %H:%M:%S.%f',
]
# National Instruments writes 7 fractional digits (ex: 02/17/2026 14:17:25.3936538),
# more than '%f' accepts: such dates are truncated to microseconds (26 chars) before parsing
NI_DATE_FORMAT='%m/%d/%Y %H:%M:%S.%f'
NI_DATE_MAX_LEN=26

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
def getVerticalOffset(data: pd.DataFrame | pd.Series, offsetExpr: str | None = None) -> float | pd.Series:
    """Get the vertical offset to add to given data.

    :param data: data to offset
    :param offsetExpr: python expression evaluated as a float (ex: "-2.5", "1e3/2"),
        or 'autoz' / 'autoz:XXs' for an auto-zero: minus the mean value over the first XX seconds
        (default VERTICAL_OFFSET_DEFAULT_MEAN_VALUE_DURATION_SEC). None or "0" means no offset.
    :return: the offset (one value per column when data is a DataFrame and auto-zero is used)
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
    """Factor converting dates in the given unit ("ns", "us", "µs", "ms" or "s") to nanoseconds. Default unit is seconds."""

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
    """Convert a date expression into seconds since epoch.

    :param offsetStr: 'now', a value with a unit (ex: "3h", "1.5 D", "200ms"; units D|h|m|s|ms|us|ns)
        or any string accepted by pandas.Timestamp (ex: "2024-01-02 03:04:05"). None gives 0.
    :param timezone: timezone used for 'now'
    """
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
    """Merge several 2D arrays into one DataFrame, limited to the provided x/y domain.

    :param npArraysList: 2D arrays of shape (2, n): row 0 holds x values, row 1 holds y values
    :param xMin, xMax, yMin, yMax: inclusive bounds of the domain; None means unbounded
    :return: DataFrame with an "index" column (x) and one "data_<i>" column per array (i starting at 1),
        one row per distinct x value, sorted by x (NaN where an array has no point at that x)
    """
    frames=[]
    for serieNb, xy in enumerate(npArraysList, start=1):
        x, y = xy[0], xy[1]
        keep = np.ones(x.shape, dtype=bool)
        if xMin is not None: keep &= x >= xMin
        if xMax is not None: keep &= x <= xMax
        if yMin is not None: keep &= y >= yMin
        if yMax is not None: keep &= y <= yMax
        frames.append(pd.DataFrame({"index": x[keep], "data_"+str(serieNb): y[keep]}))

    if not frames:
        return pd.DataFrame(columns=["index"])

    # one row per x value: values of every array sharing that x end up on the same row
    return pd.concat(frames).groupby("index", as_index=False, sort=True).first()


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
    """Magnitude spectrum of a parameter (positive frequencies only).

    The sampling rate is taken as the average over the whole index, so samples are assumed evenly spaced.

    :param df: parameter indexed by time in seconds; df.name is used to name the result
    :param colIdx: column to use when df is a DataFrame
    :return: DataFrame "<name>.fft" of normalized magnitudes, indexed by "Frequency (Hz)"
    """

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
    """Return a function parsing dates written like dateSample into UTC datetime objects.

    Formats are tried in DATE_FORMATS order, then the National Instruments one (see NI_DATE_FORMAT).
    Returns None if dateSample is a plain int or float, so that pd.read_csv() keeps numeric dates.

    :raises Exception: if no known format matches dateSample
    """
    # /!\ need explicit tzinfo for Windows env (internal error near epoch time otherwise)
    for numType in (int, float):
        try:
            numType(dateSample)
            return None
        except (ValueError, TypeError):
            pass

    def _parser(dateFormatStr: str, maxLen: int | None = None) -> Callable[[str], datetime]:
        return lambda x: datetime.strptime(x[:maxLen], dateFormatStr).replace(tzinfo=timezone.utc)

    candidates=[_parser(dateFormatStr) for dateFormatStr in DATE_FORMATS]+[_parser(NI_DATE_FORMAT,NI_DATE_MAX_LEN)]
    for parser in candidates:
        try:
            parser(dateSample)
            return parser
        except (ValueError, TypeError):
            pass

    raise Exception(f"Unable to parse date format '{dateSample}'")


def loadDataframeFromFile(sourceFolderOrFile: str,
                          paramRegexes: str | list[str] | None,
                          indices: str | list[str] | None = None,
                          extensions: list[str] | None = None,
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
    """Load the parameters matching paramRegexes from a data file, a folder of data files or a list of those.

    :param sourceFolderOrFile: file or folder path (folders are scanned recursively), or a list of paths
    :param paramRegexes: regex(es) selecting parameters, as a list or a comma-separated string.
        A regex may be prefixed by a file regex: "fileRegex::paramRegex".
        None only prints the parameters available in each file and returns [].
    :param indices: index parameter to use for each regex (same length as paramRegexes), for formats
        where the time index is a separate parameter
    :param extensions: file extensions to consider (default: all of SUPPORTED_DATAFILE_EXTENSIONS)
    :param excludeParamsRegex: parameters matching this regex are skipped
    :param dryRun: only list matching parameters, without loading values
    :param monitorProgress: progress reporter (a default one is created if None)
    :param abortEvent: when set, loading stops as soon as possible
    :param minDateSec: minimal date in seconds since epoch 1970-01-01
    :param maxDateSec: maximal date in seconds since epoch 1970-01-01
    :param shiftDateSec: seconds, or name of a parameter giving the clock drift, used to shift dates of loaded parameters
    :param shiftDateRegex: only parameters matching this regex are shifted by shiftDateSec
    :param shiftDateInverted: subtract shiftDateSec instead of adding it
    :param callback: called on each loaded parameter; its result replaces the parameter in the returned list.
        It receives a 'monitorProgress' keyword argument if it declares one.
    :param mergeParams: merge parameters of the same name found in several files
    :param silent: fewer progress messages
    :return: loaded parameters (or callback results), in paramRegexes order
    """
    if extensions is None:
        extensions=["."+fileExt for fileExt in SUPPORTED_DATAFILE_EXTENSIONS]

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
    
    def _find_params_task(i: int, paramRegex: str) -> list[pd.DataFrame | pd.Series]:
        paramIndex = indicesList[i] if indicesList is not None else None

        if dryRun:
            monitorProgress.msg(msg=f"listing params for '{paramRegex}' ...")
        else:
            monitorProgress.msg(msg=f"extracting params for '{paramRegex}' ...")
        
        subMp = monitorProgress.child("Regex "+str(paramRegex))
        localDfList = paramScanner.findParams(paramRegex, paramIndex,
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

        dfListRst += _find_params_task(i, paramRegex)

    return dfListRst

def addPoints(paramData: pd.Series | pd.DataFrame, newIndex: pd.Index) -> pd.Series | pd.DataFrame:
    """Add the dates of newIndex to paramData, with values linearly interpolated on the index."""
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
