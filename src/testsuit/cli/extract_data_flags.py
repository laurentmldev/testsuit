
import os,re
import argparse
import concurrent.futures
import sys
import pandas as pd
from functools import partial

import numpy as np
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


from testsuit.datatools.datatoolbox import SUPPORTED_DATAFILE_EXTENSIONS
from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr
from testsuit.datatools.DataframeToHdf5 import DataframeToHdf5

from testsuit.misc.logger import get_logger
from testsuit.misc.logger import create_logger
from testsuit.misc.MonitorProgress import MonitorProgress,consoleRichProgressCb



EXTENSION_REGEX = re.compile(r"\.(" + "|".join(SUPPORTED_DATAFILE_EXTENSIONS) + r")$", re.IGNORECASE)

FLAGS_FILE_SUFFIX="_flags.h5"

def cbExtractFlags(df, flags_info, monitorProgress):
    """Apply provided flags_info to this dataframe.
    
    return a list of DataFrames, one per flag, each with a single column."""

    if df is None or df.empty:
        return []
        
    if len(df.columns) > 1:
        raise Exception(f"extract flags only supports 1-column params. {df.name} has more: {df.columns}")

    col = df.columns[0]
    series = df[col]
    
    # Robust integer check (works for numpy dtypes and pandas extension types)
    is_int = getattr(series.dtype, 'kind', None) in ('i', 'u')
    
    if not is_int:
        def _to_uint(val):
            if pd.isna(val):
                return np.nan
            s = str(val).strip()
            if not s or s.lower() == 'nan':
                return np.nan
            if re.match(r'^[01]+$', s):
                return int(s, 2)
            return int(s)
        
        series = series.apply(_to_uint).astype(pd.UInt64Dtype())
    # Prepare for vectorized bitwise ops: NaN -> 0 temporarily, track mask
    has_na = series.isna()
    # .to_numpy() guarantees standard numpy int behavior for >> & operators
    series_clean = series.fillna(0).astype(np.uint64).to_numpy()
    
    if has_na.all():
        monitorProgress.msg(msg=f"{series.name}: ALL values are NaN", msgSeverity="warning")
    
    elif has_na.any(): 
        monitorProgress.msg(msg=f"{series.name} contains {len(has_na[has_na==True])}/{len(series)} NaN values:\n{series[has_na].pstr()}", msgSeverity="warning")
            
    
    results = []
    for bit_pos, flag_def in flags_info.items():
        name = flag_def.get('name')
        if name is None:
            desc = flag_def.get('description', '')
            name = re.sub(r'[^a-zA-Z0-9]+', '_', desc).strip('_').lower()
            
        values_map = flag_def.get('values')
        
        if '-' in str(bit_pos):
            # ── Multi-bit range extraction (VECTORIZED) ──
            low, high = map(int, str(bit_pos).split('-'))
            num_bits = high - low + 1
            mask = (1 << num_bits) - 1
            
            # Vectorized: shift right then mask
            extracted_int_arr = ((series_clean >> low) & mask)
            extracted_int = pd.Series(extracted_int_arr, index=df.index).where(~has_na)
            
            # Build binary strings for values_map lookup
            extracted = extracted_int.apply(
                lambda v, num_bits=num_bits: f'{v:0{num_bits}b}' if pd.notna(v) else np.nan
            )
            
            if values_map:
                # Lookup by binary string key first
                mapped = extracted.map(values_map)
                # Fill remaining NaN with int-key lookup
                remaining = mapped.isna() & extracted_int.notna()
                if remaining.any():
                    mapped[remaining] = extracted_int[remaining].map(values_map)
                # Fill remaining NaN with the binary string itself
                mapped = mapped.fillna(extracted)
                extracted = mapped
        else:
            # ── Single-bit extraction (VECTORIZED) ──
            pos = int(bit_pos)
            bit_val_arr = (series_clean >> pos) & 1
            bit_val = pd.Series(bit_val_arr, index=df.index).where(~has_na)
            
            if values_map:
                # Lookup by string key first, then int key
                str_mapped = bit_val.astype(str).map(values_map)
                remaining = str_mapped.isna() & bit_val.notna()
                if remaining.any():
                    str_mapped[remaining] = bit_val[remaining].map(values_map)
                # .fillna(0) avoids NaN->bool conversion error
                str_mapped = str_mapped.fillna(bit_val.fillna(0).astype("boolean"))
                extracted = str_mapped
            else:
                extracted = bit_val.fillna(0).astype("boolean")
                extracted[has_na] = pd.NA
        
        # ── Build result DataFrames (same structure as before) ──
        flag_df = pd.DataFrame({name: extracted}, index=df.index)
        if values_map is not None:
            flag_df.attrs["enum_mapping"] = values_map
        flag_df.name = os.path.basename(df.name) + "_" + name
        results.append(flag_df)
        
        if '-' in str(bit_pos):
            flag_df_int = pd.DataFrame({name+"_int": extracted_int}, index=df.index)
            if values_map is not None:
                flag_df_int.attrs["enum_mapping"] = values_map
            flag_df_int.name = os.path.basename(df.name) + "_" + name+"_int"
            results.append(flag_df_int)
    
    return results

def _process_file_with_flags_extraction(fpath, results_folder, flags_info, monitorProgress):
    """Worker function for multithreaded flags extraction processing."""
    
    monitorProgress.msg(msg=f"extracting flags from {fpath}")
    match = EXTENSION_REGEX.search(fpath)
    if not match:
        raise Exception(f"Unable to detect file extension: {fpath}")
    targetFile = fpath[:match.start()] + FLAGS_FILE_SUFFIX
    if results_folder is not None:
        targetFile=results_folder+os.sep+os.path.basename(targetFile)
    if os.path.exists(targetFile):
        os.remove(targetFile)
        
    monitorProgress.set_total_items(len(flags_info.keys()))
    for filePattern in flags_info.keys():
        if re.search(filePattern, fpath):      
            mgr = FolderParamMgr(fpath)
            for paramPattern in flags_info[filePattern]:
                subMp=monitorProgress.child(paramPattern)
                dfFlagsLists = mgr.findParams(paramPattern, 
                                                callback=partial(cbExtractFlags, 
                                                                    flags_info=flags_info[filePattern][paramPattern],
                                                                    monitorProgress=subMp
                                                                    ),
                                                silent=True,
                                                monitorProgress=subMp)
                # Flatten: each callback returns a list of DataFrames (one per flag)
                for dfFlags in dfFlagsLists:
                    for df in dfFlags:
                        df.origin = targetFile                                                
                        h5GroupName = os.path.basename(targetFile)
                        DataframeToHdf5(targetFile, h5GroupName, df, comment="flags extracted", chunk_size=True)
        else:
            monitorProgress.complete_n(1)
    monitorProgress.msg(msg=f"DONE written {targetFile}")
    
def extract_data_flags(target_files, flags_info_file, results_folder=None,
                       monitorProgress=None):
    """
    Create a new H5 file where bin flags in given data are interpreted as human-usable parameters.
    
    :param results_folder (str): where to store generate files. Default is same folder than source file.
    
    """

    monitorProgress.set_total_items(len(target_files))
    if not os.path.exists(flags_info_file):
        monitorProgress.msg(msg=f"File {flags_info_file} not reachable. Cannot get flags description.",
                    msgSeverity="error")
        return False
    
    yaml = YAML()
    try:
        with open(flags_info_file) as f:
            flags_info = yaml.load(f)
    except YAMLError as e:
        raise Exception(f"failed to parse flags_info '{flags_info_file}' YAML file: {e}")

    
    # Parallelize file processing with multithreading
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(
                _process_file_with_flags_extraction,
                fpath,
                results_folder,
                flags_info,
                monitorProgress.child(fpath),
            ): fpath
            for fpath in target_files
        }
        for future in concurrent.futures.as_completed(futures):
            fpath = futures[future]
            try:
                future.result()
            except Exception as e:      
                import traceback
                traceback.print_exc()          
                monitorProgress.msg(msg=f"unexpected error processing {fpath}: {e}", msgSeverity="error")
    return True


 
def main():
    parser = argparse.ArgumentParser(
        description="Extract flags values."
    )
    parser.add_argument('flags_info_file', help="Path to the flags description")
    parser.add_argument('target_files', nargs='+', help="Path to the folder containing the extracted data files")
    
    args = parser.parse_args()
    
    create_logger("extract_data_flags")
    
     # do not process flags file themselves generated during a previous run
     # (convenient when using 'xxx_*.h5' in console)
    filtered_target_files=[x for x in args.target_files if not x.endswith(FLAGS_FILE_SUFFIX)]

    monitorProgress=MonitorProgress(name="extract_data_flags",progressCb=consoleRichProgressCb, debug=False)
    
    rst = extract_data_flags(filtered_target_files, args.flags_info_file,monitorProgress=monitorProgress)
    if not rst:
        get_logger().error("failed to extract flags info.")
        sys.exit(1)


if __name__ == "__main__":
    main()
