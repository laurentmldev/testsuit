"""matplotlib plot backend: static figures as SVG (texts kept as text) or PNG."""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib.ticker import FuncFormatter

from testsuit.datatools.plotBackends import APlotBackend
from testsuit.datatools.plotBackends.common import FigureSpec,timeline_label_position,timeline_font


def save_svg_figure(fig, filePath: str) -> None:
    """Save a matplotlib figure as SVG with its texts as <text> elements: matplotlib otherwise draws
    every glyph as a path (about a quarter of a typical criterion figure, embedded in HTML reports)."""
    with plt.rc_context({"svg.fonttype": "none"}):
        fig.savefig(filePath, metadata={"Date": None})


def sec_to_timestamp_s(x: float, pos: float) -> str:
    """Convert seconds to HH:MM:SS format"""
    # Explicitly convert numpy.float64 to Python float
    total_seconds = float(x)
    hours = int(total_seconds // 3600) % 24
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def sec_to_timestamp_us(x: float, pos: float) -> str:
    """Convert seconds to MM:SS.uuuuuu format"""
    total_seconds = float(x)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    micros = int((total_seconds % 1) * 1000000)  # 10^6 for microseconds
    return f"{minutes:02d}:{seconds:02d}.{micros:06d}s"


class MatplotlibBackend(APlotBackend):
    name = "matplotlib"
    figure_formats = {"svg": ".svg", "png": ".png"}

    def _figure(self, spec: FigureSpec, projection: str | None):
        fig=plt.figure(spec.name,layout="constrained",figsize=spec.figSize)
        return fig,fig.add_subplot(1,1,1,projection=projection)

    def lines(self, spec, fig=None, ax=None, handles=None, labels=None, interactive=True):
        # avoid plots to pop-up in Jupyter GUI when plotting via mexploit
        if not interactive:
            plt.ioff()
        handles=handles if handles else []
        labels=labels if labels else []
        if not fig:
            fig,ax=self._figure(spec,spec.projection)
        self.set_titles(fig,ax,spec.title,spec.description,spec.xLabel,spec.yLabel)
        ax.grid(True)

        for trace in spec.traces:
            handles.append(ax.plot(trace.x,trace.y,
                                   linestyle=trace.linestyle,
                                   marker=trace.marker,
                                   drawstyle=trace.drawstyle,
                                   color=trace.color)[0])
            labels.append(trace.label)

        if spec.xTimestamp:
            minDate,maxDate=spec.timeRange
            if maxDate-minDate > 30:
                ax.xaxis.set_major_formatter(FuncFormatter(sec_to_timestamp_s))
                ax.tick_params(axis='x', rotation=70)
            else:
                ax.xaxis.set_major_formatter(FuncFormatter(sec_to_timestamp_us))

        ax.legend(handles,labels,loc="upper right")
        return fig,ax,handles,labels

    def lines3d(self, spec, fig=None, ax=None, handles=None, labels=None, interactive=True):
        if not interactive:
            plt.ioff()
        handles=handles if handles else []
        labels=labels if labels else []
        if not fig:
            fig,ax=self._figure(spec,"3d")
        self.set_titles(fig,ax,spec.title,spec.description,spec.xLabel,spec.yLabel)
        ax.set_zlabel(spec.zLabel)
        ax.grid(True)

        for trace in spec.traces:
            handles.append(ax.plot(trace.x,trace.y,trace.z,
                                   linestyle=trace.linestyle,
                                   marker=trace.marker,
                                   drawstyle=trace.drawstyle,
                                   color=trace.color)[0])
            labels.append(trace.label)

        # camera: elevation and azimuth of the eye position
        x, y, z = spec.cameraEye
        norm = np.sqrt(x**2 + y**2 + z**2)
        ax.view_init(elev=np.degrees(np.arcsin(z / norm)) if norm > 0 else 0, azim=np.degrees(np.arctan2(y, x)))

        ax.legend(labels)
        return fig,ax,handles,labels

    def histogram(self, spec):
        plt.ioff()
        fig,ax=self._figure(spec,None)
        trace=spec.traces[0]
        ax.hist(trace.y,bins=spec.nbBins,alpha=0.9,rwidth=0.95,color=trace.color)
        self.set_titles(fig,ax,spec.title,spec.description,spec.xLabel,spec.yLabel)
        ax.grid(False)
        return fig

    def timeline(self, spec, timeline_df):
        plt.ioff()
        with plt.style.context("fivethirtyeight"):
            fig,ax=self._figure(spec,None)
            ax.set_facecolor('white')
            ax.plot(timeline_df.Date, [0,] * len(timeline_df), "-o", color="black", markerfacecolor="white",linewidth=1)
            ax.set_ylim(-9, 9)

            for idx in range(len(timeline_df)):
                dt = timeline_df["Date"][idx]
                color = timeline_df["Color"][idx]
                figLevel, side_above = timeline_label_position(idx)
                weight, style = timeline_font(timeline_df["Fontstyle"][idx])
                ax.annotate(
                    timeline_df["Title"][idx],
                    xy=(dt, 0.1 if side_above else -0.1),
                    xytext=(dt, figLevel),
                    arrowprops=dict(arrowstyle="-", color=color, linewidth=0.8, linestyle=timeline_df["Linestyle"][idx]),
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

            fig.suptitle(spec.title, fontsize=14)
            ax.set_title(spec.description, pad=10, loc="left", fontsize=25, fontweight="bold")
            ax.grid(False)
        return fig

    def save(self, fig, filePath, figure_format):
        if figure_format=="svg":
            save_svg_figure(fig, filePath)
        else:
            fig.savefig(filePath)
        plt.close(fig)
        return filePath

    ######## interactive figures (Jupyter) ########
    def set_titles(self, fig, ax, title, description, xLabel, yLabel):
        fig.suptitle(title,fontsize=14)
        ax.set_title(description)
        if xLabel:
            ax.set_xlabel(xLabel)
        ax.set_ylabel(yLabel)

    def set_legend(self, fig, ax, handles, labels, visible):
        if visible:
            ax.legend(handles,labels,loc="upper right")
        elif ax.get_legend() is not None:
            ax.get_legend().remove()

    def clear(self, fig, ax):
        ax.clear()

    def lines_data(self, fig, ax):
        data=[]
        for line in ax.get_lines():
            x,y=line.get_data()
            x=np.asarray(x)
            # datetime64 is stored as int64 nanoseconds since epoch: seconds with sub-second precision
            if x.dtype.kind == 'M':
                x = x.astype('int64') / 1e9
            data.append((x,np.asarray(y)))
        return data

    def view_limits(self, fig, ax):
        return ax.get_xlim(),ax.get_ylim()

    def export_image(self, fig, filePath):
        fig.savefig(filePath)
