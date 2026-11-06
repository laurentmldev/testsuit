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

"""The last log lines of tsqd, kept in memory for the UI (Lab page, Logs): the events of the sequencer
(runs started and ended, lab reloads, state changes...), its warnings and its errors, with their traceback.

A logging handler on the 'testsuit' logger (the sequencer, the datapack tool, the plugins) and on
uvicorn's error logger. The lines are numbered: a client asks for the ones after the last it has.

The state probes of the lab components (logger 'testsuit.probe': each command, its result, its errors)
come every few seconds: they are kept apart, the last PROBES of them, so they never push the events out,
and they do not go to the console.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone

LOGGERS = ("testsuit", "uvicorn.error")
PROBE_LOGGER = "testsuit.probe"
SIZE = 2000
PROBES = 1000


class LogBuffer(logging.Handler):

    def __init__(self, size: int = SIZE, probes: int = PROBES) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: deque[dict] = deque(maxlen=size)
        self.probes: deque[dict] = deque(maxlen=probes)
        self.count = 0
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
            probe = record.name == PROBE_LOGGER
            if not probe and record.levelno < logging.INFO:
                return
            with self._guard:
                self.count += 1
                (self.probes if probe else self.records).append({
                    "n": self.count,
                    "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                    "level": record.levelname, "logger": record.name, "message": message, "probe": probe})
        except Exception:
            self.handleError(record)

    def lines(self, since: int = 0, level: str | None = None, limit: int = 1000, probes: bool = True) -> dict:
        """The lines after line since (at least of level; with the probes or not), the newest limit ones;
        next: the since of the next call."""
        threshold = logging.getLevelName(level.upper()) if level else 0
        threshold = threshold if isinstance(threshold, int) else 0
        with self._guard:
            kept = list(self.records) + (list(self.probes) if probes else [])
            lines = sorted((r for r in kept if r["n"] > since and logging.getLevelName(r["level"]) >= threshold),
                           key=lambda r: r["n"])
            last = self.count
        return {"lines": lines[-limit:], "next": last}


_buffer: LogBuffer | None = None


def install() -> LogBuffer:
    """The buffer of this process, on the loggers it keeps (once)."""
    global _buffer
    if _buffer is None:
        _buffer = LogBuffer()
        for name in LOGGERS:                    # at the level tsqd logs at (--log-level)
            logging.getLogger(name).addHandler(_buffer)
        probes = logging.getLogger(PROBE_LOGGER)    # every probe, here only
        probes.addHandler(_buffer)
        probes.setLevel(logging.DEBUG)
        probes.propagate = False
    return _buffer
