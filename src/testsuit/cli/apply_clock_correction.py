
import os
import argparse
import concurrent.futures
import sys


from testsuit.datatools.datatoolbox import loadDataframeFromFile
from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr
from testsuit.datatools.DataframeToHdf5 import DataframeToHdf5

from testsuit.misc.logger import get_logger
from testsuit.misc.logger import create_logger
from testsuit.misc.MonitorProgress import MonitorProgress,consoleRichProgressCb

CORRECTED_CLOCK_FILE_SUFFIX="clock_corrected"

NB_WORKERS=8

def _process_file_with_clock_correction(fpath, clock_correction_file, shiftDateSec, shiftDateRegex, monitorProgress, deleteIfExists=True):
    """Worker function for multithreaded clock correction processing."""
    monitorProgress.msg(msg=f"applying dates correction on file: {fpath}")

    if clock_correction_file:
        mgr = FolderParamMgr([fpath, clock_correction_file], shiftDateSec=shiftDateSec, shiftDateRegex=shiftDateRegex, shiftDateInverted=True,continueOnError=True)
    else:
        mgr = FolderParamMgr([fpath], shiftDateSec=shiftDateSec, shiftDateRegex=shiftDateRegex, shiftDateInverted=True,continueOnError=True)
    try:        
        dfs = mgr.findParams(os.path.basename(fpath) + "::", monitorProgress=monitorProgress)#,silent=True)        
    except Exception as e:
        import traceback
        traceback.print_exc()
        monitorProgress.msg(msg=f"could not apply clock correction to {fpath} : " + str(e), msgSeverity="error")
        return

    comment = {}
    comment["Comments"] = "Extracted from original data"

    if len(dfs) == 0:
        monitorProgress.msg(msg=f"No param in {os.path.basename(fpath)}", msgSeverity="warning")
        return

    targetFile = dfs[0].origin.replace(".h5", f".{CORRECTED_CLOCK_FILE_SUFFIX}.h5")
    if deleteIfExists and os.path.exists(targetFile):
        os.remove(targetFile)
        
    h5GroupName = os.path.basename(dfs[0].origin)
    for df in dfs:
        if "clock corrected" in df.attrs:
            DataframeToHdf5(targetFile, h5GroupName, df, comment=comment, chunk_size=True)

    monitorProgress.msg(msg=f"written {targetFile}")
    
def apply_clock_correction(target_folder, 
                           shiftDateSec, shiftDateRegex, shiftDateSrcFile, shiftDateInverted=False, 
                           monitorProgress=None,deleteIfExist=True):
    """
    Apply given clock correction (either constant litteral or a param name to be loaded.
    Actual processing is planned to be applicable on-the-fly, and thus is implemented in
    FolderParamMgr::findParams() and AFileMgr::finalizeParam() methods."""
     
    monitorProgress.msg(msg=[f"correcting clock for {shiftDateRegex}",
                    f"clock correction from {shiftDateSec} in {shiftDateSrcFile} "])
    
    
    # try to interpret provided shitDateSec as a float litteral, if not, it is considered as a param name
    try: shiftDateSec=float(shiftDateSec)
    except: pass
            
    clock_correction_file=None
    
    totalSteps=3 if isinstance(shiftDateSec,str) else 2
    
    monitorProgress.set_total_items(totalSteps)
    subFindMatchingDfmP = monitorProgress.child("find matching params")
    
    # early check of availability of provided shitDateSec param name
    # if not str, it is a float litteral constant correction value
    if isinstance(shiftDateSec,str):
        
        clock_correction_file = shiftDateSrcFile
        if not os.path.exists(clock_correction_file) and not clock_correction_file.startswith(os.sep):
            clock_correction_file = os.path.join(target_folder, shiftDateSrcFile)
            monitorProgress.msg(msg=f"Using file {clock_correction_file} to get clock correction info '{shiftDateSec}'.",
                    msgSeverity="info")
        if not os.path.exists(clock_correction_file):
            monitorProgress.msg(msg=f"File {clock_correction_file} not reachable. Cannot apply clock correction '{shiftDateSec}'.",
                        msgSeverity="error")
            return False
        
        shiftdatefilemgr = FolderParamMgr(clock_correction_file)
        shiftDateMatchingParams = shiftdatefilemgr.findParams(shiftDateSec, dryRun=True, silent=True, monitorProgress=subFindMatchingDfmP)
        shiftDateParamNames = [df.name for df in shiftDateMatchingParams]
        if len(shiftDateParamNames) == 0:
            monitorProgress.msg(msg=f"No param matching shiftDateSec='{shiftDateSec}' not found in {clock_correction_file}.",
                        msgSeverity="error")
            return False        
        if len(shiftDateParamNames) > 1:
            monitorProgress.msg(msg=f"more than one param matching shiftDateSec='{shiftDateSec}' in {clock_correction_file}: {shiftDateParamNames}",
                    msgSeverity="error")
            return False 
   
    subLoadMatchingDfmP = monitorProgress.child("load matching params")
    
    # list all params matching given regex, to identify corresponding files
    matching_dfs = loadDataframeFromFile(sourceFolderOrFile=target_folder, paramRegexes=shiftDateRegex, 
                                         indices=None, dryRun=True, silent=True, monitorProgress=subLoadMatchingDfmP)
    if len(matching_dfs) == 0:
        monitorProgress.msg(msg=["found no data needing clock correction", f"pattern: {shiftDateRegex}"], msgSeverity="warning")
        return False
        
    files_to_process = list(set(df.origin for df in matching_dfs if (df.origin and CORRECTED_CLOCK_FILE_SUFFIX not in df.origin)))
    subApplyClockmP = monitorProgress.child("apply clock correction",len(files_to_process))
    
    # Parallelize file processing with multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=NB_WORKERS) as executor:
        futures = {
            executor.submit(
                _process_file_with_clock_correction,
                fpath,
                clock_correction_file,
                shiftDateSec,
                shiftDateRegex,
                subApplyClockmP.child(fpath),
                deleteIfExist
            ): fpath
            for fpath in files_to_process
        }
        for future in concurrent.futures.as_completed(futures):
            fpath = futures[future]
            try:
                future.result()
            except Exception as e:
                monitorProgress.msg(msg=f"unexpected error processing {fpath}: {e}", msgSeverity="error")
    return True


 
def main():
    parser = argparse.ArgumentParser(
        description="Apply clock correction to extracted data files."
    )
    parser.add_argument('target_folder', help="Path to the folder containing the extracted data files")
    parser.add_argument('--shiftDateSec', help="Float value, or name of param containing clock diff to apply at each timestamp.", default="/ParamClockRef$")
    parser.add_argument('--shiftDateRegex', help="Filter which params/files to apply date shift", default="ref_.*::")
    parser.add_argument('--shiftDateInverted', action='store_true', default=False,  help="Inverse logic if your clock diff is based on the other clock")
    parser.add_argument('--update', action='store_true', default=False,  help="Update existing file if any, rather than deleting it. (useful to add manually a new param in a file)")
    parser.add_argument('--shiftDateSrcFile', help="Name of file containing the shiftDateSec if it is a param name", default="ref_data.h5")
    
    args = parser.parse_args()
    
    
    create_logger("apply_clock_correction")
    
    monitorProgress=MonitorProgress(name="apply_clock_correction",progressCb=consoleRichProgressCb)
        
    rst = apply_clock_correction(args.target_folder, args.shiftDateSec, args.shiftDateRegex, args.shiftDateSrcFile, 
                        shiftDateInverted=args.shiftDateInverted,monitorProgress=monitorProgress, deleteIfExist=not args.update)
    if not rst:
        get_logger().error(f"failed to fix clock based on {args.shiftDateSec}.")
        sys.exit(1)


if __name__ == "__main__":
    main()
