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

"""Install the Start-menu shortcut of the conda package (menuinst v2): conda/menu/ into $PREFIX/Menu/.

Run by the build script of conda/meta.yaml, after pip install. conda (and the Windows installer of
conda/installer/) creates the shortcut from Menu/testsuit.json when the package is installed, and removes it
when it is uninstalled. The JSON cannot carry the license header: menuinst reads it as plain JSON.
"""

import os
import shutil
from pathlib import Path

menu_src = Path(__file__).resolve().parent / "menu"
menu_dst = Path(os.environ["PREFIX"]) / "Menu"
menu_dst.mkdir(parents=True, exist_ok=True)
for name in ("testsuit.json", "testsuit.ico", "testsuit.png"):
    shutil.copy2(menu_src / name, menu_dst / name)
