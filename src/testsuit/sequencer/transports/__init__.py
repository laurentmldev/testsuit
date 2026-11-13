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

"""Transports (README section 12): how the sequencer talks to a component.

Every process the sequencer starts on a component runs in a process group of its own, whose id is
written to ``<remote_root>/.tsq/<run>/<component>/<step>.pid``. Killing a step, a component or a
whole run is then a ``kill`` of those groups, even after the ssh session or the daemon is gone
(README section 11).
"""
from __future__ import annotations

import asyncio
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from testsuit.sequencer import registry
from testsuit.sequencer.util import SequencerError

LogSink = Callable[[bytes], None]


@dataclass
class Result:
    """Outcome of a command: exit status, or why it has none."""
    exit_status: int | None
    output: str = ""                        # stdout and stderr, merged, last MAX_KEPT characters
    error: str | None = None                # timeout, killed, transport: the command did not complete
    signal: str | None = None

    @property
    def completed(self) -> bool:
        return self.error is None and self.exit_status is not None


MAX_KEPT = 1 << 20


class Process:
    """A started command. ``wait()`` gives its Result."""

    def __init__(self) -> None:
        self.done = asyncio.get_running_loop().create_future()
        self.output = bytearray()
        self.killer = None                      # set by the transport: kills the process group

    async def kill(self) -> None:
        if self.killer is not None and not self.done.done():
            try:
                await self.killer()
            except Exception:
                pass

    def feed(self, data: bytes, log: LogSink | None) -> None:
        self.output += data
        if len(self.output) > MAX_KEPT:
            del self.output[:len(self.output) - MAX_KEPT]
        if log:
            log(data)

    def text(self) -> str:
        return self.output.decode("utf-8", errors="replace")

    async def wait(self, timeout: float | None = None) -> Result:
        return await asyncio.wait_for(asyncio.shield(self.done), timeout)


class Transport:
    """Base class. A transport is created per concrete component and kept for the daemon's lifetime."""

    name = "base"

    def __init__(self, component, remote_root: str | None = None) -> None:
        self.component = component              # inventory.Component
        self.host = component.host
        self.vars = getattr(component, "settings", component.vars)   # its stage group's ansible_user... too
        self._root = remote_root if remote_root is not None else str(self.vars.get("tsq_remote_root", "/tmp/tsq"))
        self.shell_command = shell_of(component)

    def in_shell(self, script: str) -> str:
        """The script as the component's shell (tsq_shell) runs it: itself under /bin/sh, the default."""
        return f"exec {self.shell_command} -c {shlex.quote(script)}" if self.shell_command else script

    # ── to implement ─────────────────────────────────────────────────────
    async def start(self, command: str, *, env: dict | None = None, log: LogSink | None = None,
                    pidfile: str | None = None) -> Process:
        """Start a shell command in a new process group, its group id written to pidfile."""
        raise NotImplementedError

    async def put(self, local: Path, remote: str) -> None:
        """Copy the local folder to the remote path (created, contents replaced)."""
        raise NotImplementedError

    async def get(self, remote: str, local: Path) -> int:
        """Copy the remote folder into the local one; return the number of bytes copied."""
        raise NotImplementedError

    async def close(self) -> None:
        pass

    async def resolve_root(self) -> str:
        return self._root

    # ── built on start() ─────────────────────────────────────────────────
    async def run(self, command: str, *, env: dict | None = None, timeout: float | None = None,
                  log: LogSink | None = None, pidfile: str | None = None) -> Result:
        """Run a command and wait for it; on timeout or cancellation, kill its process group."""
        try:
            process = await self.start(command, env=env, log=log, pidfile=pidfile)
        except SequencerError as e:
            return Result(None, error=f"transport: {e}")
        except Exception as e:
            return Result(None, error=f"transport: {type(e).__name__}: {e}")
        try:
            return await process.wait(timeout)
        except asyncio.TimeoutError:
            await (self.kill_pidfile(pidfile) if pidfile else process.kill())
            return Result(None, output=process.text(), error="timeout")
        except asyncio.CancelledError:
            await asyncio.shield(self.kill_pidfile(pidfile) if pidfile else process.kill())
            raise

    async def shell(self, command: str, timeout: float | None = 30) -> Result:
        """A short helper command (probes, self-test, process control), no pid file."""
        return await self.run(command, timeout=timeout)

    async def kill_pidfile(self, pidfile: str | None, sig: str = "KILL") -> None:
        if pidfile:
            await self.shell(f"kill -{sig} -$(cat {shlex.quote(pidfile)}) 2>/dev/null; true", timeout=15)

    async def signal_dir(self, folder: str, sig: str = "TERM") -> None:
        """Signal every process group recorded under folder (recursively)."""
        q = shlex.quote(folder)
        await self.shell(f"[ -d {q} ] && find {q} -name '*.pid' | while read f; do "
                         f"kill -{sig} -$(cat \"$f\") 2>/dev/null; done; true", timeout=20)

    async def alive_in(self, folder: str, pattern: str = "*.pid") -> list[str]:
        """Pid files under folder (matching a find -path pattern) whose process group is still alive."""
        q = shlex.quote(folder)
        result = await self.shell(f"[ -d {q} ] && find {q} -path {shlex.quote(pattern)} | while read f; do "
                                  f"g=$(cat \"$f\"); if ps -A -o pgid=,stat= >/dev/null 2>&1; then "
                                  f"ps -A -o pgid=,stat= | awk -v g=\"$g\" '$1==g && $2 !~ /^Z/ {{f=1}} END {{exit !f}}' "
                                  f"&& echo \"$f\"; else kill -0 -$g 2>/dev/null && echo \"$f\"; fi; done; true", timeout=20)
        if not result.completed:
            raise SequencerError(result.error or "probe failed")
        return [line for line in result.output.splitlines() if line.strip()]


def create_transport(name: str, component, remote_root: str | None = None) -> Transport:
    from testsuit.sequencer.transports.local import LocalTransport
    from testsuit.sequencer.transports.ssh import SshTransport
    builtin = {"local": LocalTransport, "ssh": SshTransport}
    cls = registry.transports.get(name.lower()) or builtin.get(name.lower())
    if cls is None:
        raise SequencerError(f"component '{component.id}': unknown transport '{name}' "
                             f"(available: {', '.join(sorted(set(builtin) | set(registry.transports)))})")
    return cls(component, remote_root)


def shell_of(component) -> str:
    """The shell commands run under on a component (tsq_shell: bash, "bash -l"...), '' for /bin/sh."""
    value = str(getattr(component, "settings", component.vars).get("tsq_shell") or "").strip()
    if value and not re.fullmatch(r"[\w./+-]+( +-[\w-]+)*", value):
        raise SequencerError(f"component '{component.id}': tsq_shell '{value}' is not a shell, with options "
                             "if any (bash, /bin/bash, \"bash -l\")")
    return value


TRACE = "+tsq$ "                         # before each line of a traced command, in its output


def traced(command: str, before: str = "") -> str:
    """A step or a command run by hand as it runs: each line it runs is written to its output just before what
    that line prints (``set -x``, each line after ``TRACE``), so the output reads line by line; ``before``: what
    runs first, not traced (a ``cd``...)."""
    return (f"{before}\n" if before else "") + f"PS4={shlex.quote(TRACE)}\nset -x\n" + command


_TRACED = re.compile(r"^\++" + re.escape(TRACE.lstrip("+")) + r".*(\n|$)", re.MULTILINE)


def untraced(output: str) -> str:
    """The output of a traced command without the lines it ran: what the command itself printed."""
    return _TRACED.sub("", output)


def strict(command: str) -> str:
    """A command written by the user (a step, a component's command, probe or self-test), as it runs: it
    stops at the first line that fails (``set -e``), its exit status then that line's. To go on after a
    failure, the command says so (``|| true``, ``if``, ``set +e``)."""
    return "set -e\n" + command


def env_prefix(env: dict[str, Any] | None) -> str:
    if not env:
        return ""
    return "".join(f"export {k}={shlex.quote(str(v))}; " for k, v in env.items())
