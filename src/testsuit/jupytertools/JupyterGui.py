
    
from IPython.display import display,Javascript
from IPython.core.getipython import get_ipython
from ipywidgets import *

from testsuit.jupytertools import FileDialog

from testsuit.misc.logger import create_logger
from testsuit.misc.MonitorProgress import MonitorProgress,consoleSilentProgressCb
from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr
from testsuit.datatools import datatoolbox

################## Setup Jupyter cells size ########################
disable_js = """
IPython.OutputArea.prototype._should_scroll = function(lines) {
    return false;
}
"""

def showHeader(title: str) -> None:
    """Show the testsuit logo with the tool name on top of a GUI (the same animated SVG as the HTML reports)."""
    from testsuit.exploit.runner.report_html import make_report_logo_svg
    display(HTML(make_report_logo_svg(title)))

def setupDisplay():
    create_logger("JupyterGUI")
    display(HTML("<style>.container { width:100% !important; }</style>"))    
    display(Javascript(disable_js))

def insert_cell(contents,relIndex=1,replace=False):

    display(Javascript("IPython.notebook.select(IPython.notebook.get_selected_index()+"+str(relIndex)+")"))
    
    shell = get_ipython()
    payload = dict(
        source='set_next_input',
        text=contents,
        replace=replace,
    )
    shell.payload_manager.write_payload(payload, single = False)

def execute_next_cells(relRangeStart=1,relRangeEnd=None):
    startRangeJavascript="IPython.notebook.get_selected_index()+"+str(relRangeStart)
    endRangeJavascript="IPython.notebook.ncells()"
    if relRangeEnd:
        endRangeJavascript="IPython.notebook.get_selected_index()+"+str(relRangeEnd)
    display(Javascript("IPython.notebook.execute_cell_range("+startRangeJavascript+","+endRangeJavascript+")"))

def execute_next_cell():
    execute_next_cells(1,2)

def delete_cells(cellPosStart,cellPosEnd=None):

    if cellPosEnd is None:
        cellPosEnd=cellPosStart+1
    elif cellPosEnd==-1:
        cellPosEnd="IPython.notebook.ncells()"

    display(Javascript(f"for (let i={cellPosStart};i<{cellPosEnd};i++) IPython.notebook.delete_cell({cellPosStart})"))

################## AGuiComponent ########################
class AGuiComponent:
    def __init__(self):        
        pass

    def show(self):
        raise Exception("'show' not implemented")

    def hide(self):
        raise Exception("'hide' not implemented")
    
    def update(self,forceUpdate=False):
        raise Exception("'update' not implemented")

################### GuiFileSelection ######################## 
class GuiFileSelection(AGuiComponent):
    
    def __init__(self,guiComponentsMap,fileMgrsList,customSelectedFilesChangedCb=None,multi=True,\
                        filterFiles=None):
        self.name="GuiFileSelection"
        self.guiComponentsMap=guiComponentsMap
        self.guiComponentsMap[self.name]=self
        self.customSelectedFilesChangedCb=customSelectedFilesChangedCb
        self.fileMgrsList=fileMgrsList
        if filterFiles is None:
            filterFiles=[("Data Files"," ".join(["*." + fileExt for fileExt in datatoolbox.SUPPORTED_DATAFILE_EXTENSIONS]))]
        self.filters=filterFiles
        self.multi=multi
        self.label_files_list = HTML("No File Selected")

        self.btn_choose_files = Button(
                        description = 'Pickup some File(s)',
                        button_style = 'primary',
                        layout=Layout(width='auto', height='40px')
                        )
        self.btn_choose_files.style.font_weight='bold'

        self.btn_choose_folder = Button(
                        description = 'Select a Full Directory',
                        button_style = 'primary',
                        layout=Layout(width='auto', height='40px')
                        )
        self.btn_choose_folder.style.font_weight='bold'

        self.title = HTML("<hr/><h3>Choose Data Source(s)</h3>")
        self.filesDescTablesArea=HTML("")
        self.box=VBox([ self.title, HBox([self.btn_choose_files,self.btn_choose_folder,self.label_files_list]), self.filesDescTablesArea])
        display(self.box)
        self.btn_choose_files.on_click(self.btn_choose_files_clicked)
        self.btn_choose_folder.on_click(self.btn_choose_folder_clicked)

        
    def show(self):        
        pass


    def openFileMgr(self,filename,fileIdx, addToList=True):
         
        if not filename:
            return

        fileMgr=FolderParamMgr(filename,fileIdx=fileIdx)

        if addToList:
            self.fileMgrsList.append(fileMgr)

        return fileMgr

    def filesListChanged(self):        
        self.updateFilesListLabel()
        if 'GuiPrepareFiles' in self.guiComponentsMap:
            self.guiComponentsMap['GuiPrepareFiles'].show()
        if 'GuiChooseFields' in self.guiComponentsMap:
            self.guiComponentsMap['GuiChooseFields'].show()
        if len(self.fileMgrsList)>0 and self.customSelectedFilesChangedCb is not None:
            self.customSelectedFilesChangedCb()
        if 'GuiChooseFields' in self.guiComponentsMap:
            self.guiComponentsMap['GuiChooseFields'].update()    

    def btn_choose_files_clicked(self,arg):

        self.fileMgrsList.clear() 
        fileListStr = FileDialog.openFileDialog(filters=self.filters,multi=self.multi)

        if not fileListStr or len(fileListStr)==0:
            return
    
        self.label_files_list.value=""
        fileIdx=0
        for fileName in fileListStr:
            self.openFileMgr(fileName,fileIdx)
            fileIdx+=1

        self.filesListChanged()
        

    def btn_choose_folder_clicked(self,arg):
        self.fileMgrsList.clear() 
        folderPath = FileDialog.openFolderDialog()
        
        self.label_files_list.value=""
        fileIdx=0
        self.openFileMgr(folderPath,fileIdx)

        self.filesListChanged()
   
    def updateFilesListLabel(self):
        self.label_files_list.value=""
        for fileMgr in self.fileMgrsList:
            self.label_files_list.value+=fileMgr.getBaseName()+"<br/>"

         # update file description
        self.filesDescTablesArea.value='''
<style type='text/css' >
    table, table th, table td {
        border:1px solid black;
        padding:2px;
        margin:2px;
        text-align:center;
    }
    table th {
        background:lightgreen;
    }

    table .primary {
        background:green;
    }

    table .secondary {
        background:lightblue;
    }

    table .tertiary {
        background:lightgrey;
    }
</style>'''

        for fileMgr in self.fileMgrsList:            
            self.filesDescTablesArea.value+="<table>"
            self.filesDescTablesArea.value+="<tr><th class='primary' style='width:30%'  >Data Source "+str(fileMgr.getFileIdx()+1)+"</th>"\
                                               +"<td style='width:60%;font-weight:bold;' class='primary' >"+fileMgr.getFileName()+"</th></tr>"
            self.filesDescTablesArea.value+=fileMgr.toHtmlTbl()
            self.filesDescTablesArea.value+="</table><br/>"

################### GuiPrepareFiles ######################## 
#
# Run some pre-processing on file when required
# Ex: clean CSV files header line from special characters
#
class GuiPrepareFiles(AGuiComponent):
    def __init__(self,guiComponentsMap,fileMgrsList):
        self.name="GuiPrepareFiles"
        self.guiComponentsMap=guiComponentsMap
        self.guiComponentsMap[self.name]=self
        self.fileMgrsList=fileMgrsList
        self.btn_prepare_files = Button(
                    description = 'Clean Files Contents',
                    layout=Layout(width='auto', height='40px')
                    )
        self.btn_prepare_files.style.font_weight='bold'        
        self.prgbar_prepare_files = IntProgress(min=0, max=0)
        self.box=HBox([self.btn_prepare_files,self.prgbar_prepare_files])
        display(self.box)
        self.btn_prepare_files.on_click(self.btn_prepare_files_clicked)
        self.hide()
        
        
    def hide(self):
        self.box.layout.display= 'none'

    def show(self):
        self.box.layout.display = 'block'
        self.prgbar_prepare_files.layout.visibility= 'hidden'
    
    def showProgressBar(self):
        self.prgbar_prepare_files.layout.visibility= 'visible'
    
    def hideProgressBar(self):
        #self.prgbar_prepare_files.layout.display= 'none'
        self.prgbar_prepare_files.layout.visibility= 'hidden'
        
    def prepareFilesProgressCb(self,newVal):
        self.prgbar_prepare_files.value=newVal

    def btn_prepare_files_clicked(self,arg):  
        self.showProgressBar()
        fileIdx=0
        for fileMgr in self.fileMgrsList:      
            self.prgbar_prepare_files.value=1            
            self.prgbar_prepare_files.description=fileMgr.getBaseName()
            if fileMgr.getNbEntries() is not None:
                self.prgbar_prepare_files.max=fileMgr.getNbEntries()
            else:
                self.prgbar_prepare_files.max=0
            preparedFileName=fileMgr.prepareFile(self.prepareFilesProgressCb)
            # update files list if prepared file changed name
            if preparedFileName is not None:
                self.fileMgrsList[fileMgr.getFileIdx()]=\
                    self.guiComponentsMap['GuiFileSelection'].openFileMgr(preparedFileName,fileMgr.getFileIdx(),addToList=False)
            # update files list label
            self.guiComponentsMap["GuiFileSelection"].filesListChanged()
            fileIdx+=1
        self.hideProgressBar()

        if len(self.fileMgrsList)>0:
            autoH5FileName=self.fileMgrsList[0].getFileName().replace("."+self.fileMgrsList[0].getFileType(),".h5")
            self.guiComponentsMap['GuiH5Convertion'].update(autoH5FileName)        

        if 'GuiChooseFields' in self.guiComponentsMap:
            self.guiComponentsMap['GuiChooseFields'].update()        

################### GuiChooseFields ######################## 
class GuiChooseFields(AGuiComponent):
    def __init__(self,guiComponentsMap,fileMgrsList,customSelectedFieldsChangedCb):
        self.name="GuiChooseFields"
        self.guiComponentsMap=guiComponentsMap
        self.guiComponentsMap[self.name]=self
        self.customSelectedFieldsChangedCb=customSelectedFieldsChangedCb
        self.fileMgrsList=fileMgrsList
        self.fieldsListDescrArea=HTML("")
        self.colsLabel = HTML()
        self.useFirstFieldAsTimestamp = widgets.Checkbox(value=False,description="Use First Line for Timestamps")

        self.nbFieldsLabel = HTML("<h4>no field selected</h4>")
        self.fieldsTextTitle = HTML("<hr/><h3>Select Fields</h3>")
        self.fieldsTextArea = Textarea(
            value='',
            placeholder='Fields names (one per line). It can be Regex too.',
            description='',
            disabled=False,
            layout=Layout(width='40%', height='100px')
        )
        self.box=VBox([self.fieldsTextTitle,self.fieldsListDescrArea,
                        self.useFirstFieldAsTimestamp,self.fieldsTextArea,self.nbFieldsLabel, self.colsLabel])

        display(self.box)
        self.hide()

        
    def show(self):        
        self.box.layout.display = 'block'

    def hide(self):
        self.box.layout.display = 'none'

    def isUseFirstFieldAsTimestamp(self):
        return self.useFirstFieldAsTimestamp.value

    # select fields to extract from input files
    def updateFieldsSelection(self,fileMgrsList,selectedFieldsCb,fieldsTextArea):
        
        # when user changes list, update fields list (based on regex)
        def onChange(arg):
            
            if arg['name'] != 'value':
                return
            newVal=arg['new']
            regexesListStr=newVal.split('\n')
            regs=[]

            for regexStr in regexesListStr:
                try:
                    regs.append(datatoolbox.getFileParamMatchRegex(regexStr))
                except Exception as e:
                    selectedFieldsCb(e)    
                    return           

            for fileMgr in fileMgrsList:
                fileMgr.clearSelectedFieldsIdx()

            # find all matching fields from all files
            for reg in regs:     
                for fileMgr in fileMgrsList:
                    curFieldIdx=-1
                    for fieldName in fileMgr.getFieldNames():
                        curFieldIdx+=1
                        if curFieldIdx in fileMgr.getSelectedFieldsIdx():
                            continue
                        if reg.match(fieldName):
                            fileMgr.addSelectedFieldIdx(curFieldIdx)

            # invoke provided callback for applicative processing
            selectedFieldsCb()             

        fieldsTextArea.observe(onChange)

        # initial round to get all values by default
        onChange({'name':"value", 'new':fieldsTextArea.value })
    
    def getSearchTexts(self):
        return self.fieldsTextArea.value.split("\n")

    # choose fields   
    def update(self):
        
        def onColsChangesCb(exception=None):
            global colIndicesPerFile
            totalNbFields=0
            if exception:
                self.colsLabel.value="ERROR: "+str(exception) 
                self.nbFieldsLabel.value="<h4>-</h4>"
                colIndicesPerFile={}

            else:
                self.colsLabel.value="""
<div style='color:grey;'>Tip 1: you can use regex: '.*' for anything, '.' for any single char, '^' for start, '$' for end. Ex: "<b>PARAM_XYZ.*_[12-18]</b>"</div>
<div style='color:grey;'>Tip 2: you can use regex for the source file also. Ex: "<b>_some_part_of_filename_::MY_PARAM</b>"</div>
"""
                for fileMgr in self.fileMgrsList:
                    totalNbFields+=len(fileMgr.getSelectedFieldsIdx())
                    if len(fileMgr.getSelectedFieldsIdx())>0:
                        self.colsLabel.value+="<br/><b>From "+fileMgr.getBaseName()+"</b>:<span style='font-family:courrier;font-size:1.2rem'>"+str(fileMgr.getSelectedFieldsNames())+"</span>"
                if totalNbFields>0:              
                    self.nbFieldsLabel.value="<h3><span style='font-weight:bold;color:blue;'>"+str(totalNbFields)+" </span>Field(s) Selected</h3>"
                else:
                    self.colsLabel.value+="-- no match --"
                    self.nbFieldsLabel.value="<h3 style='color:red'>no field selected</h3>"
  
            # notify changes on selected fields list to other elements of the application
            if self.customSelectedFieldsChangedCb is not None:
                self.customSelectedFieldsChangedCb(totalNbFields=totalNbFields,forceUpdate=True)

        self.updateFieldsSelection(self.fileMgrsList,onColsChangesCb,self.fieldsTextArea) 

     
    def loadParams(self,progressCb=None):


        monitorProgress=MonitorProgress(name="Jupyter Notebook",progressCb=consoleSilentProgressCb)
        monitorProgress.set_total_items(len(self.fileMgrsList))
        dfList=[]
        for fileMgr in self.fileMgrsList:        
            # uses first search to match timestamps
            indexPathRegexList=None
            if self.isUseFirstFieldAsTimestamp()==True:
                indexPathRegexStr=self.getSearchTexts()[0]
                indexPathRegexList=[]
                for i in range(len(fileMgr.getSelectedFieldsNames())):
                    indexPathRegexList+=[indexPathRegexStr]            

            dfList+=fileMgr.loadParams(fileMgr.getSelectedFieldsNames(),indexNamesList=indexPathRegexList,monitorProgress=monitorProgress.child(fileMgr.getBaseName()))

        return dfList