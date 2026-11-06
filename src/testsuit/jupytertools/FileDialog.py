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
    