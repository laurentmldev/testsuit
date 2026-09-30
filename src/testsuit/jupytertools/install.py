"""Copy the starter notebooks shipped with testsuit into a working folder, and optionally open Jupyter there.

The notebooks only call the GUIs installed with the package (``runGUI()``), so updating testsuit with pip
updates the GUIs; the notebooks themselves rarely need to be copied again.
"""
from __future__ import annotations

import shutil
import subprocess
from importlib import resources
from pathlib import Path

NOTEBOOKS_PACKAGE = "testsuit.jupytertools.notebooks"


def list_notebooks() -> list[str]:
    """Names of the starter notebooks shipped with the package (ex: 'data_plot.ipynb')."""
    return sorted(f.name for f in resources.files(NOTEBOOKS_PACKAGE).iterdir() if f.name.endswith(".ipynb"))


def install_notebooks(target_dir: str | Path = ".", names: list[str] | None = None,
                      overwrite: bool = False) -> list[Path]:
    """Copy starter notebooks into target_dir (created if needed).

    :param target_dir: folder receiving the notebooks
    :param names: notebooks to copy, with or without '.ipynb' (default: all of list_notebooks())
    :param overwrite: replace notebooks already present; otherwise they are kept as they are
    :return: paths of the notebooks actually written
    :raises ValueError: if a requested name is not a shipped notebook
    """
    available = list_notebooks()
    wanted = available if names is None else [n if n.endswith(".ipynb") else n + ".ipynb" for n in names]
    unknown = sorted(set(wanted) - set(available))
    if unknown:
        raise ValueError(f"unknown notebook(s) {unknown}, available: {available}")

    target = Path(target_dir).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name in wanted:
        dest = target / name
        if dest.exists() and not overwrite:
            continue
        with resources.as_file(resources.files(NOTEBOOKS_PACKAGE) / name) as src:
            shutil.copyfile(src, dest)
        written.append(dest)
    return written


def open_jupyter(target_dir: str | Path) -> int:
    """Start the classic Jupyter notebook UI in target_dir and wait for it to stop.

    The GUIs add and run cells through the classic notebook JavaScript API, which JupyterLab and
    Notebook 7 do not provide, hence 'jupyter nbclassic' (installed by the 'jupyter' extra).
    """
    return subprocess.call(["jupyter", "nbclassic", f"--notebook-dir={Path(target_dir).expanduser()}"])
