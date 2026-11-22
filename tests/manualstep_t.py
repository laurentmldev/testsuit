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

"""Test campaigns: the campaign file, its datapacks generated with the dataset versions it pins, routing tags."""
"""Manual operations (sequencer SPEC section 6.9): a step that waits for the operator, and campaigns whose datapack
runs again when the bench was not set up right (section 9.6)."""
import asyncio
import copy
import zipfile

import pytest
import yaml
from sequencer_t import PIPELINE, Bench, run_until_done, running_step, write

from testsuit.datatools.datapack import datapack_tools
from testsuit.datatools.datapack.campaign import build_campaign
from testsuit.sequencer import manualstep as ms
from testsuit.sequencer.model import Step
from testsuit.sequencer.util import SequencerError

MANUAL = {"title": "Plug cable A", "text": "Plug **cable A** on pin F of {{ testdef.id }}.\n\n![Pin F](images/pin_f.png)",
          "checks": ["Cable A plugged on pin F", "Power LED green"]}
PNG = bytes.fromhex("89504e470d0a1a0a")


def test_manual_step_checked():
    Step.model_validate({"id": "plug", "manual": MANUAL}).check()
    with pytest.raises(SequencerError, match="takes a mapping"):
        ms.check_manual("plug it", "step 'plug'")
    for spec, problem in (({"text": "x", "steps": []}, "unknown 'manual' key"),
                          ({"title": "nothing to do"}, "needs a 'text' or 'checks'"),
                          ({"checks": "one"}, "list of non-empty texts"), ({"checks": [""]}, "list of non-empty texts"),
                          ({"text": "![x](../pin.png)"}, "inside its folder")):
        with pytest.raises(SequencerError, match=problem):
            Step.model_validate({"id": "plug", "manual": spec}).check()
    with pytest.raises(SequencerError, match="no 'slot' nor 'for_each'"):
        Step.model_validate({"id": "plug", "slot": "dut", "manual": MANUAL}).check()
    Step.model_validate({"id": "plug", "manual": {"text": "![web](https://example.org/a.png)"}}).check()


def test_manual_images_and_answers():
    spec = {"text": '![a](images/a.png) ![b](<images/b.png> "B") ![a](images/a.png) ![w](https://x.org/w.png)'}
    assert ms.images(spec) == ["images/a.png", "images/b.png", "https://x.org/w.png"]
    pipeline = {"phases": [{"id": "p", "steps": [{"id": "s", "manual": spec}],
                            "finally": [{"id": "f", "manual": {"text": "![c](c.jpg)"}}]}],
                "subpipelines": {"sub": {"steps": [{"id": "t", "manual": {"text": "![d](d/d.png)"}}]}}}
    assert ms.pipeline_manual_images(pipeline) == ["images/a.png", "images/b.png", "c.jpg", "d/d.png"]

    record = ms.waiting_record("plug", MANUAL)
    assert record["title"] == "Plug cable A" and [c["done"] for c in record["checks"]] == [False, False]
    with pytest.raises(SequencerError, match="tick or bypass every check before 'done' \\(2 left"):
        ms.acknowledge(record, {"outcome": "done"})
    assert ms.acknowledge(record, {"check": 0, "done": True}, by="ann") is None
    assert record["checks"][0]["by"] == "ann"
    with pytest.raises(SequencerError, match="no check 5"):
        ms.acknowledge(record, {"check": 5})
    assert ms.acknowledge(record, {"check": 1, "outcome": "done"}, by="bob") == "done"
    assert record["outcome"] == "done" and record["by"] == "bob"
    with pytest.raises(SequencerError, match="already done"):
        ms.acknowledge(record, {"outcome": "failed"})
    record = ms.waiting_record("plug", MANUAL)
    with pytest.raises(SequencerError, match="give a comment saying why it is bypassed"):
        ms.acknowledge(record, {"check": 1, "bypass": True})
    ms.acknowledge(record, {"check": 0, "comment": "cable a bit loose"}, by="ann")
    assert record["checks"][0] == {**record["checks"][0], "done": False, "comment": "cable a bit loose", "by": "ann"}
    ms.acknowledge(record, {"check": 0}, by="ann")                   # ticked, its comment kept
    assert record["checks"][0]["done"] and record["checks"][0]["comment"] == "cable a bit loose"
    ms.acknowledge(record, {"check": 1, "bypass": True, "comment": "LED broken, power measured"}, by="bob")
    assert record["checks"][1]["bypassed"] and not record["checks"][1]["done"]
    assert ms.bypassed(record) == [record["checks"][1]]
    ms.acknowledge(record, {"check": 1, "bypass": False})
    with pytest.raises(SequencerError, match="1 left: Power LED green"):
        ms.acknowledge(record, {"outcome": "done"})
    ms.acknowledge(record, {"check": 1, "bypass": True})                # its comment is still its reason
    ms.acknowledge(record, {"note": "bench 2 only"})
    assert ms.acknowledge(record, {"outcome": "done"}) == "done" and record["note"] == "bench 2 only"
    steps = [{"phase": "p", "id": "s", "state": "running", "manual": {"title": "T", "since": "now", "outcome": None}},
             {"phase": "p", "id": "u", "state": "running", "manual": {"title": "U", "outcome": "done"}}]
    assert ms.awaiting(steps) == [{"step": "p:s", "title": "T", "since": "now"}]


def manual_pipeline(**step) -> dict:
    pipeline = copy.deepcopy(PIPELINE)
    steps = pipeline["phases"][0]["steps"]
    steps.insert(3, {"id": "plug", "manual": MANUAL, "needs": ["ready"], **step})
    steps[4]["needs"] = ["plug"]                          # run: after the cable is plugged
    return pipeline


def picture(bench: Bench, name: str) -> None:
    write(bench.dp / "testdef" / name / "images" / "pin_f.png", "")
    (bench.dp / "testdef" / name / "images" / "pin_f.png").write_bytes(PNG)


def manual_bench(tmp_path, monkeypatch, name: str = "plugged", **step) -> tuple[Bench, object]:
    monkeypatch.setattr(datapack_tools, "git_state", lambda folder: {"commit": "0" * 40, "tag": None, "clean": False})
    bench = Bench(tmp_path, stages=1)
    picture(bench, name)
    return bench, bench.build(name, manual_pipeline(**step))


async def waiting(seq, run_id: str) -> None:
    await running_step(seq, run_id, "test_run:plug")
    while not seq.executions[run_id].ctx.manual_waits:
        await asyncio.sleep(0.02)


def test_manual_step_waits_for_the_operator(tmp_path, monkeypatch):
    from testsuit.sequencer.api import create_app
    bench, zip_path = manual_bench(tmp_path, monkeypatch)
    with zipfile.ZipFile(zip_path) as z:                  # the picture travels with the datapack
        assert any(n.endswith("/definition/images/pin_f.png") for n in z.namelist())

    async def main():
        seq = bench.sequencer()
        routes = {(r.path, m): r.endpoint for r in create_app(seq).routes if hasattr(r, "endpoint") for m in r.methods}
        answer = routes[("/runs/{run_id}/manual/{step:path}", "POST")]
        run = seq.submit(zip_path)

        async def operator(seq):
            await waiting(seq, run["id"])
            listed = seq.store.runs()[0]                      # flagged in the All runs list
            assert listed["awaiting_manual"][0]["step"] == "test_run:plug"
            assert listed["awaiting_manual"][0]["title"] == "Plug cable A"
            shown = await routes[("/runs/{run_id}", "GET")](run["id"])
            record = next(s for s in shown["steps"] if s["id"] == "plug")
            assert record["manual"]["text"].startswith("Plug **cable A** on pin F of plugged.")
            assert record["manual"]["base"] == "definition/"
            with pytest.raises(Exception, match="tick or bypass every check"):
                await answer(run["id"], "test_run:plug", {"outcome": "done"})
            await answer(run["id"], "test_run:pl", {"check": 0, "by": "ann"})
            await answer(run["id"], "test_run:plug", {"check": 1, "bypass": True, "comment": "LED broken", "by": "ann"})
            done = await answer(run["id"], "test_run:plug", {"outcome": "done", "note": "on bench 1", "by": "ann"})
            assert done["manual"]["outcome"] == "done"
        [run] = await run_until_done(seq, [run["id"]], during=operator)
        await seq.close()
        return run
    run = asyncio.run(main())
    assert run["verdict"] == "succeeded" and run["awaiting_manual"] == []
    plug = next(s for s in run["steps"] if s["id"] == "plug")
    assert plug["state"] == "succeeded" and [c["by"] for c in plug["manual"]["checks"]] == ["ann", "ann"]
    assert plug["reason"] == "1 check(s) bypassed: Power LED green (LED broken)"
    log = next((tmp_path / "storage" / "runs" / run["id"]).rglob("plug.log")).read_text()
    assert "manual operation done by ann" in log and "[bypassed] Power LED green (ann): LED broken" in log
    assert "comment: on bench 1" in log
    from testsuit.sequencer.campaigns import run_summary
    assert run_summary(run)["bypassed_checks"] == [{"step": "test_run:plug", "check": "Power LED green",
                                                   "comment": "LED broken", "by": "ann"}]


def test_manual_step_failed_or_not_answered(tmp_path, monkeypatch):
    bench, zip_path = manual_bench(tmp_path, monkeypatch)
    with pytest.raises(datapack_tools.DatapackError, match="picture 'images/pin_f.png' shown by a manual step .* not found"):
        bench.build("unattended", manual_pipeline(timeout="0.3s"))
    picture(bench, "unattended")
    timed_out = bench.build("unattended", manual_pipeline(timeout="0.3s"))

    async def main():
        seq = bench.sequencer()
        run = seq.submit(zip_path)

        async def operator(seq):
            await waiting(seq, run["id"])
            with pytest.raises(SequencerError, match="no single manual operation 'test_run:nope'"):
                seq.answer_manual(run["id"], "test_run:nope", {"outcome": "failed"})
            seq.answer_manual(run["id"], "test_run:plug", {"outcome": "failed", "note": "no cable A on the bench"})
        [failed] = await run_until_done(seq, [run["id"]], during=operator)
        [unattended] = await run_until_done(seq, [seq.submit(timed_out)["id"]])
        with pytest.raises(SequencerError, match="waits for no manual operation"):
            seq.answer_manual(failed["id"], "test_run:plug", {"outcome": "done"})
        await seq.close()
        return failed, unattended
    failed, unattended = asyncio.run(main())
    plug = next(s for s in failed["steps"] if s["id"] == "plug")
    assert failed["verdict"] == "failed" and plug["reason"] == "manual operation failed: no cable A on the bench"
    assert next(s for s in failed["steps"] if s["id"] == "run")["state"] == "skipped"
    plug = next(s for s in unattended["steps"] if s["id"] == "plug")
    assert unattended["verdict"] == "error" and plug["reason"] == "manual operation: no answer in time"


def test_campaign_datapack_runs_again(tmp_path, monkeypatch):
    """the bench was not set up right: the operator fails the manual operation, the datapack runs again, unchanged,
    and the campaign's verdict is that of its last attempt"""
    bench, _ = manual_bench(tmp_path, monkeypatch)
    bench.build("count")
    path = bench.dp / "bench.campaign.yml"
    path.write_text(yaml.safe_dump({"id": "bench", "name": "Bench",
                                    "testdefs": ["testdef/count/testdef.yml", "testdef/plugged/testdef.yml"]}))
    folder = build_campaign(path, nocheck=True, output_dir=tmp_path / "campaigns")

    async def main():
        seq = bench.sequencer()
        view = seq.submit_campaign(folder)
        first = view["runs"][1]["run"]
        with pytest.raises(SequencerError, match="only an ended run"):
            seq.rerun(first)

        async def operator(seq):
            await waiting(seq, first)
            seq.answer_manual(first, "test_run:plug", {"outcome": "failed", "note": "wrong cable"})
        await run_until_done(seq, [r["run"] for r in view["runs"]], during=operator)
        view = seq.campaign_view(view["id"])
        assert view["state"] == "done" and view["verdict"] == "failed" and view["ended_at"]

        again = seq.rerun(first)
        assert again["id"] != first and again["rerun_of"] == first and again["campaign_run"] == view["id"]
        assert again["routing"] == seq.store.get_run(first)["routing"]
        with pytest.raises(SequencerError, match=f"earlier attempt: run {again['id']} took its place"):
            seq.rerun(first)
        view = seq.campaign_view(view["id"])
        assert view["state"] == "running" and view["verdict"] is None and not view["ended_at"]
        assert [r["run"] for r in view["runs"]][1] == again["id"] and view["counts"]["total"] == 2

        async def fixed(seq):
            await waiting(seq, again["id"])
            for i in range(2):
                seq.answer_manual(again["id"], "test_run:plug", {"check": i})
            seq.answer_manual(again["id"], "test_run:plug", {"outcome": "done"})
        await run_until_done(seq, [again["id"]], during=fixed)
        view = seq.campaign_view(view["id"])
        alone = seq.submit(bench.build("alone"))
        with pytest.raises(SequencerError, match="not part of a campaign run"):
            seq.rerun(alone["id"])
        await seq.close()
        return view
    view = asyncio.run(main())
    assert view["state"] == "done" and view["verdict"] == "succeeded"
    entry = view["runs"][1]
    assert entry["attempt"] == 2 and [a["verdict"] for a in entry["earlier"]] == ["failed"]
    assert entry["earlier"][0]["failed_step"]["id"] == "plug"
    from testsuit.sequencer.campaigns import report_html, report_text
    assert f'#/runs/{entry["earlier"][0]["run"]}' in report_html(view)
    assert "attempt 2 (earlier: " in "\n".join(report_text(view))
