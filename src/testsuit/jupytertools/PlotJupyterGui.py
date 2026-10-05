
import re,os
import numpy as np
import pandas as pd
    
from IPython.display import display
from ipywidgets import *


from testsuit.datatools.plotHelpers import plotData, plotData3D, get_plot_backend, plot_backend_name, plot_backend_names

    
from testsuit.misc.logger import create_logger
log=create_logger("h5plotGui")

from testsuit.jupytertools import FileDialog
from testsuit.jupytertools import JupyterGui
from testsuit.datatools import datatoolbox

from testsuit.datatools.DataFileMgrs.CsvFileMgr import cleanCsvHeaderLine


class GuiDumpH5Tree(JupyterGui.AGuiComponent):
    def __init__(self,guiComponentsMap,fileMgrsList):
        self.name="GuiDumpH5Tree"
        self.guiComponentsMap=guiComponentsMap
        self.guiComponentsMap[self.name]=self
        self.fileMgrsList=fileMgrsList

        self.label_tree = HTML("<hr/>")
        self.toggleSts=0,
        self.btn_toggle_tree = Button(
                    description = '',
                    layout=Layout(width='auto', height='30px')
                    )
        
        self.box=VBox([self.btn_toggle_tree,self.label_tree])
        display(self.box)
        self.btn_toggle_tree.on_click(self.toggleTree)

        self.fig=None
        self.hide()
        self.hideTree()

    def toggleTree(self,clickEvtt=None):
        if self.toggleSts==0:
            self.showTree()
        else:
            self.hideTree()
            

    def update(self):
        self.label_tree.value=self.fileMgrsList[0].dumpTreeHtml()

    def showTree(self,clickEvt=None):
        
        self.update()
        self.label_tree.layout.display='block'
        self.btn_toggle_tree.description='Hide Tree Structure'
        self.toggleSts=1
        
    def hideTree(self):
        self.label_tree.layout.display='none'
        self.btn_toggle_tree.description='Dump Tree Structure'
        self.label_tree.value=""
        self.toggleSts=0

    def hide(self):
        self.box.layout.display='none'

    def show(self):
        self.box.layout.display='block'
    

##########################################

class GuiPlotFields(JupyterGui.AGuiComponent):

    def __init__(self,guiComponentsMap,fileMgrsList):
        self.name="GuiPlotFields"
        self.guiComponentsMap=guiComponentsMap
        self.guiComponentsMap[self.name]=self
        self.fileMgrsList=fileMgrsList
        self.dataLabels=[]
        self.dataHandles=[]
        self.MAX_LEGEND_LABEL_LENGTH=60

        self.label_fig = HTML("<hr/>")

        self.txt_vert_offset = Text(value=None,
                                  placeholder="ex: '-12', or 'autoz:3.14s'",
                                  description="Vert. Offset",continuous_update=False)
                    
        self.vert_offset_help = HTML("<div style='color:grey;'>Tip: you can use 'autoz:XXs' to auto-zero based on mean value of XX first seconds</div>")

        
        self.dropdown_projtype = Dropdown(
                options=[("2D","rectilinear"),
                         ("3D","3d")
                         ],
                value="rectilinear",
                description="Type"
        )

        self.dropdown_backend = Dropdown(options=plot_backend_names(), value=plot_backend_name(), description="Backend")
        # plotly figures are displayed here (matplotlib ones display themselves)
        self.out_fig = Output()

        self.dropdown_linestyle = Dropdown(
                options=[("Solid","solid"),
                         ("Dotted","dotted"),
                         ("Dashed","dashed"),
                         ("Dashdot","dashdot"),
                         ("None","")                         
                         ],
                value="solid",
                description="Linestyle"
        )
        
        self.dropdown_markerstyle = Dropdown(
                options=[("Dot","."),
                         ("None",""),
                         ("Pixel",","),
                         ("Circle","o"),
                         ("Tri Up","2"),
                         ("Plus","p"),
                         ("X","x"),
                         ("Star","*"),
                         ("Diamond","d"),
                         ("Horisontal Line","_"),
                         ("Vertical Line","|")
                         ],
                value=",",
                description="Markers"
        )

        self.checkbox_legend = Checkbox(value=True, description="Legend")
        self.checkbox_x_is_timestamp = Checkbox(value=True, description="Absciss is Timestamp")

#'default', 'steps', 'steps-pre', 'steps-mid', 'steps-post'
        self.dropdown_color = Dropdown(
                options=[
                        ("### Random ###","random"),
                        # red
                        ("RED","red"),
                        ("lightcoral","lightcoral"),
                        ("darkred","darkred"),

                        # blue
                         ("BLUE","blue"),
                         ("lightblue","lightblue"),
                         ("darkblue","darkblue"),

                        # cyan
                         ("CYAN","cyan"),
                         ("turquoise","turquoise"),
                         ("darkcyan","darkcyan"),
                          
                        # green
                         ("GREEN","green"),
                         ("chartreuse","chartreuse"),
                         ("darkgreen","darkgreen"),

                        # orange
                         ("ORANGE","orange"),
                         ("papayawhip","papayawhip"),
                         ("darkorange","darkorange"),
                        
                        # black
                         ("GREY","grey"),
                         ("lightgrey","lightgrey"),
                         ("black","black"),
                         
                        # yellow
                         ("YELLOW","yellow"),
                         ("brown","brown"),
                         ("chocolate","chocolate"),

                        # pink
                         ("PURPLE","purple"),
                         ("fuchsia","fuchsia"),
                         ("orchid","orchid"),

                         ],
                value="random",
                description="Plot Color"
        )

        self.dropdown_drawstyle = Dropdown(
                options=[("Default","default"),
                         ("Steps Post","steps-post"),
                         ("Steps Pre","steps-pre"),
                         ("Steps Mid","steps-mid")
                         ],
                value="default",
                description="Drawstyle"
        )

        self.btn_draw = Button(
                    description = 'Add to Figure',
                    button_style = 'primary',
                    layout=Layout(width='auto', height='40px')
                    )
        self.btn_draw.style.font_weight='bold' 

        self.btn_clear = Button(
                    description = 'Clear',
                    layout=Layout(width='auto', height='40px')
                    )
        self.btn_clear.style.font_weight='bold' 

        self.btn_save_figure = Button(description="Export Figure",
                                      layout=Layout(width='auto', height='40px'),
                                      button_style = 'success',
                    )
        self.btn_save_figure.style.font_weight='bold' 
        
        self.txt_fig_title = Text(value="This is figure title",
                                  placeholder='Type here a title for the figure',
                                  description="Figure Title",continuous_update=False)
        self.titleManuallySet=False

        self.txt_plot_title = Text(value="",
                                  placeholder='Type here a title for the plot',
                                  description="Plot Title",continuous_update=False)

        self.txt_plot_xlabel = Text(value="",
                                  placeholder='Type here xlabel',
                                  description="X-axis Label",continuous_update=False)
        

        self.txt_plot_ylabel = Text(value="Values",
                                  placeholder='Type here ylabel',
                                  description="Y-axis Label",continuous_update=False)
        

        self.titlesBox=HBox([VBox([self.txt_fig_title,self.txt_plot_title]),VBox([self.txt_plot_xlabel,self.txt_plot_ylabel]),VBox([self.checkbox_legend,self.checkbox_x_is_timestamp])])
        self.box=VBox([self.label_fig,HBox([self.txt_vert_offset,self.vert_offset_help]),
                                      HTML("<hr/>"),
                                      HBox([self.dropdown_projtype,self.dropdown_backend]),
                                      HBox([self.dropdown_linestyle,self.dropdown_markerstyle,self.dropdown_drawstyle,self.dropdown_color]),
                                      HTML("<hr/>"),
                                      HBox([self.btn_draw,self.btn_clear,self.btn_save_figure,]),
                                      HTML("<hr/>"),
                                      self.titlesBox,
                                      self.out_fig])

        display(self.box)
        self.btn_draw.on_click(self.update)        
        self.btn_clear.on_click(self.clearFigure)
        self.btn_save_figure.on_click(self.saveFigure)
        self.txt_fig_title.observe(self.onTitlesChange,names="value")
        self.txt_fig_title.observe(self.onTitleManualChange)
        self.txt_plot_title.observe(self.onTitlesChange,names="value")
        self.txt_plot_ylabel.observe(self.onTitlesChange,names="value")
        self.txt_plot_xlabel.observe(self.onTitlesChange,names="value")
        self.checkbox_legend.observe(self.toggleLegend,names="value")
        self.dropdown_projtype.observe(self.onProjChange,names="value")
        self.dropdown_backend.observe(self.onProjChange,names="value")
        self.fig=None
        self.ax=None
        self.hide()

    @property
    def backend(self):
        return get_plot_backend(self.dropdown_backend.value)

    def onProjChange(self,evt=None):
        # 2D/3D or backend changed: next plot in a new figure
        self.fig=None
        self.ax=None
        self.dataLabels=[]
        self.dataHandles=[]

    def onTitleManualChange(self,evt=None):
        self.titleManuallySet=True

    def onTitlesChange(self,evt=None):
        if self.fig is not None:
            self.backend.set_titles(self.fig,self.ax,self.txt_fig_title.value,self.txt_plot_title.value,
                                    self.txt_plot_xlabel.value or None,self.txt_plot_ylabel.value)

    def setFigureTitle(self,newTitleValue):
        if not self.titleManuallySet:
            self.txt_fig_title.value=newTitleValue
            self.onTitlesChange()

    def saveFigure(self,clickEvt=None):
        defaultFileName=None
        fileTypes=None

        if self.dropdown_projtype.value=="rectilinear":
            defaultFileName=cleanCsvHeaderLine(self.txt_fig_title.value)+".csv"
            fileTypes=[("CSV","*.csv"),("PNG","*.png *.PNG")]
    
        elif self.dropdown_projtype.value=="3d":
            defaultFileName=cleanCsvHeaderLine(self.txt_fig_title.value)+".png"
            fileTypes=[("PNG","*.png *.PNG")]
        else:
            print("ERROR: unhandled projection type for plot export : '"+self.dropdown_projtype.value+"'")
            return
        if self.dropdown_backend.value=="plotly":
            fileTypes.append(("HTML (interactive)","*.html"))

        targetFileName = FileDialog.saveFileDialog( title="Export figure as",
                                                    defaultfile=defaultFileName,
                                                    filetypes=fileTypes)
        if targetFileName is None:
            return
        if targetFileName.lower().endswith(("png","html","htm")):
            self.backend.export_image(self.fig,targetFileName)
            print("saved "+targetFileName)

        # only functional for 2D plots
        elif targetFileName.lower().endswith("csv"):
            (x_lim_min,x_lim_max),(y_lim_min,y_lim_max)=self.backend.view_limits(self.fig,self.ax)
            npArraysList=[np.stack([x_data,y_data]) for x_data,y_data in self.backend.lines_data(self.fig,self.ax)]

            finalDf=datatoolbox.zoomAndMerge2DData(npArraysList,x_lim_min,x_lim_max,y_lim_min,y_lim_max)
            tmpColNames=[self.txt_plot_xlabel.value]+self.dataLabels
            colNames=[]
            for colName in tmpColNames:
                colNames.append(cleanCsvHeaderLine(colName))
            finalDf.columns=colNames
            finalDf.to_csv(targetFileName, index=False, header=True)

            print("saved "+targetFileName)

    def clearFigure(self,clickEvt=None):
        if self.fig is not None:
            self.backend.clear(self.fig,self.ax)
        self.dataLabels=[]
        self.dataHandles=[]

    
    def toggleLegend(self,clickEvt=True):
        if self.fig is not None:
            visible = clickEvt if isinstance(clickEvt,bool) else clickEvt["owner"].value==True
            self.backend.set_legend(self.fig,self.ax,self.dataHandles,self.__cleanLegendLabels(self.dataLabels),visible)

    def showFigure(self):
        widget=self.backend.display(self.fig)
        if widget is not None:
            with self.out_fig:
                self.out_fig.clear_output(wait=True)
                display(widget)


    def createFigure(self):
        self.btn_clear.layout.display='block'
        self.btn_save_figure.layout.display='block'       
        
    def getHOffsetVal(self,data,offsetExpr="0"):
        return datatoolbox.getVerticalOffset(data,offsetExpr)


    def updateRectilinearPlot(self,dfList):

        # Build critConf
        critConf = {
            "name": self.txt_fig_title.value if self.txt_fig_title.value else "Plot",
            "title": self.txt_fig_title.value,
            "description": self.txt_plot_title.value,
            "xLabel": self.txt_plot_xlabel.value,
            "yLabel": self.txt_plot_ylabel.value,
            "figSize": (12, 7),
            "xTimestamp": self.checkbox_x_is_timestamp.value,
        }
        
        dataList=[]
        for df in dfList:
            
            color = self.dropdown_color.value
            if color == "random": color=None

            nbDim=len(df.shape)
            dataIndex=df.index
            offsetVal=self.getHOffsetVal(df,self.txt_vert_offset.value)
            if nbDim==1:
                dataDim=df.shape[0]                            
                dataValues=df.values + offsetVal
                log.info("["+df.name+" offset: "+str(offsetVal)+"]") 

                # removing Nan values from plot to avoid splitting it in seveal lines
                # only in 1D for now (considering a 3D traj has no nan inside ...)
                noNanIndices = ~np.isnan(dataValues)
                xDataToPlot=dataIndex[noNanIndices]
                yDataToPlot=dataValues[noNanIndices]
                
                paramInfo = {
                    "data": pd.DataFrame({df.columns[0]:yDataToPlot},index=xDataToPlot),
                    "linestyle": self.dropdown_linestyle.value,
                    "marker": self.dropdown_markerstyle.value,
                    "drawstyle": self.dropdown_drawstyle.value,
                    "color": color,
                    "name": os.path.basename(df.origin) + "::" + datatoolbox.getDfName(df)
                }
                
                dataList.append(paramInfo)
 
            else:
                dataDim=df.shape[1]
                dataValues=(df+offsetVal).transpose().values

                for dim in range(dataDim):
                    xDataToPlot=dataIndex
                    yDataToPlot=dataValues[dim]
                    if isinstance(offsetVal,(int,float)):
                        curOffsetVal=offsetVal
                    else:
                        curOffsetVal=offsetVal[df.columns[dim]]
                    if curOffsetVal!=0:
                        log.info("["+df.columns[dim]+" offset: "+str(curOffsetVal)+"]")

                    paramInfo = {
                        "data": pd.DataFrame({df.columns[dim]:yDataToPlot},index=xDataToPlot),
                        "linestyle": self.dropdown_linestyle.value,
                        "marker": self.dropdown_markerstyle.value,
                        "drawstyle": self.dropdown_drawstyle.value,
                        "color": color,
                        "name": os.path.basename(df.origin) + "::" + str(df.columns[dim])
                    }

                    dataList.append(paramInfo)
            
        critConf["rendering_engine"]=self.dropdown_backend.value
        self.fig,self.ax,self.dataHandles,self.dataLabels = plotData(critConf, dataList, interactive=True,fig=self.fig,ax=self.ax,dataHandles=self.dataHandles,legendsLabels=self.dataLabels)            
        self.toggleLegend(self.checkbox_legend.value)
        self.showFigure()

    def update3dPlot(self, dfList):
        
        
        critConf = {
            "name": self.txt_fig_title.value if self.txt_fig_title.value else "3D Plot",
            "title": self.txt_fig_title.value,
            "description": self.txt_plot_title.value,
            "xLabel": self.txt_plot_xlabel.value if self.txt_plot_xlabel.value else "X",
            "yLabel": self.txt_plot_ylabel.value if self.txt_plot_ylabel.value else "Y",
            "zLabel": "Z",
            "figSize": (12, 7),
            "rendering_engine": self.dropdown_backend.value,
        }
        
        dataList = []
        for df in dfList:
            color = self.dropdown_color.value
            if color == "random":
                color = None
            
            if len(df.shape) != 2 or df.shape[1] != 3:
                log.error(f"ERROR: 3D plot requires DataFrame with 3 columns, got shape {df.shape}")
                continue
            
            paramInfo = {
                "data": df,
                "linestyle": self.dropdown_linestyle.value,
                "marker": self.dropdown_markerstyle.value,
                "drawstyle": self.dropdown_drawstyle.value,
                "color": color,
                "name": os.path.basename(df.origin) + "::" + datatoolbox.getDfName(df)
            }
            dataList.append(paramInfo)
        
        self.fig, self.ax, self.dataHandles, self.dataLabels = plotData3D(
            critConf, dataList, interactive=True,
            fig=self.fig, ax=self.ax,
            dataHandles=self.dataHandles, legendsLabels=self.dataLabels
        )
        self.toggleLegend(self.checkbox_legend.value)
        self.showFigure()
        

    def update(self,forceUpdate=False, convertSec2Date=None):
          
        if self.fig is None:
            self.createFigure()

        self.onTitlesChange()

        dfList=self.guiComponentsMap["GuiChooseFields"].loadParams()

        if dfList is None or len(dfList)==0:
            return   
          
            
        if self.dropdown_projtype.value=="rectilinear":
            self.updateRectilinearPlot(dfList)
        elif self.dropdown_projtype.value=="3d":
                self.update3dPlot(dfList)
        else:
            print("ERROR: Projection type not implemented : "+self.dropdown_projtype.value)

            

    def show(self):
        self.box.layout.display='block'

    def hide(self):
        self.box.layout.display='none'
        self.btn_clear.layout.display='none'
        self.btn_save_figure.layout.display='none'
        
    ######## PRIVATE ########
    
    def __cleanLegendLabels(self,labelsList):
        cleanedList=[]
        for label in labelsList:
            if len(label)>self.MAX_LEGEND_LABEL_LENGTH:
                cleanedList.append("..."+label[-self.MAX_LEGEND_LABEL_LENGTH:])
            else:
                cleanedList.append(label)

        return cleanedList
##########################################


def runGUI():
    
    JupyterGui.setupDisplay()
    
    guiComponentsMap={}
    fileMgrsList = []

    def selectedFilesChanged(totalNbFields=0,forceUpdate=False):
        guiComponentsMap["GuiDumpH5Tree"].hideTree()
        guiComponentsMap["GuiDumpH5Tree"].show()
        fileName=fileMgrsList[0].getBaseName()
        defaultFigTitle=re.sub(r"\.\S+$","",fileName).replace("_"," ").title()
        guiComponentsMap["GuiPlotFields"].setFigureTitle(defaultFigTitle)

    def selectedFieldsChanged(totalNbFields=0,forceUpdate=False):
        guiComponentsMap["GuiPlotFields"].show()       
        
    JupyterGui.showHeader("Data-Plot")
    
    JupyterGui.GuiFileSelection(guiComponentsMap,fileMgrsList,selectedFilesChanged).show()
    GuiDumpH5Tree(guiComponentsMap,fileMgrsList)
    JupyterGui.GuiChooseFields(guiComponentsMap,fileMgrsList,selectedFieldsChanged)
    GuiPlotFields(guiComponentsMap,fileMgrsList)
    
    