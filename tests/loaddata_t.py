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


import sys,os,pytest,logging


from testsuit.cli.data2h5 import data2h5
from testsuit.cli.extract_data_flags import extract_data_flags

from testsuit.datatools.h5diff import diff_h5files_struct
from testsuit.datatools.datatoolbox import loadDataframeFromFile

from testsuit.misc.MonitorProgress import MonitorProgress,consoleProgressCb

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



def test_csv_params_read_together(monkeypatch):
    """The generic CSV loader reads the file once for all the parameters asked (not once per parameter),
    and each parameter is the same as when loaded alone."""
    import pandas as pd
    from testsuit.datatools.DataFileMgrs import formats
    from testsuit.datatools.DataFileMgrs.CsvFileMgr import CsvFileMgr
    from testsuit.misc.MonitorProgress import consoleSilentProgressCb

    path = os.path.join("tests", "tmp", "loaddata_test", "five_params.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("Timestamps;p1;p2;p3;p4;p5\n")
        for i in range(50):
            f.write(f"2026-11-18 09:30:{i:02d}.250;{i};{i * 0.5};{-i};{i % 3};{'' if i % 7 else 1.5}\n")
    mgr = formats.create_file_mgr(path, 0)
    assert type(mgr) is CsvFileMgr
    names = ["p5", "p1", "p3", "p2", "p4"]
    progress = lambda: MonitorProgress(name="t", progressCb=consoleSilentProgressCb)
    alone = [mgr.loadParams([n], monitorProgress=progress(), silent=True)[0] for n in names]
    reads = []
    real = pd.read_csv
    monkeypatch.setattr(pd, "read_csv", lambda *a, **k: reads.append(k.get("usecols")) or real(*a, **k))
    together = mgr.loadParams(names, monitorProgress=progress(), silent=True)
    assert len(reads) == 1 and len(reads[0]) == 6            # the index column and the 5 parameters
    for a, b in zip(alone, together):
        pd.testing.assert_frame_equal(a, b, check_exact=True)
        assert a.name == b.name and a.index.name == b.index.name
