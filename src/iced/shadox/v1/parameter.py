#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from .utils import (convert_shadox_to_python, check_python_value_match_shadox, log_action, format_value_before_update)
from .exceptions import ShadoxParameterValueNotFoundException, ShadoxUnauthorizedException


class Parameter(object):
    def __init__(self, shadox_client, json_data):
        self.id = json_data['id']
        self.name = json_data['name']
        self.path = json_data.get('path', '/')
        self.alias = json_data.get('alias', [])
        self.alerts = json_data.get('alerts', [])
        self.definition = json_data['definition']
        self.values_definition = json_data['values']
        self.values = ParameterValues(self, shadox_client)
        self.client = shadox_client
        self._export = json_data.get('export', False)
        self.is_imported = json_data.get('import', False)
        self.last_modified = json_data.get('lastModified', '')

    def refresh_with(self, new_parameter):
        self.name = new_parameter.name
        self.path = new_parameter.path
        self.alias = new_parameter.alias
        self.alerts = new_parameter.alerts
        self.last_modified = new_parameter.last_modified

        # Update the values before the definition
        authorized_keys = self.values.__getattr__('_authorized_keys')
        hash_values = self.values.__getattr__('_hash_values')
        values = self.values.__getattr__('_values')
        touched_keys = self.values.__getattr__('_touched_keys')
        new_authorized_keys = new_parameter.values.__getattr__('_authorized_keys')
        # Check for deleted values kind
        for d in self.values_definition:
            if not (d['name'] in new_authorized_keys):
                authorized_keys.remove(d['name'])
                touched_keys.remove(d['name'])
                hash_values.pop(d['name'], None)
                values.pop(d['name'], None)
        # Check for inserted values kind
        for d in new_parameter.values_definition:
            authorized_keys.add(d['name'])

        self.definition = new_parameter.definition
        self.values_definition = new_parameter.values_definition
        self.values.__setattr__('_authorized_keys', authorized_keys)
        self.values.__setattr__('_hash_values', hash_values)
        self.values.__setattr__('_values', values)
        self.values.__setattr__('_touched_keys', touched_keys)

    @property
    def default_value(self):
        """Get the default value"""
        first_value_name = self.values_definition[0]['name']
        return self.values.__getattr__(first_value_name)

    @default_value.setter
    def default_value(self, value):
        """Set the default value"""
        first_value_name = self.values_definition[0]['name']
        self.values.__setattr__(first_value_name, value)

    def build_update(self):
        """
        Build an array of values to update
        :return:
        """
        values = []
        keys = []
        hash_values = self.values.__getattr__('_hash_values')
        for key in self.values.__getattr__('_touched_keys'):
            value = self.values.__getattr__(key)
            if hash(str(value)) != hash_values[key]:
                log_action(
                    "|--- Build parameter update for '{}' key '{}'".format(self.name, key),
                    4,
                    self.client.loggerLevel
                )

                # Check the value match shadox type
                check_python_value_match_shadox(self, value, key)

                # Format value before sending to shadox
                value = format_value_before_update(self, value, key)
                keys.append(key)
                values.append(dict(
                    id=self.id,
                    value=key,
                    data=value
                ))
        return {'values': values, 'keys': keys}

    def is_dirty(self):
        """
        Indicates if a push is required to send updated data to the server
        """
        hash_values = self.values.__getattr__('_hash_values')
        values = self.values.__getattr__('_values')
        for key in self.values.__getattr__('_touched_keys'):
            value = values[key]
            if hash(str(value)) != hash_values[key]:
                return True

        return False

    def refresh_hash(self):
        """Refresh hashes in parameter's values, it should be called after a push"""
        new_hash_value = {}
        values = self.values.__getattr__('_values')
        for key in self.values.__getattr__('_touched_keys'):
            value = values[key]
            new_hash_value[key] = hash(str(value))
        self.values.__setattr__('_hash_values', new_hash_value)

    def __repr__(self):
        return "\nid:'{}' name:'{}' path:'{}' alias:'{}'\n".format(self.id, self.name, self.path, self.alias)


class ParameterValues(object):
    def __init__(self, parameter, client):
        self._parameter = parameter
        self._client = client
        self._values = {}
        self._hash_values = {}
        self._html_previews = {}
        self._authorized_keys = set()
        self._touched_keys = set()
        for d in parameter.values_definition:
            self._authorized_keys.add(d['name'])

    def __dir__(self):
        """
        Function used to make the autocompletion in ipython.
        :return list of existing attributes
        """
        return list(self._authorized_keys)

    def __setattr__(self, key, value):
        """
        Magic method to set an attr
        For every attr starting with '_', default behaviour
        Standard attr, check if the value match the parameter type
        :param key:
        :param value:
        :return:
        """
        if key.startswith('_'):
            super(ParameterValues, self).__setattr__(key, value)
        else:
            # Check the key is in the parameters values
            if key not in self._authorized_keys:
                raise ShadoxParameterValueNotFoundException("Parameter Value '{}' does not exist!".format(key))

            if self._parameter.is_imported:
                raise ShadoxUnauthorizedException("An imported parameter can not be updated!")

            log_action(
                u'|- Set parameter value "{}" for "{}" = {}'.format(key, self._parameter.name, value),
                4,
                self._parameter.client.loggerLevel
            )

            # Check python struct & value match shadox expectation, raise an exception if not conform
            check_python_value_match_shadox(self._parameter, value, key)

            # If the key is not touched, we set empty values to trigger the dirty behaviour
            if key not in self._touched_keys:
                self._touched_keys.add(key)
                self._hash_values[key] = ''

            self._values[key] = convert_shadox_to_python(self._parameter, value, key, False)

    def __getattr__(self, key):
        """
        Magic method to get an attr
        For every attr starting with '_', default behaviour
        Standard attr, if they're not fetched already, we ask the api
        :param key:
        :return attr
        """
        if key.startswith('_'):
            return self.__getattribute__(key)
        else:
            if key not in self._values:
                log_action(
                    "|- Get parameter value '{}' for '{}'".format(key, self._parameter.name),
                    4,
                    self._parameter.client.loggerLevel
                )
                # Fetch the value via API
                value = self._fetch_value(key)
                py_value = convert_shadox_to_python(self._parameter, value, key, True)
                self._hash_values[key] = hash(str(py_value))
                self._touched_keys.add(key)
                self._values[key] = py_value

            return self._values[key]

    def _fetch_value(self, key):
        """
        Fetch a parameter value via api call
        :param key:
        :return
        """
        return self._client.get_parameter_value(self._parameter.id, key)
