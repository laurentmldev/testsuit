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

The plotly widgets, the data file formats (InfluxDB, ClickHouse, TDMS, MDF) and the web UI (`mxp webui`) come with
the default install. Optional features are installed as extras:

| Extra          | Adds                                                                 |
|----------------|----------------------------------------------------------------------|
| `jupyter`      | notebook GUIs (`jupytertools`)                                       |
| `dwdatareader` | DXD (Dewesoft) files                                                 |
| `sequencer`    | the test sequencer `tsq`/`tsqd` and its web UI                       |
| `test`         | what the test suite needs                                            |
| `dev`          | `test` plus build and lint tools                                     |

`plotly`, `file_formats` and `editor` still exist as empty extras, so older install commands keep working.

For example `pip install -e ".[jupyter,dwdatareader]"`.

To use testsuit without cloning it, install straight from GitHub (add `@<tag>` to pin a version):

```bash
pip install "testsuit[jupyter] @ git+https://github.com/laurentmldev/testsuit.git"
```

### Windows installer

For a Windows machine with no Python (or with another Python, or Anaconda, which it leaves alone): one
`testsuit-<version>-Windows-x86_64.exe` installs its own Python, testsuit and all its dependencies, offline, with
a **testsuit** shortcut in the Start menu and on the desktop that runs the mexploit web UI (`mxp webui --last`, as
the Navigator tile below). Get it from the `conda` workflow (artifact `testsuit-<version>-Windows-x86_64.exe` of a run, the
installer itself, not zipped, on each
`v*` tag or by hand from the Actions tab). Windows 10 or later, 64-bit; no admin rights needed: it installs for the
current user in `%LOCALAPPDATA%\testsuit` by default, does not add itself to `PATH` nor register its Python as the
system's default, and is removed from Windows' *Installed apps*. An installer of a newer version installs over it
(uninstall the old one first, or pick another folder).

Plugins (entry points of the `testsuit.plugins` group) are installed with its own Python, from a terminal:

```bat
"%LOCALAPPDATA%\testsuit\python.exe" -m pip install <plugin, wheel or git URL>
```

The shortcut, and the `mxp` and `exploit_runner` processes it starts, load them at each launch. Without installing it,
a plugin can also be declared in the `TESTSUIT_PLUGINS` user environment variable (`module` or `module:function`,
comma-separated), with its folder in `PYTHONPATH`. Installing a newer version of testsuit may remove the plugins
installed with pip: install them again after an upgrade.

It is built with [conda constructor](https://github.com/conda/constructor) from the conda package below
(recipe in [conda/installer/](conda/installer/construct.yaml)), on Windows:

```bash
conda install -n base constructor conda-standalone
cp LICENSE conda/installer/LICENSE.txt
TESTSUIT_VERSION=<version> TESTSUIT_CHANNEL=file:///C:/conda-channels/testsuit-conda-channel constructor conda/installer
```

### From Anaconda Navigator

Installed with conda, testsuit shows in Anaconda Navigator as the **testsuit** app (its tile runs the mexploit web UI). The package is not published to
any channel: it is installed from a local folder. Get that folder from the `conda` workflow (artifact
`testsuit-conda-channel` of a run, on each `v*` tag or by hand from the Actions tab: unzip it anywhere), or build it
yourself (recipe in [conda/](conda/meta.yaml)):

```bash
cd ui && npm ci && npm run build && cd ..
python -m build --sdist                    # dist/testsuit-<version>.tar.gz, with the web UI
TESTSUIT_VERSION=<version> TESTSUIT_SDIST=$PWD/dist/testsuit-<version>.tar.gz conda build conda -c conda-forge --output-folder testsuit-conda-channel
```

Unzip or build that folder somewhere it stays (`C:\conda-channels\testsuit-conda-channel` below), then install it
in either of two ways.

**In Navigator**: on the *Home* page, *Channels*, *Add*: `file:///C:/conda-channels/testsuit-conda-channel` (forward
slashes, also on Windows), and *Add* `conda-forge` too (the dependencies come from there); *Update channels*.
**testsuit** shows on *Home*: *Install*, then *Launch* (installing it also adds a **testsuit** shortcut to the
Start menu, which runs the same). To upgrade, unzip the new version into the same folder, then
*Update channels*: the tile offers the new version.

> **Tips for the Channels dialog.** There is no *Browse* button: the field takes a name or a URL only, so a plain
> path (`C:\...`) is refused as you type. Type or paste the folder as `file:///` (three slashes) followed by its path
> with forward slashes, then press **Enter**: Navigator checks that the folder exists, and only then enables
> *Update channels* (a tooltip says why when it refuses). The folder is the one holding `noarch\` (with
> `repodata.json` in it): a zip unzipped into a folder of its own often adds one level too many.
>
> **Anaconda installed by an administrator**: adding a channel needs no rights (it goes to your own
> `C:\Users\<you>\.condarc`), but the `base` environment (`C:\ProgramData\anaconda3`) cannot be written to. Create an
> environment of your own (*Environments*, *Create*: it goes to `C:\Users\<you>\.conda\envs`), select it in
> *Applications on* on the *Home* page, then *Install*. An administrator may also restrict the channels for everyone
> (`allowlist_channels` in the system `.condarc`): then only they can allow this one.

**In a terminal** on the environment Navigator shows (*Anaconda Prompt* on Windows), its dependencies from conda-forge:

```bash
conda install -c C:/conda-channels/testsuit-conda-channel -c conda-forge testsuit
```

then restart Navigator (or *Refresh* its *Home* page): **testsuit** is there, installed. The same command upgrades it
from a newer folder. Upgrade testsuit in that environment with conda, not `pip install -U`, which would leave conda
and Navigator on the old version.

*Launch* runs `mxp webui --last`: the folder opened last, or a folder picker the first time; *Change folder…* in its
bar opens another one, and *Stop* stops it (launching it again while it runs only opens the browser on it). Plugins
(entry points of the `testsuit.plugins` group) must be installed in the same environment: `pip install` them from a
terminal on it (*Environments*, ▶, *Open Terminal* in Navigator); upgrading testsuit keeps them.

Navigator shows no console: when *Launch* seems to do nothing, open http://127.0.0.1:8650 (the server may be running
already), and read `testsuit\mxp_webui.log` in `%APPDATA%` (`~/.config/testsuit/mxp_webui.log` elsewhere), which
tells what each launch did: the folder, the address, whether the browser opened.

On Linux, `mxp webui` (and `tsq view`) open the browser with the desktop's own openers (`$BROWSER`, `xdg-open`,
`gio open`, then the usual browsers), run without the conda environment's folders on their `PATH`: the `gio` of a
conda environment has no handler for web pages (it printed `gio: <url>: Operation not supported` and no browser
opened). What the openers print goes to the log; when none opens a browser, the command says to open the address by
hand.

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
| `plotly` (default) | plotly FigureWidget  | interactive figure (zoom, hover, 3D rotation), or `svg`/`png` with kaleido |
| `matplotlib` | matplotlib figure | SVG (`figure_format: png` also works) |

Pick it per check with `rendering_engine: matplotlib` in the scenario YAML, per run with `mxp --plot-backend matplotlib` / `exploit_runner --plot-backend matplotlib` (or the `PLOT_RENDERING_BACKEND` environment variable), and in the Data-Plot GUI with its Backend menu. The figure format works the same way: `figure_format`, `--figure-format`, `PLOT_FIGURE_FORMAT`.

The exploit_runner HTML report stays a single file that works offline with plotly figures: they are stored as gzipped plotly JSON and drawn by plotly.js, which the report then embeds once (about 2 MB). Reports made with matplotlib only don't embed it. In mxp's own `pytest_report.html`, each plotly figure is drawn in a frame by a page of `pytest_report_files/`, which shares one copy of plotly.js. Another library can add its own backend with `testsuit.datatools.plotBackends.register_plot_backend()`.

## Writing criteria

The checks run on test results are mexploit scenarios: YAML files of entries, each running a criterion (a `crit_*` function, built in or from a plugin) with its fields. Each criterion declares its fields (a Pydantic model), so the tools know what a scenario may contain:

| Tool | What it does |
|---|---|
| `mxp describe` | every criterion a scenario can use, with its help and the JSON Schema of its fields; the helpers of expressions |
| `mxp lint` | checks a scenario without running it: fields, expressions, computed parameters, parameter names against data (`--auto`: with the keys of its test definition) |
| `mxp utest` | unit tests of a scenario: small data cases (`criteria_utests/<scenario>/utests.yml`), each with the status every check must give, and the coverage of each check |
| `mxp webui` | the web UI (on the current folder, or `--last`: the folder opened last; *Change folder…* opens another one): the criteria editor in the browser: completion of the criteria, fields, helpers, parameters and keys, problems as you type, the unit tests with their matrix and coverage, the data of the cases as tables and plots; **Exploit**, which runs exploit_runner on scenarios, dictionaries, keys set by hand and data picked in the page; and **Plot**, to plot the parameters of data files by hand (2D or 3D, a style per trace, exports to PNG, HTML and CSV). Also in tsqd (criteria on the Datapacks tab, and an Exploit tab with exploit_runner and Plot, on the data of its runs). |
| `mxp vscode` | sets up VSCode with no extension of its own: schemas for completion and validation, unit tests in the Testing view, tasks for lint (Problems view), utest and edit |

Scenarios are Jinja templates rendered before they run: a value can be a key of the test's dictionaries (`max: "{{ train.speed_limit_mps }}"`), a shared part can be included (`{% include "limits.inc.yml" %}`), and a loop can apply the same checks to several parameters (`{% for p in train.monitored %}`). The tools above render them too, and report each problem on the line where it is written ([examples](src/testsuit/exploit/README.md#templating-with-jinja-keys-includes-and-loops)).

An unknown field in a scenario is a warning when it runs, in its log and in the exploit_runner report; it will be an error in a next version. The [exploit README](src/testsuit/exploit/README.md#writing-scenarios-describe-lint-and-unit-tests) tells more, and [examples/toytrain](examples/toytrain) has unit tests for its criteria, every check covered.

## Datapacks

The `datapack` command builds the input files of every slot of a test setup and packs them in a zip. A test definition names a setup and its configuration; the setup declares data repositories (datasets imported by importers such as `git`), slot classes with their input files, and configurations listing the slots (each bound at run time to a lab component by the test sequencer):

```yaml
# testdef.yml
id: toytrain_max_speed
name: Max speed
setup_file: ../../setup/toytrain_setup.yml
setup_config: sim_bench
dictionaries: [scenario.yml]
```

Input files are Jinja2 templates (`{{ train.speed_limit_mps }}`, `{% include "x" %}`, loops, filters) rendered with keys from YAML dictionaries: setup dictionaries first, then imported datasets, then test definition dictionaries, each overriding the previous ones. A key can reference other keys (`output_dir: "{{ testdef.path }}/../datapacks"`), and an undefined key is an error. The zip follows the BagIt layout: `data/<slot>/input/` holds the generated files, `datapack.yml` records what they were built from and which keys each file used, and `context.yml` holds every resolved key, so a datapack can be used without its dictionaries. `datapack --setup-config <name>` uses another configuration of the setup than the test definition's, for example to build the same test for another platform. `datapack --verify <zip>` checks its checksums. A **campaign** file (`<name>.campaign.yml`) lists test definitions to generate with the same version of every dataset and to run together: `datapack <name>.campaign.yml` builds all their datapacks, and the sequencer runs them as one campaign run with a synthetic report. Test definitions and checks can list the **requirements** they verify (`reqs`), from a YAML file of the tests repository or any requirements tool through a plugin: `reqs matrix` builds the coverage matrices of tests, results and campaigns, and `reqs uncovered` lists what no test covers ([sequencer README, section 10.3](src/testsuit/sequencer/README.md#103-requirements-coverage)). `examples/toytrain/datapack` is a complete example. To have an AI write a test definition from a test specification, give it [docs/ai/testdef_prompt.txt](docs/ai/testdef_prompt.txt) followed by the specification, the lab roles and, when there is one, the setup to reuse (the prompt lists its inputs). The [datapack README](src/testsuit/datatools/datapack/README.md) gives the usage, the file formats and the architecture.

## Repository layout

```
src/testsuit/           the installable package
  datatools/            load data files into pandas, convert to HDF5, push to a DB
    plotBackends/       matplotlib and plotly drawing of plotHelpers' figures
    DataFileMgrs/       one reader per file format (Csv, Tdms, Mdf, Dxd, Udbf, H5, InfluxDb, Clickhouse)
    datapack/           datapack generation: YAML dictionaries, Jinja2 templates, BagIt zip
  reqs/                 requirements coverage: sources and their backends (plugins), links, snapshots, matrices
  exploit/              checks of recorded data, see its README
    mexploit/           YAML scenario checks (corridors, sequences, computed params, plots) run through pytest
    runner/             runs mexploit over test sessions and builds HTML reports
  sequencer/            test sequencer: tsqd daemon, REST API, run engine, web UI and Editor (tsq command line in cli/, UI sources in ui/)
  jupytertools/         ipywidgets GUIs for notebooks
  misc/                 logging, progress reporting, file helpers
  cli/                  command-line tools (see below)
docs/diagrams/          PlantUML figures of the READMEs (sources and rendered SVG)
docs/ai/                prompts for AI tools: testdef_prompt.txt writes a test definition from a specification
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
| `mxp`                    | run a mexploit scenario folder against data folders; `mxp describe`, `mxp lint`, `mxp utest`, `mxp webui`, `mxp vscode` help to write and test scenarios ([Writing criteria](#writing-criteria)) |
| `exploit_runner`         | run mexploit over whole test sessions from a YAML config          |
| `datapack`               | generate a datapack from a test definition file                   |
| `reqs`                   | requirements coverage: browse the requirements sources, coverage matrices of tests, results and campaigns, uncovered requirements |
| `tsq`                    | test sequencer client: submit datapacks, follow runs, manage the lab (`pip install "testsuit[sequencer]"`) |
| `tsqd`                   | test sequencer daemon: queue, lab locks, runs, REST API, web UI at `/ui` ([sequencer README](src/testsuit/sequencer/README.md)) |
| `evalfile`               | render a Jinja2 template file with keys from YAML dictionaries    |
| `testsuit-notebooks`     | copy the Jupyter GUI notebooks into a folder, optionally open Jupyter |

**HDF5 files written** (`data2h5`, `apply_clock_correction`, `extract_data_any`, `extract_data_flags`, H5-Conv, the
Extract of the Exploit pages): each parameter is a dataset of its group, whose `TimestampRef` attribute names its
timestamp (index) dataset. The parameters of a group whose timestamps are exactly the same (same values, same type)
share one timestamp dataset; a timestamp that differs gets its own (`<name>`, else `<name>_2`, `_3`…). The comparison
hashes and reads the data by chunks, so it holds for files of several GB. Appending rows to a parameter keeps it on a
shared timestamp only while its other parameters get the same rows. The group keeps the list of its timestamp
datasets (`IndexDatasets`) and each timestamp dataset its user count and hash (`IndexUsers`, `IndexDigest`).

**Secrets.** Wherever a password or a token is given (the `token` of an InfluxDB config file, the `password` of a ClickHouse one, `data2db --token`, the credentials of a requirements source), it can be a reference rather than the secret itself: `env:<VARIABLE>` reads an environment variable, and `file:<path>` the content of a file, such as a Docker or Kubernetes secret (`token: file:/run/secrets/influx_token`; trailing white space dropped; a relative path is relative to the config file, or to the current folder on the command line). The sources file of the requirements accepts references only.

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
