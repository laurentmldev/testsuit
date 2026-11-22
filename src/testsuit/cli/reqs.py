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

"""``reqs``: the requirements of a tests repository, and how the tests cover them (sequencer SPEC section 5.4)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from testsuit.plugins import load_plugins


def _sources(args):
    from testsuit.reqs.backend import ReqError
    from testsuit.reqs.sources import Sources, find_sources_file
    file = Path(args.sources) if args.sources else find_sources_file(Path.cwd())
    if file is None:
        raise ReqError("no requirements.yml at the root of this tests repository: give it with --sources")
    return Sources(file)


def _print_tree(nodes, indent=0):
    for n in nodes:
        print("  " * indent + n.id + (f"  {n.label}" if n.label and n.label != n.id else ""))
        _print_tree(n.children, indent + 1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="reqs", description=__doc__)
    parser.add_argument("--sources", help="the sources file (default: requirements.yml at the root of the tests "
                                          "repository of the current folder, or of the first target)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("tree", help="the tree of the requirements of a source")
    p.add_argument("--source", help="source name (default: the default source)")
    p = sub.add_parser("list", help="the requirements under a path")
    p.add_argument("path", nargs="?", default="", help="node path, [<source>:]<path> (default: the root)")
    p.add_argument("-r", "--children", action="store_true", help="with the requirements of the descendants")
    p = sub.add_parser("show", help="requirements by reference, as YAML")
    p.add_argument("refs", nargs="+", help="[<source>:]<id>")
    for name, help_text in (("matrix", "coverage matrices of tests, datapacks, results and campaigns"),
                            ("uncovered", "the requirements under paths that no target links")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("targets", nargs="+", help="test definitions, campaign files (*.campaign.yml), datapack or "
                                                  "results zips, folders of zips")
        p.add_argument("--paths", nargs="+", default=[] if name == "matrix" else [""],
                       help="requirement paths, [<source>:]<path>: their requirements are rows even when not linked"
                            + ("" if name == "matrix" else " (default: every requirement)"))
        p.add_argument("-r", "--children", action="store_true", help="with --paths: their descendants too")
        p.add_argument("--offline", action="store_true", help="only the snapshots of the datapacks: no current "
                                                              "versions (changed requirements are not flagged)")
        if name == "matrix":
            p.add_argument("--format", choices=["text", "yaml", "csv", "html"], default="text")
            p.add_argument("--view", choices=["reqs", "tests"], default="reqs", help="CSV: requirements × tests "
                                                                                     "(default) or tests × requirements")
            p.add_argument("--sequencer", default="", help="HTML: URL of the sequencer, for links to the runs and "
                                                           "their exploit reports")
            p.add_argument("--title", default="Requirements coverage", help="HTML: title of the page")
            p.add_argument("-o", "--output", help="write to this file instead of the standard output")
    args = parser.parse_args(argv)
    load_plugins()

    from testsuit.reqs.backend import ReqError
    try:
        if args.command == "tree":
            sources = _sources(args)
            _print_tree(sources.tree(args.source))
        elif args.command == "list":
            sources = _sources(args)
            source, sep, node = args.path.partition(":")
            if not sep or source not in sources.names:
                source, node = sources.default, args.path
            for key, req in sorted(sources.list(node, args.children, source).items()):
                title = req.title or (req.description.splitlines() or [""])[0]
                print(f"{key}\t{req.path}\t{title}")
        elif args.command == "show":
            sources = _sources(args)
            refs = [sources.ref(r) for r in args.refs]
            found = sources.get(refs)
            out = {sources.key(r): (found[r].as_dict() if found.get(r) else None) for r in refs}
            print(yaml.safe_dump(out, sort_keys=False, allow_unicode=True), end="")
            return 0 if all(out.values()) else 1
        else:
            from testsuit.reqs import render
            from testsuit.reqs.coverage import coverage
            cov = coverage([Path(t) for t in args.targets], Path(args.sources) if args.sources else None,
                           args.paths or None, args.children, live=not args.offline)
            if args.command == "uncovered":
                for key in cov.uncovered():
                    req = cov.requirements.get(key, {})
                    print(f"{key}\t{req.get('path', '')}\t{req.get('title', '')}")
                for problem in cov.problems:
                    print(f"problem: {problem}", file=sys.stderr)
                return 1 if cov.uncovered() else 0
            text = {"text": render.text, "yaml": render.as_yaml,
                    "csv": lambda c: render.as_csv(c, args.view),
                    "html": lambda c: render.as_html(c, args.title, args.sequencer)}[args.format](cov)
            if args.output:
                Path(args.output).write_text(text, encoding="utf-8")
            else:
                sys.stdout.write(text)
    except (ReqError, ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
