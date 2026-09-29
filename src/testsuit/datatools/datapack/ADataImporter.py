

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

    def isClean(self):
        """Return True if evcerything is clean tagged, no local modifs etc"""
        if not self.getChanges(testClean=True):
            print("ERROR: Given folder has local modifs: '"+self._targetDir+"'")
            return False
        
        if not self.getTag(testTag=True):
            print("ERROR: Given folder is not a tag: '"+self._targetDir+"'")
            return False
            
        return True

