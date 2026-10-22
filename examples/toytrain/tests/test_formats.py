"""The toy-train file formats, loaded through testsuit like its built-in ones."""
import shutil
from importlib.metadata import entry_points

import numpy as np
import pytest

from testsuit.datatools.DataFileMgrs import formats
from testsuit.datatools.DataFileMgrs.CsvFileMgr import CsvFileMgr, GetCsvFileType
from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr
from testsuit.misc.MonitorProgress import MonitorProgress

from toytrain.filemgrs import PropulsionJsonFileMgr, SimulatedFileMgr, TelemetryCsvFileMgr

from conftest import PROJECT_ROOT, SAMPLE

DATA = SAMPLE / "data" / "max_speed"
OVERSPEED = PROJECT_ROOT / "tests" / "etc" / "overspeed"
START = 1792663200.0  # 2026-10-22T10:00:00Z


def load(scanner, name):
    [param] = scanner.findParams(name, monitorProgress=MonitorProgress(name="test"), silent=True)
    return param.iloc[:, 0]


def test_plugin_declared_as_entry_point():
    assert "toytrain" in {ep.name for ep in entry_points(group="testsuit.plugins")}


def test_folder_scan_uses_toytrain_readers():
    # FolderParamMgr loads the plugins by itself
    mgrs = {m.getBaseName(): type(m) for m in FolderParamMgr(str(DATA)).getFileMgrs()}
    assert mgrs == {"propulsion.propj": PropulsionJsonFileMgr,
                    "telemetry.csv": TelemetryCsvFileMgr}


def test_propulsion_json():
    sent = load(FolderParamMgr(str(DATA)), "speed_sent_mps")
    assert len(sent) == 601
    assert sent.index[0] == START and sent.index[-1] == START + 30
    assert sent.max() == pytest.approx(0.5)


def test_simulated_signals():
    scanner = FolderParamMgr(str(OVERSPEED))
    speed = load(scanner, "^speed_mps")
    assert len(speed) == 601
    assert speed.iloc[0] == pytest.approx(0.01) and speed.iloc[-1] == pytest.approx(0.8)
    assert list(np.unique(load(scanner, "pilot_state"))) == [1]


def test_simulated_is_deterministic(tmp_path):
    shutil.copy(OVERSPEED / "overspeed.sim.yml", tmp_path / "again.sim.yml")
    first = SimulatedFileMgr(str(OVERSPEED / "overspeed.sim.yml")).channels()["speed_mps"]
    second = SimulatedFileMgr(str(tmp_path / "again.sim.yml")).channels()["speed_mps"]
    assert first.equals(second)


def test_simulated_kinds(tmp_path):
    simFile = tmp_path / "kinds.sim.yml"
    simFile.write_text("start: 0\nduration_sec: 20\nrate_hz: 1\nsignals:\n"
                       "  state: {kind: steps, values: [0, 1, 2], every_sec: 10}\n"
                       "  vibration: {kind: sine, amplitude: 1, freq_hz: 0.25, plus: [{kind: noise, sigma: 0.01, seed: 1}]}\n")
    channels = SimulatedFileMgr(str(simFile)).channels()
    assert list(channels["state"].iloc[[0, 10, 20]]) == [0, 1, 2]
    assert channels["vibration"].iloc[1] == pytest.approx(1, abs=0.05)


def test_simulated_unknown_kind(tmp_path):
    simFile = tmp_path / "bad.sim.yml"
    simFile.write_text("start: 0\nduration_sec: 1\nrate_hz: 1\nsignals:\n  X: {kind: square}\n")
    with pytest.raises(ValueError, match="unknown simulated signal kind 'square'"):
        SimulatedFileMgr(str(simFile)).getFieldNames()


def test_telemetry_csv():
    mgr = formats.create_file_mgr(str(DATA / "telemetry.csv"))
    assert type(mgr) is TelemetryCsvFileMgr
    assert GetCsvFileType(str(DATA / "telemetry.csv")) == "toytrain-telemetry"
    assert mgr.getFieldNames() == ["Time", "speed_mps", "pilot_cmd", "pilot_state", "contact_front", "contact_back"]
    assert mgr.getNbEntries() == 601
    assert mgr.metadata() == {"version": "v1", "unit": "CALC-TM", "train": "TT-01"}

    speed = load(FolderParamMgr(str(DATA)), "^speed_mps")
    assert speed.index[0] == START and speed.index[1] == pytest.approx(START + 0.05, abs=1e-3)
    assert speed.max() == pytest.approx(0.5)


def test_plain_csv_is_not_telemetry(tmp_path):
    csvFile = tmp_path / "plain.csv"
    csvFile.write_text("Time;Value\n2026-10-22T10:00:00Z;1\n")
    assert type(formats.create_file_mgr(str(csvFile))) is CsvFileMgr
