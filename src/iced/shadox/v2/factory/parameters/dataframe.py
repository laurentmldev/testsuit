# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import pandas
from ..values import ValuesFactory, VALUE_TYPE_FILE
from ...exceptions import ShadoxParameterValueTypeException
from . import NoneType

ALLOWED_TYPES = [dict, pandas.DataFrame, NoneType]


class DataframeParameter(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(parameter, value, key):
        if isinstance(value, pandas.DataFrame):
            # Already converted
            return value
        else:
            data = {}
            columns = []
            for c in parameter.definition.columns:
                value_type = c.content_type.type
                c_name = c.title
                if value is not None:
                    c_value = [None] * len(value[c_name])
                    for i, v in enumerate(value[c_name]):
                        c_value[i] = ValuesFactory.dispatch(value_type).convert(v, key, parameter)
                else:
                    c_value = []
                columns.append(c_name)
                data[c_name] = pandas.Series(c_value, dtype=ValuesFactory.get_numpy_type(value_type), name=c_name)
            return pandas.DataFrame(data, columns=columns)

    @staticmethod
    def check(parameter, value, key):
        # Check dataframe type
        if type(value) not in ALLOWED_TYPES:
            raise ShadoxParameterValueTypeException(
                'Wrong type for key "{}" in dataframe parameter "{}"'.format(key, parameter.id))

        # No sub-value for None (ie NA)
        if value is None:
            return

        # Check each value
        for c in parameter.definition.columns:
            value_type = c.content_type.type
            c_name = c.title
            for v in value[c_name]:
                if not ValuesFactory.dispatch(value_type).check(v, parameter, key):
                    raise ShadoxParameterValueTypeException(
                        'Wrong type for key "{}", column {} in parameter "{}" type expected {} type value "{}"'.format(
                            key,
                            c_name,
                            parameter.id,
                            value_type,
                            type(v)))

    @staticmethod
    def has_file(parameter):
        # Check if any column has a file type definition
        for c in parameter.definition.columns:
            if c.content_type.type == VALUE_TYPE_FILE:
                return True

        return False

    @staticmethod
    def has_files_changes(parameter, value, hashes):
        if value is None:
            return False

        # Check if any column has a file type definition
        for c in parameter.definition.columns:
            # It is the same process for file/document/notebook, so the function is implemented only in FileValue
            if c.content_type.type == VALUE_TYPE_FILE:
                for v in value[c.title]:
                    if ValuesFactory.dispatch(VALUE_TYPE_FILE).has_file_hash_changed(v, hashes):
                        return True

        return False

    @staticmethod
    def format(parameter, dataframe_value, hashes, value_key):

        # Empty dataframe are sent as NA value instead of a dataframe with 0 records
        if len(dataframe_value) == 0:
            return None

        data = {}

        for column_definition in parameter.definition.columns:
            value_type = column_definition.content_type.type
            col_name = column_definition.title

            # Get the factory and factory method
            factory = ValuesFactory.dispatch(value_type)
            factory_method = getattr(factory, 'format' if value_type == VALUE_TYPE_FILE else 'format_dataframe')

            # Iterate over columns datas and call format function
            data[col_name] = [
                factory_method(x, parameter, hashes, value_key, update_function=DataframeParameter.update_value,
                               col=col_name, row=row)
                for (row, x) in enumerate(dataframe_value[col_name])
            ]

        return data

    @staticmethod
    def update_value(parameter, value_key, value, position):
        dataframe = getattr(parameter.value_kinds, value_key)._value
        # fix iloc updating a copy of the pandas.DataFrame http://stackoverflow.com/a/20998091/6620054
        dataframe.iloc[position['row'], dataframe.columns.get_indexer([position['col']])] = value

    @staticmethod
    def hash_value(value):
        if value is None:
            return None
        return hash(value.to_string())
