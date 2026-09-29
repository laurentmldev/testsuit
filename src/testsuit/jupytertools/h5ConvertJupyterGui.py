
import re,os,traceback
from unidecode import unidecode
  

from IPython.display import display
from ipywidgets import *

from testsuit.misc.logger import create_logger
log=create_logger("h5convGui")

from testsuit.datatools.DataframeToHdf5 import DataframeToHdf5
from testsuit.datatools import datatoolbox
from testsuit.jupytertools import JupyterGui
from testsuit.jupytertools import FileDialog

################### GuiH5Convertion ######################## 
class GuiH5Convertion(JupyterGui.AGuiComponent):
    def __init__(self,guiComponentsMap,fileMgrsList):
        self.name="GuiH5Convertion"
        self.guiComponentsMap=guiComponentsMap
        self.guiComponentsMap[self.name]=self
        self.fileMgrsList=fileMgrsList

        self.autoH5FileNameActive=True

        self.txtinput_h5_file_name=Text(continuous_update=False)
        self.btn_choose_h5_file = Button(
                            description = 'Change Target H5 File',
                            layout=Layout(width='30%', height='30px')
                            )
        self.btn_choose_h5_file.style.font_weight='bold'
        self.warning_text_del_existing_file=HTML()
        self.btn_del_existing_h5_file = Button(
                            description = 'Delete Existing H5 File',
                            button_style = 'warning',                        
                            layout=Layout(width='30%', height='30px')
                            )
        self.btn_del_existing_h5_file.style.font_weight='bold'
        
        
        self.btn_start_conversion = Button(
                            description = 'Extract to H5',
                            button_style = 'primary',
                            layout=Layout(width='50%', height='40px')
                            )
        self.btn_start_conversion.style.font_weight='bold'

        self.genH5Infos = HTML()
        self.genH5ProgressBar = IntProgress(min=0, max=0, 
                                  description="Extracting",
                                  layout=Layout(width='30%'))    
        self.genH5ProgressBar.value = 1

        self.title = HTML("<hr/><h3>Generate H5 File</h3>")

        self.box_del_h5_file=VBox([self.warning_text_del_existing_file,self.btn_del_existing_h5_file])
        self.box_h5_file_name=HBox([self.txtinput_h5_file_name,self.btn_choose_h5_file])
        self.box_h5_file=VBox([self.box_h5_file_name,self.box_del_h5_file])
        self.box_h5_progress=VBox([self.genH5ProgressBar,self.genH5Infos])
        self.box=VBox([self.title, self.box_h5_file,self.btn_start_conversion,self.box_h5_progress])
        
        display(self.box)
        self.hide()
       
        self.btn_choose_h5_file.on_click(self.btn_choose_h5_file_clicked)
        self.btn_del_existing_h5_file.on_click(self.btn_del_existing_h5_file_clicked)     
        self.txtinput_h5_file_name.observe(self.h5_file_name_area_changed)
        self.btn_start_conversion.on_click(self.btn_generate_h5_clicked)

        
    def show(self):        
        self.box.layout.display = 'block'   
        self.showStartGenBtn()     

    def hide(self):
        self.box.layout.display = 'none'

    def hideDelH5File(self):
        self.box_del_h5_file.layout.display = 'none'

    def showDelH5File(self):
        self.box_del_h5_file.layout.display = 'block'
        self.warning_text_del_existing_file.layout.display = 'block'
        self.btn_del_existing_h5_file.layout.display = 'block'

    def hideH5ProgressInfos(self):
        self.box_h5_progress.layout.display= 'none'
        
    def showH5ProgressInfos(self):
        self.box_h5_progress.layout.display= 'block'
        
    def hideDelH5FileBtn(self):
        self.btn_del_existing_h5_file.layout.display = 'none'

    def showStartGenBtn(self):
        self.btn_start_conversion.layout.display = 'block'

    def hideStartGenBtn(self):
        self.btn_start_conversion.layout.display = 'none'
        
    def btn_choose_h5_file_clicked(self,arg):
        newFileName = FileDialog.saveFileDialog(defaultfile=self.txtinput_h5_file_name.value,filetypes=[("HDF5 File","*.h5 *.hdf5")],defaultextension=".h5")
        self.update(newFileName,True) 

    # update targe H5 file name
    def update(self,newFileName=None,forceUpdate=False):

        if self.autoH5FileNameActive==False and forceUpdate==False:
            return
        
        if newFileName is not None:
            self.txtinput_h5_file_name.value=newFileName
        self.btn_start_conversion.description="Generate "+os.path.basename(self.txtinput_h5_file_name.value)
        self.hideDelH5File()
        self.hideH5ProgressInfos()
        
        if os.path.isfile(self.txtinput_h5_file_name.value):
            self.warning_text_del_existing_file.value="<span style='color:red'><b>ATTENTION:</b> Target H5 file exists: </span>"+\
                    self.txtinput_h5_file_name.value          
            self.showDelH5File()            

    def btn_del_existing_h5_file_clicked(self,arg):
                os.remove(self.txtinput_h5_file_name.value)
                self.warning_text_del_existing_file.value="Existing H5 file has been deleted!"
                self.hideDelH5FileBtn()

    
    def h5_file_name_area_changed(self,arg):
        if arg['name'] != 'value':
            return
        newVal=arg['new']
        self.update(newVal,forceUpdate=True)
        self.autoH5FileNameActive=False
        
    def cleanH5Name(self,strName):
        cleanName=unidecode(strName.replace(".","_").replace(" ","_").replace("%","_"))
        if re.match(r"^\W.*",cleanName):
            cleanName="h5_"+cleanName # need to start with a letter
        return cleanName

    def btn_generate_h5_clicked(self,arg):
        global colIndicesPerFile
        global fileMgrsList
        h5FileName = self.txtinput_h5_file_name.value

        self.genH5Infos.value=""
        self.showH5ProgressInfos()
        
        def genProgressCb(percent=None,msg=None,msgSeverity="info"):
            if percent:
                self.genH5ProgressBar.value=percent                

            if not msg:
                return
            
            if isinstance(msg,list):
                msg=" || ".join(msg)
                
            if msgSeverity=="error" or msgSeverity=="danger":
                log.error(msg)
            elif msgSeverity=="warn" or msgSeverity=="warning" :
                log.warning(msg)
            else:
                log.info(msg)
        
        totalNbParams=0
        for fileMgr in self.fileMgrsList:
            totalNbParams+=len(fileMgr.getSelectedFieldsIdx())                
        self.genH5Infos.value="Extracting "+str(totalNbParams)+" param(s) from "+str(len(self.fileMgrsList))+" file(s)<br/>"
            
        for fileMgr in self.fileMgrsList:
            # supposes here that all param have same amount of entries for a given file
            if fileMgr.getNbEntries() is not None:
                self.genH5ProgressBar.max=fileMgr.getNbEntries()*len(fileMgr.getSelectedFieldsIdx())
            else:
                self.genH5ProgressBar.max=0
            self.genH5ProgressBar.value=1
            self.genH5ProgressBar.description=fileMgr.getBaseName()
            try:
                def progressCbLoadParams(percent=None,msg=None,msgSeverity="info"):
                    if percent is not None:
                        percent=percent/2
                    genProgressCb(percent,msg,msgSeverity)

                dfList=self.guiComponentsMap["GuiChooseFields"].loadParams(progressCbLoadParams)

                dfIdx=0
                for df in dfList:
                    if not DataframeToHdf5(h5FileName,h5GroupName=self.cleanH5Name(fileMgr.getBaseName()),df=df,comment="generated from Jupyter tool H5-Conv"):
                        raise Exception("Unable to write "+datatoolbox.getDfName(df)+" in file "+h5FileName)
                    percent=50 + (dfIdx*50/len(dfList))
                    genProgressCb(percent,["writing data",df.name])
                    dfIdx+=1
                        
                self.genH5ProgressBar.value=self.genH5ProgressBar.max
            except Exception as e:
                print(traceback.format_exc())
                self.genH5Infos.value+="<div style='color:red' ><b>ERROR: unable to finalise process</b>.</div>"
                self.genH5Infos.value+="<div style='color:red;font-family:Courier'>"+str(e)+"</div>"                
                self.genH5Infos.value+="<div style='color:orange' ><b>TIP:</b> Maybe you should <b>delete</b> existing H5 file and start again ?</div>"                
                return
            
        self.genH5Infos.value+="<div style='color:green;font-size:1.5em;font-weight:bold'>Successfully generated file</div>"\
                              +"<div style='color:grey;font-size:1.2em;font-weight:bold;font-family:Courier' >"+h5FileName+"</div>"
    
def runGUI():
    
    JupyterGui.setupDisplay()
    
    guiComponentsMap={}
    fileMgrsList = []

    def selectedFieldsChanged(totalNbFields=0,forceUpdate=False):
        
        if totalNbFields==0:
            guiComponentsMap["GuiH5Convertion"].hide()
        else:
            guiComponentsMap["GuiH5Convertion"].show()
            guiComponentsMap["GuiH5Convertion"].update(forceUpdate=forceUpdate)

    def selectedFilesChanged():
        
        if len(fileMgrsList)>0:
            autoH5FileName=fileMgrsList[0].getFileName().replace("."+fileMgrsList[0].getFileType(),".h5")
            guiComponentsMap["GuiH5Convertion"].update(newFileName=autoH5FileName)
        else:     
            guiComponentsMap["GuiH5Convertion"].update(newFileName="")

    display(Image(
        value=open(os.path.realpath(os.path.dirname( __file__[:-1] if __file__.endswith('.pyc') else __file__ ) +os.sep+"/media/logo.png"), "rb").read(),
        format='png',
        width=300,
        height=400,
    ))
    display(HTML("<h2 style='color:green;padding:0;margin:0;font-weight:bold'>[H5-Conv]</h2>"))
    JupyterGui.GuiFileSelection(guiComponentsMap,fileMgrsList,selectedFilesChanged).show()
    JupyterGui.GuiPrepareFiles(guiComponentsMap,fileMgrsList)
    JupyterGui.GuiChooseFields(guiComponentsMap,fileMgrsList,selectedFieldsChanged)
    GuiH5Convertion(guiComponentsMap,fileMgrsList)
    