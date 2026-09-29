#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.
from collections import defaultdict

from iced.shadox.v2.responses import ApiParametersUpdateResponse
from .exceptions import ShadoxForbiddenException
from .utils import validate_path_param

from .parameter import Parameter
from .group import Group
from .utils import log_action


class Snapshot(object):
    def __init__(self, shadox_client, json_data):
        self.id = json_data['id']
        self.branch = json_data['branch']
        self.variant = json_data['variantName']
        self.workingCopy = json_data['workingCopy']
        self.version = json_data['version']
        self.description = json_data.get('description', '')
        self.parameters = [Parameter(shadox_client, x, self) for x in json_data['parameters']]
        self.groups = [Group(shadox_client, x) for x in json_data['groups']]
        self._tree = self._build_tree(json_data['tree'])
        self._client = shadox_client
        self._dirty_tree = False
        self._deleted_parameters = []

    def __repr__(self):
        return 'Snapshot[variant={}, nb_parameters={}, id={}, workingCopy={}]'.format(self.variant, len(self.parameters), self.id, self.workingCopy)

    @property
    def tree(self):
        return self._tree

    @tree.setter
    def tree(self, new_tree):
        if '/' not in new_tree:
            new_tree.append('/')
        for path in new_tree:
            validate_path_param(path)

        for param in self.parameters:
            if param.path not in new_tree:
                validate_path_param(param.path)
                new_tree.append(param.path)
        self._tree = new_tree
        self._tree = self._check_missing_folders()
        self._tree.sort()
        self._dirty_tree = True

    def create_parameter(self, path, name, structure, content_type='double', dataframe_columns=[('Untitled', 'double')], id=None):
        """
        :param path: parameter path, '/' for root
        :param name: path + name is unique in a dataset
        :param structure: 'scalar', 'vector', 'matrix', 'dataframe', 'document' or 'notebook'
        :param content_type: for scalar/vector/matrix, optional. A string (default: 'double')
        :param dataframe_columns: for dataframe only, optional. A list of tuples, each one containing two elements ('column_name', 'type_of_content')
        Ex: [('Name', 'string'), ('Mass', 'double')]
        :param id: optional
        :return: the newly created parameter
        """
        structure = structure.replace(' ', '').lower() # Normalize the name to lower case without space : "Data frame" => "dataframe"
        p = Parameter._new_parameter(self._client, self, path, name, structure, content_type, dataframe_columns, id)
        self.parameters.append(p)
        if path not in self.tree:
            self.add_folder(path)
        return p

    def create_group(self, path, name, members, id=None):
        g = Group._new_group(self._client, path, name, members, id)
        self.groups.append(g)
        return g

    def search_parameter_by_id(self, param_id):
        for p in self.parameters:
            if p.id == param_id:
                return p

        return None

    def search_parameter_by_alias(self, param_alias):
        for p in self.parameters:
            if param_alias in p.alias:
                return p

        return None

    def search_parameter_by_path(self, param_path):
        for p in self.parameters:
            if p.path + p.name == param_path:
                return p

        return None

    def get_parameters_by_alias(self):
        """
        :return: dictionary alias -> parameter
        """
        return {alias:p for p in self.parameters if p.alias for alias in p.alias}

    def get_parameters_by_path(self):
        """
        :return: dictionary path -> parameter
        """
        return {p.path + p.name:p for p in self.parameters}

    def get_all_parameters_by_name(self):
        """
        :return: dictionary name -> list(parameter)
        """
        d = defaultdict(list)
        for p in self.parameters:
            d[p.name].append(p)
        return d

    def search_group_by_id(self, group_id):
        for g in self.groups:
            if g.id == group_id:
                return g
        return None

    def search_group_by_alias(self, group_alias):
        for g in self.groups:
            if group_alias in g.alias:
                return g
        return None


    def search_group_by_path(self, group_path):
        for g in self.groups:
            if g.path + g.name == group_path:
                return g
        return None

    def search_group_values_by_id(self, group_id):
        for g in self.groups:
            if g.id == group_id:
                group = {'Group: ': g}
                parameters_group = []
                for p in self.parameters:
                    if p.id in g.members:
                        parameters_group.append({"Name: ":p.name,"Values: ":p.values})
                group['Members: '] = parameters_group
                return group
        return None

    def search_group_values_by_alias(self, group_alias):
        for g in self.groups:
            if group_alias in g.alias:
                group = {'Group: ' : g }
                parameters_group = []
                for p in self.parameters:
                    if p.id in g.members:
                        parameters_group.append({"Name: ":p.name,"Values: ":p.values})
                group['Members: '] = parameters_group
                return group
        return None

    def search_group_values_by_path(self, group_path):
        for g in self.groups:
            if g.path + g.name == group_path:
                group = {'Group: ': g}
                parameters_group = []
                for p in self.parameters:
                    if p.id in g.members:
                        parameters_group.append({"Name: ": p.name, "Values: ": p.values})
                group['Members: '] = parameters_group
                return group
        return None

    def _build_tree(self, tree):
        """tree holds the directory structure of the Snapshot.
        """
        if '/' not in tree: # json_data doesn't yet contain the root '/'
            tree.insert(0, '/')
        return tree

    def pretty_ls(self):
        affiche = ""
        in_group = []
        if len(self.groups) != 0:
            affiche+="Groups: \n"
            for g in self.groups:
                affiche+="\t" + g.name
                if len(g.alias) != 0 :
                    affiche += " - alias: "
                for a in g.alias :
                    affiche += a + " "
                affiche+= " - path: " + g.path + "\n"
                for p in self.parameters:
                    if p.id in g.members:
                        affiche+="\t\t" + p.name[len(g.name)+2:] + "\n"
                        in_group.append(p)
        affiche+="\nParameters: \n"
        for p in self.parameters:
            if p not in in_group :
                affiche+="\t" + p.name
                if len(p.alias) != 0 :
                    affiche += " - alias: "
                for a in p.alias :
                    affiche += a + " "
                affiche += " - path: " + p.path + "\n"
        return affiche

    def refresh_with_updates(self, update_response):
        self.version = update_response.new_version
        self._refresh_tree(update_response.tree)
        new_groups = update_response.groups
        updated_parameters = update_response.updated_parameters
        deleted_parameter_ids = update_response.deleted_parameter_ids

        groups = [Group(None, g) for g in new_groups]
        groups_to_remove = []
        # Update and remove groups
        for i, group in enumerate(self.groups):
            refreshed_group = next((g for g in groups if g.id == group.id), None)
            if refreshed_group:
                group.refresh_with(refreshed_group)
            else:
                groups_to_remove.append(i)
        [self.groups.pop(i) for i in reversed(groups_to_remove)]

        updated_params = []
        params_to_remove = []
        # Update and remove parameters (ignore the ones not present in json_data)
        for i, param in enumerate(self.parameters):
            updated_param = next((p for p in updated_parameters if p.id == param.id), None)
            if updated_param:
                param.refresh_with(updated_param)
                updated_params.append(param)
            elif param.id in deleted_parameter_ids:
                params_to_remove.append(i)
        deleted_params = [self.parameters.pop(i) for i in reversed(params_to_remove)]

        # Pretty print the updates
        if deleted_params:
            log_action(u"|- Deleted parameters", 0, self._client.loggerLevel)
            columns = ['Parameter id', 'Parameter aliases']
            row_format = u"{:<40} " * len(columns)
            log_action(row_format.format(*columns), 0, self._client.loggerLevel)
            for p in deleted_params:
                log_action(row_format.format(p.id, ','.join(p.alias)), 0, self._client.loggerLevel)

        if updated_params:
            log_action(u"|- Updated parameters", 0, self._client.loggerLevel)
            columns = ['Parameter id', 'Parameter name', 'Parameter alias']
            row_format = u"{:<40} " * len(columns)
            log_action(row_format.format(*columns), 0, self._client.loggerLevel)
            for p in updated_params:
                log_action(row_format.format(p.id, p.name, ','.join(p.alias)), 0, self._client.loggerLevel)



    def refresh_with(self, refreshed_snapshot):
        self.id = refreshed_snapshot.id
        self.branch = refreshed_snapshot.branch
        self.variant = refreshed_snapshot.variant
        self.workingCopy = refreshed_snapshot.workingCopy
        self.version = refreshed_snapshot.version
        self.description = refreshed_snapshot.description
        self._refresh_tree(refreshed_snapshot.tree)

        # Add new parameters
        for p in refreshed_snapshot.parameters:
            if not self.search_parameter_by_id(p.id):
                self.parameters.append(p)

        indexes_to_remove = []

        # Update and remove current parameters
        for i, p in enumerate(self.parameters):
            # Search the refresh parameter
            refreshed_parameter = refreshed_snapshot.search_parameter_by_id(p.id)
            # If we found it, refresh fields
            if refreshed_parameter:
                p.refresh_with(refreshed_parameter)
            else:
                indexes_to_remove.append(i)

        # Remove now the parameters not found (reverse to avoid indexes to change)
        if len(indexes_to_remove) > 0:
            for i in reversed(indexes_to_remove):
                self.parameters.pop(i)


        # Add new groupss
        for g in refreshed_snapshot.groups:
            if not self.search_group_by_id(g.id):
                self.groups.append(g)

        indexes_to_remove = []

        # Update and remove current groups
        for i, g in enumerate(self.groups):
            # Search the refresh group
            refreshed_group = refreshed_snapshot.search_group_by_id(g.id)
            # If we found it, refresh fields
            if refreshed_group:
                g.refresh_with(refreshed_group['Group: '])
            else:
                indexes_to_remove.append(i)

        # Remove now the parameters not found (reverse to avoid indexes to change)
        if len(indexes_to_remove) > 0:
            for i in reversed(indexes_to_remove):
                self.groups.pop(i)

    def rename_folder(self, old_path, new_path):
        """ Rename a folder in the tree and move all of its parameters.

        Args:
            old_path (str): name of the folder to rename
            new_path (str): new name of the folder

        Raises:
            ValueError: if the folder does not exist
            ShadoxForbiddenException: if the folder is the root folder
        """
        validate_path_param(new_path)
        if old_path not in self.tree:
            raise ValueError("Folder {} does not exist in tree.".format(old_path))
        if old_path == '/':
            raise ShadoxForbiddenException("Cannot rename root folder.")

        copy_tree = list(self.tree)

        for path in copy_tree:
            if path.startswith(old_path):
                self.tree[self.tree.index(path)] = path.replace(old_path, new_path)

                for p in self.parameters:
                    if p.path == path:
                        p.path = path.replace(old_path, new_path)
                for g in self.groups:
                    if g.path == path:
                        g.path = path.replace(old_path, new_path)
                self._dirty_tree = True

        self.tree = self._check_missing_folders()

    def add_folder(self, path):
        """ Add a folder to the tree.

        Args:
            path (str): name of the folder to add
        """
        validate_path_param(path)
        if path not in self.tree:
            self.tree.append(path)
            self._dirty_tree = True
            self.tree = self._check_missing_folders()

    def delete_folder(self, path, force=False):
        """ Delete a folder from the tree.

        Args:
            path (str): name of the folder to delete
            force (bool, optional): if True, delete all parameters and groups in the folder. Defaults to False.

        Raises:
            ShadoxForbiddenException: if the folder is the root folder or if the tree is empty
            ShadoxForbiddenException: if the folder contains parameters or groups and force is False
            ValueError: if the folder does not exist
        """
        validate_path_param(path)
        if path == '/':
            raise ShadoxForbiddenException("Cannot delete root folder.")
        elif self.tree is None or self.tree == ['/']:
            raise ShadoxForbiddenException("Cannot delete folder. Tree is empty.")
        elif path not in self.tree:
            raise ValueError("Folder {} does not exist in tree.".format(path))

        deleted_params, deleted_groups = [], []
        for p in self.parameters:
            if p.path.startswith(path):
                deleted_params.append(p)
        for g in self.groups:
            if g.path.startswith(path):
                deleted_groups.append(g)

        if not force and (deleted_groups or deleted_params):
            raise ShadoxForbiddenException("Cannot delete folder '" + path + "' because it contains parameters")

        for p in deleted_params:
            p.delete()
        for g in deleted_groups:
            g.delete()

        copy_tree = list(self.tree)
        for sub_path in self.tree:
            if sub_path.startswith(path):
                copy_tree.remove(sub_path)
        self.tree = copy_tree
        self._dirty_tree = True

    def _refresh_tree(self, new_tree):
        self.tree = self._build_tree(new_tree)
        self._dirty_tree = False

    def _check_missing_folders(self):
        missing = []
        for folder in self.tree:
            splits = folder.split('/')
            for i in range(1, len(splits) + 1):
                subfolder = '/'.join(splits[:i])
                subfolder = subfolder + '/' if not subfolder.endswith('/') else subfolder
                if subfolder not in self.tree:
                    missing.append(subfolder)
        return self.tree + missing
