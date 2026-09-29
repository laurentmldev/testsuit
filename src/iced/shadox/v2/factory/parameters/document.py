# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from ..values import ValuesFactory, VALUE_TYPE_DOCUMENT, VALUE_TYPE_FILE
from ...exceptions import ShadoxParameterValueTypeException
from ...wrappers.shadox_document import ShadoxDocument
from .scalar import ScalarParameter


class DocumentParameter(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(parameter, document_value, key):
        return ValuesFactory.dispatch(VALUE_TYPE_DOCUMENT).convert(document_value, key, parameter)

    @staticmethod
    def check(parameter, document_value, key):
        if not ValuesFactory.dispatch(VALUE_TYPE_DOCUMENT).check(document_value, parameter, key):
            raise ShadoxParameterValueTypeException(
                'Wrong type for key "{}" in document parameter "{}"'.format(key, parameter.id))

    @staticmethod
    def has_file(parameter):
        return True

    @staticmethod
    def has_files_changes(parameter, document_value, hashes):
        # It is the same process for file/document/notebook, so the function is implemented only in FileValue
        return ValuesFactory.dispatch(VALUE_TYPE_FILE).has_file_hash_changed(document_value, hashes, ShadoxDocument)

    @staticmethod
    def format(parameter, value, hashes, value_key):
        return ValuesFactory.dispatch(VALUE_TYPE_DOCUMENT).format(value, parameter, hashes, value_key,
                                                                  update_function=ScalarParameter.update_value)

    @staticmethod
    def hash_value(value):
        if value is None:
            return None
        return hash(str(value))
