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

import numpy as np
import pytest
from nptdms import TdmsWriter, ChannelObject, RootObject

from testsuit.datatools.datatoolbox import loadDataframeFromFile
from testsuit.misc.MonitorProgress import MonitorProgress, consoleSilentProgressCb


def _write_tdms(path, channel_props):
    root = RootObject(properties={"Date Created": np.datetime64("2024-01-02T03:04:05")})
    channels = [ChannelObject("Grp 1", "Temp (°C)", np.arange(5, dtype=float) * 1.5, properties=channel_props),
                ChannelObject("Grp 1", "Press-1.a", np.arange(5, dtype=float) + 10, properties=channel_props)]
    with TdmsWriter(str(path)) as writer:
        writer.write_segment([root] + channels)


def _load(path):
    monitorProgress = MonitorProgress(name="tdms_utest", progressCb=consoleSilentProgressCb)
    dfs = loadDataframeFromFile(str(path), ".*", monitorProgress=monitorProgress, silent=True)
    return {df.name: df for df in dfs}


@pytest.mark.parametrize(
    "channel_props,first_date_sec",
    [
        # absolute time = wf_start_time + wf_start_offset
        ({"wf_increment": 0.5, "wf_start_offset": 1.0, "wf_start_time": np.datetime64("2024-05-06T07:08:09")}, 1714979290.0),
        # no start time: falls back to the file 'Date Created' property
        ({"wf_increment": 0.5}, 1704164645.0),
    ],
)
def test_load_tdms(tmp_path, channel_props, first_date_sec):
    path = tmp_path / "sample.tdms"
    _write_tdms(path, channel_props)

    dfs = _load(path)

    assert sorted(dfs) == ["Grp_1___Press_1_a", "Grp_1___Temp__oC"]
    temp = dfs["Grp_1___Temp__oC"]
    assert list(temp.columns) == ["Grp_1___Temp__oC"]
    assert temp.index.tolist() == [first_date_sec + 0.5 * i for i in range(5)]
    assert temp.iloc[:, 0].tolist() == [0.0, 1.5, 3.0, 4.5, 6.0]
    assert dfs["Grp_1___Press_1_a"].iloc[:, 0].tolist() == [10.0, 11.0, 12.0, 13.0, 14.0]
