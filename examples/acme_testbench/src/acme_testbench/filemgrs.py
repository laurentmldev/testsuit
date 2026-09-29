"""File managers ("readers") for the data files of the ACME test bench.

Three examples, from the most complete to the smallest:

- AcmeJsonFileMgr: a brand new format (``*.acmej``), the JSON dump of the bench controller
- SimulatedFileMgr: a fake format (``*.sim.yml``) whose signals are generated, not read: handy to
  write and test scenarios before any real data exists
- AcmeLoggerCsvFileMgr: a CSV syntax (a ``#ACME-LOGGER`` line on top of a usual CSV file), read by
  testsuit's CsvFileMgr once that line is skipped

testsuit calls a file manager in two steps: getFieldNames() lists the parameters of the file, then
loadParams() loads the ones a scenario asked for, as pandas objects indexed by dates in seconds
since epoch, each one passed through AFileMgr.finalizeParam() (date shift, time range, naming).
"""
from __future__ import annotations

import abc
import json
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import yaml

from testsuit.datatools.DataFileMgrs.AFileMgr import AFileMgr
from testsuit.datatools.DataFileMgrs.CsvFileMgr import CsvFileMgr
from testsuit.misc.MonitorProgress import MonitorProgress

ACME_LOGGER_MAGIC = "#ACME-LOGGER"


def _epochSec(date: str | int | float | datetime) -> float:
    """Seconds since epoch of an ISO date (UTC when no time zone is given) or of a number."""
    if isinstance(date, (int, float)):
        return float(date)
    ts = pd.Timestamp(date)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.timestamp()


class ChannelsFileMgr(AFileMgr, metaclass=abc.ABCMeta):
    """Base of the formats read in one go as {channel name: pandas Series indexed by epoch seconds}.

    Subclasses only implement readChannels(); listing and loading parameters is common.
    """

    def __init__(self, filename: str, fileIdx: int = 0) -> None:
        super().__init__(filename, fileIdx)
        self._channels: dict[str, pd.Series] | None = None

    @abc.abstractmethod
    def readChannels(self) -> dict[str, pd.Series]:
        """All the channels of the file, by name, indexed by dates in seconds since epoch."""
        ...

    def channels(self) -> dict[str, pd.Series]:
        if self._channels is None:
            self._channels = self.readChannels()
        return self._channels

    def getFieldNames(self) -> list[str]:
        if self._fieldNamesList is None:
            self._fieldNamesList = list(self.channels())
        return self._fieldNamesList

    def getNbEntries(self) -> int:
        if self._nbEntries is None:
            self._nbEntries = max((len(s) for s in self.channels().values()), default=0)
        return self._nbEntries

    def prepareFile(self, progressCb: MonitorProgress) -> None:
        # nothing to clean in these formats
        return None

    def loadParams(self, paramNamesList: list[str],
                   indexNamesList: list[str] | None = None,
                   monitorProgress: MonitorProgress | None = None,
                   abortEvent: threading.Event | None = None,
                   minDateSec: float | None = None,
                   maxDateSec: float | None = None,
                   callback: Callable[..., Any] | None = None,
                   shiftDateSec: float | str | pd.DataFrame | None = None,
                   shiftDateRegex: str | None = None,
                   shiftDateInverted: bool | None = None,
                   silent: bool = False) -> list:
        if monitorProgress is None:
            monitorProgress = MonitorProgress(name=self.getBaseName())
        monitorProgress.set_total_items(len(paramNamesList))

        rst = []
        for paramName in paramNamesList:
            if abortEvent and abortEvent.is_set():
                raise Exception(f"Received abort event, {self.getFileType()} params extraction interrupted")
            if not silent:
                monitorProgress.msg(msg=[f"extracting {paramName}", f"source file: {self.getBaseName()}",
                                         f"source type: {self.getFileType()}"])

            series = self.channels()[paramName]
            # a copy: finalizeParam() shifts the index in place, the cached channel must stay as read
            dfParam = pd.DataFrame({paramName: series.to_numpy()}, index=series.index.to_numpy(dtype="float64"))
            rst.append(self.finalizeParam(dfParam, name=paramName, indexName="Timestamps/timestamp",
                                          origin=self.getFileName(),
                                          minDateSec=minDateSec, maxDateSec=maxDateSec,
                                          shiftDateSec=shiftDateSec, shiftDateRegex=shiftDateRegex,
                                          shiftDateInverted=shiftDateInverted,
                                          monitorProgress=monitorProgress.child(f"finalize {paramName}"),
                                          silent=silent, callback=callback))
        return rst


class AcmeJsonFileMgr(ChannelsFileMgr):
    """JSON dump of the bench controller (``*.acmej``)::

        {"device": "bench-A", "start": "2026-09-01T10:00:00Z", "rate_hz": 10,
         "channels": {"BenchTemp_degC": [21.0, 21.1, ...], "BenchPressure_bar": [...]}}

    Every channel is sampled at rate_hz from start.
    """

    def getFileType(self) -> str:
        return "acme-json"

    def _content(self) -> dict:
        with open(self.getFileName(), encoding="utf-8") as f:
            return json.load(f)

    def getFileInfo(self) -> list:
        content = self._content()
        return super().getFileInfo() + [f"Device: {content.get('device', '?')}", f"Rate: {content['rate_hz']} Hz"]

    def readChannels(self) -> dict[str, pd.Series]:
        content = self._content()
        start = _epochSec(content["start"])
        period = 1.0 / float(content["rate_hz"])
        return {name: pd.Series(values, index=start + np.arange(len(values)) * period, dtype="float64")
                for name, values in content["channels"].items()}


class SimulatedFileMgr(ChannelsFileMgr):
    """Fake data: a ``*.sim.yml`` file describes signals, generated when the file is read::

        start: 2026-09-01T10:00:00Z
        duration_sec: 60
        rate_hz: 10
        signals:
          ShaftSpeed_rpm: {kind: ramp, from: 0, to: 3000}
          Vibration_g:    {kind: sine, amplitude: 0.4, freq_hz: 0.5, offset: 0}
          OpMode:         {kind: steps, values: [0, 1, 2], every_sec: 20}
          Noise:          {kind: noise, sigma: 0.05, seed: 3}
          Level:          {kind: const, value: 5}

    Signals can be added together with ``plus: [other signal specs]``. Generation is deterministic
    (noise uses its seed), so scenarios written against simulated data give stable results.
    """

    GENERATORS: dict[str, Callable[[dict, np.ndarray], np.ndarray]] = {
        "const": lambda spec, t: np.full(len(t), float(spec["value"])),
        "ramp": lambda spec, t: np.linspace(float(spec["from"]), float(spec["to"]), len(t)),
        "sine": lambda spec, t: float(spec.get("offset", 0)) + float(spec["amplitude"])
                                * np.sin(2 * np.pi * float(spec["freq_hz"]) * t),
        "steps": lambda spec, t: np.asarray(spec["values"], dtype="float64")[
                     np.minimum((t // float(spec["every_sec"])).astype(int), len(spec["values"]) - 1)],
        "noise": lambda spec, t: np.random.default_rng(spec.get("seed", 0)).normal(0, float(spec["sigma"]), len(t)),
    }

    def getFileType(self) -> str:
        return "acme-simulated"

    @classmethod
    def generate(cls, spec: dict, t: np.ndarray) -> np.ndarray:
        kind = spec["kind"]
        if kind not in cls.GENERATORS:
            raise ValueError(f"unknown simulated signal kind '{kind}' (known: {', '.join(cls.GENERATORS)})")
        values = cls.GENERATORS[kind](spec, t)
        for other in spec.get("plus", []):
            values = values + cls.generate(other, t)
        return values

    def readChannels(self) -> dict[str, pd.Series]:
        with open(self.getFileName(), encoding="utf-8") as f:
            desc = yaml.safe_load(f)
        rate = float(desc["rate_hz"])
        t = np.arange(0, float(desc["duration_sec"]) + 1e-9, 1.0 / rate)
        start = _epochSec(desc["start"])
        return {name: pd.Series(self.generate(spec, t), index=start + t)
                for name, spec in desc["signals"].items()}


def isAcmeLoggerCsv(filename: str) -> bool:
    """detect() of the ACME logger CSV variant: its first line starts with #ACME-LOGGER."""
    with open(filename, encoding="utf-8", errors="replace") as f:
        return f.readline().startswith(ACME_LOGGER_MAGIC)


class AcmeLoggerCsvFileMgr(CsvFileMgr):
    """CSV written by the ACME data logger: a metadata line, then a usual CSV file::

        #ACME-LOGGER v1;device=oil-skid-2;operator=jdoe
        Time;OilFlow_lpm;OilLevel_pct
        2026-09-01T10:00:00.000Z;12.1;80.0

    CsvFileMgr already ignores '#' lines when reading values; only the header lookup and the file
    description need to know about the metadata line.
    """

    def getCsvFileType(self) -> str:
        return "acme-logger"

    def metadata(self) -> dict[str, str]:
        with open(self.getFileName(), encoding="utf-8") as f:
            fields = f.readline().strip()[len(ACME_LOGGER_MAGIC):].strip().split(";")
        meta = {"version": fields[0]}
        meta.update(field.split("=", 1) for field in fields[1:] if "=" in field)
        return meta

    def getHeaderLine(self) -> str:
        with open(self.getFileName(), encoding="utf-8") as f:
            for line in f:
                if not line.startswith("#"):
                    return line
        raise Exception(f"no header line in ACME logger file: {self.getFileName()}")

    def getNbEntries(self) -> int:
        # data lines only: the metadata and header lines are not entries
        return super().getNbEntries() - 2

    def getFileInfo(self) -> list:
        return super().getFileInfo() + [f"{key}: {val}" for key, val in self.metadata().items()]
