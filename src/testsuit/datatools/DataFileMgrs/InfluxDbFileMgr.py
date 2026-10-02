import yaml
import pandas as pd
import threading
from typing import Any
from collections.abc import Callable

from testsuit.misc.logger import get_logger
from testsuit.misc.MonitorProgress import MonitorProgress

from testsuit.datatools.DataFileMgrs.AFileMgr import AFileMgr

import re

class InfluxDbV3FileMgr(AFileMgr):
    """Uses a YML description as a pseudo data file to access DB contents."""
    def __init__(self, filename: str, fileIdx: int) -> None:
        
        super().__init__(filename, fileIdx)
        self._host = None
        self._token = None
        self._org = None
        self._database = None  # Changed from _bucket to _database
        self._table = None
        self._client = None
        self._matching_tables = None  # Cache for resolved tables
        self._paramNamesKey = None
        self._paramValuesKey = None
        self._mapping = None  # New: list of (pattern, paramNamesKey, paramValuesKeys)

        try:
            with open(self.getFileName()) as f:
                config = yaml.safe_load(f)
                self._host = config.get('host')
                self._token = config.get('token')
                self._org = config.get('org')
                self._database = config.get('database')  # Changed from 'bucket'
                self._table = config.get('table')
                self._paramNamesKey = config.get('paramNamesKey')
                self._paramValuesKey = config.get('paramValuesKey')

                # Parse new mapping structure if present
                mapping_raw = config.get('mapping')
                if mapping_raw:
                    self._mapping = []
                    for entry in mapping_raw:
                        for pattern, configs in entry.items():
                            pattern = str(pattern)
                            # Merge list of dicts into a single config dict
                            merged_cfg = {}
                            for cfg in configs:
                                merged_cfg.update(cfg)
                            pNamesKey = merged_cfg.get('paramNamesKey')
                            pValuesKeys = merged_cfg.get('paramValuesKeys', ['value'])
                            self._mapping.append((pattern, pNamesKey, pValuesKeys))

        except FileNotFoundError:
            get_logger().warning(f"Config file not found: {self.getFileName()}")
        except yaml.YAMLError as e:
            get_logger().warning(f"Failed to parse YAML config: {e}")
        except Exception as e:
            get_logger().warning(f"Failed to load InfluxDB config from file: {e}")
            
    def _resolve_mapping(self, table_name: str) -> tuple[str | None, list[str] | None]:
        """
        Resolve paramNamesKey and paramValuesKeys for a given table using pattern matching.
        Returns (paramNamesKey, paramValuesKeys) or (None, None) if no mapping.
        Falls back to old _paramNamesKey / _paramValuesKey if no mapping defined.
        """
        if self._mapping is not None:
            for pattern, pNamesKey, pValuesKeys in self._mapping:
                if re.match(pattern,table_name):
                    return (pNamesKey, pValuesKeys)

        # Fallback to old behavior
        return (self._paramNamesKey, [self._paramValuesKey] if self._paramValuesKey else None)
    
    def _connect(self) -> None:
        """
        Establish connection to InfluxDB V3 if not already connected.
        """
        if self._client is None:
            if not all([self._host, self._org, self._database]):
                raise Exception(
                    f"InfluxDB V3 connection parameters not fully configured. "
                    f"host={self._host}, org={self._org}, database={self._database}"
                )
            from influxdb_client_3 import InfluxDBClient3
            self._client = InfluxDBClient3(
                host=self._host,
                token=self._token,
                org=self._org,
                database=self._database
            )
            
            print(f"[connected to {self._host}]")

    def close(self) -> None:
        """Close the InfluxDB connection if open (idempotent, safe to call several times)."""
        client = getattr(self, "_client", None)
        if client is None:
            return
        self._client = None
        try:
            client.close()
        except Exception as e:
            get_logger().warning(f"Failed to close InfluxDB V3 client: {e}")

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
        
    def _get_matching_tables(self) -> list[str]:
        """
        Resolve table name with wildcard support.
        Returns list of matching table names.
        """
        
        matching_table_names = []
        
        # Connect if not already connected
        self._connect()
        
        # Query information_schema to get all table names in iox schema
        query = '''
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'iox'
            AND table_type = 'BASE TABLE'
        '''
        
        try:
            result = self._query_to_dataframe(query)
        except Exception as e:
            get_logger().warning(f"Failed to query table names from InfluxDB V3. Please check database is reachable: {e}")
            raise e
        
        if result is not None and len(result) > 0 and 'table_name' in result.columns:
            all_tables = result['table_name'].tolist()
            
            # Apply wildcard pattern matching
            pattern = self._table.lower() if self._table else '.*'
            
            for table_name in all_tables:
                if re.match(pattern,table_name):
                    matching_table_names.append(table_name)
        
        
        return matching_table_names
    
    def getFileType(self) -> str:
        return "influxdb-v3"

    def getFileInfo(self) -> list[str]:
        fileInfo = AFileMgr.getFileInfo(self)        
        matching = self._get_matching_tables()
        table_info = f"Measurement: {self._table}"
        if len(matching) > 1:
            table_info += f" (matches {len(matching)}: {', '.join(matching[:3])}{'...' if len(matching) > 3 else ''})"
        elif len(matching) == 1:
            table_info += f" (resolved to: {matching[0]})"
        else:
            table_info += " (no matches found)"
        
        return fileInfo + [ f"Host: {self._host}",
                            f"Org: {self._org}",
                            f"Database: {self._database}",
                            table_info]

    def toHtmlTbl(self) -> str:
        htmlTbl = AFileMgr.toHtmlTbl(self)
        htmlTbl += f"<tr><th>Host</th><td>{self._host}</td></tr>"
        htmlTbl += f"<tr><th>Org</th><td>{self._org}</td></tr>"
        htmlTbl += f"<tr><th>Database</th><td>{self._database}</td></tr>"
        
        matching = self._get_matching_tables()
        if len(matching) > 1:
            htmlTbl += f"<tr><th>Measurement</th><td>{self._table} (matches {len(matching)})</td></tr>"
        elif len(matching) == 1:
            htmlTbl += f"<tr><th>Measurement</th><td>{self._table} (resolved to: {matching[0]})</td></tr>"
        else:
            htmlTbl += f"<tr><th>Measurement</th><td>{self._table} (no matches)</td></tr>"
        
        return htmlTbl

    def _query_to_dataframe(self, query: str) -> pd.DataFrame | None:
        """
        Execute query using InfluxDB V3 query API and convert result to pandas DataFrame.
        InfluxDB V3 returns PyArrow Table directly.
        """
        
        try:
            # InfluxDB V3 client.query() returns a PyArrow Table
            result = self._client.query(query)
        except Exception as e:
            raise Exception(f"ERROR while running query \n-----\n{query}\n-----\n: {e}")
        
        if result is None or len(result) == 0:
            return None
        
        # Convert PyArrow Table to pandas DataFrame
        import pyarrow as pa
        if isinstance(result, pa.Table):
            return result.to_pandas()
        
        return result

    def getNbEntries(self) -> int:
        """
        Retrieve table size via InfluxDB V3 API for all matching tables.
        Returns the size of the largest table (not cumulative).
        """
        if self._nbEntries is None:
            try:
                self._connect()
                matching_tables = self._get_matching_tables()
                
                if not matching_tables:
                    self._nbEntries = 0
                    return self._nbEntries
                
                max_count = 0
                for table in matching_tables:
                    query = f'''
                        SELECT COUNT(*) as count_value
                        FROM "{table}"
                        WHERE time > '1970-01-01T00:00:00Z'
                    '''
                    result = self._query_to_dataframe(query)
                    if result is not None and len(result) > 0 and 'count_value' in result.columns:
                        count = int(result['count_value'].iloc[0])
                        if count > max_count:
                            max_count = count
                
                self._nbEntries = max_count
            except Exception as e:
                get_logger().warning(f"Failed to get entry count from InfluxDB V3: {e}")
                self._nbEntries = 0

        return self._nbEntries
    
    def getFieldNames(self) -> list[str]:
        """
        Retrieve list of fields (columns) in matching tables via InfluxDB V3 API.
        Returns unique field names across all matching tables.
        """
        if self._fieldNamesList is None:
            try:
                self._connect()
                matching_tables = self._get_matching_tables()
                
                if matching_tables:
                    all_field_names = set()
                    
                    for table in matching_tables:
                        paramNamesKey, paramValuesKeys = self._resolve_mapping(table)
                        
                        if paramNamesKey and paramValuesKeys:
                            # Dynamic/mapping mode: extract unique param names from data
                            nbHours = 2400
                            query = None
                            while nbHours > 1:
                                query = f'''
                                SELECT DISTINCT "{paramNamesKey}" 
                                FROM "{table}" 
                                WHERE time > now() - INTERVAL '{nbHours} hours'
                                '''
                                try: 
                                    result = self._query_to_dataframe(query)
                                    break
                                except Exception:
                                    nbHours = nbHours / 2

                            if result is not None and len(result) > 0 and paramNamesKey in result.columns:
                                for param_name in result[paramNamesKey].unique():
                                    for valueKey in paramValuesKeys:
                                        if valueKey.lower()=="value":
                                            all_field_names.add(f"{table}.{param_name}")
                                        else:
                                            all_field_names.add(f"{table}.{param_name}.{valueKey}")
                        else:
                            # Default mode: extract from schema
                            query = f'''
                                SELECT column_name 
                                FROM information_schema.columns 
                                WHERE table_schema = 'iox' 
                                AND table_name = '{table}'
                            '''
                            result = self._query_to_dataframe(query)
                            
                            if result is not None and len(result) > 0 and 'column_name' in result.columns:
                                for col in result['column_name'].tolist():
                                    if col != 'time':
                                        if col == "value":
                                            all_field_names.add(table)
                                        else:
                                            all_field_names.add(table + "." + col)
                    
                    self._fieldNamesList = sorted(list(all_field_names))
                else:
                    self._fieldNamesList = []
            except Exception as e:
                get_logger().warning(f"Failed to get field names from InfluxDB V3: {e}")
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
        Load parameters from InfluxDB V3 database with batched UNION ALL queries for acceleration.
        Supports wildcard table names and arbitrary field/column names.
        
        :param minDateSec (float): minimal date in seconds since epoch 1970-01-01
        :param maxDateSec (float): maximal date in seconds since epoch 1970-01-01
        """
        rst = []

        if len(paramNamesList) == 0:
            raise Exception(self.getBaseName() + ": list of params to load is empty")

        if indexNamesList is not None and len(indexNamesList) > 0:
            raise Exception("indexNamesList is not supported for InfluxDB extract")

        self._connect()
        matching_tables = self._get_matching_tables()

        if not matching_tables:
            raise Exception(f"No tables found matching pattern '{self._table}'")

        # Build time filter for SQL queries
        time_conditions = []
        if minDateSec is not None:
            min_time_str = pd.to_datetime(minDateSec, unit='s').strftime('%Y-%m-%dT%H:%M:%SZ')
            time_conditions.append(f"time >= '{min_time_str}'")
        else:
            time_conditions.append("time > '1970-01-01T00:00:00Z'")
        
        if maxDateSec is not None:
            max_time_str = pd.to_datetime(maxDateSec, unit='s').strftime('%Y-%m-%dT%H:%M:%SZ')
            time_conditions.append(f"time <= '{max_time_str}'")
        
        time_filter = " AND ".join(time_conditions)

        is_mapping_mode = self._mapping is not None

        if len(paramNamesList)>1 and not silent:
            monitorProgress.msg(msg=[f"extracting {len(paramNamesList)} params", f"source file: {self.getBaseName()}",
                     f"source type: {self.getFileType()}"])

        # Group params into batches for UNION ALL acceleration
        # only working with params of same type ... keep t to 1 batch for now ...
        param_batch_size = 1
        param_batches = [paramNamesList[i:i + param_batch_size] for i in range(0, len(paramNamesList), param_batch_size)]
        
        processed_params = 0
        
        monitorProgress.set_total_items(param_batch_size)
        batchNb=0
        for batch in param_batches:
            batchNb+=1
            union_selects = []
            batch_info = {}
            
            for requestedFieldFullName in batch:
                if abortEvent and abortEvent.is_set():
                    raise Exception("Received abort event, InfluxDB params extraction interrupted")

                # Resolve table/field
                paramName = None
                paramNamesKey = None
                paramValuesKeys = None
                paramNamePart = None
                
                if is_mapping_mode:
                    if "." not in requestedFieldFullName:
                        raise Exception(f"Invalid param name format for mapping mode: {requestedFieldFullName}")
                    requestedTableName, paramNamePart = requestedFieldFullName.split(".", 1)
                    paramNamesKey, paramValuesKeys = self._resolve_mapping(requestedTableName)
                    
                    if len(paramValuesKeys) == 1:
                        paramName = paramNamePart
                        requestedFieldName = paramValuesKeys[0]
                    else:
                        paramName = None
                        requestedFieldName = None
                        
                        if len(requestedFieldFullName.split('.'))==2:
                            paramName=requestedFieldFullName.split('.')[1]
                            requestedFieldName="VALUE"
                            if requestedFieldName not in paramValuesKeys:
                                paramName=None
                                requestedFieldName=None
                        else:
                            for vk in paramValuesKeys:
                                if paramNamePart.endswith(f".{vk}"):
                                    paramName = paramNamePart[:-len(f".{vk}")]
                                    requestedFieldName = vk
                                    break
                        if paramName is None:
                            raise Exception(f"Cannot resolve valueKey for field '{requestedFieldFullName}' with paramValuesKeys={paramValuesKeys}")
                else:
                    # Legacy mode
                    if len(requestedFieldFullName.split(".")) == 1:
                        requestedTableName = requestedFieldFullName
                        requestedFieldName = "value"
                    else:                
                        requestedTableName = requestedFieldFullName.split(".")[0]
                        requestedFieldName = requestedFieldFullName.split(".")[1]
                        paramNamePart = requestedFieldName

                if requestedTableName not in matching_tables:
                    raise Exception(f"table '{requestedTableName}' not listed in input config file: "+self._table)
                
                # Check schema
                field_check_query = f'''
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_schema = 'iox' 
                    AND table_name = '{requestedTableName}'
                    AND column_name = '{requestedFieldName}'
                '''
                field_check_df = self._query_to_dataframe(field_check_query)
                if field_check_df is None or len(field_check_df) == 0:
                    raise Exception(f"no such param '{requestedTableName}.{requestedFieldName}'")

                # Build where clause
                where_conditions = [time_filter]
                if is_mapping_mode:
                    where_conditions.append(f'"{paramNamesKey}" = \'{paramName}\'')
                elif self._paramNamesKey and self._paramValuesKey:
                    where_conditions.append(f'"{self._paramNamesKey}" = \'{paramNamePart}\'')
                where_clause = " AND ".join(where_conditions)
                
                union_selects.append(f'SELECT time, "{requestedFieldName}" as value, \'{requestedFieldFullName}\' as param_name FROM "{requestedTableName}" WHERE {where_clause}')
                batch_info[requestedFieldFullName] = {
                    'requestedTableName': requestedTableName,
                    'requestedFieldName': requestedFieldName,
                    'is_mapping_mode': is_mapping_mode,
                    'paramName': paramName,
                    'paramNamesKey': paramNamesKey,
                    'paramValuesKeys': paramValuesKeys,
                    'where_clause': where_clause
                }
            
            if not union_selects:
                continue

            # Build UNION query
            union_query = " UNION ALL ".join(union_selects) + " ORDER BY time"
            
            # Count total entries for this batch
            count_query = f"SELECT COUNT(*) as count_value FROM ({union_query})"
            count_df = self._query_to_dataframe(count_query)
            total_entries = 0
            if count_df is not None and len(count_df) > 0 and 'count_value' in count_df.columns:
                total_entries = int(count_df['count_value'].iloc[0])
            
            # Fetch in chunks
            offset = 0
            fetch_batch_size = 1000000
            all_records = []
            total_processed = 0
            
            subMpBatch=monitorProgress.child(f"db-extract-batch_{batchNb}",total_entries + len(batch_info)) # +len(batch_info) for finalizeParam
            while offset < total_entries:
                if abortEvent and abortEvent.is_set():
                    raise Exception("Received abort event, InfluxDB params extraction interrupted")

                query = f"{union_query} LIMIT {fetch_batch_size} OFFSET {offset}"
                df_batch = self._query_to_dataframe(query)
                
                if df_batch is None or len(df_batch) == 0:
                    break
                
                all_records.append(df_batch)
                offset += fetch_batch_size
                total_processed += len(df_batch)   
                subMpBatch.complete_n(len(df_batch))                             
                    
            if len(all_records) > 0:
                dfAll = pd.concat(all_records, ignore_index=True)
            else:
                dfAll = pd.DataFrame(columns=['time', 'value', 'param_name'])
            
            # Process each param in the batch
            for requestedFieldFullName, info in batch_info.items():
                dfParam = dfAll[dfAll['param_name'] == requestedFieldFullName][['time', 'value']].copy()
                
                if len(dfParam) == 0:
                    get_logger().warn(f"No data found for field '{info['requestedTableName']}.{info['requestedFieldName']}'")
                    continue

                # Convert time to float64 Unix timestamp (seconds)
                dfParam.index = pd.to_datetime(dfParam['time']).values.astype('int64').astype('float64') / 1e9
                dfParam = dfParam[['value']].rename(columns={'value': info['requestedTableName'] + "." + info['requestedFieldName']})
                
                if info['is_mapping_mode']:
                    paramFinalName = requestedFieldFullName
                else:
                    paramFinalName = info['requestedTableName'] if info['requestedFieldName'] == "value" else info['requestedTableName'] + "." + info['requestedFieldName']
                    
                rst.append(self.finalizeParam(dfParam,
                    name=paramFinalName,
                    indexName="Timestamps/timestamp_"+paramFinalName,
                    origin=self.getFileName(),
                    minDateSec=minDateSec, maxDateSec=maxDateSec,
                    shiftDateSec=shiftDateSec, shiftDateRegex=shiftDateRegex,shiftDateInverted=shiftDateInverted,
                    silent=silent, callback=callback,
                    monitorProgress=subMpBatch.child(f"finalize {paramFinalName}")))
                processed_params += 1

        # release the connection: the client is re-created lazily by _connect() on next use
        self.close()
        
        return rst 
    
###############################################################################################################""

class InfluxDbV2FileMgr(AFileMgr):    
    """Uses a YML description as a pseudo data file to access DB contents."""
    
    def __init__(self, filename: str, fileIdx: int) -> None:
        
        super().__init__(filename, fileIdx)
        self._host = None
        self._token = None
        self._org = None
        self._bucket = None
        self._measurement = None
        self._client = None

        try:
            with open(self.getFileName()) as f:
                config = yaml.safe_load(f)
                self._host = config.get('host')
                self._token = config.get('token')
                self._org = config.get('org')
                self._bucket = config.get('bucket')
                self._measurement = config.get('measurement')

        except FileNotFoundError:
            get_logger().warning(f"Config file not found: {self.getFileName()}")
        except yaml.YAMLError as e:
            get_logger().warning(f"Failed to parse YAML config: {e}")
        except Exception as e:
            get_logger().warning(f"Failed to load InfluxDB config from file: {e}")    

    def _connect(self) -> None:
        """
        Establish connection to InfluxDB V2 if not already connected.
        """
        if self._client is None:
            if not all([self._host, self._org, self._bucket]):
                raise Exception(
                    f"InfluxDB V2 connection parameters not fully configured. "
                    f"host={self._host}, org={self._org}, bucket={self._bucket}"
                )
            from influxdb_client import InfluxDBClient
            self._client = InfluxDBClient(
                url=self._host,  # Changed from 'host' to 'url'
                token=self._token,
                org=self._org
            )
            print(f"[connected to {self._host}]")

    def close(self) -> None:
        """Close the InfluxDB connection if open (idempotent, safe to call several times)."""
        client = getattr(self, "_client", None)
        if client is None:
            return
        self._client = None
        try:
            client.close()
        except Exception as e:
            get_logger().warning(f"Failed to close InfluxDB V3 client: {e}")

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
        
    def getFileType(self) -> str:
        return "influxdb-v2"

    def getFileInfo(self) -> list[str]:
        fileInfo = AFileMgr.getFileInfo(self)        
        return fileInfo + [ f"Host: {self._host}",
                            f"Org: {self._org}",
                            f"Bucket: {self._bucket}"
                            f"Measurement: {self._measurement}"]

    def toHtmlTbl(self) -> str:
        htmlTbl = AFileMgr.toHtmlTbl(self)
        htmlTbl += f"<tr><th>Host</th><td>{self._host}</td></tr>"
        htmlTbl += f"<tr><th>Org</th><td>{self._org}</td></tr>"
        htmlTbl += f"<tr><th>Bucket</th><td>{self._bucket}</td></tr>"
        htmlTbl += f"<tr><th>Measurement</th><td>{self._measurement}</td></tr>"
        
        return htmlTbl



    def getNbEntries(self) -> int:
        """
        Retrieve table size via InfluxDB V2 API.
        """
        if self._nbEntries is None:
            try:
                self._connect()
                # Count all records in the bucket
                query = f'''
                    from(bucket: "{self._bucket}")
                        |> range(start: 1970-01-01)
                        |> filter(fn: (r) => r._measurement == "{self._measurement}")                        
                        |> count()
                '''
                result = self._query_to_dataframe(query)
                if result is not None and len(result) > 0 and '_value' in result.columns:
                    self._nbEntries = int(result['_value'].sum())
                else:
                    self._nbEntries = 0
            except Exception as e:
                get_logger().warning(f"Failed to get entry count from InfluxDB V2: {e}")
                self._nbEntries = 0

        return self._nbEntries
    
    def getFieldNames(self) -> list[str]:
        """
        Retrieve list of fields in bucket via InfluxDB V2 API.
        """
        if self._fieldNamesList is None:
            try:
                self._connect()
                # Query to get field keys using Flux - use _field column directly
                query = f'''
                    from(bucket: "{self._bucket}")
                        |> range(start: 1970-01-01)
                        |> filter(fn: (r) => r._measurement == "{self._measurement}")
                        |> keep(columns: ["_field"])
                        |> distinct(column: "_field")
                '''
                result = self._query_to_dataframe(query)
                if result is not None and len(result) > 0 and '_field' in result.columns:
                    self._fieldNamesList = result['_field'].unique().tolist()
                else:
                    # Fallback: try to get column names from a sample query
                    query = f'''
                        from(bucket: "{self._bucket}")
                            |> range(start: 1970-01-01)
                            |> filter(fn: (r) => r._measurement == "{self._measurement}")                        
                            |> limit(n: 1)
                    '''
                    result = self._query_to_dataframe(query)
                    if result is not None and len(result.columns) > 0:
                        # Filter out InfluxDB internal columns
                        internal_cols = ['_start', '_stop', '_time', '_value', '_field', '_measurement', 'table']
                        self._fieldNamesList = [col for col in result.columns if col not in internal_cols]
                    else:
                        self._fieldNamesList = []
            except Exception as e:
                get_logger().warning(f"Failed to get field names from InfluxDB V2: {e}")
                self._fieldNamesList = []

        return self._fieldNamesList


    def _query_to_dataframe(self, query: str) -> pd.DataFrame | None:
        """
        Execute query using InfluxDB V2 query API and convert result to pandas DataFrame.
        """
        from influxdb_client import QueryApi
        query_api: QueryApi = self._client.query_api()
        
        result = query_api.query(query)
        
        # Convert FluxQueryResult to DataFrame
        if result is None or len(result) == 0:
            return None
        
        # Flatten the result tables into a single DataFrame
        records = []
        for table in result:
            for record in table.records:
                records.append(dict(record))
        
        if len(records) == 0:
            return None
        
        df = pd.DataFrame(records)
        return df
    
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
        Load parameters from InfluxDB V2 bucket with batched queries for progress reporting.
        
        :param minDateSec (float): minimal date in seconds since epoch 1970-01-01
        :param maxDateSec (float): maximal date in seconds since epoch 1970-01-01
        """
        rst = []

        if not silent:
            if len(paramNamesList)==1:
                monitorProgress.msg(msg=[f"extracting {paramNamesList[0]}", f"source file: {self.getBaseName()}",
                            f"source type: {self.getFileType()}"])
            else:
                monitorProgress.msg(msg=[f"extracting {len(paramNamesList)} params", f"source file: {self.getBaseName()}",
                            f"source type: {self.getFileType()}"])

        if len(paramNamesList) == 0:
            raise Exception(self.getBaseName() + ": list of params to load is empty")

        if indexNamesList is not None and len(indexNamesList) > 0:
            raise Exception("indexNamesList is not supported for InfluxDB extract")

        self._connect()

        # Build range clause for Flux queries
        min_time_str = pd.to_datetime(minDateSec, unit='s').strftime('%Y-%m-%dT%H:%M:%SZ') if minDateSec is not None else '1970-01-01T00:00:00Z'
        range_clause = f'range(start: {min_time_str}'
        if maxDateSec is not None:
            max_time_str = pd.to_datetime(maxDateSec, unit='s').strftime('%Y-%m-%dT%H:%M:%SZ')
            range_clause += f', stop: {max_time_str}'
        range_clause += ')'

        # Build filter for all requested fields in ONE query
        field_filter = " or ".join([f'r["_field"] == "{name}"' for name in paramNamesList])
        
        # Get total count first for progress calculation
        count_query = f'''
            from(bucket: "{self._bucket}")
                |> {range_clause}
                |> filter(fn: (r) => r._measurement == "{self._measurement}")                        
                |> filter(fn: (r) => {field_filter})
                |> count()
        '''
        count_df = self._query_to_dataframe(count_query)
        if count_df is not None and len(count_df) > 0 and '_value' in count_df.columns:
            total_entries = int(count_df['_value'].sum())
        else:
            total_entries = 0
        
        # Batch size for progress reporting
        batch_size = 10000
        offset = 0
        all_records = []
        total_batches = (total_entries + batch_size - 1) // batch_size if total_entries > 0 else 1
        
        monitorProgress.set_total_items(total_batches)
        
        # Query in batches
        while offset < total_entries or offset == 0:  # offset == 0 handles empty result case
            if abortEvent and abortEvent.is_set():
                raise Exception("Received abort event, InfluxDB params extraction interrupted")

            query = f'''
                from(bucket: "{self._bucket}")
                    |> {range_clause}
                    |> filter(fn: (r) => r._measurement == "{self._measurement}")                        
                    |> filter(fn: (r) => {field_filter})
                    |> keep(columns: ["_time", "_field", "_value"])
                    |> sort(columns: ["_time", "_field"])
                    |> limit(n: {batch_size}, offset: {offset})
            '''
            
            df_batch = self._query_to_dataframe(query)
            
            if df_batch is None or len(df_batch) == 0:
                break
            
            all_records.append(df_batch)
            offset += batch_size
            
            monitorProgress.complete_n(batch_size)

        # Combine all batches
        if len(all_records) > 0:
            dfAll = pd.concat(all_records, ignore_index=True)
        else:
            dfAll = pd.DataFrame(columns=['_time', '_field', '_value'])
        
        for requestedFieldName in paramNamesList:
            if abortEvent and abortEvent.is_set():
                raise Exception("Received abort event, InfluxDB params extraction interrupted")

            try:
                # Filter for this specific parameter
                dfParam = dfAll[dfAll['_field'] == requestedFieldName].copy()
                
                if len(dfParam) == 0:
                    raise Exception(f"No data found for field '{requestedFieldName}'")

                # Convert time to float64 Unix timestamp (seconds)
                dfParam.index = pd.to_datetime(dfParam['_time']).values.astype('int64').astype('float64') / 1e9
                dfParam = dfParam[['_value']].rename(columns={'_value': requestedFieldName})
                
                rst.append(self.finalizeParam(dfParam,
                            name=requestedFieldName,
                            indexName="Timestamps/timestamp_"+requestedFieldName,
                            origin=self.getFileName(),
                            minDateSec=minDateSec,maxDateSec=maxDateSec,
                            shiftDateSec=shiftDateSec,shiftDateRegex=shiftDateRegex,shiftDateInverted=shiftDateInverted,
                            silent=silent,callback=callback))

            except Exception as e:
                raise Exception(
                    "Unable to extract param '" + requestedFieldName +
                    "' from InfluxDB V2 bucket '" + self.getFileName() + "': " + str(e)
                )

        # release the connection: the client is re-created lazily by _connect() on next use
        self.close()
        
        return rst