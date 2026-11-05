"""The sequencer kernel: intake, queue, scheduling, run lifecycle, abort, recovery (README sections 9 to 11).

One ``Sequencer`` per lab. The daemon (``tsqd``) serves it over the REST API; ``tsq run --local``
uses the same class in the foreground, with the same SQLite store as lock arbiter.
"""
from __future__ import annotations

import asyncio
import graphlib
import logging
import os
import re
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
from testsuit.sequencer.engine import (DEFAULT_STOP_GRACE, ERROR, FAILED, SKIPPED, SUCCEEDED, BoundSlot, PhaseRunner, RunContext,
                                       StepRecord, StepRunner, worst)
from testsuit.sequencer.intake import Datapack, check_against_lab, load_datapack, unpack
from testsuit.sequencer.lab import Lab, checkout, load_lab, pull_lab
from testsuit.sequencer.model import FlatStep, Step
from testsuit.sequencer.monitor import Monitor, component_keys, selftest_component
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


def _event_line(kind: str, data: dict) -> str:
    """An event as one log line: its kind, then its fields that have a value (long ones cut)."""
    import json
    fields = []
    for key, value in data.items():
        if value in (None, "", [], {}):
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str, separators=(",", ":"))
        fields.append(f"{key}={text if len(text) <= 160 else text[:157] + '...'}")
    return f"{kind}: " + " ".join(fields)


class Sequencer:

    def __init__(self, config: Config, store: Store | None = None, local: bool = False):
        self.config = config
        self.local = local
        for name in ("incoming", "runs", "results"):
            folder = config.storage_path(name)
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                where = (f"storage.root in {config.file}" if config.file else
                         "no configuration file found (no --config, no $TSQ_CONFIG, no /etc/testsuit/tsq.yml), "
                         "so the default storage.root")
                raise SequencerError(f"cannot create the storage folder '{folder}': {e.strerror} ({where}). "
                                     f"Create it for this user, or point storage.root elsewhere.") from e
        self.store = store or Store(config.storage_path("database"))
        self.lab: Lab = load_lab(config.lab_folder, config.inventory_file, config.lab.require_tag)
        self.monitor = Monitor(self.store, config.monitor.interval, self.notify)
        self._transports: dict[str, Transport] = {}
        self.monitor.attach(self.lab, self.transport_for)
        if config.selftest.when_idle:
            self.monitor.on_idle = self._selftest_one
        self.executions: dict[str, "RunExecution"] = {}
        self.subscribers: set[asyncio.Queue] = set()
        self._wake: asyncio.Event | None = None
        self.started_at = now()

    # ── events ─────────────────────────────────────────────────────────
    def notify(self, kind: str, data: dict) -> None:
        if kind not in ("log", "probe"):            # a probe is not an event worth keeping: every few seconds
            self.store.add_event(kind, data)
        if kind not in ("log", "step", "probe"):            # the events in the log of tsqd too (Lab page, Logs)
            log.log(logging.WARNING if kind == "state_mismatch" or str(data.get("verdict", "")).upper() == "ERROR"
                    else logging.INFO, _event_line(kind, data))
        event = {"kind": kind, "ts": now(), **data}
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        if kind in ("component", "run") and self._wake is not None:
            self._wake.set()

    def end_streams(self) -> None:
        """End the open event streams (GET /events): the server is stopping, and would otherwise wait
        for these never-ending responses before it exits."""
        for queue in list(self.subscribers):
            while True:
                try:
                    queue.put_nowait(None)
                    break
                except asyncio.QueueFull:
                    queue.get_nowait()                  # the end matters more than an event nobody will read

    def wake(self) -> None:
        if self._wake is not None:
            self._wake.set()

    # ── lab ────────────────────────────────────────────────────────────
    def transport_for(self, comp_id: str) -> Transport:
        if comp_id not in self._transports:
            comp = self.lab.inventory.components[comp_id]
            stage = self.lab.inventory.stages[comp.stages[0]] if comp.stages else None
            resolved = self.lab.resolve(comp, stage)
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
        for resource in set(binding.realms.values()) | set(binding.slots.values()):
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

    def repost(self, run_id: str) -> dict:
        """Queue an ended run again, same run id: its outputs (run folder, results) are removed and its
        datapack unpacked afresh from the posted zip."""
        run = self.require_run(run_id)
        if run["state"] not in DONE_STATES or run["id"] in self.executions:
            raise SequencerError(f"run {run['id']} is {run['state']}: only an ended run can be posted again")
        incoming = Path(run["zip"] or "")
        if not incoming.is_file():
            raise SequencerError(f"run {run['id']}: its datapack zip is gone, post the datapack again")
        runs = self.config.storage_path("runs")
        run_dir, fresh = runs / run["id"], runs / f".{run['id']}.repost"
        shutil.rmtree(fresh, ignore_errors=True)
        try:                                                  # unpacked and checked aside: a refusal keeps the old run
            bag = unpack(incoming, fresh)
            problems = check_against_lab(load_datapack(bag), self.lab)
            if problems:
                raise SequencerError("the lab cannot run this datapack:\n  " + "\n  ".join(problems))
        except Exception:
            shutil.rmtree(fresh, ignore_errors=True)
            raise
        shutil.rmtree(run_dir, ignore_errors=True)
        shutil.rmtree(self.config.storage_path("results") / run["id"], ignore_errors=True)
        fresh.rename(run_dir)
        self.store.release(run["id"])
        self.store.update_run(run["id"], state="queued", verdict=None, reason=None, submitted_at=now(),
                              activated_at=None, ended_at=None, waiting_for=None, attempts=0, binding=None,
                              phase=None, results=None, warnings=None, steps=None, abort=None,
                              dir=str(run_dir / bag.name), local=int(self.local), owner=OWNER)
        self.notify("run", {"run": run["id"], "state": "queued", "name": run["name"], "priority": run["priority"],
                            "conform": bool(run["conform"]), "reposted": True})
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
        resolved = self.lab.resolve(comp, stage)
        via = self.lab.via(comp, stage, resolved.status)     # a probe through another component: its checks too
        result = await selftest_component(comp, resolved, self.transport_for(via.id if via else comp_id), self.monitor,
                                          self.config.selftest.timeout, via)
        specs = {check["id"]: check for check in resolved.selftest}
        for check in result["checks"]:                  # what each check tested, for the Lab page
            if check["id"] in specs:
                check["spec"] = specs[check["id"]]
        self.monitor.selftests[comp_id] = result
        failed = [c for c in result["checks"] if not c["ok"] and not c.get("skipped")]
        if not result["ok"]:
            # kept until a self-test passes: the probes that follow do not turn it back to idle (monitor.probe)
            detail = "; ".join(f"{c['id']}: {c['detail']}" if c["detail"] else c["id"] for c in failed) or "failed"
            self.store.set_selftest_failed(comp_id, {"at": result["at"], "detail": detail, "checks": result["checks"]})
            state = result["state"] or "broken"
            previous = self.store.set_state(comp_id, state, f"self-test failed: {detail}")
            self.notify("component", {"component": comp_id, "state": state, "previous": previous,
                                      "detail": f"self-test failed: {detail}"})
        elif (self.store.resources("component").get(("component", comp_id)) or {}).get("selftest_failed"):
            self.store.set_selftest_failed(comp_id, None)
            self.notify("selftest", {"target": comp_id, "ok": True})
            await self.monitor.probe(comp_id)               # back to its probed state now
        return result

    # ── commands run by hand, on a component in maintenance ─────────────
    MANUAL_KINDS = ("run", "signal", "builtin kill")

    def _in_maintenance(self, comp_id: str) -> bool:
        resources = self.store.resources()
        comp = self.lab.inventory.components[comp_id]
        return any((resources.get(key) or {}).get("maintenance")
                   for key in [("component", comp_id), *[("stage", s) for s in comp.stages]])

    def _manual_keys(self, comp, stage, root: str, via=None) -> dict:
        keys = component_keys(comp, root, via, self.lab)
        keys["component"].update(name=comp.id, type=comp.role)
        return keys

    @staticmethod
    def _segments(template: str, keys: dict, where: str) -> list[dict]:
        """A command as it will run, in pieces: the literal text, and each ``{{ expression }}`` with its value
        (or why it cannot be rendered), for the Lab page to show the key behind each value."""
        if "{%" in template:                            # control blocks: the whole, rendered at once
            try:
                return [{"text": str(render(template, keys, where)), "key": None}]
            except SequencerError as e:
                return [{"text": template, "key": None, "error": str(e)}]
        segments = []
        for part in re.split(r"(\{\{.*?\}\})", template, flags=re.DOTALL):
            if not part:
                continue
            if part.startswith("{{"):
                try:
                    segments.append({"text": str(render(part, keys, where)), "key": part[2:-2].strip()})
                except SequencerError as e:
                    segments.append({"text": part, "key": part[2:-2].strip(), "error": str(e).split(": ", 1)[-1]})
            else:
                segments.append({"text": part, "key": None})
        return segments

    async def manual_commands(self, comp_id: str) -> dict:
        """The commands of a component (as the lab resolves them), whether each can be run by hand, and each
        run command as it would run (``segments``: its text, with the key behind each value)."""
        comp = self.lab.inventory.components[comp_id]
        stage = self.lab.inventory.stages[comp.stages[0]] if comp.stages else None
        resolved = self.lab.resolve(comp, stage)

        async def root_of(cid: str) -> str:
            transport = self.transport_for(cid)
            try:
                return await asyncio.wait_for(transport.resolve_root(), 10)
            except Exception:
                return transport._root                  # unreachable now: its root as configured
        root = await root_of(comp_id)
        commands = []
        for name, spec in sorted(resolved.commands.items()):
            spec = dict(spec or {})
            kind = ("run" if "run" in spec else "signal" if "signal" in spec
                    else f"builtin {spec['builtin']}" if "builtin" in spec else next(iter(spec), "?"))
            entry = {"name": name, "kind": kind, "spec": spec, "manual": kind in self.MANUAL_KINDS}
            if kind == "run":
                via = None
                if spec.get("via"):
                    try:
                        via = self.lab.find_via(comp, stage, str(spec["via"]), f"command '{name}' of {comp_id}")
                    except SequencerError:
                        pass
                entry["segments"] = self._segments(str(spec["run"]), self._manual_keys(comp, stage, root, via),
                                                   f"{comp_id}.{name}")
            commands.append(entry)
        return {"component": comp_id, "maintenance": self._in_maintenance(comp_id),
                "held_by": [r for r, _ in self.store.holders().get(("component", comp_id), [])],
                "commands": commands}

    async def manual_command(self, comp_id: str, name: str, timeout: float | None = None) -> dict:
        """Run one command of a component by hand (README section 7.6): only in maintenance, and not while a run
        holds it. A ``run`` command runs with the keys of the lab (no datapack: no params, no slot);
        a detached one is started with its pid file under ``<root>/.tsq/manual/<component>/``, where ``signal``
        commands and the built-in ``kill`` find the processes started by hand."""
        info = await self.manual_commands(comp_id)
        if not info["maintenance"]:
            raise SequencerError(f"component '{comp_id}' is not in maintenance: put it (or its stage) in maintenance "
                                 "to run its commands by hand")
        if info["held_by"]:
            raise SequencerError(f"component '{comp_id}' is held by run(s) {', '.join(info['held_by'])}")
        command = next((c for c in info["commands"] if c["name"] == name), None)
        if command is None:
            raise SequencerError(f"component '{comp_id}' has no command '{name}' "
                                 f"(has: {', '.join(c['name'] for c in info['commands']) or 'none'})")
        if not command["manual"]:
            raise SequencerError(f"'{name}' is a {command['kind']} command: only run and signal commands, and the "
                                 "built-in kill, can be run by hand (deploy and collect need a datapack)")
        comp = self.lab.inventory.components[comp_id]
        stage = self.lab.inventory.stages[comp.stages[0]] if comp.stages else None
        spec = command["spec"]
        transport = self.transport_for(comp_id)
        result: dict = {"component": comp_id, "command": name, "kind": command["kind"], "at": now()}
        try:
            root = await transport.resolve_root()
        except Exception as e:
            return {**result, "ok": False, "error": f"cannot reach {comp.host}: {e}"}
        manual_dir = f"{root}/.tsq/manual/{comp_id}"
        started = time.monotonic()
        if command["kind"] != "run":                   # signal, kill: the processes started by hand
            sig = "KILL" if command["kind"] == "builtin kill" else str(spec["signal"])
            places = [(transport, manual_dir)]         # on the component, and on what its commands run through
            for via in {v.id: v for v in self.lab.command_vias(comp, stage).values()}.values():
                other = self.transport_for(via.id)
                places.append((other, f"{await other.resolve_root()}/.tsq/manual/{comp_id}"))
            alive = []
            for place, folder in places:
                alive += await place.alive_in(folder)
                await place.signal_dir(folder, sig)
            result.update(ok=True, run=f"kill -{sig} on the processes started by hand",
                          output="\n".join(alive) or "no process started by hand is running")
        else:
            via = None
            if spec.get("via"):
                via = self.lab.find_via(comp, stage, str(spec["via"]), f"command '{name}' of {comp_id}")
                transport = self.transport_for(via.id)
                manual_dir = f"{await transport.resolve_root()}/.tsq/manual/{comp_id}"
            keys = self._manual_keys(comp, stage, root, via)
            text = str(render(spec["run"], keys, f"{comp_id}.{name}"))
            env = {str(k): str(v) for k, v in (render(spec.get("env") or {}, keys, f"{comp_id}.{name}") or {}).items()}
            if timeout is None and spec.get("timeout") is not None:
                timeout = parse_duration(render(spec["timeout"], keys, f"{comp_id}.{name}"))
            await transport.shell(f"mkdir -p {shlex.quote(manual_dir)}", timeout=15)
            pidfile = f"{manual_dir}/{name}.pid"
            result.update(run=text, via=via.id if via else None, on=via.host if via else comp.host)
            if spec.get("detach"):
                process = await transport.start(text, env=env, pidfile=pidfile)
                try:
                    done = await process.wait(2.0)
                    result.update(ok=done.exit_status == 0, exit=done.exit_status, output=done.output, error=done.error)
                except asyncio.TimeoutError:
                    result.update(ok=True, detached=True, output=process.text(),
                                  detail="started, still running (stop it with a signal command or kill)")
            else:
                done = await transport.run(text, env=env, timeout=timeout or 300, pidfile=pidfile)
                result.update(ok=done.exit_status == 0, exit=done.exit_status, output=done.output, error=done.error)
        result["duration"] = round(time.monotonic() - started, 3)
        self.notify("manual_command", {"component": comp_id, "command": name, "ok": result["ok"],
                                       "audit": "run by hand, in maintenance"})
        log.info("manual command %s of %s: %s", name, comp_id, "ok" if result["ok"] else "failed")
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
                binding = Binding.from_dict(run["binding"])
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

    async def bind_slots(self, ids: list[str] | None = None) -> None:
        inv = self.seq.lab.inventory
        realm_of = {c.id: c.realm for c in self.datapack.needs.slots.values()}
        for logical, concrete_id in self.binding.slots.items():
            if ids is not None and logical not in ids:
                continue
            if logical in self.ctx.bound and self.ctx.bound[logical].concrete.id == concrete_id:
                continue
            lc = self.datapack.needs.slots[logical]
            concrete = inv.components[concrete_id]
            realm = realm_of.get(logical) or ""
            stage = inv.stages[self.binding.realms[realm]] if realm else None
            resolved = self.seq.lab.resolve(concrete, stage)
            transport = self.seq.transport_for(concrete_id)
            bound = BoundSlot(logical=lc, concrete=concrete, stage=stage, realm=realm,
                                   resolved=resolved, transport=transport)
            for via in self.seq.lab.command_vias(concrete, stage, resolved).values():
                if via.id not in bound.vias:           # commands run through it: where their pid files go
                    via_transport = self.seq.transport_for(via.id)
                    bound.vias[via.id] = (via, via_transport, await via_transport.resolve_root())
            try:
                bound.root = await transport.resolve_root()
            except Exception:
                if not bound.vias:
                    raise
                bound.root = str(transport._root)    # reached through another component only: not needed
            if concrete.lock != "exclusive":
                # Several runs may hold it at once: each gets its own input/output folders.
                bound.work = f"{bound.root}/.tsq/{self.run_id}"
            self.ctx.bound[logical] = bound
        self.ctx.realm_binding = dict(self.binding.realms)
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
                          realm_binding={}, priority=run["priority"], notify=notify, state_of=state_of,
                          expect_state=lambda concrete, state: self.store.set_expected(concrete, state),
                          on_state_mismatch=self.datapack.pipeline.on_state_mismatch,
                          max_output=self.seq.config.storage.max_run_output, lab=self.seq.lab)

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
        await self.bind_slots()

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
        for stage_id in self.binding.realms.values():
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
            self.versions[bound.concrete.id] = {"version": text, "slot": logical, "role": bound.concrete.role}
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
        held = Binding(realms={s: c for s, c in self.binding.realms.items() if s in flat.phase.realms},
                       slots={l: c for l, c in self.binding.slots.items()
                              if self.datapack.needs.slots[l].realm in flat.phase.realms or l in flat.phase.slots})
        missing_stages = [s for s in flat.phase.realms if s not in held.realms]
        missing_comps = [c for c in flat.phase.slots if c not in held.slots]
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
                    self.binding.realms.update(binding.realms)
                    self.binding.slots.update(binding.slots)
                    self.store.update_run(self.run_id, binding=self.binding.as_dict(), waiting_for="")
                    new = [l for l in binding.slots if l not in self.ctx.bound]
                    await self.bind_slots()
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
            for s in phase.realms:
                if s in self.binding.realms:
                    keep_binding.realms[s] = self.binding.realms[s]
            for l, c in self.binding.slots.items():
                if l in phase.slots or self.datapack.needs.slots[l].realm in phase.realms:
                    keep_binding.slots[l] = c
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

        async def alive(bound: BoundSlot) -> bool:
            for transport, folder in bound.pid_places(self.run_id):
                try:
                    if await transport.alive_in(folder):
                        return True
                except Exception:
                    if transport is bound.transport and bound.vias:
                        continue                       # reached through another component only
                    return True
            return False
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

    async def _command(self, bound: BoundSlot, name: str) -> None:
        """A component command run by the sequencer itself (stop or kill on abort), recorded as a step."""
        flat = next((f for f in self.datapack.phases if f.phase.id == self.ctx.phase), self.datapack.phases[0])
        fs = FlatStep(id=f"tsq_{name}", step=Step(id=f"tsq_{name}", slot=bound.id, do=name), needs=[], params={})
        record = StepRecord(phase=flat.phase.id, id=fs.id, slot=bound.id, state="running", started=now())
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
        await self.bind_slots()
        for bound in self.ctx.bound.values():
            for transport, folder in bound.pid_places(self.run_id):
                try:
                    await transport.signal_dir(folder, "KILL")
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
            for transport, folder in bound.pid_places(self.run_id):
                try:
                    await transport.signal_dir(folder, "KILL")
                    await transport.shell(f"rm -rf {shlex.quote(folder)}; rmdir {shlex.quote(folder.rsplit('/', 1)[0])} "
                                          "2>/dev/null; true", timeout=20)
                except Exception as e:
                    if not (transport is bound.transport and bound.vias):
                        log.warning(f"run {self.run_id}: cleaning {bound.concrete.id} on {transport.host}: {e}")

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
