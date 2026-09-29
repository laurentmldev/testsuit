"""Compare the structure and values of two HDF5 files (used by the tests to check generated files).

Inspired from https://github.com/NeurodataWithoutBorders/diff/blob/master/ndiff.py
Differences are printed with a tag in parentheses (DIFF_UNIQUE_A, DIFF_SHAPE, DIFF_VALUES, ...).
"""

from __future__ import annotations

import h5py
import numpy as np
import pandas as pd

from testsuit.datatools.DataFileMgrs.H5FileMgr import H5FileMgr

def read_attributes(hval: h5py.Group | h5py.Dataset) -> dict[str, type]:
    """Type of each attribute of an HDF5 object (attribute values themselves are not compared)."""
    return {k: type(hval.attrs[k]) for k in hval.attrs}

def read_group(hval: h5py.Group) -> dict:
    """Summary of a group: only its attributes are compared."""
    return {"attr": read_attributes(hval), "htype": "group"}

def read_data(hval: h5py.Dataset) -> dict:
    """Summary of a dataset: attributes, type and shape, plus the dataset itself to compare values."""
    return {"attr": read_attributes(hval),
            "htype": "dataset",
            "dtype": type(hval),
            "shape": hval.shape,
            "dataset": hval}

def evaluate_group(path: str, grp: h5py.Group) -> dict | None:
    """Summary of every element of a group, by name. None if an element is neither a group nor a dataset."""
    desc = {}
    for k, v in grp.items():
        if isinstance(v, h5py.Dataset):
            desc[k] = read_data(v)
        elif isinstance(v, h5py.Group):
            desc[k] = read_group(v)
        else:
            print(f"Unknown h5py type: {type(v)} ({path} -- {k})")
            return None
    return desc

def diff_dataset(name: str,
                 df1: pd.DataFrame | pd.Series,
                 df2: pd.DataFrame | pd.Series) -> bool:
    """True if both parameters are equal (same index, columns, dtypes and values); prints what differs."""
    if df1.equals(df2):
        return True

    print("** Different contents for '"+name+"' (DIFF_VALUES)**")
    # detail the difference
    if not df1.index.equals(df2.index):
        print(f"** Different index for '{name}' (DIFF_VALUES)** \n {df1.index}\n {df2.index}")
    if isinstance(df1, pd.DataFrame) and isinstance(df2, pd.DataFrame):
        if not df1.dtypes.equals(df2.dtypes):
            print(f"** Different types for '{name}' (DIFF_VALUES)** \n {df1.dtypes}\n {df2.dtypes}")
        if list(df1.columns) != list(df2.columns):
            print(f"** Different cols for '{name}' (DIFF_VALUES)** \n {df1.columns}\n {df2.columns}")
    return False

def _diff_attributes(desc1: dict, desc2: dict, file1: str, file2: str) -> bool:
    """Compare attribute names and types of two element summaries. True if identical."""
    rst=True
    attr1, attr2 = desc1["attr"], desc2["attr"]
    for k in attr1:
        if k not in attr2:
            print("** Attribute '"+k+"' only in '"+file1+"' (DIFF_UNIQ_ATTR_A)**")
            rst=False
        elif attr1[k] != attr2[k]:
            print(f"** Attribute '{k}' has different type: '{attr1[k]}' and '{attr2[k]}' (DIFF_ATTR_DTYPE)")
            rst=False
    for k in attr2:
        if k not in attr1:
            print("** Attribute '"+k+"' only in '"+file2+"' (DIFF_UNIQ_ATTR_B)**")
            rst=False
    return rst

def diff_groups(f1: H5FileMgr,
                grp1: h5py.Group,
                f2: H5FileMgr,
                grp2: h5py.Group,
                path: str) -> bool:
    """Recursively compare two HDF5 groups: element names, types, shapes, attributes and dataset values.

    :return: True if no difference was found
    """
    print("------------------------------")
    print("Examining "+path)
    desc1 = evaluate_group(path, grp1)
    desc2 = evaluate_group(path, grp2)
    file1 = f1.getFileName()
    file2 = f2.getFileName()

    if desc1 is None or desc2 is None:
        print(f"** Could not read Element '{path}' in '{file1 if desc1 is None else file2}' **")
        return False

    rst=True
    common = [k for k in desc1 if k in desc2]
    for k in desc1:
        if k not in desc2:
            print("** Element '"+k+"' only in '"+file1+"' (DIFF_UNIQUE_A)**")
            rst=False
    for k in desc2:
        if k not in desc1:
            print("** Element '"+k+"' only in '"+file2+"' (DIFF_UNIQUE_B)**")
            rst=False

    for name in common:
        print("\t"+name)
        d1, d2 = desc1[name], desc2[name]
        if d1["htype"] != d2["htype"]:
            print("**  Different element types: '"+d1["htype"]+"' and '"+d2["htype"]+"' (DIFF_OBJECTS)")
            rst=False
            continue    # different hdf5 types -- don't try to compare further

        rst = _diff_attributes(d1, d2, file1, file2) and rst

        if d1["htype"] == "group":
            rst = diff_groups(f1, grp1[name], f2, grp2[name], path+name+"/") and rst
            continue

        # datasets: compare shape and type, then values
        if d1["shape"] != d2["shape"]:
            print(f"** Different shapes: '{d1['shape']}' and '{d2['shape']}' (DIFF_SHAPE)**")
            rst=False
        if d1["dtype"] != d2["dtype"]:
            print(f"** Different dtypes: '{d1['dtype']}' and '{d2['dtype']}' (DIFF_DTYPE)**")
            rst=False

        df1 = f1.loadParams([d1["dataset"].name])[0]
        df2 = f2.loadParams([d2["dataset"].name])[0]
        if not diff_dataset(name,df1,df2):
            print(df1)
            print(df2)
            rst=False

    return rst

def diff_h5files_struct(file1: str, file2: str) -> bool:
    """Compare two HDF5 files (see diff_groups). True if no difference was found."""
    print("Comparing '"+file1+"' and '"+file2+"'")
    try:
        f1 = H5FileMgr(file1)
    except OSError:
        print(f"Unable to open file '{file1}'")
        return False
    try:
        f2 = H5FileMgr(file2)
    except OSError:
        print(f"Unable to open file '{file2}'")
        return False
    return diff_groups(f1, f1.getH5FileRoot(), f2, f2.getH5FileRoot(), "/")
