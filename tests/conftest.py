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


"""Shared by every test: the last known answers of servers kept in a folder of the test session (not the user's cache),
and no server taken for down from one test to the next."""
import pytest


@pytest.fixture(autouse=True)
def _servers(tmp_path_factory, monkeypatch):
    from testsuit import netguard
    monkeypatch.setenv("TSQ_CACHE", str(tmp_path_factory.mktemp("tsq_cache")))
    netguard.reset()
    yield
    netguard.reset()
