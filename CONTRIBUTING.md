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

- Library code lives in `src/<package>/` and is imported as `datatools...`, `exploit...`, `misc...`, never through `sys.path` edits.
- Command-line tools live in `scripts/`. Keep the logic in an importable function (for example `data2h5()` in `scripts/data2h5.py`) and keep the `if __name__ == "__main__":` block to argument parsing, so tests can call the function directly.
- Tests are `tests/<topic>_t.py`. Their inputs go in `tests/etc/`, the expected outputs they compare against go in `tests/ref/`, and anything they write goes under `tests/tmp/` (ignored by git).
- New runtime dependencies go in `[project].dependencies` of `pyproject.toml`; dependencies only needed by one feature go in an extra under `[project.optional-dependencies]`.
- Versions come from git tags (setuptools-scm): tag a release as `vX.Y.Z` on `main`.

## Adding a data file format

1. Create `src/datatools/DataFileMgrs/<Format>FileMgr.py` with a class deriving from `AFileMgr` (see `CsvFileMgr.py` or `MdfFileMgr.py` for complete examples).
2. Register its extension in the `elif filename.lower().endswith(...)` chain of `FolderParamMgr._loadFileMgrs()` and in `SUPPORTED_DATAFILE_EXTENSIONS` in `datatools/datatoolbox.py`.
3. Add a sample file under `tests/etc/data/` and a case in `tests/loaddata_t.py`.

## Adding a mexploit check

A scenario entry `type: foo` runs the function `crit_foo(critConf, ...)`.

- For a check everyone can use, add `crit_foo` to `src/exploit/mexploit/criteria.py` and cover it with a scenario under `tests/etc/mexploit/scenarii/mxp_OK_main_functions/`.
- For a project-specific check, keep it outside this repo: write `crit_foo` in your own module and load it from the scenario with a `# MXP_REGISTER_CRITLIB(path/to/your_criteria.py)` line, as in `tests/etc/mexploit/scenarii/mxp_OK_custom_crit/`.
