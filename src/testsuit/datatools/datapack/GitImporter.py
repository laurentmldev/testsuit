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


import git,os,sys
import subprocess
from testsuit.datatools.datapack.ADataImporter import ADataImporter
from testsuit.datatools.datapack.importers import sort_versions
from testsuit.datatools.datapack.procs import run_logged


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

    def listVersions(self):
        """Tags of the remote repository, newest first, then its branches."""
        def remote(kind):
            out = subprocess.run(["git", "ls-remote", "--refs", kind, self._remotePath], capture_output=True,
                                 text=True, timeout=20, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            if out.returncode != 0:
                raise RuntimeError(out.stderr.strip() or f"git ls-remote failed on '{self._remotePath}'")
            return [line.split("\t", 1)[1].split("/", 2)[2] for line in out.stdout.splitlines() if "\t" in line]
        return sort_versions(remote("--tags")) + sorted(remote("--heads"))

    def retrieve(self):

        print("\n### Git Dataset Importer ###")
        print("# Target Folder="+str(self._targetDir))
        print("# Repo="+str(self._remotePath))
        print("# Branch="+str(self._versionId))
        print("############################")
        
        # git never prompts (no terminal here): credentials come from the user's git setup
        retval, tail = run_logged(["git", "clone", "-v", "--single-branch", "-b", str(self._versionId),
                                   str(self._remotePath), str(self._targetDir)])
        if retval != 0:
            print(f"ERROR: git clone of '{self._remotePath}' at '{self._versionId}' failed (exit status {retval})"
                  + (f": {tail.splitlines()[-1]}" if tail else ""))
            return False

        return True            

