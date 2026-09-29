#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import json
import os
import requests

from .utils import log_action

from .exceptions import (
    ShadoxMissingParametersException,
    ShadoxUnauthorizedException,
    ShadoxForbiddenException,
    ShadoxNotFoundException,
    ShadoxInternalException,
    ShadoxAPIKeyRevokedException,
    ShadoxAPIKeyExpiredException,
)

class ShadoxHTTPHelper(object):
    """
        INTERNAL USE ONLY
        Helper providing basic HTTP methods to call Shadox API (v1) with a specific API key
    """

    def __init__(
            self,
            api_url,
            api_key,
            agent_name,
            logger_level,
            certificates_path='/etc/ssl/certs/shadox/',
            proxies=None,  # Requests module proxies dict: proxies = {'http': 'http_proxy', 'https': 'https_proxy'}
    ):
        self.logger_level = logger_level
        log_action(u"### Init HTTP helper", 4, self.logger_level)
        self.proxies = proxies

        self.ssl_certificates_path = certificates_path
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

        self.api_url = api_url + 'v1/' if api_url.endswith('/') else api_url + '/v1/'
        self.api_key = api_key
        self.agent_name = agent_name

    @staticmethod
    def _handle_exception(result):
        if result.status_code < 400:
            return
        message = None
        try:
            message = result.text
            try:
                res_json = json.loads(message)
                if res_json['message']:
                    message = res_json['message']
            except:
                pass
        except:
            pass
        disp_message = " - Message = " + message if message else ""
        if result.status_code == 500:
            if message != None:
                raise ShadoxInternalException(message)
            raise ShadoxInternalException(result)
        if result.status_code == 400:
            raise ShadoxForbiddenException("Server refused the request because of bad parameters." + disp_message)
        if result.status_code == 401:
            res_json = json.loads(result.text)
            if res_json['error'] == 'ShadoxAPIKeyExpiredException':
                raise ShadoxAPIKeyExpiredException("The API key has expired!")
            if res_json['error'] == 'ShadoxAPIKeyRevokedException':
                raise ShadoxAPIKeyRevokedException("The API key is revoked!")
            raise ShadoxUnauthorizedException(message)
        if result.status_code == 403:
            raise ShadoxForbiddenException("The request was forbidden" + disp_message)
        if result.status_code == 404:
            raise ShadoxNotFoundException("Resource not found" + disp_message)
        raise ShadoxInternalException("Unhandled HTTP status {}{}".format(result.status_code, disp_message))

    def _build_headers(self, upload=False):
        """
        :return dict
        :raise ShadoxUnauthorizedException
        """
        if not self.api_key:
            raise ShadoxUnauthorizedException('No API key provided')
        return {
            'Content-Type': None if upload else 'application/json',
            'Accept': 'application/json',
            'User-Agent': self.agent_name,
            'X-Api-Key': self.api_key
        }

    def get(self, url):
        """
        :param url:
        :param headers:
        :raise ShadoxMissingParameters
        :raise ShadoxUnauthorizedException
        :raise ShadoxForbiddenException
        :raise ShadoxNotFoundException
        :returns json
        """
        url = self.api_url + url
        log_action(u"# Get url '{}'".format(url), 5, self.logger_level)
        headers = self._build_headers()
        try:
            result = requests.get(url=url, headers=headers,
                                  verify=self.ssl_certificates_path, proxies=self.proxies)
        except requests.exceptions.MissingSchema:
            raise ShadoxMissingParametersException("There is parameters missing")

        self._handle_exception(result)

        return result.json()

    def post(self, url, data=None, return_type='json', files=None):
        """
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
        url = self.api_url + url
        log_action(u"# Post url '{}'".format(url), 5, self.logger_level)
        headers = self._build_headers(files is not None)
        try:
            result = requests.post(url=url, json=data, headers=headers,
                                   files=files, verify=self.ssl_certificates_path,
                                   proxies=self.proxies)
        except requests.exceptions.MissingSchema:
            raise ShadoxMissingParametersException("There is parameters missing")

        self._handle_exception(result)

        if return_type == 'json':
            res = result.text
            try:
                return json.loads(res)
            except:
                raise ShadoxInternalException('Expected JSON body but got : {}'.format(res))
        return result

    def __repr__(self):
        return 'ShadoxHTTPHelper[api_url={}, api_key={}]'.format(
            self.api_url,
            self.api_key,
        )
