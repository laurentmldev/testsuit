from .publication import BasePublication
from .utils import validate_path_param

class Publication(BasePublication):
    """A publication as referenced in a library revision

    Inherits all the attributes of `BasePublication`

    For more information that is not available through the library,
    a dataset ShadoxPublication can be instantiated:
    `Publication.build_client().get_current_publication()`

    Attributes
    ----------
    dataset_path : str
    folders : list of str
    comment : str
        Can be modified
    """

    def __init__(
        self,
        dataset_path,
        parent_revision,
        json_data,
        api_url
    ):
        super(Publication, self).__init__(json_data, api_url)
        self._touched_keys = None
        self._revision = parent_revision
        self.dataset_path = dataset_path
        self.comment = json_data['libraryComment']
        self.folders = json_data.get('folders', None) if json_data.get('folders', None) else ['/']
        self.folders.sort()

        # Booleans used for the API request state
        self._updated_publication = False
        self._add_publication = False
        self._move_publication = False
        self._delete_publication = False

        self._updatable_keys = set(['comment', 'folders'])
        self._touched_keys = set()

    def build_client(self):
        """Connect directly to the publication through its dataset

        Use this method to get more information about the publication,
        and to access the parameters in its snaphsot.

        Returns
        -------
        ShadoxClient
            A client connected to this publication and dataset
        """
        return self._revision._library_client._open_publication(
            '/dataset/{}/publication/{}-{}'.format(
                self.dataset_path, self.variant, self.name
            )
        )

    def _assert_editable(self):
        self._revision._assert_editable()

    def _build_update_comment(self):
        """INTERNAL USE"""
        if 'comment' in self._touched_keys:
            return {
                'datasetPath': self.dataset_path,
                'publicationName': '{}-{}'.format(self.variant, self.name),
                'comment': self.comment,
            }
        return None

    def _build_updated_publication(self):
        """INTERNAL USE"""
        if any([self._updated_publication, self._add_publication, self._move_publication, self._delete_publication]):
            return {
                'datasetPath': self.dataset_path,
                'publicationName': '{}-{}'.format(self.variant, self.name),
                'version': self.name,
                'variant': self.variant,
                'publicationFolders': self.folders
            }
        return None

    def _before_setattr(self, value, key):
        #Check if the value entered correspond to the whished type for this attribute
        if key == 'folders':
            revision = self._revision
            folders = value
            for folder in folders:
                validate_path_param(folder)

            old_pub = revision._get_old_publication(self.dataset_path, self.variant)
            if old_pub and old_pub._delete_publication:
                raise ValueError('Publication already deleted')

            intersection = set(folders).intersection(set(old_pub.folders)) if old_pub else folders
            if intersection == set(folders):
                raise ValueError(u"Publication '{}:{}-{}' already exists in folder(s) '{}'".format(self.dataset_path, self.variant, self.name, folders))

    def _after_setattr(self, value, key):
        if key == 'folders':
            if self._revision.publications_by_dataset is None:
                return
            self._update_publication(value, 'move')
            self._revision._update_folder_in_revision_and_tree(self._revision.publications_by_dataset)

    def update_comment(self, comment):
        """Deprecated, use `p.comment = comment`

        Parameters
        ----------
        comment : str
        """
        self.comment = comment
        
    def _update_publication(self, publication_folders, state, update=False):
        """INTERNAL USE"""
        if self.folders != publication_folders:
            self.folders = publication_folders
        if update:
            self._updated_publication = True
        if state == 'update':
            self._updated_publication = True
        elif state == 'add':
            self._updated_publication = False
            self._delete_publication = False
            for p in self._revision._publications_to_remove:
                if p.variant == self.variant and p.name == self.name:
                    self._revision._publications_to_remove.remove(p)
            self._add_publication = True
        elif state == 'move':
            self._delete_publication = False
            for p in self._revision._publications_to_remove:
                if p.variant == self.variant and p.name == self.name:
                    self._revision._publications_to_remove.remove(p)
            self._move_publication = True
        elif state == 'delete':
            if not self._add_publication:
                self._delete_publication = True
            self._updated_publication = False
            self._add_publication = False
            self._move_publication = False

    def __repr__(self):
        return u'Publication[' \
            + (('dataset=' + self.dataset_path) if self.dataset_path else '') \
            + ((',variant=' + self.variant) if self.variant else '') \
            + ((',name=' + self.name) if self.name else '') \
            + ((',label=' + self.label) if self.label else '') \
            + ((',status=' + self.status_repr()) if self.status else '') \
            + ']'
