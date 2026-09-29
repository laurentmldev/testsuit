#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.
from sys import getdefaultencoding
from functools import wraps
from six import string_types, integer_types
from uuid import uuid4
from iced.shadox.v2.factory.values import VALUE_TYPE_DOUBLE, VALUE_TYPE_INTEGER, VALUE_TYPE_STRING, VALUE_TYPE_FILE, VALUE_TYPE_BOOLEAN, VALUE_TYPE_DOCUMENT, VALUE_TYPE_NOTEBOOK, VALUE_TYPE_URI
from iced.shadox.v2.factory.parameters import PARAMETER_TYPE_SCALAR, PARAMETER_TYPE_VECTOR, PARAMETER_TYPE_MATRIX, PARAMETER_TYPE_DATAFRAME, PARAMETER_TYPE_DOCUMENT, PARAMETER_TYPE_EXTRES
from iced.shadox.v2.exceptions import ShadoxForbiddenException
from iced.shadox.v2.utils import validate_col_title, log_action, check_document_subtype, check_file_subtype

def _dataframe_only(assert_edit):
    def decorator(method):
        @wraps(method)
        def wrapper(*args, **kwargs):
            if hasattr(args[0], 'columns'):
                if assert_edit: args[0]._parameter._assert_editable()
                return method(*args, **kwargs)
            else:
                raise ShadoxForbiddenException('This method is only for dataframes.')

        return wrapper
    return decorator

def _convert_allowed_reference(content_type, allowed_reference):
    if allowed_reference.get('parameterAlias', None):
        ref_p = content_type._parameter.snapshot._client.get_parameter('alias:' + allowed_reference['parameterAlias'])
    elif allowed_reference.get('parameterPath', None):
        ref_p = content_type._parameter.snapshot._client.get_parameter('path:' + allowed_reference['parameterPath'])
    else:
        ref_p = content_type._parameter.snapshot._client.get_parameter('id:' + allowed_reference['parameterId'])

    converted_allowed_reference = {'parameterId': ref_p.id}
    ref_vk = ref_p.value_kinds[allowed_reference['valueKind']]
    converted_allowed_reference['valueKind'] = ref_vk.name
    if ref_p.definition.structure != 'dataframe':
        if allowed_reference.get('columnId', None):
            raise ShadoxForbiddenException('Referenced parameter is not a dataframe but a columnId was given')
        if ref_p.definition.structure != 'vector':
            raise ShadoxForbiddenException('Referenced parameter must be a parameter or a vector')
        if ref_p.definition.content_type.type != content_type.type:
            raise ShadoxForbiddenException('Referenced parameter\'s type is ' + ref_p.definition.content_type.type + ' but expected ' + content_type.type)
    elif ref_p.definition.structure == 'dataframe':
        if allowed_reference.get('columnName', None):
            ref_col = next((c for c in ref_p.definition.columns if c.title == allowed_reference['columnName']), None)
        elif allowed_reference.get('columnId', None):
            ref_col = next((c for c in ref_p.definition.columns if c.id == allowed_reference['columnId']), None)
        else:
            raise ShadoxForbiddenException('Referenced parameter is a dataframe but no columnId or columnName was given')
        if not ref_col:
            raise ShadoxForbiddenException('Referenced column was not found in parameter')
        converted_allowed_reference['columnId'] = ref_col.id
        if ref_col.content_type.type != content_type.type:
            raise ShadoxForbiddenException('Referenced parameter\'s column type is ' + ref_col.content_type.type + ' but expected ' + content_type.type)
    return converted_allowed_reference

def _resolve_allowed_reference(snapshot, allowed_reference):
    parameter_id = allowed_reference['parameterId']
    value_kind = allowed_reference['valueKind']
    column_id = allowed_reference.get('columnId', None)

    parameter = snapshot._client.get_parameter('id:' + parameter_id)
    value_kind = parameter.value_kinds[value_kind]

    column = None
    if parameter.definition.structure == 'dataframe' and column_id:
        column = next(col for col in parameter.definition.columns if col.id == column_id)

    return (parameter, value_kind, column)

class ParameterDefinition(object):

    def __init__(self, parameter, json_data):
        self._parameter = parameter
        self.description = json_data.get('description')
        self._simulation = json_data.get('simulation')
        self._structure = json_data.get('structure')
        if self.structure in [PARAMETER_TYPE_SCALAR, PARAMETER_TYPE_VECTOR, PARAMETER_TYPE_MATRIX]:
            self._content_type = ParameterDefinition._build_content_type(parameter, json_data['contentType'])
        elif self.structure == PARAMETER_TYPE_DATAFRAME:
            change_col_title = lambda col, t: self._rename_col_title(col, t)
            self._columns = [DataframeColumn(self._parameter, change_col_title, c) for c in json_data['columns']]
        elif self.structure == PARAMETER_TYPE_DOCUMENT:
            self._document_type = json_data['documentType']['subtype']
        elif self.structure == PARAMETER_TYPE_EXTRES:
            self._content_type = ParameterDefinition._build_content_type(parameter, {'type': VALUE_TYPE_URI})

    @property
    def structure(self):
        return self._structure

    @property
    def columns(self):
        return self._columns

    @columns.setter
    def columns(self, ignored):
        raise ShadoxForbiddenException('Columns cannot be modified directly. Use method add_col, delete_col and change_col_order')

    @property
    def content_type(self):
        return self._content_type

    @content_type.setter
    def content_type(self, ignored):
        raise ShadoxForbiddenException('Use change_content_type to modify type.')

    @property
    def simulation(self):
        return self._simulation

    @property
    def document_type(self):
        return self._document_type

    @document_type.setter
    def document_type(self, doc_type):
        if self.structure != PARAMETER_TYPE_DOCUMENT:
            raise ShadoxForbiddenException('This method is only for documents.')
        check_document_subtype(doc_type)
        self._document_type = doc_type

    def change_content_type(self, new_type):
        self._parameter._assert_editable()
        if not hasattr(self, 'content_type'):
            raise ShadoxForbiddenException('This method can only be used for scalars, vectors & matrices.')
        self._content_type = ParameterDefinition._build_content_type(self._parameter, {'type': new_type})
        self._parameter._reset_values()

    @_dataframe_only(True)
    def change_column_type(self, col_title_or_idx, new_type):
        """
        Change the content type of a dataframe column
        :param col_title_or_idx: string or integer
        :param new_type: among ['integer', 'double', 'boolean', 'string', 'reference', 'document', 'file']
        """
        idx = None
        if type(col_title_or_idx) is int:
            idx = col_title_or_idx
        elif type(col_title_or_idx) is str:
            idxs = [i for i, c in enumerate(self.columns) if c.title == col_title_or_idx]
            idx = idxs[0] if len(idxs) == 1 else None
        if not idx and idx != 0:
            raise ShadoxForbiddenException('This column does not exist.')
        self._columns[idx].content_type = ParameterDefinition._build_content_type(self._parameter, {'type': new_type})
        self._parameter._reset_values(idx)

    @_dataframe_only(True)
    def _rename_col_title(self, column, new_title):
        """ INTERNAL USE ONLY """
        check_col_title(self.columns, new_title)
        idx = self.columns.index(column)
        self._parameter._rename_df_col_title(idx, new_title)
        return True

    @_dataframe_only(False)
    def get_col_titles(self):
        return [c.title for c in self.columns]

    @_dataframe_only(True)
    def add_col(self, title=uuid4().hex, type=VALUE_TYPE_DOUBLE):
        check_col_title(self.columns, title)
        change_col_title = lambda col, t: self._rename_col_title(col, t)
        self._parameter._add_df_col(title, type)
        self._columns.append(DataframeColumn(self._parameter, change_col_title, {'contentType': {'type': type}}, title))

    @_dataframe_only(True)
    def delete_col(self, title):
        if len(self.columns) <= 1:
            raise ShadoxForbiddenException('Cannot delete the last column.')
        cols = [c for c in self.columns if c.title == title]
        if len(cols) == 0:
            raise ShadoxForbiddenException('No column was found for this title')
        self._columns.remove(cols[0])
        self._parameter._delete_col_values(title)
        log_action(u"Column {} has been successfully deleted.".format(title), 0, -1)


    @_dataframe_only(True)
    def change_col_order(self, ordered_titles):
        """
        :param ordered_titles: a list of ordered titles
        """
        col_by_title = dict((c.title, c) for c in self.columns)
        existing_titles = col_by_title.keys()
        if len(ordered_titles) != len(existing_titles):
            raise ShadoxForbiddenException('The number of newly ordered titles differs from the number of existing titles.')
        new_columns = []
        for t in ordered_titles:
            if t not in existing_titles:
                raise ShadoxForbiddenException('No column with title ' + t + ' exists.')
            new_columns.append(col_by_title[t])
        self._columns = new_columns
        self._parameter._reorder_df_col_values(ordered_titles)

    @staticmethod
    def _build_content_type(param, content_type_json_data={'type': VALUE_TYPE_DOUBLE}):
        c_type = content_type_json_data.get('type')
        if c_type == VALUE_TYPE_DOUBLE:
            return DoubleContentType(param, content_type_json_data)
        if c_type == VALUE_TYPE_INTEGER:
            return IntegerContentType(param, content_type_json_data)
        if c_type == VALUE_TYPE_STRING:
            return StringContentType(param, content_type_json_data)
        if c_type == VALUE_TYPE_FILE:
            return FileContentType(param, content_type_json_data)
        if c_type in [VALUE_TYPE_BOOLEAN, VALUE_TYPE_DOCUMENT, VALUE_TYPE_NOTEBOOK, VALUE_TYPE_URI]:
            return ContentType(param, c_type)
        raise ShadoxForbiddenException("Unknown type " + c_type)

    def __getitem__(self, key):
        """
        For backward compatibility.
        Allows to read definition as a dict: p.definition['contentType']['structure']
        """
        dict_repr = self._to_dict()
        return dict_repr[key]

    def __contains__(self, item):
        """For backward compatibility
        Enables `element in Parameter.ParameterDefinition` behavior
        """
        return item in self._to_dict()

    def get(self, key, default=None):
        if key in self:
            return self[key]
        return default

    def _to_dict(self):
        dic = {
                'description': self.description,
                'simulation': self.simulation,
                'structure': self.structure,
                }
        if hasattr(self, 'columns'):
            dic['columns'] = [c._to_dict() for c in self.columns]
        elif hasattr(self, 'content_type'):
            dic['contentType'] = self.content_type._to_dict()
        elif hasattr(self, 'document_type'):
            dic['documentType'] = {'subtype': self.document_type}
        return dic

    def get_hash(self):
        return make_hash(self._to_dict())

    def __repr__(self):
        display = 'structure: ' + self.structure + ', simulation: ' + str(self.simulation)
        if hasattr(self, 'columns'):
            display += '\ncolumns: ['
            for c in self.columns:
                display=str(display)
                display += '\n'+str(c)
            display += '\n]'
        elif hasattr(self, 'content_type'):
            display = str(display)
            display += '\ncontent_type: { ' + str(self.content_type) + ' }'
        elif hasattr(self, 'document_type'):
            display += '\ndocument_type:' + self.document_type
        return display


def check_col_title(columns, title):
    validate_col_title(title)
    if title in [c.title for c in columns]:
        raise ShadoxForbiddenException('This column name is already used.')


class DataframeColumn(object):
    def __init__(self, param, change_col_title, json_data={}, col_title=uuid4().hex):
        self._parameter = param
        self._change_col_title = change_col_title
        self.content_type = ParameterDefinition._build_content_type(param, json_data.get('contentType'))
        self._id = json_data.get('id', str(uuid4()))
        self._title = json_data.get('title', col_title)

    def _to_dict(self):
        return {
                'id': self.id,
                'title': self.title,
                'contentType': self.content_type._to_dict(),
                }

    @property
    def id(self):
        return self._id

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, title):
        self._parameter._assert_editable()
        if self._change_col_title(self, title):
            self._title = title

    def __repr__(self):
        display = str('{ id: ' + str(self.id) + ', title: ' + self.title + ', content_type: {')
        display+=str(self.content_type)+'} }'
        return display


class ContentType(object):
    def __init__(self, param, type):
        self._parameter = param
        self._type = type

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, ignored):
        raise ShadoxForbiddenException('Use definition.change_content_type to modify the type.')

    def set_allowed_reference_from_alias(self, parameter_alias, value_kind, column_id=None, column_name=None):

        self.allowed_reference = {
            'parameterId': None,
            'parameterPath': None,
            'parameterAlias': parameter_alias,
            'valueKind': value_kind,
            'columnId': column_id,
            'columnName': column_name
            }

    def set_allowed_reference_from_path(self, parameter_path, value_kind, column_id=None, column_name=None):

        self.allowed_reference = {
            'parameterId': None,
            'parameterPath': parameter_path,
            'parameterAlias': None,
            'valueKind': value_kind,
            'columnId': column_id,
            'columnName': column_name
            }

    def set_allowed_reference_from_id(self, parameter_id, value_kind, column_id=None, column_name=None):

        self.allowed_reference = {
            'parameterId': parameter_id,
            'parameterPath': None,
            'parameterAlias': None,
            'valueKind': value_kind,
            'columnId': column_id,
            'columnName': column_name
            }

    def set_allowed_reference_from_parameter(self, parameter, value_kind, column_id=None, column_name=None):
        try:
            self.set_allowed_reference_from_id(parameter.id, value_kind.name, column_id=column_id, column_name=column_name)
        except AttributeError: #ValueKind is a string
            self.set_allowed_reference_from_id(parameter.id, value_kind, column_id, column_name)

    def set_allowed_reference_from_value_kind(self, value_kind, column_id=None, column_name=None):
        self.set_allowed_reference_from_parameter(value_kind._parameter, value_kind, column_id=column_id, column_name=column_name)

    @property
    def allowed_reference_resolved(self):
        return _resolve_allowed_reference(self._parameter.snapshot, self.allowed_reference)

    @property
    def allowed_reference(self):
        raise ShadoxForbiddenException('Allowed references are only available on integers, doubles and strings')

    @allowed_reference.setter
    def allowed_reference(self, val):
        raise ShadoxForbiddenException('Allowed references are only available on integers, doubles and strings')

    def _to_dict(self):
        return {'type': self.type}

    def __repr__(self):
        return u'type: ' + self.type


class StringContentType(ContentType):
    def __init__(self, param, json_data={}):
        ContentType.__init__(self, param, VALUE_TYPE_STRING)
        self._allowed_set = json_data.get('allowedSet', None)
        self._allowed_reference = json_data.get('allowedReference', None)

    def _to_dict(self):
        d = ContentType._to_dict(self)
        if self.allowed_set:
            d['allowedSet'] = self.allowed_set
        if self.allowed_reference:
            d['allowedReference'] = self.allowed_reference
        return d

    def __repr__(self):
        return 'type: {}{}{}'.format(
            self.type,
            ", explicit_set_length: {}".format(len(self._allowed_set)) if self._allowed_set else "",
            ", implicit_set: {{id: {}}}".format(self._allowed_reference["parameterId"]) if self._allowed_reference else ""
            )

    @property
    def allowed_set(self):
        return self._allowed_set

    @allowed_set.setter
    def allowed_set(self, allowed_list):
        """
        :param allowed_list: a list of string, for instance : [ "Earth", "Moon" ]
        """
        self._parameter._assert_editable()
        allowed_set = check_list(allowed_list, is_str, 'strings')
        self._allowed_set = allowed_set

    @property
    def allowed_reference(self):
        return self._allowed_reference

    @allowed_reference.setter
    def allowed_reference(self, allowed_reference):
        """
        :param allowed_reference : a dictionary with at least {'parameterId': some_id_in_this_snapshot, 'valueKind': 'default'}
        and {'columnId': id_of_column} if the referenced parameter is a dataframe
        """
        self._parameter._assert_editable()
        self._allowed_reference = _convert_allowed_reference(self, allowed_reference)
        self._allowed_range = None
        self._allowed_set = None




class IntegerContentType(ContentType):
    def __init__(self, param, json_data={}):
        ContentType.__init__(self, param, VALUE_TYPE_INTEGER)
        self._raw_unit = json_data.get('rawUnit', None)
        self._formatted_unit = json_data.get('formattedUnit', None)
        self._quantity = json_data.get('quantity', None)
        self._allowed_set = json_data.get('allowedSet', None)
        self._allowed_range = json_data.get('allowedRange', None)
        self._allowed_reference = json_data.get('allowedReference', None)

    def _to_dict(self):
        d = ContentType._to_dict(self)
        if self.raw_unit:
            d['rawUnit'] = self.raw_unit
        if self.formatted_unit:
            d['formattedUnit'] = self.formatted_unit
        if self.quantity:
            d['quantity'] = self.quantity
        if self.allowed_set:
            d['allowedSet'] = self.allowed_set
        if self.allowed_range:
            d['allowedRange'] = self.allowed_range
        if self.allowed_reference:
            d['allowedReference'] = self.allowed_reference
        return d

    def __repr__(self):
        if self.formatted_unit:
            formatted_unit = self.formatted_unit.encode("utf-8", errors='ignore') if getdefaultencoding() == "ascii" else self.formatted_unit
        else:
            formatted_unit = self.raw_unit
        display = 'type: {0}{1}{2}{3}{4}{5}'.format(
            self.type,
            ", unit: {}".format(formatted_unit),
            ", quantity: {}".format(self.quantity),
            ", explicit_set_length: {}".format(len(self._allowed_set)) if self._allowed_set else "",
            ", implicit_set: {{id: {}}}".format(self._allowed_reference["parameterId"]) if self._allowed_reference else "",
            ", range: {}".format(repr(self.allowed_range)) if self.allowed_range else ""
            )
        return display

    @property
    def formatted_unit(self):
        return self._formatted_unit

    # TODO : add checks
    @property
    def raw_unit(self):
        return self._raw_unit

    @raw_unit.setter
    def raw_unit(self, unit):
        self._parameter._assert_editable()
        self._raw_unit = unit

    # TODO : add checks
    @property
    def quantity(self):
        return self._quantity

    @quantity.setter
    def quantity(self, q):
        self._parameter._assert_editable()
        self._quantity = q

    @property
    def allowed_range(self):
        return self._allowed_range

    @allowed_range.setter
    def allowed_range(self, allowed_range):
        """
        :param allowed_range: a dictionnary with two entries min and max. A least of them must contains an integer
        """
        self._parameter._assert_editable()
        if allowed_range is None:
            self._allowed_range = None
            return
        min = allowed_range.get('min')
        max = allowed_range.get('max')

        if min is None and max is None:
            self._allowed_range = None
            return

        valid_min = min is None or is_int(min)
        valid_max = max is None or is_int(max)
        if not valid_min or not valid_max:
            raise ShadoxForbiddenException('Range boundaries must be integers.')
        if min is not None and max is not None and min > max:
            raise ShadoxForbiddenException('{} must be lower than {}'.format(min, max))
        self._allowed_set = None
        self._allowed_reference = None
        self._allowed_range = allowed_range

    def set_allowed_range(self, min=None, max=None):
        self.allowed_range = {'min': min, 'max': max}

    @property
    def allowed_reference(self):
        return self._allowed_reference

    @allowed_reference.setter
    def allowed_reference(self, allowed_reference):
        """
        :param allowed_reference : a dictionary with at least {'parameterId': some_id_in_this_snapshot, 'valueKind': 'default'}
        and {'columnId': id_of_column} if the referenced parameter is a dataframe
        """
        self._parameter._assert_editable()

        self._allowed_reference = _convert_allowed_reference(self, allowed_reference)
        self._allowed_range = None
        self._allowed_set = None

    @property
    def allowed_set(self):
        return self._allowed_set

    @allowed_set.setter
    def allowed_set(self, allowed_list):
        """
        :param allowed_list: a list of integer, for instance : [ 100, 200, 300 ]
        """
        self._parameter._assert_editable()
        allowed_set = check_list(allowed_list, is_int, 'integers')
        self._allowed_range = None
        self._allowed_reference = None
        self._allowed_set = allowed_set


class DoubleContentType(IntegerContentType):
    def __init__(self, param, json_data={}):
        IntegerContentType.__init__(self, param, json_data)
        ContentType.__init__(self, param, VALUE_TYPE_DOUBLE)
        self._precision = json_data.get('precision', None)

    def _to_dict(self):
        d = IntegerContentType._to_dict(self)
        if self.precision:
            d['precision'] = self.precision
        return d

    @property
    def precision(self):
        return self._precision

    @precision.setter
    def precision(self, p):
        """
        :param p: an integer. All values will be rounded to the nearest 10^p
        """
        self._parameter._assert_editable()
        if type(p) is not int and p is not None:
            raise ShadoxForbiddenException('Expecting an integer')
        self._precision = p

    @property
    def allowed_set(self):
        return self._allowed_set

    @allowed_set.setter
    def allowed_set(self, allowed_list):
        """
        :param allowed_list: a list of floats, for instance : [ 100.0, 200.5, 300. ]
        """
        self._parameter._assert_editable()
        allowed_set = check_list(allowed_list, is_float, 'floats')
        self._allowed_range = None
        self._allowed_reference = None
        self._allowed_set = allowed_set

    @property
    def allowed_range(self):
        return self._allowed_range

    @allowed_range.setter
    def allowed_range(self, allowed_range):
        """
        :param allowed_range: a dictionnary with two entries min and max. A least of them must contains a float
        """
        self._parameter._assert_editable()
        if allowed_range is None:
            self._allowed_range = None
            return
        min = allowed_range.get('min')
        max = allowed_range.get('max')
        if min is None and max is None:
            self._allowed_range = None
            return

        valid_min = min is None or is_float(min)
        valid_max = max is None or is_float(max)
        if not valid_min or not valid_max:
            raise ShadoxForbiddenException('Range boundaries must be floats.')
        if min is not None and max is not None and min > max:
            raise ShadoxForbiddenException('{} must be lower than {}'.format(min, max))
        self._allowed_set = None
        self._allowed_reference = None
        self._allowed_range = allowed_range

    def set_allowed_range(self, min=None, max=None):
        self.allowed_range = {'min': min, 'max': max}

    def __repr__(self):
        return '{}, precision: {}'.format(IntegerContentType.__repr__(self), self.precision)

    @property
    def allowed_reference(self):
        return self._allowed_reference

    @allowed_reference.setter
    def allowed_reference(self, allowed_reference):
        """
        :param allowed_reference : a dictionary with at least {'parameterId': some_id_in_this_snapshot, 'valueKind': 'default'}
        and {'columnId': id_of_column} if the referenced parameter is a dataframe
        """
        self._parameter._assert_editable()

        allowed_reference = _convert_allowed_reference(self, allowed_reference)
        self._allowed_range = None
        self._allowed_set = None
        self._allowed_reference = allowed_reference


class FileContentType(ContentType):
    def __init__(self, param, json_data={}):
        ContentType.__init__(self, param, VALUE_TYPE_FILE)
        self._subtype = json_data.get('subtype', None)
        self.simulation = False

    def _to_dict(self):
        d = ContentType._to_dict(self)
        if self.subtype:
            d['subtype'] = self.subtype
        return d

    @property
    def subtype(self):
        return self._subtype

    @subtype.setter
    def subtype(self, subtype):
        self._parameter._assert_editable()
        check_file_subtype(subtype)
        self._subtype = subtype

    def __repr__(self):
        return '{}, subtype: {}'.format(ContentType.__repr__(self), self.subtype)


def check_list(li, check_type, type_name):
    if li is None:
        return None
    if type(li) is not list or len(li) == 0:
        raise ShadoxForbiddenException('A non empty list of {} is expected'.format(type_name))
    cleaned_list = []
    for a in li:
        if not check_type(a):
            raise ShadoxForbiddenException('A list of {} is expected'.format(type_name))
        if a not in cleaned_list:
            cleaned_list.append(a)
    return cleaned_list


def is_str(s):
    return isinstance(s, string_types)


def is_int(i):
    return isinstance(i, integer_types)


def is_float(f):
    return isinstance(f, float)


def is_numeric(n):
    return is_int(n) or is_float(n) or n is None


def freeze(o):
    if isinstance(o, dict):
        return frozenset({ k:freeze(v) for k,v in o.items()}.items())

    if isinstance(o, list):
        return tuple([freeze(v) for v in o])

    return o


def make_hash(o):
    """
    makes a hash out of anything that contains only list,dict and hashable types including string and numeric types
    """
    return hash(freeze(o))
