#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import warnings


class ParameterValues(object):
    def __init__(self, collection):
        self._collection = collection

    def __dir__(self):
        """
        :return list of existing valuekinds
        """
        return dir(self._collection)

    def __iter__(self):
        for v in self._collection:
            yield v.value

    def __getattr__(self, key):
        if key.startswith('_'):
            return super(ParameterValues, self).__getattr__(key)

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn(
                "Please use parameter.value_kinds.<value_kind_name>.value instead",
                DeprecationWarning,
                stacklevel=2
            )
        return getattr(self._collection, key).value

    def __setattr__(self, key, value):
        if key.startswith('_'):
            # default call to __setattr__
            super(ParameterValues, self).__setattr__(key, value)
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("always")
                warnings.warn(
                    "Please use parameter.value_kinds.<value_kind_name>.value = <value> instead",
                    DeprecationWarning,
                    stacklevel=2
                )
            getattr(self._collection, key).value = value

    def get_authorized_keys(self):
        return self._collection._authorized_keys

    def get_hash_values(self):
        return self._collection._hash_values

    def get_values(self):
        return self._collection._values

    def get_touched_keys(self):
        return self._collection._touched_keys
