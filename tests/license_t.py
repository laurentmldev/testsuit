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

"""Every tracked source file carries the AGPL license notice (CONTRIBUTING.md, "License notice")."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {".py", ".sh", ".ts", ".tsx", ".js", ".mjs", ".css", ".html"}
VENDORED = ("src/testsuit/exploit/runner/report_libs/",)
NOTICE = (
    "Copyright (C) 2026 Laurent Millet-Lacombe",
    "SPDX-License-Identifier: AGPL-3.0-or-later",
    "This file is part of testsuit.",
    "GNU Affero",
    "General Public License as published by the Free Software Foundation, either version 3 of the License,",
    "or (at your option) any later version.",
)


def source_files():
    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\n")
            if Path(f).suffix in EXTENSIONS and not f.startswith(VENDORED) and (ROOT / f).is_file()]


def test_license_file_is_agpl():
    text = (ROOT / "LICENSE").read_text()
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text and "Version 3, 19 November 2007" in text


def test_every_source_file_has_the_notice():
    files = source_files()
    assert files
    missing = []
    for f in files:
        head = "\n".join((ROOT / f).read_text(errors="replace").splitlines()[:20])
        if not all(part in head for part in NOTICE):
            missing.append(f)
    assert not missing, ("add the license notice (CONTRIBUTING.md, 'License notice') to: " + ", ".join(missing))
