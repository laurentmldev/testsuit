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

"""The progress of reading requirements, for whoever waits on it: the Requirements view of the Editor, the log of a
datapack generation. The sources and the backends report what they start (``step``) and each call to a server
(``call``); a ``Progress`` set with ``reporting`` collects them, in the thread that reads."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

_current: ContextVar[Progress | None] = ContextVar("reqs_progress", default=None)


@dataclass
class Progress:
    """What a reading did so far. ``echo(line)``: also told of each step, and of the calls at most every
    ``every`` seconds (a log)."""
    echo: Callable[[str], None] | None = None
    every: float = 2.0
    started: float = field(default_factory=time.monotonic)
    steps: list[str] = field(default_factory=list)
    calls: int = 0
    last_call: str = ""
    done: bool = False
    _echoed: float = 0.0

    def step(self, text: str) -> None:
        self.steps.append(text)
        del self.steps[:-50]
        if self.echo:
            self.echo(text)
            self._echoed = time.monotonic()

    def call(self, text: str) -> None:
        self.calls += 1
        self.last_call = text
        now = time.monotonic()
        if self.echo and now - self._echoed >= self.every:
            self.echo(f"  ... call {self.calls}: {text}")
            self._echoed = now

    def as_dict(self) -> dict:
        return {"seconds": round(time.monotonic() - self.started, 1), "steps": self.steps[-8:], "calls": self.calls,
                "last_call": self.last_call, "done": self.done}


@contextmanager
def reporting(progress: Progress) -> Iterator[Progress]:
    token = _current.set(progress)
    try:
        yield progress
    finally:
        progress.done = True
        _current.reset(token)


def step(text: str) -> None:
    p = _current.get()
    if p is not None:
        p.step(text)


def call(text: str) -> None:
    p = _current.get()
    if p is not None:
        p.call(text)
