# testsuit

Toolbox for preparing and analysing test data: load measurement files (CSV, TDMS, MDF, Dewesoft DXD, UDBF, HDF5, InfluxDB) into pandas, convert them to HDF5 or push them to a database, check results against YAML scenarios, and package test inputs into datapacks.

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
| `plotly`   | interactive plots in mexploit reports        |
| `word`     | Word report generation (`iced.shadox_word`)  |
| `jupyter`  | notebook GUIs (`jupytertools`)               |
| `influxdb` | InfluxDB readers and `data2db` targets       |
| `test`     | what the test suite needs                    |
| `dev`      | `test` plus build and lint tools             |

For example `pip install -e ".[plotly,jupyter]"`.

## Repository layout

```
src/                  importable packages (added to the path by an install)
  datatools/          load data files into pandas, convert to HDF5, push to a DB
    DataFileMgrs/     one reader per file format (Csv, Tdms, Mdf, Dxd, Udbf, H5, InfluxDb)
    datapack/         datapack generation: dictionaries, includes, key replacement
  exploit/
    mexploit/         YAML scenario checks (corridors, sequences, computed params, plots) run through pytest
    runner/           runs mexploit over test sessions and builds HTML reports
  iced/               Shadox API client (v1, v2) and Word report generation
  jupytertools/       ipywidgets GUIs for notebooks
  misc/               logging, progress reporting, file helpers
scripts/              command-line entry points (see below)
tests/                pytest suite (*_t.py)
  etc/                input data, scenarios and configs used by the tests
  ref/                reference outputs the tests compare against
```

## Command-line scripts

Each script prints its full usage with `--help`. Run them from a clone, for example `python scripts/data2h5.py --help`.

| Script                      | Purpose                                                           |
|-----------------------------|-------------------------------------------------------------------|
| `data2h5.py`                | extract parameters from data files or folders into an HDF5 file   |
| `data2db.py`                | push parameters from data files into a database (InfluxDB, ClickHouse) |
| `extract_data_any.py`       | decode raw bit fields into parameters from a JSON/YAML config (example: `config_extract_data_any.json`) |
| `extract_data_flags.py`     | extract flag values described in a YAML file                      |
| `apply_clock_correction.py` | shift timestamps of a folder of data files                        |
| `mxp.py`                    | run a mexploit scenario folder against data folders               |
| `exploit_runner.py`         | run mexploit over whole test sessions from a YAML config          |
| `datapack.py`               | generate a datapack from a test definition file                   |
| `evalfile.py`               | resolve includes and keys in a file from dictionaries             |

## Using the library

```python
from exploit.mexploit.mexploit import mexploit

# run the checks of a scenario folder on a data folder, results written to out/
rc = mexploit("tests/etc/mexploit/scenarii/mxp_OK_main_functions", "tests/etc/data", "out/", force=True)
```

The scenarios under `tests/etc/mexploit/scenarii/` are working examples of every check type.

## Running the tests

```bash
pip install -e ".[dev]"
pytest          # runs tests/*_t.py
ruff check .    # catches runtime errors such as undefined names
```

CI (`.github/workflows/tests.yml`) runs the same lint, the tests on Python 3.10 and 3.12, and a package build, on every pull request.

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add a file format, a mexploit check or a script.

## License

Apache License 2.0, see [LICENSE](LICENSE).

THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE AUTHOR OR ITS COMPANY BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
