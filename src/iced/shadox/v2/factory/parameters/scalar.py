# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from ..values import ValuesFactory, VALUE_TYPE_FILE
from ...exceptions import ShadoxParameterValueTypeException


class ScalarParameter(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(parameter, scalar_value, key):
        return ValuesFactory.dispatch(parameter.definition.content_type.type).convert(scalar_value, key, parameter)

    @staticmethod
    def check(parameter, scalar_value, key):
        if not ValuesFactory.dispatch(parameter.definition.content_type.type).check(scalar_value, parameter, key):
            raise ShadoxParameterValueTypeException(
                'Wrong type for key "{}" in scalar parameter "{}"'.format(key, parameter.id))

    @staticmethod
    def has_file(parameter):
        return parameter.definition.content_type.type == VALUE_TYPE_FILE

    @staticmethod
    def has_files_changes(parameter, scalar_value, hashes):
        # It is the same process for file/document/notebook, so the function is implemented only in FileValue
        return ValuesFactory.dispatch(VALUE_TYPE_FILE).has_file_hash_changed(scalar_value, hashes)

    @staticmethod
    def format(parameter, scalar_value, hashes, value_key):
        return ValuesFactory.dispatch(parameter.definition.content_type.type).format(
            scalar_value, parameter, hashes, value_key, update_function=ScalarParameter.update_value
        )

    @staticmethod
    def update_value(parameter, value_key, value, position):
        getattr(parameter.value_kinds, value_key)._value = value

    @staticmethod
    def hash_value(value):
        if value is None:
            return None
        return hash(str(value))
