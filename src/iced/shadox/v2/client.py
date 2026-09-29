#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

import getpass
import re
import os
import tempfile
import json
import time
import hashlib
from multiprocessing.dummy import Pool as ThreadPool
from six import string_types

from functools import wraps

from .exceptions import (
    ShadoxForbiddenException,
    ShadoxNotFoundException,
    ShadoxParameterNotFoundException,
    ShadoxParameterValueNotFoundException,
    ShadoxInternalException,
    ShadoxNoSnapshotException,
)
from .responses import ApiDatasetSnapshotUpdate, ApiParametersUpdateResponse, BatchDependenciesUpdateReport, ImportLog, SymbolicPointerPath, DatasetInformation, ProjectInformation, IntegrityReport, IntegrityInternalReport
from .snapshot import Snapshot, Parameter
from .publication import ShadoxPublication

from .http_helper import ShadoxHTTPHelper

from .parameter import Parameter
from .group import Group
from .utils import log_action, build_member_name
from ..version import __version__


class ShadoxClient(object):
    """
        Python Client wrapper for Shadox API v1
    """
    __version__ = __version__

    def __init__(
            self,
            api_url,
            api_key=None,
            project_key=None,
            dataset_path=None,
            selector_type=None,
            selector_value=None,
            agent_name="Python Shadox API Wrapper version {}".format(
                __version__),
            dataset_urn=None,
            dataset=None,
            snapshot=None,
            logger_level=0,
            use_version=False,
            certificates_path='/etc/ssl/certs/shadox/',
            # Requests module proxies dict: proxies = {'http': 'http_proxy', 'https': 'https_proxy'}
            proxies=None,
            api_keys_file_path=None,
            api_keys_folder_path=None,
            sigdig_mode=None
    ):

        self.loggerLevel = logger_level
        log_action(u"### Init Shadox Client version {}".format(
            __version__), 4, self.loggerLevel)
        log_action(u"### Init client variables", 4, self.loggerLevel)
        self.use_version = use_version
        self.connected = False
        self.snapshot_path = None
        self.dataset_path = None
        self.dataset_name = None
        self.snapshot = None
        self._symbolic_pointer_paths = None
        self.publication = None
        self.variants = None
        self.project_tags = None
        self.api_key = None
        self._sigdig_mode = None
        self.sigdig_mode = sigdig_mode

        self.new_variants = []

        # Keep dataset_urn has compatibility for notebook/jupyter
        if dataset_urn:
            self.snapshot_path = dataset_urn
        # Build snapshot path from single variables if given
        elif project_key and dataset_path and selector_type and selector_value:
            self.snapshot_path = "project/{}/dataset/{}/{}/{}".format(project_key,
                                                                      dataset_path,
                                                                      selector_type,
                                                                      selector_value)
        # Build dataset path from single variables if given
        elif project_key and dataset_path:
            self.dataset_path = "project/{}/dataset/{}".format(
                project_key, dataset_path)

        # Build snapshot path
        if snapshot:
            self.snapshot_path = snapshot

        # Build dataset path from snapshot path if dataset not given
        if self.snapshot_path:
            if self.snapshot_path[0] == '/':
                self.snapshot_path = self.snapshot_path[1:]
            # Build the dataset_path from the url
            if not self.dataset_path:
                self.dataset_path = '/'.join(self.snapshot_path.split('/')[:4])

        # Build dataset
        if dataset and not self.dataset_path:
            self.dataset_path = dataset

        if api_key:
            self.api_key = api_key
        else:
            matches = re.findall(
                'project/(.+)/dataset/(.+)@(.+)', self.dataset_path)
            api_project_key = matches[0][0]
            api_dataset_name = matches[0][1]
            api_instance = matches[0][2]
            if api_keys_file_path:
                self.api_keys_file_path = api_keys_file_path
            else:
                api_keys_filename = "shadox-api-keys-{}@{}.json".format(api_project_key,
                                                                        api_instance)
                if api_keys_folder_path:
                    self.api_keys_file_path = os.path.join(
                        api_keys_folder_path, api_keys_filename)
                else:
                    home = os.path.expanduser('~')
                    self.api_keys_file_path = os.path.join(
                        home, api_keys_filename)

            if os.path.exists(self.api_keys_file_path):
                with open(self.api_keys_file_path) as api_keys_file_json:
                    api_keys_file_list = json.load(api_keys_file_json)
                    dataset_name = api_dataset_name + '@' + api_instance
                    for api_key_object in api_keys_file_list:
                        if api_key_object.get('datasetName') == dataset_name:
                            self.api_key = api_key_object.get('apiKey')
                            break

        self.http_helper = ShadoxHTTPHelper(
            api_url,
            self.api_key,
            agent_name,
            logger_level,
            certificates_path,
            proxies,
        )

        if self.dataset_path:
            self.variants = self.fetch_variants_with_publication()
            matches = re.findall('project/.+/dataset/(.+)', self.dataset_path)
            self.dataset_name = matches[0]
        if self.snapshot_path:
            self.snapshot = self.fetch_snapshot()

    def once_connected(method):
        """ decorator which enforces state: connected = True
        :return: wrapped method
        """
        @wraps(method)
        def wrapper(*args, **kwargs):
            """
                wrapper which enforces state: connected = True
                :throws: ShadoxNoSnapshotException
            """
            if args[0].connected:
                return method(*args, **kwargs)
            else:
                raise ShadoxNoSnapshotException(
                    "Client is not connected to a snapshot")
        return wrapper

    @property
    @once_connected
    def uri_config(self):
        if self._symbolic_pointer_paths is None:
            try:
                json_data = self.http_helper.get(
                    self.dataset_path + '/projectSymbolicPointerPaths')
                self._symbolic_pointer_paths = {
                    spp["id"]: SymbolicPointerPath(spp) for spp in json_data}
            except:
                # If the server does not anwser, default to no symbolic pointer path.
                self._symbolic_pointer_paths = {}
        return self._symbolic_pointer_paths

    @once_connected
    def get_parameters(self):
        """
        Return the list of parameters for the snapshot
        :return [Parameter]
        """
        return self.snapshot.parameters

    @once_connected
    def get_groups(self):
        """
        Return the list of groups for the snapshot
        :return [Group]
        """
        return self.snapshot.groups

    @once_connected
    def ls(self):
        """
        Return the list of groups and parameters for the snapshot
        :return [Group]
        """
        return self.snapshot.pretty_ls()

    def get_information(self):
        """Returns DatasetInformation containing general information about the dataset"""
        if not hasattr(self, '_informations'):
            json_data = self.http_helper.get(self.dataset_path + '/informations')
            self._informations = DatasetInformation(json_data)
        return self._informations

    def get_variants(self):
        """Get a list of the dataset variants"""
        if not self.variants:
            raise ShadoxNotFoundException("Client has no dataset initialized")

        return list(self.variants.keys())

    def get_publications(self, variant=None):
        """Get a list of all delivered publication in the dataset"""
        if not self.variants:
            raise ShadoxNotFoundException("Client has no dataset initialized")

        if variant:
            if variant not in self.variants:
                raise ShadoxNotFoundException(
                    "Variant can't be found in dataset")
            return self.variants[variant]

        return [publication for variant_publications in self.variants.values() for publication in variant_publications]

    def get_publications_obj(self, variant=None):
        return [publication['_obj'] if '_obj' in publication else ShadoxPublication(self, publication, self.http_helper.api_url) for publication in self.get_publications(variant)]

    @once_connected
    def get_parameter(self, search=''):
        """
        Search for a parameter by id "id:{id}" or by alias "alias:{alias}" or by path "path:{path}"
        :return Parameter
        """
        log_action(u"|- Search parameter '{}'".format(search),
                   2, self.loggerLevel)

        fullSearch = search
        parameter = None
        if re.match("^id:", search):
            # Search by id
            search = search[3:]
            log_action(u"|--- Search by id '{}'".format(search),
                       3, self.loggerLevel)
            parameter = self.snapshot.search_parameter_by_id(search)
        elif re.match("^alias:", search):
            # Search by alias
            search = search[6:]
            log_action(u"|--- Search by alias '{}'".format(search),
                       3, self.loggerLevel)
            parameter = self.snapshot.search_parameter_by_alias(search)
        elif re.match("^path:", search):  # stays the same if it is a member of a group or not
            # Search by path
            search = search[5:]
            log_action(u"|--- Search by path '{}'".format(search),
                       3, self.loggerLevel)
            parameter = self.snapshot.search_parameter_by_path(search)

        if not parameter:
            raise ShadoxParameterNotFoundException(
                "Parameter '" + fullSearch + "' not found")

        return parameter

    @once_connected
    def get_group(self, search=''):
        """
        Search for a group by id "id:{id}" or by alias "alias:{alias}" or by path "path:{path}"
        :return Group
        """
        log_action(u"|- Search group '{}'".format(search), 2, self.loggerLevel)

        group = None
        if re.match("^id:", search):
            # Search by id
            search = search[3:]
            log_action(u"|--- Search by id '{}'".format(search),
                       3, self.loggerLevel)
            group = self.snapshot.search_group_by_id(search)
        elif re.match("^alias:", search):
            # Search by alias
            search = search[6:]
            log_action(u"|--- Search by alias '{}'".format(search),
                       3, self.loggerLevel)
            group = self.snapshot.search_group_by_alias(search)

        elif re.match("^path:", search):
            # Search by path
            search = search[5:]
            log_action(u"|--- Search by path '{}'".format(search),
                       3, self.loggerLevel)
            group = self.snapshot.search_group_by_path(search)

        if not group:
            raise ShadoxParameterNotFoundException(
                "Group not found : {}".format(search))

        return group

    @once_connected
    def get_group_values(self, search=''):
        """
        Search for a group by id "id:{id}" or by alias "alias:{alias}" or by path "path:{path}"
        :return Hashmap
        """
        log_action(u"|- Search group '{}'".format(search), 2, self.loggerLevel)

        group_values = None
        if re.match("^id:", search):
            # Search by id
            search = search[3:]
            log_action(u"|--- Search by id '{}'".format(search),
                       3, self.loggerLevel)
            group_values = self.snapshot.search_group_values_by_id(search)
        elif re.match("^alias:", search):
            # Search by alias
            search = search[6:]
            log_action(u"|--- Search by alias '{}'".format(search),
                       3, self.loggerLevel)
            group_values = self.snapshot.search_group_values_by_alias(search)

        elif re.match("^path:", search):
            # Search by path
            search = search[5:]
            log_action(u"|--- Search by path '{}'".format(search),
                       3, self.loggerLevel)
            group_values = self.snapshot.search_group_values_by_path(search)

        if not group_values:
            raise ShadoxParameterNotFoundException("Group not found")

        return group_values

    def upgrade_dependencies_to_latest(self, ignore_dirty=False):
        """
        Creates a snapshot and then upgrades dependencies to latest available publications
        :return: a report of the upgrade (number of non/updated parameters & updated publications)
        :raise ShadoxForbiddenException
        """
        return self._upgrade_dependencies(ignore_dirty=ignore_dirty)

    def upgrade_dependencies_to_library(self, library_path, library_revision, ignore_dirty=False):
        """
        Creates a snapshot and then upgrades dependencies to latest available publications
        in a, non deprecated, library revision
        :param library_path: path of the library with dots as namespace separator. Ex: "folder.sub.lib@LMX"
        :param library_revision: iteration of the library. Ex: "C"
        :return: a report of the upgrade (number of non/updated parameters & updated publications)
        :raise ShadoxForbiddenException
        """
        if not library_path or not library_revision:
            raise ShadoxForbiddenException(
                "Library path and revision cannot be null.")
        library_path = library_path.replace("/", ".")
        return self._upgrade_dependencies(library_path, library_revision, ignore_dirty)

    @once_connected
    def _upgrade_dependencies(self, library_path=None, library_revision=None, ignore_dirty=False):
        """
        INTERNAL USE ONLY - upgrade dependencies to latest or to the version contained in a library revision
        :return: a report of the upgrade (number of non/updated parameters & updated publications)
        :raise ShadoxForbiddenException
        """
        if not self.snapshot.workingCopy:
            raise ShadoxForbiddenException("Cannot update a frozen snapshot.")
        if not ignore_dirty and self.is_dirty():
            raise ShadoxForbiddenException(
                "Cannot update a snapshot that has pending changes, use ignore_dirty=True to bypass")

        optional_path = "/" + library_path + "/" + \
            library_revision if library_path else ""
        url = self.snapshot_path + "/upgrade" + optional_path
        if self.use_version:
            url += '?version=' + str(self.snapshot.version)

        log_action(u"|- Upgrade dependencies", 3, self.loggerLevel)
        json_result = self.http_helper.post(url)
        update_dependencies = BatchDependenciesUpdateReport(json_result)
        report_diff = update_dependencies.diff

        library_origin = u"to their version in revision {} of {} ".format(
            library_revision, library_path) if library_path else ""
        message = \
            u"|- Successfully updated {} parameter{} from {} publication{} {}to their latest available versions.".format(
                report_diff.updated_parameters,
                "s" if report_diff.updated_parameters > 1 else "",
                report_diff.publications_updated,
                "s" if report_diff.publications_updated > 1 else "",
                library_origin
            )
        log_action(message, 0, self.loggerLevel)

        if report_diff.non_updated_parameters > 0:
            plural = "s" if report_diff.non_updated_parameters > 1 else ""
            log_action(u"|--- /!\ {} parameter{} could not be updated.".format(report_diff.non_updated_parameters, plural),
                       0, self.loggerLevel)

        # An automatic snapshot is done before trying to update dependencies, so we need to fetch the new snapshot
        self.snapshot_path = self.dataset_path + \
            '/snapshot/' + update_dependencies.new_snapshot_id
        self.refresh()

        return report_diff

    def fetch_variants_with_publication(self):
        """INTERNAL USE ONLY - Fetch a dict with variant name in key and a list of published publication associated"""
        log_action(u"|- Fetch dataset variant and publication " +
                   self.dataset_path + '/variants', 2, self.loggerLevel)
        return self.http_helper.get(self.dataset_path + '/variants')

    @property
    def project_informations(self):
        if not hasattr(self, '_project_informations') or not self._project_informations:
            log_action(u"|- Fetch project general information", 2, self.loggerLevel)
            json_data = self.http_helper.get(self.dataset_path + '/projectInfos')
            self._project_informations = ProjectInformation(json_data)
        return self._project_informations

    def add_variants_from_groups(self, selected_project_variants):
        """
        Adds new variants selected from project's variant groups to local shadox client.
        Does the cartesian product of variant groups to generate all possible variant combinations
        from the given variants and adds them to the dataset
        :param selected_project_variants: variant groups from project_informations.variant_groups
        or with the following format
        selected_project_variants = [
            {'name': group_name,
            'variants': [
                {
                    'name': variant_name
                },
                ...
                ]
            },
            ...
        ]
        """
        # get sets of variant names for each variant group
        # get total of variant combinations
        sets = []
        positions = []
        total = 1
        for group in selected_project_variants:
            sets.append([])
            _set = sets[-1]
            positions.append(0)
            total *= len(group['variants'])
            for variant in group['variants']:
                _set.append(group['name'] + ':' + variant['name'])

        variants = []

        # perform cartesian product between all sets of variants
        while total > 0:
            full_variant_name = ''
            for setIndex in range(len(positions)):
                full_variant_name += sets[setIndex][positions[setIndex]] + ','

            variants.append(full_variant_name[:-1])

            for setIndex in range(len(sets)-1, -1, -1):
                if positions[setIndex] + 1 < len(sets[setIndex]):
                    positions[setIndex] += 1
                    break
                positions[setIndex] = 0

            total -= 1

        return self.add_custom_variants(variants)

    def check_duplicate_variant(self, variant_name):
        """
        Checks if variant_name is already added locally
        :param variant_name: name of the variant to check
        :return: True if variant is already added
        :return: False otherwise
        """
        for variant in self.new_variants:
            if variant['name'] == variant_name:
                return True
        for variant in self.variants.keys():
            if variant == variant_name:
                return True
        return False

    def add_custom_variants(self, variants):
        """
        Adds new variants with the given names to the local shadox client
        :param variants: list of variant names
        """
        for variant in variants:
            if not re.search(r'^([a-zA-Z]\w*(:\w+)?,?)+$', variant):
                raise ShadoxForbiddenException(
                    'Variant name {} do not match expected format.'.format(variant) +
                    "Variant name couple must start with a letter, can only contain alphanumericals and '_', must be separated with ',' and can only contain one ':'")
        log_action(u"|- Attempting to add variants " +
                   str(variants) + " locally", 2, self.loggerLevel)
        valid_variants = self.project_informations.get_valid_variant_combinations(variants)
        for variant_name in valid_variants:
            if not self.check_duplicate_variant(variant_name):
                self.new_variants.append(
                    {'name': variant_name, 'description': ''})
        return self.new_variants

    def edit_variant_name(self, variant_name, new_name):
        """
        Changes variant name
        :param variant_name: current name of the variant to edit
        :param new_name: new name for the variant
        """
        if not re.search(r'^([a-zA-Z]\w*(:\w+)?,?)+$', new_name):
            raise ShadoxForbiddenException(
                'New variant name does not match expected format')

        if self.check_duplicate_variant(new_name):
            message = 'Variant {} already exists'.format(new_name)
            raise ShadoxForbiddenException(message)

        # Edit variant name locally if it is not already saved on the server
        for variant in self.new_variants:
            if variant['name'] == variant_name:
                log_action(u"|- Changing variant name from " + variant_name +
                           " to " + new_name + " on local client", 2, self.loggerLevel)
                variant['name'] = new_name
                return variant

        log_action(u"|- Attempting to change variant name from " +
                   variant_name + " to " + new_name + " on server", 2, self.loggerLevel)
        # Edit variant directly on the server if it is not present locally
        variant_update = {'name': new_name, 'description': None}
        self.http_helper.post(self.dataset_path + '/variant/' + variant_name +
                              '/update/information', data=variant_update, return_type='')
        self.variants[new_name] = self.variants.pop(variant_name)
        return {'name': new_name, 'description': ''}

    def edit_variant_description(self, variant_name, new_description):
        """
        Changes variant description
        :param variant_name: name of the variant to edit
        :param new_description: new description for the variant
        """
        # Edit variant description locally if it is not already saved on the server
        for variant in self.new_variants:
            if variant['name'] == variant_name:
                log_action(u"|- Updating variant description to " +
                           new_description + " on local client", 2, self.loggerLevel)
                variant['description'] = new_description
                return variant

        log_action(u"|- Attempting to update variant description to " +
                   new_description + " on server", 2, self.loggerLevel)
        # Edit variant directly on the server if it is not present locally
        variant_update = {'name': None, 'description': new_description}
        self.http_helper.post(self.dataset_path + '/variant/' + variant_name +
                              '/update/information', data=variant_update, return_type='')
        return {'name': variant_name, 'description': new_description}

    @once_connected
    def save_new_variants(self):
        """
        Saves the new variants added to the local shadox client to the dataset on the shadox server
        """
        log_action(u"|- Saving variants " + str(self.new_variants) +
                   " on dataset " + self.dataset_path, 2, self.loggerLevel)
        url = self.dataset_path + '/variant'
        res = self.http_helper.post(
            url, data=self.new_variants, return_type='')
        added_variants = []
        if res.ok:
            added_variants = self.new_variants
            self.new_variants = []
            self.variants = self.fetch_variants_with_publication()
        return added_variants

    def sync_variant_structures_prepare(self, selector=''):
        """
        Gets list of synchronizable parameters for each variant
        :param selector: variant/publication from which to synchronize structures. If selector='variant/variantName'
        selects primary snapshot from variant variantName; if selector='publication/publicationName' selects publication
        snapshot; if selector='' selects current snapshot, fails if not connected to a snapshot
        :return: Synchronization preview, including possible parameters to sync
        """
        if selector == '':
            if self.connected:
                selector_type = 'snapshot'
                selector_value = self.snapshot.id
            else:
                raise ShadoxNoSnapshotException('Client not connected to a snapshot. Please select a variant or publication')
        else:
            [selector_type, selector_value] = selector.split('/')
        sync_request = {'selectorType': selector_type, 'selectorValue': selector_value}
        sync_report = {'selectorType': selector_type, 'selectorValue': selector_value}
        reports = self.http_helper.post(self.dataset_path + '/variant/sync/structure', data=sync_request)['reports']
        filtered_reports = filter(lambda report: len(report['parameterReports']) > 0, reports)
        sync_report['reports'] = list(map(lambda report: {
            'variantName': report['variantName'],
            'parameterReports': [
                {
                    'status': param['status'],
                    'parameter': {
                        'id': param['sourceParameter']['id'],
                        'name': param['sourceParameter']['path'] + build_member_name(param['sourceParameter']['groupName'], param['sourceParameter']['name'])
                    } if param['status'] == 'CREATED'
                    else {
                        'id': param['targetParameter']['id'],
                        'name': param['targetParameter']['path'] + build_member_name(param['targetParameter']['groupName'], param['targetParameter']['name'])
                    },
                    'groupForUpgrade': param['upgradeWith']
                } for param in report['parameterReports']
            ]
        }, filtered_reports))
        return sync_report

    def sync_variant_structures_commit(self, sync_prepare, params_to_commit=None, interactive=False):
        """
        Commits structure synchronization for variants
        :param sync_prepare: Preview of synchronization changes, including possible parameters to sync
        :param params_to_commit: Dict of params to sync by variant with the following structure
        params_to_commit = {
            variant_name: [
                full_param_name,
                full_param_name,
                ...
            ], ...
        }
        :param interactive: Flag to make behavior interactive. If True, interactively asks the user if a parameter should be committed
        :return: Synchronization report
        """
        sync_request = {
            'selectorType': sync_prepare['selectorType'],
            'selectorValue': sync_prepare['selectorValue'],
            'targetVariants': [],
            'commitedParameterIdsByVariant': {}
        }
        groupsForUpgradeByVariants = {}
        for variantReport in sync_prepare['reports']:
            groups = {}
            for parameterReport in variantReport['parameterReports']:
                params = groups.get(parameterReport['groupForUpgrade'], [])
                params.append(parameterReport)
                groups[parameterReport['groupForUpgrade']] = params
            groupsForUpgradeByVariants[variantReport['variantName']] = groups
        # interactive mode
        if interactive:
            for variantName, groupsForUpgrade in groupsForUpgradeByVariants.items():
                for group in groupsForUpgrade.values():
                    params = []
                    prompt = "Variant '{}': Synchronize structure of parameters".format(variantName)
                    for parameterReport in group:
                        prompt += " {} ({}),".format(parameterReport['parameter']['name'], parameterReport['status'])
                        params.append(parameterReport['parameter']['id'])
                    prompt = prompt[:-1]
                    prompt += " ? (y/n)"
                    commit = input(prompt)
                    if commit != 'y':
                        params = []
                    if len(params) > 0:
                        if variantName in sync_request['targetVariants']:
                            sync_request['commitedParameterIdsByVariant'][variantName] += params
                        else:
                            sync_request['targetVariants'].append(variantName)
                            sync_request['commitedParameterIdsByVariant'][variantName] = params
        # non interactive mode
        else:
            variants = []
            committedParams = {}
            # commit all parameters if none are picked
            if params_to_commit is None:
                variants = [p['variantName'] for p in sync_prepare['reports']]
                committedParams = {
                    p['variantName']: [
                        param['parameter']['id'] for param in p['parameterReports']
                    ] for p in sync_prepare['reports']
                }
            # commit parameters picked on params_to_commit argument
            else:
                missing_variants = list(v for v in params_to_commit.keys() if v not in groupsForUpgradeByVariants.keys())
                if missing_variants:
                    raise ShadoxNotFoundException("The following variants given as argument were not found in report : {}".format(str(missing_variants)))
                variants = []
                committedParams = {}
                for variantName, groupsForUpgrade in groupsForUpgradeByVariants.items():
                    if variantName in params_to_commit.keys():
                        variants.append(variantName)
                        params = []
                        params_to_commit_for_variant = params_to_commit[variantName]
                        missing_params = list(params_to_commit_for_variant)
                        for param in (param for group in groupsForUpgrade.values() for param in group):
                            p_name = param['parameter']['name']
                            try:
                                missing_params.remove(p_name)
                            except:
                                pass
                        if missing_params:
                            raise ShadoxNotFoundException("The following parameters given as argument for variant {} were not found in report : {}".format(variantName, missing_params))

                        for group in groupsForUpgrade.values():
                            params_for_group = []
                            for parameterReport in group:
                                commit_param = parameterReport['parameter']['name'] in params_to_commit_for_variant
                                if len(params_for_group) > 0 and not commit_param:
                                    # this param is not selected, but another param in the same group was selected
                                    raise ShadoxForbiddenException('For variant {}, parameter {} is not selected but should be as it must be selected along with other parameters {}'
                                                                    .format(variantName, parameterReport['parameter']['name'], params_for_group))
                                if commit_param:
                                    params_for_group.append(parameterReport['parameter']['name'])
                                    params.append(parameterReport['parameter']['id'])
                        committedParams[variantName] = params
            sync_request['targetVariants'] = variants
            sync_request['commitedParameterIdsByVariant'] = committedParams
        log_action(u"|- Synchronize variant structures with request {}".format(sync_request), 2, self.loggerLevel)
        return self.http_helper.post(self.dataset_path + '/variant/sync/structure?commit=true', data=sync_request)

    def sync_variant_values_prepare(self, selector=''):
        """
        Gets list of synchronizable parameters for each variant
        :param selector: variant/publication from which to synchronize structures. If selector='variant/variantName'
        selects primary snapshot from variant variantName; if selector='publication/publicationName' selects publication
        snapshot; if selector='' selects current snapshot, fails if not connected to a snapshot
        :return: Synchronization preview, including possible parameters to sync
        """
        if selector == '':
            if self.connected:
                selector_type = 'snapshot'
                selector_value = self.snapshot.id
            else:
                raise ShadoxNoSnapshotException('Client not connected to a snapshot. Please select a variant or publication')
        else:
            [selector_type, selector_value] = selector.split('/')
        sync_request = {'selectorType': selector_type, 'selectorValue': selector_value}
        sync_report = {'selectorType': selector_type, 'selectorValue': selector_value}
        reports = self.http_helper.post(self.dataset_path + '/variant/sync/values', data=sync_request)['reports']
        filtered_reports = filter(lambda report: len(report['parameterReports']) > 0, reports)
        sync_report['reports'] = list(map(lambda report: {
            'variantName': report['variantName'],
            'parameterReports': [
                {
                    'status': param['status'],
                    'valueHasChanged': param['valueHasChanged'],
                    'parameter': {
                        'id': param['sourceParameter']['id'],
                        'name': param['sourceParameter']['path'] + build_member_name(param['sourceParameter']['groupName'], param['sourceParameter']['name'])
                    } if param['status'] == 'CREATED'
                    else {
                        'id': param['targetParameter']['id'],
                        'name': param['targetParameter']['path'] + build_member_name(param['targetParameter']['groupName'], param['targetParameter']['name'])
                    }
                } for param in report['parameterReports']
            ]
        }, filtered_reports))
        return sync_report

    def sync_variant_values_commit(self, sync_prepare, params_to_commit=None, interactive=False):
        """
        Commits values synchronization for variants
        :param sync_prepare: Preview of synchronization changes, including possible parameters to sync
        :param params_to_commit: Dict of params to sync by variant with the following structure
        params_to_commit = {
            variant_name: [
                param_name,
                param_name,
                ...
            ], ...
        }
        :param interactive: Flag to make behavior interactive. If True, interactively asks the user if a parameter should be committed
        :return: Synchronization report
        """
        sync_request = {
            'selectorType': sync_prepare['selectorType'],
            'selectorValue': sync_prepare['selectorValue'],
            'targetVariants': [],
            'commitedParameterIdsByVariant': {}
        }
        # interactive mode
        if interactive:
            for variantReport in sync_prepare['reports']:
                params = []
                for parameterReport in variantReport['parameterReports']:
                    if not parameterReport['valueHasChanged']:
                        continue
                    commit = input("Variant '{}': Synchronize value of {} parameter '{}' ? (y/n)".format(
                        variantReport['variantName'], parameterReport['status'], parameterReport['parameter']['name']
                    ))
                    if commit == 'y':
                        params.append(parameterReport['parameter']['id'])
                if len(params) > 0:
                    sync_request['targetVariants'].append(variantReport['variantName'])
                    sync_request['commitedParameterIdsByVariant'][variantReport['variantName']] = params
        # non interactive mode
        else:
            variants = []
            committedParams = {}
            # commit all parameters if none are picked
            if params_to_commit is None:
                variants = [p['variantName'] for p in sync_prepare['reports']]
                committedParams = {
                    p['variantName']: [
                        param['parameter']['id'] for param in p['parameterReports']
                    ] for p in sync_prepare['reports']
                }
            # commit parameters picked on params_to_commit argument
            else:
                missing_variants = list(v for v in params_to_commit.keys() if v not in (p['variantName'] for p in sync_prepare['reports']))
                if missing_variants:
                    raise ShadoxNotFoundException("The following variants given as argument were not found in report : {}".format(str(missing_variants)))

                variants = [p['variantName'] for p in sync_prepare['reports'] if p['variantName'] in params_to_commit.keys()]
                committedParams = {}
                for p in sync_prepare['reports']:
                    variant_name = p['variantName']
                    if variant_name in params_to_commit:
                        params_to_commit_for_variant = params_to_commit[variant_name]
                        missing_params = list(params_to_commit_for_variant)
                        for param in p['parameterReports']:
                            p_name = param['parameter']['name']
                            try:
                                missing_params.remove(p_name)
                            except:
                                pass
                        if missing_params:
                            raise ShadoxNotFoundException("The following parameters given as argument for variant {} were not found in report : {}".format(variant_name, missing_params))

                        committed_params_for_variant = []
                        for param in p['parameterReports']:
                            if param['parameter']['name'] in params_to_commit_for_variant:
                                committed_params_for_variant.append(param['parameter']['id'])
                        if committed_params_for_variant:
                            committedParams[p['variantName']] = committed_params_for_variant
            sync_request['targetVariants'] = variants
            sync_request['commitedParameterIdsByVariant'] = committedParams
        log_action(u"|- Synchronize variant values with request {}".format(sync_request), 2, self.loggerLevel)
        return self.http_helper.post(self.dataset_path + '/variant/sync/values?commit=true', data=sync_request)

    def _build_sync_variant_origins_report(self, server_sync_report):
        """
        Builds client side report for variant origin synchronization
        :param sync_report: Server side synchronization report
        :return: Client side synchronization report
        """
        sync_report = {}
        publication_infos = {}
        for v in server_sync_report.get('publicationInfos', []):
            d = dict(v)
            d['publicationFullName'] = v['datasetPath'] + '/' + v['publicationVariantName'] + '-' + v['publicationVersion']
            publication_infos[d['publicationId']] = d

        reports = []
        for report in server_sync_report['reports']:
            r = dict(report)
            r['availablePublications'] = [publication_infos.get(pub_id, None) for pub_id in report['availablePublications']]
            reports.append(r)
        sync_report['reports'] = reports

        return sync_report

    def sync_variant_origins_prepare(self, selector=''):
        """
        Gets list of synchronizable publications for each variant
        :param selector: variant/publication from which to synchronize structures. If selector='variant/variantName'
        selects primary snapshot from variant variantName; if selector='publication/publicationName' selects publication
        snapshot; if selector='' selects current snapshot, fails if not connected to a snapshot
        :return: Synchronization preview, including possible publications to sync
        """
        if selector == '':
            if self.connected:
                selector_type = 'snapshot'
                selector_value = self.snapshot.id
            else:
                raise ShadoxNoSnapshotException('Client not connected to a snapshot. Please select a variant or publication')
        else:
            [selector_type, selector_value] = selector.split('/')
        sync_request = {'selectorType': selector_type, 'selectorValue': selector_value}
        res = self.http_helper.post(self.dataset_path + '/variant/sync/origins', data=sync_request)
        sync_report = self._build_sync_variant_origins_report(res)
        sync_report['selectorType'] = selector_type
        sync_report['selectorValue'] = selector_value
        return sync_report

    def sync_variant_origins_commit(self, sync_prepare, pubs_to_commit=None, interactive=False, ignore_non_exact_matches=False):
        """
        Commits origins synchronization for variants
        :param sync_prepare: Preview of synchronization changes, including possible publications to sync
        :param pubs_to_commit: Dict of publications to sync by variant with the following structure
        pubs_to_commit = {
            variant_name: {
                full_publication_name: origin_variant_name,
                full_publication_name: origin_variant_name,
                ...
            }, ...
        }
        :param interactive: Flag to make behavior interactive. If True, interactively asks the user to choose a variant to sync for each origin
        :param ignore_non_exact_matches: Flag for default behavior. If not interactively choosing variants and no publication to commit is chosen (pubs_to_commit=None),
        exact matches for variant names will be selected for each origin to sync. If ir doesn't find an exact match for one of the publications, an exception will be raised.
        If, in such cases, this flag is True, pubications with no exact matches will be ignored and no exceptions will be raised
        :return: Synchronization report
        """
        sync_request = {
            'selectorType': sync_prepare['selectorType'],
            'selectorValue': sync_prepare['selectorValue'],
            'targetVariants': [],
            'targetVariantIdByPublicationIdByVariant': {}
        }
        # interative mode
        if interactive:
            for report in sync_prepare['reports']:
                variantName = report['variantName']
                sync_for_this_variant = {}
                for publication in report['availablePublications']:
                    full_pub_name = publication['publicationFullName']
                    prompt = "Choose target variant to sync with {} for publication '{}':\n".format(variantName, full_pub_name)
                    variants = publication['syncableVariants']
                    for i in range(len(variants)):
                        prompt += "\t{} ({})\n".format(variants[i]['name'], i)
                    prompt += "\tNo selection (return)\n> "
                    choice = input(prompt)
                    if choice == '':
                        continue
                    index = int(choice)
                    if index < 0 or index >= len(variants):
                        raise ShadoxForbiddenException('For variant {} and publication {}, choice {} is out of range'.format(variantName, publication['publicationVersion'], index))
                    sync_for_this_variant[publication['publicationId']] = variants[index]['id']
                if sync_for_this_variant:
                    sync_request['targetVariants'].append(variantName)
                    sync_request['targetVariantIdByPublicationIdByVariant'][variantName] = sync_for_this_variant
        # non interactive mode
        else:
            # commit exact matches by default
            if pubs_to_commit is None:
                for report in sync_prepare['reports']:
                    variantName = report['variantName']
                    sync_for_this_variant = {}
                    for publication in report['availablePublications']:
                        target_variant_id_by_variant_name = {var['name']: var['id'] for var in publication['syncableVariants']}
                        target_variant = target_variant_id_by_variant_name.get(variantName, None)
                        if target_variant is None:
                            if ignore_non_exact_matches:
                                continue
                            raise ShadoxNotFoundException("No exact match for variant '{}' on publication '{}'".format(variantName, publication['publicationFullName']))
                        sync_for_this_variant[publication['publicationId']] = target_variant
                    if sync_for_this_variant:
                        sync_request['targetVariants'].append(variantName)
                        sync_request['targetVariantIdByPublicationIdByVariant'][variantName] = sync_for_this_variant

            # commit publications picked on pubs_to_commit
            else:
                missing_variants = list(pubs_to_commit.keys())
                for report in sync_prepare['reports']:
                    try:
                        missing_variants.remove(report['variantName'])
                    except:
                        pass
                if missing_variants:
                    raise ShadoxNotFoundException("The following variants given as argument were not found in report : {}".format(str(missing_variants)))

                for report in sync_prepare['reports']:
                    variantName = report['variantName']
                    if variantName in pubs_to_commit:
                        pubs_to_commit_for_variant = pubs_to_commit[variantName]
                        missing_pubs = list(pubs_to_commit_for_variant)
                        for publication in report['availablePublications']:
                            full_pub_name = publication['publicationFullName']
                            try:
                                missing_pubs.remove(full_pub_name)
                            except:
                                pass
                        if missing_pubs:
                            raise ShadoxNotFoundException("The following publications given as argument for variant '{}' were not found in report : {}".format(variantName, missing_pubs))

                        sync_request['targetVariants'].append(variantName)
                        sync_for_this_variant = {}
                        for publication in report['availablePublications']:
                            target_variant_id_by_variant_name = {var['name']: var['id'] for var in publication['syncableVariants']}
                            full_pub_name = publication['publicationFullName']
                            target_variant = pubs_to_commit_for_variant.get(full_pub_name, None)
                            if target_variant is not None:
                                sync_for_this_variant[publication['publicationId']] = target_variant_id_by_variant_name[target_variant]
                        if sync_for_this_variant:
                            sync_request['targetVariantIdByPublicationIdByVariant'][variantName] = sync_for_this_variant
        log_action(u"|- Synchronize variant origins with request {}".format(sync_request), 2, self.loggerLevel)
        if not sync_request['targetVariantIdByPublicationIdByVariant']:
            # nothing to sync
            return None
        return self._build_sync_variant_origins_report(self.http_helper.post(self.dataset_path + '/variant/sync/origins?commit=true', data=sync_request))

    def fetch_snapshot(self):
        """
        INTERNAL USE ONLY - Fetch a snapshot and instantiate an object from api data
        :return Snapshot
        """
        log_action(u"|- Fetch snapshot " +
                   self.snapshot_path, 2, self.loggerLevel)

        json_result = self.http_helper.get(self.snapshot_path)
        self.connected = True
        return Snapshot(self, json_result)

    def get_parameter_value(self, parameter_id, value):
        """INTERNAL USE ONLY - Get the parameter value from its id and value kind"""
        url = self.snapshot_path + '/values/fetch'
        data = {'selectors': [{"id": parameter_id, "value": value}]}
        json_result = self.http_helper.post(url, data=data)
        if len(json_result['values']) > 0:
            return json_result['values'][0]['data']
        raise ShadoxParameterValueNotFoundException(
            "Parameter Value not found!")

    def is_dirty(self):
        """
        Indicates if a push is required to send updated data to the server
        """
        if not self.connected:
            return False

        return any(p.is_dirty() for p in self.snapshot.parameters)

    def resolve_parameters(self, parameters, unique_name=True):
        """
            Resolves a list of parameter aliases or paths into a list of Parameter objects
            If no match is found, will try to match the parameter name, but will fail in case of multiple matches.
            Pass unique_name=False if you want to get as many parameters as possible, without failing when no match is found
        """
        parameters_list = []
        errors = []

        parameters_by_path = None
        parameters_by_alias = None
        parameters_by_names = None
        for p in parameters:
            parameter = None
            err = None
            if isinstance(p, Parameter):
                parameter = p
            elif isinstance(p, string_types):
                p = p.replace('%', ' · ')  # Easier access to group members
                if p.startswith('/'):
                    if not parameters_by_path:
                        parameters_by_path = self.snapshot.get_parameters_by_path()
                    parameter = parameters_by_path.get(p, None)
                    if not parameter:
                        err = 'Could not find parameter with path ' + p
                else:
                    if not parameters_by_alias:
                        parameters_by_alias = self.snapshot.get_parameters_by_alias()
                    parameter = parameters_by_alias.get(p, None)
                    if not parameter:
                        # maybe parameter name without path ?
                        if not parameters_by_names:
                            parameters_by_names = self.snapshot.get_all_parameters_by_name()
                        name_matches = parameters_by_names[p]
                        if not unique_name:
                            parameters_list.extend(name_matches)
                            continue
                        if len(name_matches) == 1:
                            parameter = name_matches[0]
                        elif len(name_matches) > 1:
                            err = 'Multiple parameters have the same name \'{}\', use full path or unique_name=False: {}'.format(
                                p, [param.path for param in name_matches])
                        else:
                            err = 'Could not find parameter with alias or name ' + p
            else:
                err = 'Expected a Parameter or a string but got {}'.format(p)

            if parameter:
                parameters_list.append(parameter)
            else:
                errors.append(err)
        if errors:
            raise ShadoxParameterNotFoundException(
                'Unable to resolve parameters: {}'.format(errors))
        return parameters_list

    @once_connected
    def export_snapshot(self, export_type, file_path=None, parameters=None, ignore_dirty=False, export_hash=False):
        """Exports the snapshot currently on the server
        If parameters list is given, will only export those parameters.
        Allowed export types are CSV, ST0, XLACIS.

        XLACIS export has multiple variations :
         - one file per Shadox folder : XLACIS_MULTIPLE_FILES
         - single file, one datapackage per folder : XLACIS_SINGLE_FILE (XLACIS is an alias for this export)
         - single file, all folders in same datapackage : XLACIS_SINGLE_DATAPACKAGE
        """
        conf_exports = {
            'CSV': {'ext': 'csv', 'key': 'CSV'},
            'ST0': {'ext': 'st0', 'key': 'ST0'},
            'XLACIS': {'ext': 'zip', 'key': 'XLACIS'},
            'XLACIS_SINGLE_DATAPACKAGE': {'ext': 'zip', 'key': 'XLACIS_AIO'},
            'XLACIS_SINGLE_FILE': {'ext': 'zip', 'key': 'XLACIS_SF'},
            'XLACIS_MULTIPLE_FILES': {'ext': 'zip', 'key': 'XLACIS_MULTI'},

        }

        export_type = export_type.upper()
        if not export_type in conf_exports:
            raise ShadoxForbiddenException(
                'Unknown export type ' + export_type + ', allowed : ' + str(list(conf_exports.keys())))
        conf = conf_exports[export_type]

        if self.is_dirty() and not ignore_dirty:
            raise ShadoxForbiddenException(
                'You have unpushed modifications, they will not be exported. Use ignore_dirty=True to bypass this warning.')

        parameters_list = None
        if parameters:
            parameters_list = [
                p.id for p in self.resolve_parameters(parameters)]

        url = self.snapshot_path + '/export?type=' + conf['key']
        ext = conf['ext']
        return self._export_something(url, parameters_list, file_path, ext, export_hash, export_type)

    @once_connected
    def export_consumers(self, file_path=None, parameters=None, export_hash=False):
        """ Exports consumers of parameters of this snapshot in CSV format """
        parameters_list = None
        if parameters:
            parameters_list = [
                p.id for p in self.resolve_parameters(parameters)]

        url = self.snapshot_path + '/export-genealogy'
        ext = 'csv'
        folder_name = 'consumers'
        return self._export_something(url, parameters_list, file_path, ext, export_hash, folder_name)

    def _export_something(self, url, data, file_path, ext, export_hash, folder_name):
        """
            Internal helper
            export_hash => creates .sha256 file next to exported file
        """
        res = self.http_helper.post(url, data=data, return_type='file')
        if not file_path:
            file_path = self._prepare_temp_dir('exports_' + folder_name + '/')
            # maybe something else, but will most likely contain /
            file_path += str(int(time.time()))
        file_path = file_path + '.' + ext

        server_hash = res.headers.get('SHX_FILE_HASH', None)
        hasher = hashlib.sha256() if server_hash else None
        with open(file_path, 'wb') as f:
            for chunk in res.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
                    if hasher:
                        hasher.update(chunk)
        if server_hash:
            local_hash = hasher.hexdigest()
            if server_hash != local_hash:
                raise ShadoxInternalException(
                    "Hash mismatch between server and downloaded file: '{}' vs '{}'".format(server_hash, local_hash))
            if export_hash:
                with open(file_path+'.sha256', 'w') as f:
                    f.write(server_hash)

        return file_path

    @once_connected
    def import_file(self, import_type, file_path, commit=True):
        """Imports the file found at file_path in the snapshot,
        and refreshes the client if successful.
        use kwargs commit=False when importing CSV to preview the changes
        Allowed import types are : CSV, XLACIS
        """
        import_type = import_type.upper()
        if not import_type in ['CSV', 'XLACIS']:
            raise ShadoxForbiddenException(
                'Unknown import type ' + import_type + ", allowed : ['CSV', 'XLACIS']")
        with open(file_path, 'rb') as file_to_upload:
            file_hash = hashlib.sha256(file_to_upload.read()).hexdigest()
        with open(file_path, 'rb') as file_to_upload:
            files = {'file': file_to_upload}
            url = self.snapshot_path + '/import/upload?type=' + import_type + \
                '&fileHash=' + file_hash + ('&commit=true' if commit else '')
            res = self.http_helper.post(url, files=files)
            self.refresh()
            return ImportLog(res, self, import_type, str(int(time.time())))

    @once_connected
    def load_valuekinds_value(self, ls_parameter_values, nb_process=4):
        """given a collection of ValueKind instance, perform a network fetch
        on each of them with a pool of threads.
        """
        thread_pool = ThreadPool(nb_process)
        thread_pool.map(lambda vk: vk.value, ls_parameter_values)

    @once_connected
    def fetch_parameter_values(self, parameters=None, nb_process=4):
        """Use nb_process=4 threads in order to download all or a subset
        of the parameters' values in parallel.
        """
        if not parameters:
            parameters = self.snapshot.parameters
        else:
            parameters = self.resolve_parameters(parameters)
        # call ValueKind.value for each ValueKind in all Parameter instances forces network fetch
        self.load_valuekinds_value(
            (vk for p in parameters for vk in p.value_kinds), nb_process)

    @once_connected
    def fetch_value_kinds(self, vk_by_parameter_name, nb_process=4):
        """This function may be used to fetch multiple parameters' valuekinds
        across the network using multiple threads so they are available
        without any delay in further processing.
        Missing parameters/valuekinds are ignored.
        This methods accepts an argument with the following syntax:
        vk_by_parameter_name = {
            "<parameter_name1>": [
                "<vk_name1>",
                "<vk_name2>",
                "<vk_name3>",
            ],
            "<parameter_name2>": {
                "<vk_name2>",
                "<vk_name3>",
                "<vk_name4>",
            },
        }
        """
        if not isinstance(vk_by_parameter_name, dict):
            raise ShadoxForbiddenException(
                "vk_by_parameter_name should be dict")
        if not all(isinstance(ele, (list, set, tuple)) for ele in vk_by_parameter_name.values()):
            raise ShadoxForbiddenException(
                "vk_by_parameter_name values should be containers")

        parameter_values_to_fetch = []
        for p in self.snapshot.parameters:
            s_vks = set(vk_by_parameter_name.get(p.name, ()))
            if not s_vks:
                continue
            for vk in p.value_kinds:
                if vk.name in s_vks:
                    parameter_values_to_fetch.append(vk)

        self.load_valuekinds_value(parameter_values_to_fetch, nb_process)

    @once_connected
    def request_integrity(self, parameters=None, mode=1, benchmark=False, debug=False):
        """Get the hashes of parameters of this snapshot from the SHADOX server

        Args:
            parameters (list, optional): Parameters to hash. Defaults to all.
                Uses ShadoxClient.resolve_parameters()
            mode (1 or 2 or 3 or 4, optional): Hash algorithm to use. Defaults to 1.
                1: Only the raw value
                2: Value and unit
                3: Value, unit and value kind
                4: Value, unit, value kind and parameter name
            benchmark (bool, optional): If True, server will collect timings
            debug (bool, optional): If True, server will collect debug data

        Returns:
            IntegrityReport: Requested hashes and additional information
        """
        parameters_list = None
        if parameters:
            parameters_list = [{'id': p.id} for p in self.resolve_parameters(parameters)]

        url = self.snapshot_path + '/integrity'
        url += '?mode=' + str(mode)
        url += '&benchmark=' + ('true' if benchmark else 'false')
        url += '&debug=' + ('true' if debug else 'false')
        start = time.perf_counter()
        json_result = self.http_helper.post(url, data=parameters_list)
        duration_ms = int(1000 * (time.perf_counter() - start))
        return IntegrityReport(json_result, duration_ms)


    @once_connected
    def request_integrity_internal(self, recompute=False):
        """Get all internal hashes (also called "SHADOX MODE") of this snapshot from the SHADOX server

        Args:
            recompute (bool, optional):
                True - hashes are computed based on current snapshot value
                False - hashes are fetched from hash referential
        Returns:
            IntegrityInternalReport: Requested hashes and additional information
        """

        url = self.snapshot_path + '/integrity/internal'
        url += '?recompute=' + str(recompute)
        json_result = self.http_helper.get(url)
        return IntegrityInternalReport(json_result)

    @once_connected
    def refresh(self):
        """
        Refresh the snapshot and parameters with the last updates
        Take the values that are currently in the snapshot
        """
        refreshed_snapshot = self.fetch_snapshot()
        self.snapshot.refresh_with(refreshed_snapshot)

    @once_connected
    def restart(self):
        self.snapshot = self.fetch_snapshot()

    def connect(self, variant='master', publication=None):
        """Connect the client to a snapshot via its variant and perhaps its publication"""
        if not self.variants or variant not in self.variants:
            raise ShadoxNotFoundException("Variant can't be found in dataset")

        if not publication:
            self.snapshot_path = self.dataset_path + '/variant/' + variant
        else:
            try:
                next(p for p in self.variants[variant]
                     if p['version'] == publication)
            except StopIteration:
                raise ShadoxNotFoundException(
                    "Publication '{}' not found in variant '{}'".format(publication, variant))

            self.snapshot_path = self.dataset_path + \
                '/publication/{}-{}'.format(variant, publication)

        old_snapshot_id = self.snapshot.id if self.snapshot is not None else None
        self.snapshot = self.fetch_snapshot()

        # When snapshot is changed, throw away data associated to previous snapshot
        if self.snapshot.id != old_snapshot_id:
            self.publication = None

    @once_connected
    def freeze_snapshot(self, description, move_forward=False, ignore_dirty=False):
        """
        Freezes the current snapshot, with the given description, and creates a new working copy,
        then changes the snapshot_path of the ShadoxClient to either :
        - /snapshot/{id of the snapshot that was frozen} if no arg is given or arg 'move_forward' is False
        - /snapshot/{id of the new snapshot that was created} if arg 'move_forward' is True
        """
        if not self.snapshot.workingCopy:
            raise ShadoxForbiddenException('Snapshot is already frozen')
        elif not ignore_dirty and self.is_dirty():
            raise ShadoxForbiddenException(
                'Cannot freeze snapshot that has pending changes, use ignore_dirty=True to bypass')

        json_result = self.http_helper.post(
            self.snapshot_path + '/freeze', data=description)
        this_snapshot_id = json_result.get('frozenSnapshotId', None)
        new_snapshot_id = json_result.get('nextSnapshotId', None)
        self.snapshot_path = self.dataset_path + '/snapshot/' + \
            (new_snapshot_id if move_forward else this_snapshot_id)
        log_action(u"|- Froze snapshot {} and created new snapshot {}".format(
            this_snapshot_id, new_snapshot_id), 2, self.loggerLevel)
        log_action(u"|- Updated snapshot_path to snapshot id {}".format(
            new_snapshot_id if move_forward else this_snapshot_id), 3, self.loggerLevel)
        self.refresh()  # will update snapshot.workingCopy

    @once_connected
    def create_publication(self, name=None):
        """
        :return: a ShadoxPublication attached to this client, that can be modified and saved
        """
        if self.snapshot.workingCopy:
            raise ShadoxForbiddenException(
                'Snapshot is a working copy and cannot be published')
        try:
            # Attempt to load publication attached to this snapshot
            self.get_current_publication()
        except:
            # Maybe errors other than "There is no publication on this snapshot" should not be caught ?
            pass
        if self.publication:
            raise ShadoxForbiddenException(
                'There is already a publication for this snapshot, use sdx.get_current_publication()')

        log_action(u"|- Creating new publication", 2, self.loggerLevel)
        publication = ShadoxPublication(self, api_url=None);
        if name:
            publication.name = name
        # If name cannot be set, publication is not saved in the field
        self.publication = publication
        return self.publication

    @once_connected
    def create_publication_easy(self, name, label=None):
        """
        - Freezes the snapshot if necessary
        - Creates and saves a new publication with given name
        - Changes the snapshot_path of this client to point to the created publication
        :return: new publication that can be modified and saved
        """
        if self.snapshot.workingCopy:
            self.freeze_snapshot(
                'Automatic snapshot before publication ' + name)
        p = self.create_publication()
        try:
            p.name = name
            p.label = label
            p.save()
        except:
            # rollback created publication
            self.publication = None
            raise
        full_publication_name = self.snapshot.variant + '-' + name
        self.snapshot_path = self.dataset_path + \
            '/publication/' + full_publication_name
        log_action(u"|- Updated snapshot_path to path of newly created publication {}".format(
            full_publication_name), 3, self.loggerLevel)
        return p

    @once_connected
    def get_current_publication(self):
        """
        Returns the publication attached to this snapshot if it exists,
        throws if it does not
        """
        if self.publication:
            return self.publication
        log_action(
            u"|- Fetching publication associated with current snapshot", 3, self.loggerLevel)
        json_result = self.http_helper.get(self.snapshot_path + '/publication')
        self.publication = ShadoxPublication(self, json_result, self.http_helper.api_url)
        return self.publication

    @once_connected
    def _upload_file_to_publication(self, publication, file_path, filename=''):
        """
        INTERNAL USE ONLY
        Uploads file at file_path as an attachment to publication
        :return: publication json, after modification
        """
        log_action(u"|- Attaching file to publication", 3, self.loggerLevel)
        with open(file_path, 'rb') as file:
            filehash = hashlib.sha256(file.read()).hexdigest()
            file.seek(0)
            filename = os.path.basename(file.name) if filename == '' else filename
            files = {'file': file}
            pub_name = '{}-{}'.format(publication.variant, publication.name)
            url = self.dataset_path + '/publication/{}/items/upload?filename={}&fileHash={}'.format(pub_name, filename, filehash)
            json_result = self.http_helper.post(url, files=files)
        return json_result

    def _download_file_from_publication(self, publication, item_id, dest_file_path):
        """
        INTERNAL USE ONLY
        Downloads the attachement file with id item_id from the publication and
        saves it to dest_file_path
        """
        log_action(u"|- Downloading file from publication", 3, self.loggerLevel)

        pub_name = '{}-{}'.format(publication.variant, publication.name)
        url = self.dataset_path + '/publication/{}/item/{}/download'.format(pub_name, item_id)
        res = self.http_helper.post(url, return_type='file')

        server_hash = res.headers.get('SHX_FILE_HASH', None)

        with open(dest_file_path, 'wb+') as f:
            for chunk in res.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
            f.seek(0)
            local_hash = hashlib.sha256(f.read()).hexdigest()

        if local_hash != server_hash:
            raise ShadoxInternalException("Hash mismatch between server and downloaded file: '{}' vs '{}'".format(server_hash, local_hash))

        return dest_file_path

    def _download_files_from_publication(self, publication, dest_file_path):
        """
        INTERNAL USE ONLY
        Downloads all attachment files from the publication and saves it to dest_file_path
        """
        log_action(u"|- Downloading files from publication", 3, self.loggerLevel)

        pub_name = '{}-{}'.format(publication.variant, publication.name)
        url = self.dataset_path + '/publication/{}/items/download'.format(pub_name)
        res = self.http_helper.post(url, return_type='file')

        server_hash = res.headers.get('SHX_FILE_HASH', None)

        with open(dest_file_path, 'wb+') as f:
            for chunk in res.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
            f.seek(0)
            local_hash = hashlib.sha256(f.read()).hexdigest()

        if local_hash != server_hash:
            raise ShadoxInternalException("Hash mismatch between server and downloaded file: '{}' vs '{}'".format(server_hash, local_hash))

        return dest_file_path

    def _delete_file_from_publication(self, publication, item_id):
        """
        INTERNAL USE ONLY
        Deletes the attachment file with id item_id from the publication
        :return: publication json, after modification
        """
        log_action(u"|- Deleting file from publication", 3, self.loggerLevel)

        pub_name = '{}-{}'.format(publication.variant, publication.name)
        url = self.dataset_path + '/publication/{}/items/{}/delete'.format(pub_name, item_id)
        return self.http_helper.post(url)

    def _refresh_publications_list(self, json_publication):
        target_variant = json_publication['variant']
        target_version = json_publication['version']
        if not target_variant:
            return
        for variant, publications in self.variants.items():
            if target_variant != variant:
                continue
            found_publication = False
            for i, publication in enumerate(publications):
                if target_version != publication['version']:
                    continue
                publications[i] = json_publication
                found_publication = True
            if not found_publication:
                publications.append(json_publication)

    def _save_publication(self, publication):
        endpoint_for_save = '/createPublication' if publication._is_creation else '/updateMeta'
        if publication._is_creation:
            if not self.snapshot_path:
                raise ShadoxForbiddenException(
                    'To create a publication, you must be connected to a Snapshot')
            next_path = self.snapshot_path
        else:
            next_path = self.dataset_path + '/publication/' + \
                (publication.variant + '-' + publication.name)
        json_result = self.http_helper.post(
            next_path + endpoint_for_save, data=publication._build_delta())
        publication._refresh_with(json_result)
        self._refresh_publications_list(json_result)
        return publication

    @once_connected
    def _deliver_publication(self, publication):
        """
        Use publication.deliver()
        """
        json_result = self.http_helper.post(self.snapshot_path + '/deliver')
        publication._refresh_with(json_result)
        self._refresh_publications_list(json_result)
        return publication

    def _deprecate_publication(self, publication):
        """
        Use publication.deprecate()
        """
        publication_path = self.snapshot_path if self.snapshot_path else self.dataset_path + \
            '/publication/' + (publication.variant + '-' + publication.name)
        json_result = self.http_helper.post(publication_path + '/deprecate')
        publication._refresh_with(json_result)
        self._refresh_publications_list(json_result)
        return publication

    def get_project_tags(self):
        """
        Returns dictionary {'clusterName': ['tagName']}
        """
        if self.project_tags:
            return self.project_tags
        json_result = self.http_helper.get(self.dataset_path + '/projectTags')
        self.project_tags = json_result
        return self.project_tags

    def push(self, ignore_subscription=False):
        """
        Push all modifications done to parameters while the client is running.
        If some parameters were deleted, it will refresh impacted parameters
        by deleting or updating them
        :return: False if and only if nothing was pushed
        """

        # Don't throw if the client is not connected... could happened in notebook
        if not self.connected:
            return False

        log_action(u"|- Prepare updates", 3, self.loggerLevel)

        updated_tree = []
        if self.snapshot._dirty_tree:
            updated_tree = self.snapshot.tree
            url = self.snapshot_path + '/parameters/update'
            if self.use_version:
                url += '?version=' + str(self.snapshot.version)
            data = {
                'tree': updated_tree,
                'parameters': [],
                'groups': [],
            }

            json_result = self.http_helper.post(url, data=data)
            result = ApiParametersUpdateResponse(json_result)
            self.snapshot.refresh_with_updates(result)

        created_parameters = []
        created_groups = []
        for p in self.snapshot.parameters:
            if p._is_in_creation:
                created_parameters.append(p)
        for g in self.snapshot.groups:
            if g._is_in_creation:
                created_groups.append(g)
        if created_parameters:
            url = self.snapshot_path + '/parameters/create'
            if self.use_version:
                url += '?version=' + str(self.snapshot.version)
            data = [{
                'id': p.id,
                'name': p.member_name,
                'path': p.path,
                'definition': p.definition._to_dict()
            } for p in created_parameters]
            json_result = self.http_helper.post(url, data=data)
            result = ApiParametersUpdateResponse(json_result)
            self.snapshot._refresh_tree(result.tree)
            # parameters do not have id when created, so trust the order of the response to set it
            for i, p in enumerate(created_parameters):
                p._id = result.updated_parameters[i].id
                # XXX there should not be anything other than id to get from response
                p._is_in_creation = False
                p._touched_attributes.discard('path')
                p._touched_attributes.discard('name')
                # if user modified anything else (i.e definition, aliases), it will be pushed during fields_update

            # self.snapshot.refresh_with_updates(result)
            log_action(u"|- Parameter creations", 0, self.loggerLevel)
            columns = ['Parameter id', 'Path', 'Name']
            row_format = u"{:<40} " * len(columns)
            log_action(row_format.format(*columns), 0, self.loggerLevel)
            for p in created_parameters:
                log_action(row_format.format(
                    p.id, p.path, p.name), 0, self.loggerLevel)

        if created_groups:
            url = self.snapshot_path + '/groups/create'
            if self.use_version:
                url += '?version=' + str(self.snapshot.version)
            req_groups = [g.to_json() for g in created_groups]
            json_result = self.http_helper.post(url, data=req_groups)
            result = ApiParametersUpdateResponse(json_result)

            for g in created_groups:
                # cannot use order as ALL groups are returned
                res_g = next(
                    rg for rg in result.groups if rg['path'] == g.path and rg['name'] == g.name)
                g.refresh_with(Group(None, res_g))
                g._is_in_creation = False

            for res_p in result.updated_parameters:
                p = self.snapshot.search_parameter_by_id(res_p.id)
                p._path = res_p.path
                p._name = res_p.name

            log_action(u"|- Group creations", 0, self.loggerLevel)
            columns = ['Group id', 'Path', 'Name', 'Member ids']
            row_format = u"{:<40} " * len(columns)
            log_action(row_format.format(*columns), 0, self.loggerLevel)
            for g in created_groups:
                log_action(row_format.format(g.id, g.path, g.name,
                           ','.join(g.members)), 0, self.loggerLevel)

        # Build an array of values to update
        values = []
        updates = []
        imported_parameters = []
        deleted_parameters = []
        param_fields_updates = []
        updated_parameters_origins = []
        updated_groups = []
        updated_groups_origins = []
        for p in self.snapshot._deleted_parameters:
            deleted_parameters.append(p)

        for p in self.snapshot.parameters:
            update = p.build_update()
            if update['fields'] is not None:
                param_fields_updates.append(update['fields'])
            if len(update['values']) > 0:
                if p.is_imported:
                    imported_parameters.append(p)
                    continue
                values += update['values']
                updates.append([
                    p.id,
                    ','.join(p.alias),
                    ','.join([u['value'] for u in update['values']])
                ])
            if update['origin'] is not None:
                updated_parameters_origins.append(update['origin'])

        if imported_parameters and not ignore_subscription:
            err_msg = u"Warning : the following imported parameters were modified, they will be ignored :\n"
            err_msg += ''.join([repr(p) for p in imported_parameters])
            log_action(err_msg, -1, self.loggerLevel)

        for g in self.snapshot.groups:
            if g._is_modified:
                updated_groups.append(g.to_json())
            if g._updated_origin:
                updated_groups_origins.append(g._updated_origin)

        if not values and not deleted_parameters and not created_parameters and not param_fields_updates and not updated_groups and not updated_parameters_origins and not updated_groups_origins and not updated_tree:
            return False

        if updated_parameters_origins:
            log_action(u"|- Update parameters origin", 3, self.loggerLevel)
            url = self.snapshot_path + "/parameters/origin"
            json_result = self.http_helper.post(
                url, data=updated_parameters_origins)
            self.snapshot.refresh_with_updates(
                ApiParametersUpdateResponse(json_result))

        if updated_groups_origins:
            log_action(u"|- Update groups origin", 3, self.loggerLevel)
            url = self.snapshot_path + "/groups/origin"
            json_result = self.http_helper.post(
                url, data=updated_groups_origins)
            self.snapshot.refresh_with_updates(
                ApiParametersUpdateResponse(json_result))

        if param_fields_updates or updated_groups:
            # Update parameters metadata & structure if necessary
            url = self.snapshot_path + '/parameters/update'
            if self.use_version:
                url += '?version=' + str(self.snapshot.version)
            log_action(u"|- Push updates", 3, self.loggerLevel)
            data = ApiDatasetSnapshotUpdate(
                None, param_fields_updates, updated_groups).to_json()
            json_result = self.http_helper.post(url, data=data)
            self.snapshot.refresh_with_updates(
                ApiParametersUpdateResponse(json_result))

        # Build url and parameters
        if values:
            update_values, update_comments = [], []
            for update in values:
                (update_values if 'data' in update else update_comments).append(update)

            data = dict(
                values=update_values,
                comments=update_comments
            )
            url = self.snapshot_path + '/values/save'
            if self.use_version:
                url += '?version=' + str(self.snapshot.version)
            log_action(u"|- Push updates", 3, self.loggerLevel)
            json_result = self.http_helper.post(url, data=data)
            self.snapshot.version = json_result['version']
            alerts = json_result.get('alerts', [])
            parameter_ids_with_alerts = [a['id']
                                         for a in alerts if a.get('alerts', [])]
            # Reset hashes for parameters
            for p in self.snapshot.parameters:
                if p.id in [u[0] for u in updates]:
                    p.reset_dirty_state()
                    p._alerts = p.id in parameter_ids_with_alerts

            # Pretty print the updates
            log_action(u"|- Updates", 0, self.loggerLevel)
            columns = ['Parameter id', 'Parameter aliases', 'Values updated']
            row_format = u"{:<40} " * len(columns)
            log_action(row_format.format(*columns), 0, self.loggerLevel)
            for u in updates:
                log_action(row_format.format(*u), 0, self.loggerLevel)

        if deleted_parameters:
            url = self.snapshot_path + '/parameters/delete'
            if self.use_version:
                url += '?version=' + str(self.snapshot.version)
            data = [{'id': p.id} for p in deleted_parameters]

            log_action(u"|- Deleting " + "".join([p.__str__()
                       for p in deleted_parameters]), 3, self.loggerLevel)
            json_result = self.http_helper.post(url, data=data)
            self.snapshot.refresh_with_updates(
                ApiParametersUpdateResponse(json_result))
            self.snapshot._deleted_parameters = []

        if updated_tree:
            url = self.snapshot_path + '/parameters/update'
            if self.use_version:
                url += '?version=' + str(self.snapshot.version)
            data = {
                'tree': updated_tree,
                'parameters': [],
                'groups': [],
            }

            json_result = self.http_helper.post(url, data=data)
            result = ApiParametersUpdateResponse(json_result)
            self.snapshot.refresh_with_updates(result)

    def get_file_path_for_download(self, file_value, download_path):
        """INTERNAL USE ONLY - Get the file path to download ShadoxFile"""
        # Empty path if no fields
        if 'identifier' not in file_value or 'name' not in file_value:
            return ''

        if download_path:
            file_path = download_path
        else:
            # Prepare dir and file path
            temp_dir = self._prepare_temp_dir(file_value['identifier'])
            file_path = temp_dir + '/' + file_value['name']

        return file_path

    @once_connected
    def download_file(self, parameter_id, value_key, file_value, file_path=None, export_hash=False):
        """
        INTERNAL USE ONLY - Download a file
        :param parameter_id:
        :param value_key:
        :param file_value:
        :param file_path:
        :param export_hash:
        :return:
        """
        if not file_path:
            return ''

        # Build url and parameters
        data = {
            'id': parameter_id,
            'value': value_key,
            'fileRef': file_value['identifier']
        }
        url = self.snapshot_path + '/values/download'

        log_action(
            u"|--- Downloading file for value '{}' : '{}' to '{}'".format(
                value_key, file_value['name'], file_path),
            1,
            self.loggerLevel
        )
        res = self.http_helper.post(url, data=data, return_type='file')
        server_hash = res.headers.get('SHX_FILE_HASH', None)

        with open(file_path, 'wb') as f:
            for chunk in res.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
        if export_hash and server_hash:
            with open(file_path+'.sha256', 'w') as f:
                f.write(server_hash)

        return server_hash

    @once_connected
    def upload_file(self, parameter_id, value_key, file_path, local_hash):
        """
        INTERNAL USE ONLY - Upload a file and send back a dict with file infos
        :param parameter_id:
        :param value_key:
        :param file_path:
        :return:
        """
        # Allow file to be none
        if file_path is None:
            return None

        file_name = os.path.basename(file_path)

        # Build url and parameters
        # Note: use "with" to force close file after upload
        with open(file_path, 'rb') as file_to_upload:
            files = {'file': file_to_upload}
            url = self.snapshot_path + '/values/upload?id=' + parameter_id + \
                '&value=' + value_key + '&fileHash=' + local_hash

            log_action(
                u"|--- Uploading file for value '{}' : '{}' with hash '{}'".format(
                    value_key, file_name, local_hash),
                1,
                self.loggerLevel
            )
            res = self.http_helper.post(url, files=files)
        return res['fileRef']

    @once_connected
    def get_parameter_file_hashes(self, parameter_id, value_key):
        """
        INTERNAL USE ONLY - Ask the API to send the file hashes for the parameter value kind
        :param parameter_id:
        :param value_key:
        :return:
        """
        log_action(
            u"|--- Downloading hashes for value '{}' parameter {}".format(
                value_key, parameter_id),
            1,
            self.loggerLevel
        )

        # Build url and post request
        data = {"id": parameter_id, "value": value_key}
        url = self.snapshot_path + '/values/files/hash'
        return self.http_helper.post(url, data=data)

    @once_connected
    def get_uri_config_by_id(self, id):
        '''Looks for a symbolic pointer path based on its id
        :param id: Identifier of the wished symbolic pointer path
        :return: the corresponding SymbolicPointerPath object'''
        return self.uri_config[id]

    def clone(self):
        """Return another up-to-date client to the same Dataset Snapshot.
        The new cloned client is distinct from the original one, and has
        a fresh view of the Parameters and their values.
        """
        # Remove the trailing '/v1/' that is added in constructor
        api_url_without_version = "/".join(
            tuple(self.http_helper.api_url.split('/'))[:-2])
        # TODO update for http_helper
        shx = ShadoxClient(api_url=api_url_without_version,
                           api_key=self.api_key,
                           snapshot=self.snapshot_path,
                           proxies=self.http_helper.proxies,
                           agent_name=self.http_helper.agent_name,
                           certificates_path=self.http_helper.ssl_certificates_path,
                           api_keys_file_path=getattr(
                               self, "api_keys_file_path", None),
                           )
        return shx

    @property
    def sigdig_mode(self):
        return self._sigdig_mode

    @sigdig_mode.setter
    def sigdig_mode(self, sigdig_mode):
        if not sigdig_mode in (None, "truncate", "round"):
            raise ValueError(
                "sigdig_mode should be one of None, 'truncate', 'round'")
        self._sigdig_mode = sigdig_mode

    def _prepare_temp_dir(self, identifier):
        """
        INTERNAL USE ONLY - Check if the tmp dir is created, if not create and send the path
        :return:
        """
        # Create the temporary directory for the user
        temp_dir = tempfile.gettempdir() + '/shadox_files'
        # If the directory is /tmp (on unix with no $TMP env variable
        # We create a temporary directory for the user, to avoid conflicts on the same file
        if temp_dir == '/tmp/shadox_files':
            temp_dir = '/tmp/shadox_files_{}'.format(getpass.getuser())

        log_action(
            u"|--- Prepare temporary directory '{}'".format(temp_dir), 4, self.loggerLevel)
        if not os.path.exists(temp_dir):
            log_action(u"|--- Create directory", 4, self.loggerLevel)
            os.makedirs(temp_dir)

        temp_dir += '/' + identifier
        if not os.path.exists(temp_dir):
            log_action(u"|----- Create identifier directory",
                       4, self.loggerLevel)
            os.makedirs(temp_dir)

        return temp_dir

    def __iter__(self):
        """Send a parameters iterator"""
        return self.snapshot.parameters.__iter__()

    def __repr__(self):
        return 'ShadoxClient api_url=' + self.http_helper.api_url \
            + ' dataset=' + (self.snapshot_path or self.dataset_path or 'None')
