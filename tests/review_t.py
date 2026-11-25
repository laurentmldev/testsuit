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

"""Reviews of run results and exported reports (sequencer SPEC sections 11.1 and 11.2): accepting and rejecting
checks, steps and verdicts, their effect on the campaign view, the reports and the requirements coverage, sign-off,
re-runs, and the synthetic and complete exports."""
import asyncio
import zipfile
from pathlib import Path

import pytest
import yaml

from sequencer_t import Bench, exploit_bench, run_until_done
from testsuit.datatools.datapack import datapack_tools
from testsuit.datatools.datapack.campaign import build_campaign
from testsuit.sequencer import export as ex
from testsuit.sequencer import review as rv
from testsuit.sequencer.util import SequencerError

CHECK = {"kind": "check", "step": "exploit:criteria", "criteria": "criteria/count", "scenario": "count",
         "check": "counted_enough"}


# ── the review journal, on run records ─────────────────────────────────────

def _run(**fields) -> dict:
    return {"id": "r1", "state": "done", "verdict": "failed", "steps": [
        {"phase": "main", "id": "acq", "state": "succeeded", "log": "logs/main/acq.log"},
        {"phase": "main", "id": "crit", "state": "failed", "exploit": {"report": "x.html", "checks": [
            {"criteria": "criteria/speed", "scenario": "speed", "check": "max", "status": "FAILED"},
            {"criteria": "criteria/speed", "scenario": "speed", "check": "min", "status": "SUCCESS"}]}},
        {"phase": "main", "id": "after", "state": "skipped", "reason": "needs ['crit'] did not succeed"},
        {"phase": "main", "id": "optional", "state": "failed", "continue_on_error": True}], **fields}


MAX = {"kind": "check", "step": "main:crit", "criteria": "criteria/speed", "scenario": "speed", "check": "max"}


def test_review_rules():
    run, review = _run(), rv.empty({"run": "r1"})
    for body, refused in (
            ({"target": MAX, "status": "accepted"}, "needs its reason"),
            ({"target": {**MAX, "check": "min"}, "status": "accepted", "reason": "x"}, "met already"),
            ({"target": {"kind": "step", "step": "main:acq"}, "status": "accepted", "reason": "x"}, "only a failure"),
            ({"target": {"kind": "step", "step": "main:nope"}, "comment": "x"}, "has no step main:nope"),
            ({"target": MAX, "status": "maybe", "reason": "x"}, "unknown review status"),
            ({"target": {"kind": "campaign"}, "comment": "x"}, "unknown review target kind"),
            ({"target": MAX}, "needs a status")):
        with pytest.raises(rv.ReviewError, match=refused):
            rv.add(review, body, "dana", run)
    with pytest.raises(rv.ReviewError, match="names who"):
        rv.add(review, {"target": MAX, "comment": "x"}, "", run)
    with pytest.raises(rv.ReviewError, match="only the results of an ended run"):
        rv.add(review, {"target": MAX, "comment": "x"}, "dana", _run(state="running"))
    error_run = _run()
    error_run["steps"][1]["exploit"]["checks"][0]["status"] = "ERROR"
    with pytest.raises(rv.ReviewError, match="outcome is unknown"):
        rv.add(review, {"target": MAX, "status": "accepted", "reason": "x"}, "dana", error_run)
    with pytest.raises(rv.ReviewError, match="outcome is unknown"):
        rv.add(review, {"target": {"kind": "run"}, "status": "accepted", "reason": "x"}, "dana", _run(verdict="error"))
    assert review["entries"] == [] and rv.state(review) == rv.NONE


def test_review_propagation():
    run, review = _run(), rv.empty({"run": "r1"})
    entry = rv.add(review, {"target": MAX, "status": "accepted", "reason": "known noise", "ref": "BUG-12"}, "dana", run)
    assert (entry["id"], entry["was"], entry["by"]) == (1, "FAILED", "dana")
    out = rv.apply_review(run, review)
    assert run["verdict"] == "failed" and run["steps"][1]["state"] == "failed"       # the run itself is left as it is
    crit = out["steps"][1]
    assert crit["exploit"]["checks"][0]["status"] == "ACCEPTED" and crit["exploit"]["checks"][0]["raw_status"] == "FAILED"
    assert (crit["state"], crit["raw_state"], crit["reviewed"]) == ("succeeded", "failed", "checks")
    # the step that does not count (continue_on_error) stays failed, the verdict comes from the others
    assert (out["verdict"], out["raw_verdict"]) == ("succeeded", "failed")
    assert out["review"]["state"] == rv.OPEN and out["review"]["accepted"] == 1
    assert "held back by a result now accepted: main:after" in out["review"]["warnings"][0]
    # revoked: back to the raw result
    rv.add(review, {"action": "revoke", "entry": 1, "reason": "not noise after all"}, "lee", run)
    assert rv.apply_review(run, review)["verdict"] == "failed"
    with pytest.raises(rv.ReviewError, match="revoked already"):
        rv.add(review, {"action": "revoke", "entry": 1, "reason": "x"}, "lee", run)
    # a step rejected over its checks; the run's verdict over its steps
    rv.add(review, {"target": {"kind": "step", "step": "main:acq"}, "status": "rejected", "reason": "wrong bench"}, "lee", run)
    assert rv.apply_review(run, review)["steps"][0]["state"] == "failed"
    rv.add(review, {"target": {"kind": "run"}, "status": "accepted", "reason": "waived by the board"}, "lee", run)
    assert rv.apply_review(run, review)["verdict"] == "succeeded"
    # sign-off: closed until reopened, with a reason
    rv.add(review, {"action": "sign_off", "comment": "ok for release"}, "lee", run)
    assert rv.state(review) == rv.SIGNED_OFF
    with pytest.raises(rv.ReviewError, match="signed off: reopen it first"):
        rv.add(review, {"target": MAX, "comment": "late"}, "dana", run)
    with pytest.raises(rv.ReviewError, match="needs its reason"):
        rv.add(review, {"action": "reopen"}, "dana", run)
    rv.add(review, {"action": "reopen", "reason": "one more look"}, "dana", run)
    assert rv.state(review) == rv.OPEN
    # a verdict that does not come from the steps (an aborted run): step reviews leave it
    aborted, review2 = _run(abort="cancel", reason="cancelled", verdict="error"), rv.empty({})
    aborted["steps"][1]["state"] = "error"
    rv.add(review2, {"target": {"kind": "step", "step": "main:crit"}, "status": "rejected", "reason": "x"}, "dana", aborted)
    out = rv.apply_review(aborted, review2)
    assert out["verdict"] == "error" and "does not come from its steps alone" in out["review"]["warnings"][0]


def test_exploit_overlay_and_reapply():
    run, review = _run(), rv.empty({"run": "r1"})
    rv.add(review, {"target": MAX, "status": "accepted", "reason": "known </script> noise"}, "dana", run)
    out = rv.apply_review(run, review)
    page = rv.reviewed_exploit_html("<html><body><table></table></body></html>", out["steps"][1], out["review"])
    assert "tsq-review-banner" in page and "DRAFT" in page and '"status": "ACCEPTED"' in page
    assert "known <\\/script> noise" in page and page.endswith("</body></html>")
    # an earlier attempt's reviews that fit a re-run: same target, same raw result, not reviewed there yet
    again = _run(id="r2")
    fits = rv.reapplicable(again, {"id": "r1"}, review, rv.empty({}))
    assert [(f["id"], f["label"]) for f in fits] == [(1, "check main:crit: criteria/speed::max")]
    again["steps"][1]["exploit"]["checks"][0]["status"] = "SUCCESS"
    assert rv.reapplicable(again, {"id": "r1"}, review, rv.empty({})) == []


def test_trim_log_keeps_errors_and_warnings():
    lines = [f"line {i}" for i in range(10000)]
    lines[5000] = "ERROR: the bench is gone"
    lines[6000] = "\x1b[33mWARNING\x1b[0m: slow answer"
    text, left = ex.trim_log("\n".join(lines), limit=1000)
    kept = text.splitlines()
    assert kept[:ex.LOG_HEAD] == lines[:ex.LOG_HEAD] and kept[-ex.LOG_TAIL:] == lines[-ex.LOG_TAIL:]
    assert "ERROR: the bench is gone" in kept and lines[6000] in kept and "line 5500" not in kept
    assert left == 10000 - ex.LOG_HEAD - ex.LOG_TAIL - 2
    page = ex.log_page("\n".join(lines), "log", "index.html", limit=1000)
    assert '<span class="l e">ERROR: the bench is gone</span>' in page and "line(s) left out" in page
    assert '<span class="ansi-yellow">WARNING</span>' in page
    assert ex.trim_log("small", 1000) == ("small", 0)


# ── on the sequencer: a campaign run reviewed, then exported ───────────────

@pytest.fixture
def campaign(tmp_path, monkeypatch):
    """A campaign run of two test definitions, one whose check is not met (its exploit step and run failed)."""
    monkeypatch.setattr(datapack_tools, "git_state", lambda folder: {"commit": "0" * 40, "tag": None, "clean": False})
    bench = Bench(tmp_path, stages=2)
    bench.build("count")
    exploit_bench(bench, "short", {"criteria": ["criteria/count"], "data": ["test_run:dut/output/*.csv"]},
                  min_count=100000)
    path = bench.dp / "nightly.campaign.yml"
    path.write_text(yaml.safe_dump({"id": "nightly", "name": "Nightly", "testdefs": [
        "testdef/count/testdef.yml", "testdef/short/testdef.yml"]}, sort_keys=False))
    folder = build_campaign(path, nocheck=True, output_dir=tmp_path / "campaigns")

    async def main():
        seq = bench.sequencer()
        view = seq.submit_campaign(folder)
        await run_until_done(seq, [r["run"] for r in view["runs"]], timeout=120)
        return seq, seq.campaign_view(view["id"])
    seq, view = asyncio.run(main())
    yield bench, seq, view
    asyncio.run(seq.close())


def test_review_on_the_sequencer(campaign):
    bench, seq, view = campaign
    assert view["verdict"] == "failed"
    short = next(r for r in view["runs"] if r["testdef"] == "short")
    run_id = short["run"]
    # a check accepted: the step, the run and the campaign run succeed as reviewed, the raw verdicts kept
    seq.review_run(run_id, {"target": CHECK, "status": "accepted", "reason": "the bench counts slowly", "ref": "BUG-7"}, "dana")
    assert yaml.safe_load(seq.review_path(run_id).read_text())["entries"][0]["was"] == "FAILED"
    assert seq.store.get_run(run_id)["verdict"] == "failed"
    view = seq.campaign_view(view["id"])
    entry = next(r for r in view["runs"] if r["run"] == run_id)
    assert (entry["verdict"], entry["raw_verdict"]) == ("succeeded", "failed")
    assert (view["verdict"], view["raw_verdict"]) == ("succeeded", "failed")
    assert view["reviews"]["accepted"] == 1 and entry["review"]["state"] == "open" and not entry["checks"]["not_met"]
    # the requirements, the reports
    page = seq.exploit_report_html(run_id, "exploit:criteria")
    assert "tsq-review-banner" in page and "the bench counts slowly" in page
    assert seq.exploit_report_html(run_id, "criteria") == page               # the step by its id alone
    report = seq.run_report_html(run_id)
    assert "the bench counts slowly" in report and "raw: " in report and "DRAFT" in report
    from testsuit.sequencer.campaigns import report_html, report_text
    campaign_page = report_html(view)
    assert "DRAFT" in campaign_page and "accepted by review" in campaign_page and "BUG-7" in campaign_page
    assert "(raw failed, reviewed)" in "\n".join(report_text(view))
    # a running run, or a target it does not have: refused
    with pytest.raises(SequencerError, match="has no check"):
        seq.review_run(run_id, {"target": {**CHECK, "check": "nope"}, "comment": "x"}, "dana")
    # the campaign run signed off: the open run reviews with it
    with pytest.raises(SequencerError, match="a campaign run's verdict comes from its runs"):
        seq.review_campaign(view["id"], {"target": {"kind": "campaign"}, "status": "accepted", "reason": "x"}, "lee")
    seq.review_campaign(view["id"], {"target": {"kind": "campaign"}, "comment": "good enough for the release"}, "lee")
    view = seq.review_campaign(view["id"], {"action": "sign_off", "comment": "release 1.2"}, "lee")
    assert view["review"]["state"] == "signed_off" and view["reviews"]["signed_off"] == 1
    assert rv.state(seq.run_review(run_id)) == rv.SIGNED_OFF
    final = report_html(view)
    assert "Final: review signed off by lee" in final and "DRAFT" not in final
    assert "good enough for the release" in final
    stored = Path(seq.config.storage_path("results") / "campaigns" / view["id"] / "report.html").read_text()
    assert "Final: review signed off" in stored                            # written again after the review
    with pytest.raises(SequencerError, match="signed off: reopen it first"):
        seq.review_run(run_id, {"target": CHECK, "comment": "late"}, "dana")

    # re-run: the earlier attempt's reviews offered, reapplied on demand
    seq.review_run(run_id, {"action": "reopen", "reason": "bench fixed, run again"}, "lee")

    async def rerun():
        new = seq.rerun(run_id)
        await run_until_done(seq, [new["id"]], timeout=120)
        return new
    new = asyncio.run(rerun())
    view = seq.run_review_view(new["id"])
    assert [f["label"] for f in view["reapplicable"]] == ["check exploit:criteria: criteria/count::counted_enough"]
    view = seq.review_run(new["id"], {"action": "reapply"}, "dana")
    assert view["verdict"] == "succeeded" and view["journal"][0]["reapplied_from"] == {"run": run_id, "entry": 1}
    assert view["reapplicable"] == []


def test_exports(campaign, tmp_path):
    bench, seq, view = campaign
    short = next(r for r in view["runs"] if r["testdef"] == "short")
    seq.review_run(short["run"], {"target": CHECK, "status": "accepted", "reason": "slow bench"}, "dana")
    stages = []
    export = ex.campaign_export(seq, view["id"], ex.COMPLETE, "http://tsq:8640/",
                                progress=lambda stage, done, total: stages.append((stage, done, total)))
    assert stages[-1][1] == stages[-1][2] and {s for s, _, _ in stages} >= {"logs", "exploit reports", "run reports"}
    folders = sorted({p.split("/attempt-")[0] for p in export.pages if p.startswith("runs/")})
    assert folders == ["runs/count", "runs/short"]
    index = export.pages["index.html"]
    assert 'href="runs/short/attempt-1/index.html"' in index
    run_page = export.pages["runs/short/attempt-1/index.html"]
    assert 'href="exploit/exploit_criteria.html"' in run_page and "slow bench" in run_page
    assert any(p.startswith("runs/short/attempt-1/logs/") for p in export.pages)
    assert "tsq-review-banner" in export.pages["runs/short/attempt-1/exploit/exploit_criteria.html"]
    assert "runs/short/attempt-1/review.yml" in export.files
    # one HTML file, every page in it
    out = ex.write(export, tmp_path / "out" / "nightly")
    assert out.suffix == ".html" and "DecompressionStream" in out.read_text()
    assert "slow bench" in ex.page_of(out, "runs/short/attempt-1/index.html")
    # over the size limit: a zip of the same pages, readable with tsq view
    out = ex.write(export, tmp_path / "out" / "nightly_zip", single_max=10)
    assert out.suffix == ".zip"
    with zipfile.ZipFile(out) as z:
        assert {"index.html", "README.txt", "runs/short/attempt-1/index.html"} <= set(z.namelist())
    assert "slow bench" in ex.page_of(out, "runs/short/attempt-1/index.html")
    # synthetic: the report alone, its links to the sequencer
    synthetic = ex.campaign_export(seq, view["id"], ex.SYNTHETIC, "http://tsq:8640/")
    assert list(synthetic.pages) == ["index.html"] and "http://tsq:8640/#/runs/" in synthetic.pages["index.html"]
    out = ex.write(synthetic, tmp_path / "out" / "synthetic", synthetic=True)
    assert out.suffix == ".html" and "slow bench" in out.read_text()
    # a run alone
    export = ex.run_export(seq, short["run"], ex.COMPLETE)
    assert "index.html" in export.pages and any(p.startswith("logs/") for p in export.pages)
    assert 'href="logs/' in export.pages["index.html"]


def test_export_jobs(campaign):
    bench, seq, view = campaign
    events = []
    seq.subscribers.add(type("Q", (), {"put_nowait": lambda self, e: events.append(e)})())

    async def main():
        job = await seq.start_export("campaign", view["id"], "complete")
        again = await seq.start_export("campaign", view["id"], "complete")
        assert again["id"] == job["id"]                                   # the same export: not redone
        while seq.export_job(job["id"])["state"] == "running":
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.1)                                          # its last events, sent to this loop
        return seq.export_job(job["id"])
    job = asyncio.run(main())
    assert job["state"] == "done" and Path(job["file"]).is_file() and job["name"].endswith("_complete_report.html")
    assert any(e["kind"] == "export_progress" for e in events), events
    assert [e["state"] for e in events if e["kind"] == "export"] == ["running", "done"]
    with pytest.raises(SequencerError, match="unknown export mode"):
        asyncio.run(seq.start_export("campaign", view["id"], "huge"))


def test_review_and_export_cli(campaign, tmp_path, capsys):
    """tsq review and tsq export, their client mapped straight onto the API endpoints."""
    from types import SimpleNamespace

    from testsuit.cli import tsq
    from testsuit.sequencer.api import create_app
    bench, seq, view = campaign
    short = next(r for r in view["runs"] if r["testdef"] == "short")["run"]
    routes = {(r.path, m): r.endpoint for r in create_app(seq).routes if hasattr(r, "endpoint") for m in r.methods}
    request = SimpleNamespace(headers={}, base_url="http://tsq:8640/")

    async def main():
        loop = asyncio.get_running_loop()

        def call(coro):
            return asyncio.run_coroutine_threadsafe(coro, loop).result()

        class Http:
            def get(self, path, **kw):
                parts = path.strip("/").split("/")
                if parts[0] == "exports":
                    return call(routes[("/exports/{job_id}", "GET")](parts[1]))
                if parts[0] == "campaigns":
                    return call(routes[("/campaigns/{campaign_id}/review", "GET")](parts[1]))
                return call(routes[("/runs/{run_id}/review", "GET")](parts[1]))

            def post(self, path, json=None, **kw):
                parts = path.strip("/").split("/")
                if parts[0] == "campaigns":
                    endpoint = routes[(f"/campaigns/{{campaign_id}}/{parts[2]}", "POST")]
                else:
                    endpoint = routes[(f"/runs/{{run_id}}/{parts[2]}", "POST")]
                if parts[2] == "export":
                    return call(endpoint(parts[1], request, json))
                return call(endpoint(parts[1], json, request))

            def request(self, method, path, **kw):
                response = call(routes[("/exports/{job_id}/download", "GET")](path.split("/")[2]))
                data = Path(response.path).read_bytes()
                return SimpleNamespace(iter_content=lambda n: [data])

        def args(**kw):
            base = dict(id=short, campaign=False, accept=None, reject=None, revoke=None, sign_off=False, reopen=False,
                        reapply=False, reason=None, comment=None, on=None, ref=None, by="dana")
            return SimpleNamespace(**{**base, **kw})

        review = tsq.cmd_review
        with pytest.raises(tsq.CliError, match="no single check"):
            await asyncio.to_thread(review, args(accept="check:criteria::nope", reason="x"), Http())
        assert await asyncio.to_thread(review, args(accept="check:criteria::counted_enough", reason="slow bench",
                                                    ref="BUG-7"), Http()) == 0
        out = capsys.readouterr().out
        assert "verdict succeeded (raw failed)" in out and "dana: accepted check exploit:criteria" in out
        assert "(was FAILED): slow bench [BUG-7]" in out
        assert await asyncio.to_thread(review, args(comment="looked at the plots", on="step:exploit:criteria"), Http()) == 0
        assert await asyncio.to_thread(review, args(revoke=2, reason="wrong step"), Http()) == 0
        assert "[revoked]" in capsys.readouterr().out
        assert await asyncio.to_thread(review, args(id=view["id"], campaign=True, sign_off=True, comment="v1.2"), Http()) == 0
        assert await asyncio.to_thread(review, args(), Http()) == 0
        assert "review: signed off" in capsys.readouterr().out
        with pytest.raises(tsq.CliError, match="reviewed through its runs"):
            await asyncio.to_thread(review, args(id=view["id"], campaign=True, accept="run", reason="x"), Http())

        export = SimpleNamespace(id=view["id"], campaign=True, synthetic=False, with_results=False,
                                 output=str(tmp_path / "dl"))
        assert await asyncio.to_thread(tsq.cmd_export, export, Http()) == 0
        [out] = (tmp_path / "dl").iterdir()
        assert out.name.endswith("_complete_report.html") and "slow bench" in ex.page_of(out, "runs/short/attempt-1/index.html")
    asyncio.run(main())
