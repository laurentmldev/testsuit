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
