"""plotly plot backend (pip install testsuit[plotly]).

- interactive (Jupyter): figures are plotly FigureWidgets (with anywidget), updated in place
- static (reports): by default the figure is saved as plotly JSON (<name>.plotly.json), drawn by plotly.js
  in the exploit_runner HTML report, where it can be zoomed, hovered and rotated (3D). The report then embeds
  plotly.js (~2 MB gzipped and base64 encoded) to stay a single file working offline. figure_format svg
  or png save images instead, as the matplotlib backend does (requires kaleido and a Chrome/Chromium browser).
"""
from __future__ import annotations

import html
import os
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from testsuit.datatools.plotBackends import APlotBackend
from testsuit.datatools.plotBackends.common import FigureSpec,timeline_label_position,timeline_font

PLOTLY_JSON_SUFFIX = ".plotly.json"

# matplotlib styles (see common.Trace) -> plotly
DASHES = {"solid": "solid", "-": "solid", "dotted": "dot", ":": "dot", "dashed": "dash", "--": "dash", "dashdot": "dashdot", "-.": "dashdot"}
MARKERS = {".": "circle", "o": "circle", "2": "triangle-up", "^": "triangle-up", "v": "triangle-down", "s": "square",
           "p": "pentagon", "x": "x", "+": "cross", "*": "star", "d": "diamond", "D": "diamond", "_": "line-ew", "|": "line-ns"}
# steps-pre: the step happens before the point (vertical then horizontal), steps-post after it
SHAPES = {"default": "linear", "steps-pre": "vh", "steps-mid": "hvh", "steps-post": "hv", "steps": "vh"}
NO_LINE = ("None", "none", "", " ")


def _mode_and_style(trace) -> tuple[str, dict, dict]:
    """(mode, line, marker) of a scatter trace"""
    hasLine = trace.linestyle not in NO_LINE
    hasMarkers = trace.marker not in (",", None) and trace.marker not in NO_LINE
    mode = "+".join((["lines"] if hasLine else []) + (["markers"] if hasMarkers else [])) or "lines"
    line = {"dash": DASHES.get(trace.linestyle, "solid"), "color": trace.color}
    marker = {"symbol": MARKERS.get(trace.marker, "circle"), "color": trace.color}
    return mode, line, marker


def _dates_axis(minDate: float, maxDate: float) -> dict:
    """x axis of dates given as seconds, shown as by the matplotlib backend"""
    if maxDate - minDate > 30:
        return {"type": "date", "tickformat": "%H:%M:%S", "hoverformat": "%Y-%m-%d %H:%M:%S.%6f"}
    return {"type": "date", "tickformat": "%M:%S.%6fs", "hoverformat": "%Y-%m-%d %H:%M:%S.%6f"}


def _seconds(values) -> np.ndarray:
    """dates of a plotly date axis (ms since epoch, date strings or datetime64) -> seconds since epoch"""
    values = np.asarray(values)
    if values.dtype.kind in "fiu":
        return values / 1e3
    return pd.to_datetime(values).values.astype("datetime64[ns]").astype("int64") / 1e9


class PlotlyBackend(APlotBackend):
    name = "plotly"
    figure_formats = {"interactive": PLOTLY_JSON_SUFFIX, "svg": ".svg", "png": ".png"}

    def static_figures_are_interactive(self, figure_format):
        return figure_format == "interactive"

    def _figure(self, spec: FigureSpec, interactive: bool):
        fig = None
        if interactive:
            try:
                fig = go.FigureWidget()  # updated in place in Jupyter, requires anywidget
            except ImportError:
                pass
        if fig is None:
            fig = go.Figure()
        fig.update_layout(width=None if interactive else spec.figSize[0] * 100,
                          height=spec.figSize[1] * 100,
                          template="plotly_white",
                          legend={"x": 1, "xanchor": "right", "y": 1, "bgcolor": "rgba(255,255,255,0.6)"},
                          margin={"t": 90})
        return fig

    def lines(self, spec, fig=None, ax=None, handles=None, labels=None, interactive=True):
        handles = handles if handles else []
        labels = labels if labels else []
        if fig is None:
            fig = self._figure(spec, interactive)
        self.set_titles(fig, ax, spec.title, spec.description, spec.xLabel, spec.yLabel)
        fig.update_xaxes(showgrid=True)
        fig.update_yaxes(showgrid=True)
        if spec.xTimestamp:
            fig.update_xaxes(**_dates_axis(*spec.timeRange))

        newTraces = []
        for trace in spec.traces:
            mode, line, marker = _mode_and_style(trace)
            line["shape"] = SHAPES.get(trace.drawstyle, "linear")
            x = np.asarray(trace.x, dtype=float) * 1e3 if spec.xTimestamp else trace.x
            newTraces.append(go.Scatter(x=x, y=trace.y, mode=mode, line=line, marker=marker, name=trace.label))
            labels.append(trace.label)
        fig.add_traces(newTraces)
        handles += list(fig.data[-len(newTraces):]) if newTraces else []
        return fig, ax, handles, labels

    def lines3d(self, spec, fig=None, ax=None, handles=None, labels=None, interactive=True):
        handles = handles if handles else []
        labels = labels if labels else []
        if fig is None:
            fig = self._figure(spec, interactive)
        self.set_titles(fig, ax, spec.title, spec.description, spec.xLabel, spec.yLabel)
        camera = {"eye": dict(zip("xyz", spec.cameraEye)), "up": dict(zip("xyz", spec.cameraUp))}
        if spec.cameraCenter is not None:
            camera["center"] = dict(zip("xyz", spec.cameraCenter))
        fig.update_scenes(zaxis_title_text=spec.zLabel, camera=camera)

        newTraces = []
        for trace in spec.traces:
            mode, line, marker = _mode_and_style(trace)
            marker["size"] = 3
            newTraces.append(go.Scatter3d(x=trace.x, y=trace.y, z=trace.z, mode=mode, line=line, marker=marker, name=trace.label))
            labels.append(trace.label)
        fig.add_traces(newTraces)
        handles += list(fig.data[-len(newTraces):]) if newTraces else []
        return fig, ax, handles, labels

    def histogram(self, spec):
        fig = self._figure(spec, interactive=False)
        trace = spec.traces[0]
        # same bins as matplotlib (plotly's nbinsx is a maximum, rounded to "nice" bins)
        edges = np.histogram_bin_edges(trace.y, bins=spec.nbBins)
        fig.add_trace(go.Histogram(x=trace.y, name=trace.label, opacity=0.8, marker={"color": trace.color},
                                   xbins={"start": edges[0], "end": edges[-1], "size": edges[1] - edges[0]}))
        fig.update_layout(bargap=0.05, showlegend=False)
        self.set_titles(fig, None, spec.title, spec.description, spec.xLabel, spec.yLabel)
        return fig

    def timeline(self, spec, timeline_df):
        fig = self._figure(spec, interactive=False)
        dates = timeline_df["Date"]

        fig.add_trace(go.Scatter(x=dates, y=[0] * len(timeline_df), mode="lines+markers",
                                 line={"color": "black", "width": 1},
                                 marker={"color": "white", "size": 8, "line": {"color": "black", "width": 1}},
                                 showlegend=False, hoverinfo="skip"))

        for idx in range(len(timeline_df)):
            dt = dates[idx]
            color = timeline_df["Color"][idx]
            figLevel, side_above = timeline_label_position(idx)
            weight, style = timeline_font(timeline_df["Fontstyle"][idx])
            fig.add_trace(go.Scatter(x=[dt, dt], y=[0.1 if side_above else -0.1, figLevel - 0.3 if side_above else figLevel + 0.3],
                                     mode="lines", line={"color": color, "width": 0.8, "dash": self._timeline_dash(timeline_df["Linestyle"][idx])},
                                     showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=[dt], y=[figLevel], mode="text",
                                     text=[timeline_df["Title"][idx].replace("\n", "<br>")],
                                     textfont={"color": color, "size": 10, "weight": weight, "style": style},
                                     showlegend=False, hovertext=[f"{timeline_df['Event'][idx]}: {dt}"], hoverinfo="text"))

        padding = (dates.max() - dates.min()) * 0.05
        fig.update_layout(title={"text": spec.title, "subtitle": {"text": spec.description}, "x": 0.5},
                          plot_bgcolor="white", showlegend=False,
                          xaxis={"showgrid": False, "zeroline": False, "showticklabels": False,
                                 "range": [dates.min() - padding, dates.max() + padding]},
                          yaxis={"showgrid": False, "zeroline": False, "showticklabels": False, "range": [-9, 9]})
        return fig

    @staticmethod
    def _timeline_dash(linestyle) -> str:
        """matplotlib linestyle name or (offset, (on, off, ...)) tuple -> plotly dash"""
        if isinstance(linestyle, tuple) and len(linestyle) == 2:
            return " ".join(f"{int(d)}px" for d in linestyle[1]) if linestyle[1] else "solid"
        if linestyle in ("solid", "dot", "dash", "longdash", "dashdot", "longdashdot"):
            return linestyle
        return DASHES.get(linestyle, "solid")

    def save(self, fig, filePath, figure_format):
        if figure_format == "interactive":
            with open(filePath, "w", encoding="utf-8") as f:
                f.write(fig.to_json())
        else:
            try:
                fig.write_image(filePath)
            except (ImportError, ValueError, RuntimeError) as e:
                raise RuntimeError(f"plotly {figure_format} figures require kaleido and Chrome/Chromium "
                                   f"(pip install kaleido; plotly_get_chrome), or figure_format: interactive: {e}") from e
        return filePath

    ######## interactive figures (Jupyter) ########
    def display(self, fig):
        return fig

    @staticmethod
    def _is3d(fig) -> bool:
        return any(t.type == "scatter3d" for t in fig.data)

    def set_titles(self, fig, ax, title, description, xLabel, yLabel):
        fig.update_layout(title={"text": title, "subtitle": {"text": description}, "x": 0.5, "xanchor": "center"})
        if self._is3d(fig):
            fig.update_scenes(xaxis_title_text=xLabel or "X", yaxis_title_text=yLabel)
            return
        if xLabel:
            fig.update_xaxes(title_text=xLabel)
        fig.update_yaxes(title_text=yLabel)

    def set_legend(self, fig, ax, handles, labels, visible):
        with fig.batch_update() if isinstance(fig, go.FigureWidget) else nullcontext():
            fig.update_layout(showlegend=visible)
            for trace, label in zip(fig.data, labels):
                trace.name = label

    def clear(self, fig, ax):
        fig.data = ()

    def _date_axis(self, fig) -> bool:
        return fig.layout.xaxis.type == "date"

    def lines_data(self, fig, ax):
        data = []
        for trace in fig.data:
            if trace.type != "scatter":
                continue
            x = _seconds(trace.x) if self._date_axis(fig) else np.asarray(trace.x, dtype=float)
            data.append((x, np.asarray(trace.y, dtype=float)))
        return data

    def view_limits(self, fig, ax):
        data = self.lines_data(fig, ax)
        allX = np.concatenate([x for x, y in data]) if data else np.array([0.0, 1.0])
        allY = np.concatenate([y for x, y in data]) if data else np.array([0.0, 1.0])
        xRange, yRange = fig.layout.xaxis.range, fig.layout.yaxis.range
        if xRange is None or fig.layout.xaxis.autorange:
            xlim = (np.nanmin(allX), np.nanmax(allX))
        else:
            xlim = tuple(_seconds(list(xRange))) if self._date_axis(fig) else tuple(map(float, xRange))
        if yRange is None or fig.layout.yaxis.autorange:
            ylim = (np.nanmin(allY), np.nanmax(allY))
        else:
            ylim = tuple(map(float, yRange))
        return xlim, ylim

    def export_image(self, fig, filePath):
        if filePath.lower().endswith((".html", ".htm")):
            go.Figure(fig).write_html(filePath, include_plotlyjs=True)
        else:
            go.Figure(fig).write_image(filePath)



def write_figure_page(jsonFile: str | os.PathLike, htmlFile: str | os.PathLike, plotlyJsFile: str | os.PathLike) -> None:
    """HTML page drawing a plotly JSON figure (as saved by PlotlyBackend.save()) with plotly.js loaded from
    plotlyJsFile, written if missing and shared by the pages: used by mexploit's pytest-html report, which
    can't run scripts of its own cells but shows these pages in iframes."""
    from plotly.offline import get_plotlyjs

    htmlFile, plotlyJsFile = Path(htmlFile), Path(plotlyJsFile)
    if not plotlyJsFile.exists():
        plotlyJsFile.parent.mkdir(parents=True, exist_ok=True)
        plotlyJsFile.write_text(get_plotlyjs(), encoding="utf-8")
    src = Path(os.path.relpath(plotlyJsFile, htmlFile.parent)).as_posix()
    # the figure is a JS literal inside <script>: "</" would end it
    figure = Path(jsonFile).read_text(encoding="utf-8").replace("</", "<\\/")
    htmlFile.parent.mkdir(parents=True, exist_ok=True)
    htmlFile.write_text(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html.escape(htmlFile.stem)}</title>
<script src="{html.escape(src)}"></script>
<style>html, body {{ margin: 0; height: 100%; }} #figure {{ width: 100%; height: 100%; }}</style>
</head><body><div id="figure"></div><script>
const fig = {figure};
const layout = Object.assign(fig.layout || {{}}, {{ autosize: true }});
delete layout.width; delete layout.height;
Plotly.newPlot('figure', fig.data, layout, {{ responsive: true, displaylogo: false }});
</script></body></html>
""", encoding="utf-8")
