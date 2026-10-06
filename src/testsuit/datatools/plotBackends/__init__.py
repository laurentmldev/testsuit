"""Plot backends of testsuit.datatools.plotHelpers.

A backend draws the figures described by plotBackends.common (FigureSpec), for two uses:

- interactive: in Jupyter notebooks (PlotJupyterGui), the figure is returned to be displayed and updated
- static: in mexploit reports, the figure is saved in a file embedded in the exploit_runner HTML report

Built-in backends:

- ``plotly`` (default): static figures are interactive by default (zoom, hover, 3D rotation in the HTML
  report, which then embeds plotly.js, ~2 MB), or SVG/PNG images with kaleido. FigureWidgets in Jupyter
  need anywidget (``pip install testsuit[plotly]``)
- ``matplotlib``: static figures as SVG (or PNG)

The backend of a plot is, by priority: the ``rendering_engine`` entry of its critConf (scenario YAML),
the ``PLOT_RENDERING_BACKEND`` environment variable (set by ``mxp --plot-backend``), the default set with
set_default_plot_backend(). Same for the static figures format: ``figure_format`` entry,
``PLOT_FIGURE_FORMAT`` environment variable, the backend's default format.

External backends are registered with register_plot_backend()::

    from testsuit.datatools.plotBackends import APlotBackend, register_plot_backend

    class MyBackend(APlotBackend):
        name = "mybackend"
        ...

    register_plot_backend(MyBackend())
"""
from __future__ import annotations

import os
from collections.abc import Callable

from testsuit.datatools.plotBackends.common import FigureSpec

BACKEND_ENV_VAR = "PLOT_RENDERING_BACKEND"
FORMAT_ENV_VAR = "PLOT_FIGURE_FORMAT"


class APlotBackend:
    """Draws FigureSpecs. Drawing methods return the backend's figure objects, the interactive
    methods (used by the Jupyter GUI) take them back."""

    name: str = ""
    # formats of static figures: {format: file suffix}, the first one is the default
    figure_formats: dict[str, str] = {"svg": ".svg"}

    def static_figures_are_interactive(self, figure_format: str) -> bool:
        """True when the static figures of this format can be zoomed/rotated in the report
        (a single 3D figure is then enough, instead of several views)"""
        return False

    ######## drawing ########
    def lines(self, spec: FigureSpec, fig=None, ax=None, handles: list | None = None,
              labels: list | None = None) -> tuple:
        """Draw spec's 2D traces, in the given (fig, ax) to add traces to an existing figure.
        :return: (fig, ax, handles, labels), handles and labels of all the figure's traces"""
        raise NotImplementedError

    def lines3d(self, spec: FigureSpec, fig=None, ax=None, handles: list | None = None,
                labels: list | None = None) -> tuple:
        """Same as lines(), with spec's 3D traces"""
        raise NotImplementedError

    def histogram(self, spec: FigureSpec):
        """:return: figure of the histogram of spec's trace y values, in spec.nbBins bins"""
        raise NotImplementedError

    def timeline(self, spec: FigureSpec, timeline_df):
        """:return: figure of the events of timeline_df (see common.timeline_dataframe)"""
        raise NotImplementedError

    def save(self, fig, filePath: str, figure_format: str) -> str:
        """Save fig in filePath (with the format's suffix) and release it. :return: filePath"""
        raise NotImplementedError

    ######## interactive figures (Jupyter) ########
    def display(self, fig) -> object | None:
        """Widget showing fig, to be displayed once when fig is created (None: the backend shows it itself)"""
        return None

    def set_titles(self, fig, ax, title: str, description: str, xLabel: str | None, yLabel: str) -> None:
        raise NotImplementedError

    def set_legend(self, fig, ax, handles: list, labels: list, visible: bool) -> None:
        raise NotImplementedError

    def clear(self, fig, ax) -> None:
        """Remove all traces"""
        raise NotImplementedError

    def lines_data(self, fig, ax) -> list[tuple]:
        """(x, y) numpy arrays of each 2D trace, x dates as seconds"""
        raise NotImplementedError

    def view_limits(self, fig, ax) -> tuple[tuple[float, float], tuple[float, float]]:
        """((xmin, xmax), (ymin, ymax)) currently shown (zoom), x dates as seconds"""
        raise NotImplementedError

    def export_image(self, fig, filePath: str) -> None:
        """Save fig as an image, format given by filePath extension"""
        raise NotImplementedError


_backends: dict[str, APlotBackend | Callable[[], APlotBackend]] = {}
_defaultBackend = "plotly"


def register_plot_backend(backend: APlotBackend | Callable[[], APlotBackend], name: str | None = None) -> None:
    """Register a backend, or a function creating it on first use (to import its library only when
    needed). Registering a name again replaces the backend."""
    name = name or backend.name
    if not name:
        raise ValueError("plot backend without name")
    _backends[name] = backend


def unregister_plot_backend(name: str) -> None:
    _backends.pop(name, None)


def plot_backend_names() -> list[str]:
    return list(_backends)


def set_default_plot_backend(name: str) -> None:
    """Backend used when neither critConf nor the PLOT_RENDERING_BACKEND environment variable select one"""
    if name not in _backends:
        raise ValueError(f"unknown plot backend: '{name}' ({'|'.join(_backends)})")
    global _defaultBackend
    _defaultBackend = name


def plot_backend_name(critConf: dict | None = None) -> str:
    if critConf and critConf.get("rendering_engine"):
        return critConf["rendering_engine"]
    return os.getenv(BACKEND_ENV_VAR) or _defaultBackend


def get_plot_backend(nameOrCritConf: str | dict | None = None) -> APlotBackend:
    """Backend of the given name, or selected by critConf (see module doc)"""
    name = nameOrCritConf if isinstance(nameOrCritConf, str) else plot_backend_name(nameOrCritConf)
    if name not in _backends:
        raise ValueError(f"unknown rendering engine: '{name}' ({'|'.join(_backends)})")
    backend = _backends[name]
    if not isinstance(backend, APlotBackend):
        backend = _backends[name] = backend()
    return backend


def figure_format(backend: APlotBackend, critConf: dict | None = None) -> str:
    """Format of static figures selected by critConf (see module doc)"""
    fmt = (critConf or {}).get("figure_format") or os.getenv(FORMAT_ENV_VAR) or next(iter(backend.figure_formats))
    if fmt not in backend.figure_formats:
        raise ValueError(f"figure format '{fmt}' not supported by plot backend '{backend.name}' ({'|'.join(backend.figure_formats)})")
    return fmt


def _matplotlib_backend() -> APlotBackend:
    from testsuit.datatools.plotBackends.matplotlibBackend import MatplotlibBackend
    return MatplotlibBackend()


def _plotly_backend() -> APlotBackend:
    from testsuit.datatools.plotBackends.plotlyBackend import PlotlyBackend
    return PlotlyBackend()


register_plot_backend(_matplotlib_backend, "matplotlib")
register_plot_backend(_plotly_backend, "plotly")
