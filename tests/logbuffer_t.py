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

import logging

from sequencer_t import bench, no_git  # noqa: F401  (fixtures)

from testsuit.sequencer.logbuffer import LogBuffer
from testsuit.sequencer.sequencer import _event_line


def test_log_buffer_lines_since_and_level():
    buffer = LogBuffer(size=3)
    logger = logging.getLogger("testsuit.test_logbuffer")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.info("run r1 queued")
        logger.warning("component c1 unreachable")
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("scheduler error")
        logger.info("run r1 done")
    finally:
        logger.removeHandler(buffer)

    everything = buffer.lines()
    assert [line["n"] for line in everything["lines"]] == [2, 3, 4] and everything["next"] == 4   # the last 3 kept
    assert "Traceback" in everything["lines"][1]["message"] and "ValueError: boom" in everything["lines"][1]["message"]
    assert [line["level"] for line in buffer.lines(level="warning")["lines"]] == ["WARNING", "ERROR"]
    assert buffer.lines(since=3)["lines"][0]["message"] == "run r1 done"
    assert buffer.lines(since=4)["lines"] == []


def test_event_line():
    assert _event_line("run", {"run": "r1", "state": "done", "verdict": "passed", "reason": "", "binding": None}) == \
        "run: run=r1 state=done verdict=passed"
    assert len(_event_line("lab", {"git": {"commit": "x" * 300}})) < 200


def test_probes_kept_apart_and_logged(bench):  # noqa: F811  (the fixture)
    import asyncio

    from testsuit.sequencer.logbuffer import PROBE_LOGGER

    buffer = LogBuffer(size=2, probes=5)
    probes = logging.getLogger(PROBE_LOGGER)
    probes.addHandler(buffer)
    level, probes.level = probes.level, logging.DEBUG
    events = logging.getLogger("testsuit.test_logbuffer")
    events.addHandler(buffer)
    events.setLevel(logging.INFO)
    try:
        events.info("lab reloaded")
        seq = bench.sequencer()

        async def main():
            await seq.monitor.probe_all()
            await seq.close()
        asyncio.run(main())
        events.info("run r1 queued")
    finally:
        probes.removeHandler(buffer)
        probes.level = level
        events.removeHandler(buffer)

    lines = buffer.lines()["lines"]
    probed = [line for line in lines if line["probe"]]
    # every component probed: what was run and the state found, in their own buffer (the events are kept)
    assert probed and all(" -> " in line["message"] for line in probed), probed
    assert {line["message"].split(":")[0] for line in probed} <= set(seq.lab.inventory.components)
    assert [line["message"] for line in lines if not line["probe"]] == ["lab reloaded", "run r1 queued"]
    assert all(not line["probe"] for line in buffer.lines(probes=False)["lines"])
