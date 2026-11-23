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
    # the quick search of the test definition form: active requirements of every source, by reference
    found = editor.requirements_search(session, "testdef/count/testdef.yml")
    assert found["sources_file"] == "requirements.yml" and found["default"] == "reqs" and not found["errors"]
    assert {i["key"] for i in found["items"]} == {"REQ-1", "REQ-2", "REQ-3", "REQ-5"}         # REQ-4: rejected
    assert next(i for i in found["items"] if i["key"] == "REQ-1")["path"] == "SYS/COUNT"


def test_editor_requirements_form(tmp_path, monkeypatch):
    """The Editor's requirements form: its schema (backends of the plugins), the file kinds, the problems of a file."""
    from editor_t import Repos

    from testsuit.reqs.backend import describe_req_backends
    from testsuit.sequencer.editor_schemas import schemas
    monkeypatch.delenv("REQ_T_TOKEN", raising=False)
    repos = Repos(tmp_path)
    editor = repos.editor
    session = editor.open("workspace0")
    yaml_backend = next(b for b in describe_req_backends() if b["name"] == "yaml")
    assert set(yaml_backend["options"]) == {"file", "url"} and "versions" in yaml_backend["capabilities"]
    ctx = editor.context(session)
    assert any(b["name"] == "yaml" for b in ctx["req_backends"])
    backend = schemas(ctx)["requirements"]["properties"]["sources"]["additionalProperties"]["properties"]["backend"]
    assert "yaml" in backend["enum"]

    # a sources file, and the requirements file its yaml source reads
    editor.write(session, "reqs/sys.yml", content=yaml.safe_dump(REQS))
    sources = {"default": "reqs", "sources": {
        "reqs": {"backend": "yaml", "options": {"file": "reqs/sys.yml"}},
        "jama": {"backend": "jama", "options": {"url": "https://jama"}},
        "wiki": {"backend": "yaml", "options": {"file": "reqs/sys.yml"}, "credentials": {"token": "env:REQ_T_TOKEN"}}}}
    editor.write(session, "requirements.yml", content=yaml.safe_dump(sources, sort_keys=False))
    assert editor.kind(session, "requirements.yml") == "requirements"
    assert editor.kind(session, "reqs/sys.yml") == "requirements"
    assert sorted(editor.scan_workspace(session.folder, session.folder)["requirements"]) == ["reqs/sys.yml",
                                                                                            "requirements.yml"]
    problems = editor.check_file(session, "requirements.yml")
    by = {(p["severity"], p["line"]) for p in problems}
    assert any("unknown requirements backend 'jama'" in p["message"] and p["severity"] == "error" for p in problems)
    assert any("REQ_T_TOKEN" in p["message"] and p["severity"] == "warning" for p in problems)
    assert all(line > 1 for _, line in by)
    assert editor.check_file(session, "reqs/sys.yml") == []
    editor.write(session, "reqs/sys.yml", content="requirements:\n  REQ-1: {path: SYS, title: x, colour: red}\n")
    assert editor.kind(session, "reqs/sys.yml") == "requirements"
    assert "unknown field(s) colour" in editor.check_file(session, "reqs/sys.yml")[0]["message"]


REQIF = """<?xml version="1.0" encoding="UTF-8"?>
<REQ-IF xmlns="http://www.omg.org/spec/ReqIF/20110401/reqif.xsd" xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <THE-HEADER><REQ-IF-HEADER IDENTIFIER="h1"><TITLE>Train system</TITLE></REQ-IF-HEADER></THE-HEADER>
  <CORE-CONTENT><REQ-IF-CONTENT>
    <DATATYPES>
      <DATATYPE-DEFINITION-STRING IDENTIFIER="dt-str" LONG-NAME="String" MAX-LENGTH="1000"/>
      <DATATYPE-DEFINITION-XHTML IDENTIFIER="dt-xhtml" LONG-NAME="XHTML"/>
      <DATATYPE-DEFINITION-ENUMERATION IDENTIFIER="dt-status" LONG-NAME="Status">
        <SPECIFIED-VALUES>
          <ENUM-VALUE IDENTIFIER="st-ok" LONG-NAME="approved"/><ENUM-VALUE IDENTIFIER="st-no" LONG-NAME="rejected"/>
        </SPECIFIED-VALUES>
      </DATATYPE-DEFINITION-ENUMERATION>
    </DATATYPES>
    <SPEC-TYPES>
      <SPEC-OBJECT-TYPE IDENTIFIER="t-head" LONG-NAME="Heading">
        <SPEC-ATTRIBUTES><ATTRIBUTE-DEFINITION-STRING IDENTIFIER="a-chap" LONG-NAME="ReqIF.ChapterName">
          <TYPE><DATATYPE-DEFINITION-STRING-REF>dt-str</DATATYPE-DEFINITION-STRING-REF></TYPE>
        </ATTRIBUTE-DEFINITION-STRING></SPEC-ATTRIBUTES>
      </SPEC-OBJECT-TYPE>
      <SPEC-OBJECT-TYPE IDENTIFIER="t-req" LONG-NAME="Requirement">
        <SPEC-ATTRIBUTES>
          <ATTRIBUTE-DEFINITION-STRING IDENTIFIER="a-id" LONG-NAME="ReqIF.ForeignID"><TYPE><DATATYPE-DEFINITION-STRING-REF>dt-str</DATATYPE-DEFINITION-STRING-REF></TYPE></ATTRIBUTE-DEFINITION-STRING>
          <ATTRIBUTE-DEFINITION-STRING IDENTIFIER="a-name" LONG-NAME="ReqIF.Name"><TYPE><DATATYPE-DEFINITION-STRING-REF>dt-str</DATATYPE-DEFINITION-STRING-REF></TYPE></ATTRIBUTE-DEFINITION-STRING>
          <ATTRIBUTE-DEFINITION-XHTML IDENTIFIER="a-text" LONG-NAME="ReqIF.Text"><TYPE><DATATYPE-DEFINITION-XHTML-REF>dt-xhtml</DATATYPE-DEFINITION-XHTML-REF></TYPE></ATTRIBUTE-DEFINITION-XHTML>
          <ATTRIBUTE-DEFINITION-ENUMERATION IDENTIFIER="a-status" LONG-NAME="Status" MULTI-VALUED="false"><TYPE><DATATYPE-DEFINITION-ENUMERATION-REF>dt-status</DATATYPE-DEFINITION-ENUMERATION-REF></TYPE></ATTRIBUTE-DEFINITION-ENUMERATION>
        </SPEC-ATTRIBUTES>
      </SPEC-OBJECT-TYPE>
      <SPECIFICATION-TYPE IDENTIFIER="t-spec" LONG-NAME="Specification"/>
    </SPEC-TYPES>
    <SPEC-OBJECTS>
      <SPEC-OBJECT IDENTIFIER="o-brake" LAST-CHANGE="2026-01-01T00:00:00Z"><TYPE><SPEC-OBJECT-TYPE-REF>t-head</SPEC-OBJECT-TYPE-REF></TYPE>
        <VALUES><ATTRIBUTE-VALUE-STRING THE-VALUE="Braking / stopping"><DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>a-chap</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION></ATTRIBUTE-VALUE-STRING></VALUES>
      </SPEC-OBJECT>
      <SPEC-OBJECT IDENTIFIER="o-12" LAST-CHANGE="2026-02-01T00:00:00Z"><TYPE><SPEC-OBJECT-TYPE-REF>t-req</SPEC-OBJECT-TYPE-REF></TYPE>
        <VALUES>
          <ATTRIBUTE-VALUE-STRING THE-VALUE="SYS-12"><DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>a-id</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION></ATTRIBUTE-VALUE-STRING>
          <ATTRIBUTE-VALUE-STRING THE-VALUE="Stopping distance"><DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>a-name</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION></ATTRIBUTE-VALUE-STRING>
          <ATTRIBUTE-VALUE-XHTML><DEFINITION><ATTRIBUTE-DEFINITION-XHTML-REF>a-text</ATTRIBUTE-DEFINITION-XHTML-REF></DEFINITION>
            <THE-VALUE><xhtml:div><xhtml:p>The train stops within <xhtml:b>30 m</xhtml:b>.</xhtml:p><xhtml:ul><xhtml:li>dry rail</xhtml:li><xhtml:li>10 m/s</xhtml:li></xhtml:ul></xhtml:div></THE-VALUE>
          </ATTRIBUTE-VALUE-XHTML>
          <ATTRIBUTE-VALUE-ENUMERATION><DEFINITION><ATTRIBUTE-DEFINITION-ENUMERATION-REF>a-status</ATTRIBUTE-DEFINITION-ENUMERATION-REF></DEFINITION><VALUES><ENUM-VALUE-REF>st-ok</ENUM-VALUE-REF></VALUES></ATTRIBUTE-VALUE-ENUMERATION>
        </VALUES>
      </SPEC-OBJECT>
      <SPEC-OBJECT IDENTIFIER="o-13" LAST-CHANGE="2026-02-02T00:00:00Z"><TYPE><SPEC-OBJECT-TYPE-REF>t-req</SPEC-OBJECT-TYPE-REF></TYPE>
        <VALUES>
          <ATTRIBUTE-VALUE-STRING THE-VALUE="SYS-13"><DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>a-id</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION></ATTRIBUTE-VALUE-STRING>
          <ATTRIBUTE-VALUE-ENUMERATION><DEFINITION><ATTRIBUTE-DEFINITION-ENUMERATION-REF>a-status</ATTRIBUTE-DEFINITION-ENUMERATION-REF></DEFINITION><VALUES><ENUM-VALUE-REF>st-no</ENUM-VALUE-REF></VALUES></ATTRIBUTE-VALUE-ENUMERATION>
        </VALUES>
      </SPEC-OBJECT>
      <SPEC-OBJECT IDENTIFIER="o-14" LAST-CHANGE="2026-02-03T00:00:00Z"><TYPE><SPEC-OBJECT-TYPE-REF>t-req</SPEC-OBJECT-TYPE-REF></TYPE>
        <VALUES>
          <ATTRIBUTE-VALUE-STRING THE-VALUE="SYS-14"><DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>a-id</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION></ATTRIBUTE-VALUE-STRING>
          <ATTRIBUTE-VALUE-STRING THE-VALUE="Brake light"><DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>a-name</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION></ATTRIBUTE-VALUE-STRING>
        </VALUES>
      </SPEC-OBJECT>
      <SPEC-OBJECT IDENTIFIER="o-intro" LAST-CHANGE="2026-01-01T00:00:00Z"><TYPE><SPEC-OBJECT-TYPE-REF>t-req</SPEC-OBJECT-TYPE-REF></TYPE>
        <VALUES>
          <ATTRIBUTE-VALUE-STRING THE-VALUE="SYS-1"><DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>a-id</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION></ATTRIBUTE-VALUE-STRING>
          <ATTRIBUTE-VALUE-STRING THE-VALUE="Scope"><DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>a-name</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION></ATTRIBUTE-VALUE-STRING>
        </VALUES>
      </SPEC-OBJECT>
    </SPEC-OBJECTS>
    <SPECIFICATIONS>
      <SPECIFICATION IDENTIFIER="s-sys" LONG-NAME="System"><TYPE><SPECIFICATION-TYPE-REF>t-spec</SPECIFICATION-TYPE-REF></TYPE>
        <CHILDREN>
          <SPEC-HIERARCHY IDENTIFIER="h-intro"><OBJECT><SPEC-OBJECT-REF>o-intro</SPEC-OBJECT-REF></OBJECT></SPEC-HIERARCHY>
          <SPEC-HIERARCHY IDENTIFIER="h-brake"><OBJECT><SPEC-OBJECT-REF>o-brake</SPEC-OBJECT-REF></OBJECT>
            <CHILDREN>
              <SPEC-HIERARCHY IDENTIFIER="h-12"><OBJECT><SPEC-OBJECT-REF>o-12</SPEC-OBJECT-REF></OBJECT>
                <CHILDREN><SPEC-HIERARCHY IDENTIFIER="h-14"><OBJECT><SPEC-OBJECT-REF>o-14</SPEC-OBJECT-REF></OBJECT></SPEC-HIERARCHY></CHILDREN>
              </SPEC-HIERARCHY>
              <SPEC-HIERARCHY IDENTIFIER="h-13"><OBJECT><SPEC-OBJECT-REF>o-13</SPEC-OBJECT-REF></OBJECT></SPEC-HIERARCHY>
            </CHILDREN>
          </SPEC-HIERARCHY>
        </CHILDREN>
      </SPECIFICATION>
    </SPECIFICATIONS>
  </REQ-IF-CONTENT></CORE-CONTENT>
</REQ-IF>
"""


def test_reqif_backend(tmp_path):
    """The built-in ReqIF backend: specifications and headings as the tree, attributes guessed or named, XHTML text,
    enumerations, .reqifz archives, versions; through a sources file and its references."""
    from testsuit.reqs.backend import create_req_backend, describe_req_backends
    (tmp_path / "system.reqif").write_text(REQIF, encoding="utf-8")
    b = create_req_backend("reqif", {"file": "system.reqif", "url": "https://doors/{id}?o={identifier}"}, None, tmp_path)
    assert [n.as_dict() for n in b.tree()] == [{"id": "System", "label": "System", "children": [
        {"id": "Braking - stopping", "label": "Braking / stopping", "children": []}]}]
    reqs = {r.id: r for r in b.list("", True)}
    assert set(reqs) == {"SYS-1", "SYS-12", "SYS-13", "SYS-14"}
    r12 = reqs["SYS-12"]
    assert (r12.path, r12.title, r12.status) == ("System/Braking - stopping", "Stopping distance", "approved")
    assert r12.description == "The train stops within **30 m**.\n- dry rail\n- 10 m/s"
    assert r12.url == "https://doors/SYS-12?o=o-12" and len(r12.version) == 8
    assert reqs["SYS-14"].path == r12.path                   # under a requirement: in its node
    assert reqs["SYS-1"].path == "System" and reqs["SYS-13"].inactive
    assert [r.id for r in b.list("System", False)] == ["SYS-1"]
    assert b.get(["SYS-12", "NOPE"])["NOPE"] is None and b.revision()

    # options: another id attribute, the last change as version, one specification
    b2 = create_req_backend("reqif", {"file": "system.reqif", "id_attribute": "ReqIF.Name",
                                      "version_attribute": "LAST-CHANGE", "specifications": ["System"]}, None, tmp_path)
    assert b2.get(["Stopping distance"])["Stopping distance"].version == "2026-02-01T00:00:00Z"
    with pytest.raises(ReqError, match="no specification 'Other'"):
        create_req_backend("reqif", {"file": "system.reqif", "specifications": "Other"}, None, tmp_path)

    # a .reqifz archive, through a sources file: references, versions changed since
    with zipfile.ZipFile(tmp_path / "export.reqifz", "w") as z:
        z.writestr("export/system.reqif", REQIF)
    (tmp_path / "requirements.yml").write_text(yaml.safe_dump(
        {"sources": {"sys": {"backend": "reqif", "options": {"file": "export.reqifz"}}}}), encoding="utf-8")
    sources = Sources(tmp_path / "requirements.yml")
    assert sources.backend("sys").get(["SYS-12"])["SYS-12"].description.startswith("The train stops")
    (tmp_path / "bad.reqif").write_text("<html/>", encoding="utf-8")
    with pytest.raises(ReqError, match="not a ReqIF file"):
        create_req_backend("reqif", {"file": "bad.reqif"}, None, tmp_path)
    reqif = next(d for d in describe_req_backends() if d["name"] == "reqif")
    assert {"file", "id_attribute", "specifications"} <= set(reqif["options"])


ALM = [{"key": f"ALM-{i}", "fields": {"name": f"Item {i}", "text": f"Text {i}", "status": {"name": "approved" if i != 3 else "rejected"}},
        "folder": [{"name": "SYS"}, {"name": "BRAKE" if i < 3 else "SPEED"}], "labels": [{"name": "critical"}] if i == 1 else []}
       for i in range(1, 6)]


@pytest.fixture
def alm_server():
    """A requirements tool with a JSON REST API: bearer token, pages, a cursor, get by ids and by id."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            calls.append(self.path)
            if self.headers.get("Authorization") != "Bearer s3cret":
                return self.answer(401, {"error": "who are you"})
            if url.path == "/api/items":                       # ?page=N, 2 per page
                page = int(q.get("page", 1))
                return self.answer(200, {"data": ALM[(page - 1) * 2:page * 2]})
            if url.path == "/api/cursor":                      # links.next
                start = int(q.get("from", 0))
                nxt = f"/api/cursor?from={start + 3}" if start + 3 < len(ALM) else None
                return self.answer(200, {"data": ALM[start:start + 3], "links": {"next": nxt}})
            if url.path == "/api/byids":
                ids = q.get("ids", "").split(",")
                return self.answer(200, {"data": [a for a in ALM if a["key"] in ids]})
            if url.path.startswith("/api/item/"):
                found = [a for a in ALM if a["key"] == url.path.rsplit("/", 1)[1]]
                return self.answer(200, found[0]) if found else self.answer(404, {"error": "no such item"})
            self.answer(404, {})

        def answer(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", calls
    server.shutdown()


def test_rest_backend(tmp_path, alm_server, monkeypatch):
    """The built-in generic REST backend: authentication, pagination by page or cursor, jq fields, get by ids or id."""
    from testsuit.reqs.backend import create_req_backend
    base, calls = alm_server
    fields = {"id": ".key", "title": ".fields.name", "description": ".fields.text", "status": ".fields.status.name",
              "path": '[.folder[]?.name] | join("/")', "tags": "[.labels[]?.name]"}
    options = {"base": base + "/api", "auth": {"bearer": "token"}, "fields": fields, "url": "https://alm/{id}",
               "list": {"url": "items?page={page}", "items": ".data[]"}}
    b = create_req_backend("rest", options, {"token": "s3cret"}, tmp_path)
    reqs = {r.id: r for r in b.list("", True)}
    assert list(reqs) == [f"ALM-{i}" for i in range(1, 6)] and len(calls) == 4     # 3 pages and an empty one
    r1 = reqs["ALM-1"]
    assert (r1.title, r1.description, r1.path, r1.tags, r1.status, r1.url) == (
        "Item 1", "Text 1", "SYS/BRAKE", ["critical"], "approved", "https://alm/ALM-1")
    assert reqs["ALM-3"].inactive and len(r1.version) == 8
    assert [n.as_dict()["id"] for n in b.tree()] == ["SYS"] and [r.id for r in b.list("SYS/SPEED")] == ["ALM-3", "ALM-4", "ALM-5"]

    # a cursor (next), get by ids then by id (404: unknown)
    cursor = {**options, "list": {"url": "cursor", "items": ".data[]", "next": ".links.next // empty"},
              "get": {"url": "byids?ids={ids}", "items": ".data[]"}}
    calls.clear()
    b = create_req_backend("rest", cursor, {"token": "s3cret"}, tmp_path)
    got = b.get(["ALM-2", "ALM-9"])
    assert got["ALM-2"].title == "Item 2" and got["ALM-9"] is None and calls == ["/api/byids?ids=ALM-2,ALM-9"]
    assert len(b.list("", True)) == 5 and len(calls) == 3
    one = {**options, "get": {"url": "item/{id}", "items": "."}}
    b = create_req_backend("rest", one, {"token": "s3cret"}, tmp_path)
    got = b.get(["ALM-4", "NOPE"])
    assert got["ALM-4"].path == "SYS/SPEED" and got["NOPE"] is None

    # errors: a bad token, a bad jq expression, an unknown credential, an unknown field
    with pytest.raises(ReqError, match="HTTP 401"):
        create_req_backend("rest", options, {"token": "wrong"}, tmp_path).list()
    with pytest.raises(ReqError, match="field 'title': jq expression"):
        create_req_backend("rest", {**options, "fields": {**fields, "title": ".fields[["}}, {"token": "x"}, tmp_path)
    with pytest.raises(ReqError, match="credential 'token'"):
        create_req_backend("rest", options, None, tmp_path)
    with pytest.raises(ReqError, match="unknown field"):
        create_req_backend("rest", {**options, "fields": {"colour": ".c"}}, {"token": "x"}, tmp_path)

    # through a sources file and the Editor's Test connection
    from editor_t import Repos
    monkeypatch.setenv("ALM_TOKEN", "s3cret")
    repos = Repos(tmp_path / "repos")
    editor = repos.editor
    session = editor.open("workspace0")
    editor.write(session, "requirements.yml", content=yaml.safe_dump({"sources": {"alm": {
        "backend": "rest", "credentials": {"token": "env:ALM_TOKEN"}, "options": options}}}))
    assert editor.check_file(session, "requirements.yml") == []
    result = editor.requirements_test(session, "requirements.yml", "alm")
    assert result["ok"] and result["count"] == 5 and result["nodes"] == 3 and result["sample"][0]["id"] == "ALM-1"
    monkeypatch.setenv("ALM_TOKEN", "wrong")
    result = editor.requirements_test(session, "requirements.yml", "alm")
    assert not result["ok"] and "HTTP 401" in result["error"]

    # a credential read from a file (a Docker secret): absolute, or relative to the sources file
    secret = tmp_path / "secrets" / "alm_token"
    secret.parent.mkdir()
    secret.write_text("s3cret\n", encoding="utf-8")
    for where in (str(secret), "../../secrets/alm_token"):
        (tmp_path / "repos" / "x").mkdir(exist_ok=True)
        sources_file = tmp_path / "repos" / "x" / "requirements.yml"
        sources_file.write_text(yaml.safe_dump({"sources": {"alm": {
            "backend": "rest", "credentials": {"token": f"file:{where}"}, "options": options}}}), encoding="utf-8")
        assert len(Sources(sources_file).backend("alm").list("", True)) == 5
    editor.write(session, "requirements.yml", content=yaml.safe_dump({"sources": {"alm": {
        "backend": "rest", "credentials": {"token": "file:/nonexistent/secret"}, "options": options}}}))
    problems = editor.check_file(session, "requirements.yml")
    assert [p["severity"] for p in problems] == ["warning"] and "is not readable" in problems[0]["message"]
    with pytest.raises(ReqError, match="env:<VARIABLE>'\\) or a file"):
        (tmp_path / "repos" / "x" / "requirements.yml").write_text(yaml.safe_dump({"sources": {"alm": {
            "backend": "rest", "credentials": {"token": "s3cret"}, "options": options}}}), encoding="utf-8")
        Sources(tmp_path / "repos" / "x" / "requirements.yml").backend("alm")
