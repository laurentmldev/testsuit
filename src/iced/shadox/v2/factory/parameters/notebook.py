# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from ..values import ValuesFactory, VALUE_TYPE_NOTEBOOK, VALUE_TYPE_FILE
from ...exceptions import ShadoxParameterValueTypeException
from ...wrappers.shadox_notebook import ShadoxNotebook
from .scalar import ScalarParameter


class NotebookParameter(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(parameter, value, key):
        return ValuesFactory.dispatch(VALUE_TYPE_NOTEBOOK).convert(value, key, parameter)

    @staticmethod
    def check(parameter, value, key):
        if not ValuesFactory.dispatch(VALUE_TYPE_NOTEBOOK).check(value, parameter, value):
            raise ShadoxParameterValueTypeException(
                'Wrong type for key "{}" in notebook parameter "{}"'.format(key, parameter.id))

    @staticmethod
    def has_file(parameter):
        return True

    @staticmethod
    def has_files_changes(parameter, value, hashes):
        # It is the same process for file/document/notebook, so the function is implemented only in FileValue
        return ValuesFactory.dispatch(VALUE_TYPE_FILE).has_file_hash_changed(value, hashes, ShadoxNotebook)

    @staticmethod
    def format(parameter, value, hashes, value_key):
        return ValuesFactory.dispatch(VALUE_TYPE_NOTEBOOK).format(value, parameter, hashes, value_key,
                                                                  update_function=ScalarParameter.update_value)

    @staticmethod
    def hash_value(value):
        if value is None:
            return None
        return hash(str(value))
