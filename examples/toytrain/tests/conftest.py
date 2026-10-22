import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = PROJECT_ROOT / "sample"


@pytest.fixture(autouse=True)
def _in_project_root(monkeypatch):
    # testsuit resolves some relative paths from the current folder
    monkeypatch.chdir(PROJECT_ROOT)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
