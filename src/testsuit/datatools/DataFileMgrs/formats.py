"""Registry of the data file formats FolderParamMgr can load.

A format maps file extensions to a factory ``factory(filename, fileIdx) -> AFileMgr`` (usually an
AFileMgr subclass). An optional ``accepts(filename) -> bool`` narrows it to some of the files with
these extensions (by name or content); when it refuses a file, the next format is tried.
Formats registered by external code are tried before the built-in ones, so they can also replace
how a built-in extension is read.

CSV files (.csv, .txt, .log) come in several syntaxes: the first CSV variant whose
``detect(filename)`` returns True picks the file manager, the generic CsvFileMgr otherwise.
External variants are tried before the built-in ones.

Example, from an external library::

    from testsuit.datatools.DataFileMgrs.formats import register_file_format, register_csv_variant
    from testsuit.datatools.DataFileMgrs.CsvFileMgr import CsvFileMgr

    register_file_format("myformat", ["myf"], MyFormatFileMgr)
    register_csv_variant("mylogger", lambda f: open(f).readline().startswith("#MYLOGGER"), MyLoggerCsvFileMgr)

See testsuit.plugins to make these registrations visible to the command-line tools.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from testsuit.datatools.DataFileMgrs.AFileMgr import AFileMgr

FileMgrFactory = Callable[[str, int], "AFileMgr"]


@dataclass(frozen=True)
class FileFormat:
    name: str
    extensions: tuple[str, ...]
    factory: FileMgrFactory
    accepts: Callable[[str], bool] | None = None

    def matches(self, filename: str) -> bool:
        lowerName = filename.lower()
        if not any(lowerName.endswith("." + ext.lower()) for ext in self.extensions):
            return False
        return self.accepts is None or bool(self.accepts(filename))


@dataclass(frozen=True)
class CsvVariant:
    name: str
    detect: Callable[[str], bool]
    factory: FileMgrFactory


# extensions (without leading dot) of all registered formats, in registration order.
# Same list object as datatoolbox.SUPPORTED_DATAFILE_EXTENSIONS: registering a format updates it.
SUPPORTED_DATAFILE_EXTENSIONS: list[str] = []

_formats: list[FileFormat] = []
_csvVariants: list[CsvVariant] = []


def _normalizeExtensions(extensions: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(extensions, str):
        extensions = [extensions]
    return tuple(ext.lstrip(".") for ext in extensions)


def _addSupportedExtensions(extensions: tuple[str, ...]) -> None:
    known = {ext.lower() for ext in SUPPORTED_DATAFILE_EXTENSIONS}
    for ext in extensions:
        if ext.lower() not in known:
            SUPPORTED_DATAFILE_EXTENSIONS.append(ext)
            known.add(ext.lower())


def register_file_format(name: str, extensions: str | list[str] | tuple[str, ...],
                         factory: FileMgrFactory, accepts: Callable[[str], bool] | None = None,
                         builtin: bool = False) -> FileFormat:
    """Declare a data file format.

    :param name: format name (for messages; registering a name again replaces that format)
    :param extensions: file extension(s), with or without leading dot, matched case-insensitively
        on the end of the file name (so "influxdbV3.yml" works)
    :param factory: ``factory(filename, fileIdx)`` returning the file manager, typically an AFileMgr subclass
    :param accepts: optional ``accepts(filename) -> bool``, to handle only some files with these extensions
    :param builtin: for testsuit's own formats: tried after all the others
    :return: the registered format
    """
    fmt = FileFormat(name, _normalizeExtensions(extensions), factory, accepts)
    _formats[:] = [f for f in _formats if f.name != name]
    if builtin:
        _formats.append(fmt)
    else:
        # external formats first, latest registered first
        _formats.insert(0, fmt)
    _addSupportedExtensions(fmt.extensions)
    return fmt


def register_csv_variant(name: str, detect: Callable[[str], bool], factory: FileMgrFactory,
                         builtin: bool = False) -> CsvVariant:
    """Declare a CSV syntax, read by its own file manager (typically a CsvFileMgr subclass).

    :param name: variant name, returned by CsvFileMgr.GetCsvFileType() (registering a name again replaces it)
    :param detect: ``detect(filename) -> bool``, True when the file uses this syntax (usually from its first line)
    :param factory: ``factory(filename, fileIdx)`` returning the file manager
    :param builtin: for testsuit's own variants: tried after all the others
    :return: the registered variant
    """
    variant = CsvVariant(name, detect, factory)
    _csvVariants[:] = [v for v in _csvVariants if v.name != name]
    if builtin:
        _csvVariants.append(variant)
    else:
        _csvVariants.insert(0, variant)
    
    originStr="builtin" if builtin else "extension"
    logging.getLogger("testsuit").info(f"registered {originStr} CSV variant '{name}'")
    return variant


def unregister_file_format(name: str) -> None:
    """Remove a format registered with register_file_format() (its extensions stay supported)."""
    _formats[:] = [f for f in _formats if f.name != name]


def unregister_csv_variant(name: str) -> None:
    """Remove a variant registered with register_csv_variant()."""
    _csvVariants[:] = [v for v in _csvVariants if v.name != name]


def get_file_formats() -> list[FileFormat]:
    """Registered formats, in the order they are tried."""
    return list(_formats)


def get_csv_variants() -> list[CsvVariant]:
    """Registered CSV variants, in the order they are tried."""
    return list(_csvVariants)


def find_file_format(filename: str) -> FileFormat | None:
    """First registered format handling given file, None if none does."""
    return next((fmt for fmt in _formats if fmt.matches(filename)), None)


def find_csv_variant(filename: str) -> CsvVariant | None:
    """First registered CSV variant detecting given file, None for a generic CSV file."""
    return next((v for v in _csvVariants if v.detect(filename)), None)


def create_file_mgr(filename: str, fileIdx: int = 0) -> AFileMgr:
    """Create the file manager of given file, from the registered formats.

    :raises Exception: if no registered format handles the file
    """
    fmt = find_file_format(filename)
    if fmt is None:
        raise Exception("[FolderParamMgr] unhandle file type: " + filename)
    return fmt.factory(filename, fileIdx)


########################## built-in formats ##########################
# factories import their module on use, so that optional dependencies are only needed
# when a file of that format is actually loaded

def _firstLine(filename: str) -> str:
    with open(filename) as f:
        return f.readline().strip('\n')


def _csvFactory(filename: str, fileIdx: int) -> AFileMgr:
    variant = find_csv_variant(filename)
    if variant is not None:
        logging.getLogger("testsuit").info(f"loading file CSV {filename} as {variant}")
        return variant.factory(filename, fileIdx)
    from testsuit.datatools.DataFileMgrs.CsvFileMgr import CsvFileMgr
    logging.getLogger("testsuit").info(f"loading file CSV {filename} as generic CSV file")
    return CsvFileMgr(filename, fileIdx)


def _csvInfluxDb(filename: str, fileIdx: int) -> AFileMgr:
    from testsuit.datatools.DataFileMgrs.CsvFileMgr import CsvFileMgrInfluxDb
    return CsvFileMgrInfluxDb(filename, fileIdx)


def _csvChannelsPcapRecorder(filename: str, fileIdx: int) -> AFileMgr:
    from testsuit.datatools.DataFileMgrs.CsvFileMgr import CsvFileMgrChannelsPcapRecorder
    return CsvFileMgrChannelsPcapRecorder(filename, fileIdx)


def _csvChannels(filename: str, fileIdx: int) -> AFileMgr:
    from testsuit.datatools.DataFileMgrs.CsvFileMgr import CsvFileMgrChannels
    return CsvFileMgrChannels(filename, fileIdx)


def _h5Factory(filename: str, fileIdx: int) -> AFileMgr:
    from testsuit.datatools.DataFileMgrs.H5FileMgr import GetH5FileType, H5FileMgr, H5FileMgrDewesoft, H5FileMgrFES, H5FileMgrChannels
    h5FileType = GetH5FileType(filename)
    if h5FileType == "Dewesoft":
        return H5FileMgrDewesoft(filename, fileIdx)
    if h5FileType == "FES":
        return H5FileMgrFES(filename, fileIdx)
    if h5FileType == "channels":
        return H5FileMgrChannels(filename, fileIdx)
    return H5FileMgr(filename, fileIdx)


def _dxdFactory(filename: str, fileIdx: int) -> AFileMgr:
    from testsuit.datatools.DataFileMgrs.DxdFileMgr import DxdFileMgr
    return DxdFileMgr(filename, fileIdx)


def _tdmsFactory(filename: str, fileIdx: int) -> AFileMgr:
    from testsuit.datatools.DataFileMgrs.TdmsFileMgr import TdmsFileMgr
    return TdmsFileMgr(filename, fileIdx)


def _mdfFactory(filename: str, fileIdx: int) -> AFileMgr:
    from testsuit.datatools.DataFileMgrs.MdfFileMgr import MdfFileMgr
    return MdfFileMgr(filename, fileIdx)


def _influxDbV3Factory(filename: str, fileIdx: int) -> AFileMgr:
    from testsuit.datatools.DataFileMgrs.InfluxDbFileMgr import InfluxDbV3FileMgr
    return InfluxDbV3FileMgr(filename, fileIdx)


def _influxDbV2Factory(filename: str, fileIdx: int) -> AFileMgr:
    from testsuit.datatools.DataFileMgrs.InfluxDbFileMgr import InfluxDbV2FileMgr
    return InfluxDbV2FileMgr(filename, fileIdx)


def _udbfFactory(filename: str, fileIdx: int) -> AFileMgr:
    from testsuit.datatools.DataFileMgrs.UdbfFileMgr import UdbfFileMgr
    return UdbfFileMgr(filename, fileIdx)


def _isGantnerDatFile(filename: str) -> bool:
    return re.match(r"^.*_\d+_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d{6}", os.path.basename(filename)) is not None


register_file_format("hdf5", ["h5", "hdf5"], _h5Factory, builtin=True)
register_file_format("dewesoft", ["dxd", "d7d"], _dxdFactory, builtin=True)
register_file_format("csv", ["csv", "txt", "log"], _csvFactory, builtin=True)
register_file_format("tdms", ["tdms"], _tdmsFactory, builtin=True)
register_file_format("mdf", ["mdf", "mf4"], _mdfFactory, builtin=True)
register_file_format("influxdbV3", ["influxdbV3.yml", "influxdbV3.yaml"], _influxDbV3Factory, builtin=True)
register_file_format("influxdbV2", ["influxdbV2.yml", "influxdbV2.yaml"], _influxDbV2Factory, builtin=True)
# Gantner .dat files
register_file_format("gantner-udbf", ["dat"], _udbfFactory, accepts=_isGantnerDatFile, builtin=True)

register_csv_variant("influxdb", lambda f: _firstLine(f).startswith("#datatype"), _csvInfluxDb, builtin=True)
register_csv_variant("channels-pcap-recorder", lambda f: "rawVal;engVal;" in _firstLine(f), _csvChannelsPcapRecorder, builtin=True)
register_csv_variant("channels", lambda f: "date;channel;" in _firstLine(f), _csvChannels, builtin=True)
