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

"""Secrets given by reference (env:<VARIABLE>, file:<path>) in the database configs and data2db."""
import pytest
import yaml

from testsuit.credentials import CredentialError, resolve_secret


def test_resolve_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("T_SECRET", "from-env")
    monkeypatch.delenv("T_MISSING", raising=False)
    (tmp_path / "secret").write_text("from-file\n", encoding="utf-8")
    assert resolve_secret("env:T_SECRET") == "from-env"
    assert resolve_secret(f"file:{tmp_path / 'secret'}") == "from-file"
    assert resolve_secret("file:secret", tmp_path) == "from-file"
    assert resolve_secret("literal") == "literal" and resolve_secret(None) is None
    with pytest.raises(CredentialError, match="'T_MISSING' is not set"):
        resolve_secret("env:T_MISSING", what="token")
    with pytest.raises(CredentialError, match="is not readable"):
        resolve_secret("file:nope", tmp_path)


def test_database_configs_read_references(tmp_path, monkeypatch):
    from testsuit.datatools.DataFileMgrs.ClickhouseFileMgr import ClickhouseFileMgr
    from testsuit.datatools.DataFileMgrs.InfluxDbFileMgr import InfluxDbV3FileMgr
    monkeypatch.setenv("T_INFLUX", "influx-token")
    (tmp_path / "ch_password").write_text("ch-pass\n", encoding="utf-8")
    influx = tmp_path / "influx.yml"
    influx.write_text(yaml.safe_dump({"host": "http://influx", "token": "env:T_INFLUX", "database": "db", "table": "t"}))
    assert InfluxDbV3FileMgr(str(influx), 0)._token == "influx-token"
    ch = tmp_path / "ch.yml"
    ch.write_text(yaml.safe_dump({"host": "clickhouse", "password": "file:ch_password", "database": "db", "table": "t"}))
    assert ClickhouseFileMgr(str(ch), 0)._password == "ch-pass"
    ch.write_text(yaml.safe_dump({"host": "clickhouse", "password": "plain", "database": "db", "table": "t"}))
    assert ClickhouseFileMgr(str(ch), 0)._password == "plain"            # a literal still works
