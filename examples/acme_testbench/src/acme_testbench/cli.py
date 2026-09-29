"""``acme-bench``: command-line tool of the ACME test bench, built on testsuit.

    acme-bench info sample/data                          # data files, their reader and parameters
    acme-bench criteria                                  # the criteria this package adds, with templates
    acme-bench check sample/data within_range BenchTemp_degC min=15 max=40
    acme-bench import sample/datapack/archive_store v1.2 .work/bench_calib/v1.2

It is declared in pyproject.toml ([project.scripts]); like testsuit's own commands, it relies on
the plugin (acme_testbench.plugin) for the formats, criteria and importer.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from importlib.metadata import version

import yaml

from testsuit.plugins import load_plugins

CHECK_PARAM_NAME = "acme_bench_cli_param"


def _files(dataPath: str) -> list:
    # raises FileNotFoundError when no data file is found
    from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr
    return FolderParamMgr(dataPath).getFileMgrs()


def cmd_info(args: argparse.Namespace) -> int:
    """List the data files testsuit finds, with the reader of each one and its parameters."""
    mgrs = _files(args.data)
    for mgr in mgrs:
        print(f"{mgr.getBaseName()}  [{type(mgr).__name__}]")
        for line in mgr.getFileInfo()[1:]:
            print(f"    {line}")
        print(f"    {mgr.getNbEntries()} entries, parameters: {', '.join(mgr.getFieldNames())}")
    return 0


def cmd_criteria(args: argparse.Namespace) -> int:
    """List the mexploit criteria added by acme_testbench, with their scenario template."""
    from testsuit.exploit.mexploit.registry import get_criteria
    ours = {name: func for name, func in sorted(get_criteria().items())
            if func.__module__.startswith("acme_testbench.")}
    for name, func in ours.items():
        doc = (func.__doc__ or "").strip()
        print(f"{name}: {doc.splitlines()[0] if doc else ''}")
        if args.templates and "<TEMPLATE>" in doc:
            template = doc.split("<TEMPLATE>")[1].split("</TEMPLATE>")[0].strip("\n")
            print("    " + template.replace("\n", "\n    "))
    return 0


def _option(text: str) -> tuple[str, object]:
    """'key=value' command-line argument, the value parsed as YAML (numbers, lists, booleans)."""
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"expected key=value, got '{text}'")
    return key, yaml.safe_load(value)


def cmd_check(args: argparse.Namespace) -> int:
    """Run one criterion on one parameter, without writing a scenario."""
    import testsuit.exploit.mexploit.helpers  # noqa: F401  (creates the computed_params module)
    from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr
    from testsuit.exploit.mexploit.registry import get_criterion
    from testsuit.misc.logger import create_logger
    from testsuit.misc.MonitorProgress import MonitorProgress, consoleSilentProgressCb

    criterion = get_criterion(args.criterion)
    if criterion is None:
        print(f"no criterion '{args.criterion}' registered by a plugin (see: acme-bench criteria)")
        return 2

    params = FolderParamMgr(args.data).findParams(
        args.param, silent=True, monitorProgress=MonitorProgress(name="acme-bench", progressCb=consoleSilentProgressCb))
    if len(params) != 1:
        found = [p.name for p in params]
        print(f"'{args.param}' must match exactly one parameter, found {len(found)}: {found}")
        return 2

    # criteria log through testsuit's logger; messages only, like in mexploit logs
    create_logger("acme-bench", config={"consoleLevel": 20, "consoleFormat": "%(message)s",
                                        "consoleDateFormat": "%H:%M:%S"}, reset=True)
    cp = sys.modules["computed_params"]
    setattr(cp, CHECK_PARAM_NAME, params[0])
    critConf = {"name": params[0].name, "type": args.criterion,
                "computed_param": CHECK_PARAM_NAME, **dict(args.options)}
    try:
        criterion(critConf)
    except AssertionError as e:
        print(f"FAILED {str(e).replace('CRIT_CHECK', '').strip()}")
        return 1
    except (KeyError, ValueError, TypeError) as e:
        print(f"ERROR {args.criterion}: {e}")
        return 2
    finally:
        delattr(cp, CHECK_PARAM_NAME)
    print(f"OK [{args.criterion}::{critConf['name']}]")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Import a dataset version from an ACME archive store, as datapack would."""
    from acme_testbench.importer import AcmeArchiveImporter
    importer = AcmeArchiveImporter(args.target, args.store, args.version)
    if os.path.isdir(args.target):
        ok = importer.checkVersion(args.version)
        print(f"{args.target}: {'version ' + args.version + ' OK' if ok else 'NOT version ' + args.version}")
        return 0 if ok else 1
    return 0 if importer.retrieve() else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acme-bench", description="ACME test bench tools, built on testsuit.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('acme-testbench')}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("info", help=cmd_info.__doc__)
    p.add_argument("data", help="data folder or file")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("criteria", help=cmd_criteria.__doc__)
    p.add_argument("-t", "--templates", action="store_true", help="also print the scenario template of each one")
    p.set_defaults(func=cmd_criteria)

    p = sub.add_parser("check", help=cmd_check.__doc__,
                       epilog="example: acme-bench check sample/data max_slope ShaftSpeed_rpm max_per_sec=60")
    p.add_argument("data", help="data folder or file")
    p.add_argument("criterion", help="criterion type, as in scenarios: one registered by a plugin (testsuit's own criteria work on expressions, not on a parameter)")
    p.add_argument("param", help="parameter name (regex), must match a single parameter")
    p.add_argument("options", nargs="*", type=_option, metavar="key=value", help="criterion settings")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("import", help=cmd_import.__doc__,
                       epilog="an existing target is checked (version and checksums) instead of imported")
    p.add_argument("store", help="archive store folder (<version>.zip or <version>/ inside)")
    p.add_argument("version", help="dataset version to import")
    p.add_argument("target", help="folder to import into")
    p.set_defaults(func=cmd_import)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # the formats, criteria and importer of this package (and of any other installed plugin)
    load_plugins()
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(f"ERROR {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
