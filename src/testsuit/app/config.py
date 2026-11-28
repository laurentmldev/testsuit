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

"""``testsuit.toml``: what the ``testsuit`` command starts (README, "The testsuit app").

One file names the modes to turn on and has one section per mode::

    modes = ["exploit", "sequencer"]   # default: ["exploit"]
    title = "Rail lab"                 # the UI's title (top bar and browser tab)
    logo = "logo.svg"                  # an image before the title: a file, or an http(s) URL (default: testsuit's icon)

    [ui]
    listen = "127.0.0.1:8650"          # the one port of the web UI
    browser = true                     # open the browser at the start
    base_path = "/testsuit"            # behind a reverse proxy that passes its sub-path on (default: none)
    public_url = "https://lab.example.org/testsuit"   # the URL users open, printed and shown
    trusted_proxies = ["127.0.0.1"]    # whose X-Forwarded-For and X-Forwarded-Proto are believed

    [exploit]                          # criteria editor, exploit_runner, Plot and Extract on a folder
    folder = "~/tests"                 # default: the folder opened last (a folder picker the first time)
    results = "..."                    # where the exploit_runner runs are kept (default <folder>/exploit_results)
    remote = false                     # true: also for other machines and through a reverse proxy (it runs code!)

    [sequencer]                        # the test sequencer: tsqd, started and supervised by testsuit
    config = "tsq.yml"                 # its configuration (default: $TSQ_CONFIG, then /etc/testsuit/tsq.yml)
    listen = "127.0.0.1:8640"          # where tsqd listens (the UI reaches it through testsuit's port)
    url = "http://lab:8640"            # or: a tsqd running elsewhere, used as it is (not started)

Read from ``--config``, else ``$TESTSUIT_CONFIG``, else ``./testsuit.toml``, else ``testsuit.toml`` in the
user's configuration folder. Without any, the defaults: the exploit mode alone. Relative paths are relative
to the file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib

ENV_VAR = "TESTSUIT_CONFIG"
FILE_NAME = "testsuit.toml"
MODES = ("sequencer", "exploit")
LOOPBACK = ("127.0.0.1", "localhost", "::1")


class ConfigError(Exception):
    """testsuit.toml cannot be read, or is not valid."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _host_port(value: str, what: str) -> tuple[str, int]:
    host, _, port = str(value).rpartition(":")
    try:
        return host or "127.0.0.1", int(port)
    except ValueError:
        raise ValueError(f"{what}: expected host:port, got '{value}'") from None


class UiSettings(_Model):
    listen: str = "127.0.0.1:8650"
    browser: bool = True
    base_path: str = ""                       # behind a reverse proxy that keeps its sub-path: "/testsuit"
    public_url: str | None = None             # the URL users open (behind a reverse proxy), printed and shown
    trusted_proxies: list[str] = Field(default_factory=list)   # their X-Forwarded-For/-Proto are believed

    @field_validator("listen")
    @classmethod
    def _listen(cls, v):
        _host_port(v, "ui.listen")
        return v

    @field_validator("base_path")
    @classmethod
    def _base_path(cls, v):
        v = (v or "").strip().strip("/")
        if any(c in v for c in "?#") or "//" in v:
            raise ValueError("ui.base_path: expected a path such as /testsuit")
        return f"/{v}" if v else ""

    @field_validator("public_url")
    @classmethod
    def _public_url(cls, v):
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("ui.public_url: expected an http(s) URL")
        return v.rstrip("/") if v else v


class ExploitSettings(_Model):
    folder: str | None = None
    results: str | None = None
    remote: bool = False                      # also answer other machines and a reverse proxy (authenticating!)


class SequencerSettings(_Model):
    config: str | None = None
    listen: str = "127.0.0.1:8640"
    url: str | None = None
    restart: bool = True

    @field_validator("listen")
    @classmethod
    def _listen(cls, v):
        _host_port(v, "sequencer.listen")
        return v

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("sequencer.url: expected an http(s) URL")
        return v.rstrip("/") if v else v


class AppConfig(_Model):
    modes: list[Literal["sequencer", "exploit"]] = Field(default_factory=lambda: ["exploit"])
    title: str = "testsuit"
    logo: str | None = None
    ui: UiSettings = Field(default_factory=UiSettings)
    exploit: ExploitSettings = Field(default_factory=ExploitSettings)
    sequencer: SequencerSettings = Field(default_factory=SequencerSettings)

    # where the file was read from, and which settings it gave (the others are defaults)
    file: Path | None = None
    given: dict[str, Any] = Field(default_factory=dict)

    @field_validator("modes")
    @classmethod
    def _modes(cls, v):
        if not v:
            raise ValueError("modes: turn on at least one of " + ", ".join(MODES))
        return list(dict.fromkeys(v))

    # ── resolved values ─────────────────────────────────────────────────
    @property
    def base_dir(self) -> Path:
        return self.file.parent if self.file else Path.cwd()

    def path(self, value: str | None) -> Path | None:
        if not value:
            return None
        p = Path(os.path.expanduser(value))
        return p if p.is_absolute() else (self.base_dir / p).resolve()

    def on(self, mode: str) -> bool:
        return mode in self.modes

    @property
    def listen(self) -> tuple[str, int]:
        return _host_port(self.ui.listen, "ui.listen")

    @property
    def local_url(self) -> str:
        """The URL of the app from this machine (its base path included)."""
        host, port = self.listen
        host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
        return f"http://{'[' + host + ']' if ':' in host else host}:{port}{self.ui.base_path}"

    @property
    def public_url(self) -> str:
        """The URL users open: ui.public_url (behind a reverse proxy), else local_url."""
        return self.ui.public_url or self.local_url

    @property
    def loopback_only(self) -> bool:
        return self.listen[0] in LOOPBACK

    @property
    def sequencer_url(self) -> str:
        """Where tsqd answers: sequencer.url, else the tsqd testsuit starts (on sequencer.listen)."""
        if self.sequencer.url:
            return self.sequencer.url
        host, port = _host_port(self.sequencer.listen, "sequencer.listen")
        host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
        return f"http://{host}:{port}"

    @property
    def logo_file(self) -> Path | None:
        """The logo image file (None when there is none, or when it is a URL)."""
        if not self.logo or self.logo.startswith(("http://", "https://")):
            return None
        return self.path(self.logo)

    def describe(self) -> list[tuple[str, Any, str]]:
        """Every setting with its value and where it comes from (the Configuration tab, testsuit status)."""
        rows = []

        def walk(prefix: str, model: BaseModel, given: Any):
            for name in type(model).model_fields:
                if name in ("file", "given"):
                    continue
                value = getattr(model, name)
                if isinstance(value, BaseModel):
                    walk(f"{prefix}{name}.", value, given.get(name) if isinstance(given, dict) else None)
                else:
                    source = str(self.file) if isinstance(given, dict) and name in given else "default"
                    rows.append((f"{prefix}{name}", value, source))
        walk("", self, self.given)
        return rows


def user_config_dir() -> Path:
    """The user's configuration folder of testsuit (%APPDATA%\\testsuit on Windows, $XDG_CONFIG_HOME/testsuit
    or ~/.config/testsuit elsewhere): testsuit.toml, the folders opened, the logs of the app."""
    if os.name == "nt" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"])
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "testsuit"


def find_config_file(path: str | os.PathLike | None = None) -> Path | None:
    if path:
        return Path(path).expanduser()
    if os.environ.get(ENV_VAR):
        return Path(os.environ[ENV_VAR]).expanduser()
    for candidate in (Path.cwd() / FILE_NAME, user_config_dir() / FILE_NAME):
        if candidate.is_file():
            return candidate
    return None


def load_config(path: str | os.PathLike | None = None) -> AppConfig:
    """The configuration of --config, $TESTSUIT_CONFIG, ./testsuit.toml or the user's testsuit.toml (the
    defaults when there is none)."""
    file = find_config_file(path)
    if file is None:
        return AppConfig()
    if not file.is_file():
        raise ConfigError(f"configuration file not found: '{file}'")
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"'{file}': {e}") from e
    try:
        config = AppConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"invalid configuration '{file}':\n{e}") from e
    config.file = file.resolve()
    config.given = data
    return config
