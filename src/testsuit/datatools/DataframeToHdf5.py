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


from __future__ import annotations

import hashlib
import h5py,re
import numpy as np
import pandas as pd

from testsuit.misc.logger import get_logger

from testsuit.datatools import datatoolbox

DEFAULT_TIMESTAMP_PATH="/Timestamps/timestamp"
TIMESTAMP_REF_ATTR="TimestampRef"        # on a dataset: the path of its index (timestamp) dataset
TIMESTAMP_INDEXES_ATTR="IndexDatasets"   # on a group: the index datasets written in it, which a parameter may share
TIMESTAMP_USERS_ATTR="IndexUsers"    # on an index dataset: how many datasets refer to it
TIMESTAMP_DIGEST_ATTR="IndexDigest"  # on an index dataset (no "time" in these names: H5FileMgr takes such attributes for references): a hash of its type and values (written when needed)
COMPARE_CHUNK=1<<21                      # values compared at a time: 16 MB of float64 per side, whatever the size of the data

def _isTimeStampDType(dtype: np.dtype) -> bool:
    return dtype==np.datetime64 or dtype==np.dtype('datetime64[ns]')

def _asStored(values) -> np.ndarray:
    """The values as written in the file (datetime64 as uint64)."""
    arr = np.asarray(values)
    if _isTimeStampDType(arr.dtype):
        arr = arr.astype(np.uint64)
    return arr

def _appendValuesToDataset(dataSerie: np.ndarray | pd.Series | pd.Index, 
                           h5dDatasSet: h5py.Dataset) -> None:
    prevLength = h5dDatasSet.len()
    newLength = prevLength + dataSerie.size
    h5dDatasSet.resize(newLength, axis=0)
    h5dDatasSet[prevLength:newLength] = _asStored(dataSerie)

def normalizeH5Path(name: str) -> str:
    return re.sub(r'[.,;:\[\]()\s]',"_",name)


# ── index (timestamp) datasets shared by the parameters which have the very same one ──

def _segmentsLen(segments: list) -> int:
    return sum(stop-start for _src,start,stop in segments)

def _readSegments(segments: list, start: int, stop: int) -> np.ndarray:
    """Values [start:stop[ of the concatenation of the segments ((source, start, stop): an h5py dataset or an
    array, and the part of it)."""
    parts = []
    pos = 0
    for src,s0,s1 in segments:
        n = s1-s0
        a, b = max(start,pos), min(stop,pos+n)
        if a < b:
            parts.append(_asStored(src[s0+a-pos:s0+b-pos]))
        pos += n
    return parts[0] if len(parts)==1 else np.concatenate(parts)

def _sameValues(a: np.ndarray, b: np.ndarray) -> bool:
    if a.shape != b.shape:
        return False
    if a.dtype.kind in "OSU" or b.dtype.kind in "OSU":
        # strings: as read back (h5py gives bytes) against as written
        dec = lambda arr: np.array([x.decode() if isinstance(x,bytes) else str(x) for x in arr.ravel()], dtype=object)
        return bool((dec(a)==dec(b)).all())
    if a.dtype != b.dtype:
        return False   # same values in another type is another timestamp
    return bool(np.array_equal(a, b, equal_nan=a.dtype.kind in "fc"))

def _datasetEquals(ds: h5py.Dataset, segments: list) -> bool:
    """True when the 1-D dataset holds exactly the values of the segments: its length first, then its first
    and last values, then the whole by COMPARE_CHUNK values at a time (memory stays bounded however big the
    data). Never raises (unreadable, or not comparable: not the same)."""
    try:
        total = _segmentsLen(segments)
        if ds.ndim != 1 or ds.shape[0] != total:
            return False
        if total == 0:
            return True
        if not (_sameValues(np.asarray(ds[0:1]), _readSegments(segments,0,1))
                and _sameValues(np.asarray(ds[total-1:total]), _readSegments(segments,total-1,total))):
            return False
        for start in range(0, total, COMPARE_CHUNK):
            stop = min(total, start+COMPARE_CHUNK)
            if not _sameValues(np.asarray(ds[start:stop]), _readSegments(segments,start,stop)):
                return False
        return True
    except Exception:
        return False

def _digest(segments: list) -> str | None:
    """A hash (blake2b) of the type and values of the segments, read COMPARE_CHUNK values at a time; None for
    strings (compared value by value)."""
    total = _segmentsLen(segments)
    h = hashlib.blake2b(digest_size=20)
    for start in range(0, max(total,1), COMPARE_CHUNK):
        arr = _readSegments(segments, start, min(total, start+COMPARE_CHUNK)) if total else _asStored(segments[0][0][0:0])
        if arr.dtype.kind in "OSUV":
            return None
        if start == 0:
            h.update(arr.dtype.str.encode())
        h.update(np.ascontiguousarray(arr).tobytes())
    return h.hexdigest()

def _datasetDigest(ds: h5py.Dataset) -> str | None:
    """The digest of an index dataset: from its attribute, else computed (and kept in the attribute)."""
    if TIMESTAMP_DIGEST_ATTR in ds.attrs:
        return _attrStr(ds.attrs[TIMESTAMP_DIGEST_ATTR])
    digest = _digest([(ds,0,ds.shape[0])])
    if digest is not None:
        try:
            ds.attrs[TIMESTAMP_DIGEST_ATTR] = digest
        except Exception:
            pass
    return digest

def _attrStr(value) -> str:
    return value.decode() if isinstance(value,bytes) else str(value)

def _groupIndexes(grp: h5py.Group) -> list[str]:
    try:
        return [_attrStr(p) for p in grp.attrs.get(TIMESTAMP_INDEXES_ATTR, [])]
    except Exception:
        return []

def _setGroupIndexes(grp: h5py.Group, paths: list[str]) -> None:
    try:
        if paths:
            grp.attrs[TIMESTAMP_INDEXES_ATTR] = np.array(paths, dtype=h5py.string_dtype())
        elif TIMESTAMP_INDEXES_ATTR in grp.attrs:
            del grp.attrs[TIMESTAMP_INDEXES_ATTR]
    except Exception as e:  # too many indexes for an attribute: the next ones are not shared, that is all
        get_logger().warning(f"index datasets of {grp.name} not recorded for sharing: {e}")

def _users(ds: h5py.Dataset) -> int:
    """How many datasets refer to this index (1 for a file written before indexes were shared)."""
    return int(ds.attrs.get(TIMESTAMP_USERS_ATTR, 1))

def _addUser(h5f: h5py.File, grp: h5py.Group, path: str, delta: int) -> None:
    """Count one more (delta=1) or one less (-1) dataset referring to the index; one no dataset refers to
    any more is deleted."""
    ds = h5f[path]
    users = _users(ds)+delta
    if users <= 0:
        del h5f[path]
        _setGroupIndexes(grp, [p for p in _groupIndexes(grp) if p != path])
    else:
        ds.attrs[TIMESTAMP_USERS_ATTR] = users

def _findSharedIndex(h5f: h5py.File, grp: h5py.Group, segments: list, preferred: str, exclude: str | None=None) -> str | None:
    """An index dataset of the group holding exactly these values (the preferred path first)."""
    candidates = _groupIndexes(grp)
    if preferred in h5f and preferred not in candidates:
        candidates.insert(0, preferred)   # a file written before indexes were shared
    elif preferred in candidates:
        candidates.remove(preferred)
        candidates.insert(0, preferred)
    total = _segmentsLen(segments)
    digest = None
    for path in candidates:
        if path == exclude or path not in h5f:
            continue
        ds = h5f[path]
        if not isinstance(ds, h5py.Dataset) or ds.ndim != 1 or ds.shape[0] != total:
            continue
        try:
            # same length: the hashes first (each index is read once for its hash, then kept), the values
            # one by one only for strings
            digest = digest or _digest(segments) or "none"
            other = _datasetDigest(ds) if digest != "none" else None
        except Exception:
            other = None
        if other is not None:
            if other == digest:
                return path
        elif _datasetEquals(ds, segments):
            return path
    return None

def _newIndex(h5f: h5py.File, grp: h5py.Group, preferred: str, segments: list, chunk_size) -> str:
    """A new index dataset with the values of the segments, at the preferred path or, taken by another
    timestamp, at <preferred>_2, _3... Written COMPARE_CHUNK values at a time."""
    path = preferred
    n = 2
    while path in h5f:
        path = f"{preferred}_{n}"
        n += 1
    first = _readSegments(segments, 0, min(COMPARE_CHUNK, _segmentsLen(segments)))
    ds = h5f.create_dataset(path, data=first, chunks=chunk_size or True, maxshape=(None,))
    for start in range(len(first), _segmentsLen(segments), COMPARE_CHUNK):
        _appendValuesToDataset(_readSegments(segments, start, min(start+COMPARE_CHUNK, _segmentsLen(segments))), ds)
    ds.attrs[TIMESTAMP_USERS_ATTR] = 0
    digest = _digest(segments)
    if digest is not None:
        ds.attrs[TIMESTAMP_DIGEST_ATTR] = digest
    _setGroupIndexes(grp, _groupIndexes(grp)+[path])
    return path


def DataframeToHdf5(h5fileName: str,
                    h5GroupName: str,
                    df: pd.DataFrame | pd.Series,
                    comment: str | dict = "",
                    chunk_size: bool = True,
                    append: bool = True) -> bool:
    """Write provided dataframe in given HDF5 file within given group).

        If df has an "enum_mapping" attrribute, then it is used to store it properly inside HDF5 file.

        The index (timestamp) of the dataframe is written once per group for the parameters which have the very
        same one: a parameter whose index holds exactly the values (and type) of an index dataset already written
        in the group refers to it (its "TimestampRef" attribute) rather than writing its own. Otherwise its index
        is written at <group>/<index name> (default: /Timestamps/timestamp), or <...>_2, _3... when that path
        holds another timestamp. The comparison reads the datasets by chunks, so it holds for files of any size.
        Appending to a parameter whose index is shared keeps it shared only while the new values are the same
        for its other parameters (a parameter whose timestamps differ gets an index of its own).
        
        :param h5fileName (str): path and name of file to generate
        :param h5GroupName (str): name of group under which to create entries for provided Dataframe. '.' will be replaced by '_' if any.
        :param df (Pandas Dataframe): dataframe to dump as HDF5
        :param comment (str|map): 'comment' key in H5 file. if Map, key/value is dumped in H5 file.
    

        :returns: True if operation successful, False otherwise
    """

    with h5py.File(h5fileName, 'a') as h5f:

        if isinstance(df,pd.Series):
            df=df.to_frame()
 
        for col in df.columns:
            if df[col].dtype=='object':
                df[col]=df[col].astype(str)
                
        # normalizing h5group path
        h5GroupName=h5GroupName.replace('.',"_").replace('\\',"_")
        if not h5GroupName or len(h5GroupName)==0:
            h5GroupName="/"
        
        # when file is blank for our group, we instanciate all DataSets
        if h5GroupName not in h5f:
            grp = h5f.create_group(h5GroupName)
            if hasattr(df,'origin'):                
                grp.attrs["Origin"]=str(df.origin)
                    
            if isinstance(comment,dict):
                for key in comment.keys():
                    grp.attrs[key]=str(comment[key])
            else:
                grp.attrs["Comment"]=str(comment)
        grp = h5f[h5GroupName]

        # update index fields
        indexpath=None
        if hasattr(df,"index"):
            indexpath=df.index.name

        if not indexpath:
            indexpath=DEFAULT_TIMESTAMP_PATH
            
        indexFullPath=h5GroupName+"/"+normalizeH5Path(indexpath)
        datasetFullPath=h5GroupName+"/"+normalizeH5Path(datatoolbox.getDfName(df))
        newIndex=[(df.index,0,len(df.index))]

        # brand new dataset
        if datasetFullPath not in h5f:
            
            # the index: one of the group with the very same values, else a new one (so that we can ref them later)
            indexRef = _findSharedIndex(h5f, grp, newIndex, indexFullPath)
            if indexRef is None:
                indexRef = _newIndex(h5f, grp, indexFullPath, newIndex, chunk_size)

            try:
                values = df.to_numpy()
                maxshape = (None,)+values.shape[1:]   # appendable

                # Check for enum mapping (from flags extraction)
                enum_mapping = getattr(df, 'enum_mapping', None)

                if enum_mapping is not None:
                    # Create standard HDF5 enum type (h5py 3.x compatible)
                    enum_type = h5py.enum_dtype(enum_mapping,basetype="i")              
                    ds = h5f.create_dataset(datasetFullPath, data=values.astype(np.int32), chunks=chunk_size or True, maxshape=maxshape, dtype=enum_type)
                elif values.dtype == np.object_:
                    # ensure every element is a Python str (handles NaN, None, etc.)
                    values = np.array([str(x) for x in values.ravel()], dtype=object).reshape(values.shape)
                    ds = h5f.create_dataset(datasetFullPath, data=values, chunks=chunk_size or True, maxshape=maxshape, dtype=h5py.string_dtype())
                else:
                    ds = h5f.create_dataset(datasetFullPath, data=_asStored(values), chunks=chunk_size or True, maxshape=maxshape)
                ds.attrs[TIMESTAMP_REF_ATTR] = indexRef
                _addUser(h5f, grp, indexRef, 1)
                
                for key in df.attrs:
                    ds.attrs[key]=str(df.attrs[key])
                
            except Exception as e:                
                get_logger().error(f"unable to write data to HDF5: {e}\n{df.values}\n{df.dtypes}")
                if _users(h5f[indexRef]) <= 0:
                    _addUser(h5f, grp, indexRef, 0)   # a new index no dataset refers to: removed
                raise e

            return True
        
        if append==False:
            raise ValueError("writing data to existing dataset '"+datasetFullPath+"' (caller disabled 'append' option): "+h5fileName)

        # update (extend) existing dataset
        ds = h5f[datasetFullPath]
        indexRef = _attrStr(ds.attrs.get(TIMESTAMP_REF_ATTR, indexFullPath))
        if indexRef not in h5f:
            get_logger().error("no index dataset found to update "+datasetFullPath+": "+indexRef)
            return False

        try:
            index = h5f[indexRef]
            n, m = ds.shape[0], index.shape[0]
            if m < n:
                raise ValueError(f"its index {indexRef} has {m} values, for {n} values")
            extended = [(index,0,n)]+newIndex
            # an index of the group with the values the parameter will have (one of its siblings appended them)
            shared = _findSharedIndex(h5f, grp, extended, indexRef, exclude=indexRef)
            if shared is None and m == n and _users(index) <= 1:
                _appendValuesToDataset(df.index, index)   # its own index: extended in place
                if TIMESTAMP_DIGEST_ATTR in index.attrs:
                    del index.attrs[TIMESTAMP_DIGEST_ATTR]   # computed again when compared
            else:
                if shared is None:
                    # its index is shared, or longer already (its siblings got other values): one of its own
                    shared = (indexRef if m == n+len(df.index) and _datasetEquals(index, extended)
                              else _newIndex(h5f, grp, indexFullPath, extended, chunk_size))
                if shared != indexRef:
                    _addUser(h5f, grp, shared, 1)
                    ds.attrs[TIMESTAMP_REF_ATTR] = shared
                    _addUser(h5f, grp, indexRef, -1)
            _appendValuesToDataset(df.values, ds)
        except Exception as e:
            raise ValueError("unable to write data into HDF5 file for parameter '"+datasetFullPath+"' : "+str(e))
        return True
