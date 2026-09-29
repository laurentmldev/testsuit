#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.
from iced.shadox.v2.parameter import Parameter


class ApiParametersUpdateResponse(object):
    def __init__(self, json_data):
        self.new_version = json_data['newVersion']
        self.groups = json_data['groups']
        self.tree = json_data.get('tree', [])
        self.updated_parameters = [Parameter(None, p, None) for p in json_data['updatedParameters']]
        self.deleted_parameter_ids = json_data['deletedParameterIds']

    def __repr__(self):
        return u"new_version:'{}' tree:'{}' groups:'{}' \nupdated_parameters:'{}'\n deleted_parameter_ids:'{}'".format(
            self.new_version, self.tree, self.groups, [p.__str__() for p in self.updated_parameters],
            self.deleted_parameter_ids
        )


class ApiDatasetSnapshotUpdate(object):
    def __init__(self, tree, parameters, groups):
        self.tree = tree
        self.parameters = parameters
        self.groups = groups

    def to_json(self):
        return {
            "tree": self.tree,
            "parameters": self.parameters,
            "groups": self.groups
        }
