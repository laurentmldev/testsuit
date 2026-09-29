# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import numpy
from ..values import ValuesFactory, VALUE_TYPE_FILE
from ...exceptions import ShadoxParameterValueTypeException
from . import NoneType

ALLOWED_TYPES = [list, numpy.ndarray, NoneType]


class VectorParameter(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(parameter, value, key):
        if type(value) == numpy.ndarray:
            # Already converted
            return value
        else:
            # Convert value
            value_type = parameter.definition.content_type.type
            numpy_data = [
                ValuesFactory.dispatch(value_type).convert(x, key, parameter) for x in value
                ] if value is not None else []
            return numpy.array(numpy_data, dtype=ValuesFactory.get_numpy_type(value_type))

    @staticmethod
    def check(parameter, value, key):
        # Check vector type
        if type(value) not in ALLOWED_TYPES:
            raise ShadoxParameterValueTypeException(
                'Wrong type for key "{}" in vector parameter "{}"'.format(key, parameter.id))

        # No sub-value for None (ie NA)
        if value is None:
            return

        # Get a list of the array
        if type(value) == numpy.ndarray:
            value = value.tolist()

        # Check each value
        value_type = parameter.definition.content_type.type
        for x in value:
            if not ValuesFactory.dispatch(value_type).check(x, parameter, key):
                raise ShadoxParameterValueTypeException(
                    'Wrong type for key "{}" in vector parameter "{}"'.format(key, parameter.id))

    @staticmethod
    def has_file(parameter):
        return parameter.definition.content_type.type == VALUE_TYPE_FILE

    @staticmethod
    def has_files_changes(parameter, value, hashes):
        # Get a list of the array
        if type(value) == numpy.ndarray:
            value = value.tolist()

        # It is the same process for file/document/notebook, so the function is implemented only in FileValue
        for x in value:
            if ValuesFactory.dispatch(VALUE_TYPE_FILE).has_file_hash_changed(x, hashes):
                return True

        return False

    @staticmethod
    def format(parameter, scalar_value, hashes, value_key):
        if len(scalar_value) == 0:
            return None
        # If optimisation needed, it is possible to test here the type, if not file, return the list unchanged
        scalar_value = scalar_value.tolist()
        value_type = parameter.definition.content_type.type

        data = [
            ValuesFactory.dispatch(value_type).format(
                cell, parameter, hashes, value_key, update_function=VectorParameter.update_value, i=i
            ) for (i, cell) in enumerate(scalar_value)
        ]

        # Recreate a VectorParameter from new shadox data
        # new_vector_value = VectorParameter.convert(parameter, data, value_key)

        # Update the parameter value
        # parameter.values.__setattr__(value_key, new_vector_value)

        return data

    @staticmethod
    def update_value(parameter, value_key, value, position):
        getattr(parameter.value_kinds, value_key)._value[position['i']] = value

    @staticmethod
    def hash_value(value):
        if value is None:
            return None
        return hash(value.tostring())
