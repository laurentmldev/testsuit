import h5py
import numpy as np

from testsuit.datatools.DataFileMgrs.H5FileMgr import H5FileMgr


def test_object_reference_attribute_gives_the_index(tmp_path):
    """A dataset whose only object-reference attribute points to its timestamps is indexed by them."""
    path = tmp_path / "refs.h5"
    with h5py.File(path, "w") as f:
        ts = f.create_dataset("clock", data=np.array([10.0, 11.0, 12.0]))
        values = f.create_dataset("temperature", data=np.array([1.0, 2.0, 3.0]))
        values.attrs["axis"] = ts.ref  # attribute name has neither 'time' nor 'date'

    mgr = H5FileMgr(str(path))
    _value, index = mgr.getDataset("temperature")
    assert index is not None and index.name == "/clock"


def test_first_time_named_reference_wins(tmp_path):
    path = tmp_path / "refs2.h5"
    with h5py.File(path, "w") as f:
        t1 = f.create_dataset("t1", data=np.arange(3.0))
        t2 = f.create_dataset("t2", data=np.arange(3.0))
        values = f.create_dataset("v", data=np.arange(3.0))
        values.attrs["other"] = t2.ref
        values.attrs["time"] = t1.ref
        values.attrs["time_bis"] = t2.ref

    _value, index = H5FileMgr(str(path)).getDataset("v")
    assert index.name == "/t1"
