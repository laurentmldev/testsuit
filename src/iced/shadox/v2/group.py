#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from .exceptions import ShadoxUnauthorizedException, ShadoxForbiddenException
from .parameter.parameter_origin import ParameterOrigin, build_new_origin
from .utils import (
    log_action,
    add_list_items,
    delete_list_items,
    validate_alias,
    validate_param_name,
    validate_path_param,
    build_member_aliases,
    build_member_name,
    split_member_name,
    check_tags_is_in_list_project_tags,
)

def _validate_members(path, all_groups, members):
    mismatch_path = next((p for p in members if p.path != path), None)
    if mismatch_path:
        raise ShadoxForbiddenException(u'did not expect parameter with path {}'.format(mismatch_path))

    for g in all_groups:
        already_grouped = next((p for p in members if p in g.parameters), None)
        if already_grouped:
            raise ShadoxForbiddenException(u'parameter is already in a group')

    # If member is already in group, name contains illegal character
    [validate_alias(p.name) for p in members] # TODO:explicit error with "member name"

def _validate_size_change(current_size, delta):
    new_size = current_size + delta
    if new_size < 2:
        raise ShadoxForbiddenException(u'group should always have at least two members, but would end up with {}'.format(new_size))



class Group(object):
    """
    Group with at least two SHADOX parameters as members.
    The members of a group must be exported/imported (w.r.t dependencies and publications) together.
    """

    def __init__(self, shadox_client, json_data):
        self._id = json_data['id']
        self._name = json_data['name']
        self._path = json_data.get('path', '/')
        self._alias = json_data.get('alias', [])
        self._client = shadox_client
        self._export = json_data.get('export', False)
        self._is_imported = json_data.get('import', False)
        self._last_modified = json_data.get('lastModified', '')
        self._member_ids = json_data.get('parameterIds',[])
        self._origin = ParameterOrigin(json_data.get('origin')) if json_data.get('origin', None) else None
        self._is_in_creation = False
        self._is_modified = False
        self._is_deleted = False
        self._updated_origin = None

    def refresh_with(self, new_group):
        self._id = new_group.id
        self._name = new_group.name
        self._path = new_group.path
        self._alias = new_group.alias
        self._export = new_group._export
        self._is_imported = new_group.is_imported
        self._last_modified = new_group.last_modified
        self._member_ids = new_group._member_ids
        self._origin = new_group._origin
        self._is_modified = False
        self._updated_origin = None

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'path': self.path,
            'alias': self.alias,
            'export': self.is_export,
            'import': self.is_imported,
            'parameterIds': [] if self._is_deleted else self.members, # Empty member list == delete group
        }

    @classmethod
    def _new_group(cls, shadox_client, path, name, members, id=None):
        validate_param_name(name)
        validate_path_param(path)
        _validate_size_change(0, len(members))
        _validate_members(path, shadox_client.get_groups(), members)

        json_data = {
            'id': id,
            'name': name,
            'path': path,
            'alias': [],
            'export': True,
            'import': False,
            'lastModified': None,
            'parameterIds': [p.id for p in members],
        }
        g = Group(shadox_client, json_data)
        g._is_in_creation = True
        eg = shadox_client.snapshot.search_group_by_path(g.path + g.name)
        if eg:
            raise ShadoxForbiddenException('There is already a group with this name and path : {}'.format(eg))
        eg = shadox_client.snapshot.search_group_by_id(id)
        if eg:
            raise ShadoxForbiddenException('There is already a group with this id : {}'.format(eg))
        g._update_members_inherited_properties()
        return g

    def __getitem__(self, key):
        """
        Retrocompatibility for methods snapshot.search_group_by_*()
        """
        if key == 'Group: ':
            return self
        elif key == 'Members: ':
            return self.parameters
        else:
            raise TypeError("'Group' object is not subscriptable")

    @property
    def is_dirty(self):
        return self._is_modified

    @property
    def id(self):
        return self._id

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, name):
        self._assert_editable()
        self._name = name
        self._is_modified = True
        self._update_members_inherited_properties()

    @property
    def path(self):
        return self._path

    @path.setter
    def path(self, path):
        self._assert_editable()
        self._path = path
        self._is_modified = True
        self._update_members_inherited_properties()

    @property
    def alias(self):
        return self._alias

    @alias.setter
    def alias(self, alias):
        self.delete_aliases()
        self.add_aliases(alias)

    def add_aliases(self, list_alias):
        self._assert_editable()
        alias, changed_aliases = add_list_items(self._alias, list_alias, lambda aliases: [validate_alias(a) for a in aliases])
        if changed_aliases:
            self._check_availability_aliases(changed_aliases)
            self._alias = alias
            self._is_modified = True
            self._update_members_inherited_properties()

    def delete_aliases(self, list_alias=None):
        self._assert_editable()
        self._alias = delete_list_items(self._alias, list_alias)
        self._is_modified = True
        self._update_members_inherited_properties()

    def _update_members_inherited_properties(self):
        if self._is_deleted:
            for m in self.parameters:
                m._name = build_member_name(None, split_member_name(m._name)[1])
                m._alias = []
                m._origin = None
            return
        path = self.path
        gname = self.name
        aliases = self.alias
        export = self.is_export
        origin = self.origin
        is_imported = self.is_imported
        for m in self.parameters:
            m._path = path
            m._name = build_member_name(gname, split_member_name(m._name)[1])
            m._alias = build_member_aliases(aliases, m._name)
            m._export = export
            m._is_imported = is_imported
            if m._origin and origin:
                if (m._origin.dataset_path != origin.dataset_path
                    or m._origin.publication_variant != origin.publication_variant
                    or m._origin.publication_version != origin.publication_version):
                    # the origin is different, it must have been changed on the group
                    # it does not matter that members&group share the same ParameterOrigin
                    # as it will be overwritten on push()
                    m._origin = origin
            elif m._origin:
                m._origin = None
            elif origin:
                m._origin = origin

    def _check_availability_aliases(self, aliases):
        for alias in aliases:
            p = self._client.snapshot.search_parameter_by_alias(alias)
            if p:
                raise ShadoxForbiddenException('There is already a parameter with alias ' + alias + ' : ' + str(p))
            g = self._client.snapshot.search_group_by_alias(alias)
            if g:
                raise ShadoxForbiddenException('There is already a group with alias ' + alias + ' : ' + str(g))

    @property
    def is_export(self):
        return self._export

    @is_export.setter
    def is_export(self, is_export):
        self._assert_editable()
        self._export = is_export
        self._is_modified = True
        self._update_members_inherited_properties()

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
    def origin(self):
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
        else:
            raise TypeError('expected a ParameterOrigin')

    def change_origin(self, dataset_path, publication_variant = None, publication_version = None, origin_group_id = None, origin_group_alias = None):
        """
        Creates, modifies or deletes the dependency this parameter has on a parameter of another dataset.
        """
        if dataset_path is None and self.origin is None:
            return

        self._updated_origin, self._origin = build_new_origin(self._id, dataset_path, publication_variant, publication_version, origin_group_id, origin_group_alias)
        self._is_imported = bool(self._origin)
        self._update_members_inherited_properties()

    @property
    def last_modified(self):
        return self._last_modified

    @property
    def members(self):
        return list(self._member_ids)

    @property
    def parameters(self):
        return [self._client.get_parameter('id:' + member_id) for member_id in self._member_ids]

    def add_members(self, members):
        self._assert_editable()
        try:
            len(members)
        except TypeError:
            members = [members]

        _validate_members(self.path, self._client.get_groups(), members)
        current_members = self.parameters
        for m in members:
            if m in current_members:
                raise ShadoxForbiddenException('group {} already contains parameter {}'.format(self, m))

        self._member_ids.extend(m.id for m in members)
        self._is_modified = True
        self._update_members_inherited_properties()

    def remove_members(self, members=None):
        self._assert_editable()
        if members is None:
            members = list(self.parameters)
        try:
            len(members)
        except TypeError:
            members = [members]

        _validate_size_change(len(self.members), -len(members))
        current_members = self.parameters
        for m in members:
            if m not in current_members:
                raise ShadoxForbiddenException('group {} does not contain parameter {}'.format(self, m))

        [self._member_ids.remove(m.id) for m in members]
        self._is_modified = True
        self._update_members_inherited_properties()

    def _assert_editable(self):
        if self._is_deleted:
            raise ShadoxForbiddenException('group {} is marked for deletion and can no longer be edited'.format(self))

    def delete(self):
        if self._is_deleted:
            return False
        self._assert_editable()
        self._is_in_creation = False
        self._is_deleted = True
        self._is_modified = True
        self._update_members_inherited_properties()
        del self._member_ids[:] # == member_ids.clear()


    def __repr__(self):
        return u"\nid:'{}' name:'{}' path:'{}' alias:'{}' members:{}\n".format(self.id, self.name, self.path, self.alias, len(self.members))




