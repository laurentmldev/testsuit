from six import string_types
from datetime import datetime

from ..utils import (
    convert_shadox_to_python,
    check_python_value_match_shadox,
    log_action,
    hash_parameter_value,
    validate_vk_name,
    CANNOT_HAVE_VALUE_KIND)
from ..exceptions import ShadoxUnauthorizedException, ShadoxForbiddenException

_vk_not_found_str_format = "Value kind {} does not exist in parameter, maybe refresh the client"

def _validate_vk(vk):
    if not isinstance(vk, string_types):
        raise TypeError("Names of value-kinds must be strings!")
    if vk.startswith('_'):
        raise KeyError("Names of value-kinds can not start with '_'!")

class ValueKindsCollection(object):
    def __init__(self, parameter, client):
        self._parameter = parameter
        self._client = client
        self._values = {}
        self._hash_values = {}
        self._files_hashes = {}
        self._html_previews = {}
        self._authorized_keys = []
        self._touched_keys = set()
        self._touched_comments = set()
        self._is_structure_modified = False
        for d in parameter._values_definition:
            self._authorized_keys.append(d['name'])
            self._values[d['name']] = ValueKind(
                self,
                d['name'],
                d
            )

    def _create_value_kind(self, name):
        self._parameter._assert_editable()
        validate_vk_name(name)
        if name in self._authorized_keys:
            raise ShadoxUnauthorizedException("Value kind " + name + " already exists")
        vk = ValueKind._new_vk(self, name)
        self._values[name] = vk
        self._parameter._values_definition.append({'name': name})
        # self._touched_keys.add(name) # Is value dirty yet ?
        self._authorized_keys.append(name)
        self._hash_values[name] = ''
        self._is_structure_modified = True
        return vk

    def _delete_value_kind(self, name):
        self._parameter._assert_editable()
        if isinstance(name, ValueKind):
            name = name.name
        self[name] # check existence of the value kind
        if len(self._authorized_keys) == 1:
            raise ShadoxUnauthorizedException("Cannot delete the last remaining valuekind of a parameter")
        del self._values[name]
        values_def = self._parameter._values_definition
        [values_def.remove(o) for o in values_def if o['name'] == name]
        if name in self._hash_values:
            del self._hash_values[name]
        self._authorized_keys.remove(name)
        if name in self._touched_keys:
            self._touched_keys.remove(name)
        self._is_structure_modified = True

    def _rename_value_kind(self, oldname, newname):
        if self._parameter.definition.structure in CANNOT_HAVE_VALUE_KIND:
            raise ShadoxForbiddenException('This structure cannot have value kind different than the default one')
        self._parameter._assert_editable()
        vk = self._values[oldname]
        if newname in self._authorized_keys:
            raise ShadoxUnauthorizedException("Value kind " + newname + " already exists")
        validate_vk_name(newname)
        vk._name = newname
        del self._values[oldname]
        self._values[newname] = vk
        for v in self._parameter._values_definition:
            if v['name'] == oldname:
                v['name'] = newname
        if oldname in self._touched_keys:
            self._touched_keys.remove(oldname)
            self._touched_keys.add(newname) # value needs to be repushed
        previous_index = self._authorized_keys.index(oldname)
        self._authorized_keys.remove(oldname)
        self._authorized_keys.insert(previous_index, newname)
        if oldname in self._hash_values:
            del self._hash_values[oldname]
            self._hash_values[newname] = '' # so value is pushed
        self._is_structure_modified = True

    def _reorder_value_kinds(self, namelist):
        if len(self._authorized_keys) != len(namelist) or set(self._authorized_keys) != set(namelist):
            raise ShadoxUnauthorizedException('The reordered list must contain all the current valuekinds')
        self._authorized_keys.sort(key=lambda key: namelist.index(key))
        self._parameter._values_definition.sort(key=lambda valdef: namelist.index(valdef['name']))
        self._is_structure_modified = True

    def _set_vk_default(self, newdefault):
        self._parameter._assert_editable() # cannot check in _reorder() as it is used when refreshing
        self._reorder_value_kinds(sorted(self._authorized_keys, key=lambda key: (-1 if key == newdefault else 0)))

    def __dir__(self):
        """
        Function used to make the autocompletion in ipython.
        :return list of existing attributes
        """
        return list(self._authorized_keys)

    def __getattr__(self, key):
        """
        Magic method to get an attr
        For every attr starting with '_', default behaviour

        :param key:
        :return attr
        """
        if key.startswith('_'):
            return super(ValueKindsCollection, self).__getattr__(key)
        elif not key in self._authorized_keys:
            raise AttributeError(_vk_not_found_str_format.format(key))
        else:
            return self[key] # self.__getitem__(key)

    def __setattr__(self, key, value):
        if key.startswith('_'):
            super(ValueKindsCollection, self).__setattr__(key, value)
        else:
            self[key] = value  # self.__setitem__(key, value)

    def __delattr__(self, key):
        self._delete_value_kind(key)

    def __getitem__(self, key):
        """Method to get the value-kind whose name is the argument key.
        If they are not fetched already, the api is queried
        :param value kind
        :return value or error"""
        _validate_vk(key)
        if not key in self._authorized_keys:
            raise KeyError(_vk_not_found_str_format.format(key))

        if not key in self._values: # init in case of refresh_with
            self._values[key] = ValueKind(
                self,
                key,
                {}
            )
        return self._values[key]

    def __setitem__(self, key, value):
        raise ShadoxUnauthorizedException("Use parameter.create_value_kind('somevk') to create a value kind")

    def __delitem__(self, key):
        self._delete_value_kind(key)

    def __iter__(self):
        """Iter on value-kinds."""
        for key in self._authorized_keys:
            yield getattr(self, key)

    def __len__(self):
        """Define length of ValueKindsCollection as the number of ValueKinds
        """
        return len(self._authorized_keys)

    def _fetch_value(self, key):
        """
        Fetch a parameter value via api call
        :param key:
        :return
        """
        log_action(
            u"|- Get parameter value '{}' for '{}'".format(key, self._parameter.name),
            4,
            self._parameter.client.loggerLevel
        )

        # Fetch the value via API
        value = self._client.get_parameter_value(self._parameter.id, key)
        py_value = convert_shadox_to_python(self._parameter, value, key)

        return py_value

    def get_authorized_keys(self):
        return self._authorized_keys

    def get_hash_values(self):
        return self._hash_values

    def get_values(self):
        return self._values

    def get_touched_keys(self):
        return self._touched_keys

    def __repr__(self):
        return 'ValueKinds[' + ', '.join(vk.name for vk in self) + ']'


class ValueKind(object):

    def __init__(self, collection, name, value_definition):
        self._collection = collection
        self._parameter = collection._parameter
        self._name = name
        self._uninitialized = True
        self._value = None

        self._comment = value_definition.get('comment', None)
        self._preview = value_definition.get('preview', None)
        last_modified = value_definition.get('lastModified', None)
        self._last_modified = datetime.fromtimestamp(last_modified / 1000) if last_modified and last_modified > 0 else None

    @classmethod
    def _new_vk(cls, collection, name):
        # Create a new, empty value kind
        vk = cls(collection, name, {})
        vk._uninitialized = False
        vk._value = convert_shadox_to_python(vk._parameter, None, name)
        return vk

    def delete(self):
        self._collection._delete_value_kind(self.name)

    def rename(self, newname):
        self._collection._rename_value_kind(self.name, newname)

    def set_as_default(self):
        self._collection._set_vk_default(self.name)

    @property
    def value(self):
        if self._uninitialized:
            self._value = self._collection._fetch_value(self.name)
            self._uninitialized = False
            self._collection._hash_values[self.name] = hash_parameter_value(self._parameter, self._value)
        self._collection._touched_keys.add(self.name)

        return self._value

    @value.setter
    def value(self, value):
        key = self._name
        self._parameter._assert_editable()

        log_action(
            u'|- Set parameter value "{}" for "{}" = {}'.format(key, self._parameter.name, value),
            4,
            self._parameter.client.loggerLevel
        )

        # Check python struct & value match shadox expectation, raise an exception if not conform
        check_python_value_match_shadox(self._parameter, value, key)

        self._value = convert_shadox_to_python(self._parameter, value, key)
        self._uninitialized = False
        self._collection._touched_keys.add(key)
        # Overwritting the value will always result in it being pushed
        self._collection._hash_values[key] = ''

    @value.deleter
    def value(self):
        self.value = None

    @property
    def comment(self):
        '''An empty comment should be the empty string rather than None
        '''
        return self._comment if self._comment else ''

    @comment.setter
    def comment(self, comment):
        key = self._name
        if self._parameter.is_imported:
            raise ShadoxUnauthorizedException("An imported parameter can not be updated!")

        self._collection._touched_comments.add(key)
        self._comment = comment if comment else ''

    @comment.deleter
    def comment(self):
        self.comment = ''

    @property
    def preview(self):
        return self._preview

    @property
    def last_modified(self):
        return self._last_modified

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, newname):
        self.rename(newname)

    def __repr__(self):
        return 'ValueKind '+ self.name
