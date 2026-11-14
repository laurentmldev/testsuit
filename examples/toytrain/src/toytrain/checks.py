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

"""mexploit criteria registered one by one, with the @register_criterion decorator.

Importing this module registers them (the plugin does it): the decorator takes the scenario
type from the function name without its ``crit_`` prefix, or from the name it is given.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from pydantic import Field

from testsuit.exploit.mexploit.registry import register_criterion
from testsuit.exploit.mexploit.spec import CritConf
from testsuit.misc.logger import get_logger

from toytrain.criteria import Signal, signal, when


class NoDropout(CritConf, Signal):
    """No hole in the acquisition: two consecutive samples are at most max_gap_sec apart."""
    max_gap_sec: float = Field(description="Longest gap (s) allowed between two samples.")


class MeanCloseTo(CritConf, Signal):
    """The mean value is expected +- tolerance."""
    expected: float = Field(description="Expected mean.")
    tolerance: float = Field(description="Accepted distance to the expected mean.")


@register_criterion("no_dropout", conf=NoDropout)
def crit_tt_no_dropout(critConf: dict[str, Any]) -> None:
    """No hole in the acquisition: two consecutive samples are at most ``max_gap_sec`` apart.

<TEMPLATE>
telemetry_continuous:
  type: no_dropout
  param: speed_mps
  max_gap_sec: 0.1
</TEMPLATE>
    """
    data = signal(critConf)
    gaps = np.diff(data.index.to_numpy(dtype="float64"))
    worst = int(np.argmax(gaps)) if len(gaps) else 0
    maxGap = float(gaps[worst]) if len(gaps) else 0.0
    get_logger().info(f"{critConf['name']}: largest gap {maxGap:.3f}s (limit {float(critConf['max_gap_sec']):g}s)")
    assert maxGap <= float(critConf["max_gap_sec"]), (
        f"CRIT_CHECK\n[no_dropout::{critConf['name']}] {maxGap:.3f}s without data after {when(data.index[worst])}")


@register_criterion(conf=MeanCloseTo)
def crit_mean_close_to(critConf: dict[str, Any]) -> None:
    """The mean value is ``expected`` +- ``tolerance``.

<TEMPLATE>
start_speed:
  type: mean_close_to
  param: speed_mps
  from_sec: 1
  to_sec: 5
  expected: 0.25
  tolerance: 0.02
</TEMPLATE>
    """
    data = signal(critConf)
    mean = float(data.mean())
    expected, tol = float(critConf["expected"]), float(critConf["tolerance"])
    get_logger().info(f"{critConf['name']}: mean {mean:.6g}, expected {expected:g} +- {tol:g}")
    assert abs(mean - expected) <= tol, (
        f"CRIT_CHECK\n[mean_close_to::{critConf['name']}] mean {mean:.6g} not within {expected:g} +- {tol:g}")
