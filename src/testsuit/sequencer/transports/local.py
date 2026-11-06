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

"""The ``local`` transport: commands run on the sequencer host, as the user running the sequencer."""
from __future__ import annotations

import asyncio
import os
import shutil
import signal as signal_module
from pathlib import Path

from testsuit.sequencer.transports import LogSink, Process, Result, Transport


class LocalTransport(Transport):

    name = "local"

    async def resolve_root(self) -> str:
        return os.path.expanduser(self._root)

    async def start(self, command: str, *, env: dict | None = None, log: LogSink | None = None,
                    pidfile: str | None = None) -> Process:
        full_env = {**os.environ, **{k: str(v) for k, v in (env or {}).items()}}
        proc = await asyncio.create_subprocess_shell(
            self.in_shell(command), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=full_env, start_new_session=True, executable="/bin/sh")
        if pidfile:
            Path(pidfile).parent.mkdir(parents=True, exist_ok=True)
            Path(pidfile).write_text(f"{proc.pid}\n")
        process = Process()

        async def drain():
            try:
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    process.feed(chunk, log)
                code = await proc.wait()
                signal = None
                if code < 0:
                    try:
                        signal = signal_module.Signals(-code).name
                    except ValueError:
                        signal = f"SIG{-code}"
                    code = None
                process.done.set_result(Result(code, process.text(), error=f"killed by {signal}" if signal else None,
                                               signal=signal))
            except asyncio.CancelledError:
                # The loop is shutting down (tsqd stopping, asyncio.run ending): close the process's
                # transport now. Left to the garbage collector, it would be closed after the loop,
                # which fails with 'Event loop is closed'.
                proc._transport.close()
                raise
            except Exception as e:                       # pragma: no cover - defensive
                if not process.done.done():
                    process.done.set_result(Result(None, process.text(), error=f"transport: {e}"))

        async def killer():
            try:
                os.killpg(proc.pid, 9)
            except ProcessLookupError:
                pass
        process.killer = killer
        process.task = asyncio.ensure_future(drain())
        return process

    async def put(self, local: Path, remote: str) -> None:
        target = Path(os.path.expanduser(remote))
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copytree, local, target, symlinks=True)

    async def get(self, remote: str, local: Path) -> int:
        source = Path(os.path.expanduser(remote))
        if not source.is_dir():
            return 0
        local.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copytree, source, local, symlinks=True, dirs_exist_ok=True)
        return sum(p.stat().st_size for p in source.rglob("*") if p.is_file())
