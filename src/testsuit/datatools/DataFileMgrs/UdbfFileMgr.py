
"""Handler for Gantner Instruments .dat binary data files via ginsapy highspeedport_client.
  
"""

import datetime as dt
import threading
import numpy as np
import pandas as pd
import re
from typing import Any
from collections.abc import Callable

from testsuit.misc.logger import get_logger
from testsuit.misc.MonitorProgress import MonitorProgress
from testsuit.datatools.DataFileMgrs.AFileMgr import AFileMgr


def _ole2datetime(oledt: float) -> float:
    """Convert an OLE Automation date (days since 1899-12-30) to a Unix timestamp in seconds."""
    base = dt.datetime(1899, 12, 30, tzinfo=dt.timezone.utc)
    return (base + dt.timedelta(days=float(oledt))).timestamp()


class UdbfFileMgr(AFileMgr):
    """Read Gantner Instruments UDBF .dat files using the ginsapy highspeedport_client connector."""

    def __init__(self, filename: str, fileIdx: int) -> None:
        super().__init__(filename, fileIdx)
        self._conn = None
        self._datArray = None
        self._channelNames = None
        self._channelUnits = None
        self._sampleRate = None
        self._timestampColIdx = None
        self._dataColIdxs = None
        self._initFile()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _initFile(self) -> None:
        """Open the UDBF .dat file, read metadata and full data matrix."""
        import ginsapy.giutility.connect.highspeedport_client as highspeedport_client

        try:
            self._conn = highspeedport_client.HighSpeedPortClient()
            self._conn.init_file(self.getFileName())
            self._conn.read_channel_names()

            max_channel = int(self._conn.read_channel_count())
            self._sampleRate = self._conn.read_sample_rate()

            # Read channel names and units
            self._channelNames = []
            self._channelUnits = []
            
            for i in range(max_channel):
                self._channelNames.append(self._conn.read_index_name(i))
                self._channelUnits.append(self._conn.read_index_unit(i))

            # Detect timestamp column (first column whose name contains 'time' or 'date')
            self._timestampColIdx = None
            for idx, name in enumerate(self._channelNames):
                if re.search(r"(time|date)", name, re.IGNORECASE):
                    self._timestampColIdx = idx
                    break
            # Fallback: first column
            if self._timestampColIdx is None:
                self._timestampColIdx = 0

            # Remaining columns are data channels
            self._dataColIdxs = [i for i in range(max_channel) if i != self._timestampColIdx]


            self._datArray = highspeedport_client.read_gins_dat(self._conn)
        except Exception as exc:            
            self._conn.close_connection()
            raise RuntimeError(f"Unable to import .dat file '{self.getFileName()}': {exc}") from exc

        get_logger().debug(
            "Gantner .dat File - %s - %d rows x %d cols",
            self.getBaseName(),
            self._datArray.shape[0],
            self._datArray.shape[1],
        )

    # ------------------------------------------------------------------
    # AFileMgr overrides
    # ------------------------------------------------------------------

    def getFileType(self) -> str:
        return "gantner-udbf"

    def getNbEntries(self) -> int:
        if self._nbEntries is None:
            self._nbEntries = self._datArray.shape[0]
        return self._nbEntries

    def getFileInfo(self) -> list[str]:
        fileInfo = super().getFileInfo()
        return fileInfo + [
            "Sample rate: " + str(self._sampleRate) + " Hz",
            "Timestamp column: " + self._channelNames[self._timestampColIdx],
        ]

    def toHtmlTbl(self) -> str:
        htmlTbl = super().toHtmlTbl()
        htmlTbl += '<tr><th>Sample Rate</th><td>' + str(self._sampleRate) + ' Hz</td></tr>'
        htmlTbl += '<tr><th>Timestamp Column</th><td>' + self._channelNames[self._timestampColIdx] + '</td></tr>'
        return htmlTbl

    def getFieldNames(self) -> list[str]:
        """Return data channel names (excludes the timestamp column)."""
        if self._fieldNamesList is None:
            self._fieldNamesList=[]
            for i in self._dataColIdxs:
                chName=self._channelNames[i]
                if len(chName)==0: 
                    get_logger().warn(f"Channel {i} has no proper name, dropped.")
                    continue
                self._fieldNamesList.append(chName)
        return self._fieldNamesList

    def prepareFile(self, monitorProgress: MonitorProgress) -> None:
        # Nothing to do for .dat files
        return None

    def loadParams(self,
                paramNamesList: list[str],
                indexNamesList: list[str] | None = None,
                monitorProgress: MonitorProgress | None = None,
                abortEvent: threading.Event | None = None,
                minDateSec: float | None = None,
                maxDateSec: float | None = None,
                callback: Callable[..., Any] | None = None,
                shiftDateSec: float | None = None,
                shiftDateRegex: str | None = None,
                shiftDateInverted: bool | None = None,
                silent: bool = False) -> list[pd.DataFrame]:
        """Extract requested channels as Pandas DataFrames.

        Timestamps are converted from OLE Automation dates to Unix seconds (float64).
        """

        rst = []

        if len(paramNamesList) == 0:
            raise Exception(self.getBaseName() + ": list of params to load is empty")

        # .dat files carry their own timestamp column; explicit index is not supported
        if indexNamesList is not None and len(indexNamesList) > 0:
            raise ValueError("Explicit index names are not supported for Gantner .dat files.")

        # Pre-compute timestamp index (Unix seconds)
        timestamps = np.array([_ole2datetime(self._datArray[row, self._timestampColIdx])
                               for row in range(self._datArray.shape[0])])

        monitorProgress.set_total_items(len(paramNamesList))

        for paramName in paramNamesList:
            if abortEvent and abortEvent.is_set():
                raise Exception("Received abort event, .dat params extraction interrupted")

            # Resolve column index for this parameter
            if paramName not in self._channelNames:
                raise Exception(
                    f"Parameter '{paramName}' not found in .dat file '{self.getBaseName()}'. "
                    f"Available: {self._channelNames}"
                )

            colIdx = self._channelNames.index(paramName)

            # Skip if the requested param is the timestamp column itself
            if colIdx == self._timestampColIdx:
                monitorProgress.complete_item(paramName)
                continue

            if not silent:
                monitorProgress.msg(
                    msg=[f"extracting {paramName}", f"source file: {self.getBaseName()}",
                     f"source type: {self.getFileType()}"]
                    )

            try:
                values = self._datArray[:, colIdx].astype(np.float64)
                dfParam = pd.DataFrame({paramName: values}, index=timestamps)
                             
                rst.append(self.finalizeParam(dfParam,
                            name=paramName,
                            indexName="Timestamps/"+ self._channelNames[self._timestampColIdx],
                            origin=self.getFileName(),
                            minDateSec=minDateSec,maxDateSec=maxDateSec,
                            shiftDateSec=shiftDateSec,shiftDateRegex=shiftDateRegex,shiftDateInverted=shiftDateInverted,
                            monitorProgress=monitorProgress.child(f"finalize {paramName}"),
                            silent=silent,callback=callback))

            except Exception as e:
                raise Exception(
                    f"Unable to extract param '{paramName}' from .dat file '{self.getFileName()}': {str(e)}"
                )

        return rst