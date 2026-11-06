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

"""Registry of the data importers the datapack tool can use for a datasource.

A datasource of a datapack setup file names its importer (``importer: git``). The importer
fetches each dataset of the datasource into a local folder and checks its version afterwards.
It is created by a factory ``factory(targetDir, remotePath, versionId) -> ADataImporter``:

- ``targetDir``: local folder of the dataset (where to import it)
- ``remotePath``: the datasource ``source`` (a URL, a server, a folder...)
- ``versionId``: the dataset ``version`` (a branch, a tag, a version...)

The datapack tool provides the keys ``<dataset id>.path`` (the local folder) and
``<dataset id>.version``. After ``retrieve()``, the dataset folder may also hold a ``dataset.yml``
file (written by the importer or by the dataset ``postprocess`` processors): its keys are then
usable in the datapack files too.

Example, from an external library::

    from testsuit.datatools.datapack.importers import register_data_importer

    register_data_importer("myserver", MyServerImporter)   # datasource 'importer: myserver'

See testsuit.plugins to make this registration visible to the ``datapack`` command.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from testsuit.datatools.datapack.ADataImporter import ADataImporter

ImporterFactory = Callable[[str, str, str], "ADataImporter"]

# by lowercase name: importer names are case-insensitive in setup files
_importers: dict[str, ImporterFactory] = {}


def register_data_importer(name: str, factory: ImporterFactory) -> None:
    """Declare a data importer, usable as ``importer: <name>`` in datapack setup files.

    :param name: importer name, case-insensitive (registering a name again replaces that importer,
        built-in ones included)
    :param factory: ``factory(targetDir, remotePath, versionId)`` returning the importer, typically
        an ADataImporter subclass
    """
    _importers[name.lower()] = factory


def unregister_data_importer(name: str) -> None:
    """Remove an importer registered with register_data_importer()."""
    _importers.pop(name.lower(), None)


def get_data_importers() -> list[str]:
    """Names of the registered importers."""
    return list(_importers)


def sort_versions(versions: list[str]) -> list[str]:
    """Versions newest first, numbers compared as numbers (v1.10.0 after v1.9.2)."""
    import re
    def key(v: str):
        return [(1, int(part), "") if part.isdigit() else (0, 0, part) for part in re.split(r"(\d+)", v)]
    return sorted(set(versions), key=key, reverse=True)


def create_data_importer(name: str, targetDir: str, remotePath: str, versionId: str) -> ADataImporter:
    """Create the importer of a dataset.

    :raises KeyError: if no importer is registered under that name
    """
    factory = _importers.get(name.lower())
    if factory is None:
        raise KeyError(f"unknown datasource importer '{name}' (known: {', '.join(sorted(_importers))})")
    return factory(targetDir, remotePath, versionId)


########################## built-in importers ##########################

def _gitFactory(targetDir: str, remotePath: str, versionId: str) -> ADataImporter:
    from testsuit.datatools.datapack.GitImporter import GitImporter
    return GitImporter(targetDir, url=remotePath, branch=versionId)


register_data_importer("git", _gitFactory)
