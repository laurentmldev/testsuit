import os
import uuid

import numpy as np
import pytest

from testsuit.datatools.DataFileMgrs import formats
from testsuit.datatools.DataFileMgrs.ClickhouseFileMgr import ClickhouseFileMgr
from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr
from testsuit.misc.MonitorProgress import MonitorProgress

# server used by the tests reading actual data (skipped if not reachable)
CLICKHOUSE_TEST_HOST = os.environ.get("CLICKHOUSE_TEST_HOST", "127.0.0.1")
CLICKHOUSE_TEST_PORT = int(os.environ.get("CLICKHOUSE_TEST_PORT", "9000"))

T0 = 1700000000.0


def test_clickhouse_format_registered():
    assert formats.find_file_format("a/b.clickhouse.yml").name == "clickhouse"
    assert formats.find_file_format("a/b.ClickHouse.yaml").name == "clickhouse"
    # a plain YAML file is not a data file
    assert formats.find_file_format("a/b.yml") is None


def test_clickhouse_config(tmp_path):
    conf = tmp_path / "db.clickhouse.yml"
    conf.write_text("url: http://chhost:9440\ndatabase: myDb\ntable: run_.*\ntimestampColumn: time\n")
    mgr = formats.create_file_mgr(str(conf))
    assert isinstance(mgr, ClickhouseFileMgr)
    assert (mgr._host, mgr._port, mgr._user, mgr._database, mgr._table, mgr._tsCol) == \
        ("chhost", 9440, "default", "myDb", "run_.*", "time")

    conf.write_text("host: other\ndatabase: myDb\ntable: t\n")
    mgr = ClickhouseFileMgr(str(conf), 0)
    assert (mgr._host, mgr._port, mgr._tsCol) == ("other", 9000, "ts")


@pytest.fixture(scope="module")
def clickhouse_db():
    """A temporary database with a wide table (as written by data2db) and a long table."""
    clickhouse_driver = pytest.importorskip("clickhouse_driver")
    client = clickhouse_driver.Client(CLICKHOUSE_TEST_HOST, port=CLICKHOUSE_TEST_PORT, connect_timeout=2)
    try:
        client.execute("SELECT 1")
    except Exception as e:
        pytest.skip(f"no ClickHouse server at {CLICKHOUSE_TEST_HOST}:{CLICKHOUSE_TEST_PORT}: {e}")

    database = "testsuit_utest_" + uuid.uuid4().hex[:8]
    client.execute(f"CREATE DATABASE {database}")
    from datetime import datetime, timezone

    def dt(sec):
        return datetime.fromtimestamp(sec, tz=timezone.utc)

    # wide: each param written with its own dates, other columns NULL
    client.execute(f"CREATE TABLE {database}.bench (ts DateTime64(6, 'UTC'), `group/temp` Nullable(Float64), "
                   "pressure Nullable(Float64)) ENGINE = MergeTree() ORDER BY ts")
    client.execute(f"INSERT INTO {database}.bench (ts, `group/temp`) VALUES",
                   [(dt(T0 + i), float(i)) for i in range(10)])
    client.execute(f"INSERT INTO {database}.bench (ts, pressure) VALUES",
                   [(dt(T0 + i + 0.5), 100.0 + i) for i in range(5)])
    # long: one row per sample
    client.execute(f"CREATE TABLE {database}.events (ts DateTime64(6, 'UTC'), name String, value Float64) "
                   "ENGINE = MergeTree() ORDER BY ts")
    client.execute(f"INSERT INTO {database}.events (ts, name, value) VALUES",
                   [(dt(T0 + i), "speed" if i % 2 else "torque", float(i)) for i in range(6)])
    yield database
    client.execute(f"DROP DATABASE {database}")
    client.disconnect()


def _scanner(tmp_path, database, extraConf="", **kwargs):
    conf = tmp_path / "db.clickhouse.yml"
    conf.write_text(f"host: {CLICKHOUSE_TEST_HOST}\nport: {CLICKHOUSE_TEST_PORT}\ndatabase: {database}\n{extraConf}")
    return FolderParamMgr(str(tmp_path), **kwargs)


def _find(scanner, regex):
    return scanner.findParams(regex, silent=True, monitorProgress=MonitorProgress(name="utest"))


def test_clickhouse_wide_table(tmp_path, clickhouse_db):
    scanner = _scanner(tmp_path, clickhouse_db, "table: bench\n")
    assert scanner.getFileMgrs()[0].getFieldNames() == ["bench.group/temp", "bench.pressure"]

    temp, = _find(scanner, "bench.group/temp")
    assert temp.name == "bench.group/temp"
    np.testing.assert_allclose(temp.index, T0 + np.arange(10))
    np.testing.assert_allclose(temp.iloc[:, 0], np.arange(10))

    # NULL rows of the other params are not part of a param
    pressure, = _find(scanner, "pressure")
    np.testing.assert_allclose(pressure.index, T0 + np.arange(5) + 0.5)


def test_clickhouse_dates(tmp_path, clickhouse_db):
    scanner = _scanner(tmp_path, clickhouse_db, "table: bench\n", minDateSec=T0 + 2, maxDateSec=T0 + 4)
    temp, = _find(scanner, "group/temp")
    np.testing.assert_allclose(temp.iloc[:, 0], [2, 3, 4])

    # the min/max dates apply to the shifted dates
    scanner = _scanner(tmp_path, clickhouse_db, "table: bench\n", minDateSec=T0 + 2, maxDateSec=T0 + 4, shiftDateSec=1.0)
    temp, = _find(scanner, "group/temp")
    np.testing.assert_allclose(temp.index, T0 + np.array([2, 3, 4]))
    np.testing.assert_allclose(temp.iloc[:, 0], [1, 2, 3])


def test_clickhouse_long_table(tmp_path, clickhouse_db):
    scanner = _scanner(tmp_path, clickhouse_db, "table: ev.*\nparamNamesKey: name\n")
    assert scanner.getFileMgrs()[0].getFieldNames() == ["events.speed", "events.torque"]
    speed, = _find(scanner, "events.speed")
    np.testing.assert_allclose(speed.index, T0 + np.array([1, 3, 5]))
    np.testing.assert_allclose(speed.iloc[:, 0], [1, 3, 5])
