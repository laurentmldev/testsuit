#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

class ShadoxInstance(object):
  """SHADOX server information
  """

  def __init__(self, json_data):
    self._json = json_data
    self._key = json_data['key']
    self._name = json_data['name']

  @property
  def json(self):
      """Raw json sent by server"""
      return self._json

  @property
  def key(self):
      """Key of the instance, used in URL"""
      return self._key

  @property
  def trigram(self):
      """Alias for key"""
      return self.key

  @property
  def name(self):
      """User friendly name of the instance"""
      return self._name

  def __repr__(self):
    return u'ShadoxInstance[key={}, name={}]'.format(self.key, self.name)


class ResourceInformation(object):
  """General information about a dataset or library

  - description
  - tags
  - reference flag
  - replication information
  - administrator names
  - access rights
  """

  def __init__(self, json_data):
    self._json = json_data
    self._description = json_data['description']
    self._tags = json_data['tags']
    self._reference = json_data['reference']
    self._replicationInstances = {
      'master': ShadoxInstance(json_data['instanceMaster']),
      'slaves': [ShadoxInstance(i) for i in json_data['instanceSlaves']]
    }
    self._administrators = json_data['administrators']
    # retrocompat
    if 'rights' in json_data:
      self._rights = json_data['rights']
      self._variantRights = json_data['variantRights']
      self._userRights = None
      self._userVariantRights = None
    else:
      self._rights = json_data['groupRights']
      self._variantRights = json_data['variantGroupRights']
      self._userRights = json_data['userRights']
      self._userVariantRights = json_data['variantUserRights']

  @property
  def json(self):
    """Raw JSON as received from server"""
    return self._json

  @property
  def description(self):
    return self._description

  @property
  def tags(self):
    return self._tags

  @property
  def reference(self):
    return self._reference

  @property
  def replicationInstances(self):
    return self._replicationInstances

  @property
  def administrators(self):
    return self._administrators

  @property
  def rights(self):
    """RETROCOMPATIBILITY ONLY, returns self.group_rights"""
    return self.group_rights

  @property
  def variantRights(self):
    """RETROCOMPATIBILITY ONLY, returns self.group_rights_by_variant"""
    return self.group_rights_by_variant

  @property
  def group_rights(self):
    """
    [
      {
        'group': 'Managers',
        'rights': {
          'BROWSE': True,...
        }
      },...
    ]
    """
    return self._rights

  @property
  def group_rights_by_variant(self):
    """
    {
      'master': [
        {
          'group': 'Managers',
          'rights': {
            'BROWSE': True,...
          }
        },...
      ]
    }
    """
    return self._variantRights

  @property
  def user_rights(self):
    """
    [
      {
        'user': 'MARTIN',
        'rights': {
          'BROWSE': True,...
        }
      },...
    ]
    """
    return self._userRights

  @property
  def user_rights_by_variant(self):
    """
    {
      'master': [
        {
          'user': 'MARTIN',
          'rights': {
            'BROWSE': True,...
          }
        },...
      ]
    }
    """
    return self._userVariantRights

  @property
  def rights_dict(self):
    """rights for group as dictionary {'groupname': {...rights...}}"""
    return {r['group']:r['rights'] for r in self.rights}

  @property
  def all_rights_dataframe(self):
    """rights for groups as dataframe with columns 'variant,group,BROWSE,...'"""
    from pandas import DataFrame
    d = {
      'variant': [],
      'group': [],
    }
    for t in self._available_rights:
      d[t] = []

    for it in self.rights:
      d['variant'].append('')
      d['group'].append(it['group'])
      for t in self._available_rights:
        d[t].append(it['rights'][t])

    for (variant, its) in self.variantRights.items():
      for it in its:
        d['variant'].append(variant)
        d['group'].append(it['group'])
        for t in self._available_rights:
          d[t].append(it['rights'].get(t, None))

    return DataFrame(d)

  def __repr__(self):
    return repr(self.json)

class DatasetInformation(ResourceInformation):
  def __init__(self, json_data):
    super(DatasetInformation, self).__init__(json_data)
    self._available_rights = ('BROWSE', 'SEARCH_OUTPUTS', 'SUBSCRIBE_TO_OUTPUTS', 'READ', 'WRITE', 'ADMIN', 'SIGN')
    self._cartouches = json_data.get('cartouches')

  @property
  def cartouches(self):
    """Confidentiality settings of this dataset
    """
    return self._cartouches

class LibraryInformation(ResourceInformation):
  def __init__(self, json_data):
    super(LibraryInformation, self).__init__(json_data)
    self._available_rights = ('BROWSE', 'READ', 'WRITE', 'ADMIN', 'SIGN')
