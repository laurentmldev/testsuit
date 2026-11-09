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

"""The ``ssh`` transport (AsyncSSH): key-based login, SFTP transfers, commands in their own process group.

Host variables used: ``ansible_host``, ``ansible_user``, ``ansible_port``, ``ansible_ssh_private_key_file``,
``tsq_ssh_host_key`` (the host's public key(s) or ``SHA256:`` fingerprint(s): when given, the server must present
one of them, and ``known_hosts`` files are not read for that host),
and ``tsq_ssh_options`` (extra ``asyncssh.connect`` keyword arguments, for example ``known_hosts: null``),
``tsq_shell`` (the shell commands run under: ``sh``, the default, for steps and commands; the login shell for
short checks; with ``tsq_shell``, that shell for both). Commands run through ``setsid -w`` so that the whole process tree can be killed; the group id is
written to the step's pid file on the component.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import posixpath
import shlex
import stat
from pathlib import Path

from testsuit.sequencer.transports import LogSink, Process, Result, Transport, env_prefix
from testsuit.sequencer.util import SequencerError


def pinned_host_keys(value) -> list[str]:
    """The entries of a ``tsq_ssh_host_key`` (a string, or a list of strings), checked: each is an OpenSSH public
    key line (``ssh-ed25519 AAAA... [comment]``) or a ``SHA256:`` fingerprint. Empty when none is given.
    Syntax only, without asyncssh: the inventory checks it at load time."""
    if value is None or value == "" or value == []:
        return []
    entries = value if isinstance(value, list) else [value]
    out = []
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            raise SequencerError(f"tsq_ssh_host_key: '{entry}' is not a public key or a fingerprint")
        entry = entry.strip()
        if entry.startswith("SHA256:"):
            if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}=?", entry):
                raise SequencerError(f"tsq_ssh_host_key: '{entry}' is not a SHA256 fingerprint "
                                     f"(as 'ssh-keygen -lf' prints it)")
        else:
            parts = entry.split()
            if len(parts) >= 3 and re.fullmatch(r"(ssh-|ecdsa-|sk-)[a-z0-9@.\-]+", parts[1]):
                raise SequencerError(f"tsq_ssh_host_key: '{entry[:40]}...' starts with a host name or marker, as in "
                                     f"known_hosts: give the key alone, from '{parts[1]}'")
            if len(parts) < 2 or not re.fullmatch(r"(ssh-|ecdsa-|sk-)[a-z0-9@.\-]+", parts[0]):
                raise SequencerError(f"tsq_ssh_host_key: '{entry[:40]}' is neither a public key ('ssh-ed25519 "
                                     f"AAAA...', as in the server's /etc/ssh/ssh_host_*_key.pub) nor a "
                                     f"'SHA256:' fingerprint (as 'ssh-keygen -lf' prints it)")
            try:
                base64.b64decode(parts[1], validate=True)
            except ValueError:
                raise SequencerError(f"tsq_ssh_host_key: the key data of '{entry[:40]}' is not base64") from None
        out.append(entry)
    return out


def _fingerprint(text: str) -> str:
    return text.rstrip("=")


def asyncssh_host_key_error() -> tuple:
    """The exception asyncssh raises for an untrusted host key (a key exchange failure), () without asyncssh."""
    try:
        import asyncssh
    except ImportError:                     # pragma: no cover
        return ()
    return (asyncssh.HostKeyNotVerifiable,)


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
            pinned = self._pinned(asyncssh)
            if pinned:
                options.update(pinned)
            try:
                self._conn = await asyncssh.connect(self.host, **options)
            except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
                raise SequencerError(f"ssh to {self.host}: {self._why(e, options)}") from e
            return self._conn

    def _pinned(self, asyncssh) -> dict:
        """The ``asyncssh.connect`` options checking the server against ``tsq_ssh_host_key``, {} without one.

        The declared keys replace any known_hosts file (and a ``known_hosts`` of ``tsq_ssh_options``): the server
        must present one of them, or one matching a declared fingerprint, else the login fails."""
        entries = pinned_host_keys(self.vars.get("tsq_ssh_host_key"))
        if not entries:
            return {}
        keys, fingerprints = [], set()
        for entry in entries:
            if entry.startswith("SHA256:"):
                fingerprints.add(_fingerprint(entry))
            else:
                try:
                    keys.append(asyncssh.import_public_key(entry))
                except (asyncssh.KeyImportError, ValueError) as e:
                    raise SequencerError(f"ssh to {self.host}: tsq_ssh_host_key '{entry[:40]}...': {e}") from e

        class PinnedClient(asyncssh.SSHClient):
            def validate_host_public_key(self, host, addr, port, key) -> bool:
                return _fingerprint(key.get_fingerprint("sha256")) in fingerprints

        # a non-empty tuple (keys, CA keys, revoked keys): an empty value would read ~/.ssh/known_hosts instead
        return {"known_hosts": (keys, [], []), "client_factory": PinnedClient}

    def _why(self, e: BaseException, options: dict) -> str:
        """The reason of a failed login, never empty (a timeout has no message), with what to check."""
        port = options.get("port", 22)
        if isinstance(e, asyncssh_host_key_error()):
            if self.vars.get("tsq_ssh_host_key"):
                return (f"{e}: the server's key is not the one declared in tsq_ssh_host_key (key changed, or "
                        f"not the expected server?)")
            return (f"{e}: its key is in no known_hosts file of the user running the sequencer; declare it in "
                    f"tsq_ssh_host_key (ssh-keygen -F {self.host} prints it, from a host that knows it)")
        if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
            why = f"no answer on port {port} within {options.get('connect_timeout')} s (sshd not running, or a firewall?)"
        elif isinstance(e, ConnectionRefusedError):
            why = f"connection refused on port {port} (sshd not running?)"
        else:
            why = str(e) or type(e).__name__
        if self.host in ("localhost", "127.0.0.1", "::1"):
            why += "; for a component on the sequencer host itself, set tsq_transport: local"
        return why

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
        shell = self.shell_command or "sh"                  # the command's shell (tsq_shell); sh by default
        if pidfile:
            folder = posixpath.dirname(pidfile)
            wrapped = (f"mkdir -p {shlex.quote(folder)} && exec setsid -w sh -c 'echo $$ > \"$0\"; exec {shell} -c \"$1\"' "
                       f"{shlex.quote(pidfile)} {shlex.quote(script)}")
        else:
            wrapped = self.in_shell(script)                 # else the login shell runs it
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
