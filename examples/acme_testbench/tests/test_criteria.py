"""The bench criteria, called directly on computed parameters."""
import sys

import numpy as np
import pandas as pd
import pytest

import testsuit.exploit.mexploit.helpers  # noqa: F401  (creates the computed_params module)
from testsuit.exploit.mexploit import registry
from testsuit.misc.logger import create_logger
from testsuit.plugins import load_plugins

from acme_testbench import checks, criteria

cp = sys.modules["computed_params"]


@pytest.fixture(autouse=True)
def _registered():
    load_plugins()
    # criteria log through testsuit's logger, created by mexploit in a real run
    create_logger("acme_testbench_tests", reset=True)


def check(critType, values, index=None, **conf):
    """Run criterion critType on values, as a scenario entry using 'computed_param: signal'."""
    cp.signal = pd.Series(values, index=index if index is not None else np.arange(len(values), dtype="float64"))
    try:
        registry.get_criterion(critType)({"name": "test", "type": critType, "computed_param": "signal", **conf})
    finally:
        del cp.signal


def test_registered_names():
    assert registry.get_criterion("within_range") is criteria.crit_within_range
    assert registry.get_criterion("max_slope") is criteria.crit_max_slope
    assert registry.get_criterion("settles") is criteria.crit_settles
    assert registry.get_criterion("no_dropout") is checks.crit_acme_no_dropout
    assert registry.get_criterion("mean_close_to") is checks.crit_mean_close_to


def test_within_range():
    check("within_range", [1, 2, 3], min=1, max=3)
    check("within_range", [1, 2, 3], max=3)
    with pytest.raises(AssertionError, match="1 value\\(s\\) out of range, first one 5 at 2.000s"):
        check("within_range", [1, 2, 5], min=0, max=3)


def test_within_range_time_window():
    check("within_range", [9, 1, 2, 9], min=0, max=3, from_sec=1, to_sec=2)


def test_max_slope():
    check("max_slope", [0, 1, 2], max_per_sec=1)
    with pytest.raises(AssertionError, match="slope 4/s"):
        check("max_slope", [0, 1, 3], index=[0.0, 1.0, 1.5], max_per_sec=3)


def test_settles():
    check("settles", [0, 5, 9, 10, 10.5, 10], target=10, tolerance=1, within_sec=3)
    with pytest.raises(AssertionError, match="settled after 4.000s"):
        check("settles", [0, 5, 7, 8, 10, 10], target=10, tolerance=1, within_sec=3)
    with pytest.raises(AssertionError, match="last value 5"):
        check("settles", [10, 10, 5], target=10, tolerance=1, within_sec=3)


def test_no_dropout():
    check("no_dropout", [1, 1, 1], index=[0.0, 0.5, 1.0], max_gap_sec=0.5)
    with pytest.raises(AssertionError, match="2.000s without data"):
        check("no_dropout", [1, 1, 1], index=[0.0, 0.5, 2.5], max_gap_sec=0.5)


def test_mean_close_to():
    check("mean_close_to", [2.4, 2.6], expected=2.5, tolerance=0.05)
    with pytest.raises(AssertionError, match="mean 3"):
        check("mean_close_to", [3, 3], expected=2.5, tolerance=0.1)


def test_needs_a_signal():
    with pytest.raises(KeyError, match="'param' or 'computed_param' is required"):
        criteria.crit_within_range({"name": "test", "type": "within_range", "max": 1})
