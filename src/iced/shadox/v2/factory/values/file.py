# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import os
from ...wrappers.shadox_file import ShadoxFile
from ...exceptions import ShadoxFileNotFoundException, ShadoxInternalException


class FileValue(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(shadox_value, key, parameter):
        # If the value is already a ShadoxFile (user create the ShadoxFile in its code)
        # We duplicate the ShadoxFile, the ShadoxFile is immutable and bind to one parameter and value kind
        if isinstance(shadox_value, ShadoxFile):
            # If the ShadoxFile has a file idenfier, get the path via download method, if it has not, get the local path
            path = shadox_value.download() if shadox_value.get_fileidentifier() is not None else shadox_value.get_path()
            shadox_file = ShadoxFile(path)
        else:
            shadox_file = ShadoxFile._init_from_shadox(key, shadox_value)

        # Bind the parameter to the shadox file (for download/upload)
        shadox_file._bind_parameter(parameter, key)
        return shadox_file

    @staticmethod
    def check(value, parameter, key):
        # The file can be None
        if value is None:
            return True

        # The file value must be a ShadoxFile
        if not isinstance(value, ShadoxFile):
            return False

        # The file can be None
        if value.get_path() is None:
            return True

        # Check the file is available on disk
        if value.get_path() and not (os.path.exists(value.get_path()) and os.path.isfile(value.get_path())):
            raise ShadoxFileNotFoundException("File '{}' not found on disk".format(value.get_path()))

        return True

    @staticmethod
    def has_file_hash_changed(value, hashes, instance_obj=ShadoxFile):
        # If the value is not a shadox file, no need to go further
        if not isinstance(value, instance_obj):
            return False

        # There is no path set, it means the file has not changed
        # It's very unlikely this case ever happen
        if value.get_path() is None:
            return False

        # Check file availability
        # Will throw an exception if file not available, not used here has a bool
        value._is_local_file_available()

        # If the file does not exist on shadox and has a path (it means the file has been created by the client)
        if not value._is_present_on_shadox():
            return True

        # Check the server has sent the hash for this identifier
        if value.get_fileidentifier() not in hashes:
            raise ShadoxInternalException(
                "Hash should have been sent by Shadox API for file {}".format(value.get_filename()))

        # Check the server hash starts with the local hash
        # If not, the file has been updated
        local_hash = value.compute_local_hash()
        server_hash = hashes[value.get_fileidentifier()]
        return not server_hash == local_hash

    @staticmethod
    def format(value, parameter, hashes, value_key, update_function, convert_function=None, instance_obj=None,
               **position):
        # The file can be None
        if value is None:
            return None

        if convert_function is None:
            convert_function = FileValue.convert

        if instance_obj is None:
            instance_obj = ShadoxFile

        if value._parameter is None or value._parameter.id != parameter.id or value._value_key != value_key:
            new_value = convert_function(value, value_key, parameter)
        else:
            new_value = value

        if FileValue.has_file_hash_changed(new_value, hashes, instance_obj):
            new_value._upload()
            update_function(parameter, value_key, new_value, position)

        return new_value._to_shadox_value()
