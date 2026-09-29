#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from .http_helper import ShadoxHTTPHelper
from .exceptions import ShadoxNotFoundException
from .responses import LibraryInformation, ProjectInformation
from .library_revision import Revision

from ..version import __version__
from .utils import log_action
from .client import ShadoxClient

class ShadoxLibraryClient(object):
    """
        Read information of a Shadox Library and its revisions
    """

    def __init__(
            self,
            api_url,
            library_path,
            api_key,
            agent_name="Python Shadox API Wrapper for library {}".format(__version__),
            logger_level=0,
            certificates_path='/etc/ssl/certs/shadox/',
            proxies=None,
    ):
        self.logger_level = logger_level
        log_action(u"### Init Shadox library client version {}".format(__version__), 4, self.logger_level)

        self.http_helper = ShadoxHTTPHelper(
            api_url,
            api_key,
            agent_name,
            logger_level,
            certificates_path,
            proxies,
        )

        split_path = library_path.split('revision/')
        self.library_path = split_path[0]
        self.revision = None
        if len(split_path) > 1:
            self.revision = split_path[1]

        library_json_data = self.http_helper.get(self.library_path)
        self.key = library_json_data['key']
        self.study_key = library_json_data['studyKey']
        self.description = library_json_data.get('description', None)
        self.revisions = [Revision(self, rev_data) for rev_data in library_json_data['iterations']]

        if self.revision:
            self.load_revision_details(self.revision)

    def __repr__(self):
        return 'Library[studyKey={}, key={}, revisions={}]'.format(
            self.study_key, self.key,
            [r.revision for r in self.revisions]
        )

    def _open_publication(self, publication_path):
        project_path = self.library_path.split('/library/')[0]
        api_url_without_version = "/".join(tuple(self.http_helper.api_url.split('/'))[:-2])
        pub_client = ShadoxClient(
            api_url=api_url_without_version,
            api_key = self.http_helper.api_key,
            snapshot = project_path + publication_path,
            proxies=self.http_helper.proxies,
            agent_name=self.http_helper.agent_name,
            certificates_path=self.http_helper.ssl_certificates_path or '',
            logger_level=self.logger_level
        )
        return pub_client

    def _push_revision_update_add(self, revision, add_publications):
        """INTERNAL USE"""
        log_action(u"# Pushing new publications: {}".format(add_publications), 2, self.logger_level)
        res = self.http_helper.post(self.library_path + 'revision/' + revision.revision + '/publications/add', add_publications, return_type=None)
        log_action(u"# Successfully added publications", 2, self.logger_level)
        return res.json()

    def _push_revision_update_delete(self, revision, delete_publications):
        """INTERNAL USE"""
        log_action(u"# Pushing deleted publications: {}".format(delete_publications['publications']), 2, self.logger_level)
        self.http_helper.post(self.library_path + 'revision/' + revision.revision + '/publications/delete', delete_publications, return_type=None)
        log_action(u"# Publications deleted successfully.", 2, self.logger_level)

    def _push_revision_update_update(self, revision, update_publications):
        """INTERNAL USE"""
        log_action(u"# Pushing updated publications: {}".format(update_publications['publications']), 2, self.logger_level)
        res = self.http_helper.post(self.library_path + 'revision/' + revision.revision + '/publications/update', update_publications, return_type=None)
        log_action(u"# Successfully updated publications", 2, self.logger_level)
        return res.json()
    
    def _push_revision_update_move(self, revision, move_publications):
        """INTERNAL USE"""
        log_action(u"# Pushing moved publications: {}".format(move_publications['publications']), 2, self.logger_level)
        res = self.http_helper.post(self.library_path + 'revision/' + revision.revision + '/publications/move', move_publications, return_type=None)
        log_action(u"# Successfully moved publications", 2, self.logger_level)        

    def _push_revision_update_comment(self, revision, comments):
        """INTERNAL USE"""
        log_action(u"# Pushing comments modifications: {}".format(comments['comments']), 2, self.logger_level)
        self.http_helper.post(self.library_path + 'revision/' + revision.revision + '/comments', comments, return_type=None)
        log_action(u"# Comments updated successfully.", 2, self.logger_level)

    def get_revision(self):
        """Details of the revision given in constructor

        Returns
        -------
        Revision with details loaded
        """

        if not self.revision:
            raise ShadoxNotFoundException("No revision was given when building client")
        return next(it for it in self.revisions if it.revision == self.revision)

    def load_revision_details(self, revision):
        """Details of the revision given in constructor

        Parameters
        ----------
        revision : str
            conf:A62-B

        Returns
        -------
        Revision with details loaded
        """

        revision_to_load = next(it for it in self.revisions if it.revision == revision)
        json = self.http_helper.get(self.library_path + 'revision/' + revision)
        revision_to_load._enhance(json)
        return revision_to_load

    def get_information(self):
        """General information about this library

        Returns
        -------
        LibraryInformation
        """
        if not hasattr(self, '_informations'):
            json_data = self.http_helper.get(self.library_path + 'informations')
            self._informations = LibraryInformation(json_data)
        return self._informations

    @property
    def project_informations(self):
        if not hasattr(self, '_project_informations') or not self._project_informations:
            json_data = self.http_helper.get(self.library_path + 'projectInfos')
            self._project_informations = ProjectInformation(json_data)
        return self._project_informations
