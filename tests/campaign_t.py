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
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from testsuit.datatools.datapack import datapack_tools, importers
from testsuit.datatools.datapack.baseline import nonconform_reasons
from testsuit.datatools.datapack.campaign import (CampaignError, apply_routing, build_campaign, load_campaign,
                                                  looks_like_campaign, read_manifest)
from testsuit.datatools.datapack.datapack_tools import DatapackError, datapack, read_baseline


class _DirImporter:
    """An importer writing a VERSION file; a version starting with 'branch-' is not a tag."""

    def __init__(self, targetDir, remotePath, versionId):
        self.target, self.version = Path(targetDir), versionId

    def retrieve(self):
        self.target.mkdir(parents=True)
        (self.target / "VERSION").write_text(self.version)
        return True

    def checkVersion(self, expected):
        return (self.target / "VERSION").read_text() == expected and not expected.startswith("branch-")

    def getChanges(self, testClean=False):
        return True if testClean else []


@pytest.fixture
def tagged(monkeypatch):
    monkeypatch.setattr(datapack_tools, "git_state", lambda folder: {"commit": "0" * 40, "tag": "v1", "clean": True})


@pytest.fixture
def dir_importer():
    importers.register_data_importer("dir", _DirImporter)
    yield
    importers.unregister_data_importer("dir")


def _repo(tmp_path: Path) -> Path:
    """A tests repository: one setup importing datasets sw (v1) and sim (v1), two test definitions."""
    root = tmp_path / "tests"
    (root / "setup").mkdir(parents=True)
    setup = {"id": "s", "datarepositories": [{"id": "r", "path": str(tmp_path / "datarepo"), "datasources": [
                 {"id": "src", "importer": "dir", "source": "nowhere",
                  "datasets": [{"id": "sw", "version": "v1"}, {"id": "sim", "version": "v1"}]}]}],
             "slot_classes": {"t": {"configurations": {"std": {"inputs": []}}}},
             "configurations": {"c": {"slots": {"a": {"slot_class": "t", "configuration": "std"}}},
                                "d": {"slots": {"b": {"slot_class": "t", "configuration": "std"}}}}}
    (root / "setup" / "setup.yml").write_text(yaml.safe_dump(setup, sort_keys=False))
    for name in ("one", "two"):
        (root / "testdef" / name).mkdir(parents=True)
        (root / "testdef" / name / "testdef.yml").write_text(yaml.safe_dump(
            {"id": name, "name": name.title(), "setup_file": "../../setup/setup.yml", "setup_config": "c"}))
    return root


def _campaign(root: Path, **fields) -> Path:
    data = {"id": "nightly", "name": "Nightly", "priority": 60,
            "testdefs": ["testdef/one/testdef.yml", {"testdef": "testdef/two/testdef.yml", "setup_config": "d", "priority": 80}],
            "routing": {"stage_tags": ["bench_2"], "component_tags": {"pilot": ["rev_b"]}},
            "datasets": {"sw": "v2", "sim": "v1"}, **fields}
    path = root / "nightly.campaign.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_campaign_file():
    data = {"id": "c", "testdefs": ["a.yml", {"testdef": "b.yml", "setup_config": "x"}], "datasets": {"sw": 1.2}}
    assert looks_like_campaign(data) and not looks_like_campaign({"testdefs": [], "pipeline": {}})
    from testsuit.datatools.datapack.campaign import Campaign
    campaign = Campaign.model_validate(data)
    assert campaign.datasets == {"sw": "1.2"} and campaign.testdefs[0].testdef == "a.yml"
    with pytest.raises(ValueError, match="listed twice"):
        Campaign.model_validate({"id": "c", "testdefs": ["a.yml", "a.yml"]})
    with pytest.raises(ValueError, match="at least one"):
        Campaign.model_validate({"id": "c", "testdefs": []})
    with pytest.raises(ValueError, match="without a version"):
        Campaign.model_validate({"id": "c", "testdefs": ["a.yml"], "datasets": {"sw": ""}})
    with pytest.raises(ValueError):
        Campaign.model_validate({"id": "c", "testdefs": ["a.yml"], "priority": 101})


def test_missing_testdef(tmp_path):
    root = _repo(tmp_path)
    with pytest.raises(CampaignError, match="not found: testdef/nope.yml"):
        load_campaign(_campaign(root, testdefs=["testdef/nope.yml"]))


def test_build_campaign(tmp_path, tagged, dir_importer):
    """every datapack is built with the versions the campaign pins, recorded as such; conform when they are tags"""
    root = _repo(tmp_path)
    folder = build_campaign(_campaign(root), output_dir=tmp_path / "out")
    manifest = read_manifest(folder)
    assert folder.parent == tmp_path / "out" and folder.name.startswith("nightly_")
    assert manifest["id"] == "nightly" and manifest["conform"] is True
    assert [(d["testdef"], d["setup_config"], d["priority"]) for d in manifest["datapacks"]] == [
        ("testdef/one/testdef.yml", None, 60), ("testdef/two/testdef.yml", "d", 80)]
    assert manifest["routing"] == {"stage_tags": ["bench_2"], "component_tags": {"pilot": ["rev_b"]}}
    for entry in manifest["datapacks"]:
        baseline = read_baseline(folder / entry["zip"])
        assert baseline["datapack"]["conform"] is True and nonconform_reasons(baseline) == []
        assert baseline["campaign"]["id"] == "nightly"
        assert {"role": "campaign"}.items() <= next(s for s in baseline["sources"] if s["role"] == "campaign").items()
        versions = {s["id"]: (s["version"], s.get("pinned_by")) for s in baseline["sources"] if s["role"] == "dataset"}
        assert versions == {"sw": ("v2", "campaign"), "sim": ("v1", "campaign")}
        assert baseline["versions_pinned_by_campaign"] == [
            {"dataset": "sw", "version": "v2", "overrides": True, "was": "v1", "tag": True},
            {"dataset": "sim", "version": "v1", "overrides": False}]
        assert "versions_set_by_hand" not in baseline
    assert (tmp_path / "datarepo" / "sw" / "v2").is_dir() and not (tmp_path / "datarepo" / "sw" / "v1").exists()


def test_every_dataset_listed(tmp_path, tagged, dir_importer):
    """a dataset a test definition imports and the campaign does not list stops the generation: nothing is left"""
    root = _repo(tmp_path)
    with pytest.raises(CampaignError, match="'sim' of setup .* not listed in the campaign"):
        build_campaign(_campaign(root, datasets={"sw": "v2"}), output_dir=tmp_path / "out")
    assert not list((tmp_path / "out").glob("*"))


def test_pinned_version_not_a_tag(tmp_path, tagged, dir_importer, capsys):
    root = _repo(tmp_path)
    folder = build_campaign(_campaign(root, datasets={"sw": "branch-x", "sim": "v1", "unused": "v9"}),
                            output_dir=tmp_path / "out", nocheck=True)
    manifest = read_manifest(folder)
    assert manifest["conform"] is False and manifest["unused_datasets"] == ["unused"]
    baseline = read_baseline(folder / manifest["datapacks"][0]["zip"])
    assert "dataset 'sw' pinned by the campaign to version 'branch-x' instead of 'v1', not a tag of its source" \
        in nonconform_reasons(baseline)
    assert "imported by no test definition: unused" in capsys.readouterr().out


def test_no_version_by_hand_in_a_campaign(tmp_path, tagged, dir_importer):
    root = _repo(tmp_path)
    with pytest.raises(DatapackError, match="no dataset version can be set by hand"):
        datapack(root / "testdef/one/testdef.yml", output_dir=tmp_path / "out", dataset_versions={"sw": "v3"},
                 campaign={"id": "c", "file": str(root / "c.campaign.yml"), "datasets": {"sw": "v2", "sim": "v1"}})


def test_untagged_campaign_needs_nocheck(tmp_path, dir_importer, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setattr(datapack_tools, "git_state", lambda folder: {"commit": "0" * 40, "tag": None, "clean": True})
    with pytest.raises(CampaignError, match="Campaign folder is not a git tag"):
        build_campaign(_campaign(root), output_dir=tmp_path / "out")
    assert read_manifest(build_campaign(_campaign(root), output_dir=tmp_path / "out", nocheck=True))["name"].endswith("_DRAFT")


def _ask(root: Path, name: str, user_keys: dict) -> None:
    path = root / "testdef" / name / "testdef.yml"
    data = yaml.safe_load(path.read_text())
    path.write_text(yaml.safe_dump({**data, "user_keys": user_keys}))


def test_campaign_keys(tmp_path, tagged, dir_importer):
    """the keys its test definitions ask are shared by name: pinned by the campaign (its own, an entry's over
    them), or given at generation for every test definition; a pinned key cannot be given again"""
    root = _repo(tmp_path)
    _ask(root, "one", {"mode": {"choices": ["quick", "full"]}, "operator": {}})
    _ask(root, "two", {"mode": {"choices": ["full", "quick", "slow"]}, "operator": {}})
    with pytest.raises(CampaignError, match="key 'operator' is to be given at each generation.*pinned in the campaign file"):
        build_campaign(_campaign(root, keys={"mode": "full"}), output_dir=tmp_path / "out")
    assert not list((tmp_path / "out").glob("*"))
    file = _campaign(root, keys={"mode": "full"},
                     testdefs=["testdef/one/testdef.yml",
                               {"testdef": "testdef/two/testdef.yml", "setup_config": "d", "keys": {"mode": "slow"}}])
    with pytest.raises(CampaignError, match="pinned by the campaign cannot be given at generation \\(mode\\)"):
        build_campaign(file, output_dir=tmp_path / "out", manual_keys={"mode": "quick", "operator": "jd"})
    folder = build_campaign(file, output_dir=tmp_path / "out", manual_keys={"operator": "jd"})
    manifest = read_manifest(folder)
    assert manifest["conform"] is True and manifest["keys"] == {"mode": "full"} and manifest["given_keys"] == {"operator": "jd"}
    assert [d["keys"] for d in manifest["datapacks"]] == [{"mode": "full", "operator": "jd"}, {"mode": "slow", "operator": "jd"}]
    baseline = read_baseline(folder / manifest["datapacks"][1]["zip"])
    assert baseline["keys_set_by_hand"] == [
        {"key": "operator", "value": "jd", "overrides": False},
        {"key": "mode", "value": "slow", "overrides": False, "pinned_by_campaign": True}]
    with pytest.raises(CampaignError, match="'fast' is not one of its choices"):
        build_campaign(_campaign(root, keys={"mode": "quick", "operator": "x"},
                                 testdefs=[{"testdef": "testdef/two/testdef.yml", "keys": {"mode": "fast"}}]),
                       output_dir=tmp_path / "out")
    folder = build_campaign(_campaign(root, keys={"operator": "jd"}, testdefs=[
        "testdef/one/testdef.yml", {"testdef": "testdef/two/testdef.yml", "setup_config": "d", "keys": {"mode": "slow"}}]),
        output_dir=tmp_path / "out", manual_keys={"mode": "full"})   # given for every one, but two pins its own
    assert [d["keys"] for d in read_manifest(folder)["datapacks"]] == [{"mode": "full", "operator": "jd"},
                                                                     {"mode": "slow", "operator": "jd"}]
    from testsuit.datatools.datapack.campaign import Campaign
    with pytest.raises(ValueError, match="not a key name"):
        Campaign.model_validate({"id": "c", "testdefs": ["a.yml"], "keys": {"a..b": 1}})


def test_cli(tmp_path, tagged, dir_importer, monkeypatch, capsys):
    from testsuit.cli import datapack as cli
    root = _repo(tmp_path)
    monkeypatch.setattr("sys.argv", ["datapack", str(_campaign(root)), "-o", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0 and "Generated campaign:" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["datapack", str(_campaign(root)), "--dataset-version", "sw=v3"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1 and "do not apply to a campaign" in capsys.readouterr().out


def test_apply_routing():
    slots = {"p": SimpleNamespace(role="pilot", tags=["x"]), "t": SimpleNamespace(role="tm", tags=[])}
    stage_tags: dict = {"train": ["a"]}
    apply_routing({"train": "toytrain", "sim": "simu"}, stage_tags, slots,
                  {"stage_tags": ["bench_2"], "component_tags": {"pilot": ["rev_b", "x"]}})
    assert stage_tags == {"train": ["a", "bench_2"], "sim": ["bench_2"]}
    assert slots["p"].tags == ["x", "rev_b"] and slots["t"].tags == []
    stage_tags = {}
    apply_routing({"train": "toytrain", "sim": "simu"}, stage_tags, {}, {"stage_tags": {"simu": ["fast"]}})
    assert stage_tags == {"train": [], "sim": ["fast"]}


# ── campaign runs on the sequencer: the counter bench of sequencer_t ──

def _bench_campaign(tmp_path, monkeypatch, **fields) -> tuple:
    from sequencer_t import Bench
    monkeypatch.setattr(datapack_tools, "git_state", lambda folder: {"commit": "0" * 40, "tag": None, "clean": False})
    bench = Bench(tmp_path, stages=2)
    bench.build("count")
    bench.build("too_short", min_count=100000)
    campaign = {"id": "nightly", "name": "Nightly", "priority": 40,
                "testdefs": ["testdef/count/testdef.yml", {"testdef": "testdef/too_short/testdef.yml", "priority": 70}],
                **fields}
    path = bench.dp / "nightly.campaign.yml"
    path.write_text(yaml.safe_dump(campaign, sort_keys=False))
    return bench, build_campaign(path, nocheck=True, output_dir=tmp_path / "campaigns")


def test_campaign_run(tmp_path, monkeypatch):
    """the runs of a campaign are queued together, routed by its tags, and summed up in one report"""
    import asyncio

    from sequencer_t import read_member, run_until_done

    from testsuit.sequencer.campaigns import report_html
    bench, folder = _bench_campaign(tmp_path, monkeypatch, routing={"stage_tags": ["bench_2"]})

    async def main():
        seq = bench.sequencer()
        view = seq.submit_campaign(folder)
        assert view["state"] == "queued" and view["counts"]["total"] == 2
        ids = [r["run"] for r in view["runs"]]
        assert [seq.store.get_run(i)["priority"] for i in ids] == [40, 70]
        assert all(seq.store.get_run(i)["campaign_run"] == view["id"] for i in ids)
        runs = await run_until_done(seq, ids)
        view = seq.campaign_view(view["id"])
        await seq.close()
        return view, runs
    view, runs = asyncio.run(main())
    assert [r["binding"]["stage_slots"]["bench"] for r in runs] == ["bench_2", "bench_2"]   # routed: one at a time
    assert view["state"] == "done" and view["verdict"] == "failed"
    assert (view["counts"]["succeeded"], view["counts"]["failed"]) == (1, 1)
    assert view["report"] and Path(view["report"]).is_file() and view["ended_at"]
    failed = view["runs"][1]
    assert failed["testdef_file"] == "testdef/too_short/testdef.yml" and failed["failed_step"]["id"] == "analyse"
    assert failed["stages"] == ["bench_2"] and failed["results"]
    record = yaml.safe_load(read_member(runs[0], "run.yml"))
    assert record["campaign_run"] == view["id"] and record["routing"]["stage_tags"] == ["bench_2"]
    baseline = yaml.safe_load(read_member(runs[0], "baseline.yml"))
    assert baseline["run"]["routing"]["stage_tags"] == ["bench_2"]
    page = report_html(view, base="../../")
    assert f'href="../../#/runs/{runs[1]["id"]}"' in page and "log of exploit:analyse" in page
    assert "results.zip?results=true" in page


def test_campaign_routing_refused(tmp_path, monkeypatch):
    """a campaign whose tags no lab resource carries is refused whole: none of its runs is queued"""
    import asyncio

    from testsuit.sequencer.util import SequencerError
    bench, folder = _bench_campaign(tmp_path, monkeypatch, routing={"component_tags": {"dut": ["rev_z"]}})

    async def main():
        seq = bench.sequencer()
        with pytest.raises(SequencerError, match="testdef/count/testdef.yml: the lab cannot run this datapack with "
                                                 "the campaign's routing tags"):
            seq.submit_campaign(folder)
        assert seq.store.runs() == [] and seq.store.campaign_runs() == []
        assert not list(bench.config.storage_path("runs").iterdir())
        await seq.close()
    asyncio.run(main())


def test_campaign_api_and_cli(tmp_path, monkeypatch, capsys):
    import io

    from starlette.datastructures import UploadFile

    from testsuit.cli import tsq
    from testsuit.sequencer.api import create_app
    bench, folder = _bench_campaign(tmp_path, monkeypatch)
    seq = bench.sequencer()
    routes = {(r.path, m): r.endpoint for r in create_app(seq).routes if hasattr(r, "endpoint") for m in r.methods}

    async def main():
        loop = asyncio.get_running_loop()

        def call(coro):                               # tsq runs in a thread, the endpoints on this loop
            return asyncio.run_coroutine_threadsafe(coro, loop).result()

        class Http:                                   # tsq's client, straight onto the API endpoints
            def get(self, path, **kw):
                return call(routes[("/campaigns/{campaign_id}", "GET")](path.rsplit("/", 1)[1]))

            def post(self, path, files=(), data=None, **kw):
                if path == "/campaigns":
                    ups = {}
                    for field, (name, f, _) in files:
                        ups.setdefault(field, []).append(UploadFile(io.BytesIO(f.read()), filename=name))
                        f.close()
                    priority = int(data["priority"]) if data and "priority" in data else None
                    return call(routes[("/campaigns", "POST")](ups["manifest"][0], ups["datapacks"], priority))
                cid, command = path.split("/")[2:4]
                return call(routes[("/campaigns/{campaign_id}/{command}", "POST")](cid, command))

        args = SimpleNamespace(campaign=str(folder), priority=20, wait=False)
        assert await asyncio.to_thread(tsq.cmd_submit_campaign, args, Http()) == 0
        out = capsys.readouterr().out
        assert "2 run(s) queued" in out
        [view] = await routes[("/campaigns", "GET")]()
        assert view["state"] == "queued" and [r["priority"] for r in view["runs"]] == [20, 20]
        assert (await routes[("/campaigns/{campaign_id}", "GET")](view["id"][:8]))["id"] == view["id"]
        page = await routes[("/campaigns/{campaign_id}/report", "GET")](view["id"], SimpleNamespace(base_url="http://tsq/"))
        assert page.status_code == 200 and "<title>Nightly · campaign report</title>" in page.body.decode()
        assert {r["campaign_run"] for r in await routes[("/runs", "GET")]()} == {view["id"]}
        cancelled = await routes[("/campaigns/{campaign_id}/{command}", "POST")](view["id"], "cancel")
        assert cancelled["state"] == "done" and cancelled["verdict"] == "error"
        args = SimpleNamespace(id=view["id"], cancel=False, kill=False)
        assert await asyncio.to_thread(tsq.cmd_campaign, args, Http()) == 0
        assert "[error]" in capsys.readouterr().out
        await seq.close()
    asyncio.run(main())
