
#
# methods to upload data to database
#

from __future__ import annotations

import sys,os,json,math
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor,wait

import pandas as pd

from testsuit.misc.MonitorProgress import MonitorProgress 

from testsuit.datatools.datatoolbox import *


class Data2Db(ABC):
    """Abstract base class: extract data as pandas DataFrames, and inject them into the requested DB.

    Concrete subclasses declare the conf["db"]["dbtype"] value they handle via DB_TYPE,
    and implement inject_to_db() with the backend-specific upload logic.
    """

    #: value of conf["db"]["dbtype"] handled by this implementation
    DB_TYPE: str = ""

    #######################
    @abstractmethod
    def inject_to_db(self,
                     dbconf: dict,
                     fieldsDfList: list,
                     token: str,
                     monitorProgress: MonitorProgress | None = None,
                     nbWorkers: int = MAX_MTHREAD_WORKERS,
                     abortEvent: threading.Event | None = None) -> list:
        """Inject a list of field DataFrames into the target DB.

        :param dbconf (dict): backend-specific DB configuration (conf["db"])
        :param fieldsDfList (list): list of field DataFrames to inject
        :param token (str): token or password for DB access
        :param monitorProgress (MonitorProgress): progress monitoring obj
        :param nbWorkers (int): number of parallel workers
        :param abortEvent (threading.Event): threading event to signal abortion request
        """

    #######################
    def data2db(self,
                sourceFolderOrFiles: str | list,
                conf: dict,
                extensions: list[str] | None = None,
                token: str | None = None,
                dryRun: bool = False,
                monitorProgress: MonitorProgress | None = None,
                abortEvent: threading.Event | None = None,
                silent: bool = False) -> dict | None:
        """Extract data as pandas dataframes, and inject it into requested DB.
        
        :param sourceFolderOrFiles (str|list): path to file or folders containing data to extract."
        :param conf (dict): request configuration (see details here under)
        :param extensions (list): list of accepted file extensions. Defaults to every entry of datatoolbox.SUPPORTED_DATAFILE_EXTENSIONS.
        :param token (str): token or password for DB access
        :param dryRun (bool): if true, only list fields detected, but do not actually upload data
        :monitorProgress (func): progress monitoring obj (see misc::monitorProgress)
        :abortEvent (threading event): threading event to signal abortion request

Example of conf dictionary:
    { 
        "fields" : [".*"],
        "indices" : [],
        "db" : {
            "dbtype" : "influxdb",
            "url" : "http://influxdb:8086", 
            "org" : "_sandbox_",
            "database" : "testBucket", 
            "table" :"testMeas",
            "dateUnit" : "s", (OPTIONAL) (s|ms|us|ns)
            'dateOffset': "now|12584s|2024-10-12 00:00:00", (OPTIONAL)
            'minDate': "2024-10-12 12:13:00", (OPTIONAL)
            'maxDate': "2024-10-12 12:17:32", (OPTIONAL)
            'verticalOffset': "autoz|42", (OPTIONAL)
            'timezone': "Europe/Paris", (OPTIONAL)
            'rename_fields_re_match': ".*/([^/]+)$", (OPTIONAL)
            'rename_fields_re_replace': "\\1" (OPTIONAL)
        }
    }
        """
        
        def _group_dryrun_results(results: list) -> dict:
            """Group dryRun results by effective field name, merging origins into an array."""
            from collections import OrderedDict
            groups = OrderedDict()
            for entry in results:
                key = entry.get("name_in_db")
                if key not in groups:
                    groups[key] = [{ "origin": entry.get("origin"),
                                     "fullname": entry.get("fullname")} ]
                else:
                    groups[key].append({ "origin": entry.get("origin"),
                                         "fullname": entry.get("fullname")})
            return dict(groups)
        
        dfListRst=[]

        if isinstance(sourceFolderOrFiles,str): sourceFolderOrFiles=[sourceFolderOrFiles]


        def dbUploadCallback(df: pd.DataFrame, monitorProgress: MonitorProgress) -> dict | list | str:
            
            # dryRun: only display names original/final
            if dryRun:
                monitorProgress.set_total_items(1)            
                nameOri=",".join(renameDfCols(df,None))
                nameInDb=nameOri
                if "rename_fields_re_match" in conf["db"] and len(conf["db"]["rename_fields_re_match"])>0:
                    nameInDb=",".join(renameDfCols(df,conf["db"]["rename_fields_re_match"],conf["db"]["rename_fields_re_replace"]))                
                monitorProgress.complete_n(1)
                return { "name_in_db": nameInDb,"fullname" : nameOri, "origin": df.origin if hasattr(df,"origin") else "?" }
            
            # not dryRun: actually upload data to DB
            dbtype=conf["db"]["dbtype"]
            if dbtype != self.DB_TYPE:
                raise ValueError(f"Unhandled DB type '{dbtype}' (this implementation only handles '{self.DB_TYPE}').")
            return self.inject_to_db(dbconf=conf["db"],fieldsDfList=[df],token=token,
                                     monitorProgress=monitorProgress,abortEvent=abortEvent)

        monitorProgress.set_total_items(len(sourceFolderOrFiles))
        for srcFileOrFolder in sourceFolderOrFiles:   
            subMp=monitorProgress.child("Extract from "+srcFileOrFolder)
            dfListRst+=loadDataframeFromFile(srcFileOrFolder,conf["fields"],
                                            conf["indices"],
                                            extensions,dryRun=dryRun, 
                                            monitorProgress=subMp,
                                            abortEvent=abortEvent,
                                            # no need to merge fields for later writing them into a db
                                            # this allow easier parallel processing
                                            mergeParams=False, 
                                            callback=dbUploadCallback
                                        )
        
        if len(dfListRst)==0:
            raise ValueError(f"Found 0 field matching provided regexes: {conf['fields']}")

        if dryRun:
            return _group_dryrun_results(dfListRst)

    #######################
    def getFieldsList(self,
                sourceFolderOrFiles: str | list,
                conf: dict,
                extensions: list[str] | None = None,                
                monitorProgress: MonitorProgress | None = None,
                abortEvent: threading.Event | None = None,
                silent: bool = False) -> dict | None:
        """Only get list of fields names as they would be uploaded in a DB"""
        return self.data2db(sourceFolderOrFiles, conf, extensions, 
                        dryRun=True,
                        monitorProgress=monitorProgress,
                        abortEvent=abortEvent,
                        silent=silent)
        
    #######################
    def prepare_df_for_db_upload(self, conf: dict, df: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
        """Apply adjustements requested on conf over retrieved dataframe prior to db upload, including:
            - set-up date and apply offset if any
            - min/max date
            - vertical offset/auto-zero
            - rename columns
            
        :param conf (dict): see data2db field 'conf["db"]'
        :param df (pd.DataFrame): data to prepare
        """

        if not isinstance(df,(pd.DataFrame,pd.Series)):
            raise Exception(f"bad datatype for db upload. Expected pandas DataFrame or Series, got {type(df)} : "+str(df))
        
        dateCoefToNanosec=getCoefConvToNanosec(conf.get("dateUnit"))
        dateOffset=getDateOffsetSec(conf.get("dateOffset"),conf.get("timezone"))
        
        minDate=conf.get("minDate")
        if minDate and len(minDate)>0:
            try: minDate=pd.Timestamp(minDate).timestamp()*1e9
            except: 
                try: minDate=number(minDate)*1e9
                except:
                    raise ValueError("invalid value given as 'minDate'."\
                                            +"Accepted formats are '2017-12-16 03:02:35.123456' or '1513393355.123456' :"\
                                            +f" given value was '{maxDate}'")
        else: minDate=None

        maxDate=conf.get("maxDate")
        if maxDate and len(maxDate)>0:
            try: maxDate=pd.Timestamp(maxDate).timestamp()*1e9
            except: 
                try: maxDate=number(maxDate)*1e9
                except:
                    raise ValueError("invalid value given as 'maxDate'. "\
                                            +"Accepted formats are '2017-12-16 03:02:35.123456' or '1513393355.123456' :"\
                                            +f" given value was '{maxDate}'")
        else: maxDate=None

        verticalOffset=0
        if "verticalOffset" in conf: verticalOffset=conf["verticalOffset"]    
        df=df+getVerticalOffset(df,verticalOffset)

        df.index = (df.index + dateOffset) * dateCoefToNanosec # date expected in nanoseconds by InfluxDB
        # applying time range if required
        df=df[minDate:maxDate]

        df.columns=renameDfCols(df,conf.get("rename_fields_re_match"),conf.get("rename_fields_re_replace"))

        return df

class DryRunData2Db(Data2Db):
    def inject_to_db(self,
            dbconf: dict,
            fieldsDfList: list,
            token: str,
            monitorProgress: MonitorProgress | None = None,
            nbWorkers: int = MAX_MTHREAD_WORKERS,
            abortEvent: threading.Event | None = None) -> list:
        
        raise Exception("Operation 'inject_to_db' not allowed with this class 'DryRunData2Db'")

    
class InfluxdbV2Data2Db(Data2Db):
    """InfluxDB v2 implementation of Data2Db."""

    DB_TYPE = "influxdb"

    DATA_INFLUXDB_CHUNCK_SIZE_API = 20e3

    #######################
    def create_influxdb_bucket_if_missing(self,
                                          org: str,
                                          bucket_name: str,
                                          url: str,
                                          token: str,
                                          monitorProgress: MonitorProgress | None = None) -> None:
        """Create a new influxdb bucket if not already exists
        
        :param org (str): influx org name
        :param bucket (str): influx bucket name
        :param url (str): influx db url (ex: http://localhost:8086)
        :param token (str): influx access token
        :monitorProgress (func): function to be called for GUI progress messages (see datatoolbox:defaultmonitorProgress for signature)
        """
        from influxdb_client import InfluxDBClient
        from influxdb_client.rest import ApiException

        influxdb_client = InfluxDBClient(url=url, token=token, org=org, debug=False)
        buckets_api = influxdb_client.buckets_api()

        try:
            buckets_api.create_bucket(bucket_name=bucket_name, org=org, retention_rules=None)
            monitorProgress and monitorProgress.msg(msg=[org,f"created new bucket '{bucket_name}'"])
        except ApiException as e:
            err_code = json.loads(e.body).get("code")
            if err_code == "conflict":
                pass  # bucket already exists in this org, proceed safely
            elif err_code == "unauthorized":
                raise ValueError("access denied. Maybe you should specify the token to be used ?")
            else:
                raise ValueError(f"Unable to create bucket: {e}")

    #######################
    def _influxUploadWorkerApi(self, workerData: dict) -> None:
        """Worker for multithread DB upload (each thread dedicated to a field)
        :param workerData (dict): some context info to be provided to the worker"""
       
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS
        
        dfPos=workerData["dfPos"]
        dbconf=workerData["dbconf"]
        monitorProgress=workerData["monitorProgress"]
        fieldsDfList=workerData["fieldsDfList"]
        token=workerData["token"]   
        dfInjectProgress=workerData["dfInjectProgress"]
        abortEvent=workerData["abortEvent"]
        dfInjectProgress[dfPos]=0
        dfInjectProgressLock=workerData["dfInjectProgressLock"]

        influxdb_client = InfluxDBClient(url=dbconf["url"], token=token, org=dbconf["org"], debug=False, enable_gzip=True)

        df=fieldsDfList[dfPos]
        oriMinDate_ns=df.index[0]
        oriMaxDate_ns=df.index[-1]

        df=self.prepare_df_for_db_upload(dbconf,df)

        if len(df)==0:
            warningDates=""
            if dbconf.get("minDate") or dbconf.get("maxDate"):
                origMinDateStr=datetime.fromtimestamp(oriMinDate_ns/1e9).strftime('%Y-%m-%d %H:%M:%S.%f')[:-6]
                origMaxDateStr=datetime.fromtimestamp(oriMinDate_ns/1e9).strftime('%Y-%m-%d %H:%M:%S.%f')[:-6]
                warningDates=f" Maybe provided min/max dates were not appropriate ? data original time range is  [ {origMinDateStr}, {origMaxDateStr} ]"
            raise ValueError(f"No value found for '{df.columns}'.{warningDates}")
            
        logName=",".join(df.columns)
        
        nbChunks=math.ceil(len(df)/self.DATA_INFLUXDB_CHUNCK_SIZE_API)
        monitorProgress.set_total_items(nbChunks)
        
        orgStr=dbconf["org"]
        bucketStr=dbconf["database"]
        measStr=dbconf["table"]        
        monitorProgress.msg(msg=[logName,f"injecting {len(df)} entries ({nbChunks} chunk(s)) into {orgStr}/{bucketStr}/{measStr}"])   

        with influxdb_client.write_api(
                    #write_options=WriteOptions(write_type=WriteType.batching, 
                    #                           batch_size=INFLUX_WRITE_BATCH_SIZE, 
                    #                           flush_interval=INFLUX_FLUSH_INTERVAL_MS)) as write_api:
                    write_options=SYNCHRONOUS) as write_api:
            for chuckNb in range(nbChunks):
                
                if abortEvent and abortEvent.is_set(): return

                curStartLoc=int(chuckNb*self.DATA_INFLUXDB_CHUNCK_SIZE_API)
                curEndLoc=int(curStartLoc+self.DATA_INFLUXDB_CHUNCK_SIZE_API)
                
                curDataSegment=df.iloc[curStartLoc:curEndLoc]
                if isinstance(curDataSegment,pd.Series):
                    curDataSegment=curDataSegment.to_frame()
                
                if len(curDataSegment)==0:
                     raise ValueError(f"No value found for '{df.columns}' in chunk {chuckNb} / {nbChunks}")

                try:
                    write_api.write(bucket=dbconf["database"], 
                                org=dbconf["org"], 
                                record=curDataSegment,
                                data_frame_measurement_name=dbconf["table"]
                                )         
                except Exception as e:
                    raise Exception(f"Unable to write data chunk: {e}")
                
                # keep final 100% for when all data is flushed (i.e. when write_api object is destroyed)
                if chuckNb!=nbChunks-1:
                    with dfInjectProgressLock: dfInjectProgress[dfPos]=(chuckNb+1)/nbChunks

                monitorProgress.complete_item(f"chunk_{chuckNb+1}/{nbChunks}")   

        influxdb_client.close()

        with dfInjectProgressLock: dfInjectProgress[dfPos]=1
        
        timestampStr=""
        timestampStr=datetime.fromtimestamp(df.index[0]/1e9).strftime('%Y-%m-%d %H:%M:%S.%f')[:-6]\
            +" - "+datetime.fromtimestamp(df.index[-1]/1e9).strftime('%Y-%m-%d %H:%M:%S.%f')[:-6]   

        monitorProgress.msg(msg=[logName,f"injected {len(df)} entries",timestampStr], msgSeverity="success")

    #######################
    # for multiprocessing example, see https://github.com/influxdata/influxdb-client-python/blob/master/examples/import_data_set_multiprocessing.py
    def inject_to_db(self,
                     dbconf: dict,
                     fieldsDfList: list,
                     token: str,
                     monitorProgress: MonitorProgress | None = None,
                     nbWorkers: int = MAX_MTHREAD_WORKERS,
                     abortEvent: threading.Event | None = None) -> list:

        monitorProgress.set_total_items(len(fieldsDfList))

        if "database" not in dbconf:
            raise ValueError("missing 'database' in provided influxdb conf")

        buckerStr=dbconf["database"]
        monitorProgress.msg(msg=f"checking database '{buckerStr}' exists (and create it if needed)")
        self.create_influxdb_bucket_if_missing(dbconf["org"],dbconf["database"],dbconf["url"],token)

        dfInjectProgress=[0] * len(fieldsDfList)
        dfInjectProgressLock=threading.Lock()

        workersData=[]    
        dfIdx=0

        for df in fieldsDfList:  
            subMp=monitorProgress.child(f"dbinject - {fieldsDfList[dfIdx].name}")
            workersData.append({
                "dfPos":dfIdx,
                "dbconf":dbconf,
                "fieldsDfList":fieldsDfList,
                "monitorProgress":subMp,
                "token":token,
                "dfInjectProgress":dfInjectProgress,
                "dfInjectProgressLock":dfInjectProgressLock,
                "abortEvent":abortEvent
            })
            dfIdx+=1    

        with ThreadPoolExecutor(max_workers=nbWorkers) as executor:
            futures = [executor.submit(self._influxUploadWorkerApi, workerData) for workerData in workersData]
            wait(futures)
            for fut in futures: fut.result()

        return dfInjectProgress
    
#################################################################
class InfluxdbV3Data2Db(Data2Db):
    """InfluxDB v3 implementation of Data2Db.

    Notes:
    - data upload uses the official 'influxdb_client_3' package (InfluxDBClient3 + Point)
    - database/table listing & creation still use the v3 HTTP API directly with 'requests' (same approach as DbAccess.py)
    - the 'database' entry of dbconf is used as the InfluxDB v3 database name
    - the database and the table are created automatically if missing, before the upload
    - 'org' is not used by InfluxDB v3
    """

    DB_TYPE = "influxdb3"

    DATA_INFLUXDB3_CHUNCK_SIZE_API = 20e3

    #######################
    @staticmethod
    def _influx3Headers(token, contentType=None) -> dict:
        """HTTP headers for the InfluxDB v3 API (same convention as DbAccess.py)."""
        headers={}
        if contentType:
            headers["Content-Type"]=contentType
        if token:
            headers["Authorization"]="Bearer "+token
        return headers

    def _dfToPoints(self, df, measurement) -> list:
        """Convert a prepared dataframe (index in nanoseconds) to InfluxDB v3 points.

        One point per row: measurement + fields only, no tags.
        Rows without any valid value (NaN/None) are skipped.
        Timestamps come from the dataframe index, in nanoseconds.
        """
        from influxdb_client_3 import Point, WritePrecision

        points=[]
        for ts, row in df.iterrows():
            tsNs=ts.value if isinstance(ts,pd.Timestamp) else int(ts)
            point=Point(measurement)
            point.time(tsNs,WritePrecision.NS)
            nbFields=0
            for col, val in row.items():
                if val is None:
                    continue
                if hasattr(val,"item"): val=val.item()  # normalize numpy scalars
                if isinstance(val,float) and math.isnan(val):
                    continue
                point.field(col,val)
                nbFields+=1
            if nbFields==0:
                continue
            points.append(point)
        return points

    #######################
    def _listInflux3Databases(self, dbconf, token) -> list:
        """List existing database names on the InfluxDB v3 instance (same endpoint as DbAccess.py)."""
        import requests

        response=requests.get(dbconf["url"].rstrip("/")+"/api/v3/configure/database",
                              params={"format":"json"},
                              headers=self._influx3Headers(token,contentType="application/json"))
        response.raise_for_status()

        dbs=[]
        for stmt in response.json():
            if stmt.get("error"):
                raise Exception(f"InfluxDB 3 error: {stmt['error']}")
            dbName=stmt.get("iox::database")
            if dbName:
                dbs.append(dbName)
        return dbs

    def _listInflux3Tables(self, dbconf, token, database) -> list:
        """List existing table names in 'database' (same SQL as DbAccess.py 'list_tables')."""
        import requests

        response=requests.post(dbconf["url"].rstrip("/")+"/api/v3/query_sql",
                               json={"db":database,
                                     "q":"SELECT table_name FROM information_schema.tables WHERE table_schema = 'iox';"},
                               headers=self._influx3Headers(token,contentType="application/json"))
        response.raise_for_status()

        results=response.json()
        if isinstance(results,dict): results=[results]

        tables=[]
        for stmt in results:
            if "error" in stmt:
                raise Exception(f"InfluxDB 3 query error: {stmt['error']}")
            if "columns" in stmt and "rows" in stmt:
                colIdx=stmt["columns"].index("table_name")
                for row in stmt["rows"]:
                    tables.append(row[colIdx])
            elif "table_name" in stmt:
                tables.append(stmt["table_name"])
        return tables

    #######################
    def ensure_db_and_table_if_missing(self,
                                       dbconf: dict,
                                       token: str,
                                       monitorProgress: MonitorProgress | None = None) -> None:
        """Ensure the InfluxDB v3 database and table exist, creating them if missing.

        :param dbconf (dict): db config ('url', 'database', 'table')
        :param token (str): InfluxDB v3 access token
        :param monitorProgress (MonitorProgress): progress monitoring obj
        """
        import requests

        databaseStr=dbconf["database"]
        tableStr=dbconf["table"]

        # database
        if databaseStr not in self._listInflux3Databases(dbconf, token):
            response=requests.post(dbconf["url"].rstrip("/")+"/api/v3/configure/database",
                                   json={
                                        "format":"json",
                                        "db":databaseStr
                                    },
                                   headers=self._influx3Headers(token,contentType="application/json"))
            response.raise_for_status()
            monitorProgress and monitorProgress.msg(msg=f"created new database '{databaseStr}'")
        else:
            monitorProgress and monitorProgress.msg(msg=f"database '{databaseStr}' already exists")

        # table (checked after the database exists, as the query is scoped to it)
        if tableStr not in self._listInflux3Tables(dbconf, token, databaseStr):
            response=requests.post(dbconf["url"].rstrip("/")+"/api/v3/configure/table",
                                   json={   "format":"json",
                                            "db":databaseStr,
                                            "table":tableStr,                                           
                                            "retention_period": None,
                                            "fields":[],
                                            "tags":[]
                                        },
                                   headers=self._influx3Headers(token,contentType="application/json"))
            response.raise_for_status()
            monitorProgress and monitorProgress.msg(msg=f"created new table '{tableStr}' in database '{databaseStr}'")
        else:
            monitorProgress and monitorProgress.msg(msg=f"table '{tableStr}' already exists in database '{databaseStr}'")

    #######################

    def _influx3UploadWorkerApi(self, workerData: dict) -> None:
        """Worker for multithread DB upload (each thread dedicated to a field)
        :param workerData (dict): some context info to be provided to the worker"""

        from influxdb_client_3 import InfluxDBClient3

        dfPos=workerData["dfPos"]
        dbconf=workerData["dbconf"]
        monitorProgress=workerData["monitorProgress"]
        fieldsDfList=workerData["fieldsDfList"]
        token=workerData["token"]
        dfInjectProgress=workerData["dfInjectProgress"]
        abortEvent=workerData["abortEvent"]
        dfInjectProgress[dfPos]=0
        dfInjectProgressLock=workerData["dfInjectProgressLock"]

        df=fieldsDfList[dfPos]
        oriMinDate_ns=df.index[0]
        oriMaxDate_ns=df.index[-1]

        df=self.prepare_df_for_db_upload(dbconf,df)

        if len(df)==0:
            warningDates=""
            if dbconf.get("minDate") or dbconf.get("maxDate"):
                origMinDateStr=datetime.fromtimestamp(oriMinDate_ns/1e9).strftime('%Y-%m-%d %H:%M:%S.%f')[:-6]
                origMaxDateStr=datetime.fromtimestamp(oriMinDate_ns/1e9).strftime('%Y-%m-%d %H:%M:%S.%f')[:-6]
                warningDates=f" Maybe provided min/max dates were not appropriate ? data original time range is  [ {origMinDateStr}, {origMaxDateStr} ]"
            raise ValueError(f"No value found for '{df.columns}'.{warningDates}")

        logName=",".join(df.columns)

        nbChunks=math.ceil(len(df)/self.DATA_INFLUXDB3_CHUNCK_SIZE_API)
        monitorProgress.set_total_items(nbChunks)

        databaseStr=dbconf["database"]
        tableStr=dbconf["table"]
        monitorProgress.msg(msg=[logName,f"injecting {len(df)} entries ({nbChunks} chunk(s)) into {databaseStr}/{tableStr}"])

        with InfluxDBClient3(host=dbconf["url"], token=token, database=databaseStr) as client:
            for chuckNb in range(nbChunks):

                if abortEvent and abortEvent.is_set(): return

                curStartLoc=int(chuckNb*self.DATA_INFLUXDB3_CHUNCK_SIZE_API)
                curEndLoc=int(curStartLoc+self.DATA_INFLUXDB3_CHUNCK_SIZE_API)

                curDataSegment=df.iloc[curStartLoc:curEndLoc]
                if isinstance(curDataSegment,pd.Series):
                    curDataSegment=curDataSegment.to_frame()

                if len(curDataSegment)==0:
                    raise ValueError(f"No value found for '{df.columns}' in chunk {chuckNb} / {nbChunks}")

                points=self._dfToPoints(curDataSegment,tableStr)
                if len(points)==0:
                    raise ValueError(f"No value found for '{df.columns}' in chunk {chuckNb} / {nbChunks}")

                try:
                    client.write(points)
                except Exception as e:
                    raise Exception(f"Unable to write data chunk: {e}")

                with dfInjectProgressLock: dfInjectProgress[dfPos]=(chuckNb+1)/nbChunks

                monitorProgress.complete_item(f"chunk_{chuckNb+1}/{nbChunks}")

        with dfInjectProgressLock: dfInjectProgress[dfPos]=1

        timestampStr=datetime.fromtimestamp(df.index[0]/1e9).strftime('%Y-%m-%d %H:%M:%S.%f')[:-6]\
            +" - "+datetime.fromtimestamp(df.index[-1]/1e9).strftime('%Y-%m-%d %H:%M:%S.%f')[:-6]

        monitorProgress.msg(msg=[logName,f"injected {len(df)} entries",timestampStr], msgSeverity="success")

    #######################
    def inject_to_db(self,
                     dbconf: dict,
                     fieldsDfList: list,
                     token: str,
                     monitorProgress: MonitorProgress | None = None,
                     nbWorkers: int = MAX_MTHREAD_WORKERS,
                     abortEvent: threading.Event | None = None) -> list:

        monitorProgress.set_total_items(len(fieldsDfList))
        
        if "database" not in dbconf:
            raise ValueError("missing 'database' (used as the InfluxDB v3 database name) in provided influxdb3 conf")

        if "table" not in dbconf:
            raise ValueError("missing 'table' (used as the InfluxDB v3 table name) in provided influxdb3 conf")

        databaseStr=dbconf["database"]
        tableStr=dbconf["table"]
        monitorProgress.msg(msg=f"checking database '{databaseStr}' and table '{tableStr}' exist (and create them if needed)")
        self.ensure_db_and_table_if_missing(dbconf, token, monitorProgress)

        dfInjectProgress=[0] * len(fieldsDfList)
        dfInjectProgressLock=threading.Lock()

        workersData=[]
        dfIdx=0

        for df in fieldsDfList:
            subMp=monitorProgress.child(f"dbinject - {fieldsDfList[dfIdx].name}")
            workersData.append({
                "dfPos":dfIdx,
                "dbconf":dbconf,
                "fieldsDfList":fieldsDfList,
                "monitorProgress":subMp,
                "token":token,
                "dfInjectProgress":dfInjectProgress,
                "dfInjectProgressLock":dfInjectProgressLock,
                "abortEvent":abortEvent
            })
            dfIdx+=1

        with ThreadPoolExecutor(max_workers=nbWorkers) as executor:
            futures = [executor.submit(self._influx3UploadWorkerApi, workerData) for workerData in workersData]
            wait(futures)
            for fut in futures: fut.result()

        return dfInjectProgress
    

#################################################################
class ClickhouseData2Db(Data2Db):
    """ClickHouse implementation of Data2Db.

    Notes:
    - data upload uses the 'clickhouse_driver' package (native protocol, default port 9000)
    - connection is configured in dbconf: 'host' (required), 'port' (OPTIONAL, default 9000),
      'user' (OPTIONAL, default 'default'), 'password' (OPTIONAL, the 'token' argument is used as password if absent)
    - the 'database' entry of dbconf is used as the ClickHouse database name
    - the 'table' entry of dbconf is used as the ClickHouse table name
    - the dataframe index (nanoseconds) is uploaded into a 'ts' DateTime64(6) column
      (a different name can be set with the 'timestampColumn' key, OPTIONAL)
    - the database and the table are created automatically if missing, before the upload
      (missing field columns are added to an existing table)

    Example of db configuration:
        {
            "dbtype" : "clickhouse",
            "host" : "clickhouse",
            "port" : 9000, (OPTIONAL)
            "user" : "default", (OPTIONAL)
            "password" : "...", (OPTIONAL)
            "database" : "testDb",
            "table" :"testTable",
            "timestampColumn" :"ts", (OPTIONAL)
            'dateUnit': "s", (OPTIONAL) (s|ms|us|ns)
            'dateOffset': "now|12584s|2024-10-12 00:00:00", (OPTIONAL)
            'minDate': "2024-10-12 12:13:00", (OPTIONAL)
            'maxDate': "2024-10-12 12:17:32", (OPTIONAL)
            'verticalOffset': "autoz|42", (OPTIONAL)
            'timezone': "Europe/Paris", (OPTIONAL)
            'rename_fields_re_match': ".*/([^/]+)$", (OPTIONAL)
            'rename_fields_re_replace': "\\1" (OPTIONAL)
        }
    """

    DB_TYPE = "clickhouse"

    DATA_CLICKHOUSE_CHUNK_SIZE_API = 20e3

    #: pandas dtype (as string) -> ClickHouse column type (fallback: String)
    _PANDAS_TO_CLICKHOUSE_TYPES = {
        "bool":"UInt8",
        "boolean":"UInt8",
        "int8":"Int8", "int16":"Int16", "int32":"Int32", "int64":"Int64",
        "uint8":"UInt8", "uint16":"UInt16", "uint32":"UInt32", "uint64":"UInt64",
        "float32":"Float32", "float64":"Float64",
        "string":"String",
        "datetime64[ns]":"DateTime64(6)",
    }

    #######################
    @classmethod
    def _clickhouseColumnType(cls, pandasDtype) -> str:
        """Map a pandas dtype to a ClickHouse column type (fallback: String)."""
        return cls._PANDAS_TO_CLICKHOUSE_TYPES.get(str(pandasDtype),"String")

    @staticmethod
    def _clickhouseFieldColumns(df) -> list:
        """List of (columnName, series) pairs from a field entry (Series or DataFrame)."""
        if isinstance(df,pd.DataFrame):
            return list(df.items())
        return [(df.name,df)]

    @staticmethod
    def _cleanClickhouseValue(val):
        """Normalize a value for ClickHouse insertion (numpy scalars to python types, NaN/None to None)."""
        if val is None:
            return None
        if hasattr(val,"item"): val=val.item()
        if isinstance(val,float) and math.isnan(val):
            return None
        return val

    def _createClickhouseClient(self, dbconf: dict, token: str):
        """Create a 'clickhouse_driver' Client from the dbconf connection keys.
        The 'password' key is used if present, otherwise the provided token is used as password."""
        from clickhouse_driver import Client

        url=dbconf["url"]
        m=re.match(r"https?://([^:]+):(\d+)",url)
        host=m.group(1)
        port=int(m.group(2))
        password=dbconf.get("password") or (token or "")
        return Client(host=host,
                      port=port,
                      user=dbconf.get("user","default"),
                      password=password)

    #######################
    def ensure_db_and_table_if_missing(self,
                                       dbconf: dict,
                                       token: str,
                                       fieldsDfList: list,
                                       monitorProgress: MonitorProgress | None = None) -> None:
        """Ensure the ClickHouse database and table exist, creating them if missing.

        When the table does not exist, it is created (MergeTree, ordered on the timestamp column)
        with one column per field, with types inferred from the provided dataframes.
        When the table already exists, missing field columns are added to it.

        :param dbconf (dict): db config ('host', 'port', 'user', 'password', 'database', 'table')
        :param token (str): ClickHouse password (used when dbconf has no 'password')
        :param fieldsDfList (list): field DataFrames used to infer the table schema
        :param monitorProgress (MonitorProgress): progress monitoring obj
        """

        databaseStr=dbconf["database"]
        tableStr=dbconf["table"]
        tsCol=dbconf.get("timestampColumn","ts")

        client=self._createClickhouseClient(dbconf,token)
        try:
            # database
            existingDbs=[row[0] for row in client.execute("SELECT name FROM system.databases")]
            if databaseStr not in existingDbs:
                client.execute(f"CREATE DATABASE `{databaseStr}`")
                monitorProgress and monitorProgress.msg(msg=f"created new database '{databaseStr}'")
            else:
                monitorProgress and monitorProgress.msg(msg=f"database '{databaseStr}' already exists")

            # table
            existingTables=[row[0] for row in client.execute("SELECT name FROM system.tables WHERE database = %s",[databaseStr])]
            if tableStr not in existingTables:
                colTypes={}
                for df in fieldsDfList:
                    for col,series in self._clickhouseFieldColumns(df):
                        if col in colTypes: continue
                        baseType=self._clickhouseColumnType(series.dtype)
                        colTypes[col]=f"Nullable({baseType})" if series.isna().any() else baseType
                colDefs=", ".join([f"`{tsCol}` DateTime64(6)"]+[f"`{col}` {chType}" for col,chType in colTypes.items()])
                client.execute(f"CREATE TABLE `{databaseStr}`.`{tableStr}` ({colDefs}) ENGINE = MergeTree() ORDER BY `{tsCol}`")
                monitorProgress and monitorProgress.msg(msg=f"created new table '{tableStr}' in database '{databaseStr}'")
            else:
                existingCols={row[0]:row[1] for row in client.execute("SELECT name, type FROM system.columns WHERE database = %s AND table = %s",[databaseStr,tableStr])}
                if tsCol not in existingCols:
                    raise ValueError(f"existing table '{databaseStr}.{tableStr}' has no timestamp column '{tsCol}' (rename it, or set dbconf['timestampColumn'])")
                for df in fieldsDfList:
                    for col,series in self._clickhouseFieldColumns(df):
                        if col in existingCols: continue
                        baseType=self._clickhouseColumnType(series.dtype)
                        chType=f"Nullable({baseType})" if series.isna().any() else baseType
                        client.execute(f"ALTER TABLE `{databaseStr}`.`{tableStr}` ADD COLUMN `{col}` {chType}")
                        monitorProgress and monitorProgress.msg(msg=f"added missing column '{col}' to table '{tableStr}'")
                monitorProgress and monitorProgress.msg(msg=f"table '{tableStr}' already exists in database '{databaseStr}'")
        finally:
            client.disconnect()

    #######################
    def _clickhouseUploadWorkerApi(self, workerData: dict) -> None:
        """Worker for multithread DB upload (each thread dedicated to a field)
        :param workerData (dict): some context info to be provided to the worker"""

        dfPos=workerData["dfPos"]
        dbconf=workerData["dbconf"]
        monitorProgress=workerData["monitorProgress"]
        fieldsDfList=workerData["fieldsDfList"]
        token=workerData["token"]
        dfInjectProgress=workerData["dfInjectProgress"]
        abortEvent=workerData["abortEvent"]
        dfInjectProgress[dfPos]=0
        dfInjectProgressLock=workerData["dfInjectProgressLock"]

        df=fieldsDfList[dfPos]
        oriMinDate_ns=df.index[0]
        oriMaxDate_ns=df.index[-1]

        df=self.prepare_df_for_db_upload(dbconf,df)

        if len(df)==0:
            warningDates=""
            if dbconf.get("minDate") or dbconf.get("maxDate"):
                origMinDateStr=datetime.fromtimestamp(oriMinDate_ns/1e9).strftime('%Y-%m-%d %H:%M:%S.%f')[:-6]
                origMaxDateStr=datetime.fromtimestamp(oriMaxDate_ns/1e9).strftime('%Y-%m-%d %H:%M:%S.%f')[:-6]
                warningDates=f" Maybe provided min/max dates were not appropriate ? data original time range is  [ {origMinDateStr}, {origMaxDateStr} ]"
            raise ValueError(f"No value found for '{df.columns}'.{warningDates}")

        logName=",".join(df.columns)

        nbChunks=math.ceil(len(df)/self.DATA_CLICKHOUSE_CHUNK_SIZE_API)
        monitorProgress.set_total_items(nbChunks)

        databaseStr=dbconf["database"]
        tableStr=dbconf["table"]
        tsCol=dbconf.get("timestampColumn","ts")
        monitorProgress.msg(msg=[logName,f"injecting {len(df)} entries ({nbChunks} chunk(s)) into {databaseStr}.{tableStr}"])

        client=self._createClickhouseClient(dbconf,token)

        cols=list(df.columns)
        if isinstance(df.index,pd.DatetimeIndex):
            tsDts=df.index.to_pydatetime()
        else:
            tsDts=pd.to_datetime(df.index,unit="ns").to_pydatetime()

        insertColsStr=", ".join(f"`{c}`" for c in [tsCol]+cols)
        insertQuery=f"INSERT INTO `{databaseStr}`.`{tableStr}` ({insertColsStr}) VALUES"

        try:
            for chunkNb in range(nbChunks):

                if abortEvent and abortEvent.is_set(): return

                curStartLoc=int(chunkNb*self.DATA_CLICKHOUSE_CHUNK_SIZE_API)
                curEndLoc=int(curStartLoc+self.DATA_CLICKHOUSE_CHUNK_SIZE_API)

                curDataSegment=df.iloc[curStartLoc:curEndLoc]
                if isinstance(curDataSegment,pd.Series):
                    curDataSegment=curDataSegment.to_frame()

                if len(curDataSegment)==0:
                    raise ValueError(f"No value found for '{df.columns}' in chunk {chunkNb} / {nbChunks}")

                curTsDts=tsDts[curStartLoc:curEndLoc]
                rows=[(tsVal,)+tuple(map(self._cleanClickhouseValue,vals))
                      for tsVal,vals in zip(curTsDts,curDataSegment.itertuples(index=False,name=None))]

                try:
                    client.execute(insertQuery,rows)
                except Exception as e:
                    raise Exception(f"Unable to write data chunk: {e}")

                with dfInjectProgressLock: dfInjectProgress[dfPos]=(chunkNb+1)/nbChunks

                monitorProgress.complete_item(f"chunk_{chunkNb+1}/{nbChunks}")
        finally:
            client.disconnect()

        with dfInjectProgressLock: dfInjectProgress[dfPos]=1

        timestampStr=datetime.fromtimestamp(df.index[0]/1e9).strftime('%Y-%m-%d %H:%M:%S.%f')[:-6]\
            +" - "+datetime.fromtimestamp(df.index[-1]/1e9).strftime('%Y-%m-%d %H:%M:%S.%f')[:-6]

        monitorProgress.msg(msg=[logName,f"injected {len(df)} entries",timestampStr], msgSeverity="success")

    #######################
    def inject_to_db(self,
                     dbconf: dict,
                     fieldsDfList: list,
                     token: str,
                     monitorProgress: MonitorProgress | None = None,
                     nbWorkers: int = MAX_MTHREAD_WORKERS,
                     abortEvent: threading.Event | None = None) -> list:

        monitorProgress.set_total_items(len(fieldsDfList))

        if "database" not in dbconf:
            raise ValueError("missing 'database' (used as the ClickHouse database name) in provided clickhouse conf")

        if "table" not in dbconf:
            raise ValueError("missing 'table' (used as the ClickHouse table name) in provided clickhouse conf")

        if "url" not in dbconf:
            raise ValueError("missing 'url' in provided clickhouse conf")

        databaseStr=dbconf["database"]
        tableStr=dbconf["table"]
        monitorProgress.msg(msg=f"checking database '{databaseStr}' and table '{tableStr}' exist (and create them if needed)")
        self.ensure_db_and_table_if_missing(dbconf, token, fieldsDfList, monitorProgress)

        dfInjectProgress=[0] * len(fieldsDfList)
        dfInjectProgressLock=threading.Lock()

        workersData=[]
        dfIdx=0

        for df in fieldsDfList:
            subMp=monitorProgress.child(f"dbinject - {fieldsDfList[dfIdx].name}")
            workersData.append({
                "dfPos":dfIdx,
                "dbconf":dbconf,
                "fieldsDfList":fieldsDfList,
                "monitorProgress":subMp,
                "token":token,
                "dfInjectProgress":dfInjectProgress,
                "dfInjectProgressLock":dfInjectProgressLock,
                "abortEvent":abortEvent
            })
            dfIdx+=1

        with ThreadPoolExecutor(max_workers=nbWorkers) as executor:
            futures = [executor.submit(self._clickhouseUploadWorkerApi, workerData) for workerData in workersData]
            wait(futures)
            for fut in futures: fut.result()

        return dfInjectProgress

######################################### FACTORY #######################################################
DATA2DB_IMPLEMENTATIONS: dict[str, type[Data2Db]] = {
    InfluxdbV2Data2Db.DB_TYPE: InfluxdbV2Data2Db,
    InfluxdbV3Data2Db.DB_TYPE: InfluxdbV3Data2Db,
    ClickhouseData2Db.DB_TYPE: ClickhouseData2Db,
}

def create_data2db(dbtype: str) -> Data2Db:
    """Create an instance of the Data2Db implementation matching the given dbtype.

    :param dbtype (str): value of conf["db"]["dbtype"] (ex: "influxdb")
    :raises ValueError: if no implementation is registered for this dbtype
    """
    cls=DATA2DB_IMPLEMENTATIONS.get(dbtype)
    if cls is None:
        raise ValueError(f"Unhandled DB type '{dbtype}'. Available types: {sorted(DATA2DB_IMPLEMENTATIONS)}")
    return cls()