# acme_testbench: extending testsuit from your own library

A small, complete Python project that plugs into testsuit without changing it. "ACME" is a made-up test bench; everything here is example code and sample data you can copy as a starting point.

It adds:

| What | Where | Used as |
|------|-------|---------|
| a new data file format, the bench controller's JSON dump | `filemgrs.AcmeJsonFileMgr` | `*.acmej` files |
| a fake format whose signals are generated, not measured | `filemgrs.SimulatedFileMgr` | `*.sim.yml` files |
| a CSV syntax (a `#ACME-LOGGER` line on top of a CSV file) | `filemgrs.AcmeLoggerCsvFileMgr` | `*.csv` files starting with `#ACME-LOGGER` |
| mexploit criteria registered as a module | `criteria.py` | `type: within_range`, `max_slope`, `settles` |
| mexploit criteria registered one by one (`@register_criterion`) | `checks.py` | `type: no_dropout`, `mean_close_to` |
| a datapack importer | `importer.AcmeArchiveImporter` | `importer: acme-archive` in datapack setups |
| its own logo on the exploit_runner HTML report | `assets/acme_logo.svg` | every report |

All of it is registered in one function, `plugin.register()`, which `pyproject.toml` declares as a `testsuit.plugins` entry point. Once the package is installed, testsuit calls it by itself: in your scripts, in the command-line tools (`data2h5`, `mxp`, `exploit_runner`, `datapack`...), and in the `mxp` processes exploit_runner starts for each test run.

```
acme_testbench/
  pyproject.toml              entry point: [project.entry-points."testsuit.plugins"]
  src/acme_testbench/
    plugin.py                 register(): every registration, in one place
    filemgrs.py               file managers (readers)
    criteria.py, checks.py    mexploit criteria
    importer.py               datapack importer
    assets/acme_logo.svg      report logo
  sample/
    data/                     one file of each format, same 60 s run
    exploit.yml, scenario/    an exploit_runner session using the new formats and criteria
    datapack/                 a datapack test definition using the new importer
  tests/                      pytest suite, runs everything below end to end
```

## Install

From the root of the testsuit repository:

```bash
pip install -e .                                   # testsuit
pip install -e "examples/acme_testbench[test]"     # this example
```

Outside this repository, install testsuit from Git instead: `pip install "testsuit @ git+https://github.com/laurentmldev/testsuit"`.

Check that testsuit sees the plugin:

```bash
python -c "from testsuit.plugins import load_plugins; print(load_plugins())"    # ['acme_testbench']
```

## Run it

Commands below run from `examples/acme_testbench`. Outputs go to `.work/`, which Git ignores.

**Load the data files** like any other testsuit format:

```bash
data2h5 sample/data -l                                  # the parameters of every file
data2h5 sample/data .work/bench.h5 -p ".*"              # all of them into an HDF5 file
```

```python
from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr
from testsuit.misc.MonitorProgress import MonitorProgress

scanner = FolderParamMgr("sample/data")
[speed] = scanner.findParams("ShaftSpeed_rpm", monitorProgress=MonitorProgress(name="demo"))
```

**Run the checks of a test session** with exploit_runner. The report, `.work/exploit_results/exploit_synthesis.html`, starts with the ACME logo:

```bash
exploit_runner sample/exploit.yml --results_root_folder "$PWD/.work"
```

A single test run works with mxp too: `mxp sample/scenario/bench_session/testrun_01 sample/data -o .work/mxp -f`.

**Build a datapack** whose calibration dataset comes from the ACME archive store (`sample/datapack/archive_store`). `--nocheck` is needed because the example folders are not a tagged Git checkout:

```bash
datapack sample/datapack/testdef/acme_testdef.yml --nocheck
```

The dataset is imported into `.work/datarepo/bench_calib/v1.2`, its keys fill `setup/templates/bench_config.yml`, and the datapack zip lands in `.work/datapacks`. Run it again and the dataset is checked (version and checksums) instead of being imported again.

**Run the tests**:

```bash
pytest
```

## Writing your own

**A file format.** Subclass `AFileMgr`: `getFieldNames()` lists the parameters of a file, `loadParams()` returns the requested ones as pandas objects indexed by seconds since epoch, each passed through `self.finalizeParam(...)`. When your format can be read whole, `filemgrs.ChannelsFileMgr` shows the shortest path: implement `readChannels()` only. Then register it:

```python
register_file_format("acme-json", ["acmej"], AcmeJsonFileMgr)
```

Extensions can contain dots (`"sim.yml"`), and `accepts=lambda filename: ...` narrows a format to some of the files with its extensions. For a CSV dialect, subclass `CsvFileMgr` and register a detector instead: `register_csv_variant("acme-logger", isAcmeLoggerCsv, AcmeLoggerCsvFileMgr)`.

**A criterion.** A function `crit_<type>(critConf)` that raises (usually with `assert`) when the check fails. `critConf` is the scenario entry, with its `name` and `type`. Load data with testsuit's `param()` helper, or read an earlier `computed_param` entry, as `criteria.signal()` does. Register a whole module with `register_criteria_module("mylib.criteria")`, or single functions with `@register_criterion`. A docstring with a `<TEMPLATE>` block, as in testsuit's own criteria, documents the scenario syntax.

**A datapack importer.** Subclass `ADataImporter` (`retrieve()`, `checkVersion()`, `getChanges()`, `getTag()`) and register the class, or any factory `(targetDir, remotePath, versionId)`: `register_data_importer("acme-archive", AcmeArchiveImporter)`. After `retrieve()`, the dataset folder must hold a `dataset.dico` file with the keys the datapack files can use.

**The report logo.** `set_default_report_logo(svg=...)` takes SVG markup or a file path; `title="..."` keeps testsuit's animated logo with your name in it. For one run only, use `exploit_runner --report-logo logo.svg` or `--report-logo-title "ACME Bench"`.

Without installing your package, `TESTSUIT_PLUGINS=acme_testbench.plugin` in the environment does the same as the entry point. See the "Extending testsuit from another library" section of testsuit's CONTRIBUTING.md for the reference.
