# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import numpy
from . import NoneType
ALLOWED_TYPES = [bool, NoneType, numpy.bool_]


class BooleanValue(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(shadox_value, *unused):
        if shadox_value is None:
            return None
        return bool(shadox_value)

    @staticmethod
    def check(value, *unused):
        return type(value) in ALLOWED_TYPES

    @staticmethod
    def format(value, *unused, **kwunused):
        return value

    @staticmethod
    def format_dataframe(numpy_value, *unused, **kwunused):
        return bool(numpy_value)
