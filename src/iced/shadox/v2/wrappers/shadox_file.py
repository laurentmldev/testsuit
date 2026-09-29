#!/usr/bin/env python
# -*- coding: utf-8 -*-

# THIS DOCUMENT AND ITS CONTENTS ARE PROPERTY OF ARIANEGROUP.
# IT SHALL NOT BE COMMUNICATED TO ANY THIRD PARTY WITHOUT THE OWNER'S
# WRITTEN CONSENT | ARIANEGROUP SAS - ALL RIGHTS RESERVED.

from os import path
import hashlib
from ..exceptions import ShadoxFileNotFoundException, ShadoxInternalException

class ShadoxFile(object):
    def __init__(self, file_path=None):
        self._identifier = None
        self._size = None
        self._name = None
        self._parameter = None
        self._value_key = None
        self._initial_data = None
        self._path = file_path

        if self._path is not None:
            # Check the file exist on disk
            if not (path.exists(self._path) and path.isfile(self._path)):
                raise ShadoxFileNotFoundException("File {} not found on disk".format(file_path))

            self._name = path.basename(file_path)
            self._size = path.getsize(file_path)

    @classmethod
    def _init_from_shadox(cls, value_key, shadox_data):
        """INTERNAL USE ONLY - Private method to init ShadoxFile from shadox data"""
        shadox_file = cls(None)
        shadox_file._value_key = value_key
        shadox_file._initial_data = shadox_data
        if shadox_data:
            shadox_file._identifier = shadox_data['identifier'] if 'identifier' in shadox_data else None
            shadox_file._size = shadox_data['size'] if 'size' in shadox_data else None
            shadox_file._name = shadox_data['name'] if 'name' in shadox_data else None
        return shadox_file

    def download(self, download_path=None, force_download=False, export_hash=False):
        # We need a parameter linked to the ShadoxFile to download
        if not self._parameter or not self._identifier:
            return None

        # Get the path to download the file
        self._path = self._parameter.client.get_file_path_for_download(self._initial_data, download_path)

        # Get the local hash
        try:
            local_hash = self.compute_local_hash()
        except ShadoxFileNotFoundException:
            local_hash = None

        # Get the server hash, used to compare to the local hash
        # Used also to download the file hashes for the parameter value
        # Even if the local hash is None, we will need the hashes for later
        server_hash = self.get_server_hash()

        # If the local hash is not defined, means the file is not downloaded
        # Or if the hash from the server is different from the local
        # Or if the download is forced,
        # The Shadox client asks for API for the file
        if local_hash is None or local_hash != server_hash or force_download:
            # Download the file
            new_server_hash = self._parameter.client.download_file(
                self._parameter.id,
                self._value_key,
                self._initial_data,
                self._path,
                export_hash
            )
            if new_server_hash:
                new_local_hash = self.compute_local_hash()
                if new_server_hash != new_local_hash:
                    raise ShadoxInternalException("Hash mismatch between server and downloaded file: '{}' vs '{}'".format(new_server_hash, new_local_hash))
                if server_hash and new_server_hash != server_hash:
                    raise ShadoxInternalException("Hash mismatch between preliminary information from server and downloaded file: '{}' vs '{}'".format(server_hash, new_server_hash))

        return self.get_path()

    def get_fileidentifier(self):
        return self._identifier

    def get_filesize(self):
        return self._size

    def get_filename(self):
        return self._name

    def get_path(self):
        return self._path

    def get_server_hash(self):
        """Get the server hash, it can trigger a fetch request if hashes not fetched for parameter value"""
        if not self._parameter or not self._identifier:
            return None

        # Ask the parameter to download file hashes if not done yet
        server_file_hashes = self._parameter.download_files_hash(self._value_key)
        return server_file_hashes[self._identifier] if self._identifier in server_file_hashes else None

    def compute_local_hash(self):
        """Get the hash of the local file"""
        if self._is_local_file_available():
            with open(self._path, 'rb') as local_file:
                return hashlib.sha256(local_file.read()).hexdigest()

        return None

    def compute_local_size(self):
        """Compute the file size on disk"""
        if self._path is not None:
            return path.getsize(self._path)

        return None

    @property
    def empty(self):
        return self._name is None

    def _to_shadox_value(self):
        """INTERNAL USE ONLY - Private method to get the shadox representation of a ShadoxFile"""
        if self._identifier is None:
            return None

        return dict(
            name=self._name,
            size=self._size,
            identifier=self._identifier
        )

    def _bind_parameter(self, parameter, value_key):
        """INTERNAL USE ONLY - Private method to link the ShadoxFile and the Parameter"""
        self._parameter = parameter
        self._value_key = value_key

    def _upload(self):
        """INTERNAL USE ONLY - Private method to upload the file to shadox"""
        self._identifier = self._parameter.client.upload_file(self._parameter.id, self._value_key, self._path, self.compute_local_hash())
        self._size = self.compute_local_size()
        self._initial_data = self._to_shadox_value()

    def _is_present_on_shadox(self):
        """Check the file has an identifier, it means he is stored on shadox"""
        return self._identifier is not None

    def _is_local_file_available(self):
        """Check the local file is available"""
        if self._path is None:
            return False

        # Check the file is available on disk
        if self._path and not (path.exists(self._path) and path.isfile(self._path)):
            raise ShadoxFileNotFoundException("File '{}' not found on disk".format(self._path))

        return True

    def __repr__(self):
        if self._name is None:
            return "No file"
        return u"{} - {} - {}".format(self._name, self._size, self._identifier)
