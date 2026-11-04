"""State monitoring (README section 7.5) and platform self-test (section 7.6).

The monitor runs each component's ``status`` probe periodically (more often for the components of
active runs) and records the state: offline, idle, configured, running, broken, unknown. A state
that differs from the one the engine expects (``configured`` after deploy, ``running`` after
start...) raises an event.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shlex
import time
from typing import Any, Awaitable, Callable

from testsuit.sequencer import registry
from testsuit.sequencer.inventory import Component
from testsuit.sequencer.lab import Lab
from testsuit.sequencer.library import Resolved
from testsuit.sequencer.transports import Transport
from testsuit.sequencer.util import SequencerError, evaluate, now, parse_duration, parse_size, render, wait_event

log = logging.getLogger("testsuit.sequencer")
probe_log = logging.getLogger("testsuit.probe")       # each probe: kept for the Lab page's log only (logbuffer)

STATES = ("offline", "idle", "configured", "running", "broken", "unknown")
HEALTHY_STATES = ("idle", "configured", "running")


def component_keys(comp: Component, root: str, via: Component | None = None, lab: Lab | None = None) -> dict:
    """The keys of a status probe and self-test commands; via: the component they run on (``{{ via.host }}``);
    lab: the lab, for the other components (``{{ stage.roles.<role>.host }}``, ``{{ lab.components.<host> }}``)."""
    stage = lab.inventory.stages[comp.stages[0]] if lab is not None and comp.stages else None
    keys = {"component": {"id": comp.id, "concrete": comp.id, "host": comp.host, "role": comp.role, "extends": comp.extends,
                          "label": comp.label, "vars": comp.vars_in(stage), "remote_root": root,
                          "stage": stage.id if stage else "",
                          "input_dir": f"{root}/{comp.id}/input", "output_dir": f"{root}/{comp.id}/output"},
            "remote_root": root}
    if via is not None:
        keys["via"] = {"id": via.id, "host": via.host, "role": via.role, "label": via.label, "vars": via.vars}
    if lab is not None:
        keys.update(lab.lab_keys(comp, stage))
    return keys


def _match(states: dict, output: str, exit_status: int | None) -> tuple[str | None, dict | None]:
    """The first state whose rule matches the probe's answer, and that rule (None, None: no rule matches)."""
    for state, rule in (states or {}).items():
        if not isinstance(rule, dict):
            continue
        if "regex" in rule and re.search(str(rule["regex"]), output, re.MULTILINE):
            return state, rule
        if "exit" in rule:
            wanted = rule["exit"]
            if wanted == "nonzero" and exit_status not in (None, 0):
                return state, rule
            codes = wanted if isinstance(wanted, list) else [wanted]
            if exit_status is not None and exit_status in [c for c in codes if isinstance(c, int)]:
                return state, rule
    return None, None


def _rule_text(rule: dict) -> str:
    return ", ".join(f"{k} {v!r}" if k == "regex" else f"{k} {v}" for k, v in rule.items())


def _matched(report: dict, states: dict, output: str, exit_status: int | None, answer: str) -> str:
    """The state the probe's answer gives, with why in the report: the rule that matched, or that none did."""
    report["rules"] = {k: v for k, v in (states or {}).items() if isinstance(v, dict)}
    state, rule = _match(states, output, exit_status)
    if state:
        report["matched"] = state
        report["why"] = f"{answer} matches the rule of state '{state}' ({_rule_text(rule)})"
        return state
    report["why"] = (f"{answer} matches none of the rules of its status probe, so the state is unknown"
                     if report["rules"] else f"{answer}, and its status probe gives no rule (states:), so the state is unknown")
    return "unknown"


async def _probe_commands(report: dict, status: dict, probe: dict, keys: dict, transport: Transport, on: Component,
                          comp: Component, via: Component | None) -> tuple[str, str]:
    """A status probe of several commands (``run: [...]``): each runs, whatever the others gave, and is OK when
    it exits with 0. The rules match their outputs, one after the other; ``exit`` rules the first non-zero exit
    status (0 when all are OK)."""
    report["kind"] = "commands"
    timeout = parse_duration(probe.get("timeout")) or 15
    outputs, exits, errors = [], [], []
    for i, item in enumerate(probe["run"]):
        command = str(render(item, keys, f"{comp.id} status"))
        step = {"action": f"check {i + 1} of {len(probe['run'])} on {on.host}" + (f" ({via.id}, for {comp.id})" if via else "")
                + f" (timeout {timeout:g} s)", "command": command}
        report["steps"].append(step)
        result = await transport.shell(command, timeout=timeout)
        step.update(exit=result.exit_status, output=result.output[-4000:], error=result.error or "",
                    ok=result.completed and result.exit_status == 0)
        if result.completed:
            outputs.append(result.output)
            exits.append(result.exit_status)
        else:
            errors.append(result.error or "no answer")
    if not outputs:
        report["why"] = f"none of the status commands completed: {errors[0] if errors else 'no command'}"
        return "offline", errors[0] if errors else ""
    checks = [s for s in report["steps"] if "ok" in s]
    failed = [s for s in checks if not s["ok"]]
    exit_status = next((e for e in exits if e), 0)
    output = "".join(o if o.endswith("\n") else o + "\n" for o in outputs)
    state = _matched(report, status.get("states") or {}, output, exit_status,
                     f"their answers ({len(checks) - len(failed)} OK, {len(failed)} failed"
                     + (f", first non-zero exit status {exit_status}" if exit_status else "") + ")")
    return state, f"{len(failed)} of {len(probe['run'])} checks failed" if failed else "all checks OK"


async def probe_component(comp: Component, resolved: Resolved, transport: Transport,
                          expected: str | None = None, report: dict | None = None,
                          via: Component | None = None, lab: Lab | None = None) -> tuple[str, str]:
    """(state, detail) of a component, from the status probe of its lab component. report gets what was
    tested (steps: each action with its command, answer and exit status), the rules of the probe and the
    one that matched, and why the state is what it is.

    :param via: the component the probe runs on (``status.via``), transport being its own: a component that
        cannot be reached itself, in a sub-network behind the stage server
    """
    report = report if report is not None else {}
    report.setdefault("steps", [])
    steps = report["steps"]
    status = resolved.status
    on = via or comp                                    # where the probe's commands run
    report["host"] = comp.host
    report["transport"] = transport.name
    if via is not None:
        report["via"] = {"id": via.id, "host": via.host, "role": via.role}
    login = {"action": f"log in to {on.host} ({transport.name}) and find the remote root"
                       + (f": {via.id}, which the probe of {comp.id} goes through" if via else "")}
    steps.append(login)
    try:
        root = await transport.resolve_root()
    except Exception as e:
        login["error"] = str(e)
        if via is not None:
            report["why"] = f"the sequencer cannot reach {via.id} ({via.host}), which the probe goes through: {e}"
            return "offline", f"cannot reach {via.id} ({via.host}), the probe goes through it: {e}"
        report["why"] = f"the sequencer cannot reach {comp.host}: {e}"
        return "offline", f"cannot reach {comp.host}: {e}"
    login["output"] = root
    if status is None:
        report["kind"] = "none"
        report["why"] = f"its lab component ({resolved.chain[-2] if len(resolved.chain) > 1 else '?'}) has no status probe"
        return "unknown", "no status probe"
    if status.get("builtin") == "linux_system":
        report["kind"] = "builtin linux_system"
        step = {"action": "run a command", "command": "true"}
        steps.append(step)
        result = await transport.shell("true", timeout=15)
        step.update(exit=result.exit_status, output=result.output.strip()[-2000:], error=result.error or "")
        if not result.completed or result.exit_status:
            report["why"] = ("the command 'true' did not complete: " + (result.error or "no answer") if not result.completed
                             else f"the command 'true' exited with {result.exit_status}: the host does not run commands")
            return "offline", result.error or result.output.strip()
        step = {"action": f"look for the process groups the sequencer started on it ({root}/.tsq/*/{comp.id}/*.pid)"}
        steps.append(step)
        try:
            alive = await transport.alive_in(f"{root}/.tsq", f"*/{comp.id}/*.pid")
        except SequencerError as e:
            step["error"] = str(e)
            report["why"] = f"the process groups could not be listed: {e}"
            return "offline", str(e)
        step["output"] = f"{len(alive)} alive" + (f": {', '.join(map(str, alive))}" if alive else "")
        if alive:
            report["why"] = f"{len(alive)} process group(s) started by the sequencer are alive: running"
            return "running", f"{len(alive)} process group(s) started by the sequencer"
        state = "configured" if expected == "configured" else "idle"
        report["why"] = (f"it answers and runs no process group of the sequencer: {state}"
                         + (" (configured: a deploy was done and nothing runs yet)" if state == "configured" else ""))
        return state, ""
    probe = status.get("probe") or {}
    keys = component_keys(comp, root, via, lab)
    if "run" in probe and isinstance(probe["run"], list):
        return await _probe_commands(report, status, probe, keys, transport, on, comp, via)
    if "run" in probe:
        report["kind"] = "command"
        command = str(render(probe["run"], keys, f"{comp.id} status"))
        timeout = parse_duration(probe.get("timeout")) or 15
        step = {"action": f"run the status command on {on.host}" + (f" ({via.id}, for {comp.id})" if via else "")
                + f" (timeout {timeout:g} s)", "command": command}
        steps.append(step)
        result = await transport.shell(command, timeout=timeout)
        step.update(exit=result.exit_status, output=result.output[-4000:], error=result.error or "",
                    ok=result.completed and result.exit_status == 0)      # OK / FAILED, as each of several checks
        if not result.completed:
            report["why"] = f"the status command did not complete: {result.error or 'no answer'}"
            return "offline", result.error or ""
        state = _matched(report, status.get("states") or {}, result.output, result.exit_status,
                         f"its answer (exit status {result.exit_status})")
        return state, result.output.strip()[-200:]
    for name, spec in probe.items():
        fn = registry.state_probes.get(name.lower())
        if fn is not None:
            report["kind"] = f"probe {name}"
            step = {"action": f"probe {name} on {on.host}" + (f" ({via.id}, for {comp.id})" if via else ""),
                    "command": str(render(spec, keys))}
            steps.append(step)
            output, exit_status = await fn(transport, render(spec, keys), keys)
            step.update(exit=exit_status, output=str(output)[-4000:])
            state = _matched(report, status.get("states") or {}, str(output), exit_status,
                             f"its answer (status {exit_status})")
            return state, str(output)[-200:]
    if "http" in probe:
        import urllib.request
        report["kind"] = "http"
        url = render(probe["http"] if isinstance(probe["http"], str) else probe["http"]["url"], keys)
        step = {"action": "HTTP GET", "command": str(url)}
        steps.append(step)

        def get():
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    return r.read(1 << 16).decode("utf-8", "replace"), 0 if r.status < 400 else r.status
            except Exception as e:
                return str(e), None
        output, code = await asyncio.to_thread(get)
        step.update(exit=code, output=output[-4000:], ok=code == 0)
        if code is None:
            step["error"] = output
            report["why"] = f"the request failed: {output}"
            return "offline", output
        return _matched(report, status.get("states") or {}, output, code, f"its answer (HTTP code {code or '< 400'})"), output[-200:]
    report["why"] = f"unsupported probe {sorted(probe)}"
    return "unknown", f"unsupported probe {sorted(probe)}"


class Monitor:
    """Periodic probes of every lab component; states go to the store and the event bus."""

    def __init__(self, store, interval: float, notify: Callable[[str, dict], None]):
        self.store = store
        self.interval = interval
        self.notify = notify
        self.lab: Lab | None = None
        self.transports: Callable[[str], Transport] | None = None
        self.active: set[str] = set()                 # components of active runs: probed more often
        # the self-test to run when a component becomes idle (set by the sequencer), and when it last ran
        self.on_idle: Callable[[str], Awaitable[Any]] | None = None
        self.idle_retest = 60.0                        # a component kept broken by its self-test: tested again at most this often
        self._tested: dict[str, float] = {}
        self._testing: set[str] = set()
        self._last: dict[str, float] = {}
        self.reports: dict[str, dict] = {}            # the last probe of each component: what was tested, its answer, why
        self.selftests: dict[str, dict] = {}          # the last self-test of each component, with its checks
        self._wake = asyncio.Event()

    def attach(self, lab: Lab, transports: Callable[[str], Transport]) -> None:
        self.lab, self.transports = lab, transports

    def state(self, comp_id: str) -> str:
        res = self.store.resources("component").get(("component", comp_id))
        return (res or {}).get("state") or "unknown"

    async def probe(self, comp_id: str, selftest: bool = False) -> str:
        """Probe a component and record its state. A component whose last self-test failed stays
        'broken' while its probe says it is healthy, until a self-test passes: one runs when it turns
        idle. selftest=True: the probe of a self-test, which gets the probed state as it is."""
        comp = self.lab.inventory.components[comp_id]
        stage = self.lab.inventory.stages[comp.stages[0]] if comp.stages else None
        resolved = self.lab.resolve(comp, stage)
        res = self.store.resources("component").get(("component", comp_id)) or {}
        report: dict = {"component": comp_id, "at": now(), "expected": res.get("expected"), "chain": resolved.chain,
                        "status": resolved.status, "steps": []}
        started = time.monotonic()
        failed = False
        try:
            via = self.lab.via(comp, stage, resolved.status)
            state, detail = await asyncio.wait_for(
                probe_component(comp, resolved, self.transports(via.id if via else comp_id), res.get("expected"),
                                report, via, self.lab), 60)
        except asyncio.TimeoutError:
            state, detail, failed = "offline", "probe timeout (60 s)", True
            report["why"] = "the probe got no answer within 60 s"
        except Exception as e:
            state, detail, failed = "unknown", f"probe failed: {type(e).__name__}: {e}", True
            report["why"] = f"the probe itself failed: {type(e).__name__}: {e}"
        report.update(duration=round(time.monotonic() - started, 3), probed=state)
        # every probe in the log of the Lab page (logger testsuit.probe, not on the console); errors as warnings
        tested = ", ".join(st.get("command") or st["action"] for st in report["steps"][-1:]) or "no probe"
        line = (f"{comp_id}: {tested} -> {state} ({report['duration']:.1f} s)"
                + (f": {detail.strip()}" if detail and detail.strip() else ""))
        probe_log.log(logging.WARNING if failed or state in ("offline", "broken") else logging.DEBUG, line)
        if selftest:
            return state
        probed = state
        failure = res.get("selftest_failed")
        if failure and state in HEALTHY_STATES:
            state, detail = "broken", f"self-test failed ({failure.get('at', '')}): {failure.get('detail', '')}"
            report["kept_broken"] = failure
            report["why"] = (f"the probe says {probed}, but its last self-test failed ({failure.get('at', '')}): it stays "
                             "broken until a self-test passes")
        report.update(state=state, detail=detail)
        self.reports[comp_id] = report
        previous = self.store.set_state(comp_id, state, detail)
        self._last[comp_id] = time.monotonic()
        self.notify("probe", {"component": comp_id, "state": state})      # the Lab page shows when, live
        if previous != state:
            self.notify("component", {"component": comp_id, "state": state, "previous": previous, "detail": detail})
        expected = res.get("expected")
        if expected and state not in (expected, "unknown") and not (expected == "idle" and state == "configured"):
            self.notify("state_mismatch", {"component": comp_id, "expected": expected, "state": state})
        if probed == "idle" and (previous != "idle" or failure):
            self._selftest_when_idle(comp_id, retest=bool(failure) and previous == "broken")
        return state

    def _selftest_when_idle(self, comp_id: str, retest: bool) -> None:
        """A self-test in the background, for a component that became idle (not one an active run holds:
        the run tests its own); a component kept broken by its self-test is tested again at most every idle_retest."""
        if self.on_idle is None or comp_id in self.active or comp_id in self._testing:
            return
        if retest and time.monotonic() - self._tested.get(comp_id, -1e9) < self.idle_retest:
            return
        self._testing.add(comp_id)
        self._tested[comp_id] = time.monotonic()

        async def run():
            try:
                await self.on_idle(comp_id)
            except Exception as e:                       # never let it go unseen
                probe_log.warning(f"{comp_id}: self-test when idle failed: {type(e).__name__}: {e}")
            finally:
                self._testing.discard(comp_id)
        asyncio.ensure_future(run())

    def interval_of(self, comp_id: str) -> float:
        comp = self.lab.inventory.components[comp_id]
        stage = self.lab.inventory.stages[comp.stages[0]] if comp.stages else None
        resolved = self.lab.resolve(comp, stage)
        base = parse_duration((resolved.status or {}).get("interval")) or self.interval
        return max(1.0, base / 3 if comp_id in self.active else base)

    async def probe_all(self) -> None:
        await asyncio.gather(*[self.probe(c) for c in self.lab.inventory.components], return_exceptions=True)

    async def loop(self) -> None:
        while True:
            if self.lab is not None:
                due = [c for c in self.lab.inventory.components
                       if time.monotonic() - self._last.get(c, 0) >= self.interval_of(c)]
                if due:
                    await asyncio.gather(*[self.probe(c) for c in due], return_exceptions=True)
            if await wait_event(self._wake, 1.0):
                self._wake.clear()


# ── self-test ──────────────────────────────────────────────────────────────

def _not_run(selftest: list[dict], done: set[str], why: str) -> list[dict]:
    """The checks left after one that stops the self-test, listed as not run: the report shows every check."""
    return [{"id": c["id"], "ok": False, "skipped": True, "detail": f"not run: {why}"}
            for c in selftest if c["id"] not in done]


async def selftest_component(comp: Component, resolved: Resolved, transport: Transport,
                             monitor: Monitor | None = None, timeout: float = 60.0,
                             via: Component | None = None) -> dict:
    """Run a component's self-test checks; a failure tells which state to mark it.

    :param via: the component its status probe goes through (``status.via``), transport being its own: the
        ``run`` checks run there too, and the checks of a Linux system (login, remote root, disk, clock) are skipped
    """
    checks = []
    failed_state = None
    try:
        root = await transport.resolve_root()
    except Exception as e:
        return {"component": comp.id, "ok": False, "state": "offline",
                "checks": [{"id": "login", "ok": False, "detail": str(e)},
                           *_not_run(resolved.selftest, {"login"}, "the component cannot be reached")], "at": now()}
    keys = component_keys(comp, root, via, monitor.lab if monitor is not None else None)

    async def shell(command: str, limit: float | None = None):
        return await transport.shell(command, timeout=min(limit or timeout, timeout))

    for check in resolved.selftest:
        cid = check["id"]
        if check.get("if") is not None:
            try:
                if not evaluate(str(check["if"]), keys, f"selftest {cid}"):
                    continue
            except SequencerError as e:
                checks.append({"id": cid, "ok": False, "detail": str(e)})
                failed_state = failed_state or "broken"
                continue
        ok, detail = True, ""
        ran: dict = {}                                  # a run check: the command as rendered, its exit status
        builtin = check.get("builtin")
        try:
            if builtin == "status":
                if resolved.status is None:
                    detail = "no status probe"
                else:
                    state = (await monitor.probe(comp.id, selftest=True) if monitor
                             else (await probe_component(comp, resolved, transport, via=via))[0])
                    ok, detail = state not in ("broken", "offline"), f"state {state}"
                    if not ok:
                        failed_state = failed_state or state
            elif via is not None and builtin in ("login", "remote_root", "disk", "clock"):
                detail = f"skipped: the component is probed via {via.id}"
            elif builtin == "login":
                r = await shell("true", 20)
                ok, detail = r.completed and r.exit_status == 0, r.error or ""
                if not ok:
                    checks.append({"id": cid, "ok": False, "detail": detail or "login failed"})
                    later = [c["id"] for c in resolved.selftest]
                    checks += _not_run(resolved.selftest, set(later[:later.index(cid) + 1]), "the login failed")
                    return {"component": comp.id, "ok": False, "state": "offline", "checks": checks, "at": now()}
            elif builtin == "remote_root":
                q = shlex.quote(root)
                r = await shell(f"mkdir -p {q} && test -w {q}", 20)
                ok, detail = r.completed and r.exit_status == 0, root if r.completed else r.error
            elif builtin == "disk":
                need = parse_size(comp.vars.get("tsq_min_free_space", "1GB"))
                r = await shell(f"df -Pk {shlex.quote(root)} | tail -1", 20)
                fields = r.output.split() if r.completed else []
                free = int(fields[3]) * 1024 if len(fields) >= 4 and fields[3].isdigit() else None
                ok = free is not None and free >= need
                detail = f"{free} bytes free, {need} needed" if free is not None else (r.error or r.output.strip())
            elif builtin == "clock":
                limit = parse_duration(comp.vars.get("tsq_max_clock_offset", "1s"))
                before = time.time()
                r = await shell("date +%s.%N", 20)
                after = time.time()
                try:
                    offset = abs(float(r.output.strip().replace("N", "0")) - (before + after) / 2)
                    ok, detail = offset <= limit + (after - before) / 2, f"offset {offset:.3f}s"
                except ValueError:
                    ok, detail = False, r.error or r.output.strip()
            elif "run" in check:
                command = render(check["run"], keys, f"selftest {cid}")
                r = await shell(str(command), parse_duration(check.get("timeout")))
                codes = (check.get("expect") or {}).get("exit", [0])
                ok = r.completed and r.exit_status in (codes if isinstance(codes, list) else [codes])
                detail = r.error or r.output.strip()[-300:]
                ran = {"command": str(command), "exit": r.exit_status}
            else:
                ok, detail = False, f"check '{cid}' has no 'run'"
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        checks.append({"id": cid, "ok": bool(ok), "detail": detail, **ran})
        if not ok:
            failed_state = failed_state or "broken"
    return {"component": comp.id, "ok": failed_state is None, "state": failed_state, "checks": checks, "at": now()}
