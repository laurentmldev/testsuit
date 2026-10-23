"""Main configuration of a sequencer instance: tsq.yml (README section 4.1).

Read from ``--config``, else ``$TSQ_CONFIG``, else ``/etc/testsuit/tsq.yml``. Every setting has a
default. Relative paths are relative to ``storage.root`` for the storage settings, and to the
configuration file for the others (``lab.folder``, ``server.workspaces``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from testsuit.sequencer.util import SequencerError, load_yaml, parse_duration, parse_size

DEFAULT_CONFIG = Path("/etc/testsuit/tsq.yml")
ENV_VAR = "TSQ_CONFIG"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LabSettings(_Model):
    folder: str = "lab"
    inventory: str = "inventory.yml"
    require_tag: bool = True


class LargeFiles(_Model):
    threshold: int = 200 * 10**6
    mode: Literal["zip", "external"] = "zip"

    @field_validator("threshold", mode="before")
    @classmethod
    def _size(cls, v):
        return parse_size(v)


class Retention(_Model):
    runs: float | None = 30 * 86400          # seconds; None: forever
    results: float | None = None

    @field_validator("runs", "results", mode="before")
    @classmethod
    def _duration(cls, v):
        return None if v in (None, "forever", "never") else parse_duration(v)


class StorageSettings(_Model):
    root: str = "/srv/tsq"
    incoming: str = "incoming"
    runs: str = "runs"
    results: str = "results"
    database: str = "tsq.sqlite"
    min_free_space: int = 20 * 10**9
    max_run_output: int = 50 * 10**9
    large_files: LargeFiles = Field(default_factory=LargeFiles)
    retention: Retention = Field(default_factory=Retention)

    @field_validator("min_free_space", "max_run_output", mode="before")
    @classmethod
    def _size(cls, v):
        return parse_size(v)


class ServerSettings(_Model):
    listen: str = "0.0.0.0:8640"
    ui: bool = True
    editor: bool = False
    workspaces: list[str] = Field(default_factory=list)


class QueueSettings(_Model):
    default_priority: int = Field(50, ge=0, le=100)
    priority_aging: Any = "off"               # 'off', or '+1 per 30min'


class SelftestSettings(_Model):
    before_each_run: bool = True
    timeout: float = 60.0
    max_attempts: int = Field(3, ge=1)

    @field_validator("timeout", mode="before")
    @classmethod
    def _duration(cls, v):
        return parse_duration(v)


class MonitorSettings(_Model):
    interval: float = 30.0

    @field_validator("interval", mode="before")
    @classmethod
    def _duration(cls, v):
        return parse_duration(v)


class Config(_Model):
    lab: LabSettings = Field(default_factory=LabSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    queue: QueueSettings = Field(default_factory=QueueSettings)
    selftest: SelftestSettings = Field(default_factory=SelftestSettings)
    monitor: MonitorSettings = Field(default_factory=MonitorSettings)
    plugins: list[str] = Field(default_factory=list)

    # where the file was read from, and which settings it gave (the others are defaults)
    file: Path | None = None
    given: dict[str, Any] = Field(default_factory=dict)

    # ── resolved paths ──────────────────────────────────────────────────
    @property
    def base_dir(self) -> Path:
        return self.file.parent if self.file else Path.cwd()

    def _path(self, value: str, base: Path) -> Path:
        path = Path(os.path.expanduser(value))
        return path if path.is_absolute() else (base / path).resolve()

    @property
    def root(self) -> Path:
        return self._path(self.storage.root, self.base_dir)

    @property
    def lab_folder(self) -> Path:
        return self._path(self.lab.folder, self.base_dir)

    @property
    def inventory_file(self) -> Path:
        return self._path(self.lab.inventory, self.lab_folder)

    def storage_path(self, name: str) -> Path:
        return self._path(getattr(self.storage, name), self.root)

    @property
    def workspaces(self) -> list[Path]:
        return [self._path(w, self.base_dir) for w in self.server.workspaces]

    @property
    def listen(self) -> tuple[str, int]:
        host, _, port = self.server.listen.rpartition(":")
        return host or "0.0.0.0", int(port)

    def aging(self) -> tuple[int, float] | None:
        """(increment, period in seconds) of priority aging, None when off."""
        value = self.queue.priority_aging
        if value in (None, False, "off", "none", ""):
            return None
        text = str(value).replace("per", " ").split()
        try:
            return int(text[0]), parse_duration("".join(text[1:]))
        except (IndexError, ValueError, SequencerError) as e:
            raise SequencerError(f"invalid queue.priority_aging '{value}' (expected for example '+1 per 30min')") from e

    def describe(self) -> list[tuple[str, Any, str]]:
        """Every setting with its value and where it comes from, for 'tsq config'."""
        rows = []

        def walk(prefix: str, model: BaseModel, given: Any):
            for name in type(model).model_fields:
                if name in ("file", "given"):
                    continue
                value = getattr(model, name)
                key = f"{prefix}{name}"
                sub = given.get(name) if isinstance(given, dict) else None
                if isinstance(value, BaseModel):
                    walk(key + ".", value, sub or {})
                else:
                    source = str(self.file) if isinstance(given, dict) and name in given else "default"
                    rows.append((key, value, source))
        walk("", self, self.given)
        return rows


def find_config_file(path: str | os.PathLike | None = None) -> Path | None:
    if path:
        return Path(path).expanduser()
    if os.environ.get(ENV_VAR):
        return Path(os.environ[ENV_VAR]).expanduser()
    return DEFAULT_CONFIG if DEFAULT_CONFIG.is_file() else None


def load_config(path: str | os.PathLike | None = None) -> Config:
    """The configuration of --config, $TSQ_CONFIG or /etc/testsuit/tsq.yml (all defaults if none exists)."""
    file = find_config_file(path)
    if file is None:
        return Config()
    if not file.is_file():
        raise SequencerError(f"configuration file not found: '{file}'")
    data = load_yaml(file) or {}
    if not isinstance(data, dict):
        raise SequencerError(f"'{file}': expected a mapping")
    try:
        config = Config.model_validate(data)
    except ValidationError as e:
        raise SequencerError(f"invalid configuration '{file}':\n{e}") from e
    config.file = file.resolve()
    config.given = data
    config.aging()
    return config
