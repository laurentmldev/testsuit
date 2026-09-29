# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import numpy
from six import PY2, string_types, text_type, u
from . import NoneType
ALLOWED_TYPES = string_types + (str,)


class StringValue(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(shadox_value, *unused):
        if shadox_value is None:
            return None
        return shadox_value.encode('utf-8') if PY2 else str(shadox_value)

    @staticmethod
    def check(value, *unused):
        return isinstance(value, ALLOWED_TYPES) or type(value) == NoneType

    @staticmethod
    def format(value, *unused, **kwunused):
        return value

    @staticmethod
    def format_dataframe(numpy_value, *unused, **kwunused):
        if isinstance(numpy_value, text_type):
            return numpy_value
        return u(numpy_value)
