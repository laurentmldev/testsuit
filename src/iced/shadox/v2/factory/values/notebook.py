# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from ...wrappers.shadox_notebook import ShadoxNotebook
from ...exceptions import ShadoxParameterValueTypeException
from .file import FileValue


class NotebookValue(object):
    def __init__(self):
        pass

    @staticmethod
    def convert(shadox_value, key, parameter):
        # If the value is already a ShadoxFile (user create the
        if isinstance(shadox_value, ShadoxNotebook):
            # If the ShadoxFile has a file idenfier, get the path via download method, if it has not, get the local path
            path = shadox_value.download() if shadox_value.get_fileidentifier() is not None else shadox_value.get_path()
            shadox_file = ShadoxNotebook(path, shadox_value.get_html_preview())
        else:
            shadox_file = ShadoxNotebook._init_from_shadox(key, shadox_value)

        # Bind the parameter to the shadox file (for download/upload)
        shadox_file._bind_parameter(parameter, key)
        return shadox_file

    @staticmethod
    def check(shadox_notebook, parameter, key):
        # The file can be None
        if shadox_notebook is None:
            return True

        # Check the standard file
        if not FileValue.check(shadox_notebook, parameter, key):
            return False

        # The file can be None
        if shadox_notebook.get_path() is None:
            return True

        # Check the notebook extension
        extension = shadox_notebook.get_path().split('.')[-1]
        if extension != 'ipynb':
            raise ShadoxParameterValueTypeException("Excepted a ipynb file, got a {}".format(extension))

        return True

    @staticmethod
    def format(shadox_notebook, parameter, hashes, value_key, update_function):
        return FileValue.format(shadox_notebook, parameter, hashes, value_key, convert_function=NotebookValue.convert,
                                instance_obj=ShadoxNotebook, update_function=update_function)
