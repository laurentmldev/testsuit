"""The acme-bench command."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from acme_testbench.cli import main

from conftest import SAMPLE

DATA = str(SAMPLE / "data")


def test_info(capsys):
    assert main(["info", DATA]) == 0
    out = capsys.readouterr().out
    assert "bench_run_01.acmej  [AcmeJsonFileMgr]" in out
    assert "drive_line.sim.yml  [SimulatedFileMgr]" in out
    assert "device: oil-skid-2" in out
    assert "parameters: BenchTemp_degC, BenchPressure_bar" in out


def test_info_no_data(tmp_path, capsys):
    assert main(["info", str(tmp_path)]) == 2
    assert "No file matching" in capsys.readouterr().out


def test_criteria(capsys):
    assert main(["criteria", "--templates"]) == 0
    out = capsys.readouterr().out
    for name in ("within_range", "max_slope", "settles", "no_dropout", "mean_close_to"):
        assert f"\n{name}: " in "\n" + out
    assert "type: max_slope" in out
    # testsuit's own criteria are not listed
    assert "assert_compare" not in out


@pytest.mark.parametrize("args,rc,message", [
    (["within_range", "BenchTemp_degC", "min=15", "max=40"], 0, "OK [within_range::BenchTemp_degC]"),
    (["settles", "OilLevel", "target=80", "tolerance=2", "within_sec=30"], 0, "OK [settles::OilLevel_pct]"),
    (["max_slope", "ShaftSpeed", "max_per_sec=10"], 1, "FAILED [max_slope::ShaftSpeed_rpm] slope 50/s"),
    (["within_range", "_", "max=1"], 2, "must match exactly one parameter"),
    (["assert_compare", "OpMode"], 2, "no criterion 'assert_compare' registered by a plugin"),
    (["max_slope", "OpMode"], 2, "ERROR max_slope"),
])
def test_check(capsys, args, rc, message):
    assert main(["check", DATA] + args) == rc
    assert message in capsys.readouterr().out


def test_import(tmp_path, capsys):
    target = str(tmp_path / "bench_calib" / "v1.2")
    store = str(SAMPLE / "datapack" / "archive_store")
    assert main(["import", store, "v1.2", target]) == 0
    assert (tmp_path / "bench_calib" / "v1.2" / "calibration.yml").is_file()
    # already there: checked, not imported again
    assert main(["import", store, "v1.2", target]) == 0
    assert main(["import", store, "v1.3", target]) == 1
    assert "NOT version v1.3" in capsys.readouterr().out
    shutil.rmtree(tmp_path / "bench_calib")
    assert main(["import", store, "v9", target]) == 1


def test_console_script():
    """Installed as a command by pyproject.toml's [project.scripts]."""
    exe = shutil.which("acme-bench", path=str(Path(sys.executable).parent)) or shutil.which("acme-bench")
    assert exe, "acme-bench is not installed"
    proc = subprocess.run([exe, "--version"], capture_output=True, text=True, check=True)
    assert proc.stdout.startswith("acme-bench ")
    proc = subprocess.run([sys.executable, "-m", "acme_testbench.cli", "info", DATA], capture_output=True, text=True)
    assert proc.returncode == 0 and "SimulatedFileMgr" in proc.stdout
