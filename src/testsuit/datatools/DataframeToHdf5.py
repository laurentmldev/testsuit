
from __future__ import annotations

import h5py,re
import numpy as np
import pandas as pd

from testsuit.misc.logger import get_logger

from testsuit.datatools import datatoolbox

DEFAULT_TIMESTAMP_PATH="/Timestamps/timestamp"

def _isTimeStampDType(dtype: np.dtype) -> bool:
    return dtype==np.datetime64 or dtype==np.dtype('datetime64[ns]')

def _appendValuesToDataset(dataSerie: np.ndarray | pd.Series | pd.Index, 
                           h5dDatasSet: h5py.Dataset) -> None:
    prevLength = h5dDatasSet.len()
    newLength = prevLength + dataSerie.size
    h5dDatasSet.resize(newLength, axis=0)

    arr = np.asarray(dataSerie)

    # interpret datetime64 as uint64 into HDF5
    if _isTimeStampDType(arr.dtype):
        arr = arr.astype(np.uint64)

    h5dDatasSet[prevLength:newLength] = arr

def normalizeH5Path(name: str) -> str:
    return re.sub(r'[.,;:\[\]()\s]',"_",name)

def DataframeToHdf5(h5fileName: str,
                    h5GroupName: str,
                    df: pd.DataFrame | pd.Series,
                    comment: str | dict = "",
                    chunk_size: bool = True,
                    append: bool = True) -> bool:
    """Write provided dataframe in given HDF5 file within given group).

        If df has an "enum_mapping" attrribute, then it is used to store it properly inside HDF5 file.
        
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
        if h5GroupName not in h5f.keys():
            grp = h5f.create_group(h5GroupName)
            if hasattr(df,'origin'):                
                grp.attrs["Origin"]=str(df.origin)
                    
            if isinstance(comment,dict):
                for key in comment.keys():
                    grp.attrs[key]=str(comment[key])
            else:
                grp.attrs["Comment"]=str(comment)
            

        # update index fields
        indexpath=None
        if hasattr(df,"index"):
            indexpath=df.index.name

        if not indexpath:
            indexpath=DEFAULT_TIMESTAMP_PATH
            
        indexFullPath=h5GroupName+"/"+normalizeH5Path(indexpath)
        datasetFullPath=h5GroupName+"/"+normalizeH5Path(datatoolbox.getDfName(df))

        # brand new dataset
        if datasetFullPath not in h5f:
            
            # create timestamp from dataframe index (so that we can ref them later)
            if indexFullPath not in h5f:
                h5f.create_dataset(indexFullPath, data=df.index.values, chunks=chunk_size, maxshape=(None,))

            try:
                values = df.to_numpy()

                # Check for enum mapping (from flags extraction)
                enum_mapping = getattr(df, 'enum_mapping', None)

                if enum_mapping is not None:
                    # Create standard HDF5 enum type (h5py 3.x compatible)
                    enum_type = h5py.enum_dtype(enum_mapping,basetype="i")              
                    ds = h5f.create_dataset(datasetFullPath, data=values.astype(np.int32), chunks=chunk_size, maxshape=df.shape, dtype=enum_type)
                elif values.dtype == np.object_:
                    # ensure every element is a Python str (handles NaN, None, etc.)
                    values = np.array([str(x) for x in values.ravel()], dtype=object).reshape(values.shape)
                    ds = h5f.create_dataset(datasetFullPath, data=values, chunks=chunk_size, maxshape=df.shape, dtype=h5py.string_dtype())
                else:
                    ds = h5f.create_dataset(datasetFullPath, data=values, chunks=chunk_size, maxshape=df.shape)
                ds.attrs["TimestampRef"] = indexFullPath
                
                for key in df.attrs:
                    ds.attrs[key]=str(df.attrs[key])
                
            except Exception as e:                
                get_logger().error(f"unable to write data to HDF5: {e}\n{df.values}\n{df.dtypes}")
                raise e

            return True
        
        if append==False:
            raise ValueError("writing data to existing dataset '"+datasetFullPath+"' (caller disabled 'append' option): "+h5fileName)

        # update (extend) existing dataset
        if indexFullPath not in h5f:
            get_logger().error("no index dataset found to update "+datasetFullPath+": "+indexFullPath)
            return False
        
        # retrieve refs to our H5 objects
        try:
            _appendValuesToDataset(df.index, h5f[indexFullPath])
            _appendValuesToDataset(df.values,h5f[datasetFullPath])
        except Exception as e:
            raise ValueError("unable to write data into HDF5 file for parameter '"+datasetFullPath+"' : "+str(e))
        return True
