"""The toytrain command."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from toytrain.cli import main

from conftest import PROJECT_ROOT, SAMPLE

DATA = str(SAMPLE / "data" / "max_speed")
OVERSPEED = str(PROJECT_ROOT / "tests" / "etc" / "overspeed")
STORE = str(PROJECT_ROOT / "datapack" / "release_store" / "toytrain-sw")


def test_info(capsys):
    assert main(["info", DATA]) == 0
    out = capsys.readouterr().out
    assert "propulsion.propj  [PropulsionJsonFileMgr]" in out
    assert "telemetry.csv  [TelemetryCsvFileMgr]" in out
    assert "unit: CALC-TM" in out
    assert "parameters: cmd_received, speed_sent_mps" in out


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


@pytest.mark.parametrize("data,args,rc,message", [
    (DATA, ["within_range", "^speed_mps", "min=0", "max=0.505"], 0, "OK [within_range::speed_mps]"),
    (DATA, ["settles", "^speed_mps", "target=0.5", "tolerance=0.005", "within_sec=15"], 0, "OK [settles::speed_mps]"),
    (DATA, ["max_slope", "^speed_mps", "max_per_sec=0.1"], 1, "FAILED [max_slope::speed_mps] slope 0.5"),
    (OVERSPEED, ["within_range", "^speed_mps", "max=0.505"], 1, "value(s) out of range"),
    (DATA, ["within_range", "_", "max=1"], 2, "must match exactly one parameter"),
    (DATA, ["assert_compare", "pilot_state"], 2, "no criterion 'assert_compare' registered by a plugin"),
    (DATA, ["max_slope", "pilot_state"], 2, "ERROR max_slope"),
])
def test_check(capsys, data, args, rc, message):
    assert main(["check", data] + args) == rc
    assert message in capsys.readouterr().out


def test_import(tmp_path, capsys):
    target = str(tmp_path / "toytrain_sw" / "v0.3.0")
    assert main(["import", STORE, "v0.3.0", target]) == 0
    assert (tmp_path / "toytrain_sw" / "v0.3.0" / "pilot" / "FIRMWARE.txt").is_file()
    # already there: checked, not imported again
    assert main(["import", STORE, "v0.3.0", target]) == 0
    assert main(["import", STORE, "v0.4.0", target]) == 1
    assert "NOT version v0.4.0" in capsys.readouterr().out
    shutil.rmtree(tmp_path / "toytrain_sw")
    assert main(["import", STORE, "v9", target]) == 1


def test_console_script():
    """Installed as a command by pyproject.toml's [project.scripts]."""
    exe = shutil.which("toytrain", path=str(Path(sys.executable).parent)) or shutil.which("toytrain")
    assert exe, "toytrain is not installed"
    proc = subprocess.run([exe, "--version"], capture_output=True, text=True, check=True)
    assert proc.stdout.startswith("toytrain ")
    proc = subprocess.run([sys.executable, "-m", "toytrain.cli", "info", OVERSPEED], capture_output=True, text=True)
    assert proc.returncode == 0 and "SimulatedFileMgr" in proc.stdout
