"""testsuit plugin of the toy train: everything this package adds to testsuit, in one place.

``register()`` is declared as an entry point in pyproject.toml::

    [project.entry-points."testsuit.plugins"]
    toytrain = "toytrain.plugin:register"

so once the package is installed, testsuit calls it by itself: in your scripts, in the command-line
tools (data2h5, mxp, exploit_runner, datapack...) and in the mxp processes exploit_runner starts.
Without installing the package, ``TESTSUIT_PLUGINS=toytrain.plugin`` does the same.
"""
from __future__ import annotations

from importlib.resources import files

from testsuit.datatools.DataFileMgrs.formats import register_csv_variant, register_file_format
from testsuit.datatools.datapack.importers import register_data_importer
from testsuit.exploit.mexploit.registry import register_criteria_module
from testsuit.exploit.runner.report_html import set_default_report_logo

from toytrain.filemgrs import PropulsionJsonFileMgr, SimulatedFileMgr, TelemetryCsvFileMgr, isTelemetryCsv
from toytrain.importer import ReleaseImporter

LOGO_SVG = files("toytrain") / "assets" / "toytrain_logo.svg"


def register() -> None:
    # data file formats: a new extension, a fake one, and a CSV syntax
    register_file_format("propulsion-json", ["propj"], PropulsionJsonFileMgr)
    register_file_format("toytrain-simulated", ["sim.yml", "sim.yaml"], SimulatedFileMgr)
    register_csv_variant("toytrain-telemetry", isTelemetryCsv, TelemetryCsvFileMgr)

    # mexploit criteria: a whole module, and the ones checks.py registers with @register_criterion
    register_criteria_module("toytrain.criteria")
    import toytrain.checks  # noqa: F401

    # datapack importer: 'importer: toytrain-release' in datapack setup files
    register_data_importer("toytrain-release", ReleaseImporter)

    # logo on top of every exploit_runner HTML report
    set_default_report_logo(svg=LOGO_SVG.read_text(encoding="utf-8"))
