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

"""The data file managers whose "file" is the description of a database (a YAML with the host, the credentials...)
instead of recorded data: what they can list of the server, to fill a connection form."""
from __future__ import annotations

import abc

from testsuit.datatools.DataFileMgrs.AFileMgr import AFileMgr


class ADatabaseFileMgr(AFileMgr):
    """A pseudo data file giving access to a database. On top of AFileMgr, it lists what the server holds, with
    only what the description has so far: the databases need the host and the credentials, the tables of a
    database need its name too."""

    @abc.abstractmethod
    def listDatabases(self) -> list[str]:
        """The names of the databases of the server (sorted)."""

    @abc.abstractmethod
    def listTables(self) -> list[str]:
        """The names of the tables of the database of the description, all of them (not only those its 'table'
        pattern selects), sorted."""
