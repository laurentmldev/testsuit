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

"""Campaign runs: the runs of a campaign's datapacks, submitted together (SPEC section 9.6).

``summarize`` gives a campaign run with each of its runs at a glance (its verdict, where it ran, its first failed
step, the checks of its exploit steps); ``report_html`` makes the synthetic report of it, a self-contained page whose
links lead to the sequencer's pages (run, step log, exploit report, results zip).
"""
from __future__ import annotations

import html
from datetime import datetime
from urllib.parse import quote

DONE = ("done", "cancelled")
WAITING = ("queued", "waiting_resources")
_RANK = {"succeeded": 0, "failed": 1, "error": 2}
INTERVENTION = {"run_again": "run again:", "continue": "continued anyway:", "redo_from": "redone from an earlier step:",
                "next": "next after"}
# what a campaign view keeps of an earlier attempt of a datapack (replaced by a re-run)
ATTEMPT_FIELDS = ("run", "state", "verdict", "reason", "activated_at", "ended_at", "duration_s", "failed_step", "stages")


def _seconds(start: str | None, end: str | None) -> float | None:
    try:
        return (datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))).total_seconds() if start and end else None
    except ValueError:
        return None


def _check_met(check: dict) -> bool:
    return str(check.get("status", "")).upper() in ("SUCCESS", "ACCEPTED")


def run_summary(run: dict) -> dict:
    """One run of a campaign at a glance."""
    steps = run.get("steps") or []
    failed = next((s for s in steps if s.get("state") in ("error", "failed")), None)
    checks, reports = [], []
    for step in steps:
        exploit = step.get("exploit") or {}
        checks += exploit.get("checks") or []
        if exploit.get("report"):
            reports.append({"step": step.get("id"), "path": exploit["report"]})
    binding = run.get("binding") or {}
    verdict = run.get("verdict") if run.get("state") == "done" else ("error" if run.get("state") == "cancelled" else None)
    return {
        "run": run["id"], "name": run.get("name"), "testdef": run.get("testdef"), "state": run.get("state"),
        "verdict": verdict, "reason": run.get("reason") or "", "priority": run.get("priority"),
        "conform": run.get("conform"), "nonconform": run.get("nonconform") or [],
        "submitted_at": run.get("submitted_at"), "activated_at": run.get("activated_at"), "ended_at": run.get("ended_at"),
        "duration_s": _seconds(run.get("activated_at"), run.get("ended_at")),
        "stages": sorted(set((binding.get("stage_slots") or {}).values())),
        "components": binding.get("slots") or {},
        "failed_step": {"phase": failed.get("phase"), "id": failed.get("id"), "slot": failed.get("slot") or "",
                        "state": failed.get("state"), "reason": failed.get("reason") or "",
                        "log": failed.get("log") or ""} if failed else None,
        "checks": {"total": len(checks), "met": sum(1 for c in checks if _check_met(c)),
                   "not_met": [c for c in checks if not _check_met(c)]},
        "reports": reports, "results": bool(run.get("results")),
        "awaiting_manual": run.get("awaiting_manual") or [], "rerun_of": run.get("rerun_of"),
        "paused": run.get("paused") or [],
        # checks of manual operations the operator bypassed: the run went on without them
        "bypassed_checks": [{"step": f"{s.get('phase')}:{s.get('id')}", "check": c.get("text"), "comment": c.get("comment"),
                             "by": c.get("by")} for s in steps for c in (s.get("manual") or {}).get("checks") or []
                            if c.get("bypassed")],
        # what operators did on its steps: next, run again, continue anyway, redo from
        "interventions": run.get("interventions") or [],
    }


def summarize(campaign: dict, runs: list[dict]) -> dict:
    """A campaign run, its runs (in the campaign's order) and its state: ``queued`` while none started, ``running``
    while one has not ended, then ``done`` with a verdict, the worst of its runs' (a cancelled run counts as error).
    A datapack run again counts by its last attempt; the earlier ones are listed with it (``earlier``)."""
    by_id = {r["id"]: r for r in runs}
    entries = []
    for entry in campaign.get("entries") or []:
        run = by_id.pop(entry.get("run"), None)
        summary = run_summary(run) if run else {"run": entry.get("run"), "state": "gone", "verdict": None}
        earlier = []                                   # the attempts it replaced (re-runs), oldest first
        for run_id in entry.get("attempts") or []:
            old = by_id.pop(run_id, None)
            earlier.append({k: v for k, v in run_summary(old).items() if k in ATTEMPT_FIELDS} if old else
                           {"run": run_id, "state": "gone", "verdict": None})
        entries.append({"testdef_file": entry.get("testdef"), "setup_config": entry.get("setup_config"), **summary,
                        "attempt": len(earlier) + 1, "earlier": earlier})
    entries += [{"testdef_file": None, "setup_config": None, **run_summary(r)} for r in by_id.values()]
    states = [e["state"] for e in entries]
    if entries and all(s in DONE or s == "gone" for s in states):
        state = "done"
    elif all(s in WAITING for s in states):
        state = "queued"
    else:
        state = "running"
    verdicts = [e.get("verdict") or "error" for e in entries]
    verdict = max(verdicts, key=lambda v: _RANK.get(v, 2)) if state == "done" and verdicts else None
    counts: dict[str, int] = {"total": len(entries), "waiting": sum(s in WAITING for s in states),
                              "running": sum(s not in WAITING and s not in DONE and s != "gone" for s in states)}
    for v in ("succeeded", "failed", "error"):
        counts[v] = sum(1 for e in entries if e["state"] in DONE and (e.get("verdict") or "error") == v)
    started = [e["activated_at"] for e in entries if e.get("activated_at")]
    ended = [e["ended_at"] for e in entries if e.get("ended_at")]
    return {**{k: v for k, v in campaign.items() if k != "entries"},
            "state": state, "verdict": verdict if state == "done" else None,
            "conform": bool(campaign.get("conform")) and all(e.get("conform") for e in entries if "conform" in e),
            "counts": counts, "started_at": min(started) if started else None,
            "last_ended_at": max(ended) if state == "done" and ended else None,
            "duration_s": _seconds(min(started), max(ended)) if state == "done" and started and ended else None,
            "runs": entries}


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


_CSS = """
:root { --bg:#fff; --fg:#1d2330; --muted:#667085; --line:#e4e7ec; --ok:#16794c; --okbg:#e7f6ee; --bad:#b42318;
        --badbg:#fdecea; --err:#93370d; --errbg:#fef0c7; --run:#175cd3; --runbg:#e8f0fe; --card:#f8f9fb; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171c; --fg:#e6e8eb; --muted:#98a2b3; --line:#2c323b;
        --ok:#6ce9a6; --okbg:#0f2d20; --bad:#fda29b; --badbg:#3a1512; --err:#fec84b; --errbg:#3a2a07; --run:#84adff;
        --runbg:#14254a; --card:#1b1f26; } }
body { background:var(--bg); color:var(--fg); font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
       margin:0; padding:24px 16px; }
main { max-width:1100px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:16px; margin:28px 0 8px; }
.muted { color:var(--muted); } code { font-size:12.5px; }
.badge { display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px; font-weight:600; }
.succeeded { background:var(--okbg); color:var(--ok); } .failed { background:var(--badbg); color:var(--bad); }
.error, .cancelled { background:var(--errbg); color:var(--err); } .running, .queued, .waiting { background:var(--runbg); color:var(--run); }
.tiles { display:flex; flex-wrap:wrap; gap:10px; margin:16px 0; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:8px 14px; min-width:90px; }
.tile b { display:block; font-size:20px; }
table { border-collapse:collapse; width:100%; } th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { font-size:12px; color:var(--muted); font-weight:600; }
.scroll { overflow-x:auto; } dl { display:grid; grid-template-columns:max-content 1fr; gap:4px 16px; margin:0; }
dt { color:var(--muted); } dd { margin:0; } a { color:var(--run); } .warn { color:var(--err); }
"""


def report_html(view: dict, base: str = "") -> str:
    """The synthetic report of a campaign run (a ``summarize`` view), as one self-contained HTML page. base: the URL of
    the sequencer's root as seen from where the page is served (links to its UI and API)."""
    e = html.escape

    def badge(value: str | None) -> str:
        value = value or "pending"
        return f'<span class="badge {e(value)}">{e(value)}</span>'

    def run_link(run_id: str, tab: str = "") -> str:
        return f"{base}#/runs/{quote(run_id)}" + (f"?tab={tab}" if tab else "")

    git = view.get("git") or {}
    routing = view.get("routing") or {}
    counts = view.get("counts") or {}
    rows = []
    for r in view.get("runs") or []:
        links = [f'<a href="{run_link(r["run"])}">run</a>']
        failed = r.get("failed_step")
        if failed:
            step = f"{failed['phase']}:{failed['id']}" + (f"[{failed['slot']}]" if failed.get("slot") else "")
            links.append(f'<a href="{base}runs/{quote(r["run"])}/logs/{quote(step)}">log of {e(step)}</a>')
        for report in r.get("reports") or []:
            links.append(f'<a href="{base}runs/{quote(r["run"])}/files/{quote(report["path"])}">exploit report '
                         f'({e(str(report["step"]))})</a>')
        if r.get("results"):
            links.append(f'<a href="{base}runs/{quote(r["run"])}/files/results.zip?results=true">results zip</a>')
        checks = r.get("checks") or {}
        check_text = f'{checks.get("met", 0)} / {checks.get("total", 0)}' if checks.get("total") else '<span class="muted">none</span>'
        not_met = "".join(f'<div class="muted">✗ {e(str(c.get("criteria", "")))} {e(str(c.get("check") or c.get("scenario") or ""))}</div>'
                          for c in (checks.get("not_met") or [])[:5])
        detail = ""
        if failed:
            detail += f'<div>{badge(failed["state"])} <code>{e(failed["phase"] + ":" + failed["id"])}</code> {e(failed.get("reason") or "")}</div>'
        if r.get("reason") and not failed:
            detail += f'<div class="muted">{e(r["reason"])}</div>'
        if r.get("nonconform"):
            detail += "".join(f'<div class="warn">non-conform: {e(n)}</div>' for n in r["nonconform"][:3])
        detail += "".join(f'<div class="warn">bypassed: {e(str(b["check"]))} ({e(str(b.get("comment") or ""))})</div>'
                          for b in r.get("bypassed_checks") or [])
        detail += "".join(f'<div class="{"warn" if i.get("action") == "continue" else "muted"}">{e(INTERVENTION.get(i.get("action"), str(i.get("action"))))} '
                          f'<code>{e(str(i.get("step")))}</code>{e(" (" + i["by"] + ")" if i.get("by") else "")}'
                          f'{e(": " + i["reason"] if i.get("reason") else "")}</div>'
                          for i in r.get("interventions") or [] if i.get("action") != "next")
        if r.get("earlier"):                       # re-runs: the attempts it replaced, each linked
            detail += (f'<div class="muted">attempt {r.get("attempt")}; earlier: '
                       + ", ".join(f'<a href="{run_link(a["run"])}">{e(a["run"])}</a> {badge(a.get("verdict") or a.get("state"))}'
                                   for a in r["earlier"]) + "</div>")
        rows.append(
            "<tr>"
            f'<td><strong>{e(str(r.get("testdef") or ""))}</strong><div class="muted"><code>{e(str(r.get("testdef_file") or ""))}</code>'
            + (f' · {e(r["setup_config"])}' if r.get("setup_config") else "") + "</div></td>"
            f'<td>{badge(r.get("verdict") or r.get("state"))}</td>'
            f'<td>{e(_duration(r.get("duration_s")))}</td>'
            f'<td>{e(", ".join(r.get("stages") or []) or ", ".join(sorted(set((r.get("components") or {}).values()))))}</td>'
            f"<td>{check_text}{not_met}</td>"
            f"<td>{detail}</td>"
            f'<td>{" · ".join(links)}</td>'
            "</tr>")
    stage_tags = routing.get("stage_tags") or []
    stage_text = ", ".join(stage_tags) if isinstance(stage_tags, list) else \
        "; ".join(f"{k}: {', '.join(v)}" for k, v in stage_tags.items())
    component_text = "; ".join(f"{k}: {', '.join(v)}" for k, v in (routing.get("component_tags") or {}).items())
    datasets = "".join(f"<tr><td><code>{e(k)}</code></td><td><code>{e(str(v))}</code></td></tr>"
                       for k, v in (view.get("datasets") or {}).items())
    tiles = "".join(f'<div class="tile"><b>{counts.get(k, 0)}</b>{label}</div>'
                    for k, label in (("total", "runs"), ("succeeded", "succeeded"), ("failed", "failed"),
                                     ("error", "error"), ("running", "running"), ("waiting", "waiting")))
    title = view.get("title") or view.get("campaign") or view["id"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(str(title))} · campaign report</title><style>{_CSS}</style></head>
<body><main>
<h1>{e(str(title))} {badge(view.get("verdict") or view.get("state"))}</h1>
<div class="muted">campaign <code>{e(str(view.get("campaign") or ""))}</code> · run <code>{e(view["id"])}</code>
 · submitted {e(str(view.get("submitted_at") or ""))}{" · ended " + e(str(view.get("ended_at") or view.get("last_ended_at") or "")) if view.get("state") == "done" else ""}
 {" · " + e(_duration(view.get("duration_s"))) if view.get("duration_s") is not None else ""}</div>
{f'<p>{e(view["description"])}</p>' if view.get("description") else ""}
<div class="tiles">{tiles}</div>
<h2>Runs</h2>
<div class="scroll"><table>
<thead><tr><th>Test definition</th><th>Verdict</th><th>Duration</th><th>Ran on</th><th>Checks met</th><th>Details</th><th>Links</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<h2>Baseline</h2>
<dl>
<dt>Conform</dt><dd>{"yes" if view.get("conform") else '<span class="warn">no</span>'}</dd>
<dt>Campaign file</dt><dd><code>{e(str(view.get("file") or ""))}</code></dd>
<dt>Git</dt><dd>{e(str(git.get("repo") or ""))} <code>{e(str(git.get("tag") or (git.get("commit") or "")[:12]))}</code>{"" if git.get("clean", True) else ' <span class="warn">(local changes)</span>'}</dd>
<dt>Stage tags</dt><dd>{e(stage_text) or '<span class="muted">none</span>'}</dd>
<dt>Component tags</dt><dd>{e(component_text) or '<span class="muted">none</span>'}</dd>
</dl>
<h2>Dataset versions</h2>
{f"<table><thead><tr><th>Dataset</th><th>Version</th></tr></thead><tbody>{datasets}</tbody></table>" if datasets else '<p class="muted">The campaign pins no dataset.</p>'}
</main></body></html>
"""


def report_text(view: dict) -> list[str]:
    """The campaign run as lines of text (``tsq campaign``)."""
    lines = [f"campaign run {view['id']}: {view.get('title') or view.get('campaign')} "
             f"[{view.get('verdict') or view.get('state')}]" + ("" if view.get("conform") else " NON-CONFORM")]
    for r in view.get("runs") or []:
        failed = r.get("failed_step")
        checks = r.get("checks") or {}
        lines.append(f"  {r['run']}  {str(r.get('verdict') or r.get('state')):<10} {r.get('testdef') or r.get('testdef_file')}"
                     + (f"  checks {checks['met']}/{checks['total']}" if checks.get("total") else "")
                     + (f"  first {failed['state']}: {failed['phase']}:{failed['id']}" if failed else "")
                     + (f"  attempt {r['attempt']} (earlier: " + ", ".join(f"{a['run']} {a.get('verdict') or a.get('state')}"
                                                              for a in r["earlier"]) + ")" if r.get("earlier") else ""))
    return lines

