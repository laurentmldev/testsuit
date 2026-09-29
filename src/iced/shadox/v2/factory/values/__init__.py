# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import numpy

NoneType = type(None)

from .integer import IntegerValue
from .double import DoubleValue
from .string import StringValue
from .boolean import BooleanValue
from .file import FileValue
from .notebook import NotebookValue
from .document import DocumentValue
from .reference import ReferenceValue

VALUE_TYPE_INTEGER = 'integer'
VALUE_TYPE_DOUBLE = 'double'
VALUE_TYPE_STRING = 'string'
VALUE_TYPE_BOOLEAN = 'boolean'
VALUE_TYPE_FILE = 'file'
VALUE_TYPE_NOTEBOOK = 'notebook'
VALUE_TYPE_DOCUMENT = 'document'
VALUE_TYPE_URI = 'reference'


dispatchMap = {
    VALUE_TYPE_INTEGER: IntegerValue,
    VALUE_TYPE_DOUBLE: DoubleValue,
    VALUE_TYPE_STRING: StringValue,
    VALUE_TYPE_BOOLEAN: BooleanValue,
    VALUE_TYPE_FILE: FileValue,
    VALUE_TYPE_NOTEBOOK: NotebookValue,
    VALUE_TYPE_DOCUMENT: DocumentValue,
    VALUE_TYPE_URI: ReferenceValue
}
numpyMap = {
    VALUE_TYPE_INTEGER: numpy.int64,
    VALUE_TYPE_DOUBLE: numpy.float64,
    VALUE_TYPE_STRING: str,
    VALUE_TYPE_BOOLEAN: numpy.bool_,
    VALUE_TYPE_FILE: object,
    VALUE_TYPE_URI: object
}
default_df_value_map = {
    VALUE_TYPE_INTEGER: 0,
    VALUE_TYPE_DOUBLE: 0.0,
    VALUE_TYPE_STRING: '',
    VALUE_TYPE_BOOLEAN: False,
    VALUE_TYPE_FILE: None,
    VALUE_TYPE_URI: None
}


class ValuesFactory(object):
    def __init__(self):
        pass

    @staticmethod
    def dispatch(value_type):
        if value_type not in dispatchMap:
            raise NotImplementedError("Shadox type '" + value_type + "' is not supported by the Python client")
        return dispatchMap[value_type]

    @staticmethod
    def default_df_value(value_type):
        if value_type not in default_df_value_map:
            raise NotImplementedError("Type '" + value_type + "' is not supported for dataframes")
        return default_df_value_map[value_type]

    @staticmethod
    def get_numpy_type(value_type):
        """
        Get the numpy type for a parameter value type
        :param value_type:
        :return:
        """
        if value_type not in numpyMap:
            raise NotImplementedError("Shadox type '" + value_type + "' has no numpy equivalence")
        return numpyMap[value_type]
