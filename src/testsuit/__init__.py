"""Toolbox for preparing and analysing test data.

Subpackages:
    datatools     load data files into pandas, convert to HDF5, push to a DB, datapacks
    exploit       mexploit YAML scenario checks and the exploit runner
    jupytertools  ipywidgets GUIs for notebooks
    misc          logging, progress reporting, file helpers
    cli           command-line entry points (see [project.scripts] in pyproject.toml)
"""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("testsuit")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0+unknown"
