# Copyright (C) 2026 Laurent Millet-Lacombe
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of testsuit.
# testsuit is free software: you can redistribute it and/or modify it under the terms of the GNU Affero
# General Public License as published by the Free Software Foundation, either version 3 of the License,
# or (at your option) any later version.
# testsuit is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General
# Public License for more details.
# You should have received a copy of the GNU Affero General Public License along with testsuit. If not,
# see <https://www.gnu.org/licenses/>.


"""The Plot page of ``mxp webui`` (testsuit.exploit.editor.plots): data files opened with the data loaders,
figures of the plotly backend, exports; through Python and through the REST routes."""

import base64
import io
import json
import shutil
import socket
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests

from testsuit import plugins
from testsuit.exploit.editor.plots import PlotData, decimate, stride
from testsuit.exploit.editor.service import CriteriaError

TMP_ROOT = Path("tests/tmp/plot_webui")
START = 1_794_994_200.0          # 2026-11-18 09:30:00 UTC


@pytest.fixture(autouse=True)
def no_installed_plugins(monkeypatch):
    monkeypatch.setattr(plugins, "entry_points", lambda group: [])


@pytest.fixture
def data(request) -> Path:
    """A TDMS file (a speed, and a trajectory x, y, z: 2000 points at 100 Hz) and a CSV file."""
    from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter
    root = (TMP_ROOT / request.node.name).resolve()
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    n = 2000
    t = np.arange(n) / 100
    props = {"wf_start_time": np.datetime64("2026-11-18T09:30:00"), "wf_increment": 0.01, "wf_start_offset": 0.0}
    with TdmsWriter(str(root / "motor.tdms")) as w:
        w.write_segment([RootObject({}), GroupObject("Motor"),
                         ChannelObject("Motor", "speed", t * 2 + 5, properties=props),
                         GroupObject("Position"),
                         ChannelObject("Position", "x", np.cos(t), properties=props),
                         ChannelObject("Position", "y", np.sin(t), properties=props),
                         ChannelObject("Position", "z", t, properties=props)])
    tt = np.arange(0, 20, 0.5)
    pd.DataFrame({"Timestamps": pd.to_datetime(START + tt, unit="s").strftime("%Y-%m-%d %H:%M:%S.%f"),
                  "pressure": tt * 0 + 3.0}).to_csv(root / "brake.csv", sep=";", index=False)
    return root


def files(data: Path) -> list[str]:
    """The two files of data, each a source of its own."""
    return [str(data / "motor.tdms"), str(data / "brake.csv")]


def trace(fields, **style):
    return {"id": fields[0], "fields": fields, **style}


def test_decimate_keeps_peaks():
    s = pd.Series(np.zeros(100_000), index=np.arange(100_000) / 1000)
    s.iloc[54_321] = 9.0
    d = decimate(s, 1000)
    assert len(d) <= 1000 and d.max() == 9.0 and d.index.is_monotonic_increasing
    assert decimate(s, None) is s and len(stride(s.to_frame(), 1000)) <= 1000


def test_sources_and_figures(data):
    plots = PlotData()
    found = plots.sources(files(data))
    assert [(x["label"], x["folder"], [f["type"] for f in x["files"]]) for x in found["sources"]] == \
        [("motor.tdms", False, ["tdms"]), ("brake.csv", False, ["csv"])]
    assert "motor.tdms::Motor___speed" in found["fields"] and "brake.csv::pressure" in found["fields"]

    # 2D: two files, an offset, a decimation
    answer = json.loads(plots.figure({"sources": files(data), "title": "Bench", "maxPoints": 500, "traces": [
        trace(["motor.tdms::Motor___speed"], color="#ff0000", linestyle="dashed"),
        trace(["brake.csv::pressure"], offset="-3"),
        trace(["motor.tdms::nothing"])]}))
    reports = {r["id"]: r for r in answer["traces"]}
    assert reports["motor.tdms::Motor___speed"]["points"] == 2000 and reports["motor.tdms::Motor___speed"]["shown"] <= 500
    assert reports["brake.csv::pressure"]["points"] == 40 and reports["motor.tdms::nothing"]["error"]
    fig = answer["figure"]
    assert fig["layout"]["title"]["text"] == "Bench" and fig["layout"]["xaxis"]["type"] == "date"
    speed, pressure = fig["data"]
    assert speed["name"] == "Motor___speed (motor.tdms)" and speed["line"]["color"] == "#ff0000" and speed["line"]["dash"] == "dash"
    assert pressure["name"] == "pressure (brake.csv)"

    # 3D: X, Y and Z of three fields; the same trace in 2D is three lines against time
    xyz = trace(["motor.tdms::Position___x", "motor.tdms::Position___y", "motor.tdms::Position___z"], label="probe")
    fig3d = json.loads(plots.figure({"sources": files(data), "projection": "3d", "traces": [xyz]}))["figure"]
    assert [d["type"] for d in fig3d["data"]] == ["scatter3d"] and fig3d["data"][0]["name"] == "probe"
    fig2d = json.loads(plots.figure({"sources": files(data), "traces": [xyz]}))["figure"]
    assert [d["name"] for d in fig2d["data"]] == ["probe: Position___x", "probe: Position___y", "probe: Position___z"]
    bad = json.loads(plots.figure({"sources": files(data), "projection": "3d", "traces": [trace(["motor.tdms::Motor___speed"])]}))
    assert bad["figure"] is None and "3 fields" in bad["traces"][0]["error"]

    # hidden traces are not drawn
    hidden = json.loads(plots.figure({"sources": files(data), "traces": [trace(["brake.csv::pressure"], hidden=True)]}))
    assert hidden == {"figure": None, "traces": []}
    with pytest.raises(CriteriaError):
        plots.sources([str(data / "missing")])


def test_folder_is_one_source(data):
    """A folder picked is one data source (FolderParamMgr): a parameter of several of its files (a recording
    split in several files) is one field, merged in time; a file its loader cannot read says why."""
    from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter
    split = data / "split"
    split.mkdir()
    for k in range(3):
        props = {"wf_start_time": np.datetime64("2026-11-18T09:30:00") + np.timedelta64(k * 10, "s"),
                 "wf_increment": 0.01, "wf_start_offset": 0.0}
        with TdmsWriter(str(split / f"part_{k + 1}.tdms")) as w:
            w.write_segment([RootObject({}), GroupObject("Motor"), ChannelObject("Motor", "speed", np.full(1000, k), properties=props)])
    (split / "notes.csv").write_text("not a table\n")
    plots = PlotData()
    found = plots.sources([str(split), str(data / "brake.csv")])
    folder = found["sources"][0]
    assert folder["label"] == "split" and folder["folder"] and folder["fields"] == 1
    assert sorted(f["name"] for f in folder["files"]) == ["notes.csv", "part_1.tdms", "part_2.tdms", "part_3.tdms"]
    assert [f["name"] for f in folder["files"] if f["error"]] == ["notes.csv"]
    assert found["fields"][0] == "split::Motor___speed"
    answer = json.loads(plots.figure({"sources": [str(split), str(data / "brake.csv")],
                                      "traces": [trace(["split::Motor___speed"]), trace(["brake.csv::pressure"])]}))
    assert [r["points"] for r in answer["traces"]] == [3000, 40]
    speed = answer["figure"]["data"][0]
    y = speed["y"]
    if isinstance(y, dict):     # plotly >= 6: a typed array
        y = np.frombuffer(base64.b64decode(y["bdata"]), dtype=y["dtype"])
    assert speed["name"] == "Motor___speed (split)" and sorted(set(np.asarray(y).tolist())) == [0, 1, 2]

    # files of a folder whose times overlap cannot be merged: said, not hidden
    props = {"wf_start_time": np.datetime64("2026-11-18T09:30:05"), "wf_increment": 0.01, "wf_start_offset": 0.0}
    with TdmsWriter(str(split / "part_1b.tdms")) as w:
        w.write_segment([RootObject({}), GroupObject("Motor"), ChannelObject("Motor", "speed", np.full(1000, 9), properties=props)])
    plots = PlotData()
    answer = json.loads(plots.figure({"sources": [str(split)], "traces": [trace(["split::Motor___speed"])]}))
    assert answer["figure"] is None and "overlap" in answer["traces"][0]["error"]

    # two sources of the same name stay apart
    other = data / "other" / "brake.csv"
    other.parent.mkdir()
    shutil.copy(data / "brake.csv", other)
    found = plots.sources([str(data / "brake.csv"), str(other)])
    assert [x["label"] for x in found["sources"]] == ["brake.csv", "brake.csv (2)"]
    assert "brake.csv (2)::pressure" in found["fields"]


def test_fields_loaded_together(data, monkeypatch):
    """The fields of a figure not loaded yet are loaded in one call (the CSV loader then reads its file
    once), and are then kept: restyling loads nothing."""
    from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr
    calls = []
    real = FolderParamMgr.loadParams
    monkeypatch.setattr(FolderParamMgr, "loadParams", lambda self, names, *a, **k: calls.append(list(names)) or real(self, names, *a, **k))
    plots = PlotData()
    fields = ["motor.tdms::Motor___speed", "brake.csv::pressure", "motor.tdms::Position___x"]
    answer = json.loads(plots.figure({"sources": files(data), "traces": [trace([f]) for f in fields]}))
    # one call per source: motor.tdms (speed and x), brake.csv (pressure, alone: loaded on its own)
    assert calls == [["motor.tdms::Motor___speed", "motor.tdms::Position___x"], ["brake.csv::pressure"]] and all(not r["error"] for r in answer["traces"])
    assert [d["name"] for d in answer["figure"]["data"]] == ["Motor___speed (motor.tdms)", "pressure (brake.csv)", "Position___x (motor.tdms)"]
    plots.figure({"sources": files(data), "traces": [trace([f], color="#000000") for f in fields]})
    assert len(calls) == 2


def test_exports(data):
    plots = PlotData()
    spec = {"sources": files(data), "maxPoints": 100, "xLabel": "time",
            "traces": [trace(["motor.tdms::Motor___speed"]), trace(["brake.csv::pressure"], offset="autoz:1s")]}
    # every point of the view (the decimation is for the screen only), on one time column
    csv = pd.read_csv(io.StringIO(plots.export_csv({**spec, "view": {"x": [START, START + 5]}})))
    assert list(csv.columns) == ["time", "Motor___speed__motor_tdms_", "pressure__brake_csv_"]
    assert len(csv["Motor___speed__motor_tdms_"].dropna()) == 501 and csv["time"].iloc[0] == START
    assert (csv["pressure__brake_csv_"].dropna() == 0).all()
    page = plots.export_html(spec)
    assert "<html" in page[:200] and "Plotly.newPlot" in page
    with pytest.raises(CriteriaError):
        plots.export_csv({**spec, "projection": "3d"})


def test_webui_plot_routes(data):
    """The /plot routes of ``mxp webui``, through HTTP."""
    import uvicorn

    from testsuit.exploit.editor.standalone import create_app

    app = create_app(data, plugins=False, results=data / "results")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    url = f"http://127.0.0.1:{port}/plot"
    for _ in range(100):
        try:
            requests.get(f"http://127.0.0.1:{port}/mode", timeout=1)
            break
        except requests.ConnectionError:
            time.sleep(0.1)
    try:
        js = requests.get(f"{url}/plotly.js")
        assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript") and "plotly" in js.text[:2000].lower()
        found = requests.post(f"{url}/sources", json={"paths": [str(data / "motor.tdms")]}).json()
        assert [f["name"] for f in found["sources"][0]["files"]] == ["motor.tdms"]
        r = requests.post(f"{url}/figure", json={"sources": files(data), "traces": [trace(["motor.tdms::Motor___speed"])]})
        assert r.status_code == 200 and r.json()["figure"]["data"][0]["type"] == "scatter"
        assert requests.post(f"{url}/sources", json={"paths": []}).status_code == 400
        csv = requests.post(f"{url}/export/csv", json={"sources": files(data), "traces": [trace(["brake.csv::pressure"])]})
        assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
        assert requests.post(f"{url}/export/html", json={"sources": files(data), "traces": []}).status_code == 400
    finally:
        server.should_exit = True
