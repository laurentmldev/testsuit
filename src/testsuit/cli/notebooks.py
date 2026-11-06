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

"""testsuit-notebooks: copy the Jupyter GUI notebooks shipped with testsuit into a folder, and open them."""
import argparse
import sys

from testsuit.jupytertools.install import install_notebooks, list_notebooks, open_jupyter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="testsuit-notebooks",
        description="Copy the testsuit Jupyter notebooks (" + ", ".join(list_notebooks()) + ") into a folder.")
    parser.add_argument("target_dir", nargs="?", default=".", help="destination folder (default: current folder)")
    parser.add_argument("-n", "--notebook", action="append", dest="names", metavar="NAME",
                        help="copy only this notebook (repeatable), ex: -n data_plot")
    parser.add_argument("-f", "--force", action="store_true", help="overwrite notebooks already in the folder")
    parser.add_argument("-o", "--open", action="store_true",
                        help="then start Jupyter (classic UI) in that folder; needs: pip install 'testsuit[jupyter]'")
    parser.add_argument("-l", "--list", action="store_true", help="list the available notebooks and exit")
    args = parser.parse_args(argv)

    if args.list:
        print("\n".join(list_notebooks()))
        return 0
    try:
        written = install_notebooks(args.target_dir, args.names, overwrite=args.force)
    except ValueError as e:
        parser.error(str(e))
    for path in written:
        print(f"copied {path}")
    if not written:
        print("nothing copied: notebooks already there (use --force to overwrite)")
    if args.open:
        try:
            return open_jupyter(args.target_dir)
        except FileNotFoundError:
            print("ERROR: 'jupyter' not found, install it with: pip install 'testsuit[jupyter]'", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
