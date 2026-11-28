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

"""The tsqd of the sequencer mode: started by ``testsuit`` as a child process, watched, restarted when it
ends by itself, and stopped with ``testsuit``.

tsqd runs in a process of its own (its own asyncio loop, its runs and their processes): a crash of one
side leaves the other one up. What it prints goes to ``tsqd.log`` (next to testsuit.log), which the
Configuration tab shows. When ``sequencer.url`` names a tsqd running elsewhere, nothing is started: its state
is only probed.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import requests

HEALTH_TIMEOUT = 2.0
READY_WAIT = 120.0          # a first start in a new environment can be long (imports scanned by an antivirus)
STOP_WAIT = 15.0            # tsqd kills its runs and packs their results meanwhile
BACKOFF = (1, 2, 5, 10, 30)
MAX_QUICK_EXITS = 5         # exits within QUICK seconds of their start in a row: tsqd cannot start, given up
QUICK = 30.0


def _die_with_parent() -> None:
    """In the child, on Linux: SIGTERM when testsuit ends without stopping it (killed), rather than a tsqd
    left behind on its port."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)    # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def tsqd_command() -> list[str]:
    """tsqd, in the Python of testsuit."""
    return [sys.executable, "-m", "testsuit.sequencer.daemon"]


def healthy(url: str) -> dict | None:
    """The /health of the tsqd at url (None: it does not answer)."""
    try:
        r = requests.get(f"{url}/health", timeout=HEALTH_TIMEOUT)
        data = r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("ok") else None


class Tsqd:
    """The tsqd of the sequencer mode, and its state: ``starting``, ``running``, ``restarting``, ``failed``,
    ``stopped``; ``remote`` for a tsqd testsuit does not start (sequencer.url) or found already running on
    its port (``adopted``)."""

    def __init__(self, url: str, listen: str | None, config: Path | None, log: Path, restart: bool = True,
                 command: list[str] | None = None) -> None:
        """:param listen: host:port of the tsqd to start (None: a tsqd running elsewhere, at url)
        :param config: its tsq.yml (None: its own lookup, $TSQ_CONFIG then /etc/testsuit/tsq.yml)
        :param command: the command starting tsqd (tests), its --listen and --config added"""
        self.url = url
        self.listen = listen
        self.config = config
        self.log = log
        self.restart = restart
        self.command = command or tsqd_command()
        self.state = "stopped" if listen else "remote"
        self.detail = ""
        self.restarts = 0
        self.last_exit: int | None = None
        self.started_at: float | None = None
        self.adopted = False
        self._proc: subprocess.Popen | None = None
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def managed(self) -> bool:
        return self.listen is not None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc and self._proc.poll() is None else None

    # ── start, watch, restart ──────────────────────────────────────────

    def start(self) -> None:
        """Start tsqd and its watcher (returns at once: the state says when it answers)."""
        if not self.managed or self._thread:
            return
        if healthy(self.url):
            # a tsqd already answers there (left by a testsuit that did not end cleanly, or started by hand):
            # used as it is, not stopped with testsuit
            self.adopted = True
            self.state, self.detail = "remote", f"a tsqd already running at {self.url}: used as it is"
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._watch, name="tsqd-supervisor", daemon=True)
        self._thread.start()

    def _spawn(self) -> subprocess.Popen:
        cmd = [*self.command, "--listen", self.listen]
        if self.config:
            cmd += ["--config", str(self.config)]
        self.log.parent.mkdir(parents=True, exist_ok=True)
        if self.log.is_file() and self.log.stat().st_size > 5_000_000:
            self.log.replace(self.log.with_suffix(".log.1"))
        out = open(self.log, "ab")
        out.write(f"\n── {time.strftime('%Y-%m-%d %H:%M:%S')} testsuit starts: {' '.join(cmd)}\n".encode())
        out.flush()
        kwargs: dict = {}
        if os.name == "nt":
            # not in the console's group: Ctrl+C reaches testsuit only, which stops tsqd itself
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
            if sys.platform.startswith("linux"):
                kwargs["preexec_fn"] = _die_with_parent
        try:
            return subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kwargs)
        finally:
            out.close()

    def _watch(self) -> None:
        quick = 0
        while not self._stopping.is_set():
            self.state = "starting" if not self.restarts else "restarting"
            try:
                with self._lock:
                    self._proc = self._spawn()
            except OSError as e:
                self.state, self.detail = "failed", f"tsqd cannot be started: {e}"
                return
            self.started_at = time.time()
            self.detail = f"pid {self._proc.pid}"
            self._wait_ready()
            code = self._proc.wait()
            self.last_exit = code
            if self._stopping.is_set():
                break
            lived = time.time() - self.started_at
            quick = quick + 1 if lived < QUICK else 0
            if not self.restart:
                self.state, self.detail = "failed", f"tsqd ended (exit code {code}); sequencer.restart is off"
                return
            if quick >= MAX_QUICK_EXITS:
                self.state, self.detail = "failed", (f"tsqd ended {quick} times right after its start (exit code "
                                                     f"{code}): see its log")
                return
            delay = BACKOFF[min(quick, len(BACKOFF) - 1)]
            self.state, self.detail = "restarting", f"tsqd ended (exit code {code}), restarted in {delay} s"
            self.restarts += 1
            if self._stopping.wait(delay):
                break
        self.state = "stopped"

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + READY_WAIT
        while time.monotonic() < deadline and not self._stopping.is_set():
            if self._proc.poll() is not None:
                return
            if healthy(self.url):
                self.state, self.detail = "running", f"pid {self._proc.pid}"
                return
            self._stopping.wait(0.3)
        if self._proc.poll() is None and not self._stopping.is_set():
            self.detail = f"pid {self._proc.pid}, not answering at {self.url} yet"

    # ── stop ───────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Stop tsqd (its active runs are killed, their finally steps run and results packed, as on Ctrl+C)."""
        self._stopping.set()
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                # SIGTERM: tsqd stops as on Ctrl+C. On Windows, the process ends at once (TerminateProcess)
                proc.terminate()
                proc.wait(STOP_WAIT)
            except (OSError, subprocess.TimeoutExpired, ValueError):
                proc.kill()
                proc.wait()
        if self._thread:
            self._thread.join(5)
            self._thread = None
        if self.managed and not self.adopted:
            self.state = "stopped"

    def restart_now(self) -> None:
        """Stop tsqd and start it again (the Configuration tab)."""
        if not self.managed or self.adopted:
            raise RuntimeError("this tsqd is not started by testsuit: restart it where it runs")
        self.stop()
        self.restarts = 0
        self.start()

    # ── what the UI and `testsuit status` show ──────────────────────────

    def status(self) -> dict:
        state = self.state
        if state == "remote":
            state = "running" if healthy(self.url) else "unreachable"
        return {"state": state, "detail": self.detail, "url": self.url, "managed": self.managed and not self.adopted,
                "pid": self.pid, "restarts": self.restarts, "last_exit": self.last_exit,
                "started_at": self.started_at, "log": str(self.log) if self.managed and not self.adopted else None,
                "config": str(self.config) if self.config else None}

    def log_tail(self, lines: int = 200) -> str:
        try:
            with open(self.log, encoding="utf-8", errors="replace") as f:
                return "".join(deque(f, maxlen=lines))
        except OSError:
            return ""
