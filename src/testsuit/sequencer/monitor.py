"""State monitoring (README section 7.4) and platform self-test (section 7.5).

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
from typing import Any, Callable

from testsuit.sequencer import registry
from testsuit.sequencer.inventory import Component
from testsuit.sequencer.lab import Lab
from testsuit.sequencer.component_types import ResolvedType
from testsuit.sequencer.transports import Transport
from testsuit.sequencer.util import SequencerError, now, parse_duration, parse_size, render, evaluate

log = logging.getLogger("testsuit.sequencer")

STATES = ("offline", "idle", "configured", "running", "broken", "unknown")


def component_keys(comp: Component, root: str) -> dict:
    return {"component": {"id": comp.id, "concrete": comp.id, "host": comp.host, "role": comp.role, "type": comp.type,
                          "label": comp.label, "vars": comp.vars, "remote_root": root, "stage": "",
                          "input_dir": f"{root}/{comp.id}/input", "output_dir": f"{root}/{comp.id}/output"},
            "remote_root": root}


def _match(states: dict, output: str, exit_status: int | None) -> str | None:
    for state, rule in (states or {}).items():
        if not isinstance(rule, dict):
            continue
        if "regex" in rule and re.search(str(rule["regex"]), output, re.MULTILINE):
            return state
        if "exit" in rule:
            wanted = rule["exit"]
            if wanted == "nonzero" and exit_status not in (None, 0):
                return state
            codes = wanted if isinstance(wanted, list) else [wanted]
            if exit_status is not None and exit_status in [c for c in codes if isinstance(c, int)]:
                return state
    return None


async def probe_component(comp: Component, resolved: ResolvedType, transport: Transport,
                          expected: str | None = None) -> tuple[str, str]:
    """(state, detail) of a component, from its type's status probe."""
    status = resolved.status
    try:
        root = await transport.resolve_root()
    except Exception as e:
        return "offline", f"cannot reach {comp.host}: {e}"
    if status is None:
        return "unknown", "no status probe"
    if status.get("builtin") == "linux_system":
        result = await transport.shell("true", timeout=15)
        if not result.completed or result.exit_status:
            return "offline", result.error or result.output.strip()
        try:
            alive = await transport.alive_in(f"{root}/.tsq", f"*/{comp.id}/*.pid")
        except SequencerError as e:
            return "offline", str(e)
        if alive:
            return "running", f"{len(alive)} process group(s) started by the sequencer"
        return ("configured" if expected == "configured" else "idle"), ""
    probe = status.get("probe") or {}
    keys = component_keys(comp, root)
    if "run" in probe:
        command = render(probe["run"], keys, f"{comp.id} status")
        result = await transport.shell(str(command), timeout=parse_duration(probe.get("timeout")) or 15)
        if not result.completed:
            return "offline", result.error or ""
        state = _match(status.get("states") or {}, result.output, result.exit_status)
        return state or "unknown", result.output.strip()[-200:]
    for name, spec in probe.items():
        fn = registry.state_probes.get(name.lower())
        if fn is not None:
            output, exit_status = await fn(transport, render(spec, keys), keys)
            state = _match(status.get("states") or {}, output, exit_status)
            return state or "unknown", str(output)[-200:]
    if "http" in probe:
        import urllib.request
        url = render(probe["http"] if isinstance(probe["http"], str) else probe["http"]["url"], keys)

        def get():
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    return r.read(1 << 16).decode("utf-8", "replace"), 0 if r.status < 400 else r.status
            except Exception as e:
                return str(e), None
        output, code = await asyncio.to_thread(get)
        if code is None:
            return "offline", output
        return _match(status.get("states") or {}, output, code) or "unknown", output[-200:]
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
        self._last: dict[str, float] = {}
        self._wake = asyncio.Event()

    def attach(self, lab: Lab, transports: Callable[[str], Transport]) -> None:
        self.lab, self.transports = lab, transports

    def state(self, comp_id: str) -> str:
        res = self.store.resources("component").get(("component", comp_id))
        return (res or {}).get("state") or "unknown"

    async def probe(self, comp_id: str) -> str:
        comp = self.lab.inventory.components[comp_id]
        stage = self.lab.inventory.stages[comp.stages[0]] if comp.stages else None
        resolved = self.lab.types.resolve(comp, stage)
        res = self.store.resources("component").get(("component", comp_id)) or {}
        try:
            state, detail = await asyncio.wait_for(
                probe_component(comp, resolved, self.transports(comp_id), res.get("expected")), 60)
        except asyncio.TimeoutError:
            state, detail = "offline", "probe timeout"
        except Exception as e:
            state, detail = "unknown", f"probe failed: {e}"
        previous = self.store.set_state(comp_id, state, detail)
        self._last[comp_id] = time.monotonic()
        if previous != state:
            self.notify("component", {"component": comp_id, "state": state, "previous": previous, "detail": detail})
        expected = res.get("expected")
        if expected and state not in (expected, "unknown") and not (expected == "idle" and state == "configured"):
            self.notify("state_mismatch", {"component": comp_id, "expected": expected, "state": state})
        return state

    def interval_of(self, comp_id: str) -> float:
        comp = self.lab.inventory.components[comp_id]
        stage = self.lab.inventory.stages[comp.stages[0]] if comp.stages else None
        resolved = self.lab.types.resolve(comp, stage)
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
            try:
                await asyncio.wait_for(self._wake.wait(), 1.0)
                self._wake.clear()
            except asyncio.TimeoutError:
                pass


# ── self-test ──────────────────────────────────────────────────────────────

async def selftest_component(comp: Component, resolved: ResolvedType, transport: Transport,
                             monitor: Monitor | None = None, timeout: float = 60.0) -> dict:
    """Run a component's self-test checks; a failure tells which state to mark it."""
    checks = []
    failed_state = None
    try:
        root = await transport.resolve_root()
    except Exception as e:
        return {"component": comp.id, "ok": False, "state": "offline",
                "checks": [{"id": "login", "ok": False, "detail": str(e)}], "at": now()}
    keys = component_keys(comp, root)

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
        builtin = check.get("builtin")
        try:
            if builtin == "status":
                if resolved.status is None:
                    detail = "no status probe"
                else:
                    state = await monitor.probe(comp.id) if monitor else (await probe_component(comp, resolved, transport))[0]
                    ok, detail = state not in ("broken", "offline"), f"state {state}"
                    if not ok:
                        failed_state = failed_state or state
            elif builtin == "login":
                r = await shell("true", 20)
                ok, detail = r.completed and r.exit_status == 0, r.error or ""
                if not ok:
                    checks.append({"id": cid, "ok": False, "detail": detail or "login failed"})
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
            else:
                ok, detail = False, f"check '{cid}' has no 'run'"
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        checks.append({"id": cid, "ok": bool(ok), "detail": detail})
        if not ok:
            failed_state = failed_state or "broken"
    return {"component": comp.id, "ok": failed_state is None, "state": failed_state, "checks": checks, "at": now()}
