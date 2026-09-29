#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from .parameter import Parameter


class Snapshot:
    def __init__(self, shadox_client, json_data):
        self.id = json_data['id']
        self.branch = json_data['branch']
        self.workingCopy = json_data['workingCopy']
        self.version = json_data['version']
        self.parameters = [Parameter(shadox_client, x) for x in json_data['parameters']]
        self._client = shadox_client

    def search_parameter_by_id(self, param_id):
        for p in self.parameters:
            if p.id == param_id:
                return p

        return None

    def search_parameter_by_alias(self, param_alias):
        for p in self.parameters:
            if param_alias in p.alias:
                return p

        return None

    def refresh_with(self, refreshed_snapshot):
        self.branch = refreshed_snapshot.branch
        self.workingCopy = refreshed_snapshot.workingCopy
        self.version = refreshed_snapshot.version

        # Add new parameters
        for p in refreshed_snapshot.parameters:
            if not self.search_parameter_by_id(p.id):
                self.parameters.append(p)

        indexes_to_remove = []

        # Update and remove current parameters
        for i, p in enumerate(self.parameters):
            # Search the refresh parameter
            refreshed_parameter = refreshed_snapshot.search_parameter_by_id(p.id)
            # If we found it, refresh fields
            if refreshed_parameter:
                p.refresh_with(refreshed_parameter)
            else:
                indexes_to_remove.append(i)

        # Remove now the parameters not found (reverse to avoid indexes to change)
        if len(indexes_to_remove) > 0:
            for i in reversed(indexes_to_remove):
                self.parameters.pop(i)
