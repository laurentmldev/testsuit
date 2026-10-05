# Contributing

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

Both commands must pass before a pull request is merged; CI runs them on Python 3.10 and 3.12 and also builds the package.

## Conventions

- All code lives in the `testsuit` package under `src/testsuit/` and is imported as `testsuit.datatools...`, `testsuit.misc...`, never through `sys.path` edits.
- Command-line tools live in `src/testsuit/cli/`. Keep the logic in an importable function (for example `data2h5()` in `cli/data2h5.py`) and put argument parsing in `main()`, so tests can call the function directly.
- Tests are `tests/<topic>_t.py`. Their inputs go in `tests/etc/`, the expected outputs they compare against go in `tests/ref/`, and anything they write goes under `tests/tmp/` (ignored by git).
- New runtime dependencies go in `[project].dependencies` of `pyproject.toml`; dependencies only needed by one feature go in an extra under `[project.optional-dependencies]`.
- Versions come from git tags (setuptools-scm): tag a release as `vX.Y.Z` on `main`.

## Adding a data file format

1. Create `src/testsuit/datatools/DataFileMgrs/<Format>FileMgr.py` with a class deriving from `AFileMgr` (see `CsvFileMgr.py` or `MdfFileMgr.py` for complete examples).
2. Register it at the end of `src/testsuit/datatools/DataFileMgrs/formats.py` with `register_file_format(..., builtin=True)`, using a factory that imports your module, so its dependencies are only needed when such a file is loaded. This also adds its extensions to `SUPPORTED_DATAFILE_EXTENSIONS`.
   A new CSV syntax is a `CsvFileMgr` subclass registered with `register_csv_variant(..., builtin=True)` instead.
3. Add a sample file under `tests/etc/data/` and a case in `tests/loaddata_t.py`.

## Adding a command-line tool

1. Add `src/testsuit/cli/<name>.py` with a `main()` that parses arguments and calls an importable function.
2. Register it under `[project.scripts]` in `pyproject.toml` as `<name> = "testsuit.cli.<name>:main"`.
3. Re-run `pip install -e .` so the command is created, and list it in the README.

## Adding a mexploit check

A scenario entry `type: foo` runs the criterion `foo`, a function `crit_foo(critConf, ...)`. It is looked up in the scenario's own libraries first, then in the criteria registered by external code, then in the default ones.

- For a check everyone can use, add `crit_foo` to `src/testsuit/exploit/mexploit/criteria.py` and cover it with a scenario under `tests/etc/mexploit/scenarii/mxp_OK_main_functions/`.
- For a project-specific check, keep it outside this repo: either register it from your library (see below), or write `crit_foo` in your own module and load it from the scenario with a `# MXP_REGISTER_CRITLIB(path/to/your_criteria.py)` line, as in `tests/etc/mexploit/scenarii/mxp_OK_custom_crit/`.

## Extending testsuit from another library

An application built on testsuit can add formats, criteria, datapack importers and its own report logo without changing this repo. The extension points are tested in `tests/extensions_t.py`. `examples/acme_testbench` is a complete example project using all of them, with its own tests run by CI.

### Data file formats

```python
from testsuit.datatools.DataFileMgrs.formats import register_file_format, register_csv_variant
from testsuit.datatools.DataFileMgrs.CsvFileMgr import CsvFileMgr

# a brand new format: extensions and a factory(filename, fileIdx), usually an AFileMgr subclass
register_file_format("myformat", ["myf"], MyFormatFileMgr)

# a known reader for another extension
register_file_format("mylogs", ["mlog"], CsvFileMgr)

# a CSV syntax, detected from the file (here its first line), read by a CsvFileMgr subclass
register_csv_variant("mylogger",
                     lambda filename: open(filename).readline().startswith("#MYLOGGER"),
                     MyLoggerCsvFileMgr)
```

Registered formats are tried before the built-in ones, so they can also take over a built-in extension; `accepts=lambda filename: ...` limits a format to some of the files with its extensions. `FolderParamMgr`, `loadDataframeFromFile()` and the command-line tools then load these files like the built-in ones.

### mexploit criteria

```python
from testsuit.exploit.mexploit.registry import register_criterion, register_criteria_module

@register_criterion                      # scenario entries 'type: my_check'
def crit_my_check(critConf):
    assert critConf["value"] < critConf["maxValue"], "value too high"

register_criteria_module("mylib.mexploit_criteria")   # every crit_* function of that module
```

Criteria can use the mexploit fixtures and helpers exactly like the ones in `criteria.py`.

### HTML report logo

The exploit_runner report starts with an animated "M-Exploit" logo (`report_html.make_report_logo_svg()`). To use your own:

```python
exploit_runner(conf_file, report_logo_title="ACME Bench")        # same animated logo, your title
exploit_runner(conf_file, report_logo_svg="path/to/logo.svg")    # your SVG (file or markup), "" for none

from testsuit.exploit.runner.report_html import set_default_report_logo
set_default_report_logo(svg="path/to/logo.svg")    # or title="ACME Bench": every report of the process
```

From the command line: `exploit_runner --report-logo logo.svg` or `--report-logo-title "ACME Bench"`.

### datapack importers

```python
from testsuit.datatools.datapack.importers import register_data_importer

# datasource 'importer: myserver' in datapack setup files; any factory(targetDir, remotePath, versionId)
register_data_importer("myserver", MyServerImporter)   # an ADataImporter subclass
```

`remotePath` is the datasource `path`, `versionId` the dataset `path`. After `retrieve()`, the dataset folder must contain a `dataset.dico` file, whose keys the datapack files can use. `git` is the built-in importer.

### Plot backends

```python
from testsuit.datatools.plotBackends import APlotBackend, register_plot_backend

class MyBackend(APlotBackend):        # draws the FigureSpecs of plotBackends.common
    name = "mybackend"                # scenario 'rendering_engine: mybackend', mxp --plot-backend mybackend
    figure_formats = {"svg": ".svg"}  # static figure formats and file suffixes, first one is the default
    ...                               # lines(), lines3d(), histogram(), timeline(), save(); Jupyter methods for the Data-Plot GUI

register_plot_backend(MyBackend())    # or a factory, called on first use
```

The exploit_runner report embeds `.svg`, `.png` and `.jpg` figure files, and plotly JSON (`.plotly.json`).

### Making registrations visible to the command-line tools

The calls above are enough when your code runs testsuit in its own process (`mexploit()`, `exploit_runner()`, `FolderParamMgr`). The command-line tools, and the `mxp` processes that exploit_runner starts for each test run, only see them when they come from a plugin, loaded by `testsuit.plugins.load_plugins()`:

- declare an entry point in your package; its target is called without argument:

  ```toml
  [project.entry-points."testsuit.plugins"]
  mylib = "mylib.testsuit_plugin:register"
  ```

- or list modules in the `TESTSUIT_PLUGINS` environment variable (`module` or `module:function`, comma-separated; a module given alone has its `register()` called if it defines one). Child processes inherit it.

A plugin that fails to load is logged as a warning and skipped.
