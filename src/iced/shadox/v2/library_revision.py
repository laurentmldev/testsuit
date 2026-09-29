from iced.shadox.v2.publication import ShadoxPublication
from .library_publication import Publication
from .exceptions import ShadoxForbiddenException
from datetime import datetime
from .utils import log_action, validate_path_param

class Revision(object):
    """
        Information of a library revision.
        If publications_by_dataset is None, data must be fetched from API
        with ShadoxLibraryClient.load_revision_details('revision name')
    """
    def __init__(
        self,
        library_client,
        json_data,
    ):
        self._library_client = library_client
        self._parse_json(json_data)
        # Those fields are only set through self._enhance()
        self.signatories = None
        self.publications_by_dataset = None
        self.n_publications = None

        self._publications_to_remove = []

    def _enhance(self, json_data):
        """
            Rebuilds this object with additional information (signatories & publications list)
        """
        self._parse_json(json_data)
        self.signatories = json_data['signedBy']
        self.publications_by_dataset = {dataset_path:[Publication(dataset_path, self, pub_data, self._library_client.http_helper.api_url) if pub_data else None for pub_data in pubs_data] for (dataset_path, pubs_data) in json_data['deliverablesByDataset'].items()}
        self.n_publications = sum(len(publications) for publications in self.publications_by_dataset.values())
        self._update_folder_in_revision_and_tree(self.publications_by_dataset)

    def _parse_json(self, json_data):
        self.revision = json_data['revision']
        self.variant = None if not '-' in self.revision else self.revision.split('-')[0]
        self.status = json_data['status']
        self.description = json_data.get('description', None)
        if self._library_client is not None and self._library_client.http_helper is not None and self._library_client.http_helper.api_url is not None and json_data.get('id', None) is not None:
            self.permalink = self._library_client.http_helper.api_url.split("shadox-service")[0] \
            + "shadox/#/permalink/revision/" + json_data.get('id', None)
        else:
            self.permalink = None
        self.tags = json_data['tags']
        self.creation_date = datetime.fromtimestamp(json_data.get('creationDate', None) / 1000).date() if json_data.get('creationDate', None) else None
        self.release_date = datetime.fromtimestamp(json_data.get('releaseDate', None) / 1000).date() if json_data.get('releaseDate', None) else None
        self.obsolete_date = datetime.fromtimestamp(json_data.get('obsoleteDate', None) / 1000).date() if json_data.get('obsoleteDate', None) else None
        self.tree = json_data.get('folders', [])
        if self.tree:
            if '/' not in self.tree:
                self.tree.append('/')
            self.tree.sort()


    def _update_folder_in_revision_and_tree(self, publications_by_dataset):
        folder_map = {}

        for datasetKey in publications_by_dataset:
            publications = publications_by_dataset[datasetKey]
            for publication in publications:
                if publication is None or not publication.folders:
                    folders = ['/']
                else:
                    folders = publication.folders
                for path in folders:
                    if path not in folder_map:
                        folder_map[path] = {}
                    if datasetKey not in folder_map[path]:
                        folder_map[path][datasetKey] = []
                    folder_map[path][datasetKey].append(publication)

        self.publication_by_folder_in_revision = dict(sorted((path, dict(sorted((d, p) for d,p in pubs.items()))) for path, pubs in folder_map.items()))
        if self.tree and self.publication_by_folder_in_revision:
            self.tree = self.tree + list(set(self.publication_by_folder_in_revision.keys()) - set(self.tree))
            self.tree.sort()

    def get_all_publications(self, folder='/'):
        """Flat view of publications in this revision

        Args:
            folder (str, optional): Filter to show only publications in this folder.
                Defaults to '/', showing all the publications.
                Restricted publications (shown as None) have no known folder, thus are only returned with default '/'
        Returns:
            list: All the publications
        """
        validate_path_param(folder)
        if folder == '/':
            # Keep all publications (including None)
            return [pub for pubs in self.publications_by_dataset.values() for pub in pubs]
        else:
            # Filter None publications and publications not in this folder
            return [pub for pubs in self.publications_by_dataset.values() for pub in filter(None, pubs) if any(f.startswith(folder) for f in pub.folders)]

    def get_user_rights(self):
        """List of all the users that can access this revision

        Returns
        -------
        list of `{'user': 'MARTIN', 'rights': {'BROWSE': True,...}}`
        """

        if not self._library_client:
            raise ShadoxForbiddenException('Cannot get the rights of a revision outside of its library')
        infos = self._library_client.get_information()
        return infos.user_rights

    def get_user_rights_in_variant(self):
        """List of all the users that can access this revision for this variant

        Returns
        -------
        list of `{'user': 'MARTIN', 'rights': {'BROWSE': True,...}}`
        """

        if not self._library_client:
            raise ShadoxForbiddenException('Cannot get the rights of a revision outside of its library')
        infos = self._library_client.get_information()
        return infos.user_rights_by_variant[self.variant]

    def get_group_rights(self):
        """List of all the groups that can access this revision

        Returns
        -------
        list of `{'group': 'Managers', 'rights': {'BROWSE': True,...}}`
        """
        if not self._library_client:
            raise ShadoxForbiddenException('Cannot get the rights of a revision outside of its library')
        infos = self._library_client.get_information()
        return infos.group_rights

    def get_group_rights_in_variant(self):
        """List of all the groups that can access this revision for this variant

        Returns
        -------
        list of `{'group': 'Managers', 'rights': {'BROWSE': True,...}}`
        """
        if not self._library_client:
            raise ShadoxForbiddenException('Cannot get the rights of a revision outside of its library')
        infos = self._library_client.get_information()
        return infos.group_rights_by_variant[self.variant]

    def _assert_editable(self):
        if self.status != 'IN_DEFINITION':
            raise ShadoxForbiddenException('Cannot edit a RELEASED revision')

    def _is_dirty(self):
        if not self.publications_by_dataset:
            return False
        return any(any(p and p._dirty for p in pubs) for pubs in self.publications_by_dataset.values())

    def _build_update(self):
        """INTERNAL USE"""
        comments, add_publications, update_publications, move_publications, delete_publications = [], [], [], [], []
        for pubs in filter(None, self.publications_by_dataset.values()):
            for p_update in filter(None, (p._build_updated_publication() for p in pubs if p._updated_publication)):
                if p_update not in update_publications:
                    update_publications.append(p_update)
            for p_update in filter(None, (p._build_updated_publication() for p in pubs if p._add_publication)):
                if p_update not in add_publications:
                    add_publications.append(p_update)
            for p_update in filter(None, (p._build_updated_publication() for p in pubs if p._move_publication)):
                if p_update not in move_publications:
                    move_publications.append(p_update)

            for p_update in filter(None, (p._build_update_comment() for p in pubs if 'comment' in p._touched_keys)):
                if p_update not in comments:
                    comments.append(p_update)

        for p_update in filter(None, (p._build_updated_publication() for p in self._publications_to_remove if p._delete_publication)):
            if p_update not in delete_publications:
                delete_publications.append(p_update)

        if any((comments, add_publications, update_publications, move_publications, delete_publications)):
            return (
                {'publications': add_publications} if add_publications else None,
                {'publications': delete_publications} if delete_publications else None,
                {'publications': move_publications} if move_publications else None,
                {'publications': update_publications} if update_publications else None,
                {'comments': comments} if comments else None,)
        return None

    def _clean_publications(self, publications):
        """INTERNAL USE"""
        for dataset in self.publications_by_dataset.values():
            for pub in publications:
                for p in dataset:
                    if p.permalink:
                        id = p.permalink.split('/')[-1]
                        if id in pub:
                            dataset.remove(p)

        self._update_folder_in_revision_and_tree(self.publications_by_dataset)

    def push_updates(self):
        """
            Sends the updated publication comments to the server
        """
        self._assert_editable()
        updates = self._build_update()

        if not updates:
            log_action(u"# No modification to push on {}".format(self), 4, self._library_client.logger_level)
            return

        log_action(u"# Pushing all modifications on {}".format(self), 2, self._library_client.logger_level)
        (add_publications, delete_publications, move_publications, update_publications, comments) = updates

        if add_publications:
            res = self._library_client._push_revision_update_add(self, add_publications)
            if res:
                if res['publicationsAdded']:
                    for publications in res['publicationsAdded']:
                        for publication in self.publications_by_dataset.get(publications['datasetPath'], []):
                            if publication and publication.variant == publications['variant'] and publication.version == publications['version']:
                                publication._add_publication = False

                if res['publicationsRemoved']:
                   self._clean_publications(res['publicationsRemoved'])

        if delete_publications:
            self._library_client._push_revision_update_delete(self, delete_publications)
            self._publications_to_remove = []

        if update_publications:
            res = self._library_client._push_revision_update_update(self, update_publications)
            if res['publicationsAdded']:
                for publications in res['publicationsAdded']:
                    for publication in self.publications_by_dataset.get(publications['datasetPath'], []):
                        if publication and publication.variant == publications['variant'] and publication.version == publications['version']:
                            publication._updated_publication = False

            if res['publicationsRemoved']:
             self._clean_publications(res['publicationsRemoved'])

        if move_publications:
            self._library_client._push_revision_update_move(self, move_publications)
        if comments:
            self._library_client._push_revision_update_comment(self, comments)

        # Updates were accepted, remove 'dirty' state
        for publications in self.publications_by_dataset.values():
            for p in filter(None, publications):
                p._touched_keys = set()

        log_action(u"# Successfully pushed all modifications.", 2, self._library_client.logger_level)

    def __repr__(self):
        return 'Revision[revision={}, status={}, n_publications={}]'.format(
            self.revision, self.status, '?not loaded?' if self.n_publications == None else self.n_publications
        )

    def _get_old_publication(self, dataset_name, publication_variant):
        old_pub = None
        if dataset_name in self.publications_by_dataset:
            for p in self.publications_by_dataset[dataset_name]:
                if p is not None and p.variant == publication_variant:
                    old_pub = p
                    break
        return old_pub

    def _create_publication(self, old_pub, dataset_name, publication_variant, publication_version, folder_paths):
        return Publication(
            dataset_name, self, {
                'variant': publication_variant,
                'version': publication_version,
                'folders': folder_paths + list(set(old_pub.folders) - set(folder_paths + ['/'])) if old_pub else folder_paths,
                'libraryComment': old_pub.comment if old_pub else None,
                'status': 'UNKNOWN_NOT_FETCHED_YET',
            }, self._library_client.http_helper.api_url
        )

    def _parse_urn(self, urn):
        split = urn.split('/')
        if len(split) != 6:
            raise ValueError('Invalid URN: {}'.format(urn))
        return split[3], split[5].split('-')[0], split[5].split('-')[1]

    ###################
    # ADD PUBLICATION #
    ###################

    def add_publication(self, publication, update = False):
        """Adds a publication to the revision. Note that the publication is not added to the server until the push_updates() method is called.

        Args:
            publication (tuple OR Publication OR str) : The publication to add.
            update (bool, optional): If True, the publication will be updated if it already exists. Defaults to False.
        """
        self._assert_editable()
        return self._add_publication_from_dirty_parameters(publication, update)

    def add_publications(self, publications, update=False):
        """ Adds multiple publications to the revision. Note that the publications are not added to the server until the push_updates() method is called.

        Args:
            publications (list): A list of tuples of the form (dataset_name, publication_variant, publication_version, folder_paths) or Publication objects or dataset_urn.
            update (bool, optional): If True, the publication will be updated if it already exists. Defaults to False.

        Raises:
            exception: If the argument is not a list of tuples of the form (dataset_name, publication_variant, publication_version, folder_paths) or Publication objects or dataset_urn.
        """
        self._assert_editable()
        if isinstance(publications, list):
            return [self._add_publication_from_dirty_parameters(publication, update) for publication in publications]
        else:
            return [self._add_publication_from_dirty_parameters(publications, update)]

    def _add_publication_from_dirty_parameters(self, publication_param, update):
        """
            Prepare parameters before calling `_add_publication_from_clean_parameters`

            Args:
                :param publication_param (multiple choices - mandatory) : Represents a publication to add. Can be:
                    - a Publication object
                    - a ShadoxPublication object
                    - a string representing publication urn
                    - a tuple (Publication,list) containing Publication object and folder path
                    - a tuple (ShadoxPublication,list) containing ShadoxPublication object and folder path
                    - a tuple (str,list) containing publication urn and folder path
                    - a tuple (str,str,str) containing dataset name, publication variant and publication version
                    - a tuple (str,str,str,list) containing dataset name, publication variant, publication version
                        and folder path
                :param update (boolean - mandatory) : Cf `_add_publication_from_clean_parameters`
        """
        # Case Publication
        if isinstance(publication_param, Publication):
            dataset_name = publication_param.dataset_path
            publication_variant = publication_param.variant
            publication_version = publication_param.version
            folder_paths = publication_param.folders
        # Case ShadoxPublication
        elif isinstance(publication_param, ShadoxPublication):
            dataset_name = publication_param._client.dataset_path.split('/')[3]
            publication_variant = publication_param.variant
            publication_version = publication_param.version
            folder_paths = []
        # Case str
        elif isinstance(publication_param, str):
            (dataset_name, publication_variant, publication_version) = self._parse_urn(publication_param)
            folder_paths = ['/']
        # Cases tuple...
        elif isinstance(publication_param, tuple):
            # Case tuple(Publication,list)
            if len(publication_param) == 2 \
                    and isinstance(publication_param[0], Publication) \
                    and isinstance(publication_param[1], list):
                dataset_name = publication_param[0].dataset_path
                publication_variant = publication_param[0].variant
                publication_version = publication_param[0].version
                folder_paths = publication_param[1]
            # Case tuple(ShadoxPublication,list)
            elif len(publication_param) == 2 \
                    and isinstance(publication_param[0], ShadoxPublication) \
                    and isinstance(publication_param[1], list):
                dataset_name = publication_param[0]._client.dataset_path.split('/')[3]
                publication_variant = publication_param[0].variant
                publication_version = publication_param[0].version
                folder_paths = publication_param[1]
            # Case tuple(str,list)
            elif len(publication_param) == 2 \
                    and isinstance(publication_param[0], ShadoxPublication) \
                    and isinstance(publication_param[1], list):
                (dataset_name, publication_variant, publication_version) = self._parse_urn(publication_param[0])
                folder_paths = publication_param[1]
            # Case tuple(str,str,str)
            elif len(publication_param) == 3 \
                    and isinstance(publication_param[0], str) \
                    and isinstance(publication_param[1], str) \
                    and isinstance(publication_param[2], str):
                (dataset_name, publication_variant, publication_version) = publication_param
                folder_paths = ['/']
            # Case tuple(str,str,str,list)
            elif len(publication_param) == 4 \
                    and isinstance(publication_param[0], str) \
                    and isinstance(publication_param[1], str) \
                    and isinstance(publication_param[2], str) \
                    and isinstance(publication_param[3], list):
                (dataset_name, publication_variant, publication_version, folder_paths) = publication_param
            else:
                raise ValueError(
                    'Publication tuple must either be a 2-elements tuple (Publication object, folder_paths) '
                    'or a 2-elements tuple (dataset_urn, folder_paths) '
                    'or a 3-elements tuple (dataset_name, publication_variant, publication_version) '
                    'or a 4-elements tuple (dataset_name, publication_variant, publication_version, folder_paths)')
        else:
            raise ValueError('Publication must either be Publication object or a dataset_urn or a tuple')
        return self._add_publication_from_clean_parameters(
            dataset_name=dataset_name,
            publication_variant=publication_variant,
            publication_version=publication_version,
            folder_paths=folder_paths,
            update=update
        )

    def _add_publication_from_clean_parameters(self,
                                               dataset_name,
                                               publication_variant,
                                               publication_version,
                                               folder_paths,
                                               update):
        """
        Private method to add a single publication to the revision.
        REQUIRES CLEAN PARAMETERS - cf args
        Note that the publication is not added to the server until the push_updates() method is called.

        Args:
            :param dataset_name (str - mandatory)
            :param publication_variant (str - mandatory)
            :param publication_version (str - mandatory)
            :param folder_paths (list - mandatory) : List of strings representing publication location in library's folder hierarchy (ex: ["/a","/b","/c"])
            :param update (boolean - mandatory) : If True, the publication will be updated if it already exists.
        """

        if dataset_name not in self.publications_by_dataset:
            self.publications_by_dataset[dataset_name] = []

        old_pub = self._get_old_publication(dataset_name, publication_variant)
        new_pub = self._create_publication(old_pub, dataset_name, publication_variant, publication_version,
                                           folder_paths)

        intersection = set(new_pub.folders).intersection(set(old_pub.folders)) if old_pub else new_pub.folders
        if intersection == set(new_pub.folders) and old_pub.name == new_pub.name:
            raise ValueError(
                u"Publication '{}:{}-{}' already exists in folder(s) '{}'".format(dataset_name, new_pub.variant,
                                                                                  new_pub.name, new_pub.folders))

        moved = set(intersection) != set(new_pub.folders)
        if intersection and old_pub:
            if update:
                self.publications_by_dataset[dataset_name].remove(old_pub)
                moved = True
                log_action(
                    u"# Publication '{}' already exists in folder(s) '{}'. Updating and keeping old folders...".format(
                        old_pub, old_pub.folders), 2, self._library_client.logger_level)
            else:
                raise ValueError(
                    u"Publication '{}:{}-{}' already exists in folder(s) '{}', use flag update=True to update to version {}".format(
                        dataset_name, new_pub.variant, old_pub.name, intersection, new_pub.name))
        new_pub._update_publication(new_pub.folders, 'move' if moved else 'add', update=update)
        self.publications_by_dataset[dataset_name].append(new_pub)
        self._update_folder_in_revision_and_tree(self.publications_by_dataset)
        if not old_pub:
            self.n_publications += 1
        return new_pub

    ######################
    # UPDATE PUBLICATION #
    ######################

    def update_publication(self, publication):
        """ Updates a publication in the revision. Note that the publication is not updated on the server until the push_updates() method is called.

        Args:
            publication : A tuple of the form (dataset_name, publication_variant, publication_version) or a Publication object or a dataset_urn
        """
        self._assert_editable()
        return self._update_publication_from_dirty_parameters(publication)

    def update_publications(self, publications):
        """ Updates multiple publications in the revision. Note that the publications are not updated on the server until the push_updates() method is called.

        Args:
            publications (list): A list of tuples of the form (dataset_name, publication_variant, publication_version) or Publication objects or dataset_urns

        Raises:
            exception: If the argument is not a list of tuples of the form (dataset_name, publication_variant, publication_version) or Publication objects or dataset_urns
            ValueError: If the publication does not exist
        """
        self._assert_editable()
        if isinstance(publications, list):
            return [self._update_publication_from_dirty_parameters(publication) for publication in publications]
        else:
            return [self._update_publication_from_dirty_parameters(publications)]

    def _update_publication_from_dirty_parameters(self, publication_param):
        """
            Prepare parameters before calling `_update_publication_from_clean_parameters`

            Args:
                :param publication_param (multiple choices - mandatory) : Represents a publication to update. Can be:
                    - a Publication object
                    - a ShadoxPublication object
                    - a string representing publication urn
                    - a tuple (str,str,str) containing dataset name, publication variant and publication version
        """
        if isinstance(publication_param, Publication):
            (dataset_name, publication_variant, publication_version) = (publication_param.dataset_path, publication_param.variant, publication_param.version)
        elif isinstance(publication_param, ShadoxPublication):
            (dataset_name, publication_variant, publication_version) = (publication_param._client.dataset_path.split('/')[3], publication_param.variant, publication_param.version)
        elif isinstance(publication_param, tuple):
            if len(publication_param) != 3:
                raise ValueError('Publication tuple must have 3 elements: (dataset_name, publication_variant, publication_version)')
            (dataset_name, publication_variant, publication_version) = publication_param
        elif isinstance(publication_param, str):
            (dataset_name, publication_variant, publication_version) = self._parse_urn(publication_param)
        else:
            raise ValueError('Publication must be a tuple of the form (dataset_name, publication_variant, publication_version) or a Publication object or a dataset_urn')
        return self._update_publication_from_clean_parameters(dataset_name, publication_variant, publication_version)

    def _update_publication_from_clean_parameters(self, dataset_name, publication_variant, publication_version):
        """
        Private method to update a single publication to the revision.
        REQUIRES CLEAN PARAMETERS - cf args
        Note that the publication is not added to the server until the push_updates() method is called.

        Args:
            :param dataset_name (str - mandatory)
            :param publication_variant (str - mandatory)
            :param publication_version (str - mandatory)
        """
        if dataset_name not in self.publications_by_dataset:
            raise ValueError('Dataset {} not found in revision {} when trying to update the publication {}'
                             .format(dataset_name, self.revision, (publication_variant, publication_version)))

        old_pub = self._get_old_publication(dataset_name, publication_variant)
        if old_pub is None:
            raise ValueError('Dataset {} / Publication {} not found or restricted'.format(dataset_name, publication_variant))
        new_pub = self._create_publication(old_pub, dataset_name, publication_variant, publication_version, old_pub.folders)

        new_pub._update_publication(new_pub.folders, 'update')
        self.publications_by_dataset[dataset_name].remove(old_pub)
        self.publications_by_dataset[dataset_name].append(new_pub)
        self._update_folder_in_revision_and_tree(self.publications_by_dataset)
        return new_pub

    ######################
    # DELETE PUBLICATION #
    ######################

    def delete_publication(self, publication):
        """ Deletes a publication from the revision. Note that the publication is not deleted from the server until the push_updates() method is called.

        Args:
            publication (tuple OR Publication OR str): A tuple of the form (dataset_name, publication_variant) or a Publication object or a dataset_urn
        """
        self._assert_editable()
        self.delete_publications([publication])

    def delete_publications(self, publications):
        """ Deletes multiple publications from the revision. Note that the publications are not deleted from the server until the push_updates() method is called.

        Args:
            publications (list): A list of tuples of the form (dataset_name, publication_variant) OR a list of Publication objects OR a list of dataset_urns

        Raises:
            exception: If the argument is not a list of tuples of the form (dataset_name, publication_variant) OR a list of Publication objects OR a list of dataset_urns
            ValueError: If the publication does not exist
        """
        self._assert_editable()
        for publication in publications:
            if isinstance(publication, Publication):
                (dataset_name, publication_variant) = (publication.dataset_path, publication.variant)
            elif isinstance(publication, ShadoxPublication):
                (dataset_name, publication_variant) = (publication._client.dataset_path.split('/')[3], publication.variant)
            elif isinstance(publication, tuple):
                if len(publication) != 2:
                    raise ValueError('Publication tuple must have 2 elements: (dataset_name, publication_variant)')
                (dataset_name, publication_variant) = publication
            elif isinstance(publication, str):
                (dataset_name, publication_variant, _) = self._parse_urn(publication)
            else:
                raise ValueError('Publication must be a tuple of the form (dataset_name, publication_variant) or a Publication object or a dataset_urn')

            if dataset_name not in self.publications_by_dataset:
                raise ValueError('Dataset {} not found in revision {} when trying to delete the publication {}'.format(dataset_name, self.revision, publication))

            old_pub = self._get_old_publication(dataset_name, publication_variant)
            if old_pub is None:
                raise ValueError(
                    'Dataset {} / Publication {} not found or restricted'.format(dataset_name, publication_variant))

            self.publications_by_dataset[dataset_name].remove(old_pub)
            if not self.publications_by_dataset[dataset_name]:
                del self.publications_by_dataset[dataset_name]
            self._update_folder_in_revision_and_tree(self.publications_by_dataset)
            old_pub._update_publication([], 'delete')
            self._publications_to_remove.append(old_pub)
            self.n_publications -= 1
