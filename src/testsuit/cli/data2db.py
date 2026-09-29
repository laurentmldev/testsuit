
import argparse,os,sys,json,logging


from testsuit.datatools.datatoolbox import SUPPORTED_DATAFILE_EXTENSIONS
from testsuit.plugins import load_plugins
from testsuit.datatools.DataFileMgrs.AFileMgr import enable_pandas_display_helpers
from testsuit.misc.MonitorProgress import MonitorProgress,consoleRichProgressCb
from testsuit.datatools.data2db import create_data2db
from testsuit.misc.logger import create_logger,get_logger


create_logger("data2db")

## check if the given file is accessible
def isInputReadable(f):
    if not os.access(f,os.R_OK):
        get_logger().error(f"{f} does not exist or is not reachable")
        sys.exit(1)

    return f

# override the parsing error message using logger
class HelpParser(argparse.ArgumentParser):
    def error(self, message):
        get_logger().error("Input Arguments Error : "+message)
        sys.exit(1)

## the main function
def main():
    # external formats (entry points, TESTSUIT_PLUGINS) show up in --extensions help
    load_plugins()
    enable_pandas_display_helpers()
    parser = HelpParser(description=
    """Extract given parameters from data files or DB.
    
    To extract into a db, you must provide a json file as follow:
        { 
            "params" : ["No2"],
            "indices" : [],
            "db" : {
                "dbtype" : "influxdb",
                "url" : "http://influxdb:8086", 
                "org" : "_sandbox_",
                "bucket" : "testBucket", 
                "measurement" :"testMeas",
                "rename_params_re" : ".*([^/]+)$",
                "dateUnit" : "s"
            }
        }

    Return 1 if something went wrong, 0 otherwise
    """,
    formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('sourceFolderOrFile', metavar="FolderOrFile", help="source folder or file  where to scan data files",type=isInputReadable)
    parser.add_argument('confJson', help="Conf for DB parameters. See up there of details about expected contents",type=isInputReadable)
    parser.add_argument('-t','--token',help="DB password or token")
    parser.add_argument("--test",action='store_true', default=False, help="Dry-run: does not actually inject data")
    parser.add_argument('-d',"--debug",action='store_true', default=False, help="Show debug messages")
    parser.add_argument('--extensions',type=lambda s: s.split(","),metavar='ext1,ext2,...',
                        help="File extensions to use as input (default: "+",".join(SUPPORTED_DATAFILE_EXTENSIONS)+")")
    args = parser.parse_args()

    if args.debug:
        get_logger().setLevel(logging.DEBUG)

    conf=None
    with open(args.confJson) as f:
        conf=json.load(f)           
        
    monitorProgress=MonitorProgress(progressCb=consoleRichProgressCb,name="data2db")
    dbHandler = create_data2db(conf["db"]["dbtype"])
    rst = dbHandler.data2db(args.sourceFolderOrFile,conf,args.extensions,token=args.token,dryRun=args.test,monitorProgress=monitorProgress,silent=True)

    if rst == True:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
