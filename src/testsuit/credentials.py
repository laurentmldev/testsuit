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

"""Secrets given by reference: wherever testsuit reads a password or a token from a configuration file or the command
line, the value can be ``env:<VARIABLE>`` (an environment variable) or ``file:<path>`` (the content of a file, such as a
Docker or Kubernetes secret ``/run/secrets/<name>``; trailing white space dropped; a relative path is relative to the
configuration file, or the current folder on the command line), so that it is never written where it would be shared.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_ENV = re.compile(r"^env:\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")


class CredentialError(ValueError):
    """A secret given by reference cannot be read: its variable is not set, or its file not readable."""


def is_reference(value: Any) -> bool:
    return isinstance(value, str) and (value.startswith("env:") or value.startswith("file:"))


def resolve_secret(value: Any, base_dir: str | Path | None = None, what: str = "secret") -> Any:
    """The secret a value names: ``env:<VARIABLE>`` or ``file:<path>`` read now; any other value as it is (a literal,
    or None)."""
    if not is_reference(value):
        return value
    if value.startswith("env:"):
        m = _ENV.match(value)
        if not m:
            raise CredentialError(f"{what}: '{value}' is not 'env:<VARIABLE>'")
        if m[1] not in os.environ:
            raise CredentialError(f"{what}: environment variable '{m[1]}' is not set")
        return os.environ[m[1]]
    path = Path(os.path.expanduser(value[5:].strip()))
    if not path.is_absolute():
        path = Path(base_dir or Path.cwd()) / path
    try:
        return path.read_text(encoding="utf-8").rstrip()
    except (OSError, UnicodeDecodeError) as e:
        raise CredentialError(f"{what}: file '{path}' is not readable ({getattr(e, 'strerror', None) or e})") from e
