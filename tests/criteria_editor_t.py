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


"""The criteria editor's services (testsuit.exploit.editor): on a folder, through Python and through the
REST routes of ``mxp edit``."""
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest
import requests

from testsuit import plugins
from testsuit.exploit.editor.service import CriteriaConflict, CriteriaError, CriteriaFolder, NotFound

LIMITS = Path("tests/etc/mexploit/utests/limits")
TMP_ROOT = Path("tests/tmp/criteria_editor")
SCN = "limits/scenario/1_limits.mexploit.yml"


@pytest.fixture(autouse=True)
def no_installed_plugins(monkeypatch):
    monkeypatch.setattr(plugins, "entry_points", lambda group: [])


@pytest.fixture
def folder(request):
    root = TMP_ROOT / request.node.name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    shutil.copytree(LIMITS, root / "limits")
    service = CriteriaFolder(root)
    yield service
    service.close()


def wait_job(service: CriteriaFolder, job: str, timeout: float = 120) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        state = service.utest_job(job)
        if state["done"]:
            return state
        time.sleep(0.2)
    raise AssertionError("the unit tests did not end")


def test_overview_links_scenarios_and_unit_tests(folder):
    ov = folder.overview()
    assert [s["path"] for s in ov["scenarios"]] == ["limits/scenario"]
    scn = ov["scenarios"][0]
    assert scn["files"] == [SCN] and scn["utests"] == ["limits/utests.yml"] and scn["testdef"] is None
    utests = ov["utests"][0]
    assert utests["scenario"] == "limits/scenario"
    assert [c["name"] for c in utests["cases"]] == ["low", "high", "lowered_limit"]
    assert utests["cases"][1]["data_files"] == ["limits/high/values.csv"]
    assert utests["cases"][1]["expect"]["value_max"] == {"status": "FAILED", "message": "not <="}
    assert folder.scenario_folder("limits/utests.yml") == folder.root / "limits" / "scenario"


def test_read_write_and_conflicts(folder):
    doc = folder.read(SCN)
    written = folder.write(SCN, doc["content"] + "\n# more\n", doc["version"])
    assert written["version"] != doc["version"]
    with pytest.raises(CriteriaConflict) as e:           # saved from the version read before
        folder.write(SCN, doc["content"], doc["version"])
    assert e.value.current["content"].endswith("# more\n")
    with pytest.raises(CriteriaError, match="already exists"):
        folder.write(SCN, "", create=True)
    with pytest.raises(CriteriaError, match="outside the folder"):
        folder.read("../../etc/passwd")
    with pytest.raises(NotFound):
        folder.read("limits/none.yml")


def test_keys_lint_and_params(folder):
    keys = folder.keys("limits/scenario")
    assert {"key": "limits.max", "value": "5", "origin": "limits/limits.yml"} in keys["keys"]
    assert keys["origin"] == "dictionaries of limits/utests.yml"
    assert folder.lint(SCN)["problems"] == []
    # the text being typed, not saved: an unknown helper, a field misspelled
    text = folder.read(SCN)["content"].replace('param("value").max()', 'nosuch(param("value"))') \
        .replace("compOperator", "compOperatr")
    result = folder.lint(SCN, text)
    messages = {(p["entry"], p["field"]): p["message"] for p in result["problems"]}
    assert "unknown name 'nosuch'" in messages[("value_max", "expression_left")]
    assert ("value_max", "compOperatr") in messages
    assert all(p["file"] == SCN for p in result["problems"])
    assert result["data"] == ["limits/low"]           # the parameter names checked against the first case
    assert folder.read(SCN)["content"].count("compOperator") == 1     # nothing written
    assert "values.csv::value" in folder.params("limits/scenario")["params"]


def test_lint_without_the_keys(folder):
    """A key the dictionaries do not give: the values made of keys are not checked, and it is said."""
    (folder.root / "limits" / "limits.yml").write_text("other: 1\n")
    result = folder.lint(SCN)
    assert result["problems"] == [] and "not known" in result["notes"][0]


def test_describe_is_cached(folder):
    first = folder.describe("limits/scenario")
    assert "assert_compare" in first["criteria"] and first["scenario_schema"]["$defs"]
    assert folder.describe("limits/scenario") is first


def test_unit_tests_in_the_background(folder):
    started = folder.start_utests("limits/utests.yml")
    assert started["cases"] == ["low", "high", "lowered_limit"]
    state = wait_job(folder, started["job"])
    assert state["error"] is None and set(state["results"]) == {"low", "high", "lowered_limit"}
    report = state["report"]
    assert report["ok"] and report["coverage"] == {"value_max": "covered"}
    assert report["utests"] == "limits/utests.yml" and report["scenario"] == "limits/scenario"
    high = report["cases"]["high"]
    assert high["checks"]["value_max"]["got"] == "FAILED" and high["report"] == "high/results/pytest_report.html"
    assert folder.job_file(started["job"], high["report"]).read_text().startswith("<")
    with pytest.raises(NotFound):
        folder.job_file(started["job"], "../../x")
    # the last complete run, for the overview
    assert folder.overview()["utests"][0]["last"]["fully_covered"] is True
    assert folder.last_report("limits/utests.yml")["job"] == started["job"]
    with pytest.raises(CriteriaError, match="no case nope"):
        folder.start_utests("limits/utests.yml", ["nope"])


def test_unit_tests_stopped(folder):
    started = folder.start_utests("limits/utests.yml")
    assert folder.stop_utests(started["job"])["stopping"] is True
    state = wait_job(folder, started["job"])
    stopped = [c for c in state["report"]["cases"].values() if c["error"] == "stopped"]
    assert stopped, state["report"]["cases"]
    assert folder.last_report("limits/utests.yml") is None     # a stopped run is not the last report


def test_mxp_edit_routes(folder):
    """The routes and the UI of `mxp edit`, through HTTP."""
    import uvicorn

    from testsuit.exploit.editor.standalone import create_app

    app = create_app(folder.root, plugins=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            requests.get(f"{url}/mode", timeout=1)
            break
        except requests.ConnectionError:
            time.sleep(0.1)
    try:
        assert requests.get(f"{url}/mode").json()["mode"] == "criteria"
        assert requests.get(f"{url}/", allow_redirects=False).headers["location"] == "ui/#/criteria"
        ov = requests.get(f"{url}/criteria").json()
        assert ov["scenarios"][0]["utests"] == ["limits/utests.yml"]
        doc = requests.get(f"{url}/criteria/files/{SCN}").json()
        r = requests.put(f"{url}/criteria/files/{SCN}", json={"content": doc["content"] + "\n", "base": doc["version"]})
        assert r.status_code == 200
        r = requests.put(f"{url}/criteria/files/{SCN}", json={"content": "x: 1\n", "base": doc["version"]})
        assert r.status_code == 409 and r.json()["current"]["content"] == doc["content"] + "\n"
        assert requests.get(f"{url}/criteria/files/../x").status_code in (400, 404)
        assert requests.get(f"{url}/criteria/files/limits/none.yml").status_code == 404
        lint = requests.post(f"{url}/criteria/lint", json={"path": SCN, "text": "a: {type: nosuch}\n"}).json()
        assert lint["problems"][0]["message"] == "unknown criterion 'nosuch'"
        assert "assert_compare" in requests.get(f"{url}/criteria/describe", params={"scenario": "limits/scenario"}).json()["criteria"]
        job = requests.post(f"{url}/criteria/utests/jobs", json={"utests": "limits/utests.yml", "cases": ["high"]})
        assert job.status_code == 201
        jobId = job.json()["job"]
        for _ in range(600):
            state = requests.get(f"{url}/criteria/utests/jobs/{jobId}").json()
            if state["done"]:
                break
            time.sleep(0.2)
        assert state["report"]["ok"], state
        page = requests.get(f"{url}/criteria/utests/jobs/{jobId}/files/{state['report']['cases']['high']['report']}")
        assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
        assert requests.post(f"{url}/criteria/utests/last", json={"utests": "limits/utests.yml"}).json()["report"]["partial"] is True
    finally:
        server.should_exit = True
        thread.join(10)
