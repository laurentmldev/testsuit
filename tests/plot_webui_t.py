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
    """A folder of a TDMS file (a speed, and a trajectory x, y, z: 2000 points at 100 Hz) and a CSV file."""
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
    (data / "notes.csv").write_text("not a table\n")
    found = plots.sources([str(data)])
    assert {f["name"]: f["type"] for f in found["files"]} == {"brake.csv": "csv", "motor.tdms": "tdms", "notes.csv": "csv"}
    # a file its loader cannot read says why, and the others stay usable
    assert [f["name"] for f in found["files"] if f["error"]] == ["notes.csv"]
    assert not any(f.startswith("notes.csv::") for f in found["fields"])
    assert "motor.tdms::Motor___speed" in found["fields"] and "brake.csv::pressure" in found["fields"]

    # 2D: two files, an offset, a decimation
    answer = json.loads(plots.figure({"sources": [str(data)], "title": "Bench", "maxPoints": 500, "traces": [
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
    fig3d = json.loads(plots.figure({"sources": [str(data)], "projection": "3d", "traces": [xyz]}))["figure"]
    assert [d["type"] for d in fig3d["data"]] == ["scatter3d"] and fig3d["data"][0]["name"] == "probe"
    fig2d = json.loads(plots.figure({"sources": [str(data)], "traces": [xyz]}))["figure"]
    assert [d["name"] for d in fig2d["data"]] == ["probe: Position___x", "probe: Position___y", "probe: Position___z"]
    bad = json.loads(plots.figure({"sources": [str(data)], "projection": "3d", "traces": [trace(["motor.tdms::Motor___speed"])]}))
    assert bad["figure"] is None and "3 fields" in bad["traces"][0]["error"]

    # hidden traces are not drawn
    hidden = json.loads(plots.figure({"sources": [str(data)], "traces": [trace(["brake.csv::pressure"], hidden=True)]}))
    assert hidden == {"figure": None, "traces": []}
    with pytest.raises(CriteriaError):
        plots.sources([str(data / "missing")])


def test_fields_loaded_together(data, monkeypatch):
    """The fields of a figure not loaded yet are loaded in one call (the CSV loader then reads its file
    once), and are then kept: restyling loads nothing."""
    from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr
    calls = []
    real = FolderParamMgr.loadParams
    monkeypatch.setattr(FolderParamMgr, "loadParams", lambda self, names, *a, **k: calls.append(list(names)) or real(self, names, *a, **k))
    plots = PlotData()
    fields = ["motor.tdms::Motor___speed", "brake.csv::pressure", "motor.tdms::Position___x"]
    answer = json.loads(plots.figure({"sources": [str(data)], "traces": [trace([f]) for f in fields]}))
    assert calls == [fields] and all(not r["error"] for r in answer["traces"])
    assert [d["name"] for d in answer["figure"]["data"]] == ["Motor___speed (motor.tdms)", "pressure (brake.csv)", "Position___x (motor.tdms)"]
    plots.figure({"sources": [str(data)], "traces": [trace([f], color="#000000") for f in fields]})
    assert len(calls) == 1


def test_exports(data):
    plots = PlotData()
    spec = {"sources": [str(data)], "maxPoints": 100, "xLabel": "time",
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
        assert [f["name"] for f in found["files"]] == ["motor.tdms"]
        r = requests.post(f"{url}/figure", json={"sources": [str(data)], "traces": [trace(["motor.tdms::Motor___speed"])]})
        assert r.status_code == 200 and r.json()["figure"]["data"][0]["type"] == "scatter"
        assert requests.post(f"{url}/sources", json={"paths": []}).status_code == 400
        csv = requests.post(f"{url}/export/csv", json={"sources": [str(data)], "traces": [trace(["brake.csv::pressure"])]})
        assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
        assert requests.post(f"{url}/export/html", json={"sources": [str(data)], "traces": []}).status_code == 400
    finally:
        server.should_exit = True
