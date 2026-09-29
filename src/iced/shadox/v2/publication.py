#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from datetime import datetime
from six import string_types

from .exceptions import (ShadoxForbiddenException, ShadoxFileNotFoundException)
from .utils import (
    check_python_value_publication_match_shadox,
    serialize_date,
    check_tags_is_in_list_project_tags,
    add_list_items, delete_list_items
)


_dict_python_name_to_serverside_name = {
    'name': 'name',
    'label': 'label',
    'description': 'description',
    'scheduled_date': 'scheduledDate',
    'tags': 'tags',
}

class BasePublication(object):
    """Common information about a publication

    More contextualized information is available in both
    dataset (ShadoxClient) and library (ShadoxLibraryClient)

    Attributes
    ----------
    variant : string
    name : string
        also known as `version`
    label : string
    status : 'DRAFT' or 'DELIVERED' or 'DEPRECATED' or 'UNKNOWN_NOT_FETCHED_YET'
    description : string
    tags : list of strings
    signatories : list
    scheduled_date : datetime, optional
    publication_date : datetime, optional
    deprecation_date : datetime, optional
    libraries : list of `{'urn': 'lib_name@A/revision/master-A', 'status': 'RELEASED'}`
    permalink : string
        Url to open the publication in the browser

    """
    def __init__(
            self,
            publication,
            api_url=None
    ):
        self._api_url = api_url
        self.name = publication.get('version', None)
        self.label = publication.get('label', None)
        self.status = publication.get('status', None)
        self.description = publication.get('description', None)
        self.signatories = publication.get('signatories', [])
        self.tags = publication.get('tags', None) if publication.get('tags', None) else []
        self.tags.sort()
        self.scheduled_date = datetime.fromtimestamp(publication.get('scheduledDate', None) / 1000).date() if publication.get('scheduledDate', None) else None
        self.publication_date = datetime.fromtimestamp(publication.get('publicationDate', None) / 1000).date() if publication.get('publicationDate', None) else None
        self.deprecation_date = datetime.fromtimestamp(publication.get('deprecationDate', None) / 1000).date() if publication.get('deprecationDate', None) else None
        self.libraries = publication.get('relatedLibraries',  [])
        if api_url and publication.get('id', None):
            self.permalink = api_url.split("shadox-service")[0]\
                             + "shadox/#/permalink/publication/" + publication.get('id', None)
        else:
            self.permalink = None
        self.variant = None
        if publication.get('variant', None) is not None:
            self.variant = publication.get('variant', None)

        # Set readonly checks
        self._touched_keys = set()
        self._updatable_keys = set()

    def _refresh_with_obj(self, refreshed_publication):
        # Remove readonly checks
        self._touched_keys = None

        self.name = refreshed_publication.name
        self.permalink = refreshed_publication.permalink
        self.label = refreshed_publication.label
        self.variant = refreshed_publication.variant
        self.status = refreshed_publication.status
        self.description = refreshed_publication.description
        self.signatories = refreshed_publication.signatories
        self.tags = refreshed_publication.tags
        self.tags.sort()
        self.scheduled_date = refreshed_publication.scheduled_date
        self.publication_date = refreshed_publication.publication_date
        self.deprecation_date = refreshed_publication.deprecation_date
        self.libraries = refreshed_publication.libraries

        # Restore readonly checks
        self._touched_keys = set()

    def _assert_editable(self):
        raise NotImplementedError('Cannot edit BasePublication')

    def _before_setattr(self, *_):
        pass

    def _after_setattr(self, *_):
        pass

    def __setattr__(self, key, value):
        if getattr(self, '_touched_keys', None) is not None and not key.startswith('_'):
            self._assert_editable()

            if key == 'version':
                key = 'name'
            if key not in self._updatable_keys:
                raise ShadoxForbiddenException('Only the following attributes can be edited on this publication : ' + str(self._updatable_keys))

            self._before_setattr(value, key)

            self._touched_keys.add(key)
        super(BasePublication, self).__setattr__(key, value)
        self._after_setattr(value, key)

    @property
    def version(self):
        """Alias for 'name' attribute"""
        return self.name

    def get_libraries(self):
        """Tree view of `p.libraries`

        Returns
        -------
        dict of dict of str
            `{'lib_name@A': {'master': ['A']}}`
        """
        libs = {}
        for library in self.libraries:
            urn = library.get('urn')
            lib, revision = urn.split('/revision/')
            if '-' in revision:
                if not lib in libs:
                    libs[lib] = {}
                variant, rev = revision.split('-')
                if not variant in libs[lib]:
                    libs[lib][variant] = []
                libs[lib][variant].append(rev)
                libs[lib][variant].sort()
            else:
                if not lib in libs:
                    libs[lib] = []
                libs[lib].append(revision)
                libs[lib].sort()
        return libs

    def status_repr(self):
        if self.status in ('DRAFT', 'DELIVERED', 'DEPRECATED', None):
            return self.status
        elif self.status == 'UNKNOWN_NOT_FETCHED_YET':
            return 'UNKNOWN, PUSH CLIENT TO FETCH ACTUAL INFORMATION'
        else:
            raise ValueError('Invalid value for publication status')

class ShadoxPublication(BasePublication):
    """A publication as referenced in a dataset"""

    def __init__(
            self,
            client, #No client => readonly
            publication = {'new_publication': True},
            api_url=None

    ):
        super(ShadoxPublication, self).__init__(publication, api_url)
        self._touched_keys = None
        self._client = client
        self._is_creation = publication.get('new_publication', False)
        if self.variant is None and self._client is not None and self._client.snapshot is not None and self._client.snapshot.variant is not None:
            self.variant = self._client.snapshot.variant

        self._attachment_files = publication.get('files', [])

        self._touched_keys = set()
        self._updatable_keys = set(['description', 'tags'])
        if self.name is None:
            self._updatable_keys.add('name')
        if self.publication_date is None:
            self._updatable_keys.add('label')
            self._updatable_keys.add('scheduled_date')
        publication['_obj'] = self

    @property
    def dataset_name(self):
        return self._client.dataset_name if self._client else None

    def _refresh_with(self, new_publication):
        refreshed_publication = ShadoxPublication(self._client, new_publication, self._api_url)
        self._refresh_with_obj(refreshed_publication)
        self._touched_keys = None # Disable readonly checks
        self._is_creation = False
        self._attachment_files = refreshed_publication._attachment_files
        self._updatable_keys = refreshed_publication._updatable_keys
        self._touched_keys = set()
        new_publication['_obj'] = self

    def _before_setattr(self, value, key):
        #Check if the value entered correspond to the whished type for this attribute
        check_python_value_publication_match_shadox(value, key, self.variant, self._client.get_publications_obj())

    def _assert_editable(self):
        pass # there is always at least one field that can be edited

    def _build_delta(self):
        delta = dict()
        for key in self._touched_keys:
            delta[_dict_python_name_to_serverside_name[key]] = getattr(self, key)

        if 'scheduledDate' in delta.keys():
            delta['scheduledDate'] = serialize_date(delta['scheduledDate'])

        return delta

    def get_user_rights(self):
        """
        [{
            'user': 'MARTIN',
            'rights': {
                'BROWSE': True,...
            }
        },...]
        """
        infos = self._client.get_information()
        return infos.user_rights

    def get_user_rights_in_variant(self):
        """
        [{
            'user': 'MARTIN',
            'rights': {
                'BROWSE': True,...
            }
        },...]
        """
        infos = self._client.get_information()
        return infos.user_rights_by_variant[self.variant]

    def get_group_rights(self):
        """
        [{
            'group': 'Managers',
            'rights': {
                'BROWSE': True,...
            }
        },...]
        """
        infos = self._client.get_information()
        return infos.group_rights

    def get_group_rights_in_variant(self):
        """
        [{
            'group': 'Managers',
            'rights': {
                'BROWSE': True,...
            }
        },...]
        """
        infos = self._client.get_information()
        return infos.group_rights_by_variant[self.variant]

    def save(self):
        """
        If the publication was not yet created on the server, creates it.
        Saves the updated fields.
        """
        return self._client._save_publication(self)

    def deliver(self):
        if self.publication_date:
            raise ShadoxForbiddenException('This publication is already delivered')
        return self._client._deliver_publication(self)

    def deprecate(self):
        if self.deprecation_date:
            raise ShadoxForbiddenException('This publication is already deprecated')
        return self._client._deprecate_publication(self)

    def validator(self, list_tags):
        # Check the validity of tags user want to add (all defined as project Tags)
        return check_tags_is_in_list_project_tags(list_tags, self._client.get_project_tags())

    def add_tags(self, list_tags):
        tags, tags_to_add = add_list_items(self.tags, list_tags, self.validator, sortItems=True)
        if tags_to_add:
            self.tags = tags
            self._touched_keys.add('tags')

    def add_tag(self, cluster, name):
        if not isinstance(cluster, string_types) or not isinstance(name, string_types):
            raise ShadoxForbiddenException('Cluster and tag name must be strings')
        self.add_tags([cluster + ':' + name])

    def browse_attached_files(self):
        """
        Returns a list of attached files with id used for download and id used for deletion
        :return: List of attached files
        """
        files = []
        for attachement in self._attachment_files:
            files.append({'name': attachement['fileName'], 'id': attachement['id']})
        return files

    def upload_attached_file(self, file_path, filename=''):
        """
        Uploads file at file_path to this publication and refreshes the publication
        """
        pub = self._client._upload_file_to_publication(self, file_path, filename)
        self._refresh_with(pub)

    def download_attached_file(self, file, dest_file_path=''):
        """
        Downloads file from this publication and saves it at dest_file_path
        """
        if isinstance(file, string_types):
            file_id = file
            f = self._get_attachment(file_id)
        else:
            f = self._get_attachment(file['id'])
        if dest_file_path == '':
            pubname = self.variant + '-' + self.name
            dest_file_path = self._client._prepare_temp_dir('publication-' + pubname + '-attachments')
            dest_file_path += '/{}'.format(f['name'])
        return self._client._download_file_from_publication(self, f['download_id'], dest_file_path)

    def download_attached_files(self, dest_file_path=''):
        """
        Downloads all files attached to publication as a zip and saves it at dest_file_path
        """
        if dest_file_path == '':
            pubname = self.variant + '-' + self.name
            dest_file_path = self._client._prepare_temp_dir('publication-' + pubname + '-attachments')
            dest_file_path += '/all_attachments.zip'
        return self._client._download_files_from_publication(self, dest_file_path)

    def delete_attached_file(self, file):
        """
        Deletes file from this publication
        """
        if isinstance(file, string_types):
            file_id = file
            f = self._get_attachment(file_id)
        else:
            f = self._get_attachment(file['id'])
        pub = self._client._delete_file_from_publication(self, f['delete_id'])
        self._refresh_with(pub)

    def _get_attachment(self, file_id):
        """
        INTERNAL USE ONLY
        Gets attachment file from id or name
        :return: The file if file_id matches id or fileName
        """
        for attachment in self._attachment_files:
            att_id = attachment['id']
            att_name = attachment['fileName']
            if att_id == file_id or att_name == file_id:
                return {'name': attachment['fileName'], 'download_id': attachment['id'], 'delete_id': attachment['fileRef']}
        raise ShadoxFileNotFoundException('Attachment with name or id ' + file_id + ' not found in publication')

    def delete_tags(self, list_tags=None):
        """
        Delete tags from the publication
        """
        self.tags = delete_list_items(self.tags, list_tags)
        self._touched_keys.add('tags')

    def __repr__(self):
        content = [
            ('dataset=' + self.dataset_name) if self.dataset_name else None,
            ('variant=' + self.variant) if self.variant else None,
            ('name=' + self.name) if self.name else None,
            ('label=' + self.label) if self.label else None,
            ('status=' + self.status_repr()) if self.status else None
        ]
        return u'Publication[' + ','.join(filter(None, content)) + ']'
