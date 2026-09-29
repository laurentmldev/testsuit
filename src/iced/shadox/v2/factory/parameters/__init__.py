# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

NoneType = type(None)

from .scalar import ScalarParameter
from .vector import VectorParameter
from .matrix import MatrixParameter
from .dataframe import DataframeParameter
from .notebook import NotebookParameter
from .document import DocumentParameter

PARAMETER_TYPE_SCALAR = 'scalar'
PARAMETER_TYPE_VECTOR = 'vector'
PARAMETER_TYPE_MATRIX = 'matrix'
PARAMETER_TYPE_DATAFRAME = 'dataframe'
PARAMETER_TYPE_NOTEBOOK = 'notebook'
PARAMETER_TYPE_DOCUMENT = 'document'
PARAMETER_TYPE_EXTRES = 'extres'

_dispatch_map = {
    PARAMETER_TYPE_SCALAR: ScalarParameter,
    PARAMETER_TYPE_VECTOR: VectorParameter,
    PARAMETER_TYPE_MATRIX: MatrixParameter,
    PARAMETER_TYPE_DATAFRAME: DataframeParameter,
    PARAMETER_TYPE_NOTEBOOK: NotebookParameter,
    PARAMETER_TYPE_DOCUMENT: DocumentParameter,
    PARAMETER_TYPE_EXTRES: ScalarParameter,
}

class ParametersFactory(object):
    def __init__(self):
        pass

    @staticmethod
    def dispatch(parameter_structure):
        if parameter_structure in _dispatch_map:
            return _dispatch_map[parameter_structure]
        raise KeyError('Structure ' + parameter_structure + ' is not recognized : ' + str(list(_dispatch_map)))

__all__ = ["ParametersFactory"]
