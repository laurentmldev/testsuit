# -*- coding: UTF-8 -*-

import sys,re,shutil,zipfile,tarfile,os,os.path

import argparse

from pathlib import Path

from datatools.datapack.ADataImporter import ADataImporter

from iced.shadox.v2 import ShadoxClient, ShadoxFile 
from iced.shadox.v2.factory.parameters import   PARAMETER_TYPE_SCALAR,\
                                                PARAMETER_TYPE_NOTEBOOK,\
                                                PARAMETER_TYPE_DOCUMENT,\
                                                PARAMETER_TYPE_DATAFRAME,\
                                                PARAMETER_TYPE_VECTOR,\
                                                PARAMETER_TYPE_MATRIX
from iced.shadox.v2.factory.values import VALUE_TYPE_FILE



SHADOX_API_KEYS_FILE=str(Path.home())+os.sep+".shadox"+os.sep+"shadox_api_keys.json"
METATAG=".metadata"
class _HelpParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write("Input Arguments Error : "+message)
        sys.exit(1)

class ShadoxImporter(ADataImporter):
    
    def __init__(self,targetDir,keys_prefix=None,shadox_url=None,dataset_urn=None):
        super().__init__(targetDir=targetDir,remotePath=shadox_url,versionId=dataset_urn)
        self._api_keys_file=SHADOX_API_KEYS_FILE
        self._keys_prefix=keys_prefix
        self._dicoFileHandle=None

        if not os.access(self._api_keys_file,os.R_OK):
            print("ERROR: Shadox API Key file not reachable: '"+self._api_keys_file+"'")
            sys.exit(1)
        
        self._datasetName=keys_prefix
        if self._keys_prefix == None:
            self._datasetName=self._normalizeDicoKey(re.sub(r'.*/([^/]+)@.*',lambda m:m.group(1),dataset_urn))

	# tmp implementation, to be completed
    def getTag(self,testTag=False):

        if testTag:
            return self.getChanges(testClean=True)
        else:
                
            datasetDicoFile=self._targetDir+os.sep+"dataset.dico"
            with open(datasetDicoFile,"r") as f:
                foundVersion=None
                for line in f:
                    if ".version=" in line:
                        foundVersion=re.sub(r".*\.version=","",line)
                        return foundVersion
        return None
        
	# feature not really possible
    def getChanges(self,testClean=False):
        changedFiles=[]
        importDate=None
        datasetDicoFile=self._targetDir+os.sep+"dataset.dico"
        with open(datasetDicoFile,"r") as f:
            foundVersion=None
            for line in f:
                if ".importdateSec=" in line:
                    importDate=re.sub(r".*\.importdateSec=","",line)

        for (root, dirs, files) in os.walk(self._targetDir):
            for file in files:
                fullPath=os.path.join(root,file)
                lastChangeDate=os.path.getmtime(fullPath)
                if lastChangeDate-float(importDate)>1:
                    if testClean==True:
                        return False
                    else:
                        changedFiles+=[fullPath]
                    
        if testClean:
            return True
        else:
            return changedFiles

	# tmp implementation, to be completed
    def checkVersion(self,expectedVersion):

        expectedVersion=re.sub(".*variant/","",expectedVersion)
        foundVersion=None
        datasetDicoFile=self._targetDir+os.sep+"dataset.dico"
        with open(datasetDicoFile,"r") as f:
            for line in f:
                if ".version=" in line:
                    foundVersion=re.sub(".*\.version=.*variant\.","",line).strip()
                    break               
        
        return foundVersion==expectedVersion and self.getChanges(testClean=True)


    def retrieve(self):

        print("\n### Shadox Dataset Importer ###")
        print("# Target Folder="+str(self._targetDir))
        print("# Shadox URL="+str(self._remotePath))
        print("# Dataset URN="+str(self._versionId))
        print("# Dataset Name="+str(self._datasetName))
        print("############################")
        
        success=self._importFromShadox(self._datasetName,self._remotePath,self._versionId,self._api_keys_file,self._targetDir)
        if not success:
            print("ERROR: Shadox import failed, sorry.")
            return False
        
        return True

    def _writeKeyFile(self,key,value):
        if self._dicoFileHandle==None:
            Path(self._targetDir).mkdir(parents=True, exist_ok=False)
            self._dicoFileHandle=open(self._targetDir+os.sep+"dataset.dico","a")

        if value:
            self._dicoFileHandle.write(str(key)+"="+str(value)+"\n")
        else:
            # used for comments typically
            self._dicoFileHandle.write(str(key)+"\n")

    def _extractMetaData(self,shadoxParamDefinition,paramKey, datasetName):
        
        paramFullKey=datasetName+paramKey
        #print("\n# --- def : "+str(shadoxParamDefinition))

    # content value
        
        # contentType
        if 'contentType' in shadoxParamDefinition:

            defcontents=shadoxParamDefinition['contentType']

            paramtype=defcontents['type']
            self._writeKeyFile(paramFullKey+METATAG+".type",paramtype)

            # ALLOWED RANGE    
            if 'allowedRange' in defcontents:
                allowedRange=defcontents['allowedRange']
                self._writeKeyFile(paramFullKey+METATAG+".allowedRange",allowedRange)
            
            # SUBTYPE
            if 'subtype' in defcontents:
                subtype=defcontents['subtype']
                self._writeKeyFile(paramFullKey+METATAG+".subtype",subtype)        
            
            # ALLOWED SET
            if 'allowedSet' in defcontents:
                allowedSet=defcontents['allowedSet']
                self._writeKeyFile(paramFullKey+METATAG+".allowedSet",allowedSet)
            
            # RAW UNIT
            if 'rawUnit' in defcontents:
                rawUnit=shadoxParamDefinition['contentType']['rawUnit']
                # TODO : handle special chars
                self._writeKeyFile(paramFullKey+METATAG+".rawUnit",rawUnit)    
            
            # FORMATTED UNIT
            if 'formattedUnit' in defcontents:        
                formattedUnit=defcontents['formattedUnit']
                # TODO : handle special chars
                self._writeKeyFile(paramFullKey+METATAG+".formattedUnit",formattedUnit)
            
            # QUANTITY
            if 'quantity' in defcontents:        
                quantity=defcontents['quantity']
                self._writeKeyFile(paramFullKey+METATAG+".quantity",quantity)    
        
        else:
            # document
            if 'documentType' in shadoxParamDefinition:
                defcontents=shadoxParamDefinition['documentType']

                # document
                if 'subtype' in defcontents:
                    paramtype=defcontents['subtype']
                    self._writeKeyFile(paramFullKey+METATAG+".type",paramtype)

            # notebook
            else:
                # nothing special as metadata for notebook
                pass



    def _extractDataframeMetadata(self,columnDef,paramKey, datasetName):
        coltitle=columnDef['title']
        self._extractMetaData(columnDef,paramKey+"."+coltitle,datasetName)
        return coltitle

    def _normalizeDicoKey(self,name):
        return name.replace(".","-").replace("=","[EQ]").replace("/",".").replace(" ","_")

    def _getKeyStr(self,paramKey,valueKind):
        if valueKind != "nominal":
            return paramKey+"."+valueKind
        else:
            return paramKey

    # Normalize matrix output import for simu 
    def _normalizeMatrixDicoValue(self,matrixValue):
        if matrixValue.startswith("["):
            matrixValue = matrixValue.replace("[[", "{").replace("]]","}")
            matrixValue = re.sub(",$","", matrixValue  )
            matrixValue = re.sub(r'\b\s+',",", matrixValue  )
            matrixValue = "{"+matrixValue+"};"
            
        return matrixValue


    # download file and return corresponding local full path
    def _downloadfile(self,shadoxFileObject, paramKey, targetpath,valueKind,rowId,colName):    
            
            basepath=targetpath+os.sep+paramKey[1:]+os.sep+valueKind

            finalpath=basepath+os.sep+shadoxFileObject.get_filename()
            self._writeKeyFile("#  From "+shadoxFileObject.get_filename())
            
            # if all files are downloaded in same directory then Shadox plugin just keep latest one ... (issue still to do)
            # so we download them in separate dirs and then regroup them
            # first character of paramKey is a dot '.' which we don't need for the corresponding directory
            # rowId start from 1 in Shadox UI, so we make it also starting from one in the downloaded data
            if rowId==None:
                downloadpath=basepath+os.sep+"0"
            else:
                downloadpath=basepath+os.sep+str(rowId+1)+os.sep

            if colName!=None:
                downloadpath+=os.sep+str(colName)
            Path(downloadpath).mkdir(parents=True, exist_ok=False)
            
            downloadedfilepath=shadoxFileObject.download(downloadpath+os.sep+shadoxFileObject.get_filename())
            os.rename(downloadedfilepath,finalpath)
            os.rmdir(downloadpath)
            return finalpath            


    def _isArchiveFile(self,fileName) :
        return fileName.endswith(".zip")\
            or fileName.endswith(".tgz")\
            or fileName.endswith(".tar.gz")\
            or fileName.endswith(".bz2")

    def _unpack_archive(self,archPath, targetPath) :

        if os.path.isdir(targetPath) or os.path.isfile(targetPath) :
            sys.stderr.write("ERROR while extracting shadox-imported file '"+archPath+"' : target path '"+targetPath+"' already exists, aborting.")
            sys.exit(1)

        shutil.rmtree(targetPath)#,onerror=del_rw)

        if archPath.endswith('.zip'):
            opener, mode = zipfile.ZipFile, 'r'
        elif archPath.endswith('.tar.gz') or archPath.endswith('.tgz'):
            opener, mode = tarfile.open, 'r:gz'
        elif archPath.endswith('.tar.bz2') or archPath.endswith('.tbz'):
            opener, mode = tarfile.open, 'r:bz2'
        else: 
            sys.stderr.write("ERROR: while extracting shadox-imported file '"+archPath+"' : unknown format, aborting sorry.")
            sys.exit(1)

        file = opener(archPath, mode)
        try: file.extractall(targetPath)
        finally: file.close()

    def _extractScalarValue(self,shadoxParam,paramKey, datasetName, downloadsPath,valueKind):

        paramFullKey=datasetName+paramKey
        try :
            valueObj = shadoxParam.value_kinds[valueKind].value
            valueStr = str(valueObj).replace("\n"," ").replace("None","")
            key=self._getKeyStr(paramFullKey,valueKind)

            # if param is a file we download it
            if shadoxParam.definition['contentType']['type']==VALUE_TYPE_FILE:    
                downloadedFilePath=self._downloadfile(valueObj, paramKey,downloadsPath,valueKind,None,None)
                valueStr=valueObj.get_filename()
                self._writeKeyFile(key+".fil=",downloadedFilePath)
                # if file is an archive we unpack it
                if self._isArchiveFile(downloadedFilePath) :
                    downloadPath=os.path.dirname(downloadedFilePath)
                    archDirName=os.path.basename(valueObj.get_filename()).replace(".zip","").replace(".tgz","").replace(".tar.gz","")
                    unpackDirName=downloadPath+os.sep+archDirName
                    self._unpack_archive(downloadedFilePath,unpackDirName)
                    self._writeKeyFile(key+".unpack_path",unpackDirName)
                    
            self._writeKeyFile(key,valueStr)
            
        except Exception as e:
            if "NoneType found" not in str(e):
                sys.stderr.write("Inconsistant Shadox param value for "+str(paramFullKey)+"."+str(valueKind)+ " : "+str(e))
                sys.exit(1)

    def _extractVectorValue(self,shadoxParam,paramKey, datasetName, downloadsPath,valueKind):

        paramFullKey=datasetName+paramKey
        try :
            size=0
            valueObj = shadoxParam.values.__getattr__(valueKind)
            valueStr = str(valueObj).replace("\n"," ").replace("None","")
            
            key=self._getKeyStr(paramFullKey,valueKind)
                
            if shadoxParam.definition['contentType']['type']==VALUE_TYPE_FILE:
                rowId=0    
                valueStr=""
                for curValueObj in valueObj:
                    valueStr+=self._downloadfile(curValueObj, paramKey,downloadsPath,valueKind,rowId,None)+";"
                    rowId+=1
                size=rowId
            else:
                valueStr=re.sub(r"u'([^']*)'\s*",lambda m:m.group(1)+";",valueStr)
                valueStr=valueStr.replace("[","").replace("]","")
                size=len(valueStr.split(" "))
                
            self._writeKeyFile(key,valueStr)    
            self._writeKeyFile(key+METATAG+".size",size)
                
        except Exception as e:
            sys.stderr.write("Inconsistant Shadox param value for "+paramFullKey+"."+valueKind+ " : "+str(e))
            sys.exit(1)

    def _extractMatrixValue(self,shadoxParam,paramKey, datasetName, downloadsPath,valueKind):
        paramFullKey=datasetName+paramKey
        try :
            size=0
            rowId=0
            valueObj = shadoxParam.values.__getattr__(valueKind)
            valueStr = ""
            #self._writeKeyFile(paramFullKey+METATAG+".dimension",valueObj.shape)
            key=self._getKeyStr(paramFullKey,valueKind)
            
            #Get the row of matrice 
            for rowCurValueObj in valueObj:
                        
                if shadoxParam.definition['contentType']['type']==VALUE_TYPE_FILE:
                    columnId = 1
                    # Get each element of row by column  
                    for curValue in rowCurValueObj.flat:
                        valueStr+=self._downloadfile(curValue, paramKey,downloadsPath,valueKind,rowId,str(columnId))+";"
                        columnId+=1
                        
                else :
                    valueStr+=str(rowCurValueObj)+","
                rowId+=1
                
            #size=valueObj.size
            valueStr = self._normalizeMatrixDicoValue(valueStr)
            self._writeKeyFile(key,valueStr)    
            self._writeKeyFile(key+METATAG+".size",str(valueObj.shape).replace("(","{").replace(")","}"))
                
        except Exception as e:
            sys.stderr.write(" Extract Matrix Inconsistant Shadox param value for "+paramFullKey+"."+valueKind+ " : "+str(e))
            sys.exit(1)

    def _extractDataframeValue(self,shadoxParam,paramKey, datasetName, downloadsPath,valueKind,dataframeColsNames):
        
        paramFullKey=datasetName+paramKey
        valueObj = shadoxParam.values.__getattr__(valueKind)
        rowId=0
        key=self._getKeyStr(paramFullKey,valueKind)
        valuesByColumn={}
        # TODO : if only 'by column' is needed (to be confirmed), use relevant iterator instead of iterrows
        for indexRow,row in valueObj.iterrows():
            colId=0        
            for indexCol,value in row.iteritems():
                #self._writeKeyFile(key+"."+str(rowId)+"."+dataframeColsNames[colId],value)
                if (dataframeColsNames[colId] not in valuesByColumn):
                    valuesByColumn[dataframeColsNames[colId]]=""
                try:
                    if dataframeColsNames[colId]==VALUE_TYPE_FILE:
                        value=self._downloadfile(value, paramKey,downloadsPath,valueKind,rowId,dataframeColsNames[colId])
                    
                    valuesByColumn[dataframeColsNames[colId]]+=str(value)+";"
                except Exception as e:
                    sys.stderr.write(" Failed do download DataFrame Inconsistant Shadox param value for "+paramFullKey+"."+valueKind+ " : "+str(e))
                    sys.exit(1)
                colId+=1
            rowId+=1
        
                
        for curKey in dataframeColsNames :
            valueStr=""
            if rowId > 0:
                valueStr=valuesByColumn[curKey]
                
            self._writeKeyFile(key+"."+curKey,valueStr)

        self._writeKeyFile(key+METATAG+".size",rowId)


    def _extractValues(self,shadoxParam, paramKey, datasetName, downloadsPath) :

        valuekinds="";
        paramFullKey=datasetName+paramKey

        # if dataframe, list structure
        # TODO : should be maybe done in the _extractMetaData function which already get columns list
        # /!\ we suppose here that everybody has at least a 'default' valuekind
        dataframeColsNames=[]
        
        # check for errors
        try:
            
            # display structure of complex data
            if shadoxParam.definition["structure"]==PARAMETER_TYPE_DATAFRAME :
                for value_kind in shadoxParam.value_kinds :
                    # get the name of current value kind  
                    for col,values in shadoxParam.values.__getattr__(str(value_kind.name)).iteritems():
                        dataframeColsNames.append(col)

            # extract data depending on the structure
            for curValueKind in shadoxParam.values_definition:     

                valuekinds+=str(curValueKind['name'])+";"

                if shadoxParam.definition["structure"]==PARAMETER_TYPE_SCALAR:
                    self._extractScalarValue(shadoxParam,paramKey ,datasetName, downloadsPath,curValueKind['name'])
                elif shadoxParam.definition["structure"]==PARAMETER_TYPE_VECTOR :
                    self._extractVectorValue(shadoxParam,paramKey, datasetName, downloadsPath,curValueKind['name'])
                elif shadoxParam.definition["structure"]==PARAMETER_TYPE_DATAFRAME :            
                    self._extractDataframeValue(shadoxParam,paramKey, datasetName, downloadsPath,curValueKind['name'],dataframeColsNames)
                elif shadoxParam.definition["structure"]==PARAMETER_TYPE_MATRIX :            
                    self._extractMatrixValue(shadoxParam,paramKey, datasetName, downloadsPath,curValueKind['name'])    
                else:
                    sys.stderr.write("Unhandled structure type '"+str(shadoxParam.definition["structure"])+"' for parameter "+str(paramFullKey))
                    sys.exit(1)

            self._writeKeyFile(str(paramFullKey)+METATAG+".valuekinds",valuekinds)

        except Exception as e :
            sys.stderr.write("Unable to retrieve data for parameter '"+paramFullKey+"' : "+str(e))
            sys.exit(1)


    def _importFromShadox(self,datasetName,user_api_url,user_dataset_urn,api_key_file,downloadsPath) :


        simpleShadox = None
        api_key_file=os.path.realpath(api_key_file)
        try:

            if not os.path.exists(api_key_file):
                sys.stderr.write("ERROR: Shadox API-Key File not reachable : "+api_key_file)
                sys.stderr.write("ERROR: Please download it from Shadox interface.")
                sys.exit(1)

            # check folder storing API key file is readable for user only and strictly
            if os.stat(os.path.dirname(api_key_file)).st_mode & 0o0777 != 0o0500:
                # check disabled waiting for a proper solution to refresh conveniently Shadox API key file
                sys.stderr.write("WARNING: Shadox API-Key File is not in a restricted access folder : "+api_key_file+". This might not be tolerated in future versions.\n")            
                #sys.stderr.write("ERROR: Shadox API-Key File is not in a restricted access folder. Please run following command : $ chmod 500 "+os.path.dirname(api_key_file))            
                #sys.exit(1)

            simpleShadox = ShadoxClient( api_url=user_api_url, 
                                    api_keys_file_path=api_key_file, 
                                    dataset_urn=user_dataset_urn, 
                                    agent_name='python/3 datapacks/')
        except Exception as e:
            sys.stderr.write("ERROR: Shadox-importer was not able to retrieve contents of dataset '"+str(e)+"'"
                    +"\n            Please ensure that given definition for this dataset is correct :"
                    +"\n                API_URL=    "+user_api_url
                    +"\n                DATASET_URN=    "+user_dataset_urn
                    )

            #traceback.print_stack()

            sys.exit(1)

        allSuccess=True

        for j,targetParam in enumerate(simpleShadox.snapshot.parameters): 

            curParam = None
            paramFullName = (targetParam.path + targetParam.name)
            paramKey=self._normalizeDicoKey(paramFullName)
            paramFullKey=datasetName+paramKey
            errorMsg=""
            useAlias=False        
            
            # Identify parameter by path
            try:
                curParam = simpleShadox.get_parameter('path:' + paramFullName)
                
                #if not found try alias identification
            except Exception as e :
                errorMsg+="'"+paramFullName+"' : " + str(e)+";"
                nameFromGroup=(targetParam.path + "groups/" + targetParam.name.replace(u" · ",u"/"))
                try :
                    paramFullName = nameFromGroup
                    curParam = simpleShadox.get_parameter('path:' + paramFullName)                
                except Exception as e2:
                    errorMsg+="'"+paramFullName+"' : " + str(e2)+";"
                    for alias in targetParam.alias :
                        try :
                            curParam = simpleShadox.get_parameter('alias:' + alias)    
                            useAlias=True    
                        except Exception as e3:
                            errorMsg+=alias+" : "+str(e3)+";"

                if curParam == None :
                    self._writeKeyFile("# "+paramFullKey,"__FAILED__ : Unable to retrieve contents from Shadox : "+errorMsg)
                    allSuccess=False
                    continue
            
            self._writeKeyFile(paramFullKey+METATAG+".shadoxpath",paramFullName)
            
            structure=curParam.definition["structure"]
            self._writeKeyFile(paramFullKey+METATAG+".structure",structure)

            description=curParam.definition["description"].replace("\n","; ")
            if (len(description)>0) :
                self._writeKeyFile(paramFullKey+METATAG+".description",description)
            
        # SCALAR | VECTOR | MATRIX
            if structure==PARAMETER_TYPE_SCALAR or structure==PARAMETER_TYPE_VECTOR or structure==PARAMETER_TYPE_MATRIX:
                self._extractMetaData(curParam.definition,paramKey,datasetName)
                self._extractValues(curParam, paramKey,datasetName,downloadsPath)
                
        # DATAFRAME    
            elif structure==PARAMETER_TYPE_DATAFRAME :
                dimension=len(curParam.definition['columns'])
                self._writeKeyFile(paramFullKey+METATAG+".dimension",dimension)
            
                columns=""
                for curCol in curParam.definition['columns']:
                    columns+=self._extractDataframeMetadata(curCol, paramKey,datasetName)+";"

                self._writeKeyFile(paramFullKey+METATAG+".columns",columns)
                self._extractValues(curParam, paramKey,datasetName,downloadsPath)
                
        # DOCUMENT | NOTEBOOK | FILE
            elif     curParam.definition['structure'] == PARAMETER_TYPE_DOCUMENT \
                or    curParam.definition['structure'] == PARAMETER_TYPE_NOTEBOOK:
                self._extractMetaData(curParam.definition,paramKey,datasetName)

        #        print("Def. = "+str(curParam.definition))
        #        #if   (curParam.definition['docuType']['type'] == VALUE_TYPE_FILE):
        #        self._downloadfile(curParam, paramKey)
        #        #else:
        #        #    sys.stderr.write("Given Shadox param is not a file : "+str(curParam.definition))
        #        #    return False
        #        #self._extractValues(curParam, paramKey)
        #    elif curParam.definition['structure'] == PARAMETER_TYPE_NOTEBOOK:
        #        self._extractMetaData(curParam.definition,paramKey)
                # TODO download ?

        # UNKNOWN !
            else:
                self._writeKeyFile(paramFullKey," __FAILED__ : Unable to retrieve contents from Shadox : Unhandled Shadox structure type '"+structure+"'")
                allSuccess=False

        # tmp while having a solution for groups
        return allSuccess;#allSuccess 


## the main function
if __name__ == '__main__':
    parser = _HelpParser(description=
"""Download given shadox dataset and display its contents as a dictionary. 

Result is displayed in <stdout>.

Return:
    1 if an error occured
    0 if successful""",

    formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('url',help="the shadox URL. Ex: https://iced-test/shadox-service/rest/api/.../publication/master-1.2.3")
    parser.add_argument('target_dir',help="directory where to store downloaded files")
    parser.add_argument('api_keys_file',help="The json file containing your Shadox API keys")
    parser.add_argument('--keys_prefix',help="prefix to be used for generated keys (instead of the Shadox dataset name)")
    
    #parser.add_argument('--ori',action='store_true',help="show in which input dico the value has been defined")    
    args = parser.parse_args()    
    
    shadox_url=re.sub(r'(.*api)/project.*',lambda m:m.group(1),args.url)
    dataset_urn=re.sub(r'.*api(/project.*)',lambda m:m.group(1),args.url)
    
    importer=ShadoxImporter(args.target_dir,args.api_keys_file,args.keys_prefix,shadox_url,dataset_urn)
    success=importer.retrieve()
    
    if success:
        sys.exit(0)
    else:
        sys.exit(1)



