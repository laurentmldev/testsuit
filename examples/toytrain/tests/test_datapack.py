"""The toytrain-release importer, alone and within the datapack tool building the sample datapacks."""
import shutil
import subprocess
import zipfile

import pytest
import yaml

from testsuit.datatools.datapack.datapack_tools import datapack, verify_datapack
from testsuit.datatools.datapack.importers import create_data_importer, get_data_importers
from testsuit.plugins import load_plugins

from toytrain.importer import ReleaseImporter

from conftest import PROJECT_ROOT

STORE = PROJECT_ROOT / "datapack" / "release_store" / "toytrain-sw"


@pytest.fixture
def sample_copy(tmp_path):
    """The sample datapack folder in a git work tree of its own (datapack reads the git state of
    the test definition and setup folders)."""
    shutil.copytree(PROJECT_ROOT / "datapack", tmp_path / "datapack")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def test_registered():
    load_plugins()
    assert "toytrain-release" in get_data_importers()
    assert type(create_data_importer("Toytrain-Release", "t", "s", "v1")) is ReleaseImporter


@pytest.mark.parametrize("archive", ["folder", "zip"])
def test_import_and_check(tmp_path, archive):
    store = tmp_path / "store"
    if archive == "folder":
        shutil.copytree(STORE / "v0.3.0", store / "v0.3.0")
    else:
        store.mkdir()
        with zipfile.ZipFile(store / "v0.3.0.zip", "w") as z:
            for f in (STORE / "v0.3.0").rglob("*"):
                z.write(f, f.relative_to(STORE / "v0.3.0").as_posix())

    target = tmp_path / "repo" / "toytrain_sw" / "v0.3.0"
    importer = ReleaseImporter(str(target), str(store), "v0.3.0")
    assert importer.retrieve()

    keys = yaml.safe_load((target / "dataset.yml").read_text())
    assert keys["toytrain_sw"]["version"] == "v0.3.0"
    assert keys["toytrain_sw"]["pilot_version"] == "toytrain-pilot 0.3.0"
    assert importer.checkVersion("v0.3.0")
    assert not importer.checkVersion("v0.4.0")

    (target / "pilot" / "FIRMWARE.txt").write_text("tampered")
    assert importer.getChanges() == ["M pilot/FIRMWARE.txt"]
    assert not importer.checkVersion("v0.3.0")


def test_missing_version(tmp_path):
    assert not ReleaseImporter(str(tmp_path / "x" / "v9"), str(STORE), "v9").retrieve()


@pytest.mark.parametrize("test", ["max_speed", "front_contact"])
def test_sample_datapack(sample_copy, test):
    testdef = sample_copy / "datapack" / "testdef" / test / "testdef.yml"
    zip_path = datapack(str(testdef), nocheck=True)
    assert verify_datapack(zip_path) == []
    assert list((sample_copy / ".work" / "datapacks").glob(f"toytrain_{test}_toytrain_setup_*.zip")) == [zip_path]

    pack = testdef.parent / ".current_datapack"
    pilot = yaml.safe_load((pack / "data" / "pilot" / "input" / "pilot.yml").read_text())
    assert pilot["speed_limit_mps"] == 0.5
    assert pilot["network"]["propulsion"] == {"address": "10.42.0.20", "port": 5000}
    assert (pack / "data" / "pilot" / "input" / "app" / "FIRMWARE.txt").is_file()
    events = yaml.safe_load((pack / "data" / "piezo" / "input" / "piezo_sim.yml").read_text())["events"]
    assert len(events) == (1 if test == "front_contact" else 0)
    criteria = pack / "data" / "analysis" / "input" / "scenario" / "toytrain" / test / f"{test}.mexploit.yml"
    assert criteria.is_file()

    # the sequencer fields are kept as written: commands and pipeline are rendered at run time
    definition = yaml.safe_load((pack / "definition" / "testdef.yml").read_text())
    assert definition["pipeline"]["phases"][0]["stages"] == ["train"]
    assert "{{ scenario.duration_s }}" in str(definition["pipeline"])
    setup = yaml.safe_load((pack / "definition" / "setup.yml").read_text())
    assert setup["component_types"]["pilot_app"]["extends"] == "arduino_unoq"
    assert setup["configurations"]["sim_bench"]["components"]["pilot"]["role"] == "pilot"

    # second run: the datasets are already there, their version is checked instead
    assert datapack(str(testdef), nocheck=True)


def test_emulated_datapack(sample_copy):
    """setup configuration 'emulated': the same components, on loopback addresses of the dev host"""
    testdef = sample_copy / "datapack" / "testdef" / "front_contact" / "testdef.yml"
    assert verify_datapack(datapack(str(testdef), nocheck=True, setup_config="emulated")) == []

    inputs = testdef.parent / ".current_datapack" / "data"
    manifest = yaml.safe_load((testdef.parent / ".current_datapack" / "datapack.yml").read_text())
    assert manifest["setup"]["configuration"] == "emulated"
    assert sorted(manifest["components"]) == ["acquisition", "analysis", "piezo", "pilot", "propulsion", "telemetry"]
    pilot = yaml.safe_load((inputs / "pilot" / "input" / "pilot.yml").read_text())
    assert pilot["network"]["local_address"] == "127.0.0.10"
    assert pilot["network"]["propulsion"] == {"address": "127.0.0.20", "port": 5000}
    assert pilot["network"]["contacts"] == {"group": "239.42.0.1", "port": 5002}   # unchanged
    assert yaml.safe_load((inputs / "piezo" / "input" / "piezo_sim.yml").read_text())["local_address"] == "127.0.0.30"
