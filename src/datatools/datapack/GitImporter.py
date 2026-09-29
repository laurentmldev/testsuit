
import git,os,sys
import subprocess
from datatools.datapack.ADataImporter import ADataImporter
from git import RemoteProgress

from pathlib import Path

class GitImporter(ADataImporter):

    def __init__(self,targetDir,url=None,branch=None):
        super().__init__(targetDir,url,branch)
        
    def getTag(self,testTag=False):
        g = git.Git(self._targetDir)
        logs = g.log('--simplify-by-decoration','--pretty=format:%C(auto)%h | %ai | %d')
        latestLog=logs.split('\n')[0]
        if testTag==True:
            if 'tag:' not in latestLog:
                return False
            else:
                return True 
            
        return latestLog


    def getChanges(self,testClean=False):
        g = git.Git(self._targetDir)
        changes = g.status('--porcelain').split('\n')
        if len(changes)==1 and len(changes[0])==0:
            changes=[]     
        if testClean:
            if len(changes)==0:
                return True
            else:
                print("\n".join(changes))
                return False
        
        return changes

    def checkVersion(self,expectedVersion):
        if not self.getChanges(testClean=True):
            print("ERROR: Given folder has local modifs: '"+self._targetDir+"'")                
            return False
        
        if not self.getTag(testTag=True):
            print("ERROR: Given folder is not a Git tag: '"+self._targetDir+"'")
                
            return False
        
        tag=self.getTag()
        if "tag: "+expectedVersion not in tag:
            return False
        
        return True

    def retrieve(self):

        print("\n### Git Dataset Importer ###")
        print("# Target Folder="+str(self._targetDir))
        print("# Repo="+str(self._remotePath))
        print("# Branch="+str(self._versionId))
        print("############################")
        
        # hangs out when expecting credentials to be filled by user
        # repo = git.Repo.clone_from(self._remotePath,self._targetDir,branch=self._versionId,single_branch=True)
                
        my_env = os.environ.copy()
        p = subprocess.Popen("git clone -v "+self._remotePath+" --single-branch -b "+self._versionId+" "+self._targetDir, shell=True, stdout=sys.stdout, stderr=sys.stderr,env=my_env)
        retval = p.wait()
        if retval!=0:
            print("ERROR: Git command to retrieve repo faild, returning a non-zero value: "+str(retval))
            return False
        
        return True            

