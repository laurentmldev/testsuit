
from tkinter import Tk
from tkinter import filedialog as fd

def openFileDialog(title=None,filters="",multi=False):
    win=Tk()
    win.withdraw()
    win.attributes('-topmost',1)

    openFileDialog=fd.askopenfilename
    if multi:
        openFileDialog=fd.askopenfilenames
        if title is None:
            title="Choose files"
    else:
        if title is None:
            title="Choose file"
    return openFileDialog(title=title,filetypes=filters,multiple=multi)
    
def openFolderDialog(title=None,):
    win=Tk()
    win.withdraw()
    win.attributes('-topmost',1)

    openFolderDialog=fd.askdirectory
    if title is None:
        title="Choose Directory"
    return openFolderDialog(title=title)
    


def saveFileDialog(title="Choose Target File Name",defaultfile=None,filetypes=None,defaultextension=None):
    win=Tk()
    win.withdraw()
    win.attributes('-topmost',1)

    return fd.asksaveasfilename(title=title,initialfile=defaultfile,filetypes=filetypes,defaultextension=defaultextension)
    