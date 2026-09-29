#!/usr/bin/python

### inspired from https://github.com/NeurodataWithoutBorders/diff/blob/master/ndiff.py

from __future__ import annotations

import h5py
import sys
import numpy as np
import pandas as pd

from datatools.DataFileMgrs.H5FileMgr import H5FileMgr

# load attributes
def read_attributes(hval: h5py.Group | h5py.Dataset) -> dict:
    attr = {}
    for k in hval.attrs:
        attr[k] = type(hval.attrs[k])
    return attr

# returns summary of group. 
# the only element for comparison here is the group's attributes
def read_group(hval: h5py.Group) -> dict:
    desc = {}
    desc["attr"] = read_attributes(hval)
    desc["htype"] = "group"
    return desc

# returns summary of dataset
# the only elements for comparison here are the dataset's attributes,
#   and the dataset dtype
def read_data(hval: h5py.Dataset) -> dict:
    desc = {}
    desc["attr"] = read_attributes(hval)
    desc["htype"] = "dataset"
    desc["dtype"] = type(hval)
    desc["shape"] = hval.shape
    desc["dataset"] = hval
    return desc

# creates and returns a summary description for every element in a group
def evaluate_group(path: str, grp: h5py.Group) -> dict | None:
    desc = {}
    for k, v in grp.items():
        if isinstance(v, h5py.Dataset):
            desc[k] = read_data(v)
        elif isinstance(v, h5py.Group):
            desc[k] = read_group(v)
        else:
           print("Unknown h5py type: %s (%s -- %s)"% (type(v), path, k))
           return None
    return desc

def diff_dataset(name: str,
                 df1: pd.DataFrame | pd.Series,
                 df2: pd.DataFrame | pd.Series) -> bool:
    rst=True    
    #print(df1)
    #print(df2)
             
    if not isinstance(df1.index, pd.Index):
        if not df1.dtypes.equals(df2.dtypes):
            print(f"** Different types for '{name}' (DIFF_VALUES)** \n {df1.dtypes}\n {df2.dtypes}")
            rst=False
        if not df1.index.equals(df2.index):
            print(f"** Different index for '{name}' (DIFF_VALUES)** \n {df1.index}\n {df2.index}")
            if isinstance(df1,(pd.DataFrame,pd.Series)):
                diff_dataset(name+".index",df1.index,df2.index)
            else:
                print(f"{df1.index} != {df2.index}")
            rst=False
        if not list(df1.columns) == list(df2.columns):
            print(f"** Different cols for '{name}' (DIFF_VALUES)** \n {df1.columns}\n {df2.columns}")
            rst=False       
        if not np.allclose(df1.values, df2.values, equal_nan=True):
            print(f"**Values not all close for '{name}' (DIFF_VALUES)**")
            rst=False  
    if not df1.equals(df2):
        print("** Different contents for '"+name+"' (DIFF_VALUES)**")
        rst=False
    return rst

def diff_groups(f1: H5FileMgr,
                grp1: h5py.Group,
                f2: H5FileMgr,
                grp2: h5py.Group,
                path: str,
                rst: bool = True) -> bool:

    print("------------------------------")
    print("Examining "+path)
    desc1 = evaluate_group(path, grp1)
    desc2 = evaluate_group(path, grp2)
    file1 = f1.getFileName()
    file2 = f2.getFileName()

    
    if not desc1:
        print("** Could not read Element '"+path+"' in '"+grp1+"' **")
        rst=False
    if not desc2:
        print("** Could not read Element '"+path+"' in '"+grp2+"' **")
        rst=False

    common = []
    for k in desc1:
        if k in desc2:
            common.append(k)
        else:
            print("** Element '"+k+"' only in '"+file1+"' (DIFF_UNIQUE_A)**")
            rst=False
    for k in desc2:
        if k not in desc1:
            print("** Element '"+k+"' only in '"+file2+"' (DIFF_UNIQUE_B)**")
            rst=False
    for i in range(len(common)):
        name = common[i]
        print("\t"+name)
        # compare types
        h1 = desc1[name]["htype"]
        h2 = desc2[name]["htype"]
        if h1 != h2:
            print("**  Different element types: '"+h1+"' and '"+h2+"' (DIFF_OBJECTS)")
            rst=False
            continue    # different hdf5 types -- don't try to compare further
        if h1 != "dataset" and h1 != "group":
            print("WARNING: element is not a recognized type (%s) and isn't being evaluated") % h1
            rst=False
            continue
        # handle datasets first
        if desc1[name]["htype"] != "dataset":
            continue

        # compare data type and shape, and dates/values
        if desc1[name]["shape"] != desc2[name]["shape"]:
            s1 = desc1[name]["shape"]
            s2 = desc2[name]["shape"]
            print("** Different shapes: '"+str(s1)+"' and '"+str(s2)+"' (DIFF_SHAPE)**")
            rst=False
        if desc1[name]["dtype"] != desc2[name]["dtype"]:
            d1 = desc1[name]["dtype"]
            d2 = desc2[name]["dtype"]
            print("** Different dtypes: '"+d1+"' and '"+d2+"' (DIFF_DTYPE)**")
            rst=False
        
        if h1 == "dataset":
            df1 = f1.loadParams([desc1[name]["dataset"].name])[0]
            df2 = f2.loadParams([desc2[name]["dataset"].name])[0]
            rst=diff_dataset(name,df1,df2)
            if not rst:
                print(df1)
                print(df2)
        # compare attributes
        for k in desc1[name]["attr"]:
            if k not in desc2[name]["attr"]:
                print("** Attribute '"+k+"' only in '"+file1+"' (DIFF_UNIQ_ATTR_A)**")
                rst=False
        for k in desc2[name]["attr"]:
            if k not in desc1[name]["attr"]:
                print("** Attribute '"+k+"' only in '"+file2+"' (DIFF_UNIQ_ATTR_B)**")
                rst=False
        for k in desc1[name]["attr"]:
            if k in desc2[name]["attr"]:
                v = desc1[name]["attr"][k]
                v2 = desc2[name]["attr"][k]
                if v != v2:
                    print("** Attribute '"+k+"' has different type: '"+v+"' and '"+v2+"' (DIFF_ATTR_DTYPE)")
                    rst=False
    for i in range(len(common)):
        name = common[i]
        # compare types
        if desc1[name]["htype"] != desc2[name]["htype"]:
            continue    # problem already reported
        if desc1[name]["htype"] != "group":
            continue
        # compare attributes
        for k in desc1[name]["attr"]:
            if k not in desc2[name]["attr"]:
                print("** Attribute '"+k+"' only in '"+file1+"' (DIFF_UNIQ_ATTR_A)**")
                rst=False
        for k in desc2[name]["attr"]:
            if k not in desc1[name]["attr"]:
                print("** Attribute '"+k+"' only in '"+file2+"' (DIFF_UNIQ_ATTR_B)**")
                rst=False
        # recurse into subgroup
        return diff_groups(f1, grp1[name], f2, grp2[name], path+name+"/",rst)

    return rst

def diff_h5files_struct(file1: str, file2: str) -> bool:
    print("Comparing '"+file1+"' and '"+file2+"'")
    try:
        f1 = H5FileMgr(file1)
    except IOError:
        print("Unable to open file '%s'") % file1
        return False
    try:
        f2 = H5FileMgr(file2)
    except IOError:
        print("Unable to open file '%s'") % file2
        return False
    return diff_groups(f1, f1.getH5FileRoot(), f2, f2.getH5FileRoot(), "/")


