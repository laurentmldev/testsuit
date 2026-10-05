"""Plots of tests data, for Jupyter notebooks (interactive=True: the figure is returned) and mexploit
reports (interactive=False: the figure is saved under <results>/<figuresRelPath>).

Drawn by a plot backend, matplotlib (default) or plotly, selected per plot by critConf["rendering_engine"],
else by the PLOT_RENDERING_BACKEND environment variable: see testsuit.datatools.plotBackends.
"""
from __future__ import annotations

from testsuit.datatools.plotBackends import (APlotBackend, get_plot_backend, figure_format, plot_backend_name,
                                             plot_backend_names, register_plot_backend, set_default_plot_backend)
from testsuit.datatools.plotBackends import common
from testsuit.datatools.plotBackends.common import cleanFigureLegendLabel, mergeXYZParams as _mergeXYZParams
from testsuit.datatools.plotBackends.matplotlibBackend import save_svg_figure
from testsuit.misc.logger import get_logger


def _save(backend: APlotBackend, fig, critConf: dict, figuresRelPath: str) -> str:
    fmt = figure_format(backend, critConf)
    filePath = common.figure_file_path(critConf, figuresRelPath, backend.figure_formats[fmt])
    return backend.save(fig, filePath, fmt)


def static_figures_are_interactive(critConf: dict) -> bool:
    """True when the report figures of this critConf can be zoomed/rotated (plotly): one 3D view is enough"""
    backend = get_plot_backend(critConf)
    return backend.static_figures_are_interactive(figure_format(backend, critConf))


def plotData(critConf: dict,
             dataList: list,
             interactive: bool = True,
             figuresRelPath: str = "figures",
             fig: object | None = None,
             ax: object | None = None,
             dataHandles: list | None = None,
             legendsLabels: list | None = None) -> str | tuple:
    """Plot given params in a single figure

    :param critConf (dict): figure configuration (name, title, description, xLabel, yLabel, figSize, xTimestamp, rendering_engine, figure_format)
    :param dataList ([dict]): params to be plot: data (pandas Series/DataFrame indexed by time in seconds, or scalar)
        and plot info name, linestyle, marker, drawstyle, color (matplotlib names)
    :param interactive (bool): return the figure (to add traces to it with fig, ax, dataHandles, legendsLabels) or save it
    :return: path of generated file, or (fig, ax, dataHandles, legendsLabels) when interactive
    """
    backend = get_plot_backend(critConf)
    spec = common.lines_spec(critConf, dataList)
    fig, ax, dataHandles, legendsLabels = backend.lines(spec, fig, ax, dataHandles, legendsLabels, interactive=interactive)
    if interactive:
        return fig, ax, dataHandles, legendsLabels
    filePath = _save(backend, fig, critConf, figuresRelPath)
    get_logger().info("created plot figure '"+filePath+"'")
    return filePath


def plotData3D(critConf: dict,
               dataList: list,
               interactive: bool = True,
               figuresRelPath: str = "figures",
               fig: object | None = None,
               ax: object | None = None,
               dataHandles: list | None = None,
               legendsLabels: list | None = None) -> str | tuple:
    """Plot 3D data in a single figure: params with 3 columns (x,y,z), or 3 params of same length taken as X, Y and Z

    :param critConf (dict): as plotData, plus zLabel and the camera position: camera_eye, camera_up, camera_center ((x,y,z) each)
    :return: path of generated file, or (fig, ax, dataHandles, legendsLabels) when interactive
    """
    backend = get_plot_backend(critConf)
    spec = common.lines3d_spec(critConf, dataList)
    fig, ax, dataHandles, legendsLabels = backend.lines3d(spec, fig, ax, dataHandles, legendsLabels, interactive=interactive)
    if interactive:
        return fig, ax, dataHandles, legendsLabels
    filePath = _save(backend, fig, critConf, figuresRelPath)
    get_logger().info("created 3D plot figure '"+filePath+"'")
    return filePath


def plotDataHistogram(critConf: dict,
                      dataInfo: dict,
                      colIdx: int = 0,
                      nbSegments: int = 13,
                      interactive: bool = True,
                      figuresRelPath: str = "figures") -> str:
    """Plot histogram of nb occurences by segments of values

    :param critConf (dict): figure configuration (name, title, description, xLabel, yLabel, figSize, rendering_engine, figure_format)
    :param dataInfo (dict): data (pandas Series, or DataFrame of which column colIdx is used), name, color
    :param interactive (bool): unused, the figure is always saved

    :return: path of generated file
    """
    backend = get_plot_backend(critConf)
    fig = backend.histogram(common.histogram_spec(critConf, dataInfo, colIdx, nbSegments))
    filePath = _save(backend, fig, critConf, figuresRelPath)
    get_logger().info("created histogram figure '"+filePath+"'")
    return filePath


def plotTimeline(critConf: dict,
                 eventsData: dict,
                 figuresRelPath: str = "figures") -> str:
    """Create a timeline plot of provided events list

    :param eventsData (dict): data of each event, by name: date, color, level, linestyle, fontstyle, title (optional)
    :return: path of generated file
    """
    backend = get_plot_backend(critConf)
    mainTitle = critConf["title"] if "title" in critConf else critConf["name"]
    spec = common.FigureSpec(name=critConf["name"], title=mainTitle, description=critConf.get("description", ""),
                             figSize=critConf["figSize"] if "figSize" in critConf else (12, 7))
    fig = backend.timeline(spec, common.timeline_dataframe(eventsData))
    return _save(backend, fig, critConf, figuresRelPath)


######## per backend entry points, kept for compatibility ########

def _with_backend(func, backendName: str):
    def plot(critConf: dict, *args, **kwargs):
        return func({**critConf, "rendering_engine": backendName}, *args, **kwargs)
    plot.__name__ = f"{func.__name__}_{backendName}"
    plot.__doc__ = f"{func.__name__}() drawn with {backendName}"
    return plot

plotData_matplotlib = _with_backend(plotData, "matplotlib")
plotData_plotly = _with_backend(plotData, "plotly")
plotData3D_matplotlib = _with_backend(plotData3D, "matplotlib")
plotData3D_plotly = _with_backend(plotData3D, "plotly")
plotTimeline_matplotlib = _with_backend(plotTimeline, "matplotlib")
plotTimeline_plotly = _with_backend(plotTimeline, "plotly")

__all__ = ["plotData", "plotData3D", "plotDataHistogram", "plotTimeline", "static_figures_are_interactive",
           "save_svg_figure", "cleanFigureLegendLabel", "get_plot_backend", "register_plot_backend",
           "set_default_plot_backend", "plot_backend_name", "plot_backend_names", "APlotBackend"]
