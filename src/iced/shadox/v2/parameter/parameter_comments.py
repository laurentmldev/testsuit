#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import warnings
from ..exceptions import ShadoxParameterValueNotFoundException, ShadoxUnauthorizedException


class ParameterComments(object):
    def __init__(self, collection):
        self._collection = collection

    def __dir__(self):
        """
        :return list of existing valuekinds
        """
        return dir(self._collection)

    def __iter__(self):
        for v in self._collection:
            yield v.comment

    def __setattr__(self, key, value):
        if key.startswith('_'):
            super(ParameterComments, self).__setattr__(key, value)
        else:
            getattr(self._collection, key).comment = value

    def __getattr__(self, key):
        if key.startswith('_'):
            return super(ParameterComments, self).__getattr__(key)
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("always")
                warnings.warn(
                    "Please use parameter.value_kinds.<value_kind_name>.value = <value> instead",
                    DeprecationWarning,
                    stacklevel=2
                )
            return getattr(self._collection, key).comment
