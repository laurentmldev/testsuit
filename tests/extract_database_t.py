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

"""The Database form of the Extract page: the description files it writes per type of database, and the lists
of the servers (ADatabaseFileMgr) behind its fields, without servers (the listings are patched)."""
import stat

import pytest
import yaml

from testsuit.datatools.DataFileMgrs import formats
from testsuit.datatools.DataFileMgrs.ADatabaseFileMgr import ADatabaseFileMgr
from testsuit.datatools.DataFileMgrs.ClickhouseFileMgr import ClickhouseFileMgr
from testsuit.datatools.DataFileMgrs.InfluxDbFileMgr import InfluxDbV2FileMgr, InfluxDbV3FileMgr
from testsuit.exploit.editor.service import CriteriaError
from testsuit.exploit.editor.extract import DATABASES, ExtractJobs


def test_every_database_loader_lists():
    for cls in (InfluxDbV3FileMgr, InfluxDbV2FileMgr, ClickhouseFileMgr):
        assert issubclass(cls, ADatabaseFileMgr)
    # each type of the form is read by a database loader, and names fields that exist
    for kind, spec in DATABASES.items():
        assert formats.find_file_format("x." + spec["suffix"]).name == kind
        keys = {f["key"] for f in spec["fields"]}
        for needs in ("add", "databases", "tables"):
            assert set(spec[needs]) <= keys, (kind, needs)
        assert sorted(f["list"] for f in spec["fields"] if f.get("list")) == ["databases", "tables"]


def test_types_route_hides_suffix():
    types = ExtractJobs(None).database_types()["types"]
    assert [t["type"] for t in types] == list(DATABASES)
    assert all("suffix" not in t and t["fields"] for t in types)


@pytest.mark.parametrize("body, cls, expected", [
    ({"type": "influxdbV3", "host": "http://h:8181", "token": "t", "org": "o", "database": "db", "table": ""},
     InfluxDbV3FileMgr, {"host": "http://h:8181", "token": "t", "org": "o", "database": "db", "table": ".*"}),
    ({"type": "influxdbV2", "host": "http://h:8086", "token": "t", "org": "o", "bucket": "b", "measurement": "m"},
     InfluxDbV2FileMgr, {"host": "http://h:8086", "token": "t", "org": "o", "bucket": "b", "measurement": "m"}),
    ({"type": "clickhouse", "url": "chhost:9440", "user": "u", "password": "p", "database": "db", "table": "run_.*"},
     ClickhouseFileMgr, {"url": "chhost:9440", "user": "u", "password": "p", "database": "db", "table": "run_.*"}),
])
def test_database_file_per_type(body, cls, expected):
    jobs = ExtractJobs(None)
    path = jobs.database(body)["path"]
    assert yaml.safe_load(open(path)) == expected
    assert stat.S_IMODE(__import__("os").stat(path).st_mode) == 0o600
    mgr = formats.create_file_mgr(path)
    assert isinstance(mgr, cls)
    if cls is ClickhouseFileMgr:
        assert (mgr._host, mgr._port, mgr._user, mgr._password) == ("chhost", 9440, "u", "p")


def test_database_file_refusals():
    jobs = ExtractJobs(None)
    with pytest.raises(CriteriaError, match="unknown database type"):
        jobs.database({"type": "mongo"})
    with pytest.raises(CriteriaError, match="'Measurement' is needed"):
        jobs.database({"type": "influxdbV2", "host": "http://h", "org": "o", "bucket": "b"})
    with pytest.raises(CriteriaError, match="Host"):
        jobs.database({"type": "influxdbV2", "host": "h:8086", "org": "o", "bucket": "b", "measurement": "m"})
    with pytest.raises(CriteriaError, match="Host"):
        jobs.database({"type": "clickhouse", "url": "a b", "database": "db"})
    # in tsqd (a folder), a secret is given as it is: a reference would read the files of the server
    served = ExtractJobs(None, folder=__import__("pathlib").Path("."))
    with pytest.raises(CriteriaError, match="password itself"):
        served.database({"type": "clickhouse", "url": "h", "database": "db", "password": "file:/etc/x"})


def test_database_list(monkeypatch):
    seen = []
    monkeypatch.setattr(InfluxDbV2FileMgr, "listDatabases", lambda self: seen.append(self._org) or ["b1", "b2"])
    monkeypatch.setattr(ClickhouseFileMgr, "listTables", lambda self: seen.append(self._database) or ["t1"])
    jobs = ExtractJobs(None)
    assert jobs.database_list({"type": "influxdbV2", "host": "http://h", "what": "databases"}) == {"items": ["b1", "b2"]}
    assert jobs.database_list({"type": "clickhouse", "url": "h", "database": "db", "what": "tables"}) == {"items": ["t1"]}
    assert seen == [None, "db"]
    with pytest.raises(CriteriaError, match="'Database' is needed"):
        jobs.database_list({"type": "clickhouse", "url": "h", "what": "tables"})
    assert list(jobs.connections.iterdir()) == []      # the files of the listings are removed


def test_clickhouse_listings_queries(tmp_path, monkeypatch):
    conf = tmp_path / "db.clickhouse.yml"
    conf.write_text("host: h\n")
    mgr = ClickhouseFileMgr(str(conf), 0)
    queries = []

    def execute(query, params=None, columnar=False):
        queries.append((query, params))
        return [("INFORMATION_SCHEMA",), ("default",), ("runs",), ("system",)] if "databases" in query else [("a",), ("b",)]
    monkeypatch.setattr(mgr, "_execute", execute)
    monkeypatch.setattr(mgr, "_connect", lambda needsDatabase=True: queries.append(("connect", needsDatabase)))
    assert mgr.listDatabases() == ["default", "runs"]
    assert queries[0] == ("connect", False)        # no database needed to list them
    mgr._database = "runs"
    assert mgr.listTables() == ["a", "b"]
    assert queries[-1][1] == {"db": "runs"}


def test_influxdb_v2_listings(tmp_path, monkeypatch):
    conf = tmp_path / "db.influxdbV2.yml"
    conf.write_text("host: http://h:8086\norg: o\nbucket: b\"x\n")
    mgr = InfluxDbV2FileMgr(str(conf), 0)

    class Bucket:
        def __init__(self, name): self.name = name

    class BucketsApi:
        def find_buckets_iter(self, **kwargs):
            assert kwargs == {"org": "o"}
            return iter([Bucket("runs"), Bucket("_monitoring"), Bucket("_tasks"), Bucket("bench")])

    class Client:
        def buckets_api(self): return BucketsApi()
        def close(self): pass
    mgr._client = Client()
    assert mgr.listDatabases() == ["bench", "runs"]

    import pandas as pd
    queries = []
    monkeypatch.setattr(mgr, "_query_to_dataframe", lambda q: queries.append(q) or pd.DataFrame({"_value": ["m2", "m1"]}))
    assert mgr.listTables() == ["m1", "m2"]
    assert 'schema.measurements(bucket: "b\\"x")' in queries[0]
