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

"""Run engine (README sections 8.3, 10 and 11): phases, step DAG, timeouts, verdicts, cancel and kill.

A phase runs its steps as asyncio tasks: a step starts when every step it ``needs`` has succeeded
(a step whose needs did not all succeed is skipped), one command at a time per component unless
``detach``. Then the phase's ``finally`` steps always run, in order unless they give their own
``needs``. Verdicts: ``succeeded``, ``failed`` (a step ran but did not give its expected result),
``error`` (a step could not run or did not finish: crash, timeout, transport, cancel).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import shutil
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from testsuit.sequencer import registry
from testsuit.sequencer.inventory import Component, Stage
from testsuit.sequencer.model import FlatPhase, FlatStep, LogicalSlot, finally_order, phase_slots
from testsuit.sequencer.transports import Result, Transport, strict, traced, untraced
from testsuit.sequencer.cmdparams import PARAMS, command_params, criteria_missing_keys, default_params, exploit_keys, without_params
from testsuit.sequencer.library import Resolved, without_comments
from testsuit.sequencer.util import SequencerError, now, parse_duration, render, evaluate

log = logging.getLogger("testsuit.sequencer")

SUCCEEDED, FAILED, ERROR, SKIPPED = "succeeded", "failed", "error", "skipped"
_RANK = {SUCCEEDED: 0, SKIPPED: 0, FAILED: 1, ERROR: 2}
PYTHON_CRASH = 70                   # exit status of a python step whose script raised (EX_SOFTWARE)
DETACH_SETTLE = 0.5                 # a detached command that exits within this time is judged on its exit
DEFAULT_STOP_GRACE = 30.0


# Runs a python step's script: the keys as `keys`, the script's folder first on sys.path, and an uncaught
# exception as exit status 70 with its traceback. Plain Python 3, so the target needs nothing else.
PYTHON_WRAPPER = """import json, os, runpy, sys, traceback
script = sys.argv[1]
sys.argv = sys.argv[1:]
sys.path[0] = os.path.dirname(os.path.abspath(script))
with open(os.environ["TSQ_KEYS"]) as f:
    keys = json.load(f)
try:
    runpy.run_path(script, init_globals={"keys": keys}, run_name="__main__")
except SystemExit:
    raise
except BaseException:
    traceback.print_exc()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(70)
"""


def _plain(value: Any) -> Any:
    """The keys without what JSON cannot hold (the template functions such as succeeded())."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items() if not callable(v)}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value if not callable(v)]
    return value


def worst(verdicts) -> str:
    result = SUCCEEDED
    for v in verdicts:
        if _RANK.get(v, 2) > _RANK[result]:
            result = v
    return result


@dataclass
class BoundSlot:
    """A slot of the datapack bound to a lab component."""
    logical: LogicalSlot
    concrete: Component
    stage: Stage | None
    stage_slot: str                                    # its stage slot, '' for an ad hoc slot
    resolved: Resolved
    transport: Transport
    root: str = ""                                # remote root, '~' resolved
    work: str = ""                                # where input/ and output/ live; the root unless shared
    vias: dict = field(default_factory=dict)      # commands run through another component (a command's via):
                                                  # its id -> (Component, Transport, its remote root)

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

    def pid_places(self, run_id: str) -> list[tuple[Transport, str]]:
        """Where the processes a run started for it are: on itself, and on the components its commands run
        through (``via``), each a transport and the folder of their pid files there."""
        return [(self.transport, self.pid_dir(run_id))] + [
            (transport, f"{root}/.tsq/{run_id}/{self.concrete.id}") for _, transport, root in self.vias.values()]

    def keys(self) -> dict:
        """The ``slot`` key: the datapack's slot, and the lab component it is bound to."""
        return {"id": self.logical.id, "name": self.logical.name or self.logical.id, "slot_class": self.logical.slot_class,
                "role": self.logical.role, "stage_slot": self.stage_slot, "component": self.concrete.id,
                "input_dir": self.input_dir, "output_dir": self.output_dir, "remote_root": self.root}

    def component_keys(self, state: str = "unknown") -> dict:
        """The ``component`` key: the lab component the slot is bound to (its id is the lab id)."""
        return {"id": self.concrete.id, "host": self.concrete.host, "host_alias": self.concrete.host_alias, "label": self.concrete.label,
                "role": self.concrete.role, "extends": self.concrete.extends, "slot": self.logical.id,
                "stage": self.stage.id if self.stage else "", "vars": self.concrete.vars_in(self.stage),
                "input_dir": self.input_dir, "output_dir": self.output_dir, "remote_root": self.root, "state": state}


@dataclass
class StepRecord:
    phase: str
    id: str                                       # namespaced id
    slot: str = ""                                # one for_each item, '' for the whole step
    state: str = "pending"                        # pending ready running succeeded failed error skipped
    reason: str = ""
    started: str = ""
    ended: str = ""
    exit_status: int | None = None
    attempts: int = 0
    log: str = ""
    via: str = ""                                 # the component its command ran through (a command's via)
    # the lab command a 'do:' step runs: {name, component (lab component id), source ("class M", "host H",
    # "stage S", "" when its component has none)}, so the Run page opens where it is defined
    lab_command: dict | None = None
    # an upload running now (http body_file): {"sent": bytes, "total": bytes}, for the Run page's progress bar
    progress: dict | None = None
    # an exploit step's results: {folder, report, status, checks: [{criteria, scenario, check, status}]}
    exploit: dict | None = None
    # a manual operation: {title, text, checks: [{text, done, by, at}], base, since, outcome, note, by, at}
    manual: dict | None = None
    passes: int = 1                               # its run in the phase: 2 and more once run again by an operator

    @property
    def key(self) -> str:
        return f"{self.phase}:{self.id}" + (f"[{self.slot}]" if self.slot else "")

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if (v is not None or k not in ("progress", "exploit", "manual"))
                and not (k == "passes" and v == 1)}


class Aborted(Exception):
    pass


@dataclass
class RunContext:
    """Everything the engine needs to run a datapack's phases."""
    run_id: str
    bag: Path                                     # run folder: the unpacked datapack
    datapack: Any                                 # intake.Datapack
    bound: dict[str, BoundSlot]                   # slot id -> bound slot
    stage_slot_binding: dict[str, str]                 # stage_slot -> lab stage id
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
    finally_task: asyncio.Task | None = None      # the finally steps of the current phase (cancelled on kill)
    phase: str = ""
    lab: Any = None                               # lab.Lab: the keys of the other lab components (lab, stage)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _running: dict[str, asyncio.Task] = field(default_factory=dict)
    # the manual operations waiting for the operator: step record key -> the future their answer sets
    manual_waits: dict[str, asyncio.Future] = field(default_factory=dict)
    # operator controls (SPEC section 9.7): the main steps of the phase running now, step-by-step mode, the steps
    # paused after they ran ('<phase>:<step>' -> {phase, step, verdict, since, future}), the steps continued anyway
    # (record key -> reason) and the tasks that execute each record, the records replaced by a run again, and every
    # operator action
    phase_runner: Any = None
    step_mode: dict = field(default_factory=lambda: {"mode": "off", "breakpoints": []})
    pauses: dict[str, dict] = field(default_factory=dict)
    forced: dict[str, str] = field(default_factory=dict)
    step_tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    passes: dict[str, int] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    interventions: list[dict] = field(default_factory=list)

    def intervene(self, action: str, step: str, by: str = "", reason: str = "", **extra: Any) -> dict:
        """Record an operator action on the run (an event, kept with the run)."""
        entry = {"at": now(), "action": action, "step": step, "by": by, "reason": reason, **extra}
        self.interventions.append(entry)
        self.notify("intervention", {"run": self.run_id, **entry})
        return entry

    def lock_for(self, concrete: str) -> asyncio.Lock:
        return self._locks.setdefault(concrete, asyncio.Lock())

    def keys(self, phase: str, params: dict | None = None, slot: BoundSlot | None = None) -> dict:
        keys = dict(self.datapack.context)
        keys.update({
            "run": {"id": self.run_id, "dir": str(self.bag), "priority": self.priority},
            "phase": {"id": phase},
            "slots": {c.id: c.keys() for c in self.bound.values()},
            "stage_slots": {r: {"id": r, "stage": s} for r, s in self.stage_slot_binding.items()},
            "params": params or {},
            "succeeded": lambda step: self.verdict_of(phase, step) == SUCCEEDED,
            "failed": lambda step: self.verdict_of(phase, step) == FAILED,
            "errored": lambda step: self.verdict_of(phase, step) == ERROR,
            "skipped": lambda step: self.verdict_of(phase, step) == SKIPPED,
            "verdict": lambda step: self.verdict_of(phase, step),
        })
        if self.lab is not None:
            keys.update(self.lab.lab_keys(slot.concrete if slot else None, slot.stage if slot else None))
        if slot is not None:
            keys["slot"] = slot.keys()
            keys["component"] = slot.component_keys()
            keys["remote_root"] = slot.root
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

def _size(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GiB"


class StepRunner:
    """Executes one flat step of a phase, for one slot or none."""

    def __init__(self, ctx: RunContext, phase: FlatPhase, fs: FlatStep, slot: BoundSlot | None,
                 record: StepRecord):
        self.ctx, self.phase, self.fs, self.step, self.slot, self.record = ctx, phase, fs, fs.step, slot, record
        self.phase_id = phase.phase.id
        log_dir = ctx.bag / "logs" / self.phase_id
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / (fs.id + (f"[{slot.id}]" if slot else "") + ".log")
        record.log = self.log_path.relative_to(ctx.bag).as_posix()
        self._log = open(self.log_path, "ab")

    def write(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode()
        self._log.write(data)
        self._log.flush()
        self.ctx.notify("log", {"run": self.ctx.run_id, "step": self.record.key, "text": data.decode("utf-8", "replace")})

    def note(self, text: str) -> None:
        """A line of the sequencer in the step log; a text of several lines (a command) keeps its lines there, each
        a note too, indented after the first."""
        stamp = f"[tsq {now()}] "
        first, *rest = str(text).split("\n")
        self.write(stamp + first + "\n" + "".join(f"{stamp}  {line}\n" for line in rest))

    def close(self) -> None:
        self._log.close()

    def upload_progress(self):
        """What an upload tells of its progress: a line in the step log at each tenth, the step's ``progress``
        (the Run page's bar) at most each half second."""
        started, state = time.monotonic(), {"tenth": -1, "told": 0.0}

        def told(sent: int, total: int) -> None:
            tenth = 10 if total <= 0 else min(10, sent * 10 // total)
            now_ = time.monotonic()
            if tenth > state["tenth"]:
                state["tenth"] = tenth
                rate = sent / (now_ - started) if now_ > started and sent else 0
                self.note(f"sent {_size(sent)} of {_size(total)} ({tenth * 10}%)" + (f", {_size(rate)}/s" if rate else ""))
            if now_ - state["told"] >= 0.5 or sent >= total:
                state["told"] = now_
                self.record.progress = {"sent": sent, "total": total}
                self.ctx.notify("step", {"run": self.ctx.run_id, **self.record.as_dict()})
        return told

    @property
    def pidfile(self) -> str | None:
        if self.slot is None:
            return None
        return f"{self.slot.pid_dir(self.ctx.run_id)}/{self.fs.id}.pid"

    def keys(self) -> dict:
        return self.ctx.keys(self.phase_id, self.fs.params, self.slot)

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
            elif action == "python":
                spec = {"python": self.step.python}
            elif action == "http":
                spec = {"http": self.step.http}
            elif action == "exploit":
                if self.step.with_:
                    keys = exploit_keys(f"step '{self.fs.id}'", self.step.with_, keys)
                return await self.exploit(render(self.step.exploit, keys, self.fs.id), keys, timeout)
            elif action == "manual":
                return await self.manual(render(self.step.manual, keys, self.fs.id), timeout)
            elif action == "do":
                spec = self.command(self.step.do)
                keys = self.command_keys(spec, keys)
                spec = without_params(spec)
            elif action in ("push", "pull"):
                return await self.transfer(action, render(getattr(self.step, action), keys, self.fs.id))
            else:
                fn = registry.step_types[action.lower()]
                outcome = await fn(self, render(self.step.plugin_actions[action], keys, self.fs.id))
                return outcome if isinstance(outcome, tuple) else ((SUCCEEDED, "") if outcome else (FAILED, "plugin step failed"))
            verdict, reason = await self.command_spec(spec, keys, timeout)
            if action == "do" and self.step.do == "collect" and verdict == SUCCEEDED:
                return self.check_outputs()
            return verdict, reason
        except SequencerError as e:
            self.note(f"error: {e}")
            return ERROR, str(e)

    def command(self, name: str) -> dict:
        if self.slot is None:
            raise SequencerError(f"step '{self.fs.id}': 'do: {name}' needs a slot")
        spec = self.slot.resolved.commands.get(name)
        self.record.lab_command = {"name": name, "component": self.slot.concrete.id,
                                   "source": self.slot.resolved.command_sources.get(name, "") if spec is not None else ""}
        if spec is None:
            raise SequencerError(f"slot '{self.slot.id}' (lab component {self.slot.concrete.id}, role {self.slot.concrete.role}) has no "
                                 f"command '{name}' (has: {', '.join(sorted(self.slot.resolved.commands)) or 'none'})")
        # said in its log first, so it reads which command of the lab ran, on which component, and where it is defined
        self.note(f"lab command '{name}' of {self.slot.concrete.id} (slot {self.slot.id}, role {self.slot.concrete.role}, "
                  f"from {self.slot.resolved.command_sources.get(name, '?')})")
        return dict(without_comments(spec))           # its comments are notes for the people: never run

    def command_keys(self, spec: dict, keys: dict) -> dict:
        """The keys a lab command runs with: those of the step, its ``params`` being the command's parameters only
        (the defaults of its implementation, then the step's ``with:``), never the ones of a sub-pipeline."""
        name = self.step.do
        declared = self.slot.resolved.command_params.get(name, {})
        values = command_params(f"step '{self.fs.id}'", name, self.step.with_, declared, default_params(spec, name), keys)
        if values:
            self.note("parameters: " + ", ".join(f"{k}={v}" for k, v in values.items()))
            if self.record.lab_command is not None:
                self.record.lab_command["params"] = {k: _plain(v) for k, v in values.items()}
        return {**keys, PARAMS: values}

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
        if "python" in spec:
            return await self.python(spec, keys, timeout)
        if "http" in spec:
            return await self.http(spec, keys, timeout)
        if "run" in spec:
            on = None
            if spec.get("via"):                        # on another component: one in a sub-network behind it
                on = self.via_place(str(spec["via"]))
                via = on[0]
                keys = {**keys, "via": {"id": via.id, "host": via.host, "host_alias": via.host_alias, "role": via.role, "label": via.label,
                                        "vars": via.vars}}
            command = render(spec["run"], keys, self.fs.id)
            env = {**render(spec.get("env") or {}, keys, self.fs.id), **render(step.env, keys, self.fs.id)}
            detach = step.detach if step.detach is not None else bool(spec.get("detach", False))
            expect = step.expect if step.expect is not None else spec.get("expect")
            if on is not None:
                via, transport, root = on
                pidfile = f"{root}/.tsq/{self.ctx.run_id}/{self.slot.concrete.id}/{self.fs.id}.pid"
                self.note(f"through {via.id} ({via.host}), for {self.slot.concrete.id}")
                return await self.shell(str(command), env, timeout, detach, expect, (transport, pidfile))
            return await self.shell(str(command), env, timeout, detach, expect)
        for name, value in spec.items():
            if name.lower() in registry.step_types:
                outcome = await registry.step_types[name.lower()](self, render(value, keys, self.fs.id))
                return outcome if isinstance(outcome, tuple) else ((SUCCEEDED, "") if outcome else (FAILED, "plugin step failed"))
        raise SequencerError(f"step '{self.fs.id}': command {spec} has no 'run', 'python', 'http', 'wait_for', "
                             f"'signal' or known step type")

    async def http(self, spec: dict, keys: dict, timeout: float | None) -> tuple[str, str]:
        """An HTTP request (README section 7.4): from the sequencer host, or from the component ``via`` names
        (with curl there). Succeeded when the answer is the expected one (default: a status below 400)."""
        from testsuit.sequencer import httpcall
        where = f"step '{self.fs.id}'"
        request = httpcall.normalize(render(spec["http"], keys, self.fs.id), where)
        transport = None
        if spec.get("via"):
            via, transport, _ = self.via_place(str(render(spec["via"], keys, self.fs.id)))
            self.note(f"through {via.id} ({via.host}), with curl")
        # the request's own timeout when it has one, else the command's or step's, else 30 s
        limit = httpcall.timeout_of(request) if request.get("timeout") is not None or timeout is None else timeout
        self.note(f"HTTP {httpcall.describe(request)} (timeout {limit:g} s)")
        progress = self.upload_progress() if "body_file" in request else None
        try:
            answer = await httpcall.request(request, transport, limit, self.ctx.bag, progress)
        finally:
            if self.record.progress is not None:
                self.record.progress = None          # the step's end tells the Run page
        if answer.status is None:
            self.note(f"no answer: {answer.error}")
            return ERROR, f"no answer: {answer.error}"
        if request.get("extract") is not None:
            self.note(f"extract {request['extract']}" + (f": {answer.extract_error}" if answer.extract_error else ""))
        self.write(answer.body + ("" if answer.body.endswith("\n") or not answer.body else "\n"))
        if request.get("save"):
            name = str(request["save"])
            folder = self.ctx.bag / "data" / (self.slot.id if self.slot else "_sequencer") / "http"
            target = (folder / name).resolve()
            if not target.is_relative_to(folder.resolve()):
                raise SequencerError(f"{where}: 'http.save' must stay in its folder ('{name}')")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(answer.body if answer.raw is None else answer.raw, encoding="utf-8")   # the whole answer
            self.note(f"answer saved in {target.relative_to(self.ctx.bag).as_posix()}")
        ok, why = httpcall.judge(request, answer)
        self.note(why)
        return (SUCCEEDED, "") if ok else (FAILED, why)

    async def python(self, spec: dict, keys: dict, timeout: float | None) -> tuple[str, str]:
        """A Python script (README section 8.3, SQ-32): the step's keys in a JSON file named by TSQ_KEYS, run
        through a wrapper that turns an uncaught exception into exit status 70, judged like a shell command."""
        from testsuit.sequencer.model import check_python
        check_python(spec["python"], f"step '{self.fs.id}'")
        py = dict(spec["python"])
        code = py.pop("code", None)                # inline code is Python, not a template
        py = render(py, keys, self.fs.id)
        local = self.ctx.bag / ".tsq" / self.ctx.run_id / "python" / (self.fs.id + (f"[{self.slot.id}]" if self.slot else ""))
        local.mkdir(parents=True, exist_ok=True)
        (local / "keys.json").write_text(json.dumps(_plain(keys), indent=1, default=str), encoding="utf-8")
        (local / "tsq_python.py").write_text(PYTHON_WRAPPER, encoding="utf-8")
        if code is not None:
            (local / "step.py").write_text(str(code), encoding="utf-8")
        if self.slot is None:                 # on the sequencer host: the daemon's interpreter by default
            folder = str(local)
            interpreter = str(py.get("interpreter") or sys.executable)
        else:                                      # on the component: next to the step's pid file
            folder = f"{self.slot.pid_dir(self.ctx.run_id)}/python/{local.name}"
            try:
                await self.slot.transport.put(local, folder)
            except Exception as e:
                return ERROR, f"transport: {e}"
            interpreter = str(py.get("interpreter") or self.slot.concrete.vars.get("tsq_python") or "python3")
        script = f"{folder}/step.py" if code is not None else str(py["script"])
        command = " ".join(shlex.quote(a) for a in [interpreter, f"{folder}/tsq_python.py", script,
                                                    *[str(a) for a in py.get("args") or []]])
        env = {**render(spec.get("env") or {}, keys, self.fs.id), **render(self.step.env, keys, self.fs.id),
               "TSQ_KEYS": f"{folder}/keys.json"}
        detach = self.step.detach if self.step.detach is not None else bool(spec.get("detach", False))
        expect = self.step.expect if self.step.expect is not None else spec.get("expect")
        verdict, reason = await self.shell(command, env, timeout, detach, expect)
        if verdict == FAILED and self.record.exit_status == PYTHON_CRASH:
            self.note("the script raised an exception: error, not failed")
            return ERROR, "python exception"
        return verdict, reason

    def check_outputs(self) -> tuple[str, str]:
        """After a collect: the outputs the slot's role foresees are there (SPEC section 4.2). A mandatory one
        missing fails the step; an optional one is only noted."""
        from testsuit.sequencer.library import missing_outputs
        lab = self.ctx.lab
        if self.slot is None or lab is None:
            return SUCCEEDED, ""
        try:
            outputs = lab.library.role_outputs(self.slot.concrete.role)
        except (SequencerError, KeyError):
            return SUCCEEDED, ""
        if not outputs:
            return SUCCEEDED, ""
        folder = self.ctx.bag / "data" / self.slot.id / "output"
        absent = missing_outputs([{**o, "optional": False} for o in outputs], folder)
        optional = [o["name"] for o in outputs if o["optional"] and o["name"] in absent]
        missing = [n for n in absent if n not in optional]
        if optional:
            self.note(f"optional output(s) of role {self.slot.concrete.role} not collected: {', '.join(optional)}")
        if missing:
            self.note(f"expected output(s) of role {self.slot.concrete.role} not collected: {', '.join(missing)}")
            return FAILED, f"expected output(s) not collected: {', '.join(missing)}"
        self.note(f"the {len(outputs) - len(optional)} output(s) role {self.slot.concrete.role} foresees are collected")
        return SUCCEEDED, ""

    async def exploit(self, spec: dict, keys: dict, timeout: float | None) -> tuple[str, str]:
        """Criteria checked on outputs of the run (SPEC section 6.8): exploit_runner on the sequencer host, on the
        criteria the datapack holds and the files of the run folder the data entries match. Succeeded when every
        check is met, failed when one is not, error when exploit_runner could not run them."""
        from testsuit.sequencer import exploitstep as xs
        xs.check_exploit(spec, f"step '{self.fs.id}'")
        criteria = [str(c) for c in xs.as_list(spec["criteria"])]
        folders = [self.ctx.bag / "definition" / xs.definition_target(c) for c in criteria]
        absent = [c for c, f in zip(criteria, folders) if not f.exists()]
        if absent:
            self.note(f"criteria not in the datapack: {', '.join(absent)}")
            return ERROR, f"criteria not in the datapack: {', '.join(absent)}"
        unread = [f"{c} ({file}): {key}" for c, f in zip(criteria, folders) for file, key in criteria_missing_keys(f, keys)]
        if unread:
            self.note("criteria read keys nothing defines (give them in the step's 'with', as params.*): " + "; ".join(unread))
            return ERROR, "criteria read undefined keys: " + "; ".join(unread)
        data, missing = xs.data_paths(self.ctx.bag, [str(d) for d in xs.as_list(spec["data"])], list(self.ctx.bound))
        for entry in missing:
            self.note(f"no file of the run matches '{entry}'")
        if not data:
            return ERROR, "no data to check: " + ", ".join(missing)
        work = self.ctx.bag / xs.RUN_FOLDER / self.fs.id
        if work.exists():                              # a retry starts again
            shutil.rmtree(work)
        work.mkdir(parents=True)
        sessions = xs.session_names(criteria)
        title = str(spec.get("name") or f"{keys.get('testdef', {}).get('name') or self.ctx.datapack.name} - {self.fs.id}")
        config = xs.write_config(work, title, str(spec.get("description") or ""), folders, sessions, data, keys)
        self.note("criteria: " + ", ".join(criteria))
        self.note("data: " + ", ".join(p.relative_to(self.ctx.bag).as_posix() for p in data))
        verdict, reason = await self.shell(xs.command(config, work), dict(render(self.step.env, keys, self.fs.id)),
                                           timeout, False, {"exit": ["any"]})
        self.record.exploit = xs.read_checks(work, sessions, criteria)
        if verdict != SUCCEEDED:                       # did not complete: crash, timeout, cancel
            return verdict, reason
        checks = self.record.exploit.get("checks") or []
        bad = list(xs.iter_bad(checks))
        status = self.record.exploit.get("report") and f", report {self.record.exploit['report']}" or ""
        if self.record.exit_status == 0:
            self.note(f"{len(checks)} check(s) met{status}")
            return SUCCEEDED, ""
        if self.record.exit_status == xs.EXIT_FAILED:
            for c in bad:
                self.note(f"{c['status']}: {c['criteria']} {c['check'] or c['scenario']}")
            return FAILED, f"{len(bad)} of {len(checks)} check(s) not met"
        self.note(f"exploit_runner ended with exit status {self.record.exit_status}{status}")
        return ERROR, f"exploit_runner exit status {self.record.exit_status}"

    async def manual(self, spec: dict, timeout: float | None) -> tuple[str, str]:
        """An operation a person does on the bench (SPEC section 6.9): wait for the operator's answer on the Run
        page (POST /runs/<id>/manual/<step>): done (succeeded), failed, or no answer within the timeout (error)."""
        from testsuit.sequencer import manualstep as ms
        ms.check_manual(spec, f"step '{self.fs.id}'")
        self.record.manual = ms.waiting_record(self.fs.id, spec)
        checks = self.record.manual["checks"]
        self.note(f"waiting for a manual operation: {self.record.manual['title']}"
                  + "".join(f"\n[ ] {c['text']}" for c in checks))
        answer: asyncio.Future = asyncio.get_running_loop().create_future()
        self.ctx.manual_waits[self.record.key] = answer
        self.ctx.notify("step", {"run": self.ctx.run_id, **self.record.as_dict()})
        self.ctx.notify("manual_operation", {"run": self.ctx.run_id, "step": self.record.key, "title": self.record.manual["title"],
                                   "state": "waiting"})
        try:
            outcome = await asyncio.wait_for(answer, timeout)
        except asyncio.TimeoutError:
            self.record.manual.update(outcome="timeout", at=now())
            self.note("no answer of the operator in time")
            return ERROR, "manual operation: no answer in time"
        finally:
            self.ctx.manual_waits.pop(self.record.key, None)
            self.ctx.notify("manual_operation", {"run": self.ctx.run_id, "step": self.record.key, "state": "answered"})
        m = self.record.manual
        who = f" by {m['by']}" if m.get("by") else ""

        def line(c: dict) -> str:
            mark = "x" if c.get("done") else ("bypassed" if c.get("bypassed") else " ")
            return (f"\n[{mark}] {c['text']}" + (f" ({c['by']})" if c.get("by") else "")
                    + (f": {c['comment']}" if c.get("comment") else ""))
        self.note(f"manual operation {outcome}{who}" + "".join(line(c) for c in checks)
                  + (f"\ncomment: {m['note']}" if m.get("note") else ""))
        if outcome == ms.DONE:
            skipped = ms.bypassed(m)
            return SUCCEEDED, (f"{len(skipped)} check(s) bypassed: " + "; ".join(
                f"{c['text']} ({c['comment']})" for c in skipped) if skipped else "")
        return FAILED, "manual operation failed" + (f": {m['note']}" if m.get("note") else "")

    def via_place(self, name: str) -> tuple:
        """The component a command's via names, its transport and remote root (bound with the component)."""
        comp = self.slot
        if comp is None:
            raise SequencerError(f"step '{self.fs.id}': 'via' needs a slot")
        lab = self.ctx.lab
        if lab is None:
            raise SequencerError(f"step '{self.fs.id}': 'via' needs the lab")
        via = lab.find_via(comp.concrete, comp.stage, name, f"step '{self.fs.id}': its command goes via '{name}'")
        if via.id not in comp.vias:
            raise SequencerError(f"step '{self.fs.id}': {via.id} is not bound for {comp.concrete.id}: 'via' is for "
                                 "the commands of lab components")
        self.record.via = via.id                   # shown with the step: it does not run on its component
        return comp.vias[via.id]

    def transport(self) -> Transport:
        if self.slot is None:
            raise SequencerError(f"step '{self.fs.id}' needs a slot")
        return self.slot.transport

    async def shell(self, command: str, env: dict, timeout: float | None, detach: bool, expect: dict | None,
                    on: tuple[Transport, str] | None = None):
        """on: the transport and pid file to run it with, for a command run through another component."""
        before = ""
        if on is not None:
            transport, pidfile = on
        elif self.slot is None:                 # no component: on the sequencer host, in the run folder
            from testsuit.sequencer.inventory import Component as _Component
            from testsuit.sequencer.transports.local import LocalTransport
            transport = LocalTransport(_Component("sequencer", {}), str(self.ctx.bag))
            pidfile = str(self.ctx.bag / ".tsq" / self.ctx.run_id / f"{self.fs.id}.pid")
            before = f"cd {shlex.quote(str(self.ctx.bag))}"
        else:
            transport, pidfile = self.slot.transport, self.pidfile
        self.note(f"{'start (detached)' if detach else 'run'} on {transport.host}: {command}")
        script = strict(traced(command, before))            # its log: each line it runs, then what it prints
        if detach:
            try:
                process = await transport.start(script, env=env, log=self.write, pidfile=pidfile)
            except Exception as e:
                return ERROR, f"transport: {e}"
            try:
                result = await process.wait(DETACH_SETTLE)
            except asyncio.TimeoutError:
                self.ctx.detached.append(process)
                return SUCCEEDED, "started"
            return self.judge(result, expect)
        result = await transport.run(script, env=env, timeout=timeout, log=self.write, pidfile=pidfile)
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
            if expect.get(stream) and not re.search(str(expect[stream]), untraced(result.output), re.MULTILINE):
                self.note(f"output does not match '{expect[stream]}'")
                return FAILED, f"{stream} does not match"
        self.note(f"exit status {result.exit_status}")
        return SUCCEEDED, ""

    async def builtin(self, name: str, timeout: float | None) -> tuple[str, str]:
        comp = self.slot
        if comp is None:
            raise SequencerError(f"built-in command '{name}' needs a slot")
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
            self.note(f"kill every process started by run {self.ctx.run_id} on {comp.transport.host}"
                      + "".join(f" and {t.host}" for t, _ in comp.pid_places(self.ctx.run_id)[1:]))
            try:
                places = comp.pid_places(self.ctx.run_id)
                for i, (transport, folder) in enumerate(places):
                    try:
                        await transport.signal_dir(folder, "KILL")
                    except Exception:
                        if not (i == 0 and comp.vias):  # reached through another component only
                            raise
            except Exception as e:
                return ERROR, f"transport: {e}"
            self.ctx.expect_state(comp.concrete.id, "idle")
            return SUCCEEDED, ""
        raise SequencerError(f"unknown built-in command '{name}'")

    def apply_collect_filters(self, target: Path) -> None:
        import fnmatch
        rules = self.slot.resolved.collect or {}
        include, exclude = rules.get("include"), rules.get("exclude") or []
        for path in [p for p in target.rglob("*") if p.is_file()]:
            rel = path.relative_to(target).as_posix()
            if (include and not any(fnmatch.fnmatch(rel, pat) for pat in include)) or \
                    any(fnmatch.fnmatch(rel, pat) for pat in exclude):
                path.unlink()

    async def signal(self, sig: str, grace: float) -> tuple[str, str]:
        comp = self.slot
        if comp is None:
            raise SequencerError("'signal' needs a slot")
        places = comp.pid_places(self.ctx.run_id)
        sig = sig.upper().removeprefix("SIG")
        self.note(f"signal {sig} to the processes of {comp.id} on " + " and ".join(t.host for t, _ in places))
        if comp.vias:                                 # reached through another component: its own host may not answer
            reachable = []
            for i, (transport, folder) in enumerate(places):
                try:
                    await transport.alive_in(folder)
                    reachable.append((transport, folder))
                except Exception:
                    if i:
                        raise
            places = reachable
        try:
            for transport, folder in places:
                await transport.signal_dir(folder, sig)
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                if not any([await transport.alive_in(folder) for transport, folder in places]):
                    break
                await asyncio.sleep(0.2)
            else:
                self.note(f"still alive after {grace:g}s: KILL")
                for transport, folder in places:
                    await transport.signal_dir(folder, "KILL")
        except Exception as e:
            return ERROR, f"transport: {e}"
        self.ctx.expect_state(comp.concrete.id, "idle")
        return SUCCEEDED, ""

    async def transfer(self, action: str, spec: dict) -> tuple[str, str]:
        comp = self.slot
        if comp is None or not isinstance(spec, dict) or "from" not in spec or "to" not in spec:
            raise SequencerError(f"step '{self.fs.id}': '{action}' needs a slot, and 'from' and 'to'")
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
        target = self.slot
        if "component" in spec:
            raise SequencerError("wait_for: 'component' is now 'slot'")
        if "slot" in spec:
            name = spec.pop("slot")
            if name not in self.ctx.bound:
                raise SequencerError(f"wait_for: unknown slot '{name}'")
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

    def ready_check(self, kind: str, value: Any, target: BoundSlot | None):
        def need_target():
            if target is None:
                raise SequencerError(f"wait_for {kind} needs a slot")
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
            from testsuit.sequencer import httpcall
            spec = httpcall.normalize(value, f"step '{self.fs.id}': wait_for")

            async def check():
                answer = await httpcall.request(spec, None, min(httpcall.timeout_of(spec, 3), 10), self.ctx.bag)
                return httpcall.judge(spec, answer)
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
                result = await comp.transport.shell(strict(command), timeout=10)
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
    held = phase_slots(flat.phase, ctx.datapack.needs)
    if step.slot:
        name = render(step.slot, keys, fs.id)
        return [name]
    if step.for_each is None:
        return [None]
    value = render(step.for_each, keys, fs.id)
    if value == "slots":
        return list(held)
    if isinstance(value, str):
        by_role = [c for c in held if ctx.datapack.needs.slots[c].role == value]
        if not by_role:
            raise SequencerError(f"step '{fs.id}': for_each '{value}' matches no slot of the phase")
        return by_role
    if not isinstance(value, list):
        raise SequencerError(f"step '{fs.id}': for_each must be a list, 'slots' or a role")
    outside = [c for c in value if c not in held]
    if outside:
        raise SequencerError(f"step '{fs.id}': slot(s) {outside} outside the phase")
    return list(value)


class PhaseRunner:

    def __init__(self, ctx: RunContext, flat: FlatPhase):
        self.ctx, self.flat = ctx, flat
        self.id = flat.phase.id

    def record(self, fs: FlatStep, slot: str | None) -> StepRecord:
        rec = StepRecord(phase=self.id, id=fs.id, slot=slot or "")
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
            if fs.step.goes_on and verdict != SUCCEEDED:
                verdict = SUCCEEDED
            verdicts[fs.id] = verdict
            if not in_finally and not self.ctx.abort_mode and self.pauses_after(fs.id):
                await self.pause(fs, verdict)
        except asyncio.CancelledError:
            verdicts.setdefault(fs.id, ERROR)
            raise
        except SequencerError as e:
            rec = self.record(fs, None)
            self.set_state(rec, ERROR, str(e))
            verdicts[fs.id] = ERROR
        except Exception as e:                     # a sequencer bug: the step is in error, the run goes on
            log.exception(f"run {self.ctx.run_id}: step {self.id}:{fs.id}")
            rec = self.record(fs, None)
            self.set_state(rec, ERROR, f"internal error: {type(e).__name__}: {e}")
            verdicts[fs.id] = ERROR
        finally:
            events[fs.id].set()

    async def run_target(self, fs: FlatStep, target: str | None) -> str:
        component = None
        if target is not None:
            if target not in self.ctx.bound:
                rec = self.record(fs, target)
                self.set_state(rec, ERROR, f"slot '{target}' is not bound")
                return ERROR
            component = self.ctx.bound[target]
        rec = self.record(fs, target)
        rec.passes = self.ctx.passes.get(f"{self.id}:{fs.id}", 1)
        runner = StepRunner(self.ctx, self.flat, fs, component, rec)
        if rec.passes > 1:
            runner.note(f"run again by an operator: pass {rec.passes}")
        uses_component = component is not None and fs.step.action in ("do", "run", "python", "push", "pull")
        lock = self.ctx.lock_for(component.concrete.id) if uses_component and component.concrete.lock != "none" else None
        try:
            self.set_state(rec, "ready")
            if lock is not None:
                await lock.acquire()
            self.set_state(rec, "running")
            execution = asyncio.ensure_future(runner.execute())
            self.ctx.step_tasks[rec.key] = execution
            try:
                verdict, reason = await execution
            except asyncio.CancelledError:
                why = self.ctx.forced.pop(rec.key, None)
                if why is None:
                    raise
                runner.note(f"continued anyway by an operator: {why}")
                verdict, reason = SUCCEEDED, f"continued anyway: {why}"
            except SequencerError:
                raise
            except Exception as e:                 # a sequencer bug: the step ends in error, never stays running
                log.exception(f"run {self.ctx.run_id}: step {rec.key}")
                runner.note(f"internal error: {type(e).__name__}: {e}")
                verdict, reason = ERROR, f"internal error: {type(e).__name__}: {e}"
            self.set_state(rec, verdict, reason)
            return verdict
        except asyncio.CancelledError:
            runner.note("cancelled")
            self.set_state(rec, ERROR, "killed" if self.ctx.abort_mode == "kill" else
                           ("cancelled" if self.ctx.abort_mode else "timeout"))
            raise
        except SequencerError as e:
            runner.note(f"error: {e}")
            self.set_state(rec, ERROR, str(e))
            return ERROR
        finally:
            self.ctx.step_tasks.pop(rec.key, None)
            if lock is not None and lock.locked():
                lock.release()
            runner.close()

    async def run_dag(self, steps: list[FlatStep], in_finally: bool) -> dict[str, str]:
        verdicts: dict[str, str] = {}
        if in_finally:                     # finally steps without needs run in the order written
            steps = finally_order(steps)
        events = {fs.id: asyncio.Event() for fs in steps}
        tasks = {fs.id: asyncio.ensure_future(self.run_step(fs, events, verdicts, in_finally)) for fs in steps}
        if not in_finally:                 # the operator controls act on these (rewind, force): run_again replaces tasks
            self.tasks, self.events, self.verdicts = tasks, events, verdicts
            self.ctx.phase_runner = self
        self.ctx._running[self.id + (":finally" if in_finally else "")] = asyncio.current_task()
        try:
            while True:
                pending = [t for t in tasks.values() if not t.done()]
                if not pending:
                    break
                await asyncio.wait(pending)
        except asyncio.CancelledError:
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            for fs in steps:
                verdicts.setdefault(fs.id, ERROR)
            raise
        finally:
            if not in_finally and self.ctx.phase_runner is self:
                self.ctx.phase_runner = None
                for pause in list(self.ctx.pauses.values()):
                    if pause["phase"] == self.id and not pause["future"].done():
                        pause["future"].set_result("next")
        return verdicts

    # ── operator controls (SPEC section 9.7) ────────────────────────────
    def step_of(self, step_id: str) -> FlatStep:
        found = next((fs for fs in self.flat.steps if fs.id == step_id), None)
        if found is None:
            raise SequencerError(f"phase '{self.id}' has no step '{step_id}' (finally steps cannot be run again)")
        return found

    def dependants(self, step_id: str) -> list[str]:
        """The steps that need this one, directly or not, in the order written."""
        found: set[str] = set()
        todo = [step_id]
        while todo:
            current = todo.pop()
            for fs in self.flat.steps:
                if current in fs.needs and fs.id not in found:
                    found.add(fs.id)
                    todo.append(fs.id)
        return [fs.id for fs in self.flat.steps if fs.id in found]

    def pauses_after(self, step_id: str) -> bool:
        mode = self.ctx.step_mode or {}
        if mode.get("mode") == "each":
            return True
        points = mode.get("breakpoints") or []
        return mode.get("mode") == "breakpoints" and (f"{self.id}:{step_id}" in points or step_id in points)

    async def pause(self, fs: FlatStep, verdict: str) -> None:
        """Step-by-step mode: wait after a step for the operator's Next (its dependants wait too)."""
        key = f"{self.id}:{fs.id}"
        future = asyncio.get_running_loop().create_future()
        self.ctx.pauses[key] = {"phase": self.id, "step": fs.id, "verdict": verdict, "since": now(), "future": future}
        self.ctx.notify("pause", {"run": self.ctx.run_id, "step": key, "verdict": verdict, "state": "paused"})
        try:
            await future
        finally:
            if self.ctx.pauses.get(key, {}).get("future") is future:
                del self.ctx.pauses[key]
            self.ctx.notify("pause", {"run": self.ctx.run_id, "step": key, "state": "resumed"})

    def resume(self, step_id: str | None = None) -> list[str]:
        """Let paused steps go on (one, or all of this phase)."""
        done = []
        for key, pause in list(self.ctx.pauses.items()):
            if pause["phase"] == self.id and (step_id is None or pause["step"] == step_id) and not pause["future"].done():
                pause["future"].set_result("next")
                done.append(key)
        return done

    async def rewind(self, step_id: str, keep_self: bool = False) -> list[str]:
        """Run a step of the running phase again, and every step that needs it: those still running are stopped,
        their records kept in the run's history, and they run anew (pass n+1). keep_self: its dependants only."""
        if self.ctx.phase_runner is not self or self.ctx.abort_mode:
            raise SequencerError(f"phase '{self.id}' is not running its steps any more")
        self.step_of(step_id)
        ids = ([] if keep_self else [step_id]) + self.dependants(step_id)
        if not ids:
            return []
        # held in the steps' tasks while the steps are replaced, so that the phase does not end in between
        guard = asyncio.get_running_loop().create_future()
        self.tasks[f"~rewind {id(guard)}"] = guard
        old = [self.tasks[i] for i in ids if i in self.tasks and not self.tasks[i].done()]
        # the records of the steps stopped on the way: kept as stopped by the operator, not as failed by the stop
        stopped = {key for key, rec in self.ctx.records.items()
                   if rec.phase == self.id and rec.id in ids and rec.state in ("ready", "running")}
        for task in old:
            task.cancel()
        try:
            await asyncio.gather(*old, return_exceptions=True)
        except BaseException:
            guard.set_result(None)
            raise
        ran = set()
        for key, rec in list(self.ctx.records.items()):
            if rec.phase == self.id and rec.id in ids:
                entry = {**rec.as_dict(), "replaced_at": now()}
                if key in stopped:
                    entry.update(state="cancelled", reason="stopped by an operator, to run again")
                self.ctx.history.append(entry)
                if rec.started or rec.state not in ("pending", "skipped"):
                    ran.add(rec.id)
                del self.ctx.records[key]
        for i in ids:
            self.verdicts.pop(i, None)
            self.events[i] = asyncio.Event()
            if i in ran:                                 # a step that had not started yet runs its first pass
                self.ctx.passes[f"{self.id}:{i}"] = self.ctx.passes.get(f"{self.id}:{i}", 1) + 1
        for i in ids:
            self.tasks[i] = asyncio.ensure_future(self.run_step(self.step_of(i), self.events, self.verdicts, False))
        guard.set_result(None)
        del self.tasks[f"~rewind {id(guard)}"]
        self.ctx.notify("phase", {"run": self.ctx.run_id, "phase": self.id, "state": "running", "run_again": ids})
        return ids

    async def force(self, step_id: str, reason: str) -> str:
        """Continue anyway: a running step ends succeeded now; a step that ended without success counts as
        succeeded, and the steps that need it run (again)."""
        self.step_of(step_id)
        running = [r for r in self.ctx.records.values() if r.phase == self.id and r.id == step_id
                   and r.state in ("ready", "running")]
        if running:
            for rec in running:
                self.ctx.forced[rec.key] = reason
                task = self.ctx.step_tasks.get(rec.key)
                if task is not None:
                    task.cancel()
            return "running"
        records = [r for r in self.ctx.records.values() if r.phase == self.id and r.id == step_id]
        if not records:
            raise SequencerError(f"step '{self.id}:{step_id}' has not started: nothing to continue past yet")
        if self.verdicts.get(step_id) == SUCCEEDED and all(r.state == SUCCEEDED for r in records):
            raise SequencerError(f"step '{self.id}:{step_id}' succeeded already")
        for rec in records:
            if rec.state != SUCCEEDED:
                self.set_state(rec, SUCCEEDED, f"continued anyway: {reason} (was {rec.state}: {rec.reason})")
        self.verdicts[step_id] = SUCCEEDED
        paused = self.resume(step_id)
        if not paused and self.tasks.get(step_id) is not None and self.tasks[step_id].done():
            await self.rewind(step_id, keep_self=True)       # the steps it held back run now
        return "ended"

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
            # a task of its own, so that a kill sent while they run stops finally steps that hang (a cancel lets them run)
            task = asyncio.ensure_future(self.run_dag(self.flat.finally_, True))
            self.ctx.finally_task = task
            try:
                final = await asyncio.wait_for(task, timeout or 3600)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                final = {fs.id: ERROR for fs in self.flat.finally_}
                why = "killed" if self.ctx.abort_mode == "kill" else "timeout"
                for fs in self.flat.finally_:
                    if not any(r.phase == self.id and r.id == fs.id for r in self.ctx.records.values()):
                        self.set_state(self.record(fs, None), SKIPPED, why)
            finally:
                self.ctx.finally_task = None
        step_verdicts = [r.state for r in self.ctx.records.values() if r.phase == self.id
                         and not self._continue_on_error(r.id)]
        verdict = worst(step_verdicts + list(final.values()) + ([ERROR] if main_error else []))
        self.ctx.notify("phase", {"run": self.ctx.run_id, "phase": self.id, "state": "done", "verdict": verdict})
        return verdict

    def _continue_on_error(self, step_id: str) -> bool:
        fs = next((s for s in self.flat.steps + self.flat.finally_ if s.id == step_id), None)
        return bool(fs and fs.step.continue_on_error)
