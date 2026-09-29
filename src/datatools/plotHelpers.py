
from __future__ import annotations

import os,re

# some version compatibility issue
#import plotly.express as px
#import plotly.graph_objects as go
# needed for plotly write_image
#import keleido 

from datetime import datetime,timezone

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

import pandas as pd
import numpy as np
from pathlib import Path

from misc import files
import random

# Source - https://stackoverflow.com/a
# Posted by Matthias Luh, modified by community. See post 'Timeline' for change history
# Retrieved 2025-12-11, License - CC BY-SA 4.0


from misc.logger import get_logger  

def cleanFigureLegendLabel(label: str) -> str:
        if m:=re.match(r"(.*)::(.*)",label):
            filePath=m.group(1)
            paramPath=m.group(2)
            get_logger().info("file path:"+str(filePath))
            get_logger().info("param path:"+str(paramPath))
            
            return re.sub("^_*","",paramPath)
        
        return label

def plotDataHistogram(critConf: dict,
                      dataInfo: dict,
                      colIdx: int = 0,
                      nbSegments: int = 13,
                      interactive: bool = True,
                      figuresRelPath: str = "figures") -> str:
    """Plot histogram of nb occurences by segments of values

    :param critconf (dict): Dictionnary with figure configuration params (title,description,xLabel,yLabel,figSize)
    :param df (pandas dataframe): the dataframe to use
    :param interactive (bool): is interactive or just of file to generate
    
    :return: path of generated file
    """

    assert(isinstance(dataInfo,dict))
    if "data" not in dataInfo:
        raise Exception(f"given data dict missing 'data' entry: {dataInfo.keys()}")
    
    df=dataInfo["data"]
    if not isinstance(df,(pd.DataFrame,pd.Series)):
        raise ValueError(f"given data is not a pandas DataFrame or Series: {type(df)}")

    # avoid plots to pop-up in Jupyter GUI when plotting via mexploit       
    if not interactive:        
        plt.ioff()

    mainTitle=critConf["title"] if "title" in critConf else critConf["name"]
    description=critConf["description"] if "description" in critConf else df.name
    xLabel=critConf["xLabel"] if "xLabel" in critConf else "value segments"
    yLabel=critConf["yLabel"] if "yLabel" in critConf else "nb occurences"

    if hasattr(dataInfo,"index"):
        if "xLabel" not in critConf:
            xLabel="value segments of "+dataInfo.index.name

    if m:=re.match(r"(.*)::(.*)",dataInfo["name"]):
        filePath=m.group(1)
        paramPath=m.group(2)
        if "description" not in critConf:
            description="from "+filePath
        if "yLabel" not in critConf:
            yLabel="nb occurences within "+paramPath

    figSize=critConf["figSize"] if "figSize" in critConf else (12,7)
    fig=plt.figure(critConf["name"],layout="constrained",figsize=figSize)
    if isinstance(df,(pd.DataFrame)):
        ax = df.plot.hist(column=[df.columns[colIdx]],bins=nbSegments,alpha=0.8,rwidth=0.95)
    elif isinstance(df,(pd.Series)):
        ax = df.plot.hist(bins=nbSegments,alpha=0.9,rwidth=0.95)
    else:
        raise Exception(f"unexpected case: datatype {type(df)}")
    fig.suptitle(mainTitle,fontsize=14)
    ax.set_title(description)
    ax.set_xlabel(xLabel)
    ax.set_ylabel(yLabel)
    plt.grid(False)

    #ax.legend(dataHandles,legendsLabels,loc="upper right")

    figPath=critConf["test_run_config"]["results"]+os.sep+figuresRelPath
    
    Path(figPath).mkdir(parents=True, exist_ok=True)
    filePath=figPath+os.sep+files.normalizeFileName(critConf["name"])+".svg"
    fig.savefig(filePath)

    plt.clf()

    get_logger().info("created histogram figure '"+filePath+"'")

    return filePath


def plotData_matplotlib(critConf: dict,
                        dataList: list,
                        interactive: bool = True,
                        figuresRelPath: str = "figures",
                        fig: plt.Figure | None = None,
                        ax: plt.Axes | None = None,
                        dataHandles: list | None = None,
                        legendsLabels: list | None = None) -> str | tuple:
    """Plot given params in a single figure

    :param critconf (dict): Dictionnary with figure configuration params (title,description,xLabel,yLabel,figSize)
    :param dataList ([dict]): list of params to be plot. Each one shall have plot info linestyle, marker, drawstyle and color
    :param interactive (bool): is interactive or just of file to generate
    
    :return: path of generated file
    """
    
    def sec_to_timestamp_s(x: float, pos: float) -> str:
        """Convert seconds to HH:MM:SS format"""
        # Explicitly convert numpy.float64 to Python float
        total_seconds = float(x)
        hours = int(total_seconds // 3600) % 24
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        #micros = int((total_seconds % 1) * 1000000)  # 10^6 for microseconds
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"#.{micros:06d}"
    
    def sec_to_timestamp_us(x: float, pos: float) -> str:
        """Convert seconds to SS.uuuuuu format"""
        # Explicitly convert numpy.float64 to Python float
        total_seconds = float(x)
        hours = int(total_seconds // 3600) % 24
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        micros = int((total_seconds % 1) * 1000000)  # 10^6 for microseconds
        return f"{minutes:02d}:{seconds:02d}.{micros:06d}s"

    # avoid plots to pop-up in Jupyter GUI when plotting via mexploit       
    if not interactive:        
        plt.ioff()

    if not dataHandles: dataHandles=[]
    if not legendsLabels: legendsLabels=[]
    mainTitle=critConf["title"] if "title" in critConf else critConf["name"]
    description=critConf["description"] if "description" in critConf else ""
    xLabel=critConf["xLabel"] if "xLabel" in critConf else "Time (s)"
    yLabel=critConf["yLabel"] if "yLabel" in critConf else "Values"
    figSize=critConf["figSize"] if "figSize" in critConf else (12,7)
    xTimestamp=critConf["xTimestamp"] if "xTimestamp" in critConf else True
    projection=critConf["projection"] if "projection" in critConf else None

    if len(dataList)==1:
        if "xLabel" not in critConf:
            if hasattr(dataList[0],"index"):
                xLabel=dataList[0].index.name

        if "name" not in dataList[0]:
            if hasattr(dataList[0],"name"):
                dataList[0]["name"]=getattr(dataList[0],"name")
            else:
                dataList[0]["name"]=critConf["name"]
        if m:=re.match(r"(.*)::(.*)",dataList[0]["name"]):
            filePath=m.group(1)
            paramPath=m.group(2)
            if "description" not in critConf:
                description="from "+filePath
            if "yLabel" not in critConf:
                yLabel=paramPath

    if not fig:
        fig=plt.figure(critConf["name"],layout="constrained",figsize=figSize)
        ax=fig.add_subplot(1,1,1,projection=projection)
    fig.suptitle(mainTitle,fontsize=14)
    ax.set_title(description)
    ax.set_xlabel(xLabel)
    ax.set_ylabel(yLabel)
 
    plt.grid(True)

    # Highlighting the time period from 15.12.2022 to 1.01.2023 and customizing the shaded area
    #plt.axvspan(datetime(2022, 12, 15), datetime(2023, 1, 1), facecolor='yellow', alpha=0.5, hatch='/', edgecolor='red', linewidth=5)


    pIdx=0
    minDate=None
    maxDate=None

    # update min/max dates for plotting scalar values    
    for paramInfo in dataList:        
        if isinstance(paramInfo["data"],(pd.DataFrame,pd.Series)):
            if not minDate or paramInfo["data"].index[0]< minDate:
                minDate=paramInfo["data"].index[0]
            if not maxDate or paramInfo["data"].index[-1]> maxDate:
                maxDate=paramInfo["data"].index[-1]
            
    assert minDate!=None,\
        "CRIT_CHECK\n["+critConf["type"]+"::"+critConf["name"]+"] no min/max date found. "\
            +"Don't know how to plot scalar value on its own, please plot it together with a timeserie."

    for paramInfo in dataList:
        paramData=paramInfo["data"]
        if paramData is None:
            raise Exception(f"trying to plot {paramInfo['name']}, but associated data is None ")
        
        if isinstance(paramData,(int,float,bool)):
            paramData=pd.Series([paramData,paramData],index=[minDate,maxDate])

        curDataHandles = list(
                ax.plot(paramData.index,paramData,
                    linestyle = paramInfo["linestyle"] if "linestyle" in paramInfo else "solid", # solid dotted dashed dashdot
                    marker = paramInfo["marker"] if "marker" in paramInfo else ",", # , . o  2 p x * d_ |
                    drawstyle= paramInfo["drawstyle"] if "drawstyle" in paramInfo else "default", # default, steps-pre, steps-mid, steps-post
                    color = paramInfo["color"]) # None (= random), blue, cyan, green, orange, black, yellow, red, ...
            )

        if isinstance(paramData,pd.DataFrame):
            idx=0
            for dataHandle in curDataHandles:
                dataHandles.append(dataHandle)
                legendsLabels.append(cleanFigureLegendLabel(paramData.columns[idx]))
                idx+=1
        else:
            dataHandles.append(curDataHandles[0])
            if "name" in paramInfo:            
                legendsLabels.append(cleanFigureLegendLabel(paramInfo["name"]))
            elif hasattr(paramData,"name"):
                legendsLabels.append(cleanFigureLegendLabel(paramData.name))
            else:
                legendsLabels.append(cleanFigureLegendLabel("p"+str(pIdx)))

        pIdx+=1
               
    if xTimestamp:
        dT=maxDate-minDate
        if dT > 30:
            ax.xaxis.set_major_formatter(FuncFormatter(sec_to_timestamp_s))
            ax.tick_params(axis='x', rotation=70) 
            startDateStr=datetime.fromtimestamp(minDate,tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            endDateStr=datetime.fromtimestamp(maxDate,tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            ax.set_xlabel(f'Full time range from {startDateStr} to {endDateStr}')
            secLocatorInterval=max(1, int(dT/10))
            #ax.xaxis.set_major_locator(mdates.SecondLocator(interval=secLocatorInterval))
        else:
            ax.xaxis.set_major_formatter(FuncFormatter(sec_to_timestamp_us))
            dateStr=datetime.fromtimestamp(minDate,tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")           
            ax.set_xlabel(f'Full time range from {dateStr}')
            secLocatorInterval=max(1, int(dT/10))
            #ax.xaxis.set_major_locator(mdates.SecondLocator(interval=secLocatorInterval))
            
        
    ax.legend(dataHandles,legendsLabels,loc="upper right")

    if interactive: 
        return fig,ax,dataHandles,legendsLabels
    else: 
        figPath=critConf["test_run_config"]["results"]+os.sep+figuresRelPath    
        Path(figPath).mkdir(parents=True, exist_ok=True)
        filePath=figPath+os.sep+files.normalizeFileName(critConf["name"])+".svg"
        fig.savefig(filePath)
        get_logger().info("created plot figure '"+filePath+"'")
        plt.clf()
        return filePath


def plotData(critConf: dict,
             dataList: list,
             interactive: bool = True,
             figuresRelPath: str = "figures",
             fig: plt.Figure | None = None,
             ax: plt.Axes | None = None,
             dataHandles: list | None = None,
             legendsLabels: list | None = None) -> str | tuple:
    if "rendering_engine" not in critConf: 
        critConf["rendering_engine"]=os.getenv("PLOT_RENDERING_BACKEND", "matplotlib")
        
    if critConf["rendering_engine"]=="plotly":
        return plotData_plotly(critConf, dataList, interactive, figuresRelPath, fig, ax, dataHandles, legendsLabels)
    elif critConf["rendering_engine"]=="matplotlib":
        return plotData_matplotlib(critConf, dataList, interactive, figuresRelPath, fig, ax, dataHandles, legendsLabels)
    else:
        engine=critConf["rendering_engine"]
        raise Exception(f"unknown rendering engine: '{engine}' (plotly|matplotlib)")

def plotData3D_matplotlib(critConf: dict,
                          dataList: list,
                          interactive: bool = True,
                          figuresRelPath: str = "figures",
                          fig: plt.Figure | None = None,
                          ax: plt.Axes | None = None,
                          dataHandles: list | None = None,
                          legendsLabels: list | None = None) -> str | tuple:
    """Plot 3D data in a single figure using matplotlib

    :param critConf (dict): Dictionary with figure configuration params (title, description, xLabel, yLabel, zLabel, figSize, camera_eye, camera_up, camera_center)
    :param dataList ([dict]): list of params to be plot. Each one shall have plot info linestyle, marker, drawstyle and color
    :param interactive (bool): is interactive or just offline file to generate
    
    :return: path of generated file (non-interactive) or (fig, ax, dataHandles, legendsLabels) (interactive)
    """
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D
    
    if not dataHandles:
        dataHandles = []
    if not legendsLabels:
        legendsLabels = []
        
    # avoid plots to pop-up in Jupyter GUI when plotting via mexploit       
    if not interactive:        
        plt.ioff()

    
    mainTitle = critConf["title"] if "title" in critConf else critConf["name"]
    description = critConf["description"] if "description" in critConf else ""
    xLabel = critConf["xLabel"] if "xLabel" in critConf else "X"
    yLabel = critConf["yLabel"] if "yLabel" in critConf else "Y"
    zLabel = critConf.get("zLabel", "Z")
    figSize = critConf["figSize"] if "figSize" in critConf else (12, 7)
    
    # Camera position parameters
    camera_eye = critConf.get("camera_eye", (1.5, 1.5, 1.5))  # (x, y, z)
    camera_up = critConf.get("camera_up", (0, 0, 1))  # (x, y, z)
    camera_center = critConf.get("camera_center", None)  # (x, y, z) or None for auto

    if not fig:
        fig = plt.figure(critConf["name"], layout="constrained", figsize=figSize)
        ax = fig.add_subplot(1, 1, 1, projection="3d")
    
    fig.suptitle(mainTitle, fontsize=14)
    ax.set_title(description)
    ax.set_xlabel(xLabel)
    ax.set_ylabel(yLabel)
    ax.set_zlabel(zLabel)
    
    plt.grid(True)

    pIdx = 0
    for paramInfo in dataList:
        paramData = paramInfo["data"]
        
        if not isinstance(paramData, pd.DataFrame):
            raise ValueError(f"3D plot requires DataFrame with 3 columns, got {type(paramData)}")
        
        if paramData.shape[1] != 3:
            raise ValueError(f"3D plot requires exactly 3 columns, got {paramData.shape[1]}")
        
        xData = paramData.iloc[:, 0]
        yData = paramData.iloc[:, 1]
        zData = paramData.iloc[:, 2]
        
        linestyle = paramInfo.get("linestyle", "solid")
        marker = paramInfo.get("marker", ",")
        drawstyle = paramInfo.get("drawstyle", "default")
        color = paramInfo.get("color", None)
        
        # Map matplotlib drawstyle to matplotlib parameter
        drawstyle_map = {
            "default": "default",
            "steps-pre": "steps-pre",
            "steps-mid": "steps-mid",
            "steps-post": "steps-post"
        }
        mpl_drawstyle = drawstyle_map.get(drawstyle, "default")
        
        dataHandle = ax.plot(
            xData, yData, zData,
            linestyle=linestyle,
            marker=marker,
            drawstyle=mpl_drawstyle,
            color=color
        )[0]
        
        dataHandles.append(dataHandle)
        
        if "name" in paramInfo:
            legendsLabels.append(cleanFigureLegendLabel(paramInfo["name"]))
        elif hasattr(paramData, "name"):
            legendsLabels.append(cleanFigureLegendLabel(paramData.name))
        else:
            legendsLabels.append(cleanFigureLegendLabel(f"p{pIdx}"))
        
        pIdx += 1

    # Apply camera position
    # Calculate elev and azim from eye position
    x, y, z = camera_eye
    azim = np.degrees(np.arctan2(y, x))
    elev = np.degrees(np.arcsin(z / np.sqrt(x**2 + y**2 + z**2))) if np.sqrt(x**2 + y**2 + z**2) > 0 else 0
    ax.view_init(elev=elev, azim=azim)

    # Show legend
    ax.legend(legendsLabels)
    
    if interactive:
        return fig, ax, dataHandles, legendsLabels
    else:
        figPath = critConf["test_run_config"]["results"] + os.sep + figuresRelPath
        Path(figPath).mkdir(parents=True, exist_ok=True)
        filePath = figPath + os.sep + files.normalizeFileName(critConf["name"]) + ".svg"
        fig.savefig(filePath)
        get_logger().info("created 3D plot figure '" + filePath + "'")
        plt.clf()
        return filePath    

def plotData3D(critConf: dict,
               dataList: list,
               interactive: bool = True,
               figuresRelPath: str = "figures",
               fig: plt.Figure | None = None,
               ax: plt.Axes | None = None,
               dataHandles: list | None = None,
               legendsLabels: list | None = None) -> str | tuple:
    """Dispatcher for 3D plotting based on rendering engine"""
    if "rendering_engine" not in critConf:
        critConf["rendering_engine"] = os.getenv("PLOT_RENDERING_BACKEND", "matplotlib")
    
    if critConf["rendering_engine"] == "matplotlib":
        return plotData3D_matplotlib(critConf, dataList, interactive, figuresRelPath, fig, ax, dataHandles, legendsLabels)
    elif critConf["rendering_engine"] == "plotly":
        raise Exception("3D plotting with plotly is not supported yet")
    else:
        engine = critConf["rendering_engine"]
        raise Exception(f"unknown rendering engine: '{engine}' (plotly|matplotlib)")



def plotData_plotly(critConf: dict,
                    dataList: list,
                    interactive: bool = True,
                    figuresRelPath: str = "figures",
                    fig: object | None = None,
                    ax: object | None = None,
                    dataHandles: list | None = None,
                    legendsLabels: list | None = None) -> str | tuple:
    """Plot given params in a single figure using Plotly

    :param critConf (dict): Dictionary with figure configuration params (title, description, xLabel, yLabel, figSize)
    :param dataList ([dict]): list of params to be plot. Each one shall have plot info linestyle, marker, drawstyle and color
    :param interactive (bool): is interactive or just offline file to generate
    
    :return: path of generated file (non-interactive) or (fig, ax, dataHandles, legendsLabels) (interactive)
    """
    
    import plotly.graph_objects as go
    from plotly.offline import plot as plotly_plot

    if not dataHandles:
        dataHandles = []
    if not legendsLabels:
        legendsLabels = []
    
    mainTitle = critConf["title"] if "title" in critConf else critConf["name"]
    description = critConf["description"] if "description" in critConf else ""
    xLabel = critConf["xLabel"] if "xLabel" in critConf else "Time (s)"
    yLabel = critConf["yLabel"] if "yLabel" in critConf else "Values"
    figSize = critConf["figSize"] if "figSize" in critConf else (12, 7)
    xTimestamp = critConf["xTimestamp"] if "xTimestamp" in critConf else True
    projection = critConf["projection"] if "projection" in critConf else None

    if len(dataList) == 1:
        if "xLabel" not in critConf:
            if hasattr(dataList[0]["data"], "index"):
                xLabel = dataList[0]["data"].index.name

        if "name" not in dataList[0]:
            if hasattr(dataList[0]["data"], "name"):
                dataList[0]["name"] = getattr(dataList[0]["data"], "name")
            else:
                dataList[0]["name"] = critConf["name"]
        if m := re.match(r"(.*)::(.*)", dataList[0]["name"]):
            filePath = m.group(1)
            paramPath = m.group(2)
            if "description" not in critConf:
                description = "from " + filePath
            if "yLabel" not in critConf:
                yLabel = paramPath

    # Create figure
    if fig is None:
        fig = go.Figure()
        fig.update_layout(
            title={
                'text': mainTitle,
                'y': 0.95,
                'x': 0.5,
                'xanchor': 'center',
                'yanchor': 'top',
                'font': {'size': 14}
            },
            width=figSize[0] * 100,
            height=figSize[1] * 100,
            xaxis={'title': xLabel},
            yaxis={'title': yLabel},
            showlegend=True,
            legend={'x': 1, 'xanchor': 'right', 'y': 1}
        )
    
    # Add subtitle as annotation
    if description:
        fig.add_annotation(
            text=description,
            xref="paper", yref="paper",
            x=0.5, y=0.92,
            showarrow=False,
            font=dict(size=12)
        )

    # Add grid
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='LightGray')
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='LightGray')

    # Update min/max dates
    minDate = None
    maxDate = None

    for paramInfo in dataList:
        if isinstance(paramInfo["data"], (pd.DataFrame, pd.Series)):
            if minDate is None or paramInfo["data"].index[0] < minDate:
                minDate = paramInfo["data"].index[0]
            if maxDate is None or paramInfo["data"].index[-1] > maxDate:
                maxDate = paramInfo["data"].index[-1]

    assert minDate is not None, (
        "CRIT_CHECK\n[" + critConf["type"] + "::" + critConf["name"] + "] no min/max date found. "
        + "Don't know how to plot scalar value on its own, please plot it together with a timeserie."
    )

    pIdx = 0
    for paramInfo in dataList:
        paramData = paramInfo["data"]

        if isinstance(paramData, (int, float, bool)):
            paramData = pd.Series([paramData, paramData], index=[minDate, maxDate])

        # Map matplotlib linestyle to plotly line.dash
        linestyle_map = {
            "solid": "solid",
            "dotted": "dot",
            "dashed": "dash",
            "dashdot": "dashdot"
        }
        linestyle = paramInfo.get("linestyle", "solid")
        plotly_dash = linestyle_map.get(linestyle, "solid")

        # Map matplotlib marker to plotly marker.symbol (using valid Plotly symbols)
        marker_map = {
            ",": "circle",
            ".": "circle",
            "o": "circle",
            "2": "triangle-up",
            "p": "pentagon",
            "x": "x",
            "*": "star",
            "d": "diamond",
            "_": "line-ns",
            "|": "line-ew"
        }
        marker = paramInfo.get("marker", ",")
        plotly_marker_symbol = marker_map.get(marker, "circle")

        # Map matplotlib drawstyle to plotly line.shape
        drawstyle_map = {
            "default": "linear",
            "steps-pre": "hv",
            "steps-mid": "vhv",
            "steps-post": "vh"
        }
        drawstyle = paramInfo.get("drawstyle", "default")
        plotly_shape = drawstyle_map.get(drawstyle, "linear")

        color = paramInfo.get("color", None)

        if isinstance(paramData, pd.DataFrame):
            for col in paramData.columns:
                trace = go.Scatter(
                    x=paramData.index,
                    y=paramData[col],
                    mode='lines+markers',
                    line={'dash': plotly_dash, 'shape': plotly_shape, 'color': color},
                    marker={'symbol': plotly_marker_symbol},
                    name=cleanFigureLegendLabel(col)
                )
                fig.add_trace(trace)
                dataHandles.append(trace)
                legendsLabels.append(cleanFigureLegendLabel(col))
        else:
            trace = go.Scatter(
                x=paramData.index,
                y=paramData,
                mode='lines+markers',
                line={'dash': plotly_dash, 'shape': plotly_shape, 'color': color},
                marker={'symbol': plotly_marker_symbol},
                name=cleanFigureLegendLabel(paramInfo.get("name", paramData.name if hasattr(paramData, "name") else f"p{pIdx}"))
            )
            fig.add_trace(trace)
            dataHandles.append(trace)
            if "name" in paramInfo:
                legendsLabels.append(cleanFigureLegendLabel(paramInfo["name"]))
            elif hasattr(paramData, "name"):
                legendsLabels.append(cleanFigureLegendLabel(paramData.name))
            else:
                legendsLabels.append(cleanFigureLegendLabel(f"p{pIdx}"))

        pIdx += 1

    # Handle x-axis timestamp formatting
    if xTimestamp:
        dT = maxDate - minDate
        if hasattr(dT, 'total_seconds'):
            dT_seconds = dT.total_seconds()
        else:
            dT_seconds = float(dT)

        if dT_seconds > 30:
            fig.update_xaxes(
                tickformat='HH:MM:SS',
                tickangle=70
            )
            startDateStr = datetime.fromtimestamp(minDate, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            endDateStr = datetime.fromtimestamp(maxDate, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            fig.update_xaxes(title_text=f'Whole Data from {startDateStr} to {endDateStr}')
        else:
            fig.update_xaxes(
                tickformat='MM:SS.ffffff',
                tickangle=0
            )
            dateStr = datetime.fromtimestamp(minDate, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            fig.update_xaxes(title_text=f'Whole Data from {dateStr}')

    if interactive:
        return fig, ax, dataHandles, legendsLabels
    else:
        figPath = critConf["test_run_config"]["results"] + os.sep + figuresRelPath
        Path(figPath).mkdir(parents=True, exist_ok=True)
        filePath = figPath + os.sep + files.normalizeFileName(critConf["name"]) + ".svg"
        
        # Save figure (requires kaleido)
        fig.write_image(filePath)
        
        # Save figure using orca (requires plotly-orca package and Graphviz)
        #import plotly_orca
        #plotly_orca.save(fig, filePath)
        
        get_logger().info("created plot figure '" + filePath + "'")
        
        return filePath
    

def plotTimeline(critConf: dict,
                 eventsData: dict,
                 figuresRelPath: str = "figures") -> str:
    if "rendering_engine" not in critConf: 
        critConf["rendering_engine"]=os.getenv("PLOT_RENDERING_BACKEND", "matplotlib")
        
    if critConf["rendering_engine"]=="plotly":
        return plotTimeline_plotly(critConf, eventsData, figuresRelPath)
    elif critConf["rendering_engine"]=="matplotlib":
        return plotTimeline_matplotlib(critConf, eventsData, figuresRelPath)
    else:
        engine=critConf["rendering_engine"]
        raise Exception(f"unknown rendering engine: '{engine}' (plotly|matplotlib)")


def _prepare_events_dataframe(eventsData: dict) -> pd.DataFrame:
    """Prepare events data into a sorted DataFrame.
    
    :param eventsData (dict): data of each event, by name : date, color, level, linestyle
    :return: DataFrame with columns Date, Event, Color, Level, Title, Linestyle
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


def _allocate_y_position(idx: int) -> tuple[float, bool]:
    """Allocate Y position for an event label, alternating above/below timeline.
    
    :param idx: chronological index of the event
    :param latest_y_pos_idx: set of already used Y positions
    :return: tuple (allocated Y position, side_above boolean)
    """
    POSITIONS_LIST = [2,4,6,8]
    LABEL_MARGIN = 0.6
    
    side_above = (idx % 2 == 0)
    figLevel =  (1 if side_above else -1) * POSITIONS_LIST[idx % len(POSITIONS_LIST)] * LABEL_MARGIN

    return figLevel, side_above


def _get_title_and_description(critConf: dict) -> tuple[str, str]:
    """Extract title and description from critConf.
    
    :param critConf: configuration dictionary
    :return: tuple (mainTitle, description)
    """
    mainTitle = critConf["title"] if "title" in critConf else critConf["name"]
    description = critConf["description"] if "description" in critConf else ""
    return mainTitle, description


def _get_figure_path(critConf: dict, figuresRelPath: str) -> str:
    """Construct the figure file path.
    
    :param critConf: configuration dictionary
    :param figuresRelPath: relative path to figures directory
    :return: full file path for the figure
    """
    figPath = critConf["test_run_config"]["results"] + os.sep + figuresRelPath
    Path(figPath).mkdir(parents=True, exist_ok=True)
    filePath = figPath + os.sep + files.normalizeFileName(critConf["name"]) + ".svg"
    return filePath


def plotTimeline_plotly(critConf: dict,
                        eventsData: dict,
                        figuresRelPath: str = "figures") -> str:
    """Create a timeline plot of provided events list using plotly

    :param eventsData (dict): data of each event, by name : date, color, level, linestyle
    """
    import plotly.graph_objects as go
    
    def matplotlib_linestyle_to_plotly(linestyle: str | tuple) -> str:
        """Convert matplotlib linestyle tuple to plotly dash string."""
        if isinstance(linestyle, str):
            valid_dash_styles = ['solid', 'dot', 'dash', 'longdash', 'dashdot', 'longdashdot']
            if linestyle in valid_dash_styles:
                return linestyle
            matplotlib_map = {
                '-': 'solid',
                '--': 'dash',
                '-.': 'dashdot',
                ':': 'dot'
            }
            return matplotlib_map.get(linestyle, 'solid')
        
        if isinstance(linestyle, tuple) and len(linestyle) == 2:
            offset, dash_seq = linestyle
            if not dash_seq or len(dash_seq) == 0:
                return 'solid'
            dash_str = ' '.join(str(int(d)) for d in dash_seq)
            return dash_str
        
        return 'solid'

    timeline_df = _prepare_events_dataframe(eventsData)
    
    timeline_df["DateStr"] = timeline_df["Date"].dt.strftime('%Y-%m-%dT%H:%M:%S.%f')
    
    latest_y_pos_idx = set()
    
    figSize = critConf["figSize"] if "figSize" in critConf else (12, 7)
    
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=timeline_df["DateStr"],
        y=[0] * len(timeline_df),
        mode="lines+markers",
        line=dict(color="black", width=1),
        marker=dict(color="white", size=8, line=dict(color="black", width=1)),
        showlegend=False,
        hoverinfo="skip"
    ))

    for idx in range(len(timeline_df)):
        dt_str = timeline_df["DateStr"][idx]
        event = timeline_df["Event"][idx]
        color = timeline_df["Color"][idx]
        linestyle = matplotlib_linestyle_to_plotly(timeline_df["Linestyle"][idx])
        title = timeline_df["Title"][idx]
        fontstyle = timeline_df["Fontstyle"][idx]

        figLevel, side_above = _allocate_y_position(idx)

        fig.add_trace(go.Scatter(
            x=[dt_str, dt_str],
            y=[0.1 if side_above else -0.1, figLevel - 0.3 if side_above else figLevel + 0.3],
            mode="lines",
            line=dict(color=color, width=0.8, dash=linestyle),
            showlegend=False,
            hoverinfo="skip"
        ))

        weight="normal"
        style="normal"
        if fontstyle=="bold": weight=fontstyle
        elif fontstyle=="italic": style=fontstyle
        fig.add_trace(go.Scatter(
            x=[dt_str],
            y=[figLevel],
            mode="text",
            text=[title.replace("\n", "<br>")],
            textfont=dict(color=color, size=10, weight=weight,style=style),
            textposition="middle center",
            showlegend=False,
            hoverinfo="skip"
        ))

    mainTitle, description = _get_title_and_description(critConf)
    
    date_min = timeline_df["DateStr"].min()
    date_max = timeline_df["DateStr"].max()
    
    date_min_dt = pd.to_datetime(date_min)
    date_max_dt = pd.to_datetime(date_max)
    total_range = (date_max_dt - date_min_dt).total_seconds()
    padding_seconds = total_range * 0.05
    padding_td = pd.Timedelta(seconds=padding_seconds)
    
    padded_date_min = (date_min_dt - padding_td).isoformat()
    padded_date_max = (date_max_dt + padding_td).isoformat()
    
    fig.update_layout(
        title=dict(
            text=mainTitle,
            font=dict(size=14),
            x=0.5,
            y=0.98
        ),
        width=figSize[0] * 100,
        height=figSize[1] * 100,
        plot_bgcolor="white",
        showlegend=False,
        xaxis=dict(
            showgrid=False,
            zeroline=False,
            showticklabels=False,
            range=[padded_date_min, padded_date_max]
        ),
        yaxis=dict(
            showgrid=False,
            zeroline=False,
            showticklabels=False,
            range=[-9, 9]
        ),
        margin=dict(l=20, r=20, t=60, b=20)
    )

    if description:
        fig.add_annotation(
            x=0,
            y=1,
            xref="paper",
            yref="paper",
            text=description,
            showarrow=False,
            font=dict(size=16, weight="bold"),
            xanchor="left",
            yanchor="top",
            yshift=-40
        )

    filePath = _get_figure_path(critConf, figuresRelPath)
    fig.write_image(filePath)

    return filePath


def plotTimeline_matplotlib(critConf: dict,
                            eventsData: dict,
                            figuresRelPath: str = "figures") -> str:
    """Create a timeline plot of provided events list

    :param eventsData (dict): data of each event, by name : date, color, level, linestyle
    """
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker

    plt.ioff()

    timeline_df = _prepare_events_dataframe(eventsData)
    
    latest_y_pos_idx = 0

    with plt.style.context("fivethirtyeight"):
        figSize = critConf["figSize"] if "figSize" in critConf else (12, 7)
        fig = plt.figure(critConf["name"], layout="constrained", figsize=figSize)
        ax = fig.add_subplot(1, 1, 1, projection=None)
        ax.set_facecolor('white')        
        ax.plot(timeline_df.Date, [0,] * len(timeline_df), "-o", color="black", markerfacecolor="white",linewidth=1)
        ax.set_ylim(-9, 9)

        for idx in range(len(timeline_df)):
            dt = timeline_df["Date"][idx]
            event = timeline_df["Event"][idx]
            originalLevel = timeline_df["Level"][idx]
            color = timeline_df["Color"][idx]
            linestyle = timeline_df["Linestyle"][idx]
            title = timeline_df["Title"][idx]
            fontstyle = timeline_df["Fontstyle"][idx]

            figLevel, side_above = _allocate_y_position(idx)

            weight="normal"
            style="normal"
            if fontstyle=="bold": weight=fontstyle
            elif fontstyle=="italic": style=fontstyle
            ax.annotate(
                title,
                xy=(dt, 0.1 if side_above else -0.1),
                xytext=(dt, figLevel),
                arrowprops=dict(arrowstyle="-", color=color, linewidth=0.8, linestyle=linestyle),
                ha="center",
                color=color,
                fontsize="10",
                fontweight=weight,
                fontstyle=style
            )

        ax.spines[["left", "top", "right", "bottom"]].set_visible(False)
        ax.spines[["bottom"]].set_position(("axes", 0.5))
        ax.yaxis.set_visible(False)
        ax.xaxis.set_major_locator(ticker.NullLocator())
        
        mainTitle, description = _get_title_and_description(critConf)
        fig.suptitle(mainTitle, fontsize=14)
        ax.set_title(description, pad=10, loc="left", fontsize=25, fontweight="bold")
    
        ax.grid(False)

        filePath = _get_figure_path(critConf, figuresRelPath)
        fig.savefig(filePath)

        plt.clf()

        return filePath