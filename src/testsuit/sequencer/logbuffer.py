"""The last log lines of tsqd, kept in memory for the UI (Lab page, Logs): the events of the sequencer
(runs started and ended, lab reloads, state changes...), its warnings and its errors, with their traceback.

A logging handler on the 'testsuit' logger (the sequencer, the datapack tool, the plugins) and on
uvicorn's error logger. The lines are numbered: a client asks for the ones after the last it has.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone

LOGGERS = ("testsuit", "uvicorn.error")
SIZE = 2000


class LogBuffer(logging.Handler):

    def __init__(self, size: int = SIZE) -> None:
        super().__init__(level=logging.INFO)
        self.records: deque[dict] = deque(maxlen=size)
        self.count = 0
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
            with self._guard:
                self.count += 1
                self.records.append({
                    "n": self.count,
                    "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                    "level": record.levelname, "logger": record.name, "message": message})
        except Exception:
            self.handleError(record)

    def lines(self, since: int = 0, level: str | None = None, limit: int = 1000) -> dict:
        """The lines after line since (at least of level), the newest limit ones; next: the since of the next call."""
        threshold = logging.getLevelName(level.upper()) if level else 0
        threshold = threshold if isinstance(threshold, int) else 0
        with self._guard:
            lines = [r for r in self.records if r["n"] > since and logging.getLevelName(r["level"]) >= threshold]
            last = self.count
        return {"lines": lines[-limit:], "next": last, "dropped": bool(lines) and lines[0]["n"] > since + 1 and since > 0}


_buffer: LogBuffer | None = None


def install() -> LogBuffer:
    """The buffer of this process, on the loggers it keeps (once)."""
    global _buffer
    if _buffer is None:
        _buffer = LogBuffer()
        for name in LOGGERS:                    # at the level tsqd logs at (--log-level)
            logging.getLogger(name).addHandler(_buffer)
    return _buffer
