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

"""Exported reports of a campaign run or of a run (SPEC section 11.2), as reviewed.

- **synthetic**: the report as one HTML file, its links to the sequencer (its absolute URL).
- **complete**: every page from the campaign run down to each run (its report, its steps' logs, its exploit reports
  as reviewed, a preview of each input file) and the requirements coverage, navigable offline:
  - as **one HTML file** (``.html``): each page stored gzipped in it, opened when its link is followed (the browser's
    DecompressionStream), so nothing needs unpacking;
  - as a **zip** of the same pages in folders, relative links (``index.html`` at its top), when the file would be
    larger than ``SINGLE_FILE_MAX`` or the results zips are included; ``tsq view <zip>`` serves it without unpacking.

Logs over ``LOG_MAX`` are cut to their first and last lines, every line that looks like an error or a warning kept.
Input files are previewed up to ``PREVIEW_MAX_BYTES``: a plot of their parameters (``PREVIEW_TRACES`` of them, at
most ``PREVIEW_POINTS`` points each, min/max per bucket), the text of a text file, else listed only.
An export runs in the background: ``progress(stage, done, total)`` is told each unit done (a page, a log, a preview);
``cancelled()`` stops it between two.
"""
from __future__ import annotations

import base64
import gzip
import html
import json
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from testsuit.sequencer import review as rv
from testsuit.sequencer.reportlinks import OnlineLinks, PageLinks, safe_name

SYNTHETIC, COMPLETE = "synthetic", "complete"
SINGLE_FILE_MAX = 300 * 10**6        # an export larger than this (gzipped pages) is a zip
LOG_MAX = 2 * 10**6                  # a log larger than this is cut (its errors and warnings kept)
LOG_HEAD = LOG_TAIL = 2000           # lines kept at the start and at the end of a log cut
LOG_KEPT_MAX = 20000                 # error and warning lines kept, at most, from the middle of a log cut
PREVIEW_MAX_BYTES = 200 * 10**6      # an input file larger than this is listed, not previewed
PREVIEW_TRACES = 12
PREVIEW_POINTS = 4000
TEXT_PREVIEW_BYTES = 200_000
TEXT_SUFFIXES = {".txt", ".yml", ".yaml", ".json", ".xml", ".ini", ".cfg", ".conf", ".log", ".md", ".py", ".sh", ".toml"}
# a line that looks like an error or a warning (kept when a log is cut)
NOTABLE = re.compile(r"(?i)\b(error|errors|fail|failed|failure|fatal|critical|exception|traceback|panic|abort(ed)?|"
                     r"warn|warning|warnings|denied|refused|timeout|timed out|not found|cannot|unable)\b")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


class Cancelled(Exception):
    pass


@dataclass
class Export:
    """What an export writes: its pages (path -> HTML), its other files (path -> bytes or a file), its title."""
    title: str
    pages: dict[str, str] = field(default_factory=dict)
    files: dict[str, bytes | Path] = field(default_factory=dict)
    plotly: bool = False


def _noop(stage: str, done: int, total: int) -> None:
    pass


# ── logs ───────────────────────────────────────────────────────────────────

def trim_log(text: str, limit: int = LOG_MAX) -> tuple[str, int]:
    """A log cut to its first and last lines when larger than limit, every line between that looks like an error or a
    warning kept, gaps marked. Returns (text, lines left out)."""
    if len(text) <= limit:
        return text, 0
    lines = text.splitlines()
    if len(lines) <= LOG_HEAD + LOG_TAIL:                   # few huge lines: cut each
        return "\n".join(line[: limit // max(1, len(lines))] for line in lines), 0
    head, middle, tail = lines[:LOG_HEAD], lines[LOG_HEAD:-LOG_TAIL], lines[-LOG_TAIL:]
    out, skipped, left, kept = list(head), 0, 0, 0
    for line in middle:
        if kept < LOG_KEPT_MAX and NOTABLE.search(ANSI.sub("", line)):
            if skipped:
                out.append(f"[… {skipped} line(s) left out …]")
                left += skipped
                skipped = 0
            out.append(line)
            kept += 1
        else:
            skipped += 1
    if skipped:
        out.append(f"[… {skipped} line(s) left out …]")
        left += skipped
    return "\n".join(out + tail), left


_SGR = {"31": "ansi-red", "91": "ansi-red", "33": "ansi-yellow", "93": "ansi-yellow", "32": "ansi-green",
        "92": "ansi-green", "34": "ansi-blue", "94": "ansi-blue", "36": "ansi-cyan", "96": "ansi-cyan",
        "35": "ansi-magenta", "95": "ansi-magenta", "1": "ansi-bold", "90": "ansi-dim", "2": "ansi-dim"}


def _ansi_html(line: str) -> str:
    """A log line as HTML: its ANSI colors as classes, the rest escaped."""
    out, open_spans = [], 0
    pos = 0
    for m in re.finditer(r"\x1b\[([0-9;]*)m", line):
        out.append(html.escape(line[pos:m.start()]))
        pos = m.end()
        codes = [c for c in m.group(1).split(";") if c] or ["0"]
        if "0" in codes:
            out.append("</span>" * open_spans)
            open_spans = 0
        classes = " ".join(_SGR[c] for c in codes if c in _SGR)
        if classes:
            out.append(f'<span class="{classes}">')
            open_spans += 1
    out.append(html.escape(ANSI.sub("", line[pos:])))
    out.append("</span>" * open_spans)
    return "".join(out)


_PAGE_CSS = """
:root { --bg:#fff; --fg:#1d2330; --muted:#667085; --line:#e4e7ec; --card:#f8f9fb; --link:#175cd3; --err:#b42318;
        --errbg:#fdecea; --warn:#93370d; --warnbg:#fef0c7; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171c; --fg:#e6e8eb; --muted:#98a2b3; --line:#2c323b; --card:#1b1f26;
        --link:#84adff; --err:#fda29b; --errbg:#3a1512; --warn:#fec84b; --warnbg:#3a2a07; } }
body { background:var(--bg); color:var(--fg); font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; margin:0; padding:20px 16px; }
main { max-width:1200px; margin:0 auto; } a { color:var(--link); } .muted { color:var(--muted); }
h1 { font-size:18px; margin:0 0 6px; }
pre.log { background:var(--card); border:1px solid var(--line); border-radius:6px; padding:8px 10px; overflow-x:auto;
          font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
pre.log .l { display:block; white-space:pre-wrap; word-break:break-all; }
pre.log .e { background:var(--errbg); color:var(--err); } pre.log .w { background:var(--warnbg); color:var(--warn); }
pre.log .gap { color:var(--muted); font-style:italic; }
.ansi-red { color:#d92d20; } .ansi-yellow { color:#b54708; } .ansi-green { color:#079455; } .ansi-blue { color:#1570ef; }
.ansi-cyan { color:#0e9384; } .ansi-magenta { color:#c11574; } .ansi-bold { font-weight:700; } .ansi-dim { opacity:.7; }
.plot { width:100%; height:460px; } .facts { margin:8px 0 14px; }
"""

_ERRORISH = re.compile(r"(?i)\b(error|fail|failed|failure|fatal|critical|exception|traceback|panic)\b")


def log_page(text: str, title: str, back: str | None, limit: int = LOG_MAX) -> str:
    """A step's log as a page: error lines in red, warnings in yellow, its ANSI colors kept."""
    e = html.escape
    trimmed, left = trim_log(text, limit)
    lines = []
    for line in trimmed.splitlines():
        plain = ANSI.sub("", line)
        if line.startswith("[… ") and line.endswith(" …]"):
            lines.append(f'<span class="l gap">{e(line)}</span>')
            continue
        cls = "e" if _ERRORISH.search(plain) else ("w" if NOTABLE.search(plain) else "")
        lines.append(f'<span class="l{" " + cls if cls else ""}">{_ansi_html(line) or " "}</span>')
    note = (f'<div class="muted">Cut: {left} line(s) left out; the first {LOG_HEAD} and last {LOG_TAIL} lines are '
            f'here, with every line between that looks like an error or a warning.</div>' if left else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><style>{_PAGE_CSS}</style></head><body><main>
<h1>{e(title)}</h1><div class="muted">{f'<a href="{e(back)}">back to the run</a>' if back else ""}</div>{note}
<pre class="log">{"".join(lines)}</pre></main></body></html>"""


# ── input files ────────────────────────────────────────────────────────────

PLOTLY_MARK = "<!--tsq:plotly-->"


def _series_traces(path: Path) -> list[dict]:
    """The parameters of a data file as plotly traces (decimated); [] when no loader reads it."""
    import pandas as pd
    from testsuit.datatools.DataFileMgrs.formats import SUPPORTED_DATAFILE_EXTENSIONS
    from testsuit.datatools.datatoolbox import loadDataframeFromFile
    from testsuit.exploit.editor.plots import decimate
    from testsuit.misc.MonitorProgress import MonitorProgress
    name = path.name.lower()
    if not any(name.endswith(ext.lower()) for ext in SUPPORTED_DATAFILE_EXTENSIONS):
        return []
    found = loadDataframeFromFile(str(path), [".*"], silent=True,
                                  monitorProgress=MonitorProgress(progressCb=lambda *a, **k: None))
    traces = []
    for item in found or []:
        frames = [item[c] for c in item.columns] if isinstance(item, pd.DataFrame) else [item]
        for series in frames:
            if len(traces) >= PREVIEW_TRACES:
                break
            series = series.dropna()
            if series.empty or not pd.api.types.is_numeric_dtype(series):
                continue
            series = decimate(series, PREVIEW_POINTS)
            index = series.index
            x = [str(v) for v in index] if isinstance(index, pd.DatetimeIndex) else index.tolist()
            traces.append({"type": "scattergl", "mode": "lines", "name": str(series.name), "x": x,
                           "y": [None if v != v else float(v) for v in series.to_numpy(dtype=float)]})
    return traces


def preview_page(path: Path, rel: str, title: str, back: str | None, plotly_tag: str) -> tuple[str, str]:
    """A preview of an input file: a plot of its parameters, its text, or a note. Returns (page, note): note says what
    the preview holds (shown beside the file in the run report)."""
    e = html.escape
    size = path.stat().st_size
    body, note = "", ""
    if size > PREVIEW_MAX_BYTES:
        note = "too large to preview"
    elif path.suffix.lower() in TEXT_SUFFIXES:
        data = path.read_bytes()[:TEXT_PREVIEW_BYTES].decode("utf-8", "replace")
        body = f'<pre class="log">{e(data)}</pre>' + (
            f'<div class="muted">first {TEXT_PREVIEW_BYTES // 1000} kB of {size // 1000} kB</div>' if size > TEXT_PREVIEW_BYTES else "")
        note = "text"
    else:
        try:
            traces = _series_traces(path)
        except Exception as ex:                       # a loader failing on a file: listed only
            traces, note = [], f"not previewed: {type(ex).__name__}"
        if traces:
            figure = json.dumps({"data": traces, "layout": {"margin": {"t": 30, "r": 10}, "legend": {"orientation": "h"},
                                                            "title": {"text": path.name}}}).replace("</", "<\\/")
            body = (f'<div id="plot" class="plot"></div>{plotly_tag}<script>(function () {{ var f = {figure};'
                    f' function draw() {{ if (window.Plotly) Plotly.newPlot("plot", f.data, f.layout, {{responsive: true}});'
                    f' else document.getElementById("plot").textContent = "plotly.js is not loaded"; }}'
                    f' if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", draw); else draw(); }})();</script>'
                    f'<div class="muted">{len(traces)} parameter(s), at most {PREVIEW_POINTS} points each (min and max of '
                    f'buckets, peaks kept).</div>')
            note = f"plot of {len(traces)} parameter(s)"
        elif not note:
            note = "no preview for this format"
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><style>{_PAGE_CSS}</style></head><body><main>
<h1>{e(title)}</h1><div class="muted">{f'<a href="{e(back)}">back to the run</a> · ' if back else ""}<code>{e(rel)}</code> · {size} bytes</div>
<div class="facts"></div>{body or f'<p class="muted">{e(note)}.</p>'}</main></body></html>"""
    return page, note


# ── what a run gives ───────────────────────────────────────────────────────

def run_inputs(run: dict) -> list[tuple[str, str, int]]:
    """The input files of a run: (slot, path in the run folder, size), from its run folder, else its results zip."""
    out = []
    folder = Path(run["dir"]) if run.get("dir") else None
    if folder is not None and (folder / "data").is_dir():
        for slot in sorted(p for p in (folder / "data").iterdir() if p.is_dir() and not p.name.startswith("_")):
            for f in sorted((slot / "input").rglob("*")) if (slot / "input").is_dir() else []:
                if f.is_file():
                    out.append((slot.name, f.relative_to(folder).as_posix(), f.stat().st_size))
        return out
    if run.get("results") and Path(run["results"]).is_file():
        with zipfile.ZipFile(run["results"]) as z:
            for info in z.infolist():
                parts = info.filename.split("/")[1:]
                if len(parts) >= 4 and parts[0] == "data" and parts[2] == "input" and not parts[1].startswith("_") \
                        and not info.is_dir():
                    out.append((parts[1], "/".join(parts), info.file_size))
    return out


def _materialize(seq, run: dict, rel: str, tmp: Path) -> Path | None:
    """A file of a run as a file on disk (a member of its results zip extracted under tmp)."""
    folder = Path(run["dir"]) if run.get("dir") else None
    if folder is not None and (folder / rel).is_file():
        return folder / rel
    data = seq.read_run_file(run, rel)
    if data is None:
        return None
    path = tmp / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _run_folder_name(entry: dict, taken: set[str]) -> str:
    base = safe_name(str(entry.get("testdef") or entry.get("testdef_file") or entry["run"]))
    if entry.get("setup_config"):
        base += "@" + safe_name(entry["setup_config"])
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}-{n}"
    taken.add(name)
    return f"runs/{name}/attempt-{entry.get('attempt') or 1}"


def _units(seq, runs: list[dict]) -> int:
    n = 2
    for run in runs:
        n += 1 + sum(1 for s in run.get("steps") or [] if s.get("log")) \
            + sum(1 for s in run.get("steps") or [] if (s.get("exploit") or {}).get("report")) + len(run_inputs(run))
    return n


class _Progress:
    def __init__(self, progress, cancelled, total: int):
        self.progress, self.cancelled, self.total, self.done = progress or _noop, cancelled or (lambda: False), total, 0

    def step(self, stage: str) -> None:
        if self.cancelled():
            raise Cancelled("export cancelled")
        self.done += 1
        self.progress(stage, min(self.done, self.total), self.total)


def _add_run(seq, export: Export, run: dict, folder: str, links: PageLinks, prog: _Progress, tmp: Path,
             title_suffix: str = "") -> None:
    """A run's pages: its report, its logs, its exploit reports as reviewed, previews of its input files."""
    from testsuit.sequencer.runreport import run_report_html
    rid = run["id"]
    me = f"{folder}/index.html"
    for s in run.get("steps") or []:
        key = rv.step_key(s)
        if s.get("log"):
            data = seq.read_run_file(run, s["log"])
            path = PageLinks.log_path(folder, key)
            if data is not None:
                export.pages[path] = log_page(data.decode("utf-8", "replace"), f"{run.get('testdef') or rid} · log of {key}",
                                              links.at(path).rel(me))
                links.held.add(path)
            prog.step("logs")
        if (s.get("exploit") or {}).get("report"):
            path = PageLinks.exploit_path(folder, key)
            try:
                export.pages[path] = seq.exploit_report_html(rid, key)
                links.held.add(path)
            except Exception:                               # gone (the run folder and its zip): linked to the sequencer
                pass
            prog.step("exploit reports")
    inputs = []
    for slot, rel, size in run_inputs(run):
        path = PageLinks.preview_path(folder, rel)
        note = ""
        local = _materialize(seq, run, rel, tmp)
        if local is not None:
            page, note = preview_page(local, rel, f"{run.get('testdef') or rid} · {rel}", links.at(path).rel(me), PLOTLY_MARK)
            export.pages[path] = page
            export.plotly = export.plotly or PLOTLY_MARK in page and "Plotly.newPlot" in page
            links.held.add(path)
        inputs.append({"slot": slot, "path": rel, "size": size, "note": note})
        prog.step("input previews")
    review = seq.run_review(rid)
    export.pages[me] = run_report_html(seq.reviewed(run), review.get("entries") or [], links.at(me),
                                       seq.run_requirements(rid), inputs, title_suffix,
                                       issues=seq.tickets.known_brief(seq.reviewed(run)))
    export.files[f"{folder}/review.yml"] = yaml.safe_dump(review, sort_keys=False, allow_unicode=True).encode()
    prog.step("run reports")


def campaign_export(seq, campaign_id: str, mode: str = COMPLETE, sequencer_url: str = "", include_results: bool = False,
                    progress: Callable | None = None, cancelled: Callable | None = None) -> Export:
    """The pages of a campaign run's export (synthetic: the report alone)."""
    from testsuit.reqs.render import as_html
    from testsuit.sequencer.campaigns import report_html
    view = seq.campaign_view(campaign_id)
    title = f"{view.get('title') or view['campaign']} ({view['id']})"
    online = OnlineLinks(sequencer_url or None)
    cov = seq.campaign_requirements(view["id"])
    if mode == SYNTHETIC:
        prog = _Progress(progress, cancelled, 1)
        export = Export(title, {"index.html": report_html(view, requirements=cov, links=online)})
        prog.step("campaign report")
        return export
    runs = {r["id"]: r for r in seq.store.campaign_runs_of(view["id"])}
    last = [runs[e["run"]] for e in view["runs"] if e["run"] in runs]
    prog = _Progress(progress, cancelled, _units(seq, last))
    taken: set[str] = set()
    pages = {e["run"]: _run_folder_name(e, taken) for e in view["runs"] if e["run"] in runs}
    links = PageLinks(pages, {"index.html", "coverage.html", *(f"{f}/index.html" for f in pages.values())}, online)
    export = Export(title)
    with tempfile.TemporaryDirectory() as tmp:
        for entry in view["runs"]:
            run = runs.get(entry["run"])
            if run is None:
                continue
            _add_run(seq, export, run, pages[run["id"]], links, prog, Path(tmp))
            if include_results and run.get("results") and Path(run["results"]).is_file():
                path = f"{pages[run['id']]}/results.zip"
                export.files[path] = Path(run["results"])
                links.held.add(path)
    export.pages["coverage.html"] = as_html(cov, f"{view.get('title') or view['campaign']}: requirements coverage",
                                            links.at("coverage.html"))
    prog.step("coverage")
    export.pages["index.html"] = report_html(view, requirements=cov, links=links.at("index.html"))
    export.files["review.yml"] = yaml.safe_dump(seq.campaign_review(view["id"]), sort_keys=False, allow_unicode=True).encode()
    prog.step("campaign report")
    return export


def run_export(seq, run_id: str, mode: str = COMPLETE, sequencer_url: str = "", include_results: bool = False,
               progress: Callable | None = None, cancelled: Callable | None = None) -> Export:
    """The pages of a run's export (synthetic: its report alone)."""
    from testsuit.sequencer.runreport import run_report_html
    run = seq.live_run(run_id)
    title = f"{run.get('testdef') or run['id']} ({run['id']})"
    online = OnlineLinks(sequencer_url or None)
    if mode == SYNTHETIC:
        prog = _Progress(progress, cancelled, 1)
        export = Export(title, {"index.html": run_report_html(seq.reviewed(run), seq.run_review(run["id"]).get("entries") or [],
                                                              online, seq.run_requirements(run["id"]),
                                                              issues=seq.tickets.known_brief(seq.reviewed(run)))})
        prog.step("run report")
        return export
    prog = _Progress(progress, cancelled, _units(seq, [run]) - 1)
    links = PageLinks({run["id"]: "."}, {"./index.html", "index.html"}, online, "index.html")
    export = Export(title)
    with tempfile.TemporaryDirectory() as tmp:
        _add_run(seq, export, run, ".", links, prog, Path(tmp))
    export.pages = {k.removeprefix("./"): v for k, v in export.pages.items()}
    export.files = {k.removeprefix("./"): v for k, v in export.files.items()}
    if include_results and run.get("results") and Path(run["results"]).is_file():
        export.files["results.zip"] = Path(run["results"])
    return export


# ── writing it ─────────────────────────────────────────────────────────────

def _gz64(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return base64.b64encode(gzip.compress(data, 6)).decode("ascii")


_SHELL = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root { --bg:#fff; --fg:#1d2330; --muted:#667085; --line:#e4e7ec; --card:#f8f9fb; --link:#175cd3; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171c; --fg:#e6e8eb; --muted:#98a2b3; --line:#2c323b; --card:#1b1f26; --link:#84adff; } }
html, body { margin:0; height:100%; background:var(--bg); color:var(--fg); font:13px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
#bar { display:flex; gap:8px; align-items:center; padding:6px 12px; border-bottom:1px solid var(--line); background:var(--card); }
#bar button { font:inherit; padding:2px 10px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); cursor:pointer; }
#bar button:disabled { opacity:.5; cursor:default; }
#where { color:var(--muted); font-family:ui-monospace,Menlo,Consolas,monospace; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1; }
#frame { display:block; width:100%; height:calc(100% - 38px); border:0; background:var(--bg); }
#msg { padding:24px; color:var(--muted); }
</style></head>
<body>
<div id="bar"><button id="back" title="Back">←</button><button id="home" title="The top page">Top</button>
<span id="where"></span><span class="muted" style="color:var(--muted)">__NOTE__</span></div>
<div id="msg">Opening the report…</div>
<iframe id="frame" title="report page" hidden></iframe>
<script id="pages" type="application/json">__PAGES__</script>
<script id="libs" type="application/json">__LIBS__</script>
<script>
(function () {
  var PAGES = JSON.parse(document.getElementById("pages").textContent);
  var LIBS = JSON.parse(document.getElementById("libs").textContent);
  var frame = document.getElementById("frame"), msg = document.getElementById("msg");
  var cache = {}, current = null;
  function inflate(b64) {
    var bin = atob(b64), bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    if (!window.DecompressionStream) return Promise.reject(new Error("this browser cannot open this report (no DecompressionStream): use a recent Chrome, Edge, Firefox or Safari"));
    var stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    return new Response(stream).text();
  }
  function lib(name) { return cache["lib:" + name] || (cache["lib:" + name] = inflate(LIBS[name])); }
  function resolve(href, base) {
    var u = new URL(href, "https://report.invalid/" + base);
    if (u.origin !== "https://report.invalid") return null;
    return { path: decodeURIComponent(u.pathname.slice(1)), hash: u.hash };
  }
  var NAV = "<script>(function(){document.addEventListener('click',function(ev){var a=ev.target.closest&&ev.target.closest('a[href]');" +
    "if(!a||ev.defaultPrevented||ev.button!==0||ev.metaKey||ev.ctrlKey)return;var h=a.getAttribute('href');if(!h||h.charAt(0)==='#')return;" +
    "ev.preventDefault();parent.postMessage({tsqReportLink:h},'*');},false);})();<\/script>";
  function show(path, hash, push) {
    if (!(path in PAGES)) { msg.textContent = "This report does not hold " + path + "."; msg.hidden = false; return; }
    msg.textContent = "Opening " + path + "…"; msg.hidden = false;
    (cache[path] || (cache[path] = inflate(PAGES[path]))).then(function (page) {
      var needs = page.indexOf("<!--tsq:plotly-->") >= 0 && LIBS.plotly;
      return (needs ? lib("plotly") : Promise.resolve(null)).then(function (plotly) {
        if (plotly) page = page.replace("<!--tsq:plotly-->", "<script>" + plotly.replace(/<\/script/gi, "<\\/script") + "<\/script>");
        var at = page.lastIndexOf("</body>");
        page = at >= 0 ? page.slice(0, at) + NAV + page.slice(at) : page + NAV;
        current = path;
        frame.srcdoc = page;
        frame.hidden = false; msg.hidden = true;
        document.getElementById("where").textContent = path;
        document.getElementById("back").disabled = history.length < 2;
        if (push) history.pushState({ path: path }, "", "#" + encodeURIComponent(path));
        if (hash) frame.onload = function () { try { var t = frame.contentDocument.getElementById(hash.slice(1)); if (t) t.scrollIntoView(); } catch (e) {} };
      });
    }).catch(function (e) { msg.textContent = String(e && e.message || e); msg.hidden = false; });
  }
  window.addEventListener("message", function (ev) {
    if (ev.source !== frame.contentWindow || !ev.data || !ev.data.tsqReportLink) return;
    var href = ev.data.tsqReportLink, target = resolve(href, current || "index.html");
    if (target && target.path in PAGES) show(target.path, target.hash, true);
    else if (/^https?:/i.test(href)) window.open(href, "_blank", "noopener");
    else { msg.textContent = "Not in this report: " + (target ? target.path : href) + " (open it from the sequencer)."; msg.hidden = false; }
  });
  window.addEventListener("popstate", function () { show(decodeURIComponent(location.hash.slice(1)) || "index.html", "", false); });
  document.getElementById("back").onclick = function () { history.back(); };
  document.getElementById("home").onclick = function () { show("index.html", "", true); };
  show(decodeURIComponent(location.hash.slice(1)) || "index.html", "", false);
})();
</script>
</body></html>
"""


def write_single_file(export: Export, out: Path, plotly_js: str | None = None) -> Path:
    """The export as one HTML file: its pages gzipped in it, opened on demand (no unpacking)."""
    pages = {path: _gz64(page) for path, page in export.pages.items()}
    libs = {"plotly": _gz64(plotly_js)} if plotly_js else {}
    shell = (_SHELL.replace("__TITLE__", html.escape(export.title))
             .replace("__NOTE__", html.escape(f"{len(pages)} pages · opened in place, nothing to unpack"))
             .replace("__PAGES__", json.dumps(pages).replace("</", "<\\/"))
             .replace("__LIBS__", json.dumps(libs).replace("</", "<\\/")))
    out.write_text(shell, encoding="utf-8")
    return out


def single_file_size(export: Export) -> int:
    """About how large the single file would be (its pages gzipped, base64)."""
    return sum(len(gzip.compress(p.encode("utf-8"), 1)) * 4 // 3 for p in export.pages.values())


_README = """This is the complete report of {title}, exported by the testsuit sequencer.

Open index.html in a browser after extracting this zip: its links between pages are relative, and do not work from
inside a zip opened in place (Windows Explorer, for example). Or, with testsuit installed, without extracting:

    tsq view {name}
"""


def write_zip(export: Export, out: Path, plotly_js: str | None = None) -> Path:
    """The export as a zip of folders of pages (relative links), index.html at its top."""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for path, page in export.pages.items():
            if PLOTLY_MARK in page and plotly_js:
                depth = path.count("/")
                page = page.replace(PLOTLY_MARK, f'<script src="{"../" * depth}assets/plotly.min.js"></script>')
            z.writestr(path, page)
        for path, data in export.files.items():
            if isinstance(data, Path):
                z.write(data, path, compress_type=zipfile.ZIP_STORED)
            else:
                z.writestr(path, data)
        if plotly_js and export.plotly:
            z.writestr("assets/plotly.min.js", plotly_js)
        z.writestr("README.txt", _README.format(title=export.title, name=out.name))
    return out


def write(export: Export, out_base: Path, include_results: bool = False, single_max: int = SINGLE_FILE_MAX,
          synthetic: bool = False) -> Path:
    """Write an export: one HTML file when it fits (and has no results zips), else a zip. out_base: the path without
    its suffix. Returns the file written."""
    out_base.parent.mkdir(parents=True, exist_ok=True)
    plotly_js = None
    if export.plotly:
        from testsuit.exploit.editor.plots import plotly_js as get_plotly_js
        plotly_js = get_plotly_js()
    if synthetic:
        out = out_base.with_suffix(".html")
        out.write_text(export.pages["index.html"], encoding="utf-8")
        return out
    has_zips = any(isinstance(v, Path) for v in export.files.values())
    if not include_results and not has_zips and single_file_size(export) <= single_max:
        return write_single_file(export, out_base.with_suffix(".html"), plotly_js)
    return write_zip(export, out_base.with_suffix(".zip"), plotly_js)


# ── tsq view: a zip export served without unpacking ────────────────────────

def serve_zip(path: Path, port: int = 0, open_browser: bool = True) -> None:
    """Serve an exported zip on localhost (its pages read from the zip), until interrupted."""
    import mimetypes
    import threading
    import webbrowser
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import unquote, urlparse
    archive = zipfile.ZipFile(path)
    names = set(archive.namelist())
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            name = unquote(urlparse(self.path).path).lstrip("/") or "index.html"
            if name.endswith("/"):
                name += "index.html"
            if name not in names:
                self.send_error(404, f"not in {path.name}: {name}")
                return
            with lock:
                data = archive.read(name)
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
    print(f"serving {path} at {url} (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        archive.close()


def page_of(path: Path, page: str = "index.html") -> str:
    """A page of an exported report (single HTML file or zip), for tests and tools."""
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            return z.read(page).decode("utf-8")
    text = path.read_text(encoding="utf-8")
    pages = json.loads(re.search(r'<script id="pages" type="application/json">(.*?)</script>', text, re.S).group(1)
                       .replace("<\\/", "</"))
    return gzip.decompress(base64.b64decode(pages[page])).decode("utf-8")

