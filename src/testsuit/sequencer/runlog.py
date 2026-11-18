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

"""The log of each run, kept in its run folder as ``logs/sequencer.log`` and written as the run goes: the run's
events (queued, waiting for lab resources, phases started, waiting and ended, every step's state with its slot,
reason and via), and the sequencer's own lines about the run (warnings, errors and their tracebacks). The step
logs (``logs/<phase>/<step>.log``) keep what each command printed; this one says what the sequencer did, so a
run that seems stuck tells where it is, without the tsqd log.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

FILE = "sequencer.log"
_ABOUT_RUN = re.compile(r"^run ([\w.-]+)\b")       # the sequencer's lines about a run: "run <id>: ..."
_STEP_FIELDS = ("state", "reason", "via", "exit_status", "attempts")


def event_line(kind: str, data: dict) -> str:
    """A run's event as one line (its run id left out)."""
    if kind == "step":
        key = f"{data.get('phase')}:{data.get('id')}" + (f"[{data['slot']}]" if data.get("slot") else "")
        fields = [f"{k}={data[k]}" for k in _STEP_FIELDS if data.get(k) not in (None, "", 0) or k == "state"
                  if not (k == "attempts" and (data.get(k) or 0) <= 1)]
        return f"step {key} " + " ".join(fields)
    fields = []
    for key, value in data.items():
        if key == "run" or value in (None, "", [], {}):
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str, separators=(",", ":"))
        fields.append(f"{key}={text if len(text) <= 300 else text[:297] + '...'}")
    return f"{kind} " + " ".join(fields)


class RunLog(logging.Handler):
    """Writes the lines of each run in its run folder. ``folder_of(run_id)`` gives the run folder (None: unknown,
    the line is dropped). Also a logging handler: the lines of the 'testsuit' loggers about a run go there too."""

    def __init__(self, folder_of: Callable[[str], str | Path | None]) -> None:
        super().__init__(level=logging.INFO)
        self.folder_of = folder_of
        self._write_lock = threading.Lock()

    def write(self, run_id: str, text: str, level: str = "INFO") -> None:
        try:
            folder = self.folder_of(run_id)
        except Exception:
            return
        if not folder or not Path(folder).is_dir():
            return
        stamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        lines = text.rstrip("\n").split("\n")
        body = "".join(f"{stamp} {level:<7} {line}\n" if i == 0 else f"    {line}\n" for i, line in enumerate(lines))
        path = Path(folder) / "logs" / FILE
        try:
            with self._write_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(body)
        except OSError:
            pass                                       # a log that cannot be written never stops the run

    def event(self, kind: str, data: dict) -> None:
        run_id = data.get("run")
        if not run_id or kind in ("log", "probe", "manual_output"):
            return
        warn = kind == "state_mismatch" or str(data.get("verdict", "")).upper() == "ERROR" or data.get("state") == "error"
        self.write(str(run_id), event_line(kind, data), "WARNING" if warn else "INFO")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            about = _ABOUT_RUN.match(message)
            if not about:
                return
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
            self.write(about.group(1).rstrip(":"), f"[{record.name}] {message}", record.levelname)
        except Exception:
            self.handleError(record)
