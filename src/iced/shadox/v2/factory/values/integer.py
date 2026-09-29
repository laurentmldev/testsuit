# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import numpy
from six import integer_types
from . import NoneType
ALLOWED_TYPES = integer_types + (numpy.int32, numpy.int_, NoneType)


class IntegerValue(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(shadox_value, *unused):
        if shadox_value is None:
            return None
        return int(float(shadox_value))

    @staticmethod
    def check(value, *unused):
        return type(value) in ALLOWED_TYPES

    @staticmethod
    def format(value, *unused, **kwunused):
        return value

    @staticmethod
    def format_dataframe(numpy_value, *unused, **kwunused):
        return int(float(numpy_value))
