"""Backend independent part of the plots: what to draw (titles, labels, traces, time range) is
computed here from the criterion configuration (critConf) and the data, backends only draw it."""
from __future__ import annotations

import os,re
from dataclasses import dataclass,field
from datetime import datetime,timezone
from pathlib import Path

import pandas as pd

from testsuit.misc import files
from testsuit.misc.logger import get_logger


def cleanFigureLegendLabel(label: str) -> str:
    """'<file>::<param>' -> '<param>' (without leading '_')"""
    if m:=re.match(r"(.*)::(.*)",label):
        filePath=m.group(1)
        paramPath=m.group(2)
        get_logger().info("file path:"+str(filePath))
        get_logger().info("param path:"+str(paramPath))

        return re.sub("^_*","",paramPath)

    return label


@dataclass
class Trace:
    """One line of a plot. Styles use matplotlib names, translated by the other backends:
    linestyle: solid dotted dashed dashdot, marker: , . o 2 p x * d _ |,
    drawstyle: default steps-pre steps-mid steps-post, color: None (= next color of the cycle) or a color name"""
    label: str
    x: object
    y: object
    z: object = None
    linestyle: str = "solid"
    marker: str = ","
    drawstyle: str = "default"
    color: str | None = None


@dataclass
class FigureSpec:
    name: str
    title: str
    description: str = ""
    xLabel: str = ""
    yLabel: str = ""
    zLabel: str = ""
    figSize: tuple = (12,7)
    traces: list[Trace] = field(default_factory=list)
    # x values are dates in seconds (since epoch or since a time origin), shown as HH:MM:SS (timeRange > 30s) or MM:SS.ffffff
    xTimestamp: bool = False
    timeRange: tuple | None = None
    projection: str | None = None
    # 3D plots: camera position, plotly style (eye and up vectors, center or None for auto)
    cameraEye: tuple = (1.5,1.5,1.5)
    cameraUp: tuple = (0,0,1)
    cameraCenter: tuple | None = None
    # histograms
    nbBins: int = 13


def _title_and_description(critConf: dict) -> tuple[str, str]:
    mainTitle = critConf["title"] if "title" in critConf else critConf["name"]
    description = critConf["description"] if "description" in critConf else ""
    return mainTitle, description


def _trace_style(paramInfo: dict) -> dict:
    return {"linestyle": paramInfo.get("linestyle","solid"),
            "marker": paramInfo.get("marker",","),
            "drawstyle": paramInfo.get("drawstyle","default"),
            "color": paramInfo.get("color",None)}


def _trace_label(paramInfo: dict, paramData: object, pIdx: int) -> str:
    if "name" in paramInfo:
        return cleanFigureLegendLabel(paramInfo["name"])
    if hasattr(paramData,"name"):
        return cleanFigureLegendLabel(paramData.name)
    return cleanFigureLegendLabel("p"+str(pIdx))


def lines_spec(critConf: dict, dataList: list) -> FigureSpec:
    """Figure of the given params (pandas Series or DataFrames indexed by time in seconds, or scalar
    values drawn as horizontal lines over the time range of the others), with their plot info
    (linestyle, marker, drawstyle, color, name)."""
    mainTitle, description = _title_and_description(critConf)
    xLabel=critConf["xLabel"] if "xLabel" in critConf else "Time (s)"
    yLabel=critConf["yLabel"] if "yLabel" in critConf else "Values"

    if len(dataList)==1:
        if "xLabel" not in critConf:
            if hasattr(dataList[0],"index"):
                xLabel=dataList[0].index.name

        if "name" not in dataList[0]:
            if hasattr(dataList[0],"name"):
                dataList[0]["name"]=dataList[0].name
            else:
                dataList[0]["name"]=critConf["name"]
        if m:=re.match(r"(.*)::(.*)",dataList[0]["name"]):
            filePath=m.group(1)
            paramPath=m.group(2)
            if "description" not in critConf:
                description="from "+filePath
            if "yLabel" not in critConf:
                yLabel=paramPath

    # min/max dates, for plotting scalar values
    minDate=None
    maxDate=None
    for paramInfo in dataList:
        if isinstance(paramInfo["data"],(pd.DataFrame,pd.Series)):
            if minDate is None or paramInfo["data"].index[0]< minDate:
                minDate=paramInfo["data"].index[0]
            if maxDate is None or paramInfo["data"].index[-1]> maxDate:
                maxDate=paramInfo["data"].index[-1]

    assert minDate is not None,\
        "CRIT_CHECK\n["+critConf["type"]+"::"+critConf["name"]+"] no min/max date found. "\
            +"Don't know how to plot scalar value on its own, please plot it together with a timeserie."

    spec=FigureSpec(name=critConf["name"],title=mainTitle,description=description,xLabel=xLabel,yLabel=yLabel,
                    figSize=critConf["figSize"] if "figSize" in critConf else (12,7),
                    xTimestamp=critConf["xTimestamp"] if "xTimestamp" in critConf else True,
                    timeRange=(minDate,maxDate),
                    projection=critConf["projection"] if "projection" in critConf else None)

    for pIdx,paramInfo in enumerate(dataList):
        paramData=paramInfo["data"]
        if paramData is None:
            raise Exception(f"trying to plot {paramInfo['name']}, but associated data is None ")

        if isinstance(paramData,(int,float,bool)):
            paramData=pd.Series([paramData,paramData],index=[minDate,maxDate])

        if isinstance(paramData,pd.DataFrame):
            for col in paramData.columns:
                spec.traces.append(Trace(cleanFigureLegendLabel(col),paramData.index,paramData[col].values,**_trace_style(paramInfo)))
        else:
            spec.traces.append(Trace(_trace_label(paramInfo,paramData,pIdx),paramData.index,paramData.values,**_trace_style(paramInfo)))

    if spec.xTimestamp:
        dT=maxDate-minDate
        if dT > 30:
            startDateStr=datetime.fromtimestamp(minDate,tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            endDateStr=datetime.fromtimestamp(maxDate,tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            spec.xLabel=f'Full time range from {startDateStr} to {endDateStr}'
        else:
            dateStr=datetime.fromtimestamp(minDate,tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            spec.xLabel=f'Full time range from {dateStr}'

    return spec


def mergeXYZParams(dataList: list) -> list:
    """3D plots take either one param with 3 columns (x,y,z) or 3 separate
    1D params, taken as X, Y and Z in the given order. The latter are merged
    into a single 3-column DataFrame here."""
    if len(dataList) != 3 or not all(isinstance(p["data"], pd.Series) for p in dataList):
        return dataList

    series = [p["data"] for p in dataList]
    lengths = [len(s) for s in series]
    if len(set(lengths)) != 1:
        names = [p.get("name", getattr(p["data"], "name", "?")) for p in dataList]
        raise ValueError(f"3D plot from 3 separate params requires same lengths, got {dict(zip(names, lengths))}")

    merged = pd.DataFrame({"x": series[0].values, "y": series[1].values, "z": series[2].values}, index=series[0].index)
    mergedInfo = dict(dataList[0])
    mergedInfo["data"] = merged
    mergedInfo["name"] = " / ".join(str(p.get("name", getattr(p["data"], "name", ""))) for p in dataList)
    return [mergedInfo]


def lines3d_spec(critConf: dict, dataList: list) -> FigureSpec:
    """3D figure of the given params: DataFrames with 3 columns (x,y,z), or 3 Series of same length.
    critConf: title, description, xLabel, yLabel, zLabel, figSize, camera_eye, camera_up, camera_center"""
    mainTitle, description = _title_and_description(critConf)
    spec=FigureSpec(name=critConf["name"],title=mainTitle,description=description,
                    xLabel=critConf["xLabel"] if "xLabel" in critConf else "X",
                    yLabel=critConf["yLabel"] if "yLabel" in critConf else "Y",
                    zLabel=critConf.get("zLabel","Z"),
                    figSize=critConf["figSize"] if "figSize" in critConf else (12,7),
                    projection="3d",
                    cameraEye=tuple(critConf.get("camera_eye",(1.5,1.5,1.5))),
                    cameraUp=tuple(critConf.get("camera_up",(0,0,1))),
                    cameraCenter=critConf.get("camera_center"))

    for pIdx,paramInfo in enumerate(mergeXYZParams(dataList)):
        paramData=paramInfo["data"]
        if not isinstance(paramData, pd.DataFrame):
            raise ValueError(f"3D plot requires DataFrame with 3 columns, got {type(paramData)}")
        if paramData.shape[1] != 3:
            raise ValueError(f"3D plot requires exactly 3 columns, got {paramData.shape[1]}")
        spec.traces.append(Trace(_trace_label(paramInfo,paramData,pIdx),
                                 paramData.iloc[:,0].values,paramData.iloc[:,1].values,paramData.iloc[:,2].values,
                                 **_trace_style(paramInfo)))
    return spec


def histogram_spec(critConf: dict, dataInfo: dict, colIdx: int = 0, nbSegments: int = 13) -> FigureSpec:
    """Histogram of the values of dataInfo["data"] (a Series, or the column colIdx of a DataFrame)"""
    assert(isinstance(dataInfo,dict))
    if "data" not in dataInfo:
        raise Exception(f"given data dict missing 'data' entry: {dataInfo.keys()}")

    df=dataInfo["data"]
    if not isinstance(df,(pd.DataFrame,pd.Series)):
        raise ValueError(f"given data is not a pandas DataFrame or Series: {type(df)}")

    mainTitle=critConf["title"] if "title" in critConf else critConf["name"]
    description=critConf["description"] if "description" in critConf else getattr(df,"name","")
    xLabel=critConf["xLabel"] if "xLabel" in critConf else "value segments"
    yLabel=critConf["yLabel"] if "yLabel" in critConf else "nb occurences"

    if m:=re.match(r"(.*)::(.*)",dataInfo.get("name","")):
        filePath=m.group(1)
        paramPath=m.group(2)
        if "description" not in critConf:
            description="from "+filePath
        if "yLabel" not in critConf:
            yLabel="nb occurences within "+paramPath

    values=df[df.columns[colIdx]] if isinstance(df,pd.DataFrame) else df
    spec=FigureSpec(name=critConf["name"],title=mainTitle,description=description,xLabel=xLabel,yLabel=yLabel,
                    figSize=critConf["figSize"] if "figSize" in critConf else (12,7),nbBins=nbSegments)
    spec.traces.append(Trace(str(values.name),None,values.dropna().values,color=dataInfo.get("color")))
    return spec


def timeline_dataframe(eventsData: dict) -> pd.DataFrame:
    """Events sorted by date.

    :param eventsData (dict): data of each event, by name: date, color, level, linestyle, fontstyle, title (optional)
    :return: DataFrame with columns Date, Event, Color, Level, Title, Linestyle, Fontstyle
    """
    dfEventsData = {"dates": [], "names": [], "colors": [], "levels": [], "linestyles": [], "fontstyles": [], "titles": []}
    for eventName in eventsData:
        dfEventsData["names"] += [eventName]
        dfEventsData["titles"] += [eventsData[eventName]["title"]] if "title" in eventsData[eventName] else [eventName]
        dfEventsData["dates"] += [1e9 * eventsData[eventName]["date"]]
        dfEventsData["colors"] += [eventsData[eventName]["color"]]
        dfEventsData["levels"] += [eventsData[eventName]["level"]]
        dfEventsData["linestyles"] += [eventsData[eventName]["linestyle"]]
        dfEventsData["fontstyles"] += [eventsData[eventName]["fontstyle"]]

    timeline_df = pd.DataFrame(data={
        "Date": dfEventsData["dates"],
        "Event": dfEventsData["names"],
        "Color": dfEventsData["colors"],
        "Level": dfEventsData["levels"],
        "Title": dfEventsData["titles"],
        "Linestyle": dfEventsData["linestyles"],
        "Fontstyle": dfEventsData["fontstyles"]
    })
    timeline_df["Date"] = pd.to_datetime(timeline_df["Date"])
    timeline_df = timeline_df.sort_values("Date").reset_index(drop=True)

    return timeline_df


def timeline_label_position(idx: int) -> tuple[float, bool]:
    """Y position of the label of the idx-th event (chronological order), alternating above/below the timeline.

    :return: tuple (Y position, side_above boolean)
    """
    POSITIONS_LIST = [2,4,6,8]
    LABEL_MARGIN = 0.6

    side_above = (idx % 2 == 0)
    figLevel =  (1 if side_above else -1) * POSITIONS_LIST[idx % len(POSITIONS_LIST)] * LABEL_MARGIN

    return figLevel, side_above


def timeline_font(fontstyle: str) -> tuple[str, str]:
    """(weight, style) of an event label fontstyle: normal, bold or italic"""
    weight="normal"
    style="normal"
    if fontstyle=="bold": weight=fontstyle
    elif fontstyle=="italic": style=fontstyle
    return weight, style


def figure_file_path(critConf: dict, figuresRelPath: str, suffix: str) -> str:
    """<results>/<figuresRelPath>/<critConf name><suffix>, folder created if needed"""
    figPath = critConf["test_run_config"]["results"] + os.sep + figuresRelPath
    Path(figPath).mkdir(parents=True, exist_ok=True)
    return figPath + os.sep + files.normalizeFileName(critConf["name"]) + suffix
