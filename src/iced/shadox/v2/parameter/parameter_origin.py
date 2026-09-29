#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from datetime import datetime
from ..exceptions import ShadoxUnauthorizedException

_FAKE_PUBLICATION_DATE = 1000*60*60*24*5 # obviously fake date, but needs to be big enough to be parsed by datetime.fromtimestamp

class ParameterOrigin(object):
    """Origin of a parameter or group

    Some information may not be available, depending on user rights on the origin publication

    Attributes:
        dataset_path (str): qualified name of the origin dataset
        publication_version (str): version number
        publication_variant (str): variant name
        publication_status ('DELIVERED' or 'DEPRECATED'): badge
        publication_date (datetime): date of publication
        deprecation_date (datetime, optional): if publication is deprecated, date of deprecation
        id (str): id of the original parameter or group
        path (str): folder + name of the original
        alias (list of str): list of aliases of the original
    """
    def __init__(self, origin):
        self.dataset_path = origin['datasetPath']
        self.publication_date = datetime.fromtimestamp(origin['publicationDate'] / 1000)
        self.deprecation_date = datetime.fromtimestamp(origin['deprecationDate'] / 1000) if origin['deprecationDate'] else None
        self.publication_version = origin['version']
        self.publication_variant = origin['variant']
        self.publication_status = origin['status']
        self.id = origin['id'] if 'id' in origin else None
        if not self.id and 'originParameterId' in origin:
            self.id = origin['originParameterId']
        self.path = origin.get('path', None)
        self.alias = origin.get('alias', None)

    def __repr__(self):
        return '{}/{}-{}({})'.format(
            self.dataset_path,
            self.publication_variant,
            self.publication_version,
            self.publication_status
        )

    def __eq__(self, other):
        if isinstance(other, ParameterOrigin):
            return self.__dict__ == other.__dict__
        return False

    def __ne__(self, other):
        """Python2 compatibility"""
        return not self.__eq__(other)

    @property
    def parameter_id(self):
        """Deprecated, use id

        Returns:
            str: Id of the original parameter
        """
        return self.id

def build_new_origin(self_id, dataset_path, variant = None, version = None, origin_id = None, origin_alias = None):
    if not self_id:
        raise ShadoxUnauthorizedException("parameter/group id must exist before modifying origin, push the client to obtain an id")
    origin_selector = {}
    if origin_id is not None:
        origin_selector['id'] = origin_id
    elif origin_alias is not None:
        origin_selector['alias'] = origin_alias
    elif dataset_path is None:
        origin_selector = None
    else:
        raise ShadoxUnauthorizedException("need either origin_id or origin_alias")

    updated_origin = {
        'id': self_id,
        'datasetPath': dataset_path,
        'variant': variant,
        'version': version,
        'originParameter': origin_selector
    }
    origin = ParameterOrigin({
        'datasetPath': dataset_path,
        'variant': variant,
        'version': version,
        'status': 'UNKNOWN, PUSH CLIENT TO FETCH ACTUAL INFORMATION',
        'publicationDate': _FAKE_PUBLICATION_DATE,
        'deprecationDate': None,
    }) if dataset_path else None

    return updated_origin, origin
