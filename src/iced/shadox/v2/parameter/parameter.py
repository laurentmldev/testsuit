#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from six import string_types
from binascii import b2a_hex
from os import urandom
from time import time
import warnings

from .parameter_comments import ParameterComments
from .parameter_definition import ParameterDefinition
from .parameter_origin import ParameterOrigin, build_new_origin
from .parameter_values import ParameterValues
from .value_kinds import ValueKindsCollection
from ..exceptions import ShadoxUnauthorizedException, ShadoxForbiddenException
from ..factory.parameters import ParametersFactory
from ..factory.values import ValuesFactory
from ..utils import (
    check_python_value_match_shadox,
    log_action,
    format_value_before_update,
    parameter_has_file,
    has_files_hash_diff,
    hash_parameter_value,
    add_list_items,
    delete_list_items,
    validate_tag,
    validate_alias,
    validate_param_name,
    validate_path_param,
    build_member_name,
    split_member_name,
    is_in_group,
    replace_mname_in_aliases,
    check_tags_is_in_list_project_tags,
    CANNOT_HAVE_VALUE_KIND
)


def _validate_tags(list_tags, project_tags, is_imported):
    global_tags = []
    for tag in list_tags:
        if ":" not in tag:
            validate_tag(tag)
        else:
            global_tags.append(tag)
    if global_tags:
        if is_imported:
            raise ShadoxForbiddenException('Cannot add global tags to imported parameter')
        check_tags_is_in_list_project_tags(global_tags, project_tags)


def _validate_aliases(list_tags):
    for alias in list_tags:
        validate_alias(alias)

_ERR_ALIAS_GROUP = 'member aliases are inherited from group'

class Parameter(object):
    def __init__(self, shadox_client, json_data, snapshot):
        self.client = shadox_client
        self.snapshot = snapshot
        self._is_in_creation = False
        self.deleted = False
        self._id = json_data['id']
        self._name = json_data['name']
        self._path = json_data.get('path', '/')
        self._alias = json_data.get('alias', [])
        self._alerts = json_data.get('alerts', [])
        self._definition = ParameterDefinition(self, json_data['definition'])
        self._def_hash = self.definition.get_hash()
        self._values_definition = json_data['values']
        self._tags = json_data.get('tags', [])
        self._value_kinds = ValueKindsCollection(self, shadox_client)
        self._values = ParameterValues(self.value_kinds)
        self._comments = ParameterComments(self.value_kinds)
        self._origin = ParameterOrigin(json_data['origin']) if json_data.get('origin', None) else None
        self._export = json_data.get('export', False)
        self._is_imported = json_data.get('import', False)
        self._last_modified = json_data.get('lastModified', '')
        self._has_file = parameter_has_file(self)
        self._touched_attributes = set()
        self._updated_origin = None

    @classmethod
    def _new_parameter(cls, shadox_client, snapshot, path, name, structure, content_type, dataframe_columns, id=None):
        validate_param_name(name)
        validate_path_param(path)
        ParametersFactory.dispatch(structure)
        definition = {
            'description': '',
            'simulation': True,
            'structure': structure,
        }
        if structure == 'dataframe':
            definition.update({'columns': []})
            for col in dataframe_columns:
                if len(col) != 2:
                    raise ShadoxForbiddenException("Expected Format is a list of ('column_title', 'type')")
                ValuesFactory.default_df_value(col[1])  # check all types are valid
        elif structure == 'document':
            definition.update({'documentType': {'subtype': "EXCEL"}})
        elif structure != 'notebook':
            definition.update({'contentType': {'type': content_type}})

        if not id:
            id = '{0:x}'.format(int(time())) + b2a_hex(urandom(8)).decode('ascii')

        data = {
            'id': id,
            'name': name,
            'path': path,
            'values': [{
                'name': 'default',
                'preview': None,
                'comment': ''
            }],
            'definition': definition
        }
        p = Parameter(shadox_client, data, snapshot)
        p._check_availability_path_name(p.path, p.name)
        if structure == 'dataframe':
            p.first_value = {}
            for col in dataframe_columns:
                p.definition.add_col(col[0], col[1])
        else:
            p.first_value = None
        p._is_in_creation = True
        return p

    def _check_availability_aliases(self, aliases):
        for alias in aliases:
            p = self.snapshot.search_parameter_by_alias(alias)
            if p:
                raise ShadoxForbiddenException('There is already a parameter with alias ' + alias + ' : ' + str(p))
            g = self.snapshot.search_group_by_alias(alias)
            if g:
                raise ShadoxForbiddenException('There is already a group with alias ' + alias + ' : ' + str(g))

    def _check_availability_path_name(self, path, name):
        p = self.snapshot.search_parameter_by_path(path + name)
        if p:
            raise ShadoxForbiddenException('There is already a parameter with this name and path : ' + str(p))

    def _assert_editable(self):
        """ Asserts the parameter can be edited"""
        if self.is_imported:
            raise ShadoxUnauthorizedException("Parameter is imported, it can not be updated!")
        if self.deleted:
            raise ShadoxUnauthorizedException("Parameter is deleted, it can not be updated!")

    def refresh_with(self, new_parameter):
        """ called by ShadoxClient.refresh() to update structure and value
        of all Parameter instances.
        :param new_parameter:
        :return: None
        """
        self._name = new_parameter.name
        self._path = new_parameter.path
        self._alias = new_parameter.alias
        self._alerts = new_parameter.alerts
        self._last_modified = new_parameter.last_modified

        # Do not fail in _assert_editable for imported parameters (server decides if they can change or not)
        self._is_imported = False

        # Update the values before the definition
        new_authorized_keys = tuple(new_parameter.values.get_authorized_keys())
        # Check for inserted values kind
        for key in dir(new_parameter.value_kinds):
            if key not in self.value_kinds._authorized_keys:
                self.value_kinds._authorized_keys.append(key)
        # Check for deleted values kind
        for key in dir(self.value_kinds):
            if key not in new_authorized_keys:
                del self.value_kinds[key]
        self._values_definition = new_parameter._values_definition
        self.value_kinds._reorder_value_kinds(new_authorized_keys)
        self.value_kinds._is_structure_modified = False

        self._definition = ParameterDefinition(self, new_parameter.definition._to_dict())
        self._def_hash = new_parameter._def_hash
        self._values_definition = new_parameter._values_definition
        self._tags = new_parameter.tags
        self._origin = new_parameter.origin
        self._is_imported = new_parameter.is_imported
        # self._values & self._comments are proxies to self.value_kinds
        self._updated_origin = None

        # "dirty" fields have been overwritten by update
        self._touched_attributes.clear()

    @property
    def id(self):
        return self._id

    @property
    def alerts(self):
        return self._alerts

    @property
    def value_kinds(self):
        return self._value_kinds

    @property
    def origin(self):
        """Origin of an imported parameter

        Raises:
            ShadoxForbiddenException: When user does not have BROWSE rights on the original dataset

        Returns:
            None or ParameterOrigin: The origin of the parameter if it is imported from another dataset
                or None if it is not imported
        """
        if self._origin and self._origin.dataset_path == 'RESTRICTED@UNKNOWN':
            raise ShadoxForbiddenException('This parameter has an origin but you do not have access to it')
        return self._origin

    @origin.setter
    def origin(self, origin):
        if origin is None:
            self.change_origin(None)
        elif isinstance(origin, ParameterOrigin):
            if not origin.parameter_id:
                raise ShadoxUnauthorizedException('you do not have access to the id of this origin')
            self.change_origin(
                origin.dataset_path,
                origin.publication_variant,
                origin.publication_version,
                origin.parameter_id
            )
        elif isinstance(origin, Parameter):
            origin_p = origin
            origin_client = origin.client
            if origin_client == self.client or origin_client.dataset_path == self.client.dataset_path:
                raise ShadoxForbiddenException('Cannot import a parameter to and from the same dataset')
            origin_pub = origin_client.get_current_publication()
            if not origin_pub.publication_date:
                raise ShadoxForbiddenException('Cannot import a parameter from an undelivered publication')
            if origin_pub.deprecation_date:
                raise ShadoxForbiddenException('Cannot import a parameter from a deprecated publication')
            origin_dpath = origin_client.dataset_path.split('/')[-1]

            if origin_p.definition.structure != self.definition.structure:
                raise ShadoxForbiddenException('Structure must match when importing a parameter')

            self.change_origin(
                origin_dpath,
                origin_pub.variant,
                origin_pub.name,
                origin_p.id
            )
        else:
            raise TypeError('Expected None, ParameterOrigin or Parameter but got ' + str(type(origin)))

    @property
    def last_modified(self):
        return self._last_modified

    @property
    def is_imported(self):
        return self._is_imported

    @is_imported.setter
    def is_imported(self, is_imported):
        if not is_imported:
            self.origin = None
        else:
            raise AttributeError('must explicitly set origin')

    @property
    def has_file(self):
        return self._has_file

    @property
    def definition(self):
        return self._definition

    @property
    def values_definition(self):
        return self._values_definition

    @property
    def values(self):
        return self._values

    @property
    def comments(self):
        return self._comments

    @property
    def export(self):
        return self._export

    @export.setter
    def export(self, export):
        if export != self._export:
            if is_in_group(self.name):
                raise ShadoxForbiddenException('member export status is inherited from group')
            self._export = export
            self._touched_attributes.add("export")

    @property
    def name(self):
        return self._name

    @property
    def member_name(self):
        '''Without group name prefix'''
        gname, mname = split_member_name(self._name)
        return mname

    @name.setter
    def name(self, name):
        gname, mname = split_member_name(self._name)
        new_gname, new_mname = split_member_name(name)
        if new_gname and gname != new_gname:
            raise ShadoxForbiddenException('group name cannot be changed through parameter')
        if new_mname != mname:
            if gname:
                validate_alias(new_mname)
            else:
                validate_param_name(new_mname)

            new_name = build_member_name(gname, new_mname)
            self._check_availability_path_name(self.path, new_name)
            self._name = new_name
            self._touched_attributes.add("name")
            if gname:
                self._alias = replace_mname_in_aliases(self._alias, mname, new_mname)
                # no need to mark parameter's alias for modification

    @property
    def path(self):
        return self._path

    @path.setter
    def path(self, path):
        if path != self._path:
            validate_path_param(path)
            if is_in_group(self.name):
                raise ShadoxForbiddenException('member path is inherited from group')
            self._check_availability_path_name(path, self.name)
            if self.snapshot.tree and path not in self.snapshot.tree:
                self.snapshot.add_folder(path)
            self._path = path
            self._touched_attributes.add("path")

    @property
    def directories(self):
        """returns the Parameter path as a collection of directories.
        """
        return ('/',) + tuple(ele for ele in self._path.split('/') if ele)

    @property
    def alias(self):
        return self._alias

    def describe(self, description):
        self.definition.description = description

    @alias.setter
    def alias(self, alias):
        self.delete_aliases()
        self.add_aliases(alias)

    def add_aliases(self, list_alias):
        if is_in_group(self.name):
            raise ShadoxForbiddenException(_ERR_ALIAS_GROUP)
        alias, changed_aliases = add_list_items(self._alias, list_alias, _validate_aliases)
        if changed_aliases:
            self._check_availability_aliases(changed_aliases)
            self._alias = alias
            self._touched_attributes.add('alias')

    def delete_aliases(self, list_alias=None):
        """
        Delete aliases from the parameter
        """
        if is_in_group(self.name):
            raise ShadoxForbiddenException(_ERR_ALIAS_GROUP)
        self._alias = delete_list_items(self._alias, list_alias)
        self._touched_attributes.add('alias')

    def _validate_tags(self, list_tags):
        return _validate_tags(list_tags, self.client.get_project_tags() if self.client else {}, self.is_imported)

    @property
    def tags(self):
        return self._tags

    @tags.setter
    def tags(self, tags):
        self.delete_tags()
        self.add_tags(tags)

    def add_tags(self, list_tags, global_tags = False):
        tags, tags_changed = add_list_items(self._tags, list_tags, self._validate_tags if not global_tags else (lambda t_list: None), sortItems=True)
        if tags_changed:
            self._tags = tags
            self._touched_attributes.add('tags')

    def add_global_tag(self, cluster_name, tag_name):
        if not isinstance(cluster_name, string_types) or not isinstance(tag_name, string_types):
            raise ShadoxForbiddenException('Cluster and tag name must be strings')
        self.add_tags([cluster_name + ':' + tag_name])

    def delete_tags(self, list_tags=None):
        """
        Delete tags from the parameter
        """
        self._tags = delete_list_items(self._tags, list_tags)
        self._touched_attributes.add('tags')

    def change_origin(self, dataset_path, publication_variant = None, publication_version = None, origin_parameter_id = None, origin_parameter_alias = None):
        """
        Creates, modifies or deletes the dependency this parameter has on a parameter of another dataset.
        """
        if is_in_group(self.name):
            raise ShadoxForbiddenException('member origin is inherited from group')
        if dataset_path is None and self.origin is None:
            return

        self._updated_origin, self._origin = build_new_origin(self._id, dataset_path, publication_variant, publication_version, origin_parameter_id, origin_parameter_alias)
        self._is_imported = bool(self._origin)

    def update_origin_version(self, version):
        """
        Upgrade the current origin to a new publication of the same variant
        :param version: the number of the publication to which the parameter origin should be updated
        """
        if not self._is_imported:
            raise ShadoxUnauthorizedException("The parameter {} is not imported, its origin cannot be updated".format(self))
        param_id = self._origin.parameter_id
        param_alias = None
        if not param_id:
            if not self._updated_origin:
                raise ShadoxUnauthorizedException("You do not have access to the origin of this parameter")
            else:
                param_id = self._updated_origin['originParameter'].get('id', None)
                param_alias = self._updated_origin['originParameter'].get('alias', None)

        self._updated_origin, self._origin = build_new_origin(self._id, self._origin.dataset_path,
                self._origin.publication_variant, version, param_id, param_alias)

    @property
    def default_value(self):
        """DEPRECATED - use parameter.first_value instead"""
        warnings.warn(
                "Please use parameter.first_value instead",
                DeprecationWarning,
                stacklevel=2
            )
        return self.first_value

    @default_value.setter
    def default_value(self, value):
        """DEPRECATED - use parameter.first_value instead"""
        warnings.warn(
                "Please use parameter.first_value instead",
                DeprecationWarning,
                stacklevel=2
            )
        self.first_value = value

    @default_value.deleter
    def default_value(self):
        """DEPRECATED - use del parameter.first_value instead"""
        warnings.warn(
                "Please use del parameter.first_value instead",
                DeprecationWarning,
                stacklevel=2
            )
        del self.first_value

    @property
    def first_value(self):
        """Value of the first value kind"""
        return next(vk for vk in self.value_kinds).value

    @first_value.setter
    def first_value(self, value):
        next(vk for vk in self.value_kinds).value = value

    @first_value.deleter
    def first_value(self):
        del next(vk for vk in self.value_kinds).value

    def _reset_values(self, idx=None):
        """
        Reset all value kinds with a default value
        :param idx: for a dataframe, we reset the values of a column
        """
        if hasattr(self.definition, 'content_type'):
            log_action(u"As a result of a modification of the content type, all values are reset to NA.", 1, self.client.loggerLevel)
            for vk in self.value_kinds:
                vk.value = None
        elif hasattr(self.definition, 'columns'):
            col = self.definition.columns[idx]
            log_action(
                u"As a result of the modification of the content type of the column {}, all its values are reset to a default value.".format(col.title),
                1, self.client.loggerLevel)
            default_val = ValuesFactory.default_df_value(col.content_type.type)
            for vk in self.value_kinds:
                vk.value[col.title] = default_val

    def _add_df_col(self, new_title, type):
        """
        INTERNAL use only
        :param new_title: title of the new column
        :param type: type of the new column
        """
        for vk in self.value_kinds:
            default_val = ValuesFactory.default_df_value(type)
            vk.value[new_title] = default_val

    def _rename_df_col_title(self, idx, new_title):
        """
        INTERNAL use only
        :param new_title: new title for the dataframe column to rename
        """
        for vk in self.value_kinds:
            titles = vk.value.columns.values
            titles[idx] = new_title
            vk.value.columns = titles

    def _delete_col_values(self, title):
        """
        INTERNAL use only
        :param title: the title of the column to be deleted
        """
        if self.definition.structure != 'dataframe':
            return
        for vk in self.value_kinds:
            vk.value.drop(title, axis=1, inplace=True)

    def _reorder_df_col_values(self, ordered_titles):
        """
        INTERNAL use only
        :param ordered_titles: list of ordered titles
        """
        if self.definition.structure != 'dataframe':
            return
        for vk in self.value_kinds:
            vk.value = vk.value[ordered_titles]

    def delete(self):
        if self.deleted:
            return False
        if is_in_group(self.name):
            g = self.get_group()['Group: ']
            g.remove_members(self)
        updates = self.build_update()
        pending_updates = updates['values'] or updates['fields']
        if pending_updates:
            log_action(
                u"The modifications on valuekinds " + str([(u['value'] if 'value' in u else '?' ) for u in pending_updates]) + " will be lost when deleting parameter",
                0,
                self.client.loggerLevel
            )
        self.deleted = True
        self.snapshot.parameters.remove(self)
        if not self._is_in_creation:
            self.snapshot._deleted_parameters.append(self)

    def create_value_kind(self, name):
        if self.definition.structure in CANNOT_HAVE_VALUE_KIND:
            raise ShadoxForbiddenException('This structure cannot have value kind')
        return self.value_kinds._create_value_kind(name)

    def delete_value_kind(self, name):
        """alternative : del parameter.value_kinds[name]"""
        return self.value_kinds._delete_value_kind(name)

    def reorder_value_kinds(self, ordered_vk_names):
        self._assert_editable() # cannot check in value_kinds, as vk._reorder() is used when refreshing
        return self.value_kinds._reorder_value_kinds(ordered_vk_names)

    def as_allowed_reference(self, value_kind, column = None):
        """Returns a dictionary that can be used for validating values :
        other_parameter.definition.content_type.allowed_reference = parameter.as_allowed_reference('vk_max')
        """
        if self.definition.structure == 'dataframe' and not column:
            raise ShadoxForbiddenException('Column must be given for dataframe parameters')

        self.value_kinds[value_kind] # will raise if value_kind does not exist

        return {
            'parameterId': self.id,
            'valueKind': value_kind,
            'columnId': None if not column else next((col.id for col in self.definition.columns if col.title == column)),
        }

    def _has_definition_changed(self):
        return self._def_hash != self.definition.get_hash()

    def _build_fields_update(self):
        if not self._touched_attributes:
            return None
        update = {}
        for k in self._touched_attributes:
            update[k] = getattr(self, k)
        return update

    def build_update(self):
        """Build an array of values to update"""
        values = []
        if self.deleted:
            raise ValueError('Parameter should be in deleted parameters list : {}'.format(self))

        hash_values = self.value_kinds._hash_values
        files_hash = self.value_kinds._files_hashes

        data_updates = {}
        comment_updates = {}
        if not self.is_imported:
            # If origin was set, ignore all modifications on values
            for key in self.value_kinds._touched_keys:
                vk = getattr(self.value_kinds, key)
                value = vk.value
                current_value_hash = hash_parameter_value(self, value)
                needs_update = False
                hashes = files_hash[key] if key in files_hash else dict()
                # If the hash is different, we build the update
                if current_value_hash != hash_values[key]:
                    needs_update = True
                    value = self._build_value_update(key, value, hashes)

                # If hash values are the same but the parameter has file and file has changed
                if not needs_update and current_value_hash == hash_values[key] and self.has_file:
                    if has_files_hash_diff(self, value, hashes):
                        needs_update = True
                        value = self._build_value_update(key, value, hashes)

                if needs_update:
                    data_updates[key] = value

            for key in self.value_kinds._touched_comments:
                vk = getattr(self.value_kinds, key)
                comment_updates[key] = vk.comment

        for key in self.value_kinds._authorized_keys:
            update = dict(
                id=self.id,
                value=key,
            )
            should_push = False
            if key in data_updates:
                should_push = True
                update['data'] = data_updates[key]
            if key in comment_updates:
                should_push = True
                update['comment'] = comment_updates[key]
            if should_push:
                values.append(update)

        should_update_meta = False
        meta_update = {'id': self.id, 'definition': self.definition._to_dict(), 'tags': self.tags}
        fields_update = self._build_fields_update()
        if fields_update:
            should_update_meta = True
            meta_update.update(fields_update)
        if self._has_definition_changed():
            should_update_meta = True
        if self.value_kinds._is_structure_modified:
            should_update_meta = True
            meta_update['values'] = [{'name': vk.name} for vk in self.value_kinds]

        return {'values': values, 'fields': meta_update if should_update_meta else None, 'origin': self._updated_origin,'deleted': False}

    def _build_value_update(self, value_key, value, hashes):
        log_action(
            u"|--- Build parameter update for '{}' key '{}'".format(self.name, value_key),
            4,
            self.client.loggerLevel
        )

        # Check the value match shadox type
        check_python_value_match_shadox(self, value, value_key)

        # Format value before sending to shadox
        return format_value_before_update(self, value, hashes, value_key)

    def is_dirty(self):
        """Indicates if a push is required to send updated data to the server"""
        if self._is_in_creation:
            return True
        if self.deleted:  # Once deletion is pushed, parameter will not be referenced by client/snapshot anymore
            return True
        if self._updated_origin:
            return True
        if self._touched_attributes or self._has_definition_changed(): # if parameter metadata or definition has been modified
            return True
        if self.value_kinds._is_structure_modified: # if value kinds were created/deleted/renamed
            return True
        if self.value_kinds._touched_comments: # if comments were modified
            return True

        hash_values = self.value_kinds._hash_values
        values = self.value_kinds._values
        files_hash = self.value_kinds._files_hashes
        for key in self.value_kinds._touched_keys:
            value = values[key].value
            # Check the hash has changed
            if hash_parameter_value(self, value) != hash_values[key]:
                return True

            # If the parameter contains file
            if self.has_file:
                # Download files hash if necessary
                self.download_files_hash(key)

                # Check hashes present
                if has_files_hash_diff(self, value, files_hash[key]):
                    return True

        return False

    def reset_dirty_state(self):
        """Refresh hashes in parameter's values, it should be called after a push"""
        new_hash_value = {}
        values = self.value_kinds._values
        for key in self.value_kinds._touched_keys:
            value = values[key].value
            new_hash_value[key] = hash_parameter_value(self, value)
            self.download_files_hash(key, True)
        self.value_kinds._hash_values = new_hash_value

        self.value_kinds._touched_comments.clear()

    def request_integrity(self, mode=1, benchmark=False, debug=False):
        """Get the hashes of all the value kinds of this parameter from the SHADOX server

        Args:
            mode (1 or 2 or 3, optional): Hash algorithm to use. Defaults to 1.
                1: Only the raw value
                2: Value and unit(s)
                3: Value, unit(s) and name of the value kind
            benchmark (bool, optional): If True, server will collect timings
            debug (bool, optional): No effect if benchmark is not True.
                If True, additional details in timings

        Returns:
            IntegrityReport: Requested hashes and additional information
        """
        return self.client.request_integrity(parameters=[self], mode=mode, benchmark=benchmark, debug=debug)

    def download_files_hash(self, value_key, force_refresh=False):
        """Download the file hashes for a value key if the parameter has not yet"""
        files_hash = self.value_kinds._files_hashes
        if (value_key not in files_hash or force_refresh) and self.has_file:
            hashes = self.client.get_parameter_file_hashes(self.id, value_key)
            files_hash[value_key] = hashes
            self.value_kinds._files_hashes = files_hash

        return files_hash[value_key] if value_key in files_hash else dict()

    def __repr__(self):
        return u"\nid:'{}' name:'{}' path:'{}' alias:'{} tags:'{}' export:'{}'{}\n".format(self.id, self._name, self._path, self._alias, self._tags, self._export, " DELETED" if self.deleted else "")

    def get_group(self):
        gname, _ = split_member_name(self.name)
        if not gname:
            return None
        return self.snapshot.search_group_by_path(self.path + gname)

    def get_unit(self):
        if hasattr(self.definition, 'content_type'):
            if hasattr(self.definition.content_type, 'formatted_unit'):
                return self.definition.content_type.formatted_unit
            elif hasattr(self.definition.content_type, 'raw_unit'):
                return self.definition.content_type.raw_unit
            else:
                return ""

    def get_structure(self):
        return self.definition.structure

    def get_type(self):
        return self.definition.content_type.type

    def get_description(self):
        return self.definition.description

    def get_df_columns_titles(self):
        if hasattr(self.definition, 'columns'):
            titles = []
            for column in self.definition.columns:
                titles.append(column.title)
            return titles

    def get_df_columns_units(self):
        if hasattr(self.definition, 'columns'):
            units = []
            for column in self.definition.columns:
                if hasattr(column.content_type, 'formatted_unit'):
                    units.append(column.content_type.formatted_unit)
                elif hasattr(column.content_type, 'raw_unit'):
                    units.append(column.content_type.raw_unit)
                else:
                    units.append("-")
            return units

    def get_df_columns_types(self):
        if hasattr(self.definition, 'columns'):
            types = []
            for column in self.definition.columns:
                types.append(column.content_type.type)
            return types
