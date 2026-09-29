#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.


class BatchDependenciesUpdateDiff(object):
    def __init__(self, json_data):
        self.updated_parameters = json_data['updatedParameters']
        self.non_updated_parameters = json_data['nonUpdatedParameters']
        self.publications_updated = json_data['publicationsUpdated']

    def __repr__(self):
        return u'BatchDependenciesUpdateDiff[' \
            + (('updated_parameters=' + str(self.updated_parameters)) if self.updated_parameters else '') \
            + ((',non_updated_parameters=' + str(self.non_updated_parameters)) if self.non_updated_parameters else '') \
            + ((',publications_updated=' + str(self.publications_updated)) if self.publications_updated else '') \
            + ']'


class BatchDependenciesUpdateReport(object):
    def __init__(self, json_data):
        self.new_snapshot_id = json_data['newSnapshotId']
        self.diff = BatchDependenciesUpdateDiff(json_data['diff'])

    def __repr__(self):
        return u'BatchDependenciesUpdateReport[' \
            + ('new_snapshot_id=' + (self.new_snapshot_id if self.new_snapshot_id else '')) \
            + (',diff=' + str(self.diff)) \
            + ']'
