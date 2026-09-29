#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

class IntegrityReport(object):
    """ External hashes of the data as stored in SHADOX server

    Attributes:
        mode (str): which variant of the algorithm was used
        hashes (dict): hashes of the parameter values
            dict key = parameter path + ':' + value kind name
        durationServer (int): time in millisecond the server took to generate the report
        durationClient (int): time in millisecond it took for the server to respond
        timings (IntegrityTimings, optional): detailed information about the performance
    """
    def __init__(self, json_data, duration_client):
        self.mode = json_data['mode']
        self.hashes = json_data['hashesByParameter']
        self.durationServer = json_data.get('duration', None)
        self.durationClient = duration_client
        self.debug = json_data.get('debug', None)

    def __repr__(self):
        return u'IntegrityReport[' \
            + 'hashes=' + str(len(self.hashes)) \
            + ',mode=' + str(self.mode) \
            + ',duration=' + str(self.durationClient) + 'ms' \
            + (',durationServer=' + str(self.durationServer) + 'ms' if self.durationServer else '') \
            + (',hasDebug' if self.debug else '') \
            + ']'


class IntegrityInternalReport(object):
    """ Internal hashes of the data as stored in SHADOX server

    Attributes:
        hashesFrom (str):
            CURRENT_VALUE = those hashes were just computed based on current values of entities in SHADOX server
            HASH_REFERENTIAL = those hashes were fetched from hashes referential in SHADOX server
        hashes (dict<dict<str>>): 2-layer dict of hashes for each entity
            key1: entity type (datasetSnapshot / parameter / parameterValue / etc...)
            key2: entity id (ex: for parameter, id of this parameter)
            value: hash (in mode SHADOX) for this entity with this id
    """
    def __init__(self, json_data):
        self.hashesFrom = json_data['from']
        self.hashes = json_data['hashes']

    def __repr__(self):
        return u'IntegrityInternalReport[' \
            + 'hashesFrom=' + str(self.hashesFrom) \
            + ',hashes={' + ','.join('{}:{}'.format(entityType, len(self.hashes[entityType])) for entityType in self.hashes) + "}" \
            + ']'
