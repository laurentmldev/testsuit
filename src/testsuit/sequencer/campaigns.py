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

from testsuit.sequencer.reportlinks import OnlineLinks

DONE = ("done", "cancelled")
WAITING = ("queued", "waiting_resources")
_RANK = {"succeeded": 0, "failed": 1, "error": 2}
INTERVENTION = {"run_again": "run again:", "continue": "continued anyway:", "redo_from": "redone from an earlier step:",
                "next": "next after", "bypass": "manual check bypassed (non-conform):"}
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
            reports.append({"step": step.get("id"), "key": f"{step.get('phase')}:{step.get('id')}", "path": exploit["report"]})
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
                             "by": c.get("by"), "nonconform": bool(c.get("nonconform"))} for s in steps for c in (s.get("manual") or {}).get("checks") or []
                            if c.get("bypassed")],
        # what operators did on its steps: next, run again, continue anyway, redo from
        "interventions": run.get("interventions") or [],
        # its review (SPEC §11.1): the verdict above is as reviewed, the raw one here when it differs
        **({"raw_verdict": run["raw_verdict"]} if run.get("raw_verdict") and run.get("state") in DONE else {}),
        **({"review": _review_brief(run["review"])} if run.get("review") else {}),
    }


def _review_brief(review: dict) -> dict:
    """What a campaign view keeps of a run's review: its state, counts, entries and warnings."""
    return {k: review[k] for k in ("state", "accepted", "rejected", "entries", "signed_off", "warnings") if k in review}


def campaign_review_brief(review: dict | None) -> dict:
    """A campaign run's own review: its state, comments and sign-off."""
    from testsuit.sequencer import review as rv
    if not review:
        return {"state": rv.NONE, "entries": []}
    return {"state": rv.state(review),
            "entries": [rv.brief(e) for e in rv.reviews(review)],
            **({"signed_off": rv.brief(rv.signed_off(review))} if rv.signed_off(review) else {}),
            "journal": review.get("entries") or []}


def summarize(campaign: dict, runs: list[dict], review: dict | None = None) -> dict:
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
    raw = [e.get("raw_verdict") or e.get("verdict") or "error" for e in entries]
    raw_verdict = max(raw, key=lambda v: _RANK.get(v, 2)) if state == "done" and raw else None
    reviewed = {"accepted": sum((e.get("review") or {}).get("accepted", 0) for e in entries),
                "rejected": sum((e.get("review") or {}).get("rejected", 0) for e in entries),
                "open": sum(1 for e in entries if (e.get("review") or {}).get("state") == "open"),
                "signed_off": sum(1 for e in entries if (e.get("review") or {}).get("state") == "signed_off")}
    started = [e["activated_at"] for e in entries if e.get("activated_at")]
    ended = [e["ended_at"] for e in entries if e.get("ended_at")]
    return {**{k: v for k, v in campaign.items() if k != "entries"},
            "state": state, "verdict": verdict if state == "done" else None,
            "conform": bool(campaign.get("conform")) and all(e.get("conform") for e in entries if "conform" in e),
            "counts": counts, "started_at": min(started) if started else None,
            **({"raw_verdict": raw_verdict} if state == "done" and raw_verdict != verdict else {}),
            "reviews": reviewed, "review": campaign_review_brief(review),
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
.passed { background:var(--okbg); color:var(--ok); } .partial { background:var(--runbg); color:var(--run); }
.skipped { background:var(--card); color:var(--muted); border:1px solid var(--line); } .not-run { color:var(--muted); border:1px solid var(--line); }
.uncovered { color:var(--bad); border:1px dashed var(--bad); } .changed { color:var(--err); font-size:11px; }
tr.node td { background:var(--card); font-weight:600; } td.c { text-align:center; }
.reviewed, .reviewed-mark { color:var(--run); font-weight:700; cursor:help; } .small { font-size:12px; }
.review-banner { margin:0 0 14px; padding:8px 14px; border-radius:8px; border:1px solid var(--line); }
.review-banner.draft { background:var(--errbg); color:var(--err); } .review-banner.final { background:var(--okbg); color:var(--ok); }
.review-line.accepted b { color:var(--ok); } .review-line.rejected b { color:var(--bad); } .review-line.comment b { color:var(--muted); }
"""


def requirements_html(cov, view: dict, base="") -> str:
    """The requirements section of the report: counts per status and the requirements × tests matrix (SPEC §5.5).
    base: the sequencer's root URL, or the links of the report (testsuit.sequencer.reportlinks)."""
    from testsuit.reqs.render import matrix_html, tiles_html
    if cov is None or not cov.tests:
        return ""
    to = OnlineLinks(base) if isinstance(base, str) else base
    full = to.coverage(view["id"])
    return (f'<h2>Requirements</h2><div class="muted">Requirements the test definitions and their checks verify, over the '
            f'runs of this campaign, as reviewed (✎).'
            + (f' <a href="{html.escape(full)}">Full coverage report</a> (with tests × requirements).' if full else "")
            + "</div>" + tiles_html(cov) + matrix_html(cov, to))


def a(href: str | None, text: str) -> str:
    """A link (text already escaped), or the text alone without a target."""
    return f'<a href="{html.escape(href)}">{text}</a>' if href else text


def badge(value: str | None) -> str:
    value = value or "pending"
    return f'<span class="badge {html.escape(value)}">{html.escape(value)}</span>'


def verdict_html(verdict: str | None, raw: str | None) -> str:
    """A verdict as reviewed, the raw one beside when the review changed it."""
    if raw and raw != verdict:
        return (f'{badge(verdict)} <span class="reviewed-mark" title="as reviewed">✎</span>'
                f'<div class="muted small">raw: {badge(raw)}</div>')
    return badge(verdict)


def review_lines(review: dict | None, limit: int = 6) -> str:
    """The review entries of a run, a line each: status (or comment) on its target, who and why."""
    e = html.escape
    if not review:
        return ""
    out = []
    for x in (review.get("entries") or [])[:limit]:
        what = x.get("status") or "comment"
        out.append(f'<div class="review-line {e(what)}"><b>{e(what)}</b> {e(str(x.get("label") or ""))}'
                   + (f' <span class="muted">(was {e(str(x["was"]).lower())})</span>' if x.get("was") and x.get("status") else "")
                   + f' · {e(str(x.get("by") or ""))}'
                   + (f': {e(str(x["reason"]))}' if x.get("reason") else "")
                   + (f' <span class="muted">{e(str(x["comment"]))}</span>' if x.get("comment") else "")
                   + (f' <span class="muted">[{e(str(x["ref"]))}]</span>' if x.get("ref") else "") + "</div>")
    more = len(review.get("entries") or []) - limit
    if more > 0:
        out.append(f'<div class="muted">and {more} more review entries</div>')
    out += [f'<div class="warn">review: {e(w)}</div>' for w in review.get("warnings") or []]
    return "".join(out)


def review_banner(view: dict) -> str:
    """Final (the campaign run's review signed off), or DRAFT while a review of it or of one of its runs is open."""
    e = html.escape
    own = view.get("review") or {}
    if own.get("state") == "signed_off":
        so = own.get("signed_off") or {}
        return (f'<div class="review-banner final">Final: review signed off by {e(str(so.get("by", "")))} on '
                f'{e(str(so.get("at", "")))}{": " + e(str(so["comment"])) if so.get("comment") else ""}</div>')
    if own.get("state") == "open" or (view.get("reviews") or {}).get("open"):
        return ('<div class="review-banner draft"><b>DRAFT</b>: the review of these results is open, they may still be '
                'accepted or rejected.</div>')
    return ""


def report_html(view: dict, base: str = "", requirements=None, links=None) -> str:
    """The synthetic report of a campaign run (a ``summarize`` view), as one self-contained HTML page. base: the URL of
    the sequencer's root as seen from where the page is served (links to its UI and API), or links: where its links
    lead (testsuit.sequencer.reportlinks). requirements: the coverage of its runs
    (testsuit.reqs.coverage.coverage_of_runs), shown when they link requirements."""
    e = html.escape
    to = links or OnlineLinks(base)

    git = view.get("git") or {}
    routing = view.get("routing") or {}
    counts = view.get("counts") or {}
    rows = []
    for r in view.get("runs") or []:
        links_ = [a(to.run(r["run"]), "run")]
        failed = r.get("failed_step")
        if failed:
            step = f"{failed['phase']}:{failed['id']}" + (f"[{failed['slot']}]" if failed.get("slot") else "")
            links_.append(a(to.log(r["run"], step), f"log of {e(step)}"))
        for report in r.get("reports") or []:
            links_.append(a(to.exploit(r["run"], str(report.get("key") or report["step"]), report["path"]),
                            f'exploit report ({e(str(report["step"]))})'))
        if r.get("results"):
            href = to.results(r["run"])
            if href:
                links_.append(a(href, "results zip"))
        links_ = [x for x in links_ if x.startswith("<a ")] or [e(r["run"])]
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
        detail += "".join(f'<div class="warn">bypassed{" (non-conform)" if b.get("nonconform") else ""}: {e(str(b["check"]))} ({e(str(b.get("comment") or ""))})</div>'
                          for b in r.get("bypassed_checks") or [])
        detail += "".join(f'<div class="{"warn" if i.get("action") in ("continue", "bypass") else "muted"}">{e(INTERVENTION.get(i.get("action"), str(i.get("action"))))} '
                          f'<code>{e(str(i.get("step")))}</code>{e(" (" + i["by"] + ")" if i.get("by") else "")}'
                          f'{e(": " + i["reason"] if i.get("reason") else "")}</div>'
                          for i in r.get("interventions") or [] if i.get("action") not in ("next", "bypass"))
        detail += review_lines(r.get("review"))
        if r.get("earlier"):                       # re-runs: the attempts it replaced, each linked
            detail += (f'<div class="muted">attempt {r.get("attempt")}; earlier: '
                       + ", ".join(f'{a(to.run(x["run"]), e(x["run"]))} {badge(x.get("verdict") or x.get("state"))}'
                                   for x in r["earlier"]) + "</div>")
        rows.append(
            "<tr>"
            f'<td><strong>{e(str(r.get("testdef") or ""))}</strong><div class="muted"><code>{e(str(r.get("testdef_file") or ""))}</code>'
            + (f' · {e(r["setup_config"])}' if r.get("setup_config") else "") + "</div></td>"
            f'<td>{verdict_html(r.get("verdict") or r.get("state"), r.get("raw_verdict"))}</td>'
            f'<td>{e(_duration(r.get("duration_s")))}</td>'
            f'<td>{e(", ".join(r.get("stages") or []) or ", ".join(sorted(set((r.get("components") or {}).values()))))}</td>'
            f"<td>{check_text}{not_met}</td>"
            f"<td>{detail}</td>"
            f'<td>{" · ".join(links_)}</td>'
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
    reviewed = view.get("reviews") or {}
    tiles += "".join(f'<div class="tile"><b>{reviewed[k]}</b>{label}</div>'
                     for k, label in (("accepted", "accepted by review"), ("rejected", "rejected by review"))
                     if reviewed.get(k))
    title = view.get("title") or view.get("campaign") or view["id"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(str(title))} · campaign report</title><style>{_CSS}</style></head>
<body><main>
{review_banner(view)}
<h1>{e(str(title))} {verdict_html(view.get("verdict") or view.get("state"), view.get("raw_verdict"))}</h1>
<div class="muted">campaign <code>{e(str(view.get("campaign") or ""))}</code> · run <code>{e(view["id"])}</code>
 · submitted {e(str(view.get("submitted_at") or ""))}{" · ended " + e(str(view.get("ended_at") or view.get("last_ended_at") or "")) if view.get("state") == "done" else ""}
 {" · " + e(_duration(view.get("duration_s"))) if view.get("duration_s") is not None else ""}</div>
{f'<p>{e(view["description"])}</p>' if view.get("description") else ""}
<div class="tiles">{tiles}</div>
<h2>Runs</h2>
<div class="scroll"><table>
<thead><tr><th>Test definition</th><th>Verdict</th><th>Duration</th><th>Ran on</th><th>Checks met</th><th>Details</th><th>Links</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
{campaign_review_html(view)}
{requirements_html(requirements, view, to)}
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


def campaign_review_html(view: dict) -> str:
    """The review section: its state, the counts of results accepted and rejected, the comments on the campaign run."""
    e = html.escape
    own = view.get("review") or {}
    reviewed = view.get("reviews") or {}
    if own.get("state", "none") == "none" and not any(reviewed.get(k) for k in ("accepted", "rejected", "open", "signed_off")):
        return '<h2>Review</h2><p class="muted">Nobody reviewed these results.</p>'
    state = {"signed_off": "signed off", "open": "open", "none": "not signed off"}.get(own.get("state", "none"))
    so = own.get("signed_off") or {}
    lines = [f'<dt>State</dt><dd>{e(state or "")}{" by " + e(str(so.get("by"))) + " on " + e(str(so.get("at"))) if so else ""}</dd>',
             f'<dt>Results</dt><dd>{reviewed.get("accepted", 0)} accepted, {reviewed.get("rejected", 0)} rejected; '
             f'{reviewed.get("signed_off", 0)} run review(s) signed off, {reviewed.get("open", 0)} open</dd>']
    notes = "".join(f'<li>{e(str(x.get("by")))}, {e(str(x.get("at")))}: {e(str(x.get("comment") or x.get("reason") or ""))}</li>'
                    for x in own.get("entries") or [])
    return (f'<h2>Review</h2><dl>{"".join(lines)}</dl>'
            + (f'<h3>Comments</h3><ul>{notes}</ul>' if notes else ""))


def report_text(view: dict) -> list[str]:
    """The campaign run as lines of text (``tsq campaign``)."""
    own = (view.get("review") or {}).get("state", "none")
    lines = [f"campaign run {view['id']}: {view.get('title') or view.get('campaign')} "
             f"[{view.get('verdict') or view.get('state')}]" + ("" if view.get("conform") else " NON-CONFORM")
             + (f" (raw {view['raw_verdict']})" if view.get("raw_verdict") else "")
             + {"signed_off": ", review signed off", "open": ", review open"}.get(own, "")]
    for r in view.get("runs") or []:
        failed = r.get("failed_step")
        checks = r.get("checks") or {}
        lines.append(f"  {r['run']}  {str(r.get('verdict') or r.get('state')):<10} {r.get('testdef') or r.get('testdef_file')}"
                     + (f"  (raw {r['raw_verdict']}, reviewed)" if r.get("raw_verdict") else "")
                     + (f"  checks {checks['met']}/{checks['total']}" if checks.get("total") else "")
                     + (f"  first {failed['state']}: {failed['phase']}:{failed['id']}" if failed else "")
                     + (f"  attempt {r['attempt']} (earlier: " + ", ".join(f"{a['run']} {a.get('verdict') or a.get('state')}"
                                                              for a in r["earlier"]) + ")" if r.get("earlier") else ""))
    return lines

