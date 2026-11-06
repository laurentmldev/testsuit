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

"""Render a template file (or every file of a folder) with Jinja2, using YAML dictionaries."""
import argparse
import sys
from pathlib import Path

from testsuit.datatools.datapack.templating import Keys, TemplateError, expand_dotted_keys


# override the parsing error message using logger
class _HelpParser(argparse.ArgumentParser):
    def error(self, message):
        print("Input Arguments Error : "+message)
        sys.exit(1)


def _key_value(text):
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"expected key=value, got '{text}'")
    return key, value


## the main function
def main():
    parser = _HelpParser(description=
"""Render a template file, or every file of a folder, with Jinja2.

Keys come from the given YAML dictionaries, later ones overriding earlier ones, then from --set.
Includes ('{% include "x" %}') are looked up in the folder of the rendered file, then in --include-dir folders.
Without --output, the rendered file is printed on STDOUT.

Return 1 if a key is undefined or a template invalid, 0 otherwise""",
    formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('template',help="the file or folder to render",type=Path)
    parser.add_argument('dictionaries',nargs='*',help="YAML dictionary files",type=Path)
    parser.add_argument('--set','-s',action='append',default=[],type=_key_value,metavar="KEY=VALUE",help="a key (dotted name) and its value")
    parser.add_argument('--include-dir','-I',action='append',default=[],type=Path,help="folder where to look for included files")
    parser.add_argument('--output','-o',type=Path,help="output file (or folder, when rendering a folder)")
    args = parser.parse_args()

    try:
        keys = Keys()
        for dictionary in args.dictionaries:
            keys.add_file(dictionary)
        if args.set:
            keys.add(expand_dotted_keys(dict(args.set)), origin="--set")
        if args.output is not None:
            keys.render_tree(args.template, args.output, args.include_dir)
        elif args.template.is_file():
            text, _ = keys.render_file(args.template, args.include_dir)
            sys.stdout.write(text)
        else:
            parser.error("--output is required to render a folder")
    except TemplateError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
