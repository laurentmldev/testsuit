
import argparse
import sys,os,re


from testsuit.exploit.mexploit.mexploit import mexploit
import testsuit.misc.logger
from testsuit import misc

# clearer messages in HTML report (pytest syscap)
logConfig=misc.logger.DEFAULT_CONFIG_CONSOLE_ONLY
logConfig["consoleFormat"]="%(message)s"
misc.logger.create_logger("mexploit",config=logConfig)

## check if the given file is accessible
def _isInputReadable(f):
    if not os.access(f,os.R_OK):
        misc.logger.get_logger().error("{0} does not exist or is not reachable".format(f))
        sys.exit(1)

    return f

# override the parsing error message using logger
class _HelpParser(argparse.ArgumentParser):
    def error(self, message):
        misc.logger.get_logger().error("Input Arguments Error : "+message)
        sys.exit(1)

    
## the main function
def main():
    parser = _HelpParser(description=
    """ Run mexploit (Master eXPloit) analysis using given configurations.
    
    Note: any argument not listed hereunder will be forwarded to pytest. Run pytest -h for further details.
    
    Another note: this tool is called by exploit_runner.py to execute M-Exploit analysis as a separate process.

    
    Return 1 if something went wrong, 0 otherwise
    """,
    formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('scenario_folder', help="folder containing criteria to be run",type=_isInputReadable)
    parser.add_argument('data_folders', nargs='+', help="folder(s) containing data files to analyse (HDF5 typically)",type=_isInputReadable)
    parser.add_argument('-o',"--output_folder", default="./mexploit_result", help="Path where to store analysis results and logs")
    parser.add_argument('-t',"--title", help="Title of the analysis")
    parser.add_argument('-d',"--debug",action='store_true', default=False, help="Show debug messages")
    parser.add_argument('-f',"--force",action='store_true', default=False, help="Remove existing results folder")
    parser.add_argument("--minDate",nargs="?", help="Keep only data after given minDate. Example='2027/06/11 12:32:45.012'")
    parser.add_argument("--maxDate",nargs="?", help="Keep only data before given maxDate. Example='2027/06/11 12:34:49.654'")
    parser.add_argument("--shiftDateSec",nargs="?", help="Shift dates of loaded params from given amount of seconds")
    parser.add_argument("--shiftDateRegex",nargs="?", help="Apply 'shiftDateSec' only to params matching given <file_name_regex>::<param_name_regex>")
    args, unknownargs = parser.parse_known_args()

    if not args.title:
        args.title=os.path.basename(args.scenario_folder)

    rst = mexploit(args.scenario_folder, args.data_folders,args.output_folder,force=args.force,debug=args.debug, pytestargs=unknownargs,
                                                                                                minDate=args.minDate,maxDate=args.maxDate,
                                                                                                shiftDateSec=args.shiftDateSec,shiftDateRegex=args.shiftDateRegex)

    if rst!=0:
        print("\nERROR: M-Exploit analysis failed")

        with open(args.output_folder+os.sep+"mexploit.log","r") as f:
            for line in f.readlines():
                if re.search(r"(fail|FAIL|error|ERROR)",line):
                    print(line)

    sys.exit(rst)


if __name__ == "__main__":
    main()
