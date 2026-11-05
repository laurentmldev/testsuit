"""Sequencer kernel: a small bench of local processes run end to end (queue, locks, phases, abort, results)."""
import asyncio
import copy
import socket
import time
import zipfile
from pathlib import Path

import pytest
import yaml

from testsuit.datatools.datapack import datapack_tools
from testsuit.datatools.datapack.datapack_tools import datapack, verify_datapack
from testsuit.sequencer.config import Config
from testsuit.sequencer.inventory import load_inventory
from testsuit.sequencer.sequencer import EXIT_CODES, Sequencer
from testsuit.sequencer.store import Store

# A counter application: READY in its log, then a count in count.txt every 50 ms until TERM.
APP = """#!/bin/sh
out="$1"
mkdir -p "$out"
trap 'echo stopped >> "$out/app.log"; exit 0' TERM
echo "READY $(cat "$(dirname "$0")/conf.txt")" > "$out/app.log"
n=0
while true; do n=$((n+1)); echo $n > "$out/count.txt"; sleep 0.05; done
"""

# The analysis: succeeded when the dut counted at least {{ min_count }}.
CHECK = """#!/bin/sh
count=$(cat "$1/data/count.txt")
echo "count $count, min {{ min_count }}" | tee "$2/verdict.txt"
[ "$count" -ge {{ min_count }} ]
"""

LAB_ROLES = {                                            # abstract: the commands a role offers
    "roles": {
        "dut": {"extends": "linux_system", "commands": {"start": "Starts the counter.", "stop": "Stops it.",
                                                        "ready": "Waits until it counts.", "version": None}},
        "sequencer": {"extends": "linux_system"},
    },
}

LAB_COMPONENTS = {                                       # concrete: how they are implemented
    "components": {
        "counter_host": {
            "extends": "linux_system",
            "roles": ["dut"],                            # the roles it implements: its hosts are duts
            "commands": {
                "start": {"run": "sh {{ component.input_dir }}/app.sh {{ component.output_dir }}", "detach": True},
                "stop": {"signal": "TERM", "timeout": "5s"},
                "ready": {"wait_for": {"log": {"file": "{{ component.output_dir }}/app.log", "regex": "READY"},
                                       "timeout": "10s"}},
                "version": {"run": "echo counter-1.2.0"},
            },
        },
    },
}

SETUP = {
    "id": "bench_setup",
    "name": "Counter bench",
    "dictionaries": ["globals.yml"],
    "component_types": {
        "dut_app": {"configurations": {"standard": {"inputs": [
            {"source": "files/app.sh", "target": "app.sh", "method": "copy"},
            {"source": "files/conf.txt", "target": "conf.txt"}]}}},
        "analysis": {"configurations": {"standard": {"inputs": [
            {"source": "files/check.sh", "target": "check.sh"}]}}},
    },
    "configurations": {"bench": {
        "stages": {"bench": {"type": "counter_bench"}},
        "components": {
            "dut": {"name": "Device under test", "type": "dut_app", "configuration": "standard", "stage": "bench",
                    "role": "dut"},
            "analysis": {"name": "Analysis", "type": "analysis", "configuration": "standard", "role": "sequencer"},
        }}},
}

PIPELINE = {
    "timeout": "2m",
    "phases": [
        {"id": "test_run", "stages": ["bench"],
         "steps": [
             {"id": "deploy", "for_each": "components", "do": "deploy"},
             {"id": "start", "component": "dut", "do": "start", "needs": ["deploy"]},
             {"id": "ready", "component": "dut", "do": "ready", "needs": ["start"]},
             {"id": "run", "delay": "{{ duration }}", "needs": ["ready"]},
             {"id": "stop", "component": "dut", "do": "stop", "needs": ["run"]}],
         "finally": [{"id": "collect", "for_each": "components", "do": "collect"}]},
        {"id": "exploit", "needs": ["test_run"], "components": ["analysis"],
         "inputs": [{"from": "test_run:dut/output/count.txt", "to": "analysis/input/data/"}],
         "steps": [{"id": "deploy", "component": "analysis", "do": "deploy"},
                   {"id": "analyse", "component": "analysis", "needs": ["deploy"],
                    "run": "sh {{ component.input_dir }}/check.sh {{ component.input_dir }} {{ component.output_dir }}"}],
         "finally": [{"id": "collect", "component": "analysis", "do": "collect"}]},
    ],
}


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class Bench:
    """A lab of two counter benches and the sequencer host, all local processes, under tmp_path."""

    def __init__(self, root: Path, stages: int = 2):
        self.root = root
        lab = root / "lab"
        inventory = {"all": {
            "vars": {"tsq_transport": "local", "tsq_library": ["roles/", "components/"]},
            "hosts": {"seq_host": {"tsq_role": "sequencer", "tsq_lock": "none",
                                   "tsq_remote_root": str(root / "remote" / "seq_host")}},
            "children": {f"bench_{i}": {"vars": {"tsq_stage_type": "counter_bench"}, "hosts": {
                f"counter_{i}": {"tsq_extends": "counter_host",
                                 "tsq_remote_root": str(root / "remote" / f"counter_{i}")}}}
                for i in range(1, stages + 1)}}}
        write(lab / "inventory.yml", yaml.safe_dump(inventory))
        write(lab / "roles" / "bench.yml", yaml.safe_dump(LAB_ROLES))
        write(lab / "components" / "bench.yml", yaml.safe_dump(LAB_COMPONENTS))
        self.config = Config.model_validate({
            "lab": {"folder": str(lab), "inventory": "inventory.yml", "require_tag": False},
            "storage": {"root": str(root / "storage"), "min_free_space": "1MB"},
            "selftest": {"before_each_run": True, "timeout": "20s", "max_attempts": 2},
            "monitor": {"interval": "1h"}})
        dp = root / "datapack"
        write(dp / "setup" / "bench_setup.yml", yaml.safe_dump(SETUP))
        write(dp / "setup" / "globals.yml", yaml.safe_dump({"duration": "0.5s", "min_count": 3}))
        write(dp / "setup" / "files" / "app.sh", APP)
        write(dp / "setup" / "files" / "conf.txt", "{{ testdef.id }}\n")
        write(dp / "setup" / "files" / "check.sh", CHECK)
        self.dp = dp

    def build(self, name: str = "count", pipeline: dict | None = None, **values) -> Path:
        testdef = {"id": name, "name": name, "setup_file": "../../setup/bench_setup.yml", "setup_config": "bench",
                   "dictionaries": ["values.yml"], "pipeline": pipeline or PIPELINE}
        folder = self.dp / "testdef" / name
        write(folder / "values.yml", yaml.safe_dump(values))
        write(folder / "testdef.yml", yaml.safe_dump(testdef))
        return datapack(folder / "testdef.yml", nocheck=True, output_dir=self.root / "zips")

    def sequencer(self) -> Sequencer:
        return Sequencer(self.config)


@pytest.fixture
def no_git(monkeypatch):
    monkeypatch.setattr(datapack_tools, "git_state", lambda folder: {"commit": "0" * 40, "tag": None, "clean": False})


@pytest.fixture
def bench(tmp_path, no_git):
    return Bench(tmp_path)


async def run_until_done(seq: Sequencer, run_ids: list[str], timeout: float = 60, during=None):
    loop = asyncio.ensure_future(seq.scheduler_loop())
    try:
        async def wait():
            if during is not None:
                await during(seq)
            while any(seq.store.get_run(r)["state"] not in ("done", "cancelled") for r in run_ids):
                await asyncio.sleep(0.05)
        await asyncio.wait_for(wait(), timeout)
    finally:
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)
    return [seq.store.get_run(r) for r in run_ids]


async def running_step(seq: Sequencer, run_id: str, key: str) -> None:
    """Wait until a step of a run is running."""
    while True:
        execution = seq.executions.get(run_id)
        record = execution.ctx.records.get(key) if execution and execution.ctx else None
        if record is not None and record.state == "running":
            return
        await asyncio.sleep(0.05)


def results_of(run: dict) -> dict:
    with zipfile.ZipFile(run["results"]) as z:
        root = z.namelist()[0].split("/")[0]
        return {n[len(root) + 1:]: n for n in z.namelist()}, z, root


def read_member(run: dict, name: str) -> str:
    with zipfile.ZipFile(run["results"]) as z:
        root = z.namelist()[0].split("/")[0]
        return z.read(f"{root}/{name}").decode()


def test_inventory(bench):
    inv = load_inventory(bench.config.inventory_file)
    assert sorted(inv.stages) == ["bench_1", "bench_2"]
    assert inv.components["counter_1"].extends == "counter_host" and inv.components["seq_host"].adhoc


def test_inventory_descriptions(tmp_path):
    """Each level has its own tsq_description: the lab's, a stage's and a component's, none inherited."""
    path = tmp_path / "inv.yml"
    path.write_text(yaml.safe_dump({"all": {
        "vars": {"tsq_description": "the lab", "x": 1},
        "hosts": {"adhoc": {}},
        "children": {"st": {"vars": {"tsq_stage_type": "t", "tsq_description": "the stage"},
                            "hosts": {"a": {"tsq_description": "comp a"}, "b": {}}}}}}))
    inv = load_inventory(path)
    assert inv.description == "the lab" and inv.stages["st"].description == "the stage"
    assert inv.components["a"].description == "comp a"
    assert inv.components["b"].description == "" and inv.components["adhoc"].description == ""
    assert "tsq_description" not in inv.components["b"].vars_in(inv.stages["st"])
    assert inv.components["b"].vars_in(inv.stages["st"])["x"] == 1


def test_role_inputs(tmp_path):
    """A role lists the inputs it expects; a role extending it adds to them or redefines one by name."""
    from testsuit.sequencer.library import Library
    from testsuit.sequencer.util import SequencerError
    folder = tmp_path / "lib"
    write(folder / "roles.yml", yaml.safe_dump({"roles": {
        "app": {"extends": "linux_system", "inputs": [
            {"name": "app", "type": "folder", "description": "The build: `make dist`."},
            {"name": "config.yml", "optional": True}]},
        "pilot": {"extends": "app", "inputs": [{"name": "config.yml", "description": "Required here."},
                                               {"name": "profile.bin"}]}}}))
    lib = Library.load([folder])
    assert [(i["name"], i["type"], i["optional"], i["from"]) for i in lib.role_inputs("pilot")] == [
        ("app/", "folder", False, "app"), ("config.yml", "file", False, "pilot"), ("profile.bin", "file", False, "pilot")]
    assert lib.role_inputs("app")[0]["description"] == "The build: `make dist`."
    write(folder / "roles.yml", yaml.safe_dump({"roles": {"bad": {"inputs": [{"name": "x", "type": "socket"}]}}}))
    with pytest.raises(SequencerError, match="type 'socket' is not file or folder"):
        Library.load([folder])


def test_roles_and_components(bench):
    """A role lists commands, the components implement them: a component, or a host, missing one of its role's
    commands does not load; a host has the role of its component, or picks one of its roles; tsq_type and
    tsq_types, and a datapack type's extends or commands, are refused with what to write instead."""
    from types import SimpleNamespace

    from testsuit.datatools.datapack.datapack_tools import DatapackError
    from testsuit.sequencer.intake import check_against_lab
    from testsuit.sequencer.lab import load_lab
    from testsuit.sequencer.model import needs_from_setup
    from testsuit.sequencer.util import SequencerError

    lab = load_lab(Path(bench.config.lab.folder), bench.config.inventory_file, require_tag=False)
    resolved = lab.resolve(lab.inventory.components["counter_1"])
    assert sorted(resolved.commands) == ["collect", "deploy", "kill", "ready", "start", "stop", "version"]
    assert resolved.chain == ["component", "linux_system", "counter_host", "counter_1"]
    assert sorted(lab.library.role_commands("dut")) == ["collect", "deploy", "kill", "ready", "start", "stop",
                                                        "version"]
    lab_folder = Path(bench.config.lab.folder)
    components = copy.deepcopy(LAB_COMPONENTS)
    del components["components"]["counter_host"]["commands"]["ready"]
    components["components"]["counter_host"]["commands"]["reset"] = {"run": "true"}
    write(lab_folder / "components" / "bench.yml", yaml.safe_dump(components))
    with pytest.raises(SequencerError, match="component 'counter_host' .* does not implement ready, listed by its role 'dut'"):
        load_lab(lab_folder, bench.config.inventory_file, require_tag=False)
    lab = load_lab(lab_folder, bench.config.inventory_file, require_tag=False, strict=False)
    assert lab.extra_commands["counter_1"] == ["reset"]
    del components["components"]["counter_host"]["roles"]          # a base: its hosts give their role
    write(lab_folder / "components" / "bench.yml", yaml.safe_dump(components))
    with pytest.raises(SequencerError, match="'counter_1': no role: give tsq_role"):
        load_lab(lab_folder, bench.config.inventory_file, require_tag=False)
    inventory = yaml.safe_load(bench.config.inventory_file.read_text())
    for i in (1, 2):
        inventory["all"]["children"][f"bench_{i}"]["hosts"][f"counter_{i}"]["tsq_role"] = "dut"
    write(bench.config.inventory_file, yaml.safe_dump(inventory))
    with pytest.raises(SequencerError, match="'counter_1' does not implement ready, listed by its role 'dut'"):
        load_lab(lab_folder, bench.config.inventory_file, require_tag=False)

    # a component supporting several roles: its hosts pick one
    roles = copy.deepcopy(LAB_ROLES)
    roles["roles"]["spare"] = {"extends": "linux_system", "commands": {"start": "Starts it."}}
    write(lab_folder / "roles" / "bench.yml", yaml.safe_dump(roles))
    components = copy.deepcopy(LAB_COMPONENTS)
    components["components"]["counter_host"]["roles"] = ["dut", "spare"]
    write(lab_folder / "components" / "bench.yml", yaml.safe_dump(components))
    del inventory["all"]["children"]["bench_2"]["hosts"]["counter_2"]["tsq_role"]
    write(bench.config.inventory_file, yaml.safe_dump(inventory))
    with pytest.raises(SequencerError, match=r"'counter_2': its component 'counter_host' supports several roles "
                                             r"\(dut, spare\): pick one with tsq_role"):
        load_lab(lab_folder, bench.config.inventory_file, require_tag=False)
    inventory["all"]["children"]["bench_2"]["hosts"]["counter_2"]["tsq_role"] = "spare"
    write(bench.config.inventory_file, yaml.safe_dump(inventory))
    lab = load_lab(lab_folder, bench.config.inventory_file, require_tag=False)
    assert (lab.inventory.components["counter_1"].role, lab.inventory.components["counter_2"].role) == ("dut", "spare")
    inventory["all"]["children"]["bench_2"]["hosts"]["counter_2"]["tsq_role"] = "sequencer"
    write(bench.config.inventory_file, yaml.safe_dump(inventory))
    with pytest.raises(SequencerError, match="role 'sequencer' \\(tsq_role\\) is not one its component 'counter_host' "
                                             "supports: dut, spare"):
        load_lab(lab_folder, bench.config.inventory_file, require_tag=False)
    del inventory["all"]["children"]["bench_2"]["hosts"]["counter_2"]["tsq_role"]
    write(bench.config.inventory_file, yaml.safe_dump(inventory))
    components["components"]["counter_host"]["roles"] = ["dut", "nope"]
    write(lab_folder / "components" / "bench.yml", yaml.safe_dump(components))
    with pytest.raises(SequencerError, match="component 'counter_host' .*: unknown role 'nope'"):
        load_lab(lab_folder, bench.config.inventory_file, require_tag=False)
    write(lab_folder / "components" / "bench.yml", yaml.safe_dump(LAB_COMPONENTS))
    roles = copy.deepcopy(LAB_ROLES)
    roles["roles"]["dut"]["commands"]["start"] = {"run": "x"}
    write(lab_folder / "roles" / "bench.yml", yaml.safe_dump(roles))
    with pytest.raises(SequencerError, match="implementations go in the lab components"):
        load_lab(lab_folder, bench.config.inventory_file, require_tag=False)
    write(lab_folder / "roles" / "bench.yml", yaml.safe_dump(LAB_ROLES))

    inventory["all"]["vars"]["tsq_types"] = ["types/"]
    write(bench.config.inventory_file, yaml.safe_dump(inventory))
    with pytest.raises(SequencerError, match="'tsq_types' is gone"):
        load_lab(lab_folder, bench.config.inventory_file, require_tag=False)
    del inventory["all"]["vars"]["tsq_types"]
    inventory["all"]["children"]["bench_1"]["hosts"]["counter_1"]["tsq_type"] = "counter_host"
    write(bench.config.inventory_file, yaml.safe_dump(inventory))
    with pytest.raises(SequencerError, match="'tsq_type' is gone"):
        load_inventory(bench.config.inventory_file)
    del inventory["all"]["children"]["bench_1"]["hosts"]["counter_1"]["tsq_type"]
    inventory["all"]["children"]["bench_1"]["hosts"]["counter_1"]["tsq_role"] = "nope"
    write(bench.config.inventory_file, yaml.safe_dump(inventory))
    with pytest.raises(SequencerError, match="role 'nope' \\(tsq_role\\) is not one its component"):
        load_lab(Path(bench.config.lab.folder), bench.config.inventory_file, require_tag=False)

    setup = copy.deepcopy(SETUP)
    setup["configurations"]["bench"]["components"]["dut"]["role"] = "counter"
    needs = needs_from_setup(setup, "bench")
    assert "component 'dut': role 'counter' is not a role of the lab" in check_against_lab(SimpleNamespace(needs=needs), lab)
    setup["component_types"]["dut_app"]["extends"] = "counter_host"
    with pytest.raises(SequencerError, match="'extends' is gone"):
        needs_from_setup(setup, "bench")
    write(bench.dp / "setup" / "bench_setup.yml", yaml.safe_dump(setup))
    with pytest.raises(DatapackError, match="'extends' is gone"):
        bench.build()


def test_run_succeeded(bench):
    async def main():
        seq = bench.sequencer()
        run = seq.submit(bench.build())
        assert run["state"] == "queued" and run["conform"] is False   # built with nocheck
        [run] = await run_until_done(seq, [run["id"]])
        await seq.close()
        return run
    run = asyncio.run(main())
    assert (run["verdict"], run["reason"]) == ("succeeded", "")
    assert EXIT_CODES[run["verdict"]] == 0
    assert run["results"].endswith("_results_NONCONFORM.zip")
    assert verify_datapack(Path(run["results"])) == []
    record = yaml.safe_load(read_member(run, "run.yml"))
    assert record["phases"] == {"test_run": "succeeded", "exploit": "succeeded"}
    assert record["binding"]["stages"]["bench"] in ("bench_1", "bench_2")
    assert int(read_member(run, "data/dut/output/count.txt")) >= 3
    assert read_member(run, "data/dut/output/app.log").splitlines() == ["READY count", "stopped"]
    assert read_member(run, "data/analysis/output/verdict.txt").startswith("count ")
    assert "data/analysis/input/data/count.txt" in results_of(run)[0]
    assert "logs/test_run/start[dut].log" in results_of(run)[0]
    baseline = yaml.safe_load(read_member(run, "baseline.yml"))
    concrete = record["binding"]["components"]["dut"]
    assert baseline["run"]["components"][concrete]["version"] == "counter-1.2.0"
    selftest = yaml.safe_load(read_member(run, "selftest.yml"))
    assert all(r["ok"] for r in selftest["attempts"])
    seq_store = Store(bench.config.storage_path("database"))
    assert seq_store.locks() == []                                   # everything released


def test_failed_verdict(bench):
    async def main():
        seq = bench.sequencer()
        run = seq.submit(bench.build("too_short", min_count=100000))
        [run] = await run_until_done(seq, [run["id"]])
        await seq.close()
        return run
    run = asyncio.run(main())
    assert run["verdict"] == "failed" and EXIT_CODES["failed"] == 1
    record = yaml.safe_load(read_member(run, "run.yml"))
    assert record["phases"] == {"test_run": "succeeded", "exploit": "failed"}
    assert {s["id"]: s["state"] for s in record["steps"] if s["phase"] == "exploit"} == \
        {"deploy": "succeeded", "analyse": "failed", "collect": "succeeded"}


# A python step's script, deployed with the analysis inputs: the count against min_count, from the keys.
CHECK_PY = """import json, sys
count = int(open(sys.argv[1]).read())
with open(sys.argv[2] + "/py_verdict.json", "w") as f:
    json.dump({"count": count, "min": keys["min_count"], "run": keys["run"]["id"]}, f)
sys.exit(0 if count >= keys["min_count"] else 1)
"""


def python_bench(bench: Bench, exploit_steps: list) -> dict:
    """The bench pipeline whose exploit phase runs the given steps, check.py among the analysis inputs."""
    setup = copy.deepcopy(SETUP)
    setup["component_types"]["analysis"]["configurations"]["standard"]["inputs"].append(
        {"source": "files/check.py", "target": "check.py", "method": "copy"})
    write(bench.dp / "setup" / "bench_setup.yml", yaml.safe_dump(setup))
    write(bench.dp / "setup" / "files" / "check.py", CHECK_PY)
    pipeline = copy.deepcopy(PIPELINE)
    pipeline["phases"][1]["steps"] = [{"id": "deploy", "component": "analysis", "do": "deploy"}, *exploit_steps]
    return pipeline


def run_one(bench: Bench, zip_path: Path) -> dict:
    async def main():
        seq = bench.sequencer()
        run = seq.submit(zip_path)
        [run] = await run_until_done(seq, [run["id"]])
        await seq.close()
        return run
    return asyncio.run(main())


def test_python_steps(bench):
    """SQ-32: a script deployed on a component, and inline code on the sequencer host, with the run's keys."""
    pipeline = python_bench(bench, [
        {"id": "check", "component": "analysis", "needs": ["deploy"], "python": {
            "script": "{{ component.input_dir }}/check.py",
            "args": ["{{ component.input_dir }}/data/count.txt", "{{ component.output_dir }}"]}},
        {"id": "inline", "needs": ["check"], "python": {"code": (
            "import os, sys\n"
            "print('run', keys['run']['id'], 'in', os.getcwd())\n"
            "sys.exit(0 if os.path.isfile('data/dut/output/count.txt') else 1)\n")}}])
    run = run_one(bench, bench.build("py", pipeline=pipeline))
    assert (run["verdict"], run["reason"]) == ("succeeded", "")
    verdict = yaml.safe_load(read_member(run, "data/analysis/output/py_verdict.json"))
    assert verdict["count"] >= 3 and verdict["min"] == 3 and verdict["run"] == run["id"]
    assert f"run {run['id']} in" in read_member(run, "logs/exploit/inline.log")
    assert not any(".tsq" in n for n in results_of(run)[0])       # keys and wrapper stay out of the results


def test_python_step_results(bench):
    """A script's exit status is judged like a shell command's; an uncaught exception is an error."""
    pipeline = python_bench(bench, [
        {"id": "low", "component": "analysis", "needs": ["deploy"], "continue_on_error": True, "python": {
            "script": "{{ component.input_dir }}/check.py",
            "args": ["{{ component.input_dir }}/data/count.txt", "{{ component.output_dir }}"]}},
        {"id": "expected", "needs": ["deploy"], "expect": {"exit": [3]}, "python": {"code": "raise SystemExit(3)"}},
        {"id": "crash", "needs": ["low"], "python": {"code": "1 / 0"}}])
    run = run_one(bench, bench.build("py_bad", pipeline=pipeline, min_count=100000))
    record = yaml.safe_load(read_member(run, "run.yml"))
    states = {s["id"]: (s["state"], s["exit_status"]) for s in record["steps"] if s["phase"] == "exploit"}
    assert states["low"] == ("failed", 1)
    assert states["expected"] == ("succeeded", 3)
    assert states["crash"] == ("error", 70)
    assert run["verdict"] == "error"
    assert "ZeroDivisionError" in read_member(run, "logs/exploit/crash.log")


def test_python_step_checked():
    from testsuit.sequencer.library import ComponentDef
    from testsuit.sequencer.model import Step
    from testsuit.sequencer.util import SequencerError
    Step(id="ok", python={"code": "pass"}).check()
    with pytest.raises(SequencerError, match="exactly one of 'script' and 'code'"):
        Step(id="both", python={"script": "a.py", "code": "x"}).check()
    with pytest.raises(SequencerError, match="unknown 'python' key"):
        Step(id="typo", python={"scrpit": "a.py"}).check()
    with pytest.raises(SequencerError, match="command 'start'"):
        ComponentDef.from_dict("t", {"extends": "linux_system", "commands": {"start": {"python": {}}}},
                               "components.yml")


def test_queue_and_parallel_stages(tmp_path, no_git):
    """Three runs on two stages: two run at once, the third waits for a stage."""
    bench = Bench(tmp_path, stages=2)

    async def main():
        seq = bench.sequencer()
        zip_path = bench.build(duration="1s")
        runs = [seq.submit(zip_path, priority=p)["id"] for p in (10, 50, 90)]
        seen_waiting = []

        async def watch(seq):
            for _ in range(100):
                states = {r: seq.store.get_run(r)["state"] for r in runs}
                if "waiting_resources" in states.values():
                    seen_waiting.append(states)
                    return
                await asyncio.sleep(0.05)
        done = await run_until_done(seq, runs, during=watch)
        await seq.close()
        return done, seen_waiting
    done, seen_waiting = asyncio.run(main())
    assert [r["verdict"] for r in done] == ["succeeded"] * 3
    # the lowest priority waited; the two others ran at the same time, on both stages
    assert seen_waiting and seen_waiting[0][done[0]["id"]] == "waiting_resources"
    assert {r["binding"]["stages"]["bench"] for r in done[1:]} == {"bench_1", "bench_2"}
    # it started once a stage was released: after the test_run phase of another run (exploit needs no stage)
    assert done[0]["activated_at"] > max(r["activated_at"] for r in done[1:])
    # the analysis host is not locked: each run had its own folders there, never clobbered by another run
    seq_host = tmp_path / "remote" / "seq_host"
    assert not (seq_host / "analysis").exists()
    assert all((seq_host / ".tsq" / r["id"] / "analysis" / "input" / "check.sh").is_file() for r in done)


def test_cancel_runs_finally(tmp_path, no_git):
    bench = Bench(tmp_path, stages=1)

    async def main():
        seq = bench.sequencer()
        run = seq.submit(bench.build(duration="30s"))

        async def cancel(seq):
            await running_step(seq, run["id"], "test_run:run")
            await seq.abort(run["id"], "cancel")
        [done] = await run_until_done(seq, [run["id"]], during=cancel)
        alive = await seq.transport_for("counter_1").alive_in(str(tmp_path / "remote" / "counter_1" / ".tsq"))
        await seq.close()
        return done, alive
    run, alive = asyncio.run(main())
    assert run["verdict"] == "error" and run["reason"] == "cancelled"
    record = yaml.safe_load(read_member(run, "run.yml"))
    states = {s["id"]: s["state"] for s in record["steps"] if s["phase"] == "test_run"}
    assert states["collect"] == "succeeded" and states["stop"] == "skipped"
    assert record["phases"]["exploit"] == "skipped"
    assert "stopped" in read_member(run, "data/dut/output/app.log")       # the stop command ran (graceful)
    assert alive == []


def test_cancel_queued_and_priority(bench):
    async def main():
        seq = bench.sequencer()
        run = seq.submit(bench.build())
        seq.set_priority(run["id"], 80)
        assert seq.store.get_run(run["id"])["priority"] == 80
        done = await seq.abort(run["id"], "cancel")
        await seq.close()
        return done
    run = asyncio.run(main())
    assert run["state"] == "cancelled" and run["verdict"] == "error"


def test_selftest_failure(tmp_path, no_git):
    """A broken component is marked broken, the run requeued, then ends in error after max_attempts."""
    bench = Bench(tmp_path, stages=1)
    types = yaml.safe_load((tmp_path / "lab" / "components" / "bench.yml").read_text())
    types["components"]["counter_host"]["selftest"] = [{"id": "probe", "run": "test -e /nonexistent/x"}]
    write(tmp_path / "lab" / "components" / "bench.yml", yaml.safe_dump(types))

    async def main():
        seq = bench.sequencer()
        run = seq.submit(bench.build())
        seq.schedule()                                   # first attempt: self-test fails, requeued
        while run["id"] in seq.executions:
            await asyncio.sleep(0.05)
        after_first = seq.store.get_run(run["id"])
        state = seq.monitor.state("counter_1")
        seq.store.set_state("counter_1", "idle")         # an operator repaired it (it is still broken)
        [done] = await run_until_done(seq, [run["id"]])
        await seq.close()
        return after_first, state, done
    first, state, done = asyncio.run(main())
    assert first["state"] == "queued" and first["attempts"] == 1 and state == "broken"
    assert done["verdict"] == "error" and "self-test failed 2 time(s)" in done["reason"]


def test_failed_selftest_keeps_broken_until_one_passes(tmp_path, no_git):
    """A failed self-test leaves the component broken while its probe says idle; becoming idle (or being
    probed idle while kept broken) runs a self-test, and the one that passes brings it back."""
    bench = Bench(tmp_path, stages=1)
    flag = tmp_path / "healthy"
    types = yaml.safe_load((tmp_path / "lab" / "components" / "bench.yml").read_text())
    types["components"]["counter_host"]["selftest"] = [{"id": "healthy", "run": f"test -e {flag}"}]
    write(tmp_path / "lab" / "components" / "bench.yml", yaml.safe_dump(types))

    async def until(seq, state):
        for _ in range(200):
            if seq.monitor.state("counter_1") == state and "counter_1" not in seq.monitor._testing:
                return True
            await asyncio.sleep(0.05)
        return False

    async def main():
        seq = bench.sequencer()
        seq.monitor.idle_retest = 0
        [result] = await seq.selftest("counter_1")
        seen = [result["ok"], seq.monitor.state("counter_1")]
        seen.append(await seq.monitor.probe("counter_1"))          # probed idle, kept broken (and tested again: fails)
        seen.append(await until(seq, "broken"))
        detail = seq.store.resources("component")[("component", "counter_1")]["detail"]
        flag.write_text("")                                         # repaired: the next test passes
        await seq.monitor.probe("counter_1")
        seen.append(await until(seq, "idle"))
        flag.unlink()                                               # broken again, then idle: tested, broken
        seq.store.set_state("counter_1", "offline")
        await seq.monitor.probe("counter_1")
        seen.append(await until(seq, "broken"))
        await seq.close()
        return seen, detail
    seen, detail = asyncio.run(main())
    assert seen == [False, "broken", "broken", True, True, True], seen
    assert detail.startswith("self-test failed") and "healthy" in detail, detail


def test_probe_report(tmp_path, no_git):
    """Each probe leaves a report for the Lab page: what was tested, the answer, the rule that gave the state
    (or that none did), and why; a component kept broken says so, with its failed self-test checks."""
    bench = Bench(tmp_path, stages=1)
    answer = tmp_path / "answer"
    types = yaml.safe_load((tmp_path / "lab" / "components" / "bench.yml").read_text())
    types["components"]["counter_host"]["status"] = {
        "probe": {"run": f"cat {answer}"},
        "states": {"running": {"regex": "^RUN"}, "idle": {"regex": "^IDLE"}, "offline": {"exit": "nonzero"}}}
    types["components"]["counter_host"]["selftest"] = [{"id": "healthy", "run": f"grep -q OK {answer}"}]
    write(tmp_path / "lab" / "components" / "bench.yml", yaml.safe_dump(types))

    async def main():
        seq = bench.sequencer()
        seq.monitor.on_idle = None                                  # no background self-test
        reports = []
        for text in ("RUNNING\n", "WHAT\n", None):
            if text is None:
                answer.unlink()
            else:
                answer.write_text(text)
            await seq.monitor.probe("counter_1")
            reports.append(seq.monitor.reports["counter_1"])
        answer.write_text("IDLE\n")
        [result] = await seq.selftest("counter_1")                  # fails: no OK in the answer
        await seq.monitor.probe("counter_1")
        reports.append(seq.monitor.reports["counter_1"])
        await seq.close()
        return reports, result
    (running, unknown, offline, kept), result = asyncio.run(main())
    step = running["steps"][-1]
    assert step["command"] == f"cat {answer}" and step["output"] == "RUNNING\n" and step["exit"] == 0 and step["ok"]
    assert offline["steps"][-1]["ok"] is False                     # a single check is OK / FAILED too
    assert running["state"] == "running" and running["matched"] == "running" and "rule of state 'running'" in running["why"]
    assert sorted(running["rules"]) == ["idle", "offline", "running"]
    assert unknown["state"] == "unknown" and "matched" not in unknown and "matches none of the rules" in unknown["why"]
    assert offline["state"] == "offline" and offline["steps"][-1]["exit"] != 0 and "rule of state 'offline'" in offline["why"]
    healthy = next(c for c in result["checks"] if c["id"] == "healthy")
    assert not result["ok"] and not healthy["ok"] and healthy["spec"]["run"] == f"grep -q OK {answer}"
    assert kept["probed"] == "idle" and kept["state"] == "broken" and "stays broken" in kept["why"]
    assert [c["id"] for c in kept["kept_broken"]["checks"] if not c["ok"]] == ["healthy"]


def test_probe_via(tmp_path, no_git):
    """A component in a sub-network is probed through another component of its stage (status.via: its role,
    or a host): the commands run there, never on the component itself; its self-test's run checks too."""
    bench = Bench(tmp_path, stages=1)
    psu = tmp_path / "psu"
    inventory = yaml.safe_load((tmp_path / "lab" / "inventory.yml").read_text())
    inventory["all"]["children"]["bench_1"]["hosts"]["obc_1"] = {
        "tsq_extends": "obc", "tsq_transport": "ssh", "ansible_host": "192.0.2.7", "tsq_lock": "none"}
    write(tmp_path / "lab" / "inventory.yml", yaml.safe_dump(inventory))
    roles = yaml.safe_load((tmp_path / "lab" / "roles" / "bench.yml").read_text())
    roles["roles"]["obc"] = {"extends": "component"}
    write(tmp_path / "lab" / "roles" / "bench.yml", yaml.safe_dump(roles))
    types = yaml.safe_load((tmp_path / "lab" / "components" / "bench.yml").read_text())
    types["components"]["obc"] = {
        "extends": "component", "roles": ["obc"],
        "status": {"via": "dut", "probe": {"run": f"cat {psu}; echo from {{{{ via.id }}}} to {{{{ component.host }}}}; "
                                           "echo {{ stage.roles.dut.id }} {{ lab.components.seq_host.vars.tsq_role }}"},
                   "states": {"running": {"regex": "^ON"}, "idle": {"regex": "^OFF"}}},
        "selftest": [{"id": "psu", "run": f"test -f {psu}"}]}
    write(tmp_path / "lab" / "components" / "bench.yml", yaml.safe_dump(types))

    async def main():
        seq = bench.sequencer()
        seq.monitor.on_idle = None
        reports = []
        for text in ("OFF\n", "ON\n"):
            psu.write_text(text)
            await seq.monitor.probe("obc_1")
            reports.append(seq.monitor.reports["obc_1"])
        [result] = await seq.selftest("obc_1")
        await seq.close()
        return reports, result
    (off, on), result = asyncio.run(main())
    assert off["state"] == "idle" and on["state"] == "running"
    assert on["via"]["id"] == "counter_1" and on["steps"][-1]["output"] == "ON\nfrom counter_1 to 192.0.2.7\ncounter_1 sequencer\n"
    assert "counter_1, for obc_1" in on["steps"][-1]["action"]
    psu_check = next(c for c in result["checks"] if c["id"] == "psu")
    assert result["ok"] and psu_check["ok"] and psu_check["command"] == f"test -f {psu}"

    from testsuit.sequencer.engine import RunContext
    lab = bench.sequencer().lab
    keys = RunContext(run_id="r", bag=tmp_path, datapack=type("D", (), {"context": {}})(), bound={},
                      stage_binding={}, lab=lab).keys("main")       # run steps get them too
    assert keys["stage"]["id"] == "" and keys["lab"]["components"]["obc_1"]["host"] == "192.0.2.7"
    assert lab.lab_keys(lab.inventory.components["obc_1"], lab.inventory.stages["bench_1"])["stage"]["roles"]["dut"]["id"] \
        == "counter_1"

    # several checks: each runs whatever the others gave, and the rules see all their outputs
    types["components"]["obc"]["status"]["probe"] = {"run": [f"cat {psu}", "echo DOWN; exit 3", "echo UP"]}
    types["components"]["obc"]["status"]["states"] = {"broken": {"regex": "^ON[\\s\\S]*DOWN"}, "idle": {"regex": "^OFF"}}
    write(tmp_path / "lab" / "components" / "bench.yml", yaml.safe_dump(types))

    async def checks():
        seq = bench.sequencer()
        seq.monitor.on_idle = None
        await seq.monitor.probe("obc_1")
        await seq.close()
        return seq.monitor.reports["obc_1"]
    report = asyncio.run(checks())
    ran = [s for s in report["steps"] if "ok" in s]
    assert [s["ok"] for s in ran] == [True, False, True] and ran[1]["exit"] == 3
    assert report["state"] == "broken" and report["detail"] == "1 of 3 checks failed" and "2 OK, 1 failed" in report["why"]

    types["components"]["obc"]["status"]["via"] = "nobody"
    write(tmp_path / "lab" / "components" / "bench.yml", yaml.safe_dump(types))
    from testsuit.sequencer.util import SequencerError
    with pytest.raises(SequencerError, match="goes via 'nobody'"):
        bench.sequencer()


def test_commands_via(tmp_path, no_git):
    """A command with 'via' runs on another component (a role of the stage, or a host): its process and pid
    file are there, and stop (a signal) and the end of the run reach it there."""
    bench = Bench(tmp_path, stages=1)
    inventory = yaml.safe_load((tmp_path / "lab" / "inventory.yml").read_text())
    start = dict(LAB_COMPONENTS["components"]["counter_host"]["commands"]["start"], via="seq_host")
    inventory["all"]["children"]["bench_1"]["hosts"]["counter_1"]["tsq_commands"] = {"start": start}
    write(tmp_path / "lab" / "inventory.yml", yaml.safe_dump(inventory))

    async def main():
        seq = bench.sequencer()
        run = seq.submit(bench.build())
        [done] = await run_until_done(seq, [run["id"]])
        await seq.close()
        return done
    done = asyncio.run(main())
    assert done["verdict"] == "succeeded", done["reason"]
    logs = "\n".join(p.read_text() for p in (tmp_path / "storage").rglob("*.log") if "start" in p.name)
    assert "through seq_host" in logs
    assert not list((tmp_path / "remote" / "seq_host" / ".tsq").glob(f"{done['id']}/counter_1/*.pid"))

    start["via"] = "nobody"
    write(tmp_path / "lab" / "inventory.yml", yaml.safe_dump(inventory))
    from testsuit.sequencer.util import SequencerError
    with pytest.raises(SequencerError, match="command 'start' goes via 'nobody'"):
        bench.sequencer()


def test_manual_command(tmp_path, no_git):
    """In maintenance, a component's run and signal commands can be run by hand; not otherwise."""
    from testsuit.sequencer.util import SequencerError
    bench = Bench(tmp_path, stages=1)
    types = yaml.safe_load((tmp_path / "lab" / "components" / "bench.yml").read_text())
    types["components"]["counter_host"]["commands"]["start"] = {"run": "sleep 30", "detach": True}
    types["components"]["counter_host"]["commands"]["check"] = {"run": "echo host={{ component.host }}; exit 3"}
    write(tmp_path / "lab" / "components" / "bench.yml", yaml.safe_dump(types))

    async def main():
        seq = bench.sequencer()
        seq.monitor.on_idle = None
        out = {}
        try:
            await seq.manual_command("counter_1", "version")
        except SequencerError as e:
            out["refused"] = str(e)
        seq.set_flag("counter_1", "maintenance", True)
        listed = await seq.manual_commands("counter_1")
        out["manual"] = {c["name"]: c["manual"] for c in listed["commands"]}
        out["segments"] = next(c["segments"] for c in listed["commands"] if c["name"] == "check")
        out["version"] = await seq.manual_command("counter_1", "version")
        out["check"] = await seq.manual_command("counter_1", "check")
        out["start"] = await seq.manual_command("counter_1", "start")
        out["stop"] = await seq.manual_command("counter_1", "stop")
        try:
            await seq.manual_command("counter_1", "ready")
        except SequencerError as e:
            out["ready"] = str(e)
        await seq.close()
        return out
    out = asyncio.run(main())
    assert "not in maintenance" in out["refused"]
    assert out["manual"]["version"] and out["manual"]["stop"] and not out["manual"]["ready"]
    assert out["version"]["ok"] and "counter-1.2.0" in out["version"]["output"]
    assert out["check"]["ok"] is False and out["check"]["exit"] == 3 and "host=" in out["check"]["output"]
    assert out["start"]["ok"] and out["start"]["detached"]
    assert out["stop"]["ok"] and "start.pid" in out["stop"]["output"]
    assert "can be run by hand" in out["ready"]
    assert [seg["key"] for seg in out["segments"]] == [None, "component.host", None]
    assert out["segments"][0]["text"] == "echo host=" and out["segments"][1]["text"] == "counter_1"


def test_maintenance_blocks(bench):
    async def main():
        seq = bench.sequencer()
        for stage in ("bench_1", "bench_2"):
            seq.set_flag(stage, "maintenance", True)
        run = seq.submit(bench.build())
        seq.schedule()
        waiting = seq.store.get_run(run["id"])
        seq.set_flag("bench_2", "maintenance", False)
        [done] = await run_until_done(seq, [run["id"]])
        await seq.close()
        return waiting, done
    waiting, done = asyncio.run(main())
    assert waiting["state"] == "waiting_resources" and "maintenance" in waiting["waiting_for"]
    assert done["verdict"] == "succeeded" and done["binding"]["stages"]["bench"] == "bench_2"


def test_resource_overview(bench):
    """Each logical need with the lab's candidates and why they are not available; the bound ones once running."""
    from testsuit.sequencer.intake import load_datapack
    from testsuit.sequencer.scheduler import Binding, resource_overview

    async def main():
        seq = bench.sequencer()
        seq.set_flag("bench_1", "maintenance", True)
        seq.set_flag("bench_2", "maintenance", True)
        run = seq.submit(bench.build())
        seq.schedule()
        dp = load_datapack(Path(run["dir"]))
        waiting = resource_overview(seq.lab, dp.needs, dp.pipeline.phases, seq.availability(), run["id"])
        seq.set_flag("bench_2", "maintenance", False)
        [done] = await run_until_done(seq, [run["id"]])
        binding = Binding(**done["binding"])
        bound = resource_overview(seq.lab, dp.needs, dp.pipeline.phases, seq.availability(), run["id"], binding)
        await seq.close()
        return waiting, bound
    waiting, bound = asyncio.run(main())
    [stage] = waiting["stages"]
    assert stage["logical"] == "bench" and stage["bound"] is None and stage["phases"]
    assert {c["id"]: c["why"] for c in stage["candidates"]} == {
        "bench_1": "bench_1 in maintenance or held", "bench_2": "bench_2 in maintenance or held"}
    assert all(c["maintenance"] and c["roles"] for c in stage["candidates"])
    [stage] = bound["stages"]
    assert stage["bound"] == "bench_2"
    assert {c["id"]: (c["bound"], c["why"]) for c in stage["candidates"]} == {
        "bench_1": (False, "bench_1 in maintenance or held"), "bench_2": (True, None)}


def test_rejected_datapack(bench):
    pipeline = {"phases": [{"id": "p", "stages": ["bench"], "steps": [{"id": "x", "run": "true", "needs": ["nope"]}]}]}
    seq = bench.sequencer()
    with pytest.raises(Exception, match="nope"):
        seq.submit(bench.build("bad", pipeline=pipeline))
    assert seq.store.runs() == []


def test_restart_recovery(tmp_path, no_git):
    """A run found running at startup is killed, its finally run, its results packed, verdict error."""
    bench = Bench(tmp_path, stages=1)

    async def first():
        seq = bench.sequencer()
        run = seq.submit(bench.build(duration="30s"))
        seq.schedule()
        await running_step(seq, run["id"], "test_run:run")
        # the daemon dies: its tasks vanish without cleaning (no finish)
        task = seq.executions[run["id"]].task
        seq.executions[run["id"]].finish = lambda verdict: asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return run["id"]

    async def second(run_id):
        seq = bench.sequencer()
        assert seq.store.get_run(run_id)["state"] == "running"
        await seq.recover()
        alive = await seq.transport_for("counter_1").alive_in(str(tmp_path / "remote" / "counter_1" / ".tsq"))
        run = seq.store.get_run(run_id)
        await seq.close()
        return run, alive
    run_id = asyncio.run(first())
    run, alive = asyncio.run(second(run_id))
    assert run["state"] == "done" and run["verdict"] == "error" and "restarted" in run["reason"]
    assert alive == []
    assert "data/dut/output/count.txt" in results_of(run)[0]          # the finally collect ran


def test_local_and_daemon_share_locks(tmp_path, no_git):
    """tsq run --local and the daemon use the same store: a stage held by one is not given to the other."""
    bench = Bench(tmp_path, stages=1)
    daemon = Sequencer(bench.config)
    local = Sequencer(bench.config, local=True)
    zip_path = bench.build()
    held = daemon.submit(zip_path)
    daemon.store.try_lock(held["id"], [("stage", "bench_1", "exclusive")])
    mine = local.submit(zip_path)
    assert local.schedule() == []
    assert "held by run" in local.store.get_run(mine["id"])["waiting_for"]
    daemon.store.release(held["id"])


def test_kill(tmp_path, no_git):
    """kill: no stop command, the processes are killed at once, finally still collects."""
    bench = Bench(tmp_path, stages=1)

    async def main():
        seq = bench.sequencer()
        run = seq.submit(bench.build(duration="30s"))

        async def kill(seq):
            await running_step(seq, run["id"], "test_run:run")
            await seq.abort(run["id"], "kill")
        [done] = await run_until_done(seq, [run["id"]], during=kill)
        await seq.close()
        return done
    run = asyncio.run(main())
    assert run["verdict"] == "error" and run["reason"] == "killed"
    assert "stopped" not in read_member(run, "data/dut/output/app.log")
    record = yaml.safe_load(read_member(run, "run.yml"))
    assert {s["id"]: s["state"] for s in record["steps"]}["tsq_kill"] == "succeeded"


# ── daemon, REST API and tsq ─────────────────────────────────────────────────

@pytest.fixture
def daemon(tmp_path, no_git):
    """tsqd on a free port, in a thread, for the bench."""
    import socket
    import threading


    from testsuit.sequencer.daemon import build, server as tsqd_server

    bench = Bench(tmp_path, stages=1)
    config = bench.config.model_dump(exclude={"file", "given"})
    config_file = write(tmp_path / "tsq.yml", yaml.safe_dump(config))
    seq, app = build(str(config_file))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = tsqd_server(seq, app, "127.0.0.1", port, "warning")
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    import requests
    for _ in range(100):
        try:
            requests.get(f"http://127.0.0.1:{port}/health", timeout=1)
            break
        except requests.ConnectionError:
            time.sleep(0.1)
    yield bench, f"http://127.0.0.1:{port}", config_file, server, thread
    server.should_exit = True
    thread.join(30)


def test_local_process_closed_when_the_loop_stops(tmp_path, monkeypatch):
    """A command still running when its event loop stops has its transport closed with the loop,
    not later by the garbage collector (which failed with 'Event loop is closed')."""
    import types

    from testsuit.sequencer.transports.local import LocalTransport
    started = []
    spawn = asyncio.create_subprocess_shell

    async def tracked(*args, **kwargs):
        started.append(await spawn(*args, **kwargs))
        return started[-1]
    monkeypatch.setattr(asyncio, "create_subprocess_shell", tracked)

    async def main():
        transport = LocalTransport(types.SimpleNamespace(host="localhost", vars={}), str(tmp_path))
        await transport.start("sleep 30")
        await asyncio.sleep(0.1)
    asyncio.run(main())
    assert started and started[0]._transport.is_closing()


def test_daemon_api_and_cli(daemon, capsys):
    import requests

    from testsuit.cli.tsq import main as tsq
    bench, url, config_file, _, _ = daemon
    zip_path = bench.build()

    # submit --follow: steps and logs streamed, the verdict as exit code
    assert tsq(["--server", url, "submit", str(zip_path), "--priority", "70", "--follow"]) == 0
    out = capsys.readouterr().out
    assert "succeeded test_run:start[dut]" in out and "[test_run:ready[dut]]" in out
    run = requests.get(f"{url}/runs").json()[0]
    assert run["verdict"] == "succeeded" and run["priority"] == 70

    files = requests.get(f"{url}/runs/{run['id']}/files/data/dut/output").json()
    assert {f["name"] for f in files} == {"app.log", "count.txt"}
    log = requests.get(f"{url}/runs/{run['id']}/files/data/dut/output/app.log", headers={"Range": "bytes=0-4"})
    assert log.status_code == 206 and log.text == "READY"
    in_zip = requests.get(f"{url}/runs/{run['id']}/files/run.yml", params={"results": "true"})
    listed = requests.get(f"{url}/runs/{run['id']}/files/", params={"results": "true", "list": "true"}).json()
    assert "run.yml" in {f["name"] for f in listed}
    assert yaml.safe_load(in_zip.text)["verdict"] == "succeeded"
    assert requests.get(f"{url}/runs/{run['id']}/logs/test_run:start").text.startswith("[tsq ")

    resources = requests.get(f"{url}/runs/{run['id']}/resources").json()
    assert resources["stages"][0]["bound"] == "bench_1" and resources["problems"] == []

    assert tsq(["--server", url, "stages"]) == 0
    assert "bench_1" in capsys.readouterr().out
    assert tsq(["--server", url, "maintenance", "bench_1", "--note", "rewiring"]) == 0
    stage = requests.get(f"{url}/stages").json()[0]
    assert stage["maintenance"] and stage["note"] == "rewiring"
    assert tsq(["--server", url, "resume", "bench_1"]) == 0
    assert tsq(["--server", url, "selftest", "counter_1"]) == 0
    assert "ok     counter_1" in capsys.readouterr().out
    assert tsq(["--server", url, "show", run["id"][:12]]) == 0
    assert "test_run:collect[dut]" in capsys.readouterr().out
    assert tsq(["--server", url, "submit", str(zip_path), "--wait"]) == 0
    assert tsq(["--server", url, "lab", "reload"]) == 0
    assert tsq(["--server", url, "kill", "nope"]) == 2


def test_ui_title_and_logo(tmp_path, no_git):
    """server.title and server.logo reach the UI through /health; a logo file is served at /logo<ext>."""
    from fastapi import HTTPException

    from testsuit.sequencer.api import create_app
    bench = Bench(tmp_path, stages=1)
    seq = bench.sequencer()
    routes = {r.path: r.endpoint for r in create_app(seq).routes if hasattr(r, "endpoint")}
    health, logo = routes["/health"], routes["/logo{suffix}"]

    async def main():
        assert (await health())["title"] == "Test sequencer" and (await health())["logo"] is None
        write(tmp_path / "logo.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>")
        seq.config.file = tmp_path / "tsq.yml"
        seq.config.server.title = "Rail lab"
        seq.config.server.logo = "logo.svg"
        shown = await health()
        assert shown["title"] == "Rail lab" and shown["logo"] == "logo.svg"
        assert Path((await logo(".svg")).path) == tmp_path / "logo.svg"
        seq.config.server.logo = "https://example.org/rail.png"
        assert (await health())["logo"] == "https://example.org/rail.png"
        with pytest.raises(HTTPException):
            await logo(".png")
        await seq.close()
    asyncio.run(main())


def test_daemon_stops_with_an_event_stream_open(tmp_path, no_git):
    """Ctrl+C stops tsqd at once even while a browser follows its events (a response that never ends)."""
    import signal
    import subprocess
    import sys

    bench = Bench(tmp_path, stages=1)
    config_file = write(tmp_path / "tsq.yml", yaml.safe_dump(bench.config.model_dump(exclude={"file", "given"})))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-m", "testsuit.sequencer.daemon", "--config", str(config_file),
                             "--listen", f"127.0.0.1:{port}", "--log-level", "warning"])
    try:
        for _ in range(100):
            try:
                stream = socket.create_connection(("127.0.0.1", port), timeout=10)
                break
            except OSError:
                time.sleep(0.1)
        stream.sendall(b"GET /events HTTP/1.1\r\nHost: tsqd\r\n\r\n")
        received = b""
        while b": connected" not in received:          # the headers and the first chunk may come apart
            chunk = stream.recv(4096)
            assert chunk, received
            received += chunk
        proc.send_signal(signal.SIGINT)
        proc.wait(8)                                   # uvicorn exits by re-raising the SIGINT
        stream.close()
    finally:
        proc.kill()


def test_cli_without_daemon(tmp_path, no_git, capsys):
    from testsuit.cli.tsq import main as tsq
    bench = Bench(tmp_path, stages=1)
    config_file = write(tmp_path / "tsq.yml", yaml.safe_dump(bench.config.model_dump(exclude={"file", "given"})))
    zip_path = bench.build()
    assert tsq(["--config", str(config_file), "validate", str(zip_path)]) == 0
    out = capsys.readouterr().out
    assert "binding (locks ignored): bench=bench_1" in out and "valid" in out
    assert "start: do start on dut, needs deploy" in out
    assert tsq(["--config", str(config_file), "run", str(zip_path), "--local"]) == 0
    assert "succeeded" in capsys.readouterr().out
    assert tsq(["--config", str(config_file), "config"]) == 0
    assert "selftest.max_attempts" in capsys.readouterr().out
    assert tsq(["--config", str(config_file), "lab", "status"]) == 0
    assert tsq(["files", str(zip_path), "data/dut/input"]) == 0
    assert "data/dut/input/app.sh" in capsys.readouterr().out


# ── ssh transport, against an in-process AsyncSSH server running commands on this host ──

@pytest.fixture
def ssh_server(tmp_path):
    asyncssh = pytest.importorskip("asyncssh")
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    key_file = tmp_path / "id_ed25519"
    client_key.write_private_key(str(key_file))
    authorized = asyncssh.import_authorized_keys(client_key.export_public_key().decode())

    async def handle(process):
        # output forwarded until EOF before the exit status: no data lost when the command exits at once
        proc = await asyncio.create_subprocess_shell(process.command or "true", stdin=asyncio.subprocess.DEVNULL,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        while chunk := await proc.stdout.read(65536):
            process.stdout.write(chunk)
        process.exit(await proc.wait())

    async def start():
        return await asyncssh.create_server(asyncssh.SSHServer, "127.0.0.1", 0, server_host_keys=[host_key],
                                            authorized_client_keys=authorized, process_factory=handle,
                                            sftp_factory=True, encoding=None)
    return start, key_file


def test_ssh_login_failures_say_why(tmp_path):
    """A refused or silent port gives a reason (a timeout has no message of its own), and a component on
    localhost is told about the local transport."""
    pytest.importorskip("asyncssh")
    from testsuit.sequencer.inventory import Component
    from testsuit.sequencer.transports import create_transport

    async def main():
        with socket.socket() as s:                   # a free port: nothing listens
            s.bind(("127.0.0.1", 0))
            closed = s.getsockname()[1]
        held = []

        async def never_answer(reader, writer):
            held.append(writer)
            await asyncio.sleep(30)
        silent = await asyncio.start_server(never_answer, "127.0.0.1", 0)   # accepts, never answers
        quiet = silent.sockets[0].getsockname()[1]
        errors = []
        for port in (closed, quiet):
            comp = Component("box", {"ansible_host": "127.0.0.1", "ansible_port": port,
                                     "tsq_ssh_options": {"known_hosts": None, "connect_timeout": 1}})
            result = await create_transport("ssh", comp, str(tmp_path)).shell("true")
            errors.append(result.error)
        for writer in held:
            writer.close()
        silent.close()
        return errors
    refused, timeout = asyncio.run(main())
    assert "connection refused on port" in refused and "tsq_transport: local" in refused, refused
    assert "no answer on port" in timeout and "within 1 s" in timeout, timeout


def test_ssh_transport(tmp_path, ssh_server):
    from testsuit.sequencer.inventory import Component
    from testsuit.sequencer.transports import create_transport
    start, key_file = ssh_server

    async def main():
        server = await start()
        port = server.sockets[0].getsockname()[1]
        comp = Component("box", {"ansible_host": "127.0.0.1", "ansible_port": port, "ansible_user": "tsq",
                                 "ansible_ssh_private_key_file": str(key_file),
                                 "tsq_ssh_options": {"known_hosts": None}})
        transport = create_transport("ssh", comp, str(tmp_path / "remote"))
        try:
            root = await transport.resolve_root()
            result = await transport.run("echo hello; exit 3")
            assert (result.exit_status, result.output) == (3, "hello\n")

            (tmp_path / "local" / "sub").mkdir(parents=True)
            (tmp_path / "local" / "sub" / "a.txt").write_text("A")
            await transport.put(tmp_path / "local", f"{root}/box/input")
            assert (tmp_path / "remote" / "box" / "input" / "sub" / "a.txt").read_text() == "A"
            size = await transport.get(f"{root}/box/input", tmp_path / "back")
            assert size == 1 and (tmp_path / "back" / "sub" / "a.txt").read_text() == "A"

            # a detached process tree, then a timeout: the whole group is killed through its pid file
            pid_dir = f"{root}/.tsq/r1/box"
            process = await transport.start("sleep 30 & sleep 30", pidfile=f"{pid_dir}/s.pid")
            await asyncio.sleep(0.5)
            assert len(await transport.alive_in(pid_dir)) == 1
            await transport.signal_dir(pid_dir, "KILL")
            await asyncio.wait_for(process.wait(), 10)
            for _ in range(20):
                if not await transport.alive_in(pid_dir):
                    break
                await asyncio.sleep(0.1)
            assert await transport.alive_in(pid_dir) == []
            result = await transport.run("sleep 30", timeout=0.5, pidfile=f"{pid_dir}/t.pid")
            assert result.error == "timeout"
        finally:
            await transport.close()
            server.close()
    asyncio.run(main())


def test_dead_local_run_recovered(tmp_path, no_git):
    """A tsq run --local that died: the daemon cancels its queued run and releases its locks."""
    from testsuit.sequencer import sequencer as sequencer_module
    bench = Bench(tmp_path, stages=1)
    local = Sequencer(bench.config, local=True)
    run = local.submit(bench.build())
    dead = f"{socket.gethostname()}:999999999"
    assert not sequencer_module.owner_alive(dead) and sequencer_module.owner_alive("elsewhere:1")
    local.store.update_run(run["id"], owner=dead)
    local.store.try_lock(run["id"], [("stage", "bench_1", "exclusive")])

    async def main():
        daemon = Sequencer(bench.config)
        assert daemon.schedule() == []                   # the daemon never runs a local run
        await daemon.recover()
        result = daemon.store.get_run(run["id"])
        locks = daemon.store.locks()
        await daemon.close()
        return result, locks
    result, locks = asyncio.run(main())
    assert result["state"] == "cancelled" and locks == []

