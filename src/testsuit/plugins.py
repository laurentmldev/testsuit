"""Loading of external extensions (data file formats, mexploit criteria, report logo, datapack importers).

An external library extends testsuit by calling the registration functions:

- ``testsuit.datatools.DataFileMgrs.formats.register_file_format()`` / ``register_csv_variant()``
- ``testsuit.exploit.mexploit.registry.register_criterion()`` / ``register_criteria_module()``
- ``testsuit.exploit.runner.report_html.set_default_report_logo()``
- ``testsuit.datatools.datapack.importers.register_data_importer()``

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
"""
from __future__ import annotations

import importlib
import logging
import os
from importlib.metadata import entry_points

ENTRY_POINT_GROUP = "testsuit.plugins"
ENV_VAR = "TESTSUIT_PLUGINS"

_loaded: set[str] = set()


def _call(target: object) -> None:
    if callable(target):
        target()


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
        try:
            _call(ep.load())
            loaded.append(ep.name)
            logging.getLogger("testsuit").info(f"testsuit plugin '{ep.name}' ({ep.value}) loaded")
        except Exception as e:
            logging.getLogger("testsuit").warning(f"testsuit plugin '{ep.name}' ({ep.value}) failed to load: {e}")

    for spec in os.environ.get(ENV_VAR, "").split(","):
        spec = spec.strip()
        if not spec or spec in _loaded:
            continue
        _loaded.add(spec)
        mdlName, _, funcName = spec.partition(":")
        try:
            mdl = importlib.import_module(mdlName)
            _call(getattr(mdl, funcName) if funcName else getattr(mdl, "register", None))
            loaded.append(spec)
            logging.getLogger("testsuit").info(f"testsuit plugin '{spec}' ({ENV_VAR}) loaded")
        except Exception as e:
            logging.getLogger("testsuit").warning(f"testsuit plugin '{spec}' ({ENV_VAR}) failed to load: {e}")

    return loaded
