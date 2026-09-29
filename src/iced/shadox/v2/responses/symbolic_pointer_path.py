#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from iced.shadox.v2.factory.values.reference import Reference
from iced.shadox.v2.exceptions import ShadoxSymbolicPointerPathException
import re

class SymbolicPointerPath(object):
    def __init__(self, json_data):
        self.id = json_data['id']
        self.name = json_data['name']
        self.default_path = json_data['defaultSymbolicPointerPath']
        self.matches = [re.compile(pattern + '(.*)$') for pattern in json_data['matches']]

    def __repr__(self):
        return repr(self.__dict__)

    def decompose_file_path(self, file_path):
        if file_path.startswith(self.default_path):
            return self.default_path, file_path[len(self.default_path):]
        for r in self.matches:
            m = r.match(file_path)
            if m: # m.groups[-1] is the suffix
                prefix_end = m.start(len(m.groups()))
                return file_path[:prefix_end], m.groups()[-1]
        raise ShadoxSymbolicPointerPathException("Input URI does not match the symbolic pointer")

    def _build_uri(self, suffix, prefix=None):
        if not prefix or prefix==self.default_path:
            return self.default_path + suffix
        else:
            if self.decompose_file_path(prefix) == (prefix,""):
                return prefix+suffix
        raise ShadoxSymbolicPointerPathException("Input prefix does not match the SymbolicPointerPath")

    def build_uri(self, suffix, prefix=None):
        file_path = self._build_uri(suffix, prefix)
        return Reference(file_path, prefix=self, suffix=suffix)
