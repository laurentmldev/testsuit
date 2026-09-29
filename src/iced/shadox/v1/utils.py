#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import numpy
import pandas
import os
from six import string_types, u, PY2

from .exceptions import ShadoxParameterValueTypeException, ShadoxFileNotFoundException

NoneType = type(None)

PARAMETER_TYPE_SCALAR = 'scalar'
PARAMETER_TYPE_VECTOR = 'vector'
PARAMETER_TYPE_MATRIX = 'matrix'
PARAMETER_TYPE_DATAFRAME = 'dataframe'
PARAMETER_TYPE_NOTEBOOK = 'notebook'
PARAMETER_TYPE_DOCUMENT = 'document'
VALUE_TYPE_INTEGER = 'integer'
VALUE_TYPE_DOUBLE = 'double'
VALUE_TYPE_STRING = 'string'
VALUE_TYPE_BOOLEAN = 'boolean'
VALUE_TYPE_FILE = 'file'
VALUE_TYPE_NOTEBOOK = 'notebook'
VALUE_TYPE_DOCUMENT = 'document'
PARAMETER_VECTOR_ALLOWED_TYPES = [list, numpy.ndarray]
PARAMETER_MATRIX_ALLOWED_TYPES = [list, dict, numpy.ndarray, numpy.matrixlib.defmatrix.matrix]
PARAMETER_DATAFRAME_ALLOWED_TYPES = [dict, pandas.DataFrame]
VALUE_INTEGER_ALLOWED_TYPES = [int, numpy.int32, numpy.int_, NoneType]
VALUE_DOUBLE_ALLOWED_TYPES = [int, float, numpy.float_, NoneType]
VALUE_STRING_ALLOWED_TYPES = string_types + (numpy.unicode_,)
VALUE_BOOLEAN_ALLOWED_TYPES = [bool, NoneType, numpy.bool_]
VALUE_FILE_ALLOWED_TYPES = [dict, NoneType, numpy.unicode_]


def convert_shadox_to_python(parameter, value, key, download=True):
    """
    Convert a shadox parameter value to a python var
    By mapping parameter structure to each function dealing with simple shadox type
    :param parameter:
    :param value:
    :param key:
    :param download:
    :return:
    """
    return {
        PARAMETER_TYPE_SCALAR: lambda: _convert_parameter_scalar_to_python(parameter, value, key, download),
        PARAMETER_TYPE_NOTEBOOK: lambda: _convert_parameter_notebook_to_python(parameter, value, key, download),
        PARAMETER_TYPE_DOCUMENT: lambda: _convert_parameter_notebook_to_python(parameter, value, key, download),
        PARAMETER_TYPE_VECTOR: lambda: _convert_parameter_vector_to_python(parameter, value, key, download),
        PARAMETER_TYPE_MATRIX: lambda: _convert_parameter_matrix_to_python(parameter, value, key, download),
        PARAMETER_TYPE_DATAFRAME: lambda: _convert_parameter_dataframe_to_python(parameter, value, key, download),
    }[parameter.definition['structure']]()


def check_python_value_match_shadox(parameter, value, key):
    """
    Check that the value python match a shadox structure
    :param parameter:
    :param value:
    :param key:
    :return:
    """
    return {
        PARAMETER_TYPE_SCALAR: lambda: _check_python_scalar_value(parameter, value, key),
        PARAMETER_TYPE_NOTEBOOK: lambda: _check_python_notebook_value(parameter, value, key),
        PARAMETER_TYPE_DOCUMENT: lambda: _check_python_document_value(parameter, value, key),
        PARAMETER_TYPE_VECTOR: lambda: _check_python_vector_value(parameter, value, key),
        PARAMETER_TYPE_MATRIX: lambda: _check_python_matrix_value(parameter, value, key),
        PARAMETER_TYPE_DATAFRAME: lambda: _check_python_dataframe_value(parameter, value, key),
    }[parameter.definition['structure']]()


def format_value_before_update(parameter, value, key):
    """
    Format the value, only used to upload file atm
    :param parameter:
    :param value:
    :param key:
    :return:
    """
    return {
        PARAMETER_TYPE_SCALAR: lambda: _format_parameter_scalar(parameter, value, key),
        PARAMETER_TYPE_NOTEBOOK: lambda: _format_parameter_notebook(parameter, value, key),
        PARAMETER_TYPE_DOCUMENT: lambda: _format_parameter_document(parameter, value, key),
        PARAMETER_TYPE_VECTOR: lambda: _format_parameter_vector(parameter, value, key),
        PARAMETER_TYPE_MATRIX: lambda: _format_parameter_matrix(parameter, value, key),
        PARAMETER_TYPE_DATAFRAME: lambda: _format_parameter_dataframe(parameter, value, key),
    }[parameter.definition['structure']]()


def get_numpy_type(value_type):
    """
    Get the numpy type for a parameter value type
    :param value_type:
    :return:
    """
    return {
        VALUE_TYPE_INTEGER: numpy.int_,
        VALUE_TYPE_DOUBLE: numpy.float_,
        VALUE_TYPE_STRING: 'U255',
        VALUE_TYPE_BOOLEAN: numpy.bool_,
        VALUE_TYPE_FILE: numpy.object
    }[value_type]


def _check_shadox_basics_type(value, value_type):
    """
    Check the type of value match with shadox type cast
    :param value:
    :param value_type:
    :return:
    """
    return {
        VALUE_TYPE_INTEGER: lambda x: type(x) in VALUE_INTEGER_ALLOWED_TYPES,
        VALUE_TYPE_DOUBLE: lambda x: type(x) in VALUE_DOUBLE_ALLOWED_TYPES,
        VALUE_TYPE_STRING: lambda x: isinstance(x, VALUE_STRING_ALLOWED_TYPES) or type(x) == NoneType,
        VALUE_TYPE_BOOLEAN: lambda x: type(x) in VALUE_BOOLEAN_ALLOWED_TYPES,
        VALUE_TYPE_FILE: lambda x: _check_file_parameter_value(x),
        VALUE_TYPE_NOTEBOOK: lambda x: _check_notebook_parameter_value(x),
        VALUE_TYPE_DOCUMENT: lambda x: _check_document_parameter_value(x)
    }[value_type](value)


def _check_file_parameter_value(v):
    """
    Check if a file parameter value is either a dict or a string (path) matching a correct file
    :param v:
    :return:
    """
    # Check the type of the value
    if type(v) not in VALUE_FILE_ALLOWED_TYPES and not isinstance(v, VALUE_STRING_ALLOWED_TYPES):
        return False

    # Allow file to be none, thx numpy for not allowing NoneType
    if v is None:
        return True

    # If a string, check file exists
    if isinstance(v, string_types):
        if v == "":
            raise ShadoxParameterValueTypeException("File cannot be empty")
        elif os.path.exists(v) and os.path.isfile(v):
            return True
        else:
            raise ShadoxFileNotFoundException("File '{}' not found on disk".format(v))

    # If a dict, check the structre, else it's None
    return 'identifier' in v and 'name' in v and 'size' in v and 'path' in v if type(v) == dict else True


def _check_notebook_parameter_value(v):
    """
    Same as a file with the field html_preview and a verification of the extension
    :param v:
    :return:
    """
    # Check is a correct file
    if not _check_file_parameter_value(v):
        return False

    # Allow file to be None
    if v is None:
        return True

    # If it's a dict, we just have to check it has the html preview
    if isinstance(v, dict):
        return 'htmlPreview' in v

    # Check the notebook extension
    extension = v.split('.')[-1]
    if extension != 'ipynb':
        raise ShadoxParameterValueTypeException("Excepted a ipynb file, got a {}".format(extension))

    # It's a string, checks have been passed by _check_file_parameter_value
    return True


def _check_document_parameter_value(v):
    """
    Same as a scalar file
    :param v:
    :return:
    """
    # Check is a correct file
    if not _check_file_parameter_value(v):
        return False

    return True


def _cast_shadox_basics_type_to_python(value, value_type, parameter=None, key=None, download=True):
    """
    Cast a value to a python value type
    :param value:
    :param value_type:
    :param parameter:
    :param key:
    :return:
    """
    if value is None:
        return None
    return {
        VALUE_TYPE_INTEGER: lambda x: int(float(x)),
        VALUE_TYPE_DOUBLE: float,
        VALUE_TYPE_STRING: lambda x: x.encode('utf-8') if PY2 else str(x),
        VALUE_TYPE_BOOLEAN: bool,
        VALUE_TYPE_FILE: lambda x: download_file(x, parameter, key) if download else x
    }[value_type](value)


def download_file(value, parameter, key):
    """
    Download the file
    :param value:
    :param parameter:
    :param key:
    :return:
    """
    if isinstance(value, dict):
        value = parameter.client.download_file(parameter.id, key, value)
    return value


def _convert_parameter_scalar_to_python(parameter, value, key, download):
    """
    Convert a scalar parameter value to a python value
    :param parameter:
    :param value:
    :param key:
    :param download:
    :return:
    """
    return _cast_shadox_basics_type_to_python(value, parameter.definition['contentType']['type'], parameter, key,
                                              download)


def _convert_parameter_notebook_to_python(parameter, value, key, download):
    """
    Convert a notebook parameter value to a python value
    :param parameter:
    :param value:
    :param key:
    :param download:
    :return:
    """
    return _cast_shadox_basics_type_to_python(value, VALUE_TYPE_FILE, parameter, key, download)


def _convert_parameter_vector_to_python(parameter, value, key, download):
    """
    Convert a vector parameter value to a python value
    :param parameter:
    :param value:
    :param key:
    :param download:
    :return:
    """
    if type(value) != numpy.ndarray:
        value_type = parameter.definition['contentType']['type']
        numpy_data = [
            _cast_shadox_basics_type_to_python(x, value_type, parameter, key, download) for x in value
            ] if value else []
        value = numpy.array(numpy_data, dtype=get_numpy_type(value_type))

    return value


def _convert_parameter_matrix_to_python(parameter, value, key, download):
    """
    Convert a matrix parameter value to a python value
    :param parameter:
    :param value:
    :param key:
    :param download:
    :return:
    """
    if type(value) not in [numpy.ndarray, numpy.matrixlib.defmatrix.matrix]:
        value_type = parameter.definition['contentType']['type']
        numpy_data = [
            [_cast_shadox_basics_type_to_python(y, value_type, parameter, key, download) for y in x]
            for x in value
            ] if value else []
        value = numpy.matrix(numpy_data, dtype=get_numpy_type(value_type))

    return value


def _convert_parameter_dataframe_to_python(parameter, value, key, download):
    """
    Convert a dataframe parameter to a python value
    :param parameter:
    :param value:
    :param key:
    :param download:
    :return:
    """
    if not isinstance(value, pandas.DataFrame):
        data = {}
        indexes = set()
        columns = []
        for c in parameter.definition['columns']:
            value_type = c['contentType']['type']
            c_name = c['title']
            if value:
                c_value = [None] * len(value[c_name])
                for i, v in enumerate(value[c_name]):
                    indexes.add(i)
                    c_value[i] = _cast_shadox_basics_type_to_python(v, value_type, parameter, key, download)
                columns.append(c_name)
            else:
                c_value = []
            data[c_name] = pandas.Series(c_value, dtype=get_numpy_type(value_type), name=c_name)
        value = pandas.DataFrame(data, index=indexes, columns=columns)

    return value


def _check_python_scalar_value(parameter, value, key):
    """
    Check a scalar parameter python var type
    :param parameter:
    :param value:
    :param key:
    :return:
    """
    if not _check_shadox_basics_type(value, parameter.definition['contentType']['type']):
        raise ShadoxParameterValueTypeException('Wrong type for key "{}" in parameter "{}"'.format(key, parameter.id))

    return True


def _check_python_notebook_value(parameter, value, key):
    """
    Check a notebook parameter python var type
    :param parameter:
    :param value:
    :param key:
    :return:
    """
    if not _check_shadox_basics_type(value, VALUE_TYPE_NOTEBOOK):
        raise ShadoxParameterValueTypeException('Wrong type for key "{}" in parameter "{}"'.format(key, parameter.id))

    return True


def _check_python_document_value(parameter, value, key):
    """
    Check a document parameter python var type
    :param parameter:
    :param value:
    :param key:
    :return:
    """
    if not _check_shadox_basics_type(value, VALUE_TYPE_DOCUMENT):
        raise ShadoxParameterValueTypeException('Wrong type for key "{}" in parameter "{}"'.format(key, parameter.id))

    return True


def _check_python_vector_value(parameter, value, key):
    """
    Check a vector parameter python var type
    :param parameter:
    :param value:
    :param key:
    :return:
    """
    # Check vector type
    if type(value) not in PARAMETER_VECTOR_ALLOWED_TYPES:
        raise ShadoxParameterValueTypeException('Wrong type for key "{}" in parameter "{}"'.format(key, parameter.id))

    # Get a list of the array
    if type(value) == numpy.ndarray:
        value = value.tolist()

    # Check each value
    value_type = parameter.definition['contentType']['type']
    for x in value:
        if not _check_shadox_basics_type(x, value_type):
            raise ShadoxParameterValueTypeException(
                'Wrong type for key "{}" in parameter "{}"'.format(key, parameter.id))

    return True


def _check_python_matrix_value(parameter, value, key):
    """
    Check a matrix parameter python var type
    :param parameter:
    :param value:
    :param key:
    :return:
    """
    # Check matrix type
    if type(value) not in PARAMETER_MATRIX_ALLOWED_TYPES:
        raise ShadoxParameterValueTypeException('Wrong type for key "{}" in parameter "{}"'.format(key, parameter.id))

    # Get a list of the matrix/array
    if type(value) in [numpy.ndarray, numpy.matrixlib.defmatrix.matrix]:
        value = value.tolist()

    # Check each value
    value_type = parameter.definition['contentType']['type']
    for x in value:
        # Check content matrix type
        if type(x) not in PARAMETER_MATRIX_ALLOWED_TYPES:
            raise ShadoxParameterValueTypeException(
                'Wrong type for key "{}" in parameter "{}"'.format(key, parameter.id))
        for y in x:
            if not _check_shadox_basics_type(y, value_type):
                raise ShadoxParameterValueTypeException(
                    'Wrong type for key "{}" in parameter "{}"'.format(key, parameter.id))

    return True


def _check_python_dataframe_value(parameter, value, key):
    """
    Check a dataframe parameter python var type
    :param parameter:
    :param value:
    :param key:
    """
    # Check dataframe type
    if type(value) not in PARAMETER_DATAFRAME_ALLOWED_TYPES:
        raise ShadoxParameterValueTypeException('Wrong type for key "{}" in parameter "{}"'.format(key, parameter.id))

    # Check each value
    for c in parameter.definition['columns']:
        value_type = c['contentType']['type']
        c_name = c['title']
        for v in value[c_name]:
            if not _check_shadox_basics_type(v, value_type):
                raise ShadoxParameterValueTypeException(
                    'Wrong type for key "{}" in parameter "{}" type expected {} type value "{}"'.format(key,
                                                                                                        parameter.id,
                                                                                                        value_type,
                                                                                                        type(v)))

    return True


def _format_parameter_scalar(parameter, value, key):
    """
    Format the parameter scalar, if a file type str/unicode : upload
    :param parameter:
    :param value:
    :param key:
    :return:
    """
    if parameter.definition['contentType']['type'] == VALUE_TYPE_FILE:
        if isinstance(value, string_types):
            value = parameter.client.upload_file(parameter, key, value)
            # Set the value inside the parameter
            parameter.values.__setattr__(key, value)

    return value


def _format_parameter_notebook(parameter, value, key):
    """
    Same as a scalar file, except we add a htmlPreview field
    :param parameter:
    :param value:
    :param key:
    :return:
    """

    if value is None:
        return None

    # This test is not useful anymore, all files/notebook/document are "stored" as file path
    if isinstance(value, string_types):
        # Check if there is an html preview
        try:
            html_preview = parameter.values.__getattr__('_html_previews')[key]
            list_extra = [{'key': 'htmlPreview', 'value': html_preview}]
        except (KeyError, TypeError):
            list_extra = [{'key': 'htmlPreview', 'value': ''}]

        value = parameter.client.upload_file(parameter, key, value, list_extra)
        # Set the value inside the parameter
        parameter.values.__setattr__(key, value)

    # Remove the path key, the notebook cannot contain a path key
    if 'path' in value:
        value.pop('path')

    return value


def _format_parameter_document(parameter, value, key):
    """
    Same as a scalar file
    :param parameter:
    :param value:
    :param key:
    :return:
    """

    value = parameter.client.upload_file(parameter, key, value)
    # Set the value inside the parameter
    parameter.values.__setattr__(key, value)

    # Remove the path key, the notebook cannot contain a path key
    if 'path' in value:
        value.pop('path')

    return value


def _format_parameter_vector(parameter, value, key):
    """
    Format the parameter vector, if a file type str/unicode : upload
    :param parameter:
    :param value:
    :param key:
    :return:
    """
    value = value.tolist()

    if parameter.definition['contentType']['type'] == VALUE_TYPE_FILE:
        new_value = []
        for x in value:
            if isinstance(x, string_types):
                x = parameter.client.upload_file(parameter, key, x)
            new_value.append(x)
        value = new_value
        # Set the value inside the parameter
        parameter.values.__setattr__(key, value)

    return value


def _format_parameter_matrix(parameter, value, key):
    """
    Format the parameter matrix, if a file type str/unicode : upload
    :param parameter:
    :param value:
    :param key:
    :return:
    """
    value = value.tolist()

    if parameter.definition['contentType']['type'] == VALUE_TYPE_FILE:
        new_value = []
        for x in value:
            new_line = []
            for y in x:
                if isinstance(y, string_types):
                    y = parameter.client.upload_file(parameter, key, y)
                new_line.append(y)
            new_value.append(new_line)
        value = new_value
        # Set the value inside the parameter
        parameter.values.__setattr__(key, value)

    return value


def _format_parameter_dataframe(parameter, value, key):
    """
    Format the parameter matrix, if a file : upload
    :param parameter:
    :param value:
    :param key:
    :return:
    """

    # Build the new dict to send to shadox
    data = {}
    for c in parameter.definition['columns']:
        value_type = c['contentType']['type']
        c_name = c['title']

        # Upload the file
        if value_type == VALUE_TYPE_FILE:
            new_col = []
            for index, v in enumerate(value[c_name]):
                if isinstance(v, string_types):
                    v = parameter.client.upload_file(parameter, key, v)
                new_col.append(v)
            data[c_name] = new_col

        #  Convert numpy.u255 to unicode
        elif value_type == VALUE_TYPE_STRING:
            data[c_name] = [u(x) for x in value[c_name]]

        # Convert numpy.float64 to float
        elif value_type == VALUE_TYPE_DOUBLE:
            data[c_name] = [float(x) for x in value[c_name]]

        # Convert numpy.int64 to int
        elif value_type == VALUE_TYPE_INTEGER:
            data[c_name] = [int(float(x)) for x in value[c_name]]

        # Convert numpy.bool to bool
        elif value_type == VALUE_TYPE_BOOLEAN:
            data[c_name] = [bool(x) for x in value[c_name]]

    return data


def log_action(message, level_message, level_log):
    """
    Print a log
    :param message:
    :param level_message:
    :param level_log:
    :return:
    """
    if level_message <= level_log:
        print(message)
