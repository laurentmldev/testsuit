#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.
from . import SymbolicPointerPath
from ..utils import log_action


class DefaultRights(object):
    def __init__(self, json_data):
        self._json = json_data
        self.dataset = json_data.get('dataset')
        self.library = json_data.get('library')
        self.external_resource = json_data.get('externalResource')

    def __repr__(self):
        return repr(self._json)


class ProjectInformation(object):
    def __init__(self, json_data, logger_level=0):
        self._json = json_data
        self.default_rights = DefaultRights(json_data.get('defaultRights'))
        self.forbidden_variant_combinations = json_data.get('forbiddenVariantCombinations')
        self.variant_groups = json_data.get('variantGroups')
        self.uri_config = {spp["id"]: SymbolicPointerPath(spp) for spp in json_data.get('uriConfig')}
        self.tag_clusters = json_data.get('tagClusters')
        self.logger_level = logger_level

    def check_allowed_variant_combination(self, combination):
        """
            Check if variant combination is allowed
            :param combination: variant combination to be checked
            :return: True if combination is not forbidden
            :return: False otherwise
        """
        for forbidden in self.forbidden_variant_combinations:
            variant_in_forbidden = 0
            for variant in combination:
                variant_in_forbidden += 1 if variant in forbidden else 0
                if variant_in_forbidden > 1:
                    return False
        return True

    def get_valid_variant_combinations(self, variants):
        """
            Checks if variant combinations are forbidden
            :param variants: variants
            :return: list of variants that are not forbidden
        """
        variant_combinations = [
            [x.split(':') for x in v.split(',')] for v in variants]
        valid_variants = []
        for combination in variant_combinations:
            comb = []
            comb_str = ''
            for v in combination:
                comb_str += (v[0] + ':' + v[1] if len(v) == 2 else v[0]) + ','
                comb.append({'group': v[0], 'name': v[1]}
                            if len(v) == 2 else {'name': v[0]})
            if self.check_allowed_variant_combination(comb):
                valid_variants.append(comb_str[:-1])
            else:
                log_action(u"|- Variant combination " +
                           comb_str[:-1] + " is forbidden, ignoring...", 2, self.logger_level)
        return valid_variants

    def __repr__(self):
        return repr(self._json)
