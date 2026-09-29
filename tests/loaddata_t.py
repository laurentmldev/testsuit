
import sys,os,pytest,logging

# add deps folder (relative path to this module)
sys.path.append(os.path.realpath(os.path.dirname( __file__[:-1] if __file__.endswith('.pyc') else __file__ ) +os.sep+".."))

from scripts.data2h5 import data2h5
from scripts.extract_data_flags import extract_data_flags

from datatools.h5diff import diff_h5files_struct
from datatools.datatoolbox import loadDataframeFromFile

from misc.MonitorProgress import MonitorProgress,consoleProgressCb

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger()

@pytest.mark.parametrize(
    "input_file,params_list,expected_output",
    [
        ("csv/", "Param_1", "sample_csv.h5"),
        ("csv/test_date_at_the_end.csv", "testp1", "sample_csv_date_at_the_end.h5"),
        ("csv/channels_recording.csv", "param1.voltage", "channels_recording.h5"),    
        ("csv/influxdb.csv", "Param1.field2", "influxdb.h5"),
        # TODO add tests for other accepted formats
    ]
)

def test_data2h5(input_file,params_list,expected_output):

    tested_file="tests"+os.sep+"etc"+os.sep+"data"+os.sep+input_file
    rst_file="tests"+os.sep+"tmp"+os.sep+"loaddata_test"+os.sep+expected_output
    ref_file="tests"+os.sep+"ref"+os.sep+"loaddata_test"+os.sep+expected_output

    # clean existing rst file if any
    os.makedirs(os.path.dirname(rst_file), exist_ok=True)
    if os.path.exists(rst_file):
        os.remove(rst_file)

    log.info("Input file: "+tested_file)
    log.info("Result file: "+rst_file)
    log.info("Ref file: "+ref_file)

    monitorProgress=MonitorProgress(progressCb=consoleProgressCb,name="data2h5_utest")
    # extract param from source file
    rst, dfList = data2h5(sourceFolderOrFile=tested_file,paramRegexes=params_list,targetFile=rst_file,monitorProgress=monitorProgress)
    assert(rst)
    
    # ensure generated H5 file is as expected (ref file has been verified manually)    
    assert(diff_h5files_struct(ref_file, rst_file)==True)



@pytest.mark.parametrize(
    "input_file,flags_desc_file,expected_output",
    [
        ("csv/flags.csv", "etc/misc/flags_desc.yml", "flags_flags.h5"),
    ]
)

def test_data2h5_flags(input_file,flags_desc_file,expected_output):

    tested_file="tests"+os.sep+"etc"+os.sep+"data"+os.sep+input_file
    rst_file="tests"+os.sep+"tmp"+os.sep+"loaddata_test"+os.sep+expected_output
    ref_file="tests"+os.sep+"ref"+os.sep+"loaddata_test"+os.sep+expected_output
    flags_desc_file="tests"+os.sep+flags_desc_file

    # clean existing rst file if any
    os.makedirs(os.path.dirname(rst_file), exist_ok=True)
    if os.path.exists(rst_file):
        os.remove(rst_file)

    log.info("Input file: "+tested_file)
    log.info("Result file: "+rst_file)
    log.info("Ref file: "+ref_file)
    
    monitorProgress=MonitorProgress(progressCb=consoleProgressCb,name="extract_data_flags_utest")

    # extract param from source file
    rst = extract_data_flags([tested_file],flags_desc_file,results_folder=os.path.dirname(rst_file),monitorProgress=monitorProgress)
    assert(rst)
    
    # ensure generated H5 file is as expected (ref file has been verified manually)    
    # not ready yet
    assert(diff_h5files_struct(ref_file, rst_file)==True)

