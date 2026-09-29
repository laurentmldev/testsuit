# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from ...wrappers.shadox_document import ShadoxDocument
from .file import FileValue


class DocumentValue(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(shadox_value, key, parameter):
        # If the value is already a ShadoxFile (user create the
        if isinstance(shadox_value, ShadoxDocument):
            # If the ShadoxFile has a file idenfier, get the path via download method, if it has not, get the local path
            path = shadox_value.download() if shadox_value.get_fileidentifier() is not None else shadox_value.get_path()
            shadox_file = ShadoxDocument(path)
        else:
            shadox_file = ShadoxDocument._init_from_shadox(key, shadox_value)

        # Bind the parameter to the shadox file (for download/upload)
        shadox_file._bind_parameter(parameter, key)
        return shadox_file

    @staticmethod
    def check(value, parameter, key):
        # Only check if match a standard file
        return FileValue.check(value, parameter, key)

    @staticmethod
    def format(shadox_document, parameter, hashes, value_key, update_function):
        return FileValue.format(shadox_document, parameter, hashes, value_key, convert_function=DocumentValue.convert,
                                instance_obj=ShadoxDocument, update_function=update_function)
