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

"""Running a command from the datapack tool with its output (stdout and stderr) in the tool's log.

The output is read line by line and printed, so it shows wherever the tool's output goes: the terminal
of the ``datapack`` command, or the live log of a generation started from the sequencer's UI.
"""
from __future__ import annotations

import os
import subprocess


def run_logged(command: list[str], prefix: str = "    | ", env: dict | None = None, cwd: str | None = None) -> tuple[int, str]:
    """Run command, printing each line it writes (stderr included) as it comes.

    :return: (exit status, its last lines of output), the latter to explain a failure
    """
    tail: list[str] = []
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                          text=True, errors="replace", bufsize=1, cwd=cwd,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}) as process:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                line = line.rstrip("\n")
                print(prefix + line, flush=True)
                tail = (tail + [line])[-20:]
        except BaseException:                         # interrupted: the command is not left running
            process.kill()
            raise
    return process.returncode, "\n".join(tail)
