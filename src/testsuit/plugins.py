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

"""Loading of external extensions (data file formats, mexploit criteria, report logo, datapack importers and processors).

An external library extends testsuit by calling the registration functions:

- ``testsuit.datatools.DataFileMgrs.formats.register_file_format()`` / ``register_csv_variant()``
- ``testsuit.exploit.mexploit.registry.register_criterion()`` / ``register_criteria_module()``
- ``testsuit.exploit.runner.report_html.set_default_report_logo()``
- ``testsuit.datatools.datapack.importers.register_data_importer()``
- ``testsuit.datatools.datapack.processors.register_processor()``
- ``testsuit.datatools.datapack.generators.register_generator()``
- ``testsuit.reqs.register_req_backend()`` (requirements sources)
- ``testsuit.sequencer.registry``: ``register_transport()``, ``register_role()``, ``register_component()``, ``register_state_probe()``,
  ``register_step_type()``, ``register_ready_check()`` (test sequencer)

Calling them from your own code is enough when everything runs in your process. The command-line
tools (``data2h5``, ``mxp``, ``exploit_runner``, ``datapack``, ...) and the ``mxp`` processes that exploit_runner
starts are separate processes, so they find your registrations through one of:

- an entry point in the ``testsuit.plugins`` group of your package::

      [project.entry-points."testsuit.plugins"]
      mylib = "mylib.testsuit_plugin:register"

- the ``TESTSUIT_PLUGINS`` environment variable: comma-separated ``module`` or ``module:function``
  (inherited by child processes).

The entry point target, or ``function``, is called without argument; a module given alone is
imported and its ``register()`` function, if any, is called.

What each plugin registered while it loaded is recorded (the registration functions call
``note_registration()``): ``plugins_report()`` lists the plugins found, whether they loaded, and what they
include. The Configuration tab of the testsuit app shows it.
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
from importlib.metadata import entry_points
from typing import Any

ENTRY_POINT_GROUP = "testsuit.plugins"
ENV_VAR = "TESTSUIT_PLUGINS"

_loaded: set[str] = set()
# what load_plugins() did with each plugin, in load order (see plugins_report())
_records: list[dict[str, Any]] = []
# the record of the plugin being loaded: the registrations made meanwhile are its own
_current: list[dict[str, Any]] = []


def _call(target: object) -> None:
    if callable(target):
        target()


def _by_testsuit() -> bool:
    """Whether the registration function calling note_registration() was called by testsuit itself: its
    built-in formats, backends... registered when a plugin first imports their module are not the plugin's."""
    frame = sys._getframe(2)            # the registration function
    registering = frame.f_globals.get("__name__")
    while frame is not None and frame.f_globals.get("__name__") == registering:
        if frame.f_code.co_name == "<module>":   # at the import of the registration module itself
            return True
        frame = frame.f_back
    caller = frame.f_globals.get("__name__", "") if frame is not None else ""
    return caller == "testsuit" or caller.startswith("testsuit.")


def note_registration(kind: str, name: str) -> None:
    """Called by the registration functions: while a plugin loads, ``name`` is listed among the ``kind`` it
    includes ("data file formats", "mexploit criteria"...). Does nothing outside load_plugins(), nor for
    testsuit's own registrations."""
    if not _current or _by_testsuit():
        return
    names = _current[-1]["includes"].setdefault(kind, [])
    if name not in names:
        names.append(name)


def _load(record: dict[str, Any], load: Any) -> bool:
    """Run ``load()`` as the plugin of ``record`` (its registrations noted in it), recording the outcome."""
    _records.append(record)
    _current.append(record)
    try:
        load()
        record["status"] = "loaded"
        return True
    except Exception as e:
        record["status"] = "failed"
        record["error"] = f"{type(e).__name__}: {e}"
        return False
    finally:
        _current.pop()


def _dist(ep: Any) -> tuple[str | None, str | None]:
    dist = getattr(ep, "dist", None)
    if dist is None:
        return None, None
    try:
        return dist.metadata["Name"], dist.version
    except Exception:
        return None, None


def load_plugins() -> list[str]:
    """Load the plugins declared by entry points and in TESTSUIT_PLUGINS, once each.

    A plugin failing to load is logged and skipped, so it does not break the other ones.

    :return: names of the plugins loaded by this call
    """
    loaded = []
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        key = "ep:" + ep.name
        if key in _loaded:
            continue
        _loaded.add(key)
        package, version = _dist(ep)
        record = {"name": ep.name, "source": "entry point", "target": ep.value, "package": package,
                  "version": version, "includes": {}}
        if _load(record, lambda ep=ep: _call(ep.load())):
            loaded.append(ep.name)
            logging.getLogger("testsuit").debug(f"testsuit plugin '{ep.name}' ({ep.value}) loaded")
        else:
            logging.getLogger("testsuit").warning(f"testsuit plugin '{ep.name}' ({ep.value}) failed to load: {record['error']}")

    for spec in os.environ.get(ENV_VAR, "").split(","):
        spec = spec.strip()
        if not spec or spec in _loaded:
            continue
        _loaded.add(spec)
        mdlName, _, funcName = spec.partition(":")

        def load(mdlName: str = mdlName, funcName: str = funcName) -> None:
            mdl = importlib.import_module(mdlName)
            _call(getattr(mdl, funcName) if funcName else getattr(mdl, "register", None))
        record = {"name": spec, "source": ENV_VAR, "target": spec, "package": None, "version": None, "includes": {}}
        if _load(record, load):
            loaded.append(spec)
            logging.getLogger("testsuit").debug(f"testsuit plugin '{spec}' ({ENV_VAR}) loaded")
        else:
            logging.getLogger("testsuit").warning(f"testsuit plugin '{spec}' ({ENV_VAR}) failed to load: {record['error']}")

    return loaded


def plugins_report() -> list[dict[str, Any]]:
    """The plugins of this process: those load_plugins() tried, then those it would find now but has not loaded
    (installed or named in TESTSUIT_PLUGINS since).

    Each one: ``name``, ``source`` ("entry point" or "TESTSUIT_PLUGINS"), ``target`` (``module:function``),
    ``package`` and ``version`` (of an entry point's distribution), ``status`` ("loaded", "failed" or
    "not loaded"), ``error`` (when failed) and ``includes``: what it registered while it loaded, by kind
    ({"data file formats": ["myformat"], ...}).
    """
    out = [{**r, "includes": {k: list(v) for k, v in r["includes"].items()}} for r in _records]
    try:
        eps = list(entry_points(group=ENTRY_POINT_GROUP))
    except Exception:
        eps = []
    for ep in eps:
        if "ep:" + ep.name not in _loaded:
            package, version = _dist(ep)
            out.append({"name": ep.name, "source": "entry point", "target": ep.value, "package": package,
                        "version": version, "status": "not loaded", "includes": {}})
    for spec in os.environ.get(ENV_VAR, "").split(","):
        spec = spec.strip()
        if spec and spec not in _loaded:
            out.append({"name": spec, "source": ENV_VAR, "target": spec, "package": None, "version": None,
                        "status": "not loaded", "includes": {}})
    return out
