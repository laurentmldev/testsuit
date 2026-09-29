# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import numpy
from ..values import ValuesFactory, VALUE_TYPE_FILE
from ...exceptions import ShadoxParameterValueTypeException
from . import NoneType

ALLOWED_TYPES = [list, dict, numpy.ndarray, numpy.matrixlib.defmatrix.matrix, NoneType]


class MatrixParameter(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(parameter, value, key):
        if type(value) in [numpy.ndarray, numpy.matrixlib.defmatrix.matrix]:
            # Already converted
            return value
        else:
            value_type = parameter.definition.content_type.type
            numpy_data = [
                [ValuesFactory.dispatch(value_type).convert(y, key, parameter) for y in x]
                for x in value
                ] if value is not None else []
            return numpy.matrix(numpy_data, dtype=ValuesFactory.get_numpy_type(value_type))

    @staticmethod
    def check(parameter, value, key):
        # Check matrix type
        if type(value) not in ALLOWED_TYPES:
            raise ShadoxParameterValueTypeException(
                'Wrong type for key "{}" in matrix parameter "{}"'.format(key, parameter.id))

        # No sub-value for None (ie NA)
        if value is None:
            return

        # Get a list of the matrix/array
        if type(value) in [numpy.ndarray, numpy.matrixlib.defmatrix.matrix]:
            value = value.tolist()

        # Check each value
        value_type = parameter.definition.content_type.type
        for x in value:
            # Check content matrix type
            if type(x) not in ALLOWED_TYPES:
                raise ShadoxParameterValueTypeException(
                    'Wrong type for key "{}" in matrix parameter "{}"'.format(key, parameter.id))
            for y in x:
                if not ValuesFactory.dispatch(value_type).check(y, parameter, key):
                    raise ShadoxParameterValueTypeException(
                        'Wrong type for key "{}" in matrix parameter "{}"'.format(key, parameter.id))

    @staticmethod
    def has_file(parameter):
        return parameter.definition.content_type.type == VALUE_TYPE_FILE

    @staticmethod
    def has_files_changes(parameter, value, hashes):
        # Get a list of the matrix/array
        if type(value) in [numpy.ndarray, numpy.matrixlib.defmatrix.matrix]:
            value = value.tolist()

        # It is the same process for file/document/notebook, so the function is implemented only in FileValue
        for x in value:
            for y in x:
                if ValuesFactory.dispatch(VALUE_TYPE_FILE).has_file_hash_changed(y, hashes):
                    return True

        return False

    @staticmethod
    def format(parameter, matrix_value, hashes, value_key):
        # If optimisation needed, it is possible to test here the type, if not file, return the list unchanged
        matrix_value = matrix_value.tolist()
        value_type = parameter.definition.content_type.type

        if len(matrix_value) == 1 and len(matrix_value[0]) == 0:
            return None

        return [
            [
                ValuesFactory.dispatch(value_type).format(cell, parameter, hashes, value_key,
                                                          MatrixParameter.update_value, i=i, j=j)
                for (j, cell) in enumerate(line)
            ] for (i, line) in enumerate(matrix_value)
        ]

    @staticmethod
    def update_value(parameter, value_key, value, position):
        getattr(parameter.value_kinds, value_key)._value[position['i'], position['j']] = value

    @staticmethod
    def hash_value(value):
        if value is None:
            return None
        return hash(value.tostring())
