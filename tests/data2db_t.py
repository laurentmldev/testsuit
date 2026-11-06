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

import pytest

from testsuit.datatools.data2db import _confDateToNanosec


def test_conf_date_accepts_date_string_and_epoch_seconds():
    assert _confDateToNanosec({"minDate": "2017-12-16 03:02:35"}, "minDate") == 1513393355e9
    assert _confDateToNanosec({"minDate": "1513393355.5"}, "minDate") == 1513393355.5e9


def test_conf_date_missing_or_empty_is_none():
    assert _confDateToNanosec({}, "minDate") is None
    assert _confDateToNanosec({"minDate": ""}, "minDate") is None


def test_conf_date_invalid_names_the_key():
    with pytest.raises(ValueError, match="'maxDate'.*'bad'"):
        _confDateToNanosec({"maxDate": "bad"}, "maxDate")
