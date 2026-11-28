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

"""DataframeToHdf5: one index (timestamp) dataset per group for the parameters which have the very same one."""
import h5py
import numpy as np
import pandas as pd
import pytest

from testsuit.datatools import DataframeToHdf5 as d2h
from testsuit.datatools.DataframeToHdf5 import DataframeToHdf5


def param(name, index, values=None, index_name="Time"):
    index = pd.Index(np.asarray(index), name=index_name)
    df = pd.DataFrame({name: np.arange(len(index), dtype=float) if values is None else values}, index=index)
    df.name = name
    return df


def refs(path, group="g"):
    with h5py.File(path, "r") as f:
        return {k: f[group][k].attrs["TimestampRef"] for k in f[group] if "TimestampRef" in f[group][k].attrs}


def read(path, name, group="g"):
    with h5py.File(path, "r") as f:
        ds = f[group][name]
        return f[ds.attrs["TimestampRef"]][()], ds[()]


def test_same_timestamps_shared_others_apart(tmp_path):
    path = tmp_path / "out.h5"
    t = np.linspace(0, 10, 1001)
    DataframeToHdf5(str(path), "g", param("a", t))
    DataframeToHdf5(str(path), "g", param("b", t.copy(), index_name="/other/Time"))   # same values, another name
    DataframeToHdf5(str(path), "g", param("c", t + 1e-9))                            # same name, other values
    DataframeToHdf5(str(path), "g", param("d", t.astype(np.float32)))                # same values, other type
    DataframeToHdf5(str(path), "g", param("e", t[:-1]))                              # shorter
    r = refs(path)
    assert r["a"] == r["b"] == "g/Time"
    assert len({r["a"], r["c"], r["d"], r["e"]}) == 4 and r["c"] == "g/Time_2"
    with h5py.File(path, "r") as f:
        assert f["g/Time"].attrs["IndexUsers"] == 2 and f["g/Time_2"].attrs["IndexUsers"] == 1
        assert sorted(f["g"].attrs["IndexDatasets"]) == sorted({r["a"], r["c"], r["d"], r["e"]})
    for name, index in (("a", t), ("b", t), ("c", t + 1e-9), ("d", t.astype(np.float32)), ("e", t[:-1])):
        idx, values = read(path, name)
        assert np.array_equal(idx, index) and idx.dtype == index.dtype and len(values) == len(index)


def test_read_back_by_the_h5_loader(tmp_path):
    from testsuit.datatools.DataFileMgrs.H5FileMgr import H5FileMgr
    path = tmp_path / "out.h5"
    t = np.arange(50, dtype=float) * 0.1
    DataframeToHdf5(str(path), "g", param("a", t))
    DataframeToHdf5(str(path), "g", param("b", t, values=np.ones(50)))
    mgr = H5FileMgr(str(path))
    a, b = mgr.loadParams(["g/a", "g/b"], silent=True)
    assert np.array_equal(a.index.values, t) and np.array_equal(b.index.values, t)
    assert (b.iloc[:, 0] == 1).all()


def test_nan_and_strings_compared_by_value(tmp_path):
    path = tmp_path / "out.h5"
    t = np.array([0.0, np.nan, 2.0])
    DataframeToHdf5(str(path), "g", param("a", t))
    DataframeToHdf5(str(path), "g", param("b", t.copy()))
    assert refs(path)["a"] == refs(path)["b"]
    s = np.array(["x", "y", "z"], dtype=object)
    DataframeToHdf5(str(path), "s", param("a", s, index_name="Label"))
    DataframeToHdf5(str(path), "s", param("b", s.copy(), index_name="Label"))
    DataframeToHdf5(str(path), "s", param("c", np.array(["x", "y", "w"], dtype=object), index_name="Label"))
    r = refs(path, "s")
    assert r["a"] == r["b"] != r["c"]


def test_appends_keep_sharing_only_while_the_same(tmp_path):
    path = tmp_path / "out.h5"
    t1, t2 = np.arange(10.0), np.arange(10.0, 15.0)
    DataframeToHdf5(str(path), "g", param("a", t1))
    DataframeToHdf5(str(path), "g", param("b", t1))
    # a gets more rows: its index is shared, so it gets one of its own
    DataframeToHdf5(str(path), "g", param("a", t2))
    r = refs(path)
    assert r["a"] != r["b"]
    assert np.array_equal(read(path, "a")[0], np.r_[t1, t2]) and np.array_equal(read(path, "b")[0], t1)
    assert len(read(path, "a")[1]) == 15
    # b gets the same rows: it shares a's index again, the former one (no user left) is removed
    DataframeToHdf5(str(path), "g", param("b", t2))
    r2 = refs(path)
    assert r2["a"] == r2["b"] == r["a"]
    with h5py.File(path, "r") as f:
        assert r["b"] not in f and list(f["g"].attrs["IndexDatasets"]) == [r["a"]]
        assert f[r["a"]].attrs["IndexUsers"] == 2
    # one parameter alone on its index: extended in place
    DataframeToHdf5(str(path), "g", param("c", np.arange(3.0), index_name="T3"))
    DataframeToHdf5(str(path), "g", param("c", np.arange(3.0, 6.0), index_name="T3"))
    assert refs(path)["c"] == "g/T3" and np.array_equal(read(path, "c")[0], np.arange(6.0))
    # the index extended in place is compared again by its values (its former hash dropped)
    DataframeToHdf5(str(path), "g", param("d", np.arange(6.0), index_name="T6"))
    assert refs(path)["d"] == "g/T3"
    with pytest.raises(ValueError):
        DataframeToHdf5(str(path), "g", param("c", np.arange(1.0)), append=False)


def test_compared_by_chunks(tmp_path, monkeypatch):
    """Big data: the values are hashed and compared a chunk at a time (a chunk of 7 values here)."""
    monkeypatch.setattr(d2h, "COMPARE_CHUNK", 7)
    path = tmp_path / "out.h5"
    t = np.arange(100.0)
    other = t.copy()
    other[50] = -1   # differs only inside a chunk, first and last values the same
    DataframeToHdf5(str(path), "g", param("a", t))
    DataframeToHdf5(str(path), "g", param("b", other))
    DataframeToHdf5(str(path), "g", param("c", t.copy()))
    r = refs(path)
    assert r["a"] == r["c"] != r["b"]
    with h5py.File(path, "r") as f:
        segments = [(t, 0, 40), (t, 40, 100)]
        assert d2h._datasetEquals(f[r["a"]], segments) and not d2h._datasetEquals(f[r["b"]], segments)
        assert d2h._digest(segments) == d2h._digest([(f[r["a"]], 0, 100)])
        assert d2h._digest(segments) != d2h._digest([(f[r["b"]], 0, 100)])
