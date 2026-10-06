"""Plot backends of plotHelpers: matplotlib (default) and plotly, interactive (Jupyter) and static (reports)."""
import base64,gzip,json,os,re,shutil,logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
import pytest
import plotly.graph_objects as go

from testsuit.datatools import plotHelpers
from testsuit.datatools.plotBackends import (APlotBackend, BACKEND_ENV_VAR, FORMAT_ENV_VAR, get_plot_backend,
                                             register_plot_backend, unregister_plot_backend, set_default_plot_backend)
from testsuit.datatools.plotBackends.plotlyBackend import PLOTLY_JSON_SUFFIX
from testsuit.exploit.mexploit.mexploit import mexploit
from testsuit.exploit.runner import report_html

logging.basicConfig(level=logging.DEBUG)

TMP_ROOT=Path("tests/tmp/plot_backends")


@pytest.fixture
def tmp_folder(request):
    folder=TMP_ROOT / request.node.name
    shutil.rmtree(folder,ignore_errors=True)
    folder.mkdir(parents=True)
    return folder


@pytest.fixture(autouse=True)
def no_plot_env(monkeypatch):
    monkeypatch.delenv(BACKEND_ENV_VAR,raising=False)
    monkeypatch.delenv(FORMAT_ENV_VAR,raising=False)


def _critConf(folder, **kwargs):
    return {"name":"my plot","type":"plot","test_run_config":{"results":str(folder)}} | kwargs


def _array(values):
    """plotly JSON arrays: lists, or base64 typed arrays ({"dtype","bdata"}) with plotly>=6"""
    if isinstance(values,dict):
        return np.frombuffer(base64.b64decode(values["bdata"]),dtype=values["dtype"])
    return np.asarray(values)


def _dataList():
    t=1.7e9+np.arange(100)/2   # 50s of dates in seconds
    return [{"data":pd.Series(np.sin(t),index=t,name="sin"),"name":"data.h5::/grp/sin","color":"red","drawstyle":"steps-post"},
            {"data":pd.DataFrame({"a":np.cos(t),"b":2*np.cos(t)},index=t),"linestyle":"dashed","marker":"o"},
            {"data":0.5,"name":"threshold"}]


################### backend selection ###################

def test_default_backend_is_plotly(tmp_folder):
    filePath=plotHelpers.plotData(_critConf(tmp_folder),_dataList(),interactive=False)
    assert filePath==str(tmp_folder / "figures" / ("my_plot"+PLOTLY_JSON_SUFFIX))


@pytest.mark.parametrize("how",["critConf","env","default"])
def test_select_matplotlib(tmp_folder, how, monkeypatch):
    critConf=_critConf(tmp_folder)
    if how=="critConf": critConf["rendering_engine"]="matplotlib"
    elif how=="env": monkeypatch.setenv(BACKEND_ENV_VAR,"matplotlib")
    else: set_default_plot_backend("matplotlib")
    try:
        filePath=plotHelpers.plotData(critConf,_dataList(),interactive=False)
    finally:
        set_default_plot_backend("plotly")
    assert filePath==str(tmp_folder / "figures" / "my_plot.svg")
    assert ">my plot</text>" in Path(filePath).read_text()


def test_unknown_backend_and_format(tmp_folder):
    with pytest.raises(ValueError,match="unknown rendering engine: 'bokeh' \\(matplotlib\\|plotly"):
        plotHelpers.plotData(_critConf(tmp_folder,rendering_engine="bokeh"),_dataList(),interactive=False)
    with pytest.raises(ValueError,match="figure format 'interactive' not supported by plot backend 'matplotlib'"):
        plotHelpers.plotData(_critConf(tmp_folder,rendering_engine="matplotlib",figure_format="interactive"),_dataList(),interactive=False)


def test_register_external_backend(tmp_folder):
    class TextBackend(APlotBackend):
        name="text"
        figure_formats={"txt":".txt"}
        def lines(self, spec, fig=None, ax=None, handles=None, labels=None, interactive=True):
            return [spec.title]+[t.label for t in spec.traces],None,[],[]
        def save(self, fig, filePath, figure_format):
            Path(filePath).write_text("\n".join(fig))
            return filePath

    register_plot_backend(TextBackend())
    try:
        filePath=plotHelpers.plotData(_critConf(tmp_folder,rendering_engine="text"),_dataList(),interactive=False)
    finally:
        unregister_plot_backend("text")
    assert Path(filePath).read_text().split("\n")==["my plot","/grp/sin","a","b","threshold"]


################### plotly ###################

def test_plotly_static_figure(tmp_folder):
    filePath=plotHelpers.plotData(_critConf(tmp_folder,rendering_engine="plotly",title="T",description="D"),_dataList(),interactive=False)
    fig=go.Figure(json.loads(Path(filePath).read_text()))
    assert [t.name for t in fig.data]==["/grp/sin","a","b","threshold"]
    assert fig.layout.title.text=="T" and fig.layout.title.subtitle.text=="D"
    sin,a,b,threshold=fig.data
    # dates in seconds -> ms on a date axis, as HH:MM:SS for more than 30s
    assert fig.layout.xaxis.type=="date" and fig.layout.xaxis.tickformat=="%H:%M:%S"
    assert np.allclose(_array(sin.x)/1e3,1.7e9+np.arange(100)/2)
    assert (sin.mode,sin.line.color,sin.line.shape)==("lines","red","hv")
    assert (a.mode,a.line.dash,a.marker.symbol)==("lines+markers","dash","circle")
    assert list(_array(threshold.y))==[0.5,0.5]
    assert fig.layout.width==1200 and fig.layout.height==700


def test_plotly_3d_single_figure(tmp_folder):
    xyz=pd.DataFrame({"x":np.arange(10.),"y":np.arange(10.)**2,"z":np.arange(10.)**3})
    critConf=_critConf(tmp_folder,rendering_engine="plotly",camera_eye=(1,2,3),zLabel="alt")
    assert plotHelpers.static_figures_are_interactive(critConf)
    assert not plotHelpers.static_figures_are_interactive(_critConf(tmp_folder,rendering_engine="matplotlib"))
    fig=go.Figure(json.loads(Path(plotHelpers.plotData3D(critConf,[{"data":xyz,"name":"traj"}],interactive=False)).read_text()))
    assert fig.data[0].type=="scatter3d" and list(_array(fig.data[0].z))==list(xyz.z)
    assert (fig.layout.scene.camera.eye.x,fig.layout.scene.camera.eye.y,fig.layout.scene.camera.eye.z)==(1,2,3)
    assert fig.layout.scene.zaxis.title.text=="alt"


def test_histogram_same_bins(tmp_folder):
    values=pd.Series(np.random.default_rng(0).normal(size=500),name="v")
    edges=np.histogram_bin_edges(values,bins=13)
    fig=go.Figure(json.loads(Path(plotHelpers.plotDataHistogram(_critConf(tmp_folder,rendering_engine="plotly"),{"data":values,"name":"f::v"},interactive=False)).read_text()))
    assert np.isclose(fig.data[0].xbins.start,edges[0]) and np.isclose(fig.data[0].xbins.size,edges[1]-edges[0])
    assert fig.layout.yaxis.title.text=="nb occurences within v"


def test_plotly_interactive_figure():
    backend=get_plot_backend("plotly")
    critConf={"name":"gui","rendering_engine":"plotly"}
    fig,ax,handles,labels=plotHelpers.plotData(critConf,_dataList()[:1])
    fig,ax,handles,labels=plotHelpers.plotData(critConf,_dataList()[1:2],fig=fig,ax=ax,dataHandles=handles,legendsLabels=labels)
    assert labels==["/grp/sin","a","b"] and len(handles)==3 and len(fig.data)==3
    assert fig.layout.width is None   # fits the notebook cell

    backend.set_titles(fig,ax,"title","descr","x label","y label")
    assert (fig.layout.title.text,fig.layout.xaxis.title.text)==("title","x label")
    backend.set_legend(fig,ax,handles,["s","A","B"],visible=False)
    assert fig.layout.showlegend is False and [t.name for t in fig.data]==["s","A","B"]

    # CSV export of the zoomed area: dates back in seconds
    (x,y),*_=backend.lines_data(fig,ax)
    assert np.allclose(x,1.7e9+np.arange(100)/2)
    fig.update_xaxes(range=["2023-11-14 22:13:30","2023-11-14 22:13:40"])   # as set by a zoom in the notebook
    (xmin,xmax),(ymin,ymax)=backend.view_limits(fig,ax)
    assert (xmin,xmax)==(1.7e9+10,1.7e9+20) and ymin<=-0.99 and ymax>=1.99

    backend.clear(fig,ax)
    assert len(fig.data)==0


################### report ###################

def test_mexploit_plotly_figures_in_report(tmp_folder):
    rst=mexploit("tests/etc/mexploit/scenarii/mxp_OK_main_functions","tests/etc/data",str(tmp_folder / "results"),force=True,plot_backend="plotly")
    assert rst==0
    assert BACKEND_ENV_VAR not in os.environ
    figures=sorted(p.relative_to(tmp_folder / "results" / "figures").as_posix() for p in (tmp_folder / "results" / "figures").rglob("*.*"))
    assert all(f.endswith(PLOTLY_JSON_SUFFIX) for f in figures)
    # one rotatable 3D figure instead of 3 views
    assert [f for f in figures if f.startswith("traj_3d/")]==["traj_3d/traj_3d"+PLOTLY_JSON_SUFFIX]
    assert "seqcheck/seqcheck"+PLOTLY_JSON_SUFFIX in figures

    # pytest-html report: each figure drawn by a page shown in an iframe, plotly.js shared
    pages=tmp_folder / "results" / "pytest_report_files"
    page=(pages / "figures" / "plot_multi" / "plot_multi.html").read_text()
    assert '<script src="../../plotly.min.js"></script>' in page and '"A Nice multi plot"' in page
    assert (pages / "plotly.min.js").stat().st_size>1e6
    assert "iframe src='pytest_report_files/figures/plot_multi/plot_multi.html'" in (tmp_folder / "results" / "pytest_report.html").read_text().replace("&#39;","'")

    figFile=tmp_folder / "results" / "figures" / "plot_multi" / ("plot_multi"+PLOTLY_JSON_SUFFIX)
    html=report_html._plotly_figure_html(figFile)
    data=re.search(r'data-plotlyz="([^"]+)"',html).group(1)
    assert gzip.decompress(base64.b64decode(data))==figFile.read_bytes()
    assert 'filename="plot_multi.plotly.json"' in html

    lib=report_html._embedded_plotly_html()
    assert "class='embedded-plotly'" in lib
    js=gzip.decompress(base64.b64decode(re.search(r">([^<]+)</script>",lib).group(1))).decode()
    assert "plotly.js" in js[:500]


@pytest.mark.parametrize("embedded",[True,False])
def test_report_figures_html(tmp_folder, embedded):
    """plotly figures are always embedded (a local report can't load a JSON file), images as before"""
    results=tmp_folder.resolve()
    critFolder=results / "testrun" / "figures" / "crit_x"
    critFolder.mkdir(parents=True)
    (critFolder / "fig.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (critFolder / ("fig"+PLOTLY_JSON_SUFFIX)).write_text('{"data":[],"layout":{}}')
    synthesisFolder=None if embedded else results / "synthesis_files" / "figures"
    html=report_html._extract_tc_figures("s","ts","crit_x",results / "testrun",results,synthesisFolder,embedded)
    assert html.count('<div class="tc-plotly" data-plotlyz=')==1
    assert ("data-svgz=" in html)==embedded
    assert ('src="synthesis_files/figures/s/ts/fig.svg"' in html)==(not embedded)
