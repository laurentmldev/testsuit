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

"""Coverage matrices as text, YAML, CSV or one self-contained HTML page.

Both views: requirements × tests (each requirement's status in each test, rolled up per path), and tests ×
requirements (each test, the test definition and each check with the requirements it verifies).
"""
from __future__ import annotations

import csv
import html
import io
from datetime import datetime
from urllib.parse import quote

import yaml

from testsuit.reqs.coverage import STATUSES, Coverage, Test

_MARK = {"passed": "✓", "failed": "✗", "error": "!", "partial": "◐", "skipped": "–", "not run": "·", "uncovered": "∅"}


def _counts(counts: dict[str, int]) -> str:
    return " · ".join(f"{s} {counts[s]}" for s in STATUSES if counts.get(s))


def text(cov: Coverage) -> str:
    out = []
    rows = cov.rows()
    width = max([len(k) for k in rows] + [11])
    out.append("Requirements × tests")
    head = f"{'requirement':<{width}}  {'coverage':<9}  " + "  ".join(t.key for t in cov.tests)
    out.append(head)
    for key in rows:
        cells = []
        for t in cov.tests:
            s = t.status(key)
            cell = "-" if s is None else f"{_MARK[s]} {s}" + (" (changed)" if cov.stale(key, t) else "")
            cells.append(f"{cell:<{len(t.key)}}")
        out.append(f"{key:<{width}}  {cov.status(key):<9}  " + "  ".join(cells))
    out.append("")
    out.append("Coverage by path")
    for node, counts in cov.by_path().items():
        out.append(f"  {node or '(all)'}: {_counts(counts)}")
    out.append("")
    out.append("Tests × requirements")
    for t in cov.tests:
        out.append(f"{t.label}" + (f"  verdict {t.verdict}" if t.run else "  not run"))
        if t.context:
            out.append(f"  context: {t.context_text}")
        for where, links in _groups(t).items():
            out.append(f"  {where}: " + ", ".join(f"{lk.req} {lk.status}" for lk in links))
        for p in t.problems:
            out.append(f"  problem: {p}")
        for w in t.warnings:
            out.append(f"  warning: {w}")
    if cov.universe is not None:
        out.append("")
        unc = cov.uncovered()
        out.append(f"Uncovered requirements ({len(unc)} of {len(cov.rows())})")
        out += [f"  {k}  {cov.requirements.get(k, {}).get('path', '')}  {cov.requirements.get(k, {}).get('title', '')}"
                for k in unc]
    for p in cov.problems:
        out.append(f"problem: {p}")
    return "\n".join(out) + "\n"


def _groups(t: Test) -> dict[str, list]:
    groups: dict[str, list] = {}
    for lk in t.links:
        groups.setdefault(lk.where, []).append(lk)
    return groups


def as_yaml(cov: Coverage) -> str:
    return yaml.safe_dump(cov.as_dict(), sort_keys=False, allow_unicode=True)


def as_csv(cov: Coverage, view: str = "reqs") -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    if view == "tests":
        w.writerow(["test", "run", "verdict", "where", "requirement", "status"])
        for t in cov.tests:
            for lk in t.links:
                w.writerow([t.testdef, t.run, t.verdict, lk.where, lk.req, lk.status])
    else:
        w.writerow(["requirement", "path", "title", "coverage", *[t.key for t in cov.tests]])
        for key in cov.rows():
            req = cov.requirements.get(key, {})
            w.writerow([key, req.get("path", ""), req.get("title", ""), cov.status(key),
                        *[(t.status(key) or "") + (" (changed)" if cov.stale(key, t) else "") for t in cov.tests]])
    return buf.getvalue()


CSS = """
:root { --bg:#fff; --fg:#1d2330; --muted:#667085; --line:#e4e7ec; --card:#f8f9fb; --link:#175cd3;
        --ok:#16794c; --okbg:#e7f6ee; --bad:#b42318; --badbg:#fdecea; --err:#93370d; --errbg:#fef0c7;
        --part:#175cd3; --partbg:#e8f0fe; --skip:#475467; --skipbg:#eef0f3; --none:#98a2b3; --nonebg:transparent; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171c; --fg:#e6e8eb; --muted:#98a2b3; --line:#2c323b; --card:#1b1f26;
        --link:#84adff; --ok:#6ce9a6; --okbg:#0f2d20; --bad:#fda29b; --badbg:#3a1512; --err:#fec84b; --errbg:#3a2a07;
        --part:#84adff; --partbg:#14254a; --skip:#cfd4dc; --skipbg:#262b33; --none:#667085; } }
body { background:var(--bg); color:var(--fg); font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; margin:0; padding:24px 16px; }
main { max-width:1200px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:16px; margin:28px 0 8px; }
.muted { color:var(--muted); } code { font-size:12.5px; } a { color:var(--link); }
.badge { display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px; font-weight:600; white-space:nowrap; }
.passed { background:var(--okbg); color:var(--ok); } .failed { background:var(--badbg); color:var(--bad); }
.error { background:var(--errbg); color:var(--err); } .partial { background:var(--partbg); color:var(--part); }
.skipped { background:var(--skipbg); color:var(--skip); } .not-run { color:var(--muted); border:1px solid var(--line); }
.uncovered { color:var(--bad); border:1px dashed var(--bad); }
.changed { color:var(--err); font-size:11px; }
.tiles { display:flex; flex-wrap:wrap; gap:10px; margin:16px 0; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:8px 14px; min-width:80px; }
.tile b { display:block; font-size:20px; }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; } th, td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { font-size:12px; color:var(--muted); font-weight:600; } td.c { text-align:center; }
tr.node td { background:var(--card); font-weight:600; }
.warn { color:var(--err); }
"""


def _badge(status: str | None, stale: bool = False) -> str:
    e = html.escape
    if status is None:
        return '<span class="muted">–</span>'
    cls = status.replace(" ", "-")
    return (f'<span class="badge {cls}" title="{e(status)}">{_MARK[status]} {e(status)}</span>'
            + ('<div class="changed">changed since</div>' if stale else ""))


def _req_link(cov: Coverage, key: str) -> str:
    e = html.escape
    url = cov.requirements.get(key, {}).get("url")
    return f'<a href="{e(url)}"><code>{e(key)}</code></a>' if url else f"<code>{e(key)}</code>"


def _test_head(t: Test, base: str) -> str:
    e = html.escape
    label = e(t.testdef)
    if t.run and base:
        label = f'<a href="{base}#/runs/{quote(t.run)}">{label}</a>'
    return label + (f'<div class="muted">{e(t.run)}</div>' if t.run else '<div class="muted">not run</div>') + (
        f'<div class="muted">{e(t.context_text)}</div>' if t.context else "")


def tiles_html(cov: Coverage) -> str:
    e = html.escape
    rows = cov.rows()
    totals: dict[str, int] = {}
    for key in rows:
        s = cov.status(key)
        totals[s] = totals.get(s, 0) + 1
    return (f'<div class="tiles"><div class="tile"><b>{len(rows)}</b>requirements</div>'
            + "".join(f'<div class="tile"><b>{totals[s]}</b>{e(s)}</div>' for s in STATUSES if totals.get(s)) + "</div>")


def matrix_html(cov: Coverage, base: str = "") -> str:
    """The requirements × tests table, a header row per path node with its counts."""
    e = html.escape
    by_path = cov.by_path()
    body, last = [], None
    for key in cov.rows():
        req = cov.requirements.get(key, {})
        path = str(req.get("path", "")).strip("/")
        if path != last:
            last = path
            body.append(f'<tr class="node"><td colspan="{3 + len(cov.tests)}"><code>{e(path or "(no path)")}</code> '
                        f'<span class="muted">{e(_counts(by_path.get(path, {})))}</span></td></tr>')
        title_text = req.get("title") or str(req.get("description", "")).split("\n")[0][:120]
        cells = "".join(f'<td class="c">{_badge(t.status(key), cov.stale(key, t))}</td>' for t in cov.tests)
        body.append(f"<tr><td>{_req_link(cov, key)}</td><td>{e(str(title_text))}</td><td>{_badge(cov.status(key))}</td>{cells}</tr>")
    heads = "".join(f"<th>{_test_head(t, base)}</th>" for t in cov.tests)
    return (f'<div class="scroll"><table><thead><tr><th>Requirement</th><th>Title</th><th>Coverage</th>{heads}</tr>'
            f'</thead><tbody>{"".join(body)}</tbody></table></div>')


def as_html(cov: Coverage, title: str = "Requirements coverage", sequencer: str = "") -> str:
    """The page. sequencer: the sequencer's root URL, for links to the runs and their exploit reports."""
    e = html.escape
    base = sequencer.rstrip("/") + "/" if sequencer else ""
    by_path = cov.by_path()
    path_rows = "".join(f'<tr><td><code>{e(node or "(all)")}</code></td>'
                        + "".join(f'<td class="c">{counts.get(s, "")}</td>' for s in STATUSES) + "</tr>"
                        for node, counts in by_path.items())
    path_table = (f'<table><thead><tr><th>Path</th>{"".join(f"<th>{e(s)}</th>" for s in STATUSES)}</tr></thead>'
                  f"<tbody>{path_rows}</tbody></table>")

    test_rows = []
    for t in cov.tests:
        verdict = _badge({"succeeded": "passed"}.get(t.verdict, t.verdict) if t.verdict in ("succeeded", "failed", "error")
                         else "not run")
        test_rows.append(f'<tr class="node"><td colspan="2">{_test_head(t, base)}</td><td>{verdict}</td></tr>')
        for where, links in _groups(t).items():
            report = ""
            step = links[0].step
            if base and t.run and step in t.reports:
                report = f' · <a href="{base}runs/{quote(t.run)}/files/{quote(t.reports[step])}">exploit report</a>'
            reqs = " ".join(f"{_req_link(cov, lk.req)} {_badge(lk.status, cov.stale(lk.req, t))}" for lk in links)
            test_rows.append(f"<tr><td></td><td><code>{e(where)}</code>{report}</td><td>{reqs}</td></tr>")
        for p in t.problems:
            test_rows.append(f'<tr><td></td><td colspan="2" class="warn">problem: {e(p)}</td></tr>')
        for w in t.warnings:
            test_rows.append(f'<tr><td></td><td colspan="2" class="warn">{e(w)}</td></tr>')
    test_table = (f'<div class="scroll"><table><thead><tr><th>Test</th><th>Verified by</th><th>Requirements</th></tr>'
                  f'</thead><tbody>{"".join(test_rows)}</tbody></table></div>')

    problems = "".join(f'<li class="warn">{e(p)}</li>' for p in cov.problems)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><style>{CSS}</style></head>
<body><main>
<h1>{e(title)}</h1>
<div class="muted">{len(cov.tests)} test(s) · generated {datetime.now().strftime("%Y-%m-%d %H:%M")}</div>
{tiles_html(cov)}
{f"<ul>{problems}</ul>" if problems else ""}
<h2>Requirements × tests</h2>
{matrix_html(cov, base)}
<h2>Coverage by path</h2>
{path_table}
<h2>Tests × requirements</h2>
{test_table}
</main></body></html>
"""
