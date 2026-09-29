"""mexploit criteria registered one by one, with the @register_criterion decorator.

Importing this module registers them (the plugin does it): the decorator takes the scenario
type from the function name without its ``crit_`` prefix, or from the name it is given.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from testsuit.exploit.mexploit.registry import register_criterion
from testsuit.misc.logger import get_logger

from acme_testbench.criteria import signal, when


@register_criterion("no_dropout")
def crit_acme_no_dropout(critConf: dict[str, Any]) -> None:
    """No hole in the acquisition: two consecutive samples are at most ``max_gap_sec`` apart.

<TEMPLATE>
logger_continuous:
  type: no_dropout
  param: OilFlow_lpm
  max_gap_sec: 0.5
</TEMPLATE>
    """
    data = signal(critConf)
    gaps = np.diff(data.index.to_numpy(dtype="float64"))
    worst = int(np.argmax(gaps)) if len(gaps) else 0
    maxGap = float(gaps[worst]) if len(gaps) else 0.0
    get_logger().info(f"{critConf['name']}: largest gap {maxGap:.3f}s (limit {float(critConf['max_gap_sec']):g}s)")
    assert maxGap <= float(critConf["max_gap_sec"]), (
        f"CRIT_CHECK\n[no_dropout::{critConf['name']}] {maxGap:.3f}s without data after {when(data.index[worst])}")


@register_criterion
def crit_mean_close_to(critConf: dict[str, Any]) -> None:
    """The mean value is ``expected`` +- ``tolerance``.

<TEMPLATE>
pressure_mean:
  type: mean_close_to
  param: BenchPressure_bar
  expected: 2.5
  tolerance: 0.1
</TEMPLATE>
    """
    data = signal(critConf)
    mean = float(data.mean())
    expected, tol = float(critConf["expected"]), float(critConf["tolerance"])
    get_logger().info(f"{critConf['name']}: mean {mean:.6g}, expected {expected:g} +- {tol:g}")
    assert abs(mean - expected) <= tol, (
        f"CRIT_CHECK\n[mean_close_to::{critConf['name']}] mean {mean:.6g} not within {expected:g} +- {tol:g}")
