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
