"""The bench file formats, loaded through testsuit like its built-in ones."""
import shutil
from importlib.metadata import entry_points

import numpy as np
import pytest

from testsuit.datatools.DataFileMgrs import formats
from testsuit.datatools.DataFileMgrs.CsvFileMgr import CsvFileMgr, GetCsvFileType
from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr
from testsuit.misc.MonitorProgress import MonitorProgress

from acme_testbench.filemgrs import AcmeJsonFileMgr, AcmeLoggerCsvFileMgr, SimulatedFileMgr

from conftest import SAMPLE

DATA = SAMPLE / "data"
START = 1788256800.0  # 2026-09-01T10:00:00Z


def load(scanner, name):
    [param] = scanner.findParams(name, monitorProgress=MonitorProgress(name="test"), silent=True)
    return param.iloc[:, 0]


def test_plugin_declared_as_entry_point():
    assert "acme_testbench" in {ep.name for ep in entry_points(group="testsuit.plugins")}


def test_folder_scan_uses_bench_readers():
    # FolderParamMgr loads the plugins by itself
    mgrs = {m.getBaseName(): type(m) for m in FolderParamMgr(str(DATA)).getFileMgrs()}
    assert mgrs == {"bench_run_01.acmej": AcmeJsonFileMgr,
                    "drive_line.sim.yml": SimulatedFileMgr,
                    "oil_skid_01.csv": AcmeLoggerCsvFileMgr}


def test_acme_json():
    scanner = FolderParamMgr(str(DATA))
    temp = load(scanner, "BenchTemp_degC")
    assert len(temp) == 121
    assert temp.index[0] == START and temp.index[-1] == START + 60
    assert temp.iloc[0] == pytest.approx(21.0, abs=0.2)


def test_simulated_signals():
    scanner = FolderParamMgr(str(DATA))
    speed = load(scanner, "ShaftSpeed_rpm")
    assert len(speed) == 601
    assert speed.iloc[0] == 0 and speed.iloc[-1] == 3000
    mode = load(scanner, "OpMode")
    assert list(np.unique(mode)) == [0, 1, 2]
    assert mode.loc[START + 25] == 1


def test_simulated_is_deterministic(tmp_path):
    shutil.copy(DATA / "drive_line.sim.yml", tmp_path / "again.sim.yml")
    first = SimulatedFileMgr(str(DATA / "drive_line.sim.yml")).channels()["Vibration_g"]
    second = SimulatedFileMgr(str(tmp_path / "again.sim.yml")).channels()["Vibration_g"]
    assert first.equals(second)


def test_simulated_unknown_kind(tmp_path):
    simFile = tmp_path / "bad.sim.yml"
    simFile.write_text("start: 0\nduration_sec: 1\nrate_hz: 1\nsignals:\n  X: {kind: square}\n")
    with pytest.raises(ValueError, match="unknown simulated signal kind 'square'"):
        SimulatedFileMgr(str(simFile)).getFieldNames()


def test_acme_logger_csv():
    mgr = formats.create_file_mgr(str(DATA / "oil_skid_01.csv"))
    assert type(mgr) is AcmeLoggerCsvFileMgr
    assert GetCsvFileType(str(DATA / "oil_skid_01.csv")) == "acme-logger"
    assert mgr.getFieldNames() == ["Time", "OilFlow_lpm", "OilLevel_pct"]
    assert mgr.getNbEntries() == 241
    assert mgr.metadata() == {"version": "v1", "device": "oil-skid-2", "operator": "jdoe"}

    level = load(FolderParamMgr(str(DATA)), "OilLevel_pct")
    assert level.index[0] == START and level.index[1] == pytest.approx(START + 0.25, abs=1e-3)


def test_plain_csv_is_not_acme_logger(tmp_path):
    csvFile = tmp_path / "plain.csv"
    csvFile.write_text("Time;Value\n2026-09-01T10:00:00Z;1\n")
    assert type(formats.create_file_mgr(str(csvFile))) is CsvFileMgr
