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


import contextlib
import threading
from concurrent.futures import ThreadPoolExecutor,wait
from typing import Any
from collections.abc import Callable

import numpy as np 
import pandas as pd

from nptdms import TdmsFile
from nptdms.tdmsinfo import tdmsinfo
from nptdms.timestamp import TdmsTimestamp
from unidecode import unidecode

from testsuit.misc.MonitorProgress import MonitorProgress
from testsuit.datatools.DataFileMgrs.AFileMgr import AFileMgr

NB_MAX_WORKERS=6
# a channel is read in about CHANNEL_NB_CHUNKS chunks (of at least MIN_CHUNK_SIZE values) to report load progress
CHANNEL_NB_CHUNKS=50
MIN_CHUNK_SIZE=100_000

# characters replaced in TDMS channel names, applied in one pass by normalizeColumnName()
_COLUMN_NAME_TABLE = str.maketrans({**{c: "_" for c in " ()/\\'*$^[]-.:="}, "°": "o", "&": "n", "%": "pct"})

def normalizeColumnName(name: str) -> str:
    """Turn a TDMS channel path into a plain ASCII column name (ex: "/'Grp 1'/'Temp (°C)'" -> "Grp_1___Temp__oC")."""
    return unidecode(name.translate(_COLUMN_NAME_TABLE)).strip("_")

def _absoluteTimeTrack(channel) -> np.ndarray:
    """Same as nptdms channel.time_track(absolute_time=True), but a missing 'wf_start_offset'
    or 'wf_start_time' property defaults to 0 (with a warning) instead of raising KeyError."""
    try:
        increment = channel.properties['wf_increment']
    except KeyError:
        raise KeyError("Object does not have time properties available.")

    offset = channel.properties.get('wf_start_offset')
    if offset is None:
        offset = 0
        print(f"WARNING: no 'wf_start_offset' property in TDMS object '{channel.path}'")

    relative_time = np.linspace(offset, offset + (len(channel) - 1) * increment, len(channel))

    start_time = channel.properties.get('wf_start_time')
    if start_time is None:
        start_time = 0
        print(f"WARNING: no 'wf_start_time' property in TDMS object '{channel.path}'")
    if isinstance(start_time, TdmsTimestamp):
        start_time = start_time.as_datetime64('ns')

    return start_time + (relative_time * 1e9).astype("timedelta64[ns]")

def readChannelData(channel, chunkCb: Callable[[int], None] | None = None,
                    chunkSize: int | None = None, readLock: threading.Lock | None = None) -> np.ndarray:
    """Values of a TDMS channel opened in streaming mode (TdmsFile.open), read in chunks so that
    chunkCb(nbValues) can report progress (and raise to abort) after each chunk.

    :param channel: channel of a TdmsFile opened with TdmsFile.open()
    :param chunkCb: called after each chunk with the number of values read
    :param chunkSize: values per chunk, by default about CHANNEL_NB_CHUNKS chunks of at least MIN_CHUNK_SIZE values
    :param readLock: held during each chunk read, when the file handle is shared between threads
    """
    readLock = readLock or contextlib.nullcontext()
    nbValues = len(channel)
    if chunkSize is None:
        chunkSize = max(MIN_CHUNK_SIZE, -(-nbValues // CHANNEL_NB_CHUNKS))
    chunks = []
    for offset in range(0, nbValues, chunkSize):
        length = min(chunkSize, nbValues - offset)
        with readLock:
            chunks.append(channel.read_data(offset, length))
        if chunkCb:
            chunkCb(length)
    if not chunks:
        with readLock:
            data = channel.read_data()
    else:
        data = chunks[0] if len(chunks) == 1 else np.concatenate(chunks)
    if np.issubdtype(data.dtype, np.dtype('void')) and len(data.dtype) == 0:
        data = np.empty(0, dtype='float64')
    return data

def channelAsSeries(channel, chunkCb: Callable[[int], None] | None = None,
                    readLock: threading.Lock | None = None) -> pd.Series:
    """Values of a TDMS channel (opened with TdmsFile.open) indexed by absolute time, read in chunks (see readChannelData)."""
    data = readChannelData(channel, chunkCb, readLock=readLock)
    return pd.Series(data=data, index=_absoluteTimeTrack(channel))

class TdmsFileMgr(AFileMgr):
    def __init__(self, filename: str, fileIdx: int) -> None:
        super().__init__(filename,fileIdx)
        self.__tdmsfile=TdmsFile(filename,read_metadata_only=True)
        # normalized field name -> (group name, channel name), filled by getFieldNames()
        self.__channelKeys: dict[str, tuple[str, str]] = {}
        self.showWarningDateOrigin=False

    def showTdmsInfo(self):
        tdmsinfo(self.getFileName(),show_properties=True)

    def getFileType(self) -> str:
        return "tdms"
    
    def getNbEntries(self) -> int:
        if self._nbEntries is None:
            self._nbEntries = 1
            
        return self._nbEntries

    def toHtmlTbl(self) -> str:
        htmlTbl=super().toHtmlTbl()
        
        return htmlTbl

    def getFieldNames(self) -> list[str]:
        if self._fieldNamesList is None:
            self._fieldNamesList=[]
            for group in self.__tdmsfile.groups():
                for channel in group.channels():
                    fieldName=normalizeColumnName(channel.path)
                    self._fieldNamesList.append(fieldName)
                    self.__channelKeys[fieldName]=(group.name,channel.name)
                    
        return self._fieldNamesList

    # prepare / adapt header (col names mainly) to keep it comatible with main 3rd party tools
    def prepareFile(self, progressCb: MonitorProgress) -> None:
        # nothing to do for this format
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
        
        self.getFieldNames()
        rstDataframes=[]
        extractedParamsLock=threading.Lock()
        readLock=threading.Lock()   # one file handle shared by the workers, read chunk by chunk

        def loadParamFromTdmsWorker(tdmsFile: TdmsFile, paramName: str, mpParam: MonitorProgress) -> None:
            if abortEvent and abortEvent.is_set():
                raise Exception("Received abort event, TDMS params extraction interrupted")

            if not silent:
                mpParam.msg(msg=[f"extracting {paramName}", f"source file: {self.getBaseName()}",
                                 f"source type: {self.getFileType()}"])

            try:
                groupName,channelName=self.__channelKeys[paramName]
                channel=tdmsFile[groupName][channelName]
                mpLoad=mpParam.child("load",max(len(channel),1))

                def chunkCb(nbValues: int) -> None:
                    if abortEvent is not None and abortEvent.is_set():
                        raise Exception("Received abort event, TDMS params extraction interrupted")
                    mpLoad.complete_n(nbValues)

                series=channelAsSeries(channel,chunkCb,readLock=readLock)
                if len(channel)==0:
                    mpLoad.complete_n(1)
                dfParam=pd.DataFrame({paramName: series})

                # convert datetime to nb seconds since epoch
                dfParam.index=dfParam.index.values.astype("float64")/1e9
                if len(dfParam.index)>0 and dfParam.index[0]==0:
                    if "Date Created" in tdmsFile.properties:
                        creationDate=tdmsFile.properties["Date Created"]
                        creationDateSec=(creationDate - np.datetime64('1970-01-01T00:00:00'))/ np.timedelta64(1, 's')
                        dfParam.index+=creationDateSec
                        if not self.showWarningDateOrigin:
                            self.showWarningDateOrigin=True
                            mpParam.msg(msg=[self.getBaseName(),"Could not detect dates origin, used 'Date Created' property instead."])
                    else:
                        if not self.showWarningDateOrigin:
                            self.showWarningDateOrigin=True
                            mpParam.msg(msg=[self.getBaseName(),"Could not detect dates origin."])

                with extractedParamsLock:
                    rstDataframes.append(self.finalizeParam(dfParam,
                            name=paramName,
                            indexName="Timestamps/timestamp",
                            origin=self.getFileName(),
                            minDateSec=minDateSec,maxDateSec=maxDateSec,
                            shiftDateSec=shiftDateSec,shiftDateRegex=shiftDateRegex,shiftDateInverted=shiftDateInverted,
                            monitorProgress=mpParam.child("finalize"),
                            silent=silent,callback=callback))

            except Exception as e:
                raise Exception(f"unable to load param '{paramName}' from {self.getBaseName()}: "+str(e))

        monitorProgress.set_total_items(len(paramNamesList))
        # totals set upfront: a monitor without total yet makes the overall percent unknown (None)
        # 2 steps per param: load contents + finalize/callback (child created once loaded, for the same reason)
        mpParams=[monitorProgress.child(f"loading {paramName}", total_sub_items=2, renameIfExist=True)
                  for paramName in paramNamesList]

        # streaming mode: only the requested channels are read, and the file is read once for all workers
        with TdmsFile.open(self.getFileName()) as tdmsFile:
            with ThreadPoolExecutor(max_workers=NB_MAX_WORKERS) as executor:
                futures = [executor.submit(loadParamFromTdmsWorker, tdmsFile, paramName, mpParam)
                           for paramName,mpParam in zip(paramNamesList,mpParams)]
                wait(futures)
                for fut in futures:
                    fut.result()

        return rstDataframes
