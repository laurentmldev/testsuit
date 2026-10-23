"""The ``ssh`` transport (AsyncSSH): key-based login, SFTP transfers, commands in their own process group.

Host variables used: ``ansible_host``, ``ansible_user``, ``ansible_port``, ``ansible_ssh_private_key_file``,
and ``tsq_ssh_options`` (extra ``asyncssh.connect`` keyword arguments, for example ``known_hosts: null``).
Commands run through ``setsid -w`` so that the whole process tree can be killed; the group id is
written to the step's pid file on the component.
"""
from __future__ import annotations

import asyncio
import os
import posixpath
import shlex
import stat
from pathlib import Path

from testsuit.sequencer.transports import LogSink, Process, Result, Transport, env_prefix
from testsuit.sequencer.util import SequencerError


class SshTransport(Transport):

    name = "ssh"

    def __init__(self, component, remote_root: str | None = None) -> None:
        super().__init__(component, remote_root)
        self._conn = None
        self._lock = asyncio.Lock()
        self._home: str | None = None

    async def connection(self):
        try:
            import asyncssh
        except ImportError as e:
            raise SequencerError("the ssh transport needs asyncssh: pip install 'testsuit[sequencer]'") from e
        async with self._lock:
            if self._conn is not None and not self._conn.is_closed():
                return self._conn
            options = dict(self.vars.get("tsq_ssh_options") or {})
            if self.vars.get("ansible_user"):
                options.setdefault("username", str(self.vars["ansible_user"]))
            if self.vars.get("ansible_port"):
                options.setdefault("port", int(self.vars["ansible_port"]))
            key = self.vars.get("ansible_ssh_private_key_file")
            if key:
                options.setdefault("client_keys", [os.path.expanduser(str(key))])
            options.setdefault("keepalive_interval", 15)
            options.setdefault("connect_timeout", 15)
            try:
                self._conn = await asyncssh.connect(self.host, **options)
            except (OSError, asyncssh.Error) as e:
                raise SequencerError(f"ssh to {self.host}: {e}") from e
            return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def resolve_root(self) -> str:
        if self._root.startswith("~"):
            if self._home is None:
                result = await self.shell("echo $HOME", timeout=20)
                if not result.completed:
                    raise SequencerError(f"ssh to {self.host}: {result.error}")
                self._home = result.output.strip()
            return self._home + self._root[1:]
        return self._root

    async def start(self, command: str, *, env: dict | None = None, log: LogSink | None = None,
                    pidfile: str | None = None) -> Process:
        conn = await self.connection()
        script = env_prefix(env) + command
        if pidfile:
            folder = posixpath.dirname(pidfile)
            wrapped = (f"mkdir -p {shlex.quote(folder)} && exec setsid -w sh -c 'echo $$ > \"$0\"; exec sh -c \"$1\"' "
                       f"{shlex.quote(pidfile)} {shlex.quote(script)}")
        else:
            wrapped = script
        try:
            proc = await conn.create_process(wrapped, stderr=asyncio.subprocess.STDOUT, encoding=None)
        except Exception as e:
            raise SequencerError(f"ssh to {self.host}: {e}") from e
        process = Process()

        async def drain():
            try:
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    process.feed(chunk, log)
                completed = await proc.wait(check=False)
                signal = completed.exit_signal[0] if completed.exit_signal else None
                code = completed.exit_status if completed.exit_status is not None and completed.exit_status >= 0 else None
                error = f"killed by {signal}" if signal else (None if code is not None else "connection lost")
                process.done.set_result(Result(code, process.text(), error=error, signal=signal))
            except Exception as e:
                if not process.done.done():
                    process.done.set_result(Result(None, process.text(), error=f"transport: {e}"))

        async def killer():
            proc.close()
        process.killer = killer
        process.task = asyncio.ensure_future(drain())
        return process

    async def put(self, local: Path, remote: str) -> None:
        conn = await self.connection()
        remote = await self._expand(remote)
        result = await self.shell(f"rm -rf {shlex.quote(remote)} && mkdir -p {shlex.quote(remote)}")
        if not result.completed or result.exit_status:
            raise SequencerError(f"cannot prepare {remote} on {self.host}: {result.error or result.output}")
        async with conn.start_sftp_client() as sftp:
            for path in sorted(local.rglob("*")):
                target = posixpath.join(remote, path.relative_to(local).as_posix())
                if path.is_dir():
                    await sftp.makedirs(target, exist_ok=True)
                else:
                    await sftp.put(str(path), target, preserve=True)

    async def get(self, remote: str, local: Path) -> int:
        conn = await self.connection()
        remote = await self._expand(remote)
        total = 0
        async with conn.start_sftp_client() as sftp:
            if not await sftp.isdir(remote):
                return 0

            async def walk(folder: str, target: Path):
                nonlocal total
                target.mkdir(parents=True, exist_ok=True)
                for entry in await sftp.readdir(folder):
                    if entry.filename in (".", ".."):
                        continue
                    source = posixpath.join(folder, entry.filename)
                    mode = entry.attrs.permissions or 0
                    if stat.S_ISDIR(mode):
                        await walk(source, target / entry.filename)
                    elif stat.S_ISREG(mode):
                        await sftp.get(source, str(target / entry.filename), preserve=True)
                        total += entry.attrs.size or 0
            await walk(remote, local)
        return total

    async def _expand(self, path: str) -> str:
        if path.startswith("~"):
            root = self._root
            self._root = path
            try:
                return await self.resolve_root()
            finally:
                self._root = root
        return path

    async def run(self, command: str, **kwargs) -> Result:
        try:
            return await super().run(command, **kwargs)
        except SequencerError as e:          # pragma: no cover - start() errors are already results
            return Result(None, error=f"transport: {e}")
