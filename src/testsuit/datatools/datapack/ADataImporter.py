# Copyright (C) 2026 Laurent Millet-Lacombe
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of testsuit.
# testsuit is free software: you can redistribute it and/or modify it under the terms of the GNU Affero
# General Public License as published by the Free Software Foundation, either version 3 of the License,
# or (at your option) any later version.
# testsuit is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General
# Public License for more details.
# You should have received a copy of the GNU Affero General Public License along with testsuit. If not,
# see <https://www.gnu.org/licenses/>.



import abc

class ADataImporter(metaclass=abc.ABCMeta):

    def __init__(self,targetDir,remotePath,versionId):
        self._targetDir=targetDir
        self._remotePath=remotePath
        self._versionId=versionId

    @abc.abstractmethod
    def getTag(self,testTag=False):
        """Retrieve Dataset tag or version ID
            
            Parameters:
                targetDir (str): path to local dir if dataset
                testTag (bool): ensure it is a tagged version (when applicable)

            Returns:
                tag identifier, or True|False if testTag=True

            """
        ...

    @abc.abstractmethod
    def getChanges(self,testClean=False):
        """Detect if repo has local modifs"""
        ...

    @abc.abstractmethod
    def checkVersion(self,expectedVersion):
        """Ensure repo version is provided one"""
        ...

    @abc.abstractmethod
    def retrieve(self):
        """Retrieve repo from reference location"""
        ...

    def listVersions(self):
        """Versions available at the reference location (``remotePath``), newest first when known.

        Optional: an importer that cannot tell returns None (the default). The sequencer's Datapacks page
        offers these versions when updating a baseline.
        """
        return None

    def isClean(self):
        """Return True if evcerything is clean tagged, no local modifs etc"""
        if not self.getChanges(testClean=True):
            print("ERROR: Given folder has local modifs: '"+self._targetDir+"'")
            return False
        
        if not self.getTag(testTag=True):
            print("ERROR: Given folder is not a tag: '"+self._targetDir+"'")
            return False
            
        return True

