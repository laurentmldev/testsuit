"""mexploit criteria of the ACME test bench, registered as a module.

The plugin calls ``register_criteria_module("acme_testbench.criteria")``: every ``crit_<type>``
function below becomes usable as ``type: <type>`` in scenarios, next to testsuit's own criteria::

    oil_temp_ok:
      type: within_range
      param: BenchTemp_degC          # or computed_param: <name of a computed_param entry>
      min: 15
      max: 40

A criterion receives the scenario entry as a dict (``critConf``, with its ``name`` and ``type``)
and fails the check by raising, usually with ``assert``. Parameters are loaded with testsuit's
``param()`` helper, the same one scenario expressions use.
"""
from __future__ import annotations

import sys
from typing import Any

import numpy as np
import pandas as pd

from testsuit.misc.logger import get_logger


def when(date: float) -> str:
    """A date for messages: dates are seconds since epoch, or since the time origin once a
    time_origin entry set one."""
    if abs(date) > 1e8:
        return pd.Timestamp(date, unit="s").strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return f"{date:.3f}s"


def signal(critConf: dict[str, Any]) -> pd.Series:
    """Values checked by a criterion: ``param: <regex>`` (a data file parameter) or
    ``computed_param: <name>`` (the result of an earlier computed_param entry), optionally
    restricted to ``from_sec``/``to_sec`` (dates, relative to the current time origin)."""
    if "param" in critConf:
        # imported on use: loading the plugin must not load the whole mexploit machinery
        from testsuit.exploit.mexploit.helpers import param
        data = param(str(critConf["param"]))
    elif "computed_param" in critConf:
        # module created by testsuit.exploit.mexploit.helpers, holding the computed params
        cp = sys.modules["computed_params"]
        data = getattr(cp, str(critConf["computed_param"]))
    else:
        raise KeyError(f"[{critConf.get('name')}] 'param' or 'computed_param' is required")

    if isinstance(data, pd.DataFrame):
        if data.shape[1] != 1:
            raise ValueError(f"[{critConf.get('name')}] expects a single-column parameter, got {list(data.columns)}")
        data = data.iloc[:, 0]
    data = data.dropna()
    data = data.loc[critConf.get("from_sec"):critConf.get("to_sec")]
    assert len(data) > 0, f"[{critConf.get('name')}] no value to check"
    return data


def crit_within_range(critConf: dict[str, Any]) -> None:
    """Every value is within [min, max] (either bound can be omitted).

<TEMPLATE>
bench_temp_in_range:
  type: within_range
  param: BenchTemp_degC
  min: 15
  max: 40
  # from_sec: 10
  # to_sec: 50
</TEMPLATE>
    """
    data = signal(critConf)
    lo, hi = critConf.get("min"), critConf.get("max")
    get_logger().info(f"{critConf['name']}: {len(data)} values in [{data.min():.6g}, {data.max():.6g}], "
                      f"expected in [{lo if lo is not None else '-inf'}, {hi if hi is not None else '+inf'}]")
    outside = pd.Series(False, index=data.index)
    if lo is not None:
        outside |= data < float(lo)
    if hi is not None:
        outside |= data > float(hi)
    assert not outside.any(), (
        f"CRIT_CHECK\n[within_range::{critConf['name']}] {int(outside.sum())} value(s) out of range, "
        f"first one {data[outside].iloc[0]:.6g} at {when(data[outside].index[0])}")


def crit_max_slope(critConf: dict[str, Any]) -> None:
    """The rate of change never exceeds ``max_per_sec`` (in absolute value).

<TEMPLATE>
speed_ramp_ok:
  type: max_slope
  param: ShaftSpeed_rpm
  max_per_sec: 60
</TEMPLATE>
    """
    data = signal(critConf)
    assert len(data) > 1, f"[max_slope::{critConf['name']}] needs at least 2 values"
    slopes = np.abs(np.diff(data.to_numpy()) / np.diff(data.index.to_numpy(dtype="float64")))
    worst = int(np.argmax(slopes))
    limit = float(critConf["max_per_sec"])
    get_logger().info(f"{critConf['name']}: max slope {slopes[worst]:.6g}/s at {when(data.index[worst])} (limit {limit:g}/s)")
    assert slopes[worst] <= limit, (
        f"CRIT_CHECK\n[max_slope::{critConf['name']}] slope {slopes[worst]:.6g}/s at {when(data.index[worst])} "
        f"exceeds {limit:g}/s")


def crit_settles(critConf: dict[str, Any]) -> None:
    """The signal reaches ``target`` +- ``tolerance`` at most ``within_sec`` after its first value,
    and stays there until its last value.

<TEMPLATE>
level_settles:
  type: settles
  param: OilLevel_pct
  target: 80
  tolerance: 2
  within_sec: 30
</TEMPLATE>
    """
    data = signal(critConf)
    target, tol = float(critConf["target"]), float(critConf["tolerance"])
    outside = (data - target).abs() > tol
    # settled from the value following the last one out of the band
    if not outside.any():
        settledAt = data.index[0]
    elif outside.iloc[-1]:
        raise AssertionError(f"CRIT_CHECK\n[settles::{critConf['name']}] last value {data.iloc[-1]:.6g} "
                             f"is not within {target:g} +- {tol:g}")
    else:
        settledAt = data.index[np.flatnonzero(outside.to_numpy())[-1] + 1]
    delay = settledAt - data.index[0]
    get_logger().info(f"{critConf['name']}: settled within {target:g} +- {tol:g} after {delay:.3f}s "
                      f"(limit {float(critConf['within_sec']):g}s)")
    assert delay <= float(critConf["within_sec"]), (
        f"CRIT_CHECK\n[settles::{critConf['name']}] settled after {delay:.3f}s, "
        f"more than {float(critConf['within_sec']):g}s")
