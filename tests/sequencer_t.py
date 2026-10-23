"""Sequencer kernel: a small bench of local processes run end to end (queue, locks, phases, abort, results)."""
import asyncio
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

LAB_TYPES = {
    "component_types": {
        "counter_host": {
            "extends": "linux_system",
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
        "dut_app": {"extends": "counter_host", "configurations": {"standard": {"inputs": [
            {"source": "files/app.sh", "target": "app.sh", "method": "copy"},
            {"source": "files/conf.txt", "target": "conf.txt"}]}}},
        "analysis": {"extends": "linux_system", "configurations": {"standard": {"inputs": [
            {"source": "files/check.sh", "target": "check.sh"}]}},
            "commands": {"start": {"run": "sh {{ component.input_dir }}/check.sh {{ component.input_dir }} "
                                          "{{ component.output_dir }}"}}},
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
                   {"id": "analyse", "component": "analysis", "do": "start", "needs": ["deploy"]}],
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
            "vars": {"tsq_transport": "local", "tsq_types": ["types/"]},
            "hosts": {"seq_host": {"tsq_role": "sequencer", "tsq_lock": "none",
                                   "tsq_remote_root": str(root / "remote" / "seq_host")}},
            "children": {f"bench_{i}": {"vars": {"tsq_stage_type": "counter_bench"}, "hosts": {
                f"counter_{i}": {"tsq_type": "counter_host", "tsq_role": "dut",
                                 "tsq_remote_root": str(root / "remote" / f"counter_{i}")}}}
                for i in range(1, stages + 1)}}}
        write(lab / "inventory.yml", yaml.safe_dump(inventory))
        write(lab / "types" / "bench.yml", yaml.safe_dump(LAB_TYPES))
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
    assert inv.components["counter_1"].type == "counter_host" and inv.components["seq_host"].adhoc


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
    types = yaml.safe_load((tmp_path / "lab" / "types" / "bench.yml").read_text())
    types["component_types"]["counter_host"]["selftest"] = [{"id": "probe", "run": "test -e /nonexistent/x"}]
    write(tmp_path / "lab" / "types" / "bench.yml", yaml.safe_dump(types))

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

    import uvicorn

    from testsuit.sequencer.daemon import build

    bench = Bench(tmp_path, stages=1)
    config = bench.config.model_dump(exclude={"file", "given"})
    config_file = write(tmp_path / "tsq.yml", yaml.safe_dump(config))
    seq, app = build(str(config_file))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    import requests
    for _ in range(100):
        try:
            requests.get(f"http://127.0.0.1:{port}/health", timeout=1)
            break
        except requests.ConnectionError:
            time.sleep(0.1)
    yield bench, f"http://127.0.0.1:{port}", config_file
    server.should_exit = True
    thread.join(30)


def test_daemon_api_and_cli(daemon, capsys):
    import requests

    from testsuit.cli.tsq import main as tsq
    bench, url, config_file = daemon
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
    assert yaml.safe_load(in_zip.text)["verdict"] == "succeeded"
    assert requests.get(f"{url}/runs/{run['id']}/logs/test_run:start").text.startswith("[tsq ")

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
