import importlib
import json

import pytest

from testsuit.cli.notebooks import main
from testsuit.jupytertools.install import install_notebooks, list_notebooks


def test_shipped_notebooks_call_existing_guis():
    names = list_notebooks()
    assert names == ["data_plot.ipynb", "h5_convert.ipynb", "mexploit.ipynb"]
    from importlib import resources
    for name in names:
        nb = json.loads((resources.files("testsuit.jupytertools.notebooks") / name).read_text())
        code = "".join(nb["cells"][0]["source"])
        module = code.split("from testsuit.jupytertools.")[1].split(" import")[0]
        # the module file exists (importing it needs ipywidgets, not installed in CI)
        assert importlib.util.find_spec("testsuit.jupytertools." + module) is not None
        assert "runGUI()" in code


def test_install_keeps_existing_unless_overwrite(tmp_path):
    written = install_notebooks(tmp_path)
    assert sorted(p.name for p in written) == list_notebooks()
    (tmp_path / "data_plot.ipynb").write_text("mine")
    assert install_notebooks(tmp_path) == []
    assert (tmp_path / "data_plot.ipynb").read_text() == "mine"
    assert [p.name for p in install_notebooks(tmp_path, ["data_plot"], overwrite=True)] == ["data_plot.ipynb"]
    assert (tmp_path / "data_plot.ipynb").read_text() != "mine"


def test_cli_copies_selected_notebook(tmp_path, capsys):
    assert main([str(tmp_path / "work"), "-n", "mexploit"]) == 0
    assert [p.name for p in (tmp_path / "work").iterdir()] == ["mexploit.ipynb"]
    with pytest.raises(SystemExit):
        main([str(tmp_path), "-n", "nope"])
