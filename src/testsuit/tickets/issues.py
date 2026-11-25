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

"""Issues about test results: what a new one says (pre-filled from a failure), the mark that finds it again, and the
requirements an issue is about.

- A **target** is what failed, as reviews name it (testsuit.sequencer.review): a check, a step or a run's verdict; its
  **fingerprint** is the test definition and the target, the same from one run to the next: an issue bound to it is a
  known issue of every later failure of the same check.
- The body of an issue tsq creates ends with ``tsq:fingerprint=<hex>``, hidden in an HTML comment where Markdown
  renders it (GitLab, GitHub): an issue already open for the same failure is found by it before another is created.
- The requirements of an issue: those of its target (the links of the check, step or test definition: derived), and
  those it names itself (explicit: a label ``req:<id>``, the id of a known requirement in its title or text, a Jira
  issue link).
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from testsuit.tickets.model import Issue

MARK = re.compile(r"tsq:fingerprint=([0-9a-f]{12})")
EXCERPT_LINES = 40


def fingerprint(testdef: str, target: Mapping[str, Any]) -> str:
    """The test definition and the target (a slot of a step run on several left out): the same in every run."""
    kind = target.get("kind")
    step = re.sub(r"\[[^\]]*\]$", "", str(target.get("step") or ""))
    parts = [str(testdef), str(kind)]
    if kind in ("check", "step"):
        parts.append(step)
    if kind == "check":
        parts += [str(target.get(k) or "") for k in ("criteria", "scenario", "check")]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def mark_of(issue: Issue) -> str | None:
    m = MARK.search(issue.body or "")
    return m.group(1) if m else None


def step_id(step: str) -> str:
    """The step id of a step key ``<phase>:<step>[<slot>]``."""
    return re.sub(r"\[[^\]]*\]$", "", str(step or "")).split(":", 1)[-1]


def what(target: Mapping[str, Any]) -> str:
    kind = target.get("kind")
    if kind == "check":
        return f"check {target.get('check') or 'of the whole scenario'}"
    if kind == "step":
        return f"step {target.get('step')}"
    return "verdict"


def req_labels(rules: Mapping[str, Any], reqs: Iterable[str]) -> list[str]:
    pattern = rules.get("labels")
    if not pattern or not rules.get("add", True):
        return []
    return [str(pattern).replace("{id}", str(r)).replace(" ", "_") for r in reqs]


def draft(run: Mapping[str, Any], target: Mapping[str, Any], status: str, links: Mapping[str, str | None],
          reqs: list[str], excerpt: str = "", markdown: bool = True, by: str = "") -> dict[str, Any]:
    """The title and body of a new issue about a target of a run (as reviewed): its status, the run, where it ran,
    links to its reports and log, the requirements, the end of its log, and the fingerprint."""
    testdef = str(run.get("testdef") or run.get("name") or run.get("id"))
    fp = fingerprint(testdef, target)
    title = f"[tsq] {testdef}: {what(target)} {str(status).lower()}"
    binding = run.get("binding") or {}
    bound = ", ".join(f"{k}={v}" for k, v in {**(binding.get("stage_slots") or {}), **(binding.get("slots") or {})}.items())
    rows: list[tuple[str, str]] = []

    def link(text: str, url: str | None) -> str:
        if not url:
            return text
        return f"[{text}]({url})" if markdown else f"[{text}|{url}]"
    detail = ""
    if target.get("kind") == "check":
        detail = f" (criteria {target.get('criteria')}, scenario {target.get('scenario')}, step {target.get('step')})"
    rows.append(("Test definition", testdef))
    rows.append(("Run", link(str(run.get("id")), links.get("run")) + f", verdict {run.get('verdict') or run.get('state')}"
                 + (f" (raw {run['raw_verdict']})" if run.get("raw_verdict") and run.get("raw_verdict") != run.get("verdict") else "")
                 + (f", ended {run['ended_at']}" if run.get("ended_at") else "")))
    rows.append(("Datapack", str(run.get("name") or "")))
    if run.get("campaign_run"):
        rows.append(("Campaign run", link(str(run["campaign_run"]), links.get("campaign"))))
    if bound:
        rows.append(("Ran on", bound))
    if links.get("exploit"):
        rows.append(("Exploit report", link("as reviewed", links["exploit"])))
    if links.get("log"):
        rows.append(("Log", link(str(target.get("step") or "log"), links["log"])))
    if links.get("report"):
        rows.append(("Run report", link("report", links["report"])))
    if reqs:
        rows.append(("Requirements", ", ".join(reqs)))
    if markdown:
        lines = [f"**{testdef}**: {what(target)}{detail} is **{str(status).lower()}**.", "", "| | |", "|---|---|"]
        lines += [f"| {k} | {v.replace('|', '/')} |" for k, v in rows]
        if excerpt:
            lines += ["", "<details><summary>Log excerpt</summary>", "", "```", excerpt.rstrip(), "```", "", "</details>"]
        lines += ["", f"Reported{' by ' + by if by else ''} with tsq.", f"<!-- tsq:fingerprint={fp} -->"]
    else:                                        # Jira (wiki markup)
        lines = [f"*{testdef}*: {what(target)}{detail} is *{str(status).lower()}*.", ""]
        lines += [f"* {k}: {v}" for k, v in rows]
        if excerpt:
            lines += ["", "{noformat}", excerpt.rstrip(), "{noformat}"]
        lines += ["", f"Reported{' by ' + by if by else ''} with tsq. tsq:fingerprint={fp}"]
    return {"title": title[:250], "body": "\n".join(lines), "fingerprint": fp}


def excerpt(log: str, lines: int = EXCERPT_LINES) -> str:
    """The lines of a log worth an issue: those that look like errors or warnings, else its last ones."""
    from testsuit.sequencer.export import ANSI, NOTABLE
    text = ANSI.sub("", log or "")
    all_lines = text.splitlines()
    notable = [x for x in all_lines if NOTABLE.search(x)]
    picked = notable[-lines:] if notable else all_lines[-lines:]
    return "\n".join(x[:300] for x in picked)


class RequirementMatcher:
    """Which known requirements an issue names: by label, by their id in its text, by Jira issue link."""

    def __init__(self, known: Iterable[str]) -> None:
        self.by_text: dict[str, str] = {}        # what can be written -> requirement key
        for key in known:
            self.by_text.setdefault(key, key)
            bare = key.split(":", 1)[-1]
            if bare != key:
                self.by_text.setdefault(bare, key)
        words = sorted(self.by_text, key=len, reverse=True)
        self.pattern = re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in words) + r")(?![\w-])") if words else None

    def match(self, issue: Issue, rules: Mapping[str, Any]) -> dict[str, str]:
        """{requirement key: how the issue names it}."""
        out: dict[str, str] = {}
        pattern = rules.get("labels")
        if pattern:
            rx = re.compile("^" + re.escape(str(pattern)).replace(r"\{id\}", "(.+)") + "$")
            for label in issue.labels:
                m = rx.match(label)
                if m and m.group(1) in self.by_text:
                    out.setdefault(self.by_text[m.group(1)], f"label {label}")
        if rules.get("text", True) and self.pattern is not None:
            for m in self.pattern.finditer(f"{issue.title}\n{issue.body}"):
                out.setdefault(self.by_text[m.group(1)], "named in it")
        if rules.get("jira_links", True):
            for key in issue.links:
                if key in self.by_text:
                    out.setdefault(self.by_text[key], "issue link")
        return out


def derived_requirements(binding: Mapping[str, Any], tests: Iterable[Any]) -> dict[str, str]:
    """{requirement key: via} of a binding's target, from the links of the tests of its test definition
    (testsuit.reqs.coverage.Test)."""
    target = binding.get("target") or {}
    kind = target.get("kind")
    out: dict[str, str] = {}
    for t in tests:
        if str(t.testdef) != str(binding.get("testdef")):
            continue
        for lk in t.links:
            if kind == "check":
                if lk.level == "check" and lk.check == target.get("check") and lk.scenario == target.get("scenario") \
                        and step_id(target.get("step")) == step_id(lk.step):
                    out.setdefault(lk.req, f"check {lk.check}")
            elif kind == "step":
                if lk.level == "check" and step_id(target.get("step")) == step_id(lk.step):
                    out.setdefault(lk.req, f"step {target.get('step')}")
            else:
                out.setdefault(lk.req, f"verdict of {t.testdef}")
    return out


def issues_by_requirement(issues: Iterable[Issue], rules_of: Mapping[str, Mapping[str, Any]], known: Iterable[str],
                          bindings: Iterable[Mapping[str, Any]] = (), tests: Iterable[Any] = ()) -> dict[str, list[dict]]:
    """{requirement key: [{issue, via: [...]}]}: the issues listed that name it (explicit), and the issues bound to a
    check, step or verdict that verifies it (derived; their state as listed when they are, else as last seen)."""
    known = list(known)
    matcher = RequirementMatcher(known)
    tests = list(tests)
    found: dict[str, dict[str, dict]] = {}
    listed = {i.key: i for i in issues}
    for issue in listed.values():
        for req, via in matcher.match(issue, rules_of.get(issue.source) or {}).items():
            entry = found.setdefault(req, {}).setdefault(issue.key, {"issue": issue.brief() | {
                "labels": issue.labels, "assignees": issue.assignees}, "via": []})
            entry["via"].append(via)
    for b in bindings:
        if b.get("removed"):
            continue
        brief = dict(b.get("issue") or {})
        key = str(brief.get("key") or f"{brief.get('source')}#{brief.get('id')}")
        if key in listed:
            brief = listed[key].brief() | {"labels": listed[key].labels, "assignees": listed[key].assignees}
        for req, via in derived_requirements(b, tests).items():
            entry = found.setdefault(req, {}).setdefault(key, {"issue": brief, "via": []})
            if via not in entry["via"]:
                entry["via"].append(via)
    return {req: sorted(v.values(), key=lambda e: (e["issue"].get("state") != "open", e["issue"].get("key")))
            for req, v in found.items()}
