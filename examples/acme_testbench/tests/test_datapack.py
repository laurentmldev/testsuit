"""The acme-archive importer, alone and within the datapack tool."""
import shutil
import subprocess
import zipfile

import pytest

from testsuit.datatools.datapack.datapack_tools import datapack
from testsuit.datatools.datapack.importers import create_data_importer, get_data_importers
from testsuit.plugins import load_plugins

from acme_testbench.importer import AcmeArchiveImporter

from conftest import SAMPLE

STORE = SAMPLE / "datapack" / "archive_store"


@pytest.fixture
def sample_copy(tmp_path):
    """The sample datapack in a git work tree of its own (datapack reads the git state of the
    test definition and setup folders)."""
    shutil.copytree(SAMPLE / "datapack", tmp_path / "sample" / "datapack")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def test_registered():
    load_plugins()
    assert "acme-archive" in get_data_importers()
    assert type(create_data_importer("ACME-Archive", "t", "s", "v1")) is AcmeArchiveImporter


@pytest.mark.parametrize("archive", ["folder", "zip"])
def test_import_and_check(tmp_path, archive):
    store = tmp_path / "store"
    if archive == "folder":
        shutil.copytree(STORE / "v1.2", store / "v1.2")
    else:
        store.mkdir()
        with zipfile.ZipFile(store / "v1.2.zip", "w") as z:
            for f in (STORE / "v1.2").iterdir():
                z.write(f, f.name)

    target = tmp_path / "repo" / "bench_calib" / "v1.2"
    importer = AcmeArchiveImporter(str(target), str(store), "v1.2")
    assert importer.retrieve()

    dico = (target / "dataset.dico").read_text()
    assert "bench_calib.version=v1.2" in dico
    assert "bench_calib.pressure_gain=1.012" in dico
    assert importer.checkVersion("v1.2")
    assert not importer.checkVersion("v1.3")

    (target / "calibration.yml").write_text("tampered")
    assert importer.getChanges() == ["M calibration.yml"]
    assert not importer.checkVersion("v1.2")


def test_missing_version(tmp_path):
    assert not AcmeArchiveImporter(str(tmp_path / "x" / "v9"), str(STORE), "v9").retrieve()


def test_sample_datapack(sample_copy):
    testdef = sample_copy / "sample" / "datapack" / "testdef" / "acme_testdef.yml"
    assert datapack(str(testdef), nocheck=True)

    generated = testdef.parent / ".current_datapack" / "bench_a" / "input"
    config = (generated / "bench_config.yml").read_text()
    assert "version: v1.2" in config and "pressure_gain: 1.012" in config
    assert (generated / "calibration.yml").read_text() == (STORE / "v1.2" / "calibration.yml").read_text()
    assert len(list((sample_copy / ".work" / "datapacks").glob("acme_acceptance_acme_bench_*.zip"))) == 1

    # second run: the dataset is already there, its version is checked instead
    assert datapack(str(testdef), nocheck=True)
