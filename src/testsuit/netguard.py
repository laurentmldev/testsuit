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


"""Servers that cannot be reached: requirement and issue servers down must not stall tsq (sequencer SPEC 5.4, 11.3).

- **A server down** (connection refused or timed out, DNS failure, HTTP 502, 503 or 504) is not called again for a
  while (30 s, doubling at each new failure, at most 5 min): its calls fail at once with :class:`ServerDown`, so that a
  page, a report or a generation waits once, not at every call. A call that answers ends it.
- **Last known**: what was read from a server can be kept on disk (:class:`LastKnown`, in ``$TSQ_CACHE``, else
  ``$XDG_CACHE_HOME/testsuit``, else ``~/.cache/testsuit``), to be used, said as such, when the server is down.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

FIRST_PAUSE = 30.0
MAX_PAUSE = 300.0
DOWN_STATUSES = (502, 503, 504)


class ServerDown(Exception):
    """A server known to be down: not called (its last error, and when it is tried again)."""


_lock = threading.Lock()
_down: dict[str, dict[str, Any]] = {}          # host -> {since, until, pause, error}


def host_of(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}".lower()


def check(url: str) -> None:
    """Raise ServerDown when the server of ``url`` is down and its pause not over."""
    host = host_of(url)
    with _lock:
        d = _down.get(host)
        if d is None or time.monotonic() >= d["until"]:
            return
        left = d["until"] - time.monotonic()
    raise ServerDown(f"{host} cannot be reached since {d['since']} ({d['error']}); tried again in {left:.0f} s")


def failed(url: str, error: str) -> None:
    """A call to ``url`` failed as a server down does: no call to it for a while."""
    host = host_of(url)
    with _lock:
        d = _down.get(host)
        pause = min(MAX_PAUSE, d["pause"] * 2) if d else FIRST_PAUSE
        since = d["since"] if d else datetime.now().strftime("%H:%M:%S")
        _down[host] = {"since": since, "until": time.monotonic() + pause, "pause": pause, "error": str(error)[:200]}
    log.warning("%s cannot be reached (%s): not called for %.0f s", host, str(error)[:200], pause)


def answered(url: str) -> None:
    host = host_of(url)
    if host in _down:
        with _lock:
            if _down.pop(host, None) is not None:
                log.info("%s answers again", host)


def is_down_error(exc: BaseException) -> bool:
    """Whether an error (or one it comes from) is a server that cannot be reached."""
    import requests
    seen = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, (ServerDown, requests.ConnectionError, requests.Timeout)):
            return True
        e = e.__cause__ or e.__context__
    return False


def down_hosts() -> dict[str, dict[str, Any]]:
    """The servers down now: {host: {since, error, retry_in}}."""
    now = time.monotonic()
    with _lock:
        return {h: {"since": d["since"], "error": d["error"], "retry_in": round(max(0.0, d["until"] - now))}
                for h, d in _down.items()}


def reset() -> None:
    with _lock:
        _down.clear()


def cache_dir() -> Path:
    base = os.environ.get("TSQ_CACHE") or (Path(os.environ["XDG_CACHE_HOME"]) / "testsuit" if os.environ.get("XDG_CACHE_HOME")
                                           else Path.home() / ".cache" / "testsuit")
    return Path(base)


class LastKnown:
    """What a server last answered, by item, on disk: ``<cache>/<kind>/<hash of the server's identity>.json``. A failure
    to read or write it is never an error (a warning in the log)."""

    def __init__(self, kind: str, identity: Any) -> None:
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[:24]
        self.path = cache_dir() / kind / f"{digest}.json"
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            try:
                self._data = json.loads(self.path.read_text())
            except FileNotFoundError:
                self._data = {}
            except (OSError, ValueError) as e:
                log.warning("last known answers %s not read: %s", self.path, e)
                self._data = {}
        return self._data

    def get(self, key: str) -> tuple[Any, str] | None:
        """(the item, when it was read: an ISO time) or None."""
        hit = self._load().get(str(key))
        return (hit["item"], hit["at"]) if isinstance(hit, dict) and "item" in hit else None

    def put(self, items: dict[str, Any]) -> None:
        if not items:
            return
        data = self._load()
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data.update({str(k): {"item": v, "at": at} for k, v in items.items()})
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data, default=str))
            tmp.replace(self.path)
        except OSError as e:
            log.warning("last known answers %s not written: %s", self.path, e)
