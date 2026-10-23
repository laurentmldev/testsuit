"""Run engine (README sections 8.3, 10 and 11): phases, step DAG, timeouts, verdicts, cancel and kill.

A phase runs its steps as asyncio tasks: a step starts when every step it ``needs`` has succeeded
(a step whose needs did not all succeed is skipped), one command at a time per component unless
``detach``. Then the phase's ``finally`` steps always run, in order unless they give their own
``needs``. Verdicts: ``succeeded``, ``failed`` (a step ran but did not give its expected result),
``error`` (a step could not run or did not finish: crash, timeout, transport, cancel).
"""
from __future__ import annotations

import asyncio
import logging
import re
import shlex
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from testsuit.sequencer import registry
from testsuit.sequencer.inventory import Component, Stage
from testsuit.sequencer.model import FlatPhase, FlatStep, LogicalComponent, phase_components
from testsuit.sequencer.transports import Result, Transport
from testsuit.sequencer.component_types import ResolvedType
from testsuit.sequencer.util import SequencerError, now, parse_duration, render, evaluate

log = logging.getLogger("testsuit.sequencer")

SUCCEEDED, FAILED, ERROR, SKIPPED = "succeeded", "failed", "error", "skipped"
_RANK = {SUCCEEDED: 0, SKIPPED: 0, FAILED: 1, ERROR: 2}
DETACH_SETTLE = 0.5                 # a detached command that exits within this time is judged on its exit
DEFAULT_STOP_GRACE = 30.0


def worst(verdicts) -> str:
    result = SUCCEEDED
    for v in verdicts:
        if _RANK.get(v, 2) > _RANK[result]:
            result = v
    return result


@dataclass
class BoundComponent:
    """A logical component of the datapack bound to a concrete lab component."""
    logical: LogicalComponent
    concrete: Component
    stage: Stage | None
    logical_stage: str
    resolved: ResolvedType
    transport: Transport
    root: str = ""                                # remote root, '~' resolved
    work: str = ""                                # where input/ and output/ live; the root unless shared

    @property
    def base(self) -> str:
        return self.work or self.root

    @property
    def id(self) -> str:
        return self.logical.id

    @property
    def input_dir(self) -> str:
        return f"{self.base}/{self.logical.id}/input"

    @property
    def output_dir(self) -> str:
        return f"{self.base}/{self.logical.id}/output"

    def pid_dir(self, run_id: str) -> str:
        """Pid files of the processes a run started (by concrete id: components may share a remote root)."""
        return f"{self.root}/.tsq/{run_id}/{self.concrete.id}"

    def keys(self, state: str = "unknown") -> dict:
        return {"id": self.logical.id, "name": self.logical.name or self.logical.id, "type": self.logical.type,
                "lab_type": self.concrete.type, "host": self.concrete.host, "concrete": self.concrete.id,
                "label": self.concrete.label, "stage": self.logical_stage,
                "concrete_stage": self.stage.id if self.stage else "", "role": self.logical.role,
                "input_dir": self.input_dir, "output_dir": self.output_dir, "remote_root": self.root,
                "vars": self.concrete.vars_in(self.stage), "state": state}


@dataclass
class StepRecord:
    phase: str
    id: str                                       # namespaced id
    component: str = ""                           # one for_each item, '' for the whole step
    state: str = "pending"                        # pending ready running succeeded failed error skipped
    reason: str = ""
    started: str = ""
    ended: str = ""
    exit_status: int | None = None
    attempts: int = 0
    log: str = ""

    @property
    def key(self) -> str:
        return f"{self.phase}:{self.id}" + (f"[{self.component}]" if self.component else "")

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


class Aborted(Exception):
    pass


@dataclass
class RunContext:
    """Everything the engine needs to run a datapack's phases."""
    run_id: str
    bag: Path                                     # run folder: the unpacked datapack
    datapack: Any                                 # intake.Datapack
    bound: dict[str, BoundComponent]              # logical component id -> bound component
    stage_binding: dict[str, str]                 # logical stage -> concrete stage id
    priority: int = 50
    notify: Callable[[str, dict], None] = lambda kind, data: None
    state_of: Callable[[str], Any] | None = None  # async (concrete id) -> probed state, from the monitor
    expect_state: Callable[[str, str], None] = lambda concrete, state: None
    on_state_mismatch: str = "warn"
    records: dict[str, StepRecord] = field(default_factory=dict)
    abort_mode: str | None = None                 # None, 'cancel' or 'kill'
    collected_bytes: int = 0
    max_output: int | None = None
    detached: list = field(default_factory=list)  # processes started with detach, still running
    main_task: asyncio.Task | None = None         # the steps of the current phase (cancelled on abort)
    phase: str = ""
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _running: dict[str, asyncio.Task] = field(default_factory=dict)

    def lock_for(self, concrete: str) -> asyncio.Lock:
        return self._locks.setdefault(concrete, asyncio.Lock())

    def keys(self, phase: str, params: dict | None = None, component: BoundComponent | None = None) -> dict:
        keys = dict(self.datapack.context)
        keys.update({
            "run": {"id": self.run_id, "dir": str(self.bag), "priority": self.priority},
            "phase": {"id": phase},
            "components": {c.id: c.keys() for c in self.bound.values()},
            "stages": {s: {"id": c, "logical": s} for s, c in self.stage_binding.items()},
            "params": params or {},
            "succeeded": lambda step: self.verdict_of(phase, step) == SUCCEEDED,
            "failed": lambda step: self.verdict_of(phase, step) == FAILED,
            "errored": lambda step: self.verdict_of(phase, step) == ERROR,
            "skipped": lambda step: self.verdict_of(phase, step) == SKIPPED,
            "verdict": lambda step: self.verdict_of(phase, step),
        })
        if component is not None:
            keys["component"] = component.keys()
            keys["remote_root"] = component.root
        return keys

    def verdict_of(self, phase: str, step: str) -> str:
        """Verdict of a step, a 'uses' group or a phase-qualified step ('test_run:down')."""
        if ":" in step:
            phase, step = step.split(":", 1)
        flat = next((f for f in self.datapack.phases if f.phase.id == phase), None)
        members = flat.groups.get(step, [step]) if flat else [step]
        verdicts = [r.state for r in self.records.values() if r.phase == phase and r.id in members]
        if not verdicts:
            return "pending"
        if any(v in ("pending", "ready", "running") for v in verdicts):
            return "running"
        if all(v == SKIPPED for v in verdicts):
            return SKIPPED
        return worst(verdicts)


# ── step execution ─────────────────────────────────────────────────────────

class StepRunner:
    """Executes one flat step of a phase, for one component or none."""

    def __init__(self, ctx: RunContext, phase: FlatPhase, fs: FlatStep, component: BoundComponent | None,
                 record: StepRecord):
        self.ctx, self.phase, self.fs, self.step, self.component, self.record = ctx, phase, fs, fs.step, component, record
        self.phase_id = phase.phase.id
        log_dir = ctx.bag / "logs" / self.phase_id
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / (fs.id + (f"[{component.id}]" if component else "") + ".log")
        record.log = self.log_path.relative_to(ctx.bag).as_posix()
        self._log = open(self.log_path, "ab")

    def write(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode()
        self._log.write(data)
        self._log.flush()
        self.ctx.notify("log", {"run": self.ctx.run_id, "step": self.record.key, "text": data.decode("utf-8", "replace")})

    def note(self, text: str) -> None:
        self.write(f"[tsq {now()}] {text}\n")

    def close(self) -> None:
        self._log.close()

    @property
    def pidfile(self) -> str | None:
        if self.component is None:
            return None
        return f"{self.component.pid_dir(self.ctx.run_id)}/{self.fs.id}.pid"

    def keys(self) -> dict:
        return self.ctx.keys(self.phase_id, self.fs.params, self.component)

    async def execute(self) -> tuple[str, str]:
        """(verdict, reason) of the step, retries included."""
        keys = self.keys()
        attempts = max(0, int(self.step.retries)) + 1
        verdict, reason = ERROR, ""
        for attempt in range(1, attempts + 1):
            self.record.attempts = attempt
            if attempt > 1:
                self.note(f"retry {attempt - 1}/{attempts - 1}")
            verdict, reason = await self._once(keys)
            if verdict == SUCCEEDED:
                break
        return verdict, reason

    async def _once(self, keys: dict) -> tuple[str, str]:
        action = self.step.action
        timeout = parse_duration(render(self.step.timeout, keys, self.fs.id)) if self.step.timeout is not None else None
        try:
            if action == "delay":
                seconds = parse_duration(render(self.step.delay, keys, self.fs.id))
                self.note(f"delay {seconds:g}s")
                await asyncio.sleep(seconds)
                return SUCCEEDED, ""
            if action == "wait_for":
                return await self.wait_for(render(self.step.wait_for, keys, self.fs.id), timeout)
            if action == "run":
                spec = {"run": self.step.run}
            elif action == "do":
                spec = self.command(self.step.do)
            elif action in ("push", "pull"):
                return await self.transfer(action, render(getattr(self.step, action), keys, self.fs.id))
            else:
                fn = registry.step_types[action.lower()]
                outcome = await fn(self, render(self.step.plugin_actions[action], keys, self.fs.id))
                return outcome if isinstance(outcome, tuple) else ((SUCCEEDED, "") if outcome else (FAILED, "plugin step failed"))
            return await self.command_spec(spec, keys, timeout)
        except SequencerError as e:
            self.note(f"error: {e}")
            return ERROR, str(e)

    def command(self, name: str) -> dict:
        if self.component is None:
            raise SequencerError(f"step '{self.fs.id}': 'do: {name}' needs a component")
        spec = self.component.resolved.commands.get(name)
        if spec is None:
            raise SequencerError(f"component '{self.component.id}' ({self.component.concrete.type}) has no "
                                 f"command '{name}' (has: {', '.join(sorted(self.component.resolved.commands)) or 'none'})")
        return dict(spec)

    async def command_spec(self, spec: dict, keys: dict, timeout: float | None) -> tuple[str, str]:
        """Run a command definition: run, wait_for, signal, builtin, or a plugin step type."""
        step = self.step
        if timeout is None and spec.get("timeout") is not None and "wait_for" not in spec:
            timeout = parse_duration(render(spec["timeout"], keys, self.fs.id))
        if "builtin" in spec:
            return await self.builtin(spec["builtin"], timeout)
        if "wait_for" in spec:
            wait = dict(spec["wait_for"])
            if "timeout" in spec and "timeout" not in wait:
                wait["timeout"] = spec["timeout"]
            return await self.wait_for(render(wait, keys, self.fs.id), timeout)
        if "signal" in spec:
            grace = parse_duration(render(spec.get("timeout", "10s"), keys, self.fs.id))
            return await self.signal(str(spec["signal"]), grace)
        if "run" in spec:
            command = render(spec["run"], keys, self.fs.id)
            env = {**render(spec.get("env") or {}, keys, self.fs.id), **render(step.env, keys, self.fs.id)}
            detach = step.detach if step.detach is not None else bool(spec.get("detach", False))
            expect = step.expect if step.expect is not None else spec.get("expect")
            return await self.shell(str(command), env, timeout, detach, expect)
        for name, value in spec.items():
            if name.lower() in registry.step_types:
                outcome = await registry.step_types[name.lower()](self, render(value, keys, self.fs.id))
                return outcome if isinstance(outcome, tuple) else ((SUCCEEDED, "") if outcome else (FAILED, "plugin step failed"))
        raise SequencerError(f"step '{self.fs.id}': command {spec} has no 'run', 'wait_for', 'signal' or known step type")

    def transport(self) -> Transport:
        if self.component is None:
            raise SequencerError(f"step '{self.fs.id}' needs a component")
        return self.component.transport

    async def shell(self, command: str, env: dict, timeout: float | None, detach: bool, expect: dict | None):
        if self.component is None:                 # no component: on the sequencer host, in the run folder
            from testsuit.sequencer.inventory import Component as _Component
            from testsuit.sequencer.transports.local import LocalTransport
            transport = LocalTransport(_Component("sequencer", {}), str(self.ctx.bag))
            pidfile = str(self.ctx.bag / ".tsq" / self.ctx.run_id / f"{self.fs.id}.pid")
            command = f"cd {shlex.quote(str(self.ctx.bag))} && {command}"
        else:
            transport, pidfile = self.component.transport, self.pidfile
        self.note(f"{'start (detached)' if detach else 'run'} on {transport.host}: {command}")
        if detach:
            try:
                process = await transport.start(command, env=env, log=self.write, pidfile=pidfile)
            except Exception as e:
                return ERROR, f"transport: {e}"
            try:
                result = await process.wait(DETACH_SETTLE)
            except asyncio.TimeoutError:
                self.ctx.detached.append(process)
                return SUCCEEDED, "started"
            return self.judge(result, expect)
        result = await transport.run(command, env=env, timeout=timeout, log=self.write, pidfile=pidfile)
        return self.judge(result, expect)

    def judge(self, result: Result, expect: dict | None) -> tuple[str, str]:
        self.record.exit_status = result.exit_status
        if not result.completed:
            self.note(f"did not complete: {result.error}")
            return ERROR, result.error or "error"
        expect = expect or {}
        codes = expect.get("exit", [0])
        codes = codes if isinstance(codes, list) else [codes]
        if "any" not in codes and result.exit_status not in [int(c) for c in codes]:
            self.note(f"exit status {result.exit_status}, expected {codes}")
            return FAILED, f"exit status {result.exit_status}"
        for stream in ("stdout", "stderr"):
            if expect.get(stream) and not re.search(str(expect[stream]), result.output, re.MULTILINE):
                self.note(f"output does not match '{expect[stream]}'")
                return FAILED, f"{stream} does not match"
        self.note(f"exit status {result.exit_status}")
        return SUCCEEDED, ""

    async def builtin(self, name: str, timeout: float | None) -> tuple[str, str]:
        comp = self.component
        if comp is None:
            raise SequencerError(f"built-in command '{name}' needs a component")
        local_data = self.ctx.bag / "data" / comp.id
        if name == "deploy":
            self.note(f"deploy {local_data / 'input'} -> {comp.transport.host}:{comp.input_dir}")
            try:
                await asyncio.wait_for(comp.transport.put(local_data / "input", comp.input_dir), timeout)
                result = await comp.transport.shell(f"rm -rf {shlex.quote(comp.output_dir)} && mkdir -p {shlex.quote(comp.output_dir)}")
            except asyncio.TimeoutError:
                return ERROR, "timeout"
            except Exception as e:
                self.note(f"deploy failed: {e}")
                return ERROR, f"transport: {e}"
            if not result.completed or result.exit_status:
                return ERROR, f"cannot create {comp.output_dir}: {result.error or result.output.strip()}"
            self.ctx.expect_state(comp.concrete.id, "configured")
            return SUCCEEDED, ""
        if name == "collect":
            target = local_data / "output"
            self.note(f"collect {comp.transport.host}:{comp.output_dir} -> {target}")
            try:
                size = await asyncio.wait_for(comp.transport.get(comp.output_dir, target), timeout)
            except asyncio.TimeoutError:
                return ERROR, "timeout"
            except Exception as e:
                self.note(f"collect failed: {e}")
                return ERROR, f"transport: {e}"
            self.apply_collect_filters(target)
            self.ctx.collected_bytes += size
            self.note(f"collected {size} bytes")
            if self.ctx.max_output is not None and self.ctx.collected_bytes > self.ctx.max_output:
                return ERROR, f"collected outputs over storage.max_run_output ({self.ctx.max_output} bytes)"
            return SUCCEEDED, ""
        if name == "kill":
            self.note(f"kill every process started by run {self.ctx.run_id} on {comp.transport.host}")
            try:
                await comp.transport.signal_dir(comp.pid_dir(self.ctx.run_id), "KILL")
            except Exception as e:
                return ERROR, f"transport: {e}"
            self.ctx.expect_state(comp.concrete.id, "idle")
            return SUCCEEDED, ""
        raise SequencerError(f"unknown built-in command '{name}'")

    def apply_collect_filters(self, target: Path) -> None:
        import fnmatch
        rules = self.component.resolved.collect or {}
        include, exclude = rules.get("include"), rules.get("exclude") or []
        for path in [p for p in target.rglob("*") if p.is_file()]:
            rel = path.relative_to(target).as_posix()
            if (include and not any(fnmatch.fnmatch(rel, pat) for pat in include)) or \
                    any(fnmatch.fnmatch(rel, pat) for pat in exclude):
                path.unlink()

    async def signal(self, sig: str, grace: float) -> tuple[str, str]:
        comp = self.component
        if comp is None:
            raise SequencerError("'signal' needs a component")
        folder = comp.pid_dir(self.ctx.run_id)
        sig = sig.upper().removeprefix("SIG")
        self.note(f"signal {sig} to the processes of {comp.id} on {comp.transport.host}")
        try:
            await comp.transport.signal_dir(folder, sig)
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                if not await comp.transport.alive_in(folder):
                    break
                await asyncio.sleep(0.2)
            else:
                self.note(f"still alive after {grace:g}s: KILL")
                await comp.transport.signal_dir(folder, "KILL")
        except Exception as e:
            return ERROR, f"transport: {e}"
        self.ctx.expect_state(comp.concrete.id, "idle")
        return SUCCEEDED, ""

    async def transfer(self, action: str, spec: dict) -> tuple[str, str]:
        comp = self.component
        if comp is None or not isinstance(spec, dict) or "from" not in spec or "to" not in spec:
            raise SequencerError(f"step '{self.fs.id}': '{action}' needs a component, and 'from' and 'to'")
        try:
            if action == "push":
                local = (self.ctx.bag / spec["from"]).resolve()
                if not local.is_relative_to(self.ctx.bag.resolve()):
                    raise SequencerError(f"push: '{spec['from']}' is outside the run folder")
                self.note(f"push {local} -> {comp.transport.host}:{spec['to']}")
                await comp.transport.put(local, spec["to"])
            else:
                local = (self.ctx.bag / spec["to"]).resolve()
                if not local.is_relative_to(self.ctx.bag.resolve()):
                    raise SequencerError(f"pull: '{spec['to']}' is outside the run folder")
                self.note(f"pull {comp.transport.host}:{spec['from']} -> {local}")
                self.ctx.collected_bytes += await comp.transport.get(spec["from"], local)
        except SequencerError:
            raise
        except Exception as e:
            return ERROR, f"transport: {e}"
        return SUCCEEDED, ""

    # ── ready checks ─────────────────────────────────────────────────────
    async def wait_for(self, spec: dict, timeout: float | None) -> tuple[str, str]:
        if not isinstance(spec, dict):
            raise SequencerError(f"step '{self.fs.id}': wait_for must be a mapping")
        spec = dict(spec)
        timeout = parse_duration(spec.pop("timeout", None)) or timeout or 60.0
        interval = parse_duration(spec.pop("interval", None)) or 0.5
        target = self.component
        if "component" in spec:
            name = spec.pop("component")
            if name not in self.ctx.bound:
                raise SequencerError(f"wait_for: unknown component '{name}'")
            target = self.ctx.bound[name]
        if len(spec) != 1:
            raise SequencerError(f"wait_for: expected one check kind, got {sorted(spec)}")
        kind, value = next(iter(spec.items()))
        check = self.ready_check(kind, value, target)
        self.note(f"wait for {kind} {value} (timeout {timeout:g}s)")
        deadline = time.monotonic() + timeout
        last = ""
        while True:
            try:
                ok, last = await check()
            except SequencerError as e:
                ok, last = False, str(e)
            if ok:
                self.note(f"ready: {kind}" + (f" ({last})" if last else ""))
                return SUCCEEDED, ""
            if time.monotonic() >= deadline:
                self.note(f"timeout waiting for {kind}" + (f": {last}" if last else ""))
                return ERROR, "timeout"
            await asyncio.sleep(min(interval, max(0.05, deadline - time.monotonic())))

    def ready_check(self, kind: str, value: Any, target: BoundComponent | None):
        def need_target():
            if target is None:
                raise SequencerError(f"wait_for {kind} needs a component")
            return target

        if kind == "state":
            async def check():
                comp = need_target()
                if self.ctx.state_of is None:
                    raise SequencerError("no state monitor")
                state = await self.ctx.state_of(comp.concrete.id)
                wanted = value if isinstance(value, list) else [value]
                return state in wanted, f"state {state}"
            return check
        if kind == "tcp":
            host, port = (value.get("host"), value.get("port")) if isinstance(value, dict) else str(value).rsplit(":", 1)
            host = host or (target.concrete.host if target else "localhost")

            async def check():
                try:
                    _, writer = await asyncio.wait_for(asyncio.open_connection(host, int(port)), 2)
                    writer.close()
                    return True, ""
                except (OSError, asyncio.TimeoutError, socket.gaierror) as e:
                    return False, str(e)
            return check
        if kind == "http":
            spec = value if isinstance(value, dict) else {"url": value}

            async def check():
                import urllib.request

                def get():
                    try:
                        with urllib.request.urlopen(spec["url"], timeout=3) as response:
                            return response.status, response.read(1 << 20).decode("utf-8", "replace")
                    except urllib.error.HTTPError as e:
                        return e.code, ""
                    except Exception as e:
                        return None, str(e)
                status, body = await asyncio.to_thread(get)
                ok = status == int(spec.get("status", 200)) and (
                    not spec.get("regex") or re.search(spec["regex"], body) is not None)
                return ok, f"status {status}"
            return check
        if kind in ("file", "log", "cmd"):
            if kind == "file":
                command = f"test -e {shlex.quote(str(value))}"
            elif kind == "log":
                if not isinstance(value, dict) or "file" not in value or "regex" not in value:
                    raise SequencerError("wait_for log needs 'file' and 'regex'")
                command = f"grep -E -q -- {shlex.quote(str(value['regex']))} {shlex.quote(str(value['file']))}"
            else:
                command = str(value)

            async def check():
                comp = need_target()
                result = await comp.transport.shell(command, timeout=10)
                if not result.completed:
                    return False, result.error or ""
                return result.exit_status == 0, ""
            return check
        if kind.lower() in registry.ready_checks:
            fn = registry.ready_checks[kind.lower()]

            async def check():
                ok = await fn(self, value)
                return bool(ok), ""
            return check
        raise SequencerError(f"unknown wait_for kind '{kind}'")


# ── phases ─────────────────────────────────────────────────────────────────

def _targets(ctx: RunContext, flat: FlatPhase, fs: FlatStep, keys: dict) -> list[str | None]:
    step = fs.step
    held = phase_components(flat.phase, ctx.datapack.needs)
    if step.component:
        name = render(step.component, keys, fs.id)
        return [name]
    if step.for_each is None:
        return [None]
    value = render(step.for_each, keys, fs.id)
    if value == "components":
        return list(held)
    if isinstance(value, str):
        by_role = [c for c in held if ctx.datapack.needs.components[c].role == value]
        if not by_role:
            raise SequencerError(f"step '{fs.id}': for_each '{value}' matches no component of the phase")
        return by_role
    if not isinstance(value, list):
        raise SequencerError(f"step '{fs.id}': for_each must be a list, 'components' or a role")
    outside = [c for c in value if c not in held]
    if outside:
        raise SequencerError(f"step '{fs.id}': component(s) {outside} outside the phase")
    return list(value)


class PhaseRunner:

    def __init__(self, ctx: RunContext, flat: FlatPhase):
        self.ctx, self.flat = ctx, flat
        self.id = flat.phase.id

    def record(self, fs: FlatStep, component: str | None) -> StepRecord:
        rec = StepRecord(phase=self.id, id=fs.id, component=component or "")
        self.ctx.records[rec.key] = rec
        return rec

    def set_state(self, rec: StepRecord, state: str, reason: str = "") -> None:
        rec.state, rec.reason = state, reason
        if state == "running":
            rec.started = now()
        elif state in (SUCCEEDED, FAILED, ERROR, SKIPPED):
            rec.ended = now()
        self.ctx.notify("step", {"run": self.ctx.run_id, **rec.as_dict()})

    async def run_step(self, fs: FlatStep, events: dict[str, asyncio.Event], verdicts: dict[str, str],
                       in_finally: bool) -> None:
        try:
            for need in fs.needs:
                await events[need].wait()
            keys = self.ctx.keys(self.id, fs.params)
            if not in_finally:
                blocked = [n for n in fs.needs if verdicts.get(n) != SUCCEEDED]
                if blocked or self.ctx.abort_mode:
                    rec = self.record(fs, None)
                    self.set_state(rec, SKIPPED, "aborted" if self.ctx.abort_mode else f"needs {blocked} did not succeed")
                    verdicts[fs.id] = SKIPPED
                    return
            if fs.step.if_ is not None:
                condition = evaluate(fs.step.if_, keys, fs.id)
                if not condition:
                    rec = self.record(fs, None)
                    self.set_state(rec, SKIPPED, f"if: {fs.step.if_}")
                    verdicts[fs.id] = SKIPPED
                    return
            try:
                targets = _targets(self.ctx, self.flat, fs, keys)
            except SequencerError as e:
                rec = self.record(fs, None)
                self.set_state(rec, ERROR, str(e))
                verdicts[fs.id] = ERROR
                return
            results = await asyncio.gather(*[self.run_target(fs, t) for t in targets])
            verdict = worst(results)
            if fs.step.continue_on_error and verdict != SUCCEEDED:
                verdict = SUCCEEDED
            verdicts[fs.id] = verdict
        except asyncio.CancelledError:
            verdicts.setdefault(fs.id, ERROR)
            raise
        except SequencerError as e:
            rec = self.record(fs, None)
            self.set_state(rec, ERROR, str(e))
            verdicts[fs.id] = ERROR
        finally:
            events[fs.id].set()

    async def run_target(self, fs: FlatStep, target: str | None) -> str:
        component = None
        if target is not None:
            if target not in self.ctx.bound:
                rec = self.record(fs, target)
                self.set_state(rec, ERROR, f"component '{target}' is not bound")
                return ERROR
            component = self.ctx.bound[target]
        rec = self.record(fs, target)
        runner = StepRunner(self.ctx, self.flat, fs, component, rec)
        uses_component = component is not None and fs.step.action in ("do", "run", "push", "pull")
        lock = self.ctx.lock_for(component.concrete.id) if uses_component and component.concrete.lock != "none" else None
        try:
            self.set_state(rec, "ready")
            if lock is not None:
                await lock.acquire()
            self.set_state(rec, "running")
            verdict, reason = await runner.execute()
            self.set_state(rec, verdict, reason)
            return verdict
        except asyncio.CancelledError:
            runner.note("cancelled")
            self.set_state(rec, ERROR, "killed" if self.ctx.abort_mode == "kill" else
                           ("cancelled" if self.ctx.abort_mode else "timeout"))
            raise
        finally:
            if lock is not None and lock.locked():
                lock.release()
            runner.close()

    async def run_dag(self, steps: list[FlatStep], in_finally: bool) -> dict[str, str]:
        verdicts: dict[str, str] = {}
        if in_finally:                     # finally steps without needs run in the order written
            ordered = []
            for i, fs in enumerate(steps):
                if not fs.needs and i:
                    fs = FlatStep(id=fs.id, step=fs.step, needs=[steps[i - 1].id], params=fs.params, group=fs.group)
                ordered.append(fs)
            steps = ordered
        events = {fs.id: asyncio.Event() for fs in steps}
        tasks = [asyncio.ensure_future(self.run_step(fs, events, verdicts, in_finally)) for fs in steps]
        self.ctx._running[self.id + (":finally" if in_finally else "")] = asyncio.current_task()
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for fs in steps:
                verdicts.setdefault(fs.id, ERROR)
            raise
        return verdicts

    async def run(self, timeout: float | None) -> str:
        self.ctx.notify("phase", {"run": self.ctx.run_id, "phase": self.id, "state": "running"})
        main_error = None
        verdicts: dict[str, str] = {}
        main = asyncio.ensure_future(self.run_dag(self.flat.steps, False))
        self.ctx.main_task = main
        try:
            if self.ctx.abort_mode:
                main.cancel()
            verdicts = await asyncio.wait_for(main, timeout)
        except asyncio.TimeoutError:
            main_error = "timeout"
        except asyncio.CancelledError:
            if not self.ctx.abort_mode:
                raise
            main_error = self.ctx.abort_mode
        finally:
            self.ctx.main_task = None
        if main_error:
            for fs in self.flat.steps:
                if not any(r.phase == self.id and r.id == fs.id for r in self.ctx.records.values()):
                    rec = self.record(fs, None)
                    self.set_state(rec, SKIPPED, main_error)
            log.info(f"run {self.ctx.run_id}: phase {self.id}: {main_error}")
        final = {}
        if self.flat.finally_:
            try:
                final = await asyncio.wait_for(self.run_dag(self.flat.finally_, True), timeout or 3600)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                final = {fs.id: ERROR for fs in self.flat.finally_}
        step_verdicts = [r.state for r in self.ctx.records.values() if r.phase == self.id
                         and not self._continue_on_error(r.id)]
        verdict = worst(step_verdicts + list(final.values()) + ([ERROR] if main_error else []))
        self.ctx.notify("phase", {"run": self.ctx.run_id, "phase": self.id, "state": "done", "verdict": verdict})
        return verdict

    def _continue_on_error(self, step_id: str) -> bool:
        fs = next((s for s in self.flat.steps + self.flat.finally_ if s.id == step_id), None)
        return bool(fs and fs.step.continue_on_error)
