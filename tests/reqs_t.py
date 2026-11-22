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

"""Requirements coverage (testsuit.reqs, sequencer SPEC section 5.4): sources, YAML backend, links, snapshot at
generation, coverage of runs and campaigns, matrices."""
import copy
import zipfile
from pathlib import Path

import pytest
import yaml

from sequencer_t import COUNT_CRITERIA, PIPELINE, TO_CSV, Bench, run_one, write  # noqa: F401  (fixtures below)
from testsuit.datatools.datapack import datapack_tools
from testsuit.datatools.datapack.baseline import nonconform_reasons
from testsuit.datatools.datapack.datapack_tools import DatapackError, datapack, read_baseline
from testsuit.reqs import ReqBackend, Requirement, register_req_backend, unregister_req_backend
from testsuit.reqs.backend import ReqError, list_requirements
from testsuit.reqs.coverage import coverage, rollup
from testsuit.reqs.model import Ref, split_ref
from testsuit.reqs.render import as_csv, as_html, text
from testsuit.reqs.sources import Sources

REQS = {
    "nodes": {"SYS": "System", "SYS/COUNT": "Counting"},
    "requirements": {
        "REQ-1": {"path": "SYS/COUNT", "title": "Counts", "description": "The device counts."},
        "REQ-2": {"path": "SYS/COUNT", "title": "Counts enough", "description": "It reaches min_count.", "version": 2},
        "REQ-3": {"path": "SYS/COUNT/FAST", "title": "Counts fast", "description": "Not tested yet."},
        "REQ-4": {"path": "SYS", "title": "Old one", "status": "rejected"},
        "REQ-5": {"path": "OTHER", "title": "Elsewhere"},
    },
}


@pytest.fixture
def no_git(monkeypatch):
    monkeypatch.setattr(datapack_tools, "git_state", lambda folder: {"commit": "0" * 40, "tag": None, "clean": False})


def test_refs():
    assert split_ref("REQ-1", ["reqs", "jama"], "reqs") == Ref("reqs", "REQ-1")
    assert split_ref("jama:SYS-4@3", ["reqs", "jama"], "reqs") == Ref("jama", "SYS-4", "3")
    assert split_ref("urn:x:1", ["reqs"], "reqs") == Ref("reqs", "urn:x:1")      # not a source: part of the id
    with pytest.raises(ValueError):
        split_ref("a b", ["reqs"], "reqs")


def test_yaml_backend_and_sources(tmp_path, monkeypatch):
    write(tmp_path / "requirements.yml", yaml.safe_dump(REQS))
    sources = Sources(tmp_path / "requirements.yml")             # the sources file is itself the 'reqs' source
    assert sources.names == ["reqs"] and sources.default == "reqs"
    [other, sys_node] = sources.tree()
    assert (sys_node.id, sys_node.label, [c.id for c in sys_node.children]) == ("SYS", "System", ["COUNT"])
    assert [c.id for c in sys_node.children[0].children] == ["FAST"]
    assert sorted(sources.list("SYS/COUNT")) == ["REQ-1", "REQ-2"]
    assert sorted(sources.list("SYS/COUNT", children=True)) == ["REQ-1", "REQ-2", "REQ-3"]
    assert sorted(sources.list("", children=True)) == ["REQ-1", "REQ-2", "REQ-3", "REQ-4", "REQ-5"]
    found = sources.get([Ref("reqs", "REQ-2"), Ref("reqs", "NOPE")])
    assert found[Ref("reqs", "REQ-2")].version == "2" and found[Ref("reqs", "NOPE")] is None
    assert len(found[Ref("reqs", "REQ-2")].version) and sources.get([Ref("reqs", "REQ-1")])[Ref("reqs", "REQ-1")].version

    # several sources: credentials from the environment only
    write(tmp_path / "multi.yml", yaml.safe_dump({"sources": {
        "local": {"backend": "yaml", "options": {"file": "requirements.yml", "url": "https://wiki/{id}"}},
        "remote": {"backend": "fake", "credentials": {"token": "env:FAKE_TOKEN"}}}}))
    seen = {}

    class Fake(ReqBackend):
        def __init__(self, options, credentials, base_dir):
            super().__init__(options, credentials, base_dir)
            seen["credentials"] = credentials

        def tree(self):
            return []

        def list(self, path="", children=False):
            return [Requirement("R1", path="A"), Requirement("R2", path="A/B")]

        def get(self, ids):
            return {i: Requirement(i, version="7") if i.startswith("R") else None for i in ids}

    register_req_backend("fake", Fake)
    try:
        multi = Sources(tmp_path / "multi.yml")
        assert multi.get([Ref("local", "REQ-1")])[Ref("local", "REQ-1")].url == "https://wiki/REQ-1"
        with pytest.raises(ReqError, match="FAKE_TOKEN"):
            multi.backend("remote")
        monkeypatch.setenv("FAKE_TOKEN", "s3cret")
        assert multi.get([Ref("remote", "R9")])[Ref("remote", "R9")].version == "7"
        assert seen["credentials"] == {"token": "s3cret"}
        # no children query: emulated through the tree
        assert [r.id for r in list_requirements(multi.backend("remote"), "A", True)] == ["R1", "R2"]
        write(tmp_path / "bad.yml", yaml.safe_dump({"sources": {"r": {"backend": "fake", "credentials": {"token": "abc"}}}}))
        with pytest.raises(ReqError, match="environment"):
            Sources(tmp_path / "bad.yml").backend("r")
    finally:
        unregister_req_backend("fake")


def test_rollup():
    assert rollup(["passed", "passed"]) == "passed"
    assert rollup(["passed", "failed", "error"]) == "failed"
    assert rollup(["passed", "error"]) == "error"
    assert rollup(["passed", "skipped"]) == "partial"
    assert rollup(["passed", "not run"]) == "partial"
    assert rollup(["skipped", "not run"]) == "skipped"
    assert rollup(["not run"]) == "not run"


CRITERIA = COUNT_CRITERIA.replace("  type: assert_compare\n", "  type: assert_compare\n  reqs: [REQ-2]\n") + \
    "plotted:\n  type: assert\n  reqs: REQ-1\n  expression: 'True'\n"


def _bench(bench: Bench, name: str, testdef_reqs, skip=False, **values) -> Path:
    """The exploit bench, its testdef and checks linked to requirements; skip: the exploit step does not run."""
    write(bench.dp / "requirements.yml", yaml.safe_dump(REQS))
    write(bench.dp / "testdef" / name / "criteria" / "count" / "count.mexploit.yml", CRITERIA)
    pipeline = copy.deepcopy(PIPELINE)
    pipeline["phases"][1] = {"id": "exploit", "needs": ["test_run"], "steps": [
        {"id": "to_csv", "python": {"code": TO_CSV}},
        {"id": "criteria", "needs": ["to_csv"], **({"if": "false"} if skip else {}),
         "exploit": {"criteria": ["criteria/count"], "data": ["test_run:dut/output/*.csv"]}}]}
    testdef = {"id": name, "name": name, "setup_file": "../../setup/bench_setup.yml", "setup_config": "bench",
               "dictionaries": ["values.yml"], "reqs": testdef_reqs, "pipeline": pipeline}
    folder = bench.dp / "testdef" / name
    write(folder / "values.yml", yaml.safe_dump(values))
    write(folder / "testdef.yml", yaml.safe_dump(testdef))
    return folder / "testdef.yml"


@pytest.fixture
def bench(tmp_path, no_git):
    return Bench(tmp_path)


def _member(zip_path, rel):
    with zipfile.ZipFile(zip_path) as z:
        root = z.namelist()[0].split("/")[0]
        return yaml.safe_load(z.read(f"{root}/{rel}"))


def test_snapshot_and_coverage_of_runs(bench, tmp_path):
    ok = datapack(_bench(bench, "ok", ["REQ-1@1"], min_count=3), nocheck=True, output_dir=tmp_path / "zips")
    snap = _member(ok, "definition/requirements.yml")
    assert snap["testdef"] == ["REQ-1"] and sorted(snap["requirements"]) == ["REQ-1", "REQ-2"]
    assert snap["checks"] == [
        {"step": "criteria", "criteria": "criteria/count", "scenario": "count", "file": "count.mexploit.yml",
         "check": "counted_enough", "reqs": ["REQ-2"]},
        {"step": "criteria", "criteria": "criteria/count", "scenario": "count", "file": "count.mexploit.yml",
         "check": "plotted", "reqs": ["REQ-1"]}]
    # pinned to a version that is not the current one: a warning, recorded
    assert snap["requirements"]["REQ-1"]["pinned"] == "1" and snap["requirements"]["REQ-1"]["changed"]
    assert any("changed since version 1" in w for w in snap["warnings"])
    assert read_baseline(ok)["requirements"]["linked"] == 2
    assert any(s["role"] == "requirements" for s in read_baseline(ok)["sources"])

    # linked, not run
    cov = coverage([ok])
    assert cov.status("REQ-1") == "not run"

    run_ok = run_one(bench, ok)
    bad = datapack(_bench(bench, "bad", [], min_count=100000), nocheck=True, output_dir=tmp_path / "zips")
    run_bad = run_one(bench, bad)
    skipped = datapack(_bench(bench, "skip", [], skip=True, min_count=3), nocheck=True, output_dir=tmp_path / "zips")
    run_skip = run_one(bench, skipped)

    cov = coverage([Path(run_ok["results"])])
    assert [t.verdict for t in cov.tests] == ["succeeded"]
    assert (cov.status("REQ-1"), cov.status("REQ-2")) == ("passed", "passed")

    # a campaign: the requirements rolled up over its runs; the uncovered ones under a path
    cov = coverage([Path(run_ok["results"]), Path(run_bad["results"]), Path(run_skip["results"])],
                   sources_file=bench.dp / "requirements.yml", paths=["SYS/COUNT"], children=True)
    assert cov.status("REQ-2") == "failed"                         # bad's check not met
    assert cov.status("REQ-1") == "partial"                        # passed in ok and bad, skipped in skip
    [_, t_bad, t_skip] = cov.tests
    assert (t_bad.status("REQ-2"), t_bad.status("REQ-1"), t_skip.status("REQ-2")) == ("failed", "passed", "skipped")
    assert cov.uncovered() == ["REQ-3"]
    assert cov.by_path()["SYS/COUNT"] == {"failed": 1, "partial": 1, "uncovered": 1}
    cov2 = coverage([Path(run_ok["results"]), Path(run_skip["results"])])
    assert cov2.status("REQ-2") == "partial"
    assert cov.stale("REQ-1", cov.tests[0]) is False                # the snapshot holds the current version

    out = text(cov)
    assert "Requirements × tests" in out and "Tests × requirements" in out and "Uncovered requirements (1 of 3)" in out
    page = as_html(cov, sequencer="http://tsq:8640/")
    assert "Requirements × tests" in page and "Tests × requirements" in page
    assert f'href="http://tsq:8640/#/runs/{run_ok["id"]}"' in page and "exploit report" in page
    assert as_csv(cov).splitlines()[0].startswith("requirement,path,title,coverage,")
    assert "criteria: criteria/count/count.mexploit.yml::counted_enough,REQ-2,failed" in as_csv(cov, "tests")

    # the requirement changed since the run: flagged
    reqs = copy.deepcopy(REQS)
    reqs["requirements"]["REQ-2"]["version"] = 3
    write(bench.dp / "requirements.yml", yaml.safe_dump(reqs))
    cov = coverage([Path(run_ok["results"])], sources_file=bench.dp / "requirements.yml")
    assert cov.stale("REQ-2", cov.tests[0]) and "changed since" in as_html(cov)


def test_testdef_coverage_and_cli(bench, tmp_path, capsys):
    from testsuit.cli import reqs as cli
    testdef = _bench(bench, "td", ["REQ-5"], min_count=3)
    cov = coverage([testdef])
    assert sorted(cov.linked()) == ["REQ-1", "REQ-2", "REQ-5"] and cov.status("REQ-5") == "not run"
    assert cli.main(["--sources", str(bench.dp / "requirements.yml"), "uncovered", str(testdef), "--paths", "SYS",
                     "-r"]) == 1
    assert capsys.readouterr().out.split()[0] == "REQ-3"            # not REQ-4: rejected
    out = tmp_path / "m.html"
    assert cli.main(["matrix", str(testdef), "--format", "html", "-o", str(out)]) == 0
    assert "REQ-5" in out.read_text()
    assert cli.main(["--sources", str(bench.dp / "requirements.yml"), "show", "REQ-1", "NOPE"]) == 1
    assert "REQ-1:" in capsys.readouterr().out


def test_dangling_links(bench, tmp_path, monkeypatch):
    testdef = _bench(bench, "dangling", ["REQ-4", "REQ-404"], min_count=3)
    monkeypatch.setattr(datapack_tools, "git_state", lambda folder: {"commit": "0" * 40, "tag": "v1", "clean": True})
    with pytest.raises(DatapackError, match="REQ-4' is rejected.*REQ-404' is unknown.*--nocheck"):
        datapack(testdef, output_dir=tmp_path / "zips")
    zip_path = datapack(testdef, nocheck=True, output_dir=tmp_path / "zips")
    baseline = read_baseline(zip_path)
    assert baseline["datapack"]["conform"] is False
    assert any("REQ-404" in r for r in nonconform_reasons(baseline))
    # no sources file
    (bench.dp / "requirements.yml").unlink()
    with pytest.raises(DatapackError, match="no requirements.yml"):
        datapack(testdef, output_dir=tmp_path / "zips")


def test_check_reqs_field_is_known():
    from testsuit.exploit.mexploit import criteria
    from testsuit.exploit.mexploit.spec import check_entry
    entry = {"type": "assert", "expression": "True", "reqs": ["REQ-1"]}
    assert check_entry(criteria.crit_assert, entry) == []


def test_editor_problems(tmp_path):
    """Unknown, deleted or rejected requirements: errors of the test definition (the generation would fail), with
    the check that lists them; a pinned version that changed: a warning."""
    from editor_t import Repos
    repos = Repos(tmp_path)
    editor = repos.editor
    session = editor.open("workspace0")
    editor.write(session, "requirements.yml", content=yaml.safe_dump(REQS))
    editor.write(session, "testdef/count/criteria/count/count.mexploit.yml",
                 content="ok:\n  type: assert\n  reqs: [REQ-404]\n  expression: 1 == 1\n")
    testdef = {"id": "count", "name": "Count", "setup_file": "../../setup/bench_setup.yml", "setup_config": "bench",
               "reqs": ["REQ-4", "REQ-2@1"],
               "pipeline": {"phases": [{"id": "run", "stage_slots": ["bench"], "steps": [
                   {"id": "start", "slot": "dut", "do": "start"},
                   {"id": "check", "needs": ["start"], "exploit": {"criteria": ["criteria/count"],
                                                                   "data": ["run:dut/output/*.csv"]}}]}]}}
    problems = editor.write(session, "testdef/count/testdef.yml", data=testdef)["problems"]
    found = {(p["severity"], p["message"]) for p in problems if p["message"].startswith("requirements:")}
    assert found == {
        ("error", "requirements: requirement 'REQ-4' is rejected"),
        ("error", "requirements: requirement 'REQ-404' is unknown to source 'reqs' "
                  "(listed by check 'ok' of criteria/count/count/count.mexploit.yml)"),
        ("warning", "requirements: requirement 'REQ-2' changed since version 1 (now 2)")}
    [line] = [p["line"] for p in problems if "REQ-4' is rejected" in p["message"]]
    assert "REQ-4" in editor.path(session, "testdef/count/testdef.yml").read_text().splitlines()[line - 1]


def test_sequencer_and_campaign_report(bench, tmp_path):
    """tsqd: the coverage of a run (its steps as they are), and the requirements section of the campaign report."""
    import asyncio
    from testsuit.sequencer.api import create_app
    from testsuit.sequencer.campaigns import report_html
    from testsuit.reqs.coverage import coverage_of_runs
    zip_path = datapack(_bench(bench, "api", ["REQ-1"], min_count=3), nocheck=True, output_dir=tmp_path / "zips")
    run = run_one(bench, zip_path)

    async def main():
        seq = bench.sequencer()
        routes = {r.path: r.endpoint for r in create_app(seq).routes if hasattr(r, "endpoint")}
        try:
            data = await routes["/runs/{run_id}/requirements"](run["id"])
            page = (await routes["/runs/{run_id}/requirements/report"](run["id"])).body.decode()
        finally:
            await seq.close()
        return data, page
    data, page = asyncio.run(main())
    assert data["linked"] and {r["key"]: r["coverage"] for r in data["requirements"]} == {"REQ-1": "passed", "REQ-2": "passed"}
    [test] = data["tests"]
    assert test["run"] == run["id"] and test["reports"]["criteria"].endswith("exploit_synthesis.html")
    assert "Tests × requirements" in page and f'href="../../../#/runs/{run["id"]}"' in page
    view = {"id": "c1", "campaign": "camp", "runs": [], "counts": {}}
    html_page = report_html(view, base="../", requirements=coverage_of_runs([{**run, "dir": None}]))
    assert "<h2>Requirements</h2>" in html_page and "../campaigns/c1/requirements/report" in html_page
    assert "<h2>Requirements</h2>" not in report_html(view, base="../")


def test_editor_requirements_page(tmp_path):
    from editor_t import Repos
    repos = Repos(tmp_path)
    editor = repos.editor
    session = editor.open("workspace0")
    assert editor.requirements(session)["sources_file"] is None
    editor.write(session, "requirements.yml", content=yaml.safe_dump(REQS))
    testdef = {"id": "count", "name": "Count", "setup_file": "../../setup/bench_setup.yml", "setup_config": "bench",
               "reqs": ["REQ-1"], "pipeline": {"phases": [{"id": "run", "stage_slots": ["bench"], "steps": [
                   {"id": "start", "slot": "dut", "do": "start"}]}]}}
    editor.write(session, "testdef/count/testdef.yml", data=testdef)
    assert editor.kind(session, "requirements.yml") == "requirements"
    data = editor.requirements(session)
    assert data["sources_file"] == "requirements.yml" and [n["id"] for n in data["tree"]] == ["OTHER", "SYS"]
    rows = {r["key"]: r["coverage"] for r in data["coverage"]["requirements"]}
    assert rows == {"REQ-1": "not run", "REQ-2": "uncovered", "REQ-3": "uncovered", "REQ-5": "uncovered"}
    assert data["coverage"]["tests"][0]["origin"] == "testdef/count/testdef.yml"
