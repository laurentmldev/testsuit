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

"""The report of one run (SPEC section 11.2), as one self-contained HTML page: its verdict as reviewed, its steps,
the checks of its exploit steps, its review, what operators did, its non-conformities, its requirements, and links to
its logs, exploit reports and input files (to the sequencer, or to the pages of an exported report)."""
from __future__ import annotations

import html

from testsuit.sequencer import review as rv
from testsuit.sequencer.campaigns import _CSS, INTERVENTION, _duration, _seconds, a, badge, verdict_html
from testsuit.sequencer.reportlinks import OnlineLinks


def _notes(notes: list[dict] | None) -> str:
    e = html.escape
    return "".join(f'<div class="review-line {e(n.get("status") or "comment")}"><b>{e(n.get("status") or "comment")}</b> '
                   f'{e(str(n.get("by") or ""))}, {e(str(n.get("at") or ""))}'
                   + (f': {e(str(n["reason"]))}' if n.get("reason") else "")
                   + (f' <span class="muted">{e(str(n["comment"]))}</span>' if n.get("comment") else "")
                   + (f' <span class="muted">[{e(str(n["ref"]))}]</span>' if n.get("ref") else "") + "</div>"
                   for n in notes or [])


def journal_html(entries: list[dict]) -> str:
    """Every entry of a review journal, revoked ones and sign-offs included, as a table."""
    e = html.escape
    if not entries:
        return '<p class="muted">Nobody reviewed this run.</p>'
    gone = {x.get("revokes") for x in entries if x.get("action") == "revoke"}
    rows = []
    for x in entries:
        action = x.get("action")
        if action == "review":
            what = (x.get("status") or "comment") + (" (revoked)" if x.get("id") in gone else "")
            on = rv.target_label(x.get("target") or {})
        elif action == "revoke":
            what, on = "revoke", f"entry {x.get('revokes')}"
        else:
            what, on = action.replace("_", " "), ""
        rows.append(f"<tr><td>{e(str(x.get('id')))}</td><td>{e(str(x.get('at') or ''))}</td><td>{e(str(x.get('by') or ''))}</td>"
                    f"<td><b>{e(what)}</b></td><td><code>{e(on)}</code>"
                    + (f'<div class="muted">was {e(str(x["was"]).lower())}</div>' if x.get("was") else "") + "</td>"
                    f"<td>{e(str(x.get('reason') or ''))}"
                    + (f'<div class="muted">{e(str(x["comment"]))}</div>' if x.get("comment") else "")
                    + (f'<div class="muted">{e(str(x["ref"]))}</div>' if x.get("ref") else "")
                    + (f'<div class="muted">reapplied from run {e(str(x["reapplied_from"].get("run")))}</div>'
                       if isinstance(x.get("reapplied_from"), dict) else "") + "</td></tr>")
    return ("<div class=\"scroll\"><table><thead><tr><th>#</th><th>When</th><th>Who</th><th>What</th><th>On</th>"
            f"<th>Why</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>")


def run_report_html(run: dict, journal: list[dict] | None = None, links=None, requirements=None,
                    inputs: list[dict] | None = None, title_suffix: str = "", issues: list[dict] | None = None) -> str:
    """The report of a run as reviewed (``review.apply_review``). journal: its review's entries; links: where links
    lead (default: none); requirements: its coverage; inputs: its input files ``[{slot, path, size}]`` (an export);
    issues: the known issues of its failures (SPEC §11.3), as last seen."""
    e = html.escape
    to = links or OnlineLinks(None)
    review = run.get("review") or {}
    targets = review.get("targets") or {}
    rid = run["id"]
    st = review.get("state", rv.NONE)
    banner = ""
    if st == rv.SIGNED_OFF:
        so = review.get("signed_off") or {}
        banner = (f'<div class="review-banner final">Final: review signed off by {e(str(so.get("by", "")))} on '
                  f'{e(str(so.get("at", "")))}{": " + e(str(so["comment"])) if so.get("comment") else ""}</div>')
    elif st == rv.OPEN:
        banner = '<div class="review-banner draft"><b>DRAFT</b>: the review of this run is open.</div>'

    step_rows, exploit_parts = [], []
    for s in run.get("steps") or []:
        key = rv.step_key(s)
        log_link = a(to.log(rid, key), "log") if s.get("log") else ""
        state = s.get("state")
        cell = badge(state)
        if s.get("raw_state") and s["raw_state"] != state:
            cell += f' <span class="reviewed-mark" title="as reviewed">✎</span><div class="muted small">raw: {badge(s["raw_state"])}</div>'
        step_rows.append(
            f"<tr><td><code>{e(key)}</code>" + (' <span class="muted small">(does not count)</span>' if s.get("continue_on_error") else "")
            + f"</td><td>{cell}</td><td>{e(_duration(_seconds(s.get('started'), s.get('ended'))))}</td>"
            f"<td>{e(str(s.get('exit_status') if s.get('exit_status') is not None else ''))}</td>"
            f"<td>{e(str(s.get('reason') or ''))}{_notes(targets.get(f'step|{key}'))}</td><td>{log_link}</td></tr>")
        x = s.get("exploit")
        if x:
            checks = x.get("checks") or []
            rows = []
            for c in sorted(checks, key=lambda c: str(c.get("status") or "").upper() in ("SUCCESS", "ACCEPTED")):
                status = str(c.get("status") or "")
                met = status.upper() in ("SUCCESS", "ACCEPTED")
                raw = (f'<div class="muted small">raw: {e(str(c["raw_status"]).lower())}</div>' if c.get("raw_status") else "")
                folder = c.get("criteria") or ""
                if c.get("scenario") and not str(folder).rstrip("/").endswith(str(c["scenario"])):
                    folder = f"{folder} · {c['scenario']}"
                rows.append(f"<tr><td><code>{e(str(folder))}</code></td><td>{e(str(c.get('check') or 'the whole scenario'))}</td>"
                            f'<td><span class="badge {"succeeded" if met else "failed"}">{e(status.lower())}</span>{raw}</td>'
                            f"<td>{_notes(targets.get(rv.target_key(rv.check_target(s, c))))}</td></tr>")
            met = sum(1 for c in checks if str(c.get("status") or "").upper() in ("SUCCESS", "ACCEPTED"))
            report = a(to.exploit(rid, key, x.get("report") or ""), "exploit report") if x.get("report") else ""
            exploit_parts.append(
                f'<h3><code>{e(key)}</code> {badge(state)} <span class="muted small">{met} of {len(checks)} check(s) met</span> '
                f'{report}</h3>'
                + (f'<div class="scroll"><table><thead><tr><th>Criteria</th><th>Check</th><th>Status</th><th>Review</th></tr></thead>'
                   f'<tbody>{"".join(rows)}</tbody></table></div>' if rows else '<p class="muted">No check reported.</p>'))

    binding = run.get("binding") or {}
    bound = ", ".join(f"{k}={v}" for k, v in {**(binding.get("stage_slots") or {}), **(binding.get("slots") or {})}.items())
    interventions = "".join(
        f'<li>{e(INTERVENTION.get(i.get("action"), str(i.get("action"))))} <code>{e(str(i.get("step") or i.get("target") or ""))}</code>'
        f'{e(" (" + i["by"] + ")" if i.get("by") else "")}{e(": " + i["reason"] if i.get("reason") else "")}</li>'
        for i in run.get("interventions") or [] if i.get("action") != "next")
    nonconform = "".join(f"<li>{e(n)}</li>" for n in run.get("nonconform") or [])
    warnings = "".join(f'<li class="warn">{e(w)}</li>' for w in review.get("warnings") or [])
    req = ""
    if requirements is not None and requirements.tests:
        from testsuit.reqs.render import matrix_html, tiles_html
        full = to.coverage(run_id=rid)
        req = ('<h2>Requirements</h2><div class="muted">The requirements this test and its checks verify, by this run, as reviewed (✎).'
               + (f' {a(full, "Full coverage report")}.' if full else "") + "</div>" + tiles_html(requirements) + matrix_html(requirements, to))
    files = ""
    if inputs:
        rows = "".join(f"<tr><td><code>{e(f['slot'])}</code></td><td>{a(to.input_preview(rid, f['path']), e(f['path']))}</td>"
                       f"<td>{e(_size(f.get('size')))}</td><td class=\"muted\">{e(f.get('note') or '')}</td></tr>" for f in inputs)
        files = (f'<h2>Input files</h2><div class="scroll"><table><thead><tr><th>Slot</th><th>File</th><th>Size</th><th></th></tr>'
                 f"</thead><tbody>{rows}</tbody></table></div>")
    title = run.get("testdef") or rid
    campaign = run.get("campaign_run")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(str(title))} · run report{e(title_suffix)}</title><style>{_CSS}</style></head>
<body><main>
{banner}
<h1>{e(str(title))} {verdict_html(run.get("verdict") or run.get("state"), run.get("raw_verdict"))}</h1>
<div class="muted">run {a(to.run(rid), f"<code>{e(rid)}</code>")}
 · datapack <code>{e(str(run.get("name") or ""))}</code> · submitted {e(str(run.get("submitted_at") or ""))}
 {" · ran " + e(_duration(_seconds(run.get("activated_at"), run.get("ended_at")))) if run.get("ended_at") else ""}
 {" · campaign run " + a(to.campaign(campaign), "<code>" + e(campaign) + "</code>") if campaign else ""}
 {" · runs again " + a(to.run(run["rerun_of"]), "<code>" + e(run["rerun_of"]) + "</code>") if run.get("rerun_of") else ""}</div>
{f'<div class="warn">{e(str(run["reason"]))}</div>' if run.get("reason") else ""}
<dl style="margin-top:12px">
<dt>Conform</dt><dd>{"yes" if run.get("conform") else '<span class="warn">no</span>'}</dd>
<dt>Ran on</dt><dd>{e(bound) or '<span class="muted">not bound</span>'}</dd>
<dt>Review</dt><dd>{e({"none": "none", "open": "open", "signed_off": "signed off"}.get(st, st))}
 {f"· {review.get('accepted', 0)} accepted, {review.get('rejected', 0)} rejected" if st != rv.NONE else ""}</dd>
<dt>Results</dt><dd>{a(to.results(rid), "results zip") if run.get("results") and to.results(rid) else '<span class="muted">' + ("in the sequencer" if run.get("results") else "none") + "</span>"}</dd>
</dl>
{f"<ul>{warnings}</ul>" if warnings else ""}
{f"<h2>Non-conformities</h2><ul>{nonconform}</ul>" if nonconform else ""}
{f"<h2>Operator interventions</h2><ul>{interventions}</ul>" if interventions else ""}
<h2>Steps</h2>
<div class="scroll"><table><thead><tr><th>Step</th><th>State</th><th>Duration</th><th>Exit</th><th>Reason, review</th><th></th></tr></thead>
<tbody>{"".join(step_rows) or '<tr><td colspan="6" class="muted">No step ran.</td></tr>'}</tbody></table></div>
{"<h2>Exploit checks</h2>" + "".join(exploit_parts) if exploit_parts else ""}
{_issues_html(issues)}
<h2>Review</h2>
{journal_html(journal or [])}
{req}
{files}
</main></body></html>
"""


def _issues_html(issues: list[dict] | None) -> str:
    if not issues:
        return ""
    e = html.escape
    rows = "".join(f'<tr class="issue {e(str(i.get("state") or ""))}"><td>'
                   + (f'<a href="{e(str(i["url"]))}">{e(str(i.get("key")))}</a>' if i.get("url") else e(str(i.get("key"))))
                   + f'</td><td>{e(str(i.get("state") or ""))}</td><td>{e(str(i.get("title") or ""))}</td>'
                   f'<td><code>{e(str(i.get("target") or ""))}</code></td></tr>' for i in issues)
    return ('<h2>Known issues</h2><div class="scroll"><table><thead><tr><th>Issue</th><th>State</th><th>Title</th>'
            f'<th>Of</th></tr></thead><tbody>{rows}</tbody></table></div>')


def _size(n) -> str:
    if n is None:
        return ""
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return ""
