"""The sequencer kernel: intake, queue, scheduling, run lifecycle, abort, recovery (README sections 9 to 11).

One ``Sequencer`` per lab. The daemon (``tsqd``) serves it over the REST API; ``tsq run --local``
uses the same class in the foreground, with the same SQLite store as lock arbiter.
"""
from __future__ import annotations

import asyncio
import graphlib
import logging
import os
import secrets
import shlex
import shutil
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from testsuit.datatools.datapack.baseline import check_baseline, tool_versions
from testsuit.sequencer.config import Config
from testsuit.sequencer.engine import (DEFAULT_STOP_GRACE, ERROR, FAILED, SKIPPED, SUCCEEDED, BoundComponent, PhaseRunner, RunContext,
                                       StepRecord, StepRunner, worst)
from testsuit.sequencer.intake import Datapack, check_against_lab, load_datapack, unpack
from testsuit.sequencer.lab import Lab, checkout, load_lab, pull_lab
from testsuit.sequencer.model import FlatStep, Step
from testsuit.sequencer.monitor import Monitor, selftest_component
from testsuit.sequencer.scheduler import (Availability, Binding, candidate_resources, find_binding, order_queue)
from testsuit.sequencer.store import ACTIVE_STATES, QUEUED_STATES, Store
from testsuit.sequencer.transports import Transport, create_transport
from testsuit.sequencer.results import pack_results
from testsuit.sequencer.util import SequencerError, dump_yaml, now, parse_duration, render, wait_event

log = logging.getLogger("testsuit.sequencer")

EXIT_CODES = {SUCCEEDED: 0, FAILED: 1, ERROR: 2}
DONE_STATES = ("done", "cancelled")


def load_config_plugins(config: Config) -> list[str]:
    """The installed plugins, and the ones tsq.yml names (added to TESTSUIT_PLUGINS, for child processes too)."""
    from testsuit.plugins import ENV_VAR, load_plugins
    if config.plugins:
        current = [p for p in os.environ.get(ENV_VAR, "").split(",") if p.strip()]
        os.environ[ENV_VAR] = ",".join(current + [p for p in config.plugins if p not in current])
    return load_plugins()


OWNER = f"{socket.gethostname()}:{os.getpid()}"


def owner_alive(owner: str | None) -> bool:
    """Whether the process that submitted a local run is still alive (unknown, on another host: yes)."""
    host, _, pid = (owner or "").rpartition(":")
    if host != socket.gethostname() or not pid.isdigit():
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


class Sequencer:

    def __init__(self, config: Config, store: Store | None = None, local: bool = False):
        self.config = config
        self.local = local
        for name in ("incoming", "runs", "results"):
            config.storage_path(name).mkdir(parents=True, exist_ok=True)
        self.store = store or Store(config.storage_path("database"))
        self.lab: Lab = load_lab(config.lab_folder, config.inventory_file, config.lab.require_tag)
        self.monitor = Monitor(self.store, config.monitor.interval, self.notify)
        self._transports: dict[str, Transport] = {}
        self.monitor.attach(self.lab, self.transport_for)
        self.executions: dict[str, "RunExecution"] = {}
        self.subscribers: set[asyncio.Queue] = set()
        self._wake: asyncio.Event | None = None
        self.started_at = now()

    # ── events ─────────────────────────────────────────────────────────
    def notify(self, kind: str, data: dict) -> None:
        if kind != "log":
            self.store.add_event(kind, data)
        event = {"kind": kind, "ts": now(), **data}
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        if kind in ("component", "run") and self._wake is not None:
            self._wake.set()

    def wake(self) -> None:
        if self._wake is not None:
            self._wake.set()

    # ── lab ────────────────────────────────────────────────────────────
    def transport_for(self, comp_id: str) -> Transport:
        if comp_id not in self._transports:
            comp = self.lab.inventory.components[comp_id]
            stage = self.lab.inventory.stages[comp.stages[0]] if comp.stages else None
            resolved = self.lab.types.resolve(comp, stage)
            self._transports[comp_id] = create_transport(resolved.transport, comp)
        return self._transports[comp_id]

    async def reload_lab(self, pull: bool = False, revision: str | None = None) -> Lab:
        folder = self.config.lab_folder
        previous = pull_lab(folder, revision) if pull else None
        try:
            lab = load_lab(folder, self.config.inventory_file, self.config.lab.require_tag)
        except SequencerError:
            if previous:
                checkout(folder, previous)
            raise
        old = self._transports
        self.lab, self._transports = lab, {}
        self.monitor.attach(lab, self.transport_for)
        for transport in old.values():
            await transport.close()
        self.notify("lab", {"git": lab.git, "warnings": lab.warnings})
        self.wake()
        return lab

    def availability(self) -> Availability:
        resources = self.store.resources()
        return Availability(
            states={rid: r.get("state") or "unknown" for (kind, rid), r in resources.items() if kind == "component"},
            maintenance={key for key, r in resources.items() if r.get("maintenance") or r.get("hold")},
            holders=self.store.holders(), usage=self.store.usage())

    # ── intake ─────────────────────────────────────────────────────────
    def submit(self, zip_path: Path, priority: int | None = None, expected_baseline: dict | None = None) -> dict:
        """Verify, unpack and validate a datapack, and queue it. Non-conform datapacks are accepted, flagged."""
        run_id = new_run_id()
        priority = self.config.queue.default_priority if priority is None else int(priority)
        if not 0 <= priority <= 100:
            raise SequencerError("priority must be between 0 and 100")
        incoming = self.config.storage_path("incoming") / f"{run_id}.zip"
        shutil.copy2(zip_path, incoming)
        run_dir = self.config.storage_path("runs") / run_id
        try:
            bag = unpack(incoming, run_dir)
            datapack = load_datapack(bag)
            problems = check_against_lab(datapack, self.lab)
            if problems:
                raise SequencerError("the lab cannot run this datapack:\n  " + "\n  ".join(problems))
        except Exception:
            shutil.rmtree(run_dir, ignore_errors=True)
            incoming.unlink(missing_ok=True)
            raise
        expected = expected_baseline if expected_baseline is not None else datapack.expected_baseline
        nonconform = []
        if not datapack.conform:
            nonconform.append("the datapack is not conform in itself (built with --nocheck, or from untagged or "
                              "modified sources, or unconfirmed datasets)")
        if datapack.baseline is not None:
            nonconform += check_baseline(datapack.baseline, expected, parts=("datapack", "sources", "tools"))
        elif expected:
            nonconform.append("the datapack has no baseline.yml to check against the expected baseline")
        self.store.add_run(id=run_id, name=datapack.name, testdef=datapack.testdef_id, zip=str(incoming),
                           dir=str(bag), priority=priority, conform=not nonconform, nonconform=nonconform,
                           expected_baseline=expected, local=int(self.local), owner=OWNER)
        run = self.store.get_run(run_id)
        self.notify("run", {"run": run_id, "state": "queued", "name": datapack.name, "priority": priority,
                            "conform": not nonconform})
        self.wake()
        return run

    # ── scheduling ─────────────────────────────────────────────────────
    def schedule(self) -> list[str]:
        """Walk the queue once and activate what can run; return the activated run ids."""
        avail = self.availability()
        reserved: set[tuple[str, str]] = set()
        started = []
        queue = order_queue(self.store.queue(), self.config.aging(), time.time())
        for run in queue:
            if run["local"] != self.local or (self.local and run["owner"] != OWNER):
                continue                                     # tsq run --local only runs its own datapack, tsqd none of them
            if run["id"] in self.executions:
                continue
            try:
                datapack = load_datapack(Path(run["dir"]))
            except SequencerError as e:
                self.finish_without_running(run["id"], f"invalid datapack: {e}")
                continue
            phases = datapack.pipeline.phases if datapack.pipeline.reserve == "all" else datapack.pipeline.phases[:1]
            binding, reasons = find_binding(self.lab, datapack.needs, phases, avail, run["id"], avoid=reserved,
                                            testdef=datapack.testdef_id)
            if binding is not None:
                conflicts = self.store.try_lock(run["id"], binding.locks(self.lab))
                if not conflicts:
                    self.activate(run, datapack, binding)
                    started.append(run["id"])
                    avail = self.availability()
                    continue
                reasons = conflicts
            reserved |= candidate_resources(self.lab, datapack.needs, phases, avail)
            waiting = "; ".join(reasons)
            if run["waiting_for"] != waiting or run["state"] != "waiting_resources":
                self.store.update_run(run["id"], state="waiting_resources", waiting_for=waiting)
                self.notify("run", {"run": run["id"], "state": "waiting_resources", "waiting_for": waiting})
        return started

    def activate(self, run: dict, datapack: Datapack, binding: Binding) -> None:
        self.store.update_run(run["id"], state="reserved", activated_at=now(), waiting_for="",
                              binding=binding.as_dict(), warnings=self.lab.warnings)
        for resource in set(binding.stages.values()) | set(binding.components.values()):
            self.store.add_usage(resource, datapack.testdef_id)
        self.notify("run", {"run": run["id"], "state": "reserved", "binding": binding.as_dict()})
        execution = RunExecution(self, run["id"], datapack, binding)
        self.executions[run["id"]] = execution
        execution.task = asyncio.ensure_future(execution.run())

    def finish_without_running(self, run_id: str, reason: str, state: str = "done", verdict: str = ERROR) -> None:
        self.store.release(run_id)
        self.store.update_run(run_id, state=state, verdict=verdict, reason=reason, ended_at=now())
        self.notify("run", {"run": run_id, "state": state, "verdict": verdict, "reason": reason})

    async def scheduler_loop(self) -> None:
        self._wake = asyncio.Event()
        while True:
            try:
                self.schedule()
            except Exception:
                log.exception("scheduler error")
            await wait_event(self._wake, 5.0)
            self._wake.clear()

    # ── operator actions ───────────────────────────────────────────────
    def set_priority(self, run_id: str, priority: int) -> dict:
        run = self.require_run(run_id)
        if run["state"] not in QUEUED_STATES:
            raise SequencerError(f"run {run['id']} is {run['state']}: only a queued run's priority can change")
        if not 0 <= int(priority) <= 100:
            raise SequencerError("priority must be between 0 and 100")
        self.store.update_run(run["id"], priority=int(priority))
        self.notify("run", {"run": run["id"], "priority": int(priority)})
        self.wake()
        return self.store.get_run(run["id"])

    def require_run(self, run_id: str) -> dict:
        run = self.store.get_run(run_id)
        if run is None:
            raise SequencerError(f"no run '{run_id}'")
        return run

    async def abort(self, run_id: str, mode: str) -> dict:
        """cancel: queued runs leave the queue, running ones stop gracefully then get killed; kill: no grace."""
        run = self.require_run(run_id)
        if run["state"] in QUEUED_STATES and run["id"] not in self.executions:
            self.finish_without_running(run["id"], "cancelled while queued" if mode == "cancel" else "killed while queued",
                                        state="cancelled")
            return self.store.get_run(run["id"])
        execution = self.executions.get(run["id"])
        if execution is None:
            if run["state"] in DONE_STATES:
                raise SequencerError(f"run {run['id']} is already {run['state']}")
            self.store.update_run(run["id"], abort=mode)          # a run of another process (tsq run --local)
            return self.store.get_run(run["id"])
        await execution.abort(mode)
        return self.store.get_run(run["id"])

    def _resource(self, name: str) -> tuple[str, str]:
        if name in self.lab.inventory.stages:
            return "stage", name
        if name in self.lab.inventory.components:
            return "component", name
        raise SequencerError(f"no stage or component '{name}' in the lab")

    def set_flag(self, name: str, flag: str, value: bool, note: str = "") -> dict:
        kind, rid = self._resource(name)
        self.store.set_flag(kind, rid, flag, value, note)
        self.notify("resource", {"kind": kind, "id": rid, flag: value, "note": note})
        self.wake()
        return {"kind": kind, "id": rid, flag: value}

    def release(self, name: str) -> dict:
        """Release an operator hold, and any run lock on the resource (audited manual escape hatch)."""
        kind, rid = self._resource(name)
        self.store.set_flag(kind, rid, "hold", False)
        runs = self.store.force_release(kind, rid)
        if kind == "stage":
            for comp in self.lab.inventory.stages[rid].components:
                runs += self.store.force_release("component", comp)
        self.notify("release", {"kind": kind, "id": rid, "runs": sorted(set(runs)), "audit": "manual release"})
        self.wake()
        return {"kind": kind, "id": rid, "released_from": sorted(set(runs))}

    async def selftest(self, name: str | None = None) -> list[dict]:
        """The platform self-test on demand, for one stage, one component or the whole lab."""
        inv = self.lab.inventory
        if name is None:
            targets = list(inv.components)
        else:
            kind, rid = self._resource(name)
            targets = inv.stages[rid].components if kind == "stage" else [rid]
        results = await asyncio.gather(*[self._selftest_one(c) for c in targets])
        self.notify("selftest", {"target": name or "lab", "ok": all(r["ok"] for r in results)})
        return list(results)

    async def _selftest_one(self, comp_id: str) -> dict:
        comp = self.lab.inventory.components[comp_id]
        stage = self.lab.inventory.stages[comp.stages[0]] if comp.stages else None
        resolved = self.lab.types.resolve(comp, stage)
        result = await selftest_component(comp, resolved, self.transport_for(comp_id), self.monitor,
                                          self.config.selftest.timeout)
        if not result["ok"]:
            self.store.set_state(comp_id, result["state"] or "broken", "self-test failed")
            self.notify("component", {"component": comp_id, "state": result["state"], "detail": "self-test failed"})
        return result

    # ── restart recovery and housekeeping ───────────────────────────────
    async def recover(self) -> None:
        """Runs found active at startup: back to the queue if not started, else killed, finally run, locks released."""
        for run in self.store.runs(ACTIVE_STATES + ("queued",), limit=10000):
            if run["local"] and (run["owner"] == OWNER or owner_alive(run["owner"])):
                continue                                     # a tsq run --local still running
            if not run["local"] and self.local:
                continue                                     # the daemon's runs are the daemon's to recover
            if run["state"] in QUEUED_STATES:
                if run["local"]:                             # nobody else runs a local run
                    self.finish_without_running(run["id"], "its tsq run --local process ended", state="cancelled")
                elif run["state"] == "waiting_resources":
                    self.store.update_run(run["id"], state="queued")
                continue
            log.warning(f"run {run['id']} was {run['state']} when the sequencer stopped: killing it")
            try:
                datapack = load_datapack(Path(run["dir"]))
                binding = Binding(**(run["binding"] or {}))
                execution = RunExecution(self, run["id"], datapack, binding)
                await execution.recover(run.get("phase") or "")
            except Exception as e:
                log.exception(f"recovery of run {run['id']}")
                self.finish_without_running(run["id"], f"sequencer restarted; recovery failed: {e}")

    def cleanup(self) -> None:
        """Remove run folders past storage.retention.runs (the results zips stay)."""
        keep = self.config.storage.retention.runs
        if keep is None:
            return
        limit = time.time() - keep
        for run in self.store.runs(DONE_STATES, limit=10000):
            if run.get("ended_at") and datetime.fromisoformat(run["ended_at"]).timestamp() < limit:
                folder = Path(run["dir"]).parent
                if folder.is_dir() and folder.parent == self.config.storage_path("runs"):
                    shutil.rmtree(folder, ignore_errors=True)

    async def close(self) -> None:
        for transport in self._transports.values():
            await transport.close()
        self.store.close()


class RunExecution:
    """One activation of a datapack: self-test, phases, results."""

    def __init__(self, seq: Sequencer, run_id: str, datapack: Datapack, binding: Binding):
        self.seq, self.run_id, self.datapack, self.binding = seq, run_id, datapack, binding
        self.store = seq.store
        self.task: asyncio.Task | None = None
        self.ctx: RunContext | None = None
        self.selftests: list[dict] = []
        self.versions: dict[str, dict] = {}
        self.phase_verdicts: dict[str, str] = {}
        self.reason = ""
        self.abort_mode: str | None = None

    # ── helpers ─────────────────────────────────────────────────────────
    def set_state(self, state: str, **fields: Any) -> None:
        self.store.update_run(self.run_id, state=state, **fields)
        self.seq.notify("run", {"run": self.run_id, "state": state, **{k: v for k, v in fields.items() if k != "steps"}})

    async def bind_components(self, ids: list[str] | None = None) -> None:
        inv = self.seq.lab.inventory
        logical_to_stage = {c.id: c.stage for c in self.datapack.needs.components.values()}
        for logical, concrete_id in self.binding.components.items():
            if ids is not None and logical not in ids:
                continue
            if logical in self.ctx.bound and self.ctx.bound[logical].concrete.id == concrete_id:
                continue
            lc = self.datapack.needs.components[logical]
            concrete = inv.components[concrete_id]
            stage_logical = logical_to_stage.get(logical) or ""
            stage = inv.stages[self.binding.stages[stage_logical]] if stage_logical else None
            resolved = self.seq.lab.types.resolve(concrete, stage, self.datapack.needs.component_types, lc.type,
                                                  lc.commands)
            transport = self.seq.transport_for(concrete_id)
            bound = BoundComponent(logical=lc, concrete=concrete, stage=stage, logical_stage=stage_logical,
                                   resolved=resolved, transport=transport)
            bound.root = await transport.resolve_root()
            if concrete.lock != "exclusive":
                # Several runs may hold it at once: each gets its own input/output folders.
                bound.work = f"{bound.root}/.tsq/{self.run_id}"
            self.ctx.bound[logical] = bound
        self.ctx.stage_binding = dict(self.binding.stages)
        self.seq.monitor.active |= {b.concrete.id for b in self.ctx.bound.values()}

    def make_context(self) -> RunContext:
        run = self.store.get_run(self.run_id)

        async def state_of(concrete: str) -> str:
            return await self.seq.monitor.probe(concrete)

        def notify(kind: str, data: dict) -> None:
            self.seq.notify(kind, data)
            if kind == "step":                         # live steps for other processes (tsq show on a --local run)
                self.snapshot_steps()

        return RunContext(run_id=self.run_id, bag=self.datapack.dir, datapack=self.datapack, bound={},
                          stage_binding={}, priority=run["priority"], notify=notify, state_of=state_of,
                          expect_state=lambda concrete, state: self.store.set_expected(concrete, state),
                          on_state_mismatch=self.datapack.pipeline.on_state_mismatch,
                          max_output=self.seq.config.storage.max_run_output)

    def snapshot_steps(self) -> None:
        if self.ctx is not None:
            self.store.update_run(self.run_id, steps=[r.as_dict() for r in self.ctx.records.values()])

    # ── lifecycle ───────────────────────────────────────────────────────
    async def run(self) -> None:
        verdict = ERROR
        watcher = asyncio.ensure_future(self.watch_abort())
        try:
            verdict = await self._run()
        except asyncio.CancelledError:
            self.reason = self.reason or "sequencer stopped"
            raise
        except Exception as e:
            log.exception(f"run {self.run_id}")
            self.reason = f"internal error: {type(e).__name__}: {e}"
            verdict = ERROR
        finally:
            watcher.cancel()
            if self.store.get_run(self.run_id)["state"] not in ("queued",):
                await asyncio.shield(self.finish(verdict))

    async def _run(self) -> str:
        cfg = self.seq.config
        self.ctx = self.make_context()
        await self.bind_components()

        free = shutil.disk_usage(cfg.storage_path("runs")).free
        if free < cfg.storage.min_free_space:
            self.reason = f"only {free} bytes free in the run storage, storage.min_free_space is {cfg.storage.min_free_space}"
            return ERROR

        if cfg.selftest.before_each_run:
            self.set_state("selftest")
            if not await self.selftest(list(self.ctx.bound)):
                run = self.store.get_run(self.run_id)
                attempts = (run["attempts"] or 0) + 1
                if attempts >= cfg.selftest.max_attempts:
                    self.reason = f"platform self-test failed {attempts} time(s)"
                    return ERROR
                self.store.release(self.run_id)
                self.store.update_run(self.run_id, state="queued", attempts=attempts, binding=None,
                                      waiting_for="self-test failed, requeued")
                self.seq.notify("run", {"run": self.run_id, "state": "queued", "reason": "self-test failed"})
                self.seq.executions.pop(self.run_id, None)
                self.seq.wake()
                return "requeued"

        await self.read_versions(list(self.ctx.bound))
        self.set_state("running")
        return await self.run_phases()

    async def selftest(self, logical_ids: list[str]) -> bool:
        concrete = sorted({self.ctx.bound[l].concrete.id for l in logical_ids if l in self.ctx.bound})
        # every component of a locked stage is checked, used by the datapack or not
        for stage_id in self.binding.stages.values():
            concrete += [c for c in self.seq.lab.inventory.stages[stage_id].components if c not in concrete]
        results = await asyncio.gather(*[self.seq._selftest_one(c) for c in concrete])
        self.selftests += results
        dump_yaml(self.datapack.dir / "selftest.yml", {"attempts": self.selftests})
        failed = [r for r in results if not r["ok"]]
        for r in failed:
            bad = ", ".join(f"{c['id']}: {c['detail']}" for c in r["checks"] if not c["ok"])
            log.warning(f"run {self.run_id}: self-test of {r['component']} failed ({bad})")
        return not failed

    async def read_versions(self, logical_ids: list[str]) -> None:
        async def one(logical: str):
            bound = self.ctx.bound[logical]
            spec = bound.resolved.commands.get("version")
            if not spec or "run" not in spec:
                return
            keys = self.ctx.keys("", None, bound)
            try:
                result = await bound.transport.run(str(render(spec["run"], keys)), timeout=30)
                text = result.output.strip() if result.completed and result.exit_status == 0 else None
            except Exception:
                text = None
            self.versions[bound.concrete.id] = {"version": text, "logical": logical, "type": bound.concrete.type}
        await asyncio.gather(*[one(l) for l in logical_ids if l in self.ctx.bound])

    def run_baseline(self) -> dict:
        lab = self.seq.lab
        part = {"sequencer": {"version": next((t["version"] for t in tool_versions() if t["name"] == "testsuit"), None),
                              "tools": tool_versions()},
                "lab": {"repo": (lab.git or {}).get("repo"), "commit": (lab.git or {}).get("commit"),
                        "tag": (lab.git or {}).get("tag"), "clean": (lab.git or {}).get("clean"),
                        "path": (lab.git or {}).get("path"), "inventory": lab.inventory.file.name,
                        "warnings": lab.warnings},
                "binding": self.binding.as_dict(),
                "components": self.versions}
        return part

    async def run_phases(self) -> str:
        phases = {f.phase.id: f for f in self.datapack.phases}
        order = list(graphlib.TopologicalSorter({p.id: set(p.needs) for p in self.datapack.pipeline.phases}).static_order())
        timeout = parse_duration(self.datapack.pipeline.timeout)
        deadline = time.monotonic() + timeout if timeout else None
        for index, phase_id in enumerate(order):
            flat = phases[phase_id]
            if self.ctx.abort_mode:
                self.phase_verdicts[phase_id] = SKIPPED
                continue
            blocked = [n for n in flat.phase.needs if self.phase_verdicts.get(n) != SUCCEEDED]
            if blocked:
                self.phase_verdicts[phase_id] = SKIPPED
                self.seq.notify("phase", {"run": self.run_id, "phase": phase_id, "state": "skipped",
                                          "reason": f"needs {blocked} did not succeed"})
                continue
            if index and not await self.acquire(flat):
                self.phase_verdicts[phase_id] = ERROR
                break
            self.store.update_run(self.run_id, phase=phase_id)
            self.ctx.phase = phase_id
            self.copy_inputs(flat)
            remaining = None
            if deadline is not None:
                remaining = max(1.0, deadline - time.monotonic())
            phase_timeout = parse_duration(flat.phase.timeout)
            limit = min([t for t in (remaining, phase_timeout) if t] or [None]) if (remaining or phase_timeout) else None
            verdict = await PhaseRunner(self.ctx, flat).run(limit)
            self.phase_verdicts[phase_id] = verdict
            self.snapshot_steps()
            self.release_after(order[index + 1:])
        verdicts = list(self.phase_verdicts.values())
        if self.ctx.abort_mode:
            self.reason = self.reason or ("killed" if self.ctx.abort_mode == "kill" else "cancelled")
            return ERROR
        return worst(verdicts)

    async def acquire(self, flat) -> bool:
        """Bind and lock what a later phase needs and the run does not hold yet."""
        held = Binding(stages={s: c for s, c in self.binding.stages.items() if s in flat.phase.stages},
                       components={l: c for l, c in self.binding.components.items()
                                   if self.datapack.needs.components[l].stage in flat.phase.stages
                                   or l in flat.phase.components})
        missing_stages = [s for s in flat.phase.stages if s not in held.stages]
        missing_comps = [c for c in flat.phase.components if c not in held.components]
        if not missing_stages and not missing_comps:
            # still locked? (released after an earlier phase if no phase in between needed it)
            locks = {(l["kind"], l["resource"]) for l in self.store.locks(self.run_id)}
            if all(r in locks for r in {(k, r) for k, r, _ in held.locks(self.seq.lab)}):
                return True
        while not self.ctx.abort_mode:
            binding, reasons = find_binding(self.seq.lab, self.datapack.needs, [flat.phase], self.seq.availability(),
                                            self.run_id, fixed=held, testdef=self.datapack.testdef_id)
            if binding is not None:
                conflicts = self.store.try_lock(self.run_id, binding.locks(self.seq.lab))
                if not conflicts:
                    self.binding.stages.update(binding.stages)
                    self.binding.components.update(binding.components)
                    self.store.update_run(self.run_id, binding=self.binding.as_dict(), waiting_for="")
                    new = [l for l in binding.components if l not in self.ctx.bound]
                    await self.bind_components()
                    if new and self.seq.config.selftest.before_each_run and not await self.selftest(new):
                        self.reason = f"self-test failed before phase {flat.phase.id}"
                        return False
                    await self.read_versions(new)
                    return True
                reasons = conflicts
            self.store.update_run(self.run_id, waiting_for=f"phase {flat.phase.id}: " + "; ".join(reasons))
            await asyncio.sleep(2)
        return False

    def release_after(self, later: list[str]) -> None:
        if self.datapack.pipeline.reserve == "all":
            return
        phases = {p.id: p for p in self.datapack.pipeline.phases}
        keep_binding = Binding()
        for pid in later:
            phase = phases[pid]
            for s in phase.stages:
                if s in self.binding.stages:
                    keep_binding.stages[s] = self.binding.stages[s]
            for l, c in self.binding.components.items():
                if l in phase.components or self.datapack.needs.components[l].stage in phase.stages:
                    keep_binding.components[l] = c
        keep = {(k, r) for k, r, _ in keep_binding.locks(self.seq.lab)}
        released = self.store.release(self.run_id, keep=keep)
        if released:
            self.seq.notify("release", {"run": self.run_id, "released": released})
            self.seq.wake()

    def copy_inputs(self, flat) -> None:
        """Phase inputs: '<phase>:<component or *>/output/...' -> '<component>/input/...', inside the run folder."""
        data = self.datapack.dir / "data"
        for item in flat.phase.inputs:
            _, _, source = item.from_.partition(":") if ":" in item.from_ else ("", "", item.from_)
            comp, _, rest = source.partition("/")
            sources = [data / c / rest for c in (self.ctx.bound if comp == "*" else [comp])]
            target = data / item.to
            for src in sources:
                if not src.exists():
                    log.warning(f"run {self.run_id}: phase {flat.phase.id}: input {src} not found")
                    continue
                if src.is_dir():
                    shutil.copytree(src, target / src.name if item.to.endswith("/") else target, dirs_exist_ok=True)
                else:
                    dest = target / src.name if item.to.endswith("/") or target.is_dir() else target
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)

    async def abort(self, mode: str) -> None:
        """cancel: no new step starts, components with live processes get their stop command (grace), then
        kill; kill: their kill command at once. The current phase's finally steps run after."""
        if self.ctx is None or self.task is None or self.ctx.abort_mode == "kill":
            return
        self.abort_mode = mode
        self.ctx.abort_mode = mode
        self.store.update_run(self.run_id, abort=mode)
        self.seq.notify("run", {"run": self.run_id, "abort": mode})

        async def alive(bound: BoundComponent) -> bool:
            try:
                return bool(await bound.transport.alive_in(bound.pid_dir(self.run_id)))
            except Exception:
                return True
        bound = list(self.ctx.bound.values())
        if mode == "cancel":
            live = [b for b, a in zip(bound, await asyncio.gather(*[alive(b) for b in bound])) if a]
            stops = [self._command(b, "stop") for b in live if "stop" in b.resolved.commands]
            if stops:
                try:
                    await asyncio.wait_for(asyncio.gather(*stops, return_exceptions=True), DEFAULT_STOP_GRACE)
                except asyncio.TimeoutError:
                    pass
        # kill before the current phase's finally steps run (they start once its steps are cancelled)
        live = [b for b, a in zip(bound, await asyncio.gather(*[alive(b) for b in bound])) if a]
        await asyncio.gather(*[self._command(b, "kill") for b in live if "kill" in b.resolved.commands],
                             return_exceptions=True)
        if self.ctx.main_task is not None:
            self.ctx.main_task.cancel()

    async def watch_abort(self) -> None:
        """An abort written to the store by another process (the daemon, for a tsq run --local run)."""
        while True:
            await asyncio.sleep(1)
            mode = (self.store.get_run(self.run_id) or {}).get("abort")
            if mode and mode != self.abort_mode:
                await self.abort(mode)

    async def _command(self, bound: BoundComponent, name: str) -> None:
        """A component command run by the sequencer itself (stop or kill on abort), recorded as a step."""
        flat = next((f for f in self.datapack.phases if f.phase.id == self.ctx.phase), self.datapack.phases[0])
        fs = FlatStep(id=f"tsq_{name}", step=Step(id=f"tsq_{name}", component=bound.id, do=name), needs=[], params={})
        record = StepRecord(phase=flat.phase.id, id=fs.id, component=bound.id, state="running", started=now())
        self.ctx.records[record.key] = record
        runner = StepRunner(self.ctx, flat, fs, bound, record)
        try:
            record.state, record.reason = await runner.execute()
        finally:
            record.ended = now()
            runner.close()

    async def recover(self, phase_id: str) -> None:
        """After a restart: kill what the run started, run its current phase's finally, pack, release."""
        self.ctx = self.make_context()
        self.ctx.abort_mode = "kill"
        self.reason = "sequencer restarted during the run"
        await self.bind_components()
        for bound in self.ctx.bound.values():
            try:
                await bound.transport.signal_dir(bound.pid_dir(self.run_id), "KILL")
            except Exception:
                pass
        flat = next((f for f in self.datapack.phases if f.phase.id == phase_id), None)
        if flat is not None and flat.finally_:
            runner = PhaseRunner(self.ctx, flat)
            try:
                await runner.run_dag(flat.finally_, True)
            except Exception:
                log.exception(f"run {self.run_id}: finally after restart")
        await self.finish(ERROR)

    async def finish(self, verdict: str) -> None:
        if verdict == "requeued":
            return
        run = self.store.get_run(self.run_id)
        try:
            self.set_state("cleaning")
            if self.ctx is not None:
                for process in self.ctx.detached:
                    if not process.done.done():
                        await process.kill()
                await self.clean_remote()
            self.write_records(verdict, run)
            cfg = self.seq.config
            name = f"{self.datapack.name}_results" + ("" if run["conform"] else "_NONCONFORM")
            results = await asyncio.to_thread(
                pack_results, self.datapack.dir, cfg.storage_path("results") / self.run_id, name, cfg.storage.large_files.threshold,
                cfg.storage.large_files.mode)
            self.store.update_run(self.run_id, results=str(results))
        except Exception as e:
            log.exception(f"run {self.run_id}: packing the results")
            self.reason = self.reason or f"results not packed: {e}"
            verdict = ERROR
        finally:
            self.store.release(self.run_id)
            if self.ctx is not None:
                self.seq.monitor.active -= {b.concrete.id for b in self.ctx.bound.values()}
            self.snapshot_steps()
            self.store.update_run(self.run_id, state="done", verdict=verdict, reason=self.reason, ended_at=now())
            self.seq.notify("run", {"run": self.run_id, "state": "done", "verdict": verdict, "reason": self.reason})
            self.seq.executions.pop(self.run_id, None)
            self.seq.wake()

    async def clean_remote(self) -> None:
        """Kill what is left of the run's processes, and remove their pid files."""
        for bound in self.ctx.bound.values():
            folder = bound.pid_dir(self.run_id)
            try:
                await bound.transport.signal_dir(folder, "KILL")
                await bound.transport.shell(f"rm -rf {shlex.quote(folder)}; rmdir {shlex.quote(folder.rsplit('/', 1)[0])} "
                                            "2>/dev/null; true", timeout=20)
            except Exception as e:
                log.warning(f"run {self.run_id}: cleaning {bound.concrete.id}: {e}")

    def write_records(self, verdict: str, run: dict) -> None:
        bag = self.datapack.dir
        run_part = self.run_baseline() if self.ctx is not None else {}
        baseline = dict(self.datapack.baseline or {})
        baseline["run"] = run_part
        problems = list(run["nonconform"] or [])
        problems += check_baseline(baseline, run.get("expected_baseline"), parts=("run",))
        run_part["conform"] = not problems
        run_part["mismatches"] = problems
        if problems != (run["nonconform"] or []):
            self.store.update_run(self.run_id, conform=False, nonconform=problems)
        dump_yaml(bag / "baseline.yml", baseline, "# where the contents of this datapack come from, and what it ran on\n")
        current = self.store.get_run(self.run_id)
        dump_yaml(bag / "run.yml", {
            "run": self.run_id, "datapack": self.datapack.name, "verdict": verdict, "reason": self.reason,
            "priority": current["priority"], "submitted_at": current["submitted_at"],
            "activated_at": current["activated_at"], "ended_at": now(), "conform": not problems,
            "nonconform": problems, "binding": self.binding.as_dict(), "phases": self.phase_verdicts,
            "steps": [r.as_dict() for r in (self.ctx.records.values() if self.ctx else [])],
            "lab_warnings": self.seq.lab.warnings, "abort": self.abort_mode})
        if not (bag / "selftest.yml").exists():
            dump_yaml(bag / "selftest.yml", {"attempts": self.selftests})
