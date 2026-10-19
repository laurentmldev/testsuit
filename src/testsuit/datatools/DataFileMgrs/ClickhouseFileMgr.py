import yaml
import re
import threading
from typing import Any
from collections.abc import Callable

import numpy as np
import pandas as pd

from testsuit.misc.logger import get_logger
from testsuit.misc.MonitorProgress import MonitorProgress

from testsuit.datatools.DataFileMgrs.AFileMgr import AFileMgr


class ClickhouseFileMgr(AFileMgr):
    """Uses a YML description ('*.clickhouse.yml') as a pseudo data file to access ClickHouse tables contents.

    Data is read with the 'clickhouse_driver' package (native protocol, default port 9000).

    Two table layouts are handled:
    - wide (default, as written by data2db 'clickhouse'): one timestamp column, one column per param.
      Rows where a param column is NULL are ignored for that param. Params are named '<table>.<column>'.
    - long (when 'paramNamesKey' is set): one row per sample, param name in column 'paramNamesKey',
      value in column 'paramValuesKey' (default 'value'). Params are named '<table>.<param name>'.

    Example of config file:
        host: clickhouse           # or url: http://clickhouse:9000 (same as data2db conf)
        port: 9000                 # OPTIONAL
        user: default              # OPTIONAL
        password: ...              # OPTIONAL
        database: testDb
        table: testTable           # regex, might match several tables of the database
        timestampColumn: ts        # OPTIONAL
        paramNamesKey: name        # OPTIONAL (long layout)
        paramValuesKey: value      # OPTIONAL (long layout)
    """

    DEFAULT_PORT = 9000
    FETCH_BATCH_SIZE = 1000000

    def __init__(self, filename: str, fileIdx: int) -> None:

        super().__init__(filename, fileIdx)
        self._host = None
        self._port = self.DEFAULT_PORT
        self._user = "default"
        self._password = None
        self._database = None
        self._table = None
        self._tsCol = "ts"
        self._paramNamesKey = None
        self._paramValuesKey = "value"
        self._client = None
        # field name -> (table, column, param name in long layout or None)
        self._fields = None

        try:
            with open(self.getFileName()) as f:
                config = yaml.safe_load(f) or {}
            self._host = config.get('host')
            if config.get('url'):
                m = re.match(r"(?:\w+://)?([^:/]+)(?::(\d+))?", str(config['url']))
                self._host = m.group(1)
                if m.group(2): self._port = int(m.group(2))
            self._port = int(config.get('port', self._port))
            self._user = config.get('user', self._user)
            self._password = config.get('password')
            self._database = config.get('database')
            self._table = config.get('table')
            self._tsCol = config.get('timestampColumn', self._tsCol)
            self._paramNamesKey = config.get('paramNamesKey')
            self._paramValuesKey = config.get('paramValuesKey', self._paramValuesKey)
        except FileNotFoundError:
            get_logger().warning(f"Config file not found: {self.getFileName()}")
        except yaml.YAMLError as e:
            get_logger().warning(f"Failed to parse YAML config: {e}")
        except Exception as e:
            get_logger().warning(f"Failed to load ClickHouse config from file: {e}")

    @staticmethod
    def _ident(name: str) -> str:
        """Return name as a safely-escaped ClickHouse identifier"""
        return "`" + str(name).replace("\\", "\\\\").replace("`", "\\`") + "`"

    def _tableIdent(self, table: str) -> str:
        return self._ident(self._database) + "." + self._ident(table)

    def _connect(self) -> None:
        """
        Establish connection to ClickHouse if not already connected.
        """
        if self._client is None:
            if not all([self._host, self._database]):
                raise Exception(
                    f"ClickHouse connection parameters not fully configured. "
                    f"host={self._host}, database={self._database}"
                )
            from clickhouse_driver import Client
            self._client = Client(host=self._host,
                                  port=self._port,
                                  user=self._user,
                                  password=self._password or "",
                                  database=self._database)

    def close(self) -> None:
        """Close the ClickHouse connection if open (idempotent, safe to call several times)."""
        client = getattr(self, "_client", None)
        if client is None:
            return
        self._client = None
        try:
            client.disconnect()
        except Exception as e:
            get_logger().warning(f"Failed to close ClickHouse client: {e}")

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _execute(self, query: str, params: dict | None = None, columnar: bool = False) -> list:
        self._connect()
        try:
            return self._client.execute(query, params, columnar=columnar)
        except Exception as e:
            raise Exception(f"ERROR while running query \n-----\n{query}\n-----\n: {e}")

    def _get_matching_tables(self) -> list[str]:
        """
        Tables of the database whose name matches the 'table' regex of the config.
        """
        try:
            rows = self._execute("SELECT name FROM system.tables WHERE database = %(db)s ORDER BY name", {"db": self._database})
        except Exception as e:
            get_logger().warning(f"Failed to query table names from ClickHouse. Please check database is reachable: {e}")
            raise e

        pattern = self._table if self._table else '.*'
        return [row[0] for row in rows if re.fullmatch(pattern, row[0])]

    def getFileType(self) -> str:
        return "clickhouse"

    def getFileInfo(self) -> list[str]:
        fileInfo = AFileMgr.getFileInfo(self)
        return fileInfo + [f"Host: {self._host}:{self._port}",
                           f"Database: {self._database}",
                           self._tableInfo()]

    def toHtmlTbl(self) -> str:
        htmlTbl = AFileMgr.toHtmlTbl(self)
        htmlTbl += f"<tr><th>Host</th><td>{self._host}:{self._port}</td></tr>"
        htmlTbl += f"<tr><th>Database</th><td>{self._database}</td></tr>"
        htmlTbl += f"<tr><th>Table</th><td>{self._tableInfo()[len('Table: '):]}</td></tr>"
        return htmlTbl

    def _tableInfo(self) -> str:
        matching = self._get_matching_tables()
        table_info = f"Table: {self._table}"
        if len(matching) > 1:
            table_info += f" (matches {len(matching)}: {', '.join(matching[:3])}{'...' if len(matching) > 3 else ''})"
        elif len(matching) == 1:
            table_info += f" (resolved to: {matching[0]})"
        else:
            table_info += " (no matches found)"
        return table_info

    def getNbEntries(self) -> int:
        """
        Number of rows of the largest matching table (not cumulative).
        """
        if self._nbEntries is None:
            try:
                counts = [self._execute(f"SELECT count() FROM {self._tableIdent(table)}")[0][0]
                          for table in self._get_matching_tables()]
                self._nbEntries = max(counts, default=0)
            except Exception as e:
                get_logger().warning(f"Failed to get entry count from ClickHouse: {e}")
                self._nbEntries = 0

        return self._nbEntries

    def _getFields(self) -> dict[str, tuple[str, str, str | None]]:
        """field name -> (table, column, param name in long layout or None)"""
        if self._fields is None:
            fields = {}
            for table in self._get_matching_tables():
                if self._paramNamesKey:
                    rows = self._execute(f"SELECT DISTINCT toString({self._ident(self._paramNamesKey)}) AS n "
                                         f"FROM {self._tableIdent(table)} ORDER BY n")
                    for (paramName,) in rows:
                        fields[f"{table}.{paramName}"] = (table, self._paramValuesKey, paramName)
                else:
                    rows = self._execute("SELECT name FROM system.columns WHERE database = %(db)s AND table = %(table)s ORDER BY position",
                                         {"db": self._database, "table": table})
                    for (col,) in rows:
                        if col != self._tsCol:
                            fields[f"{table}.{col}"] = (table, col, None)
            self._fields = fields
        return self._fields

    def getFieldNames(self) -> list[str]:
        """
        '<table>.<column>' (wide layout) or '<table>.<param name>' (long layout) of all matching tables.
        """
        if self._fieldNamesList is None:
            try:
                self._fieldNamesList = list(self._getFields())
            except Exception as e:
                get_logger().warning(f"Failed to get field names from ClickHouse: {e}")
                self._fieldNamesList = []

        return self._fieldNamesList

    def loadParams(self,
                   paramNamesList: list[str],
                   indexNamesList: list[str] | None = None,
                   monitorProgress: MonitorProgress | None = None,
                   abortEvent: threading.Event | None = None,
                   callback: Callable[..., Any] | None = None,
                   minDateSec: float | None = None,
                   maxDateSec: float | None = None,
                   shiftDateSec: float | None = None,
                   shiftDateRegex: str | None = None,
                   shiftDateInverted: bool | None = None,
                   silent: bool = False) -> list[pd.DataFrame]:
        """
        Load parameters from ClickHouse tables. The min/max dates are applied in the queries.

        :param minDateSec (float): minimal date in seconds since epoch 1970-01-01
        :param maxDateSec (float): maximal date in seconds since epoch 1970-01-01
        """
        rst = []

        if len(paramNamesList) == 0:
            raise Exception(self.getBaseName() + ": list of params to load is empty")

        if indexNamesList is not None and len(indexNamesList) > 0:
            raise Exception("indexNamesList is not supported for ClickHouse extract")

        fields = self._getFields()
        tsCol = self._ident(self._tsCol)

        # min/max dates apply to clock-corrected dates (see finalizeParam): they are filtered in the query
        # (on the raw timestamp column, so that the table ordering key is used) only when the correction
        # is known here, i.e. none or a constant offset applied to all params. Otherwise finalizeParam filters.
        timeConditions = []
        if shiftDateSec is None or (not isinstance(shiftDateSec, pd.DataFrame) and not shiftDateRegex):
            offsetSec = (-shiftDateSec if shiftDateInverted else shiftDateSec) if shiftDateSec else 0
            if minDateSec is not None:
                timeConditions.append(f"{tsCol} >= fromUnixTimestamp64Micro(toInt64({int(round((minDateSec-offsetSec)*1e6))}))")
            if maxDateSec is not None:
                timeConditions.append(f"{tsCol} <= fromUnixTimestamp64Micro(toInt64({int(round((maxDateSec-offsetSec)*1e6))}))")

        if len(paramNamesList) > 1 and not silent:
            monitorProgress.msg(msg=[f"extracting {len(paramNamesList)} params", f"source file: {self.getBaseName()}",
                                     f"source type: {self.getFileType()}"])

        monitorProgress.set_total_items(len(paramNamesList))

        for paramFinalName in paramNamesList:
            if abortEvent and abortEvent.is_set():
                raise Exception("Received abort event, ClickHouse params extraction interrupted")

            if paramFinalName not in fields:
                raise Exception(f"no such param '{paramFinalName}' in {self.getBaseName()}")
            table, column, paramName = fields[paramFinalName]

            whereConditions = [f"{self._ident(column)} IS NOT NULL"] + timeConditions
            params = {}
            if paramName is not None:
                whereConditions.append(f"toString({self._ident(self._paramNamesKey)}) = %(paramName)s")
                params["paramName"] = paramName
            whereClause = " AND ".join(whereConditions)

            total_entries = self._execute(f"SELECT count() FROM {self._tableIdent(table)} WHERE {whereClause}", params)[0][0]

            subMp = monitorProgress.child(f"db-extract {paramFinalName}", total_entries + 1,  # +1 for finalizeParam
                                          allowOverTotalItems=True)

            query = f"SELECT toUnixTimestamp64Micro(toDateTime64({tsCol}, 6)), {self._ident(column)} " \
                    f"FROM {self._tableIdent(table)} WHERE {whereClause} ORDER BY {tsCol}"

            # fetched in batches, to report progress and be able to abort
            tsChunks, valChunks = [], []
            offset = 0
            while offset < total_entries:
                if abortEvent and abortEvent.is_set():
                    raise Exception("Received abort event, ClickHouse params extraction interrupted")

                columns = self._execute(f"{query} LIMIT {self.FETCH_BATCH_SIZE} OFFSET {offset}", params, columnar=True)
                if len(columns) == 0 or len(columns[0]) == 0:
                    break
                tsChunks.append(np.asarray(columns[0], dtype="int64"))
                valChunks.append(np.asarray(columns[1]))
                offset += len(columns[0])
                subMp.complete_n(len(columns[0]))

            if offset == 0:
                get_logger().warning(f"No data found for field '{paramFinalName}'")
                subMp.complete_all()
                continue

            dfParam = pd.DataFrame({paramFinalName: np.concatenate(valChunks)},
                                   index=np.concatenate(tsChunks).astype("float64") / 1e6)

            rst.append(self.finalizeParam(dfParam,
                name=paramFinalName,
                indexName="Timestamps/timestamp_"+paramFinalName,
                origin=self.getFileName(),
                minDateSec=minDateSec, maxDateSec=maxDateSec,
                shiftDateSec=shiftDateSec, shiftDateRegex=shiftDateRegex, shiftDateInverted=shiftDateInverted,
                silent=silent, callback=callback,
                monitorProgress=subMp.child(f"finalize {paramFinalName}")))

        # release the connection: the client is re-created lazily by _connect() on next use
        self.close()

        return rst
