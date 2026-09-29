#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import getpass
import requests
import re
import os
import tempfile
import json
from .exceptions import (ShadoxMissingParametersException, ShadoxUnauthorizedException,
                         ShadoxForbiddenException, ShadoxNotFoundException, ShadoxParameterNotFoundException,
                         ShadoxParameterValueNotFoundException, ShadoxInternalException,
                         ShadoxAPIKeyRevokedException, ShadoxAPIKeyExpiredException)
from .snapshot import Snapshot
from .utils import log_action
from ..version import __version__


class ShadoxClient:
    """
        Python Client wrapper for Shadox API v1
    """
    __version__ = __version__

    def __init__(
            self,
            api_url,
            api_key,
            project_key=None,
            dataset_path=None,
            selector_type=None,
            selector_value=None,
            agent_name="Python Shadox API Wrapper",
            dataset_urn=None,
            logger_level=0,
            use_version=False):

        self.loggerLevel = logger_level
        log_action("### Init client variables", 4, self.loggerLevel)
        self.api_key = api_key
        self.project_key = project_key
        self.dataset_path = dataset_path
        self.selector_type = selector_type
        self.selector_value = selector_value
        self.ssl_certificates_path = '/etc/ssl/certs/shadox/'
        self.use_version = use_version

        if not os.path.exists(self.ssl_certificates_path):
            # Will trigger a warning when running a request to a HTTPS server
            self.ssl_certificates_path = False

        # Sadly ASL's certificate doesn't contain a SubjectAltName field and it triggers
        # a warning from urllib3 (See https://github.com/shazow/urllib3/issues/497)
        # A new certificate will be generated one day to include this field *and*
        # use a more recent hashing algorithm than sha1. When done, this warning suppression
        # can be removed from the code
        from requests.packages.urllib3.exceptions import SubjectAltNameWarning
        requests.packages.urllib3.disable_warnings(SubjectAltNameWarning)

        # API url
        self.api_url = api_url + 'v1/' if api_url.endswith('/') else api_url + '/v1/'

        # Agent name
        self.agent_name = agent_name

        # Snapshot path
        if dataset_urn:
            self.snapshot_path = dataset_urn
        else:
            self.snapshot_path = "project/{}/dataset/{}/{}/{}".format(self.project_key,
                                                                      self.dataset_path,
                                                                      self.selector_type,
                                                                      self.selector_value)

        # Fetch snapshot
        self.snapshot = self.fetch_snapshot()

    def ls(self):
        """
        Return the list of parameters for the snapshot
        :return [Parameter]
        """
        return self.snapshot.parameters

    def get_parameter(self, search=''):
        """
        Search for a parameter by id "id:{id}" or by alias "alias:{alias}"
        :return Parameter
        """
        log_action("|- Search parameter '{}'".format(search), 2, self.loggerLevel)

        fullSearch = search
        parameter = None
        if re.match("^id:", search):
            # Search by id
            search = search.replace("id:", "")
            log_action("|--- Search by id '{}'".format(search), 3, self.loggerLevel)
            parameter = self.snapshot.search_parameter_by_id(search)
        elif re.match("^alias:", search):
            # Search by alias
            search = search.replace("alias:", "")
            log_action("|--- Search by alias '{}'".format(search), 3, self.loggerLevel)
            parameter = self.snapshot.search_parameter_by_alias(search)

        if not parameter:
            raise ShadoxParameterNotFoundException("Parameter '"  + fullSearch + "' not found")

        return parameter

    def _build_headers(self, upload=False):
        """
        Return the standard headers for requests
        :return dict
        """
        return {
            'Content-Type': None if upload else 'application/json',
            'Accept': 'application/json',
            'User-Agent': self.agent_name,
            'X-Api-Key': self.api_key
        }

    def fetch_snapshot(self):
        """
        Fetch a snapshot and instantiate an object from api data
        :return Snapshot
        """
        log_action("|- Fetch snapshot " + self.snapshot_path, 2, self.loggerLevel)

        json_result = self._get(self.api_url + self.snapshot_path, self._build_headers())
        return Snapshot(self, json_result)

    def get_parameter_value(self, parameter_id, value):
        url = self.api_url + self.snapshot_path + '/values/fetch'
        data = {'selectors': [{"id": parameter_id, "value": value}]}
        json_result = self._post(url, data=data, headers=self._build_headers())
        if len(json_result['values']) > 0:
            return json_result['values'][0]['data']
        raise ShadoxParameterValueNotFoundException("Parameter Value not found!")

    def is_dirty(self):
        """
        Indicates if a push is required to send updated data to the server
        """
        return any(p.is_dirty() for p in self.snapshot.parameters)

    def refresh(self):
        """Refresh the snapshot and parameters with the last updates"""
        refreshed_snapshot = self.fetch_snapshot()
        self.snapshot.refresh_with(refreshed_snapshot)

    def restart(self):
        self.snapshot = self.fetch_snapshot()

    def push(self):
        """
        Push all modifications done while the client is running
        :return:
        """
        log_action("|- Prepare updates", 3, self.loggerLevel)

        # Build an array of values to update
        values = []
        updates = []
        for p in self.snapshot.parameters:
            update = p.build_update()
            if len(update['values']) > 0:
                values += update['values']
                updates.append([
                    p.id,
                    ','.join(p.alias),
                    ','.join(update['keys'])
                ])

        if len(values) == 0:
            return False

        # Build url and parameters
        data = dict(
            values=values
        )
        url = self.api_url + self.snapshot_path + '/values/save'
        if self.use_version:
            url += '?version=' + str(self.snapshot.version)
        log_action("|- Push updates", 3, self.loggerLevel)
        json_result = self._post(url, data=data, headers=self._build_headers())
        self.snapshot.version = json_result['version']
        # Reset hashes for parameters
        for p in self.snapshot.parameters:
            p.refresh_hash()

        # Pretty print the updates
        log_action("|- Updates", 0, self.loggerLevel)
        columns = ['Parameter id', 'Parameter aliases', 'Values updated']
        row_format = "{:<40} " * len(columns)
        log_action(row_format.format(*columns), 0, self.loggerLevel)
        for u in updates:
            log_action(row_format.format(*u), 0, self.loggerLevel)

    def download_file(self, parameter_id, value_key, file_value):
        """
        Download a file
        :param parameter_id:
        :param value_key:
        :param file_value:
        :return:
        """
        # Empty path if no fields
        if not ('identifier' in file_value) or not ('name' in file_value):
            return ''

        # Prepare dir and file path
        temp_dir = self._prepare_temp_dir(file_value['identifier'])
        file_path = temp_dir + '/' + file_value['name']

        # Build url and parameters
        data = {
            'id': parameter_id,
            'value': value_key,
            'fileRef': file_value['identifier']
        }
        url = self.api_url + self.snapshot_path + '/values/download'

        log_action(
            "|--- Downloading file for value '{}' : '{}'".format(value_key, file_value['name']),
            1,
            self.loggerLevel
        )
        res = self._post(url, data=data, headers=self._build_headers(), return_type='file')
        with open(file_path, 'wb') as f:
            for chunk in res.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)

        return file_path

    def upload_file(self, parameter, value_key, file_path, extra_fields=None):
        """
        Upload a file and send back a dict with file infos
        :param parameter:
        :param value_key:
        :param file_path:
        :param extra_fields:
        :return:
        """

        # Allow file to be none
        if file_path is None:
            return None

        file_name = os.path.basename(file_path)

        # Build url and parameters
        # Note: use "with" to force close file after upload
        with open(file_path, 'rb') as file:
            files = {'file': file}
            url = self.api_url + self.snapshot_path + '/values/upload?id=' + parameter.id + '&value=' + value_key

            log_action(
                "|--- Uploading file for value '{}' : '{}'".format(value_key, file_name),
                1,
                self.loggerLevel
            )
            res = self._post(url, headers=self._build_headers(True), files=files)
        self.snapshot.version = res['nextSnapshotVersion']
        uploaded_file = {
            'name': file_name,
            'size': os.path.getsize(file_path),
            'identifier': res['fileRef'],
            'path': file_path
        }

        if extra_fields and isinstance(extra_fields, list):
            for ef in extra_fields:
                uploaded_file[ef['key']] = ef['value']

        return uploaded_file

    def _prepare_temp_dir(self, identifier):
        """
        Check if the tmp dir is created, if not create and send the path
        :return:
        """
        # Create the temporary directory for the user
        temp_dir = tempfile.gettempdir() + '/shadox_files'
        # If the directory is /tmp (on unix with no $TMP env variable
        # We create a temporary directory for the user, to avoid conflicts on the same file
        if temp_dir == '/tmp/shadox_files':
            temp_dir = '/tmp/shadox_files_{}'.format(getpass.getuser())

        log_action("|--- Prepare temporary directory '{}'".format(temp_dir), 4, self.loggerLevel)
        if not os.path.exists(temp_dir):
            log_action("|--- Create directory", 4, self.loggerLevel)
            os.makedirs(temp_dir)

        temp_dir += '/' + identifier
        if not os.path.exists(temp_dir):
            log_action("|----- Create identifier directory", 4, self.loggerLevel)
            os.makedirs(temp_dir)

        return temp_dir

    def _get(self, url, headers=None):
        """
        Send a GET request to api
        :param url:
        :param headers:
        :raise ShadoxMissingParameters
        :raise ShadoxUnauthorizedException
        :raise ShadoxForbiddenException
        :raise ShadoxNotFoundException
        :returns json
        """
        log_action("# Get url '{}'".format(url), 5, self.loggerLevel)
        try:
            result = requests.get(url=url, headers=headers, verify=self.ssl_certificates_path)
        except requests.exceptions.MissingSchema:
            raise ShadoxMissingParametersException("There is parameters missing")

        self._handle_exception(result)

        return result.json()

    def _post(self, url, headers=None, data=None, return_type='json', files=None):
        """
        Send a POST request to api
        :param url:
        :param headers:
        :param data:
        :param return_type:
        :param files:
        :raise ShadoxMissingParameters
        :raise ShadoxUnauthorizedException
        :raise ShadoxForbiddenException
        :raise ShadoxNotFoundException
        :returns json
        """
        log_action("# Post url '{}'".format(url), 5, self.loggerLevel)
        try:
            result = requests.post(url=url, json=data, headers=headers, files=files, verify=self.ssl_certificates_path)
        except requests.exceptions.MissingSchema:
            raise ShadoxMissingParametersException("There is parameters missing")

        self._handle_exception(result)

        if return_type == 'json':
            return result.json()
        else:
            return result

    def __iter__(self):
        """Send a parameters iterator"""
        return self.snapshot.parameters.__iter__()

    @staticmethod
    def _handle_exception(result):
        if result.status_code == 500:
            raise ShadoxInternalException(result)
        elif result.status_code == 401:
            res_json = json.loads(result.text)
            if res_json['error'] == 'ShadoxAPIKeyExpiredException':
                raise ShadoxAPIKeyExpiredException("The API key has expired!")
            elif res_json['error'] == 'ShadoxAPIKeyRevokedException':
                raise ShadoxAPIKeyRevokedException("The API key is revoked!")
            else:
                raise ShadoxUnauthorizedException(res_json['message'])
        elif result.status_code == 403:
            raise ShadoxForbiddenException("Access to resource is restricted (Maybe the snapshot is frozen ?)")
        elif result.status_code == 404:
            raise ShadoxNotFoundException("Resource not found")
