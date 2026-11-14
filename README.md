# testsuit

Toolbox for preparing and analysing test data: load measurement files (CSV, TDMS, MDF, Dewesoft DXD, UDBF, HDF5, InfluxDB, ClickHouse) into pandas, convert them to HDF5 or push them to a database, check results against YAML scenarios, and package test inputs into datapacks.

## Installation

Python 3.10 or newer is required.

```bash
git clone https://github.com/laurentmldev/testsuit.git
cd testsuit
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

Optional features are installed as extras:

| Extra      | Adds                                         |
|------------|----------------------------------------------|
| `plotly`   | plotly figures updated in place in Jupyter   |
| `jupyter`  | notebook GUIs (`jupytertools`)               |
| `influxdb` | InfluxDB readers and `data2db` targets       |
| `test`     | what the test suite needs                    |
| `dev`      | `test` plus build and lint tools             |

For example `pip install -e ".[plotly,jupyter]"`.

To use testsuit without cloning it, install straight from GitHub (add `@<tag>` to pin a version):

```bash
pip install "testsuit[jupyter] @ git+https://github.com/laurentmldev/testsuit.git"
```

## Jupyter GUIs

Three notebook GUIs come with the package: Data-Plot (browse and plot parameters), H5-Conv (convert data files to HDF5) and M-Exploit (run a scenario and view its report). Copy their starter notebooks into a working folder and open Jupyter there:

```bash
pip install "testsuit[jupyter]"          # or: pip install -e ".[jupyter]" from a clone
testsuit-notebooks ~/my_analysis --open  # copies data_plot, h5_convert and mexploit .ipynb
```

The notebooks only call `runGUI()` from the installed package, so upgrading testsuit upgrades the GUIs without copying the notebooks again (`--force` refreshes them). The GUIs need the classic notebook UI (`jupyter nbclassic`, what `--open` starts), and a desktop session for their file dialogs (tkinter).

## Plots

Figures (Data-Plot GUI, mexploit `plot*` checks, sequence timelines) are drawn by a plot backend:

| Backend | Jupyter | mexploit / exploit_runner report |
|---|---|---|
| `plotly` (default) | plotly FigureWidget (with `pip install "testsuit[plotly]"`) | interactive figure (zoom, hover, 3D rotation), or `svg`/`png` with kaleido |
| `matplotlib` | matplotlib figure | SVG (`figure_format: png` also works) |

Pick it per check with `rendering_engine: matplotlib` in the scenario YAML, per run with `mxp --plot-backend matplotlib` / `exploit_runner --plot-backend matplotlib` (or the `PLOT_RENDERING_BACKEND` environment variable), and in the Data-Plot GUI with its Backend menu. The figure format works the same way: `figure_format`, `--figure-format`, `PLOT_FIGURE_FORMAT`.

The exploit_runner HTML report stays a single file that works offline with plotly figures: they are stored as gzipped plotly JSON and drawn by plotly.js, which the report then embeds once (about 2 MB). Reports made with matplotlib only don't embed it. In mxp's own `pytest_report.html`, each plotly figure is drawn in a frame by a page of `pytest_report_files/`, which shares one copy of plotly.js. Another library can add its own backend with `testsuit.datatools.plotBackends.register_plot_backend()`.

## Datapacks

The `datapack` command builds the input files of every slot of a test setup and packs them in a zip. A test definition names a setup and its configuration; the setup declares data repositories (datasets imported by importers such as `git`), slot types with their input files, and configurations listing the slots (each bound at run time to a lab component by the test sequencer):

```yaml
# testdef.yml
id: toytrain_max_speed
name: Max speed
setup_file: ../../setup/toytrain_setup.yml
setup_config: sim_bench
dictionaries: [scenario.yml]
```

Input files are Jinja2 templates (`{{ train.speed_limit_mps }}`, `{% include "x" %}`, loops, filters) rendered with keys from YAML dictionaries: setup dictionaries first, then imported datasets, then test definition dictionaries, each overriding the previous ones. A key can reference other keys (`output_dir: "{{ testdef.path }}/../datapacks"`), and an undefined key is an error. The zip follows the BagIt layout: `data/<slot>/input/` holds the generated files, `datapack.yml` records what they were built from and which keys each file used, and `context.yml` holds every resolved key, so a datapack can be used without its dictionaries. `datapack --setup-config <name>` uses another configuration of the setup than the test definition's, for example to build the same test for another platform. `datapack --verify <zip>` checks its checksums. `examples/toytrain/datapack` is a complete example. The [datapack README](src/testsuit/datatools/datapack/README.md) gives the usage, the file formats and the architecture.

## Repository layout

```
src/testsuit/           the installable package
  datatools/            load data files into pandas, convert to HDF5, push to a DB
    plotBackends/       matplotlib and plotly drawing of plotHelpers' figures
    DataFileMgrs/       one reader per file format (Csv, Tdms, Mdf, Dxd, Udbf, H5, InfluxDb, Clickhouse)
    datapack/           datapack generation: YAML dictionaries, Jinja2 templates, BagIt zip
  exploit/              checks of recorded data, see its README
    mexploit/           YAML scenario checks (corridors, sequences, computed params, plots) run through pytest
    runner/             runs mexploit over test sessions and builds HTML reports
  sequencer/            test sequencer: tsqd daemon, REST API, run engine, web UI and Editor (tsq command line in cli/, UI sources in ui/)
  jupytertools/         ipywidgets GUIs for notebooks
  misc/                 logging, progress reporting, file helpers
  cli/                  command-line tools (see below)
docs/diagrams/          PlantUML figures of the READMEs (sources and rendered SVG)
examples/toytrain/      the example project: a toy train's test environment (library extending testsuit, sequencer configuration)
tests/                  pytest suite (*_t.py)
  etc/                  input data, scenarios and configs used by the tests
  ref/                  reference outputs the tests compare against
```

## Command-line tools

Installing the package puts these commands on your `PATH`. Each prints its full usage with `--help`, for example `data2h5 --help`. They can also be run as `python -m testsuit.cli.<name>`.

| Command                  | Purpose                                                           |
|--------------------------|-------------------------------------------------------------------|
| `data2h5`                | extract parameters from data files or folders into an HDF5 file   |
| `data2db`                | push parameters from data files into a database (InfluxDB, ClickHouse) |
| `extract_data_any`       | decode raw bit fields into parameters from a JSON/YAML config (example: `examples/config_extract_data_any.json`) |
| `extract_data_flags`     | extract flag values described in a YAML file                      |
| `apply_clock_correction` | shift timestamps of a folder of data files                        |
| `mxp`                    | run a mexploit scenario folder against data folders; `mxp describe`, `mxp lint`, `mxp utest` help to write and test scenarios, `mxp edit` opens them in the criteria editor (`pip install "testsuit[editor]"`) |
| `exploit_runner`         | run mexploit over whole test sessions from a YAML config          |
| `datapack`               | generate a datapack from a test definition file                   |
| `tsq`                    | test sequencer client: submit datapacks, follow runs, manage the lab (`pip install "testsuit[sequencer]"`) |
| `tsqd`                   | test sequencer daemon: queue, lab locks, runs, REST API, web UI at `/ui` ([sequencer README](src/testsuit/sequencer/README.md)) |
| `evalfile`               | render a Jinja2 template file with keys from YAML dictionaries    |
| `testsuit-notebooks`     | copy the Jupyter GUI notebooks into a folder, optionally open Jupyter |

## Using the library

```python
from testsuit.datatools.datatoolbox import loadDataframeFromFile
from testsuit.exploit.mexploit.mexploit import mexploit

# run the checks of a scenario folder on a data folder, results written to out/
dfs = loadDataframeFromFile("tests/etc/data/csv/flags.csv", ".*")  # one DataFrame per parameter

rc = mexploit("tests/etc/mexploit/scenarii/mxp_OK_main_functions", "tests/etc/data", "out/", force=True)
```

The scenarios under `tests/etc/mexploit/scenarii/` are working examples of every check type. The [exploit README](src/testsuit/exploit/README.md) gives the usage of `mxp` and `exploit_runner`, the scenario and configuration formats, and the architecture.

## Running the tests

```bash
pip install -e ".[dev]"
pytest          # runs tests/*_t.py
ruff check .    # catches runtime errors such as undefined names
```

CI (`.github/workflows/tests.yml`) runs the same lint, the tests on Python 3.10 and 3.12, and a package build, on every pull request.

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add a file format, a mexploit check or a script, and how another library can register its own formats, criteria and report logo.

## License

Copyright (C) 2026 Laurent Millet-Lacombe.

testsuit is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE) for the full text.

The AGPL also covers network use: if you run a modified tsqd for other people, you must offer them its source code. The web UI links to the source from its top bar; point that link at your own copy if you change the code.

Every source file starts with the license notice (see [CONTRIBUTING.md](CONTRIBUTING.md#license-notice)). The vendored libraries under `src/testsuit/exploit/runner/report_libs/` keep their own licenses.
