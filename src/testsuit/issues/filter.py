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

"""The filter of an issue source: which of the issues its tool returns the source keeps (applied after the request).

``filter`` of a source (or of its class), every condition given must hold::

    filter:
      state: open                  # open, closed or all: the issues the source ever lists
      labels: [bench]              # every one of them
      any_labels: [bug, defect]    # at least one of them
      not_labels: [wontfix]        # none of them
      text: "speed|counter"        # a regular expression found in the title or the text (case independent)
      not_text: "duplicate of"     # ... not found
      title: "^\\[tsq\\]"           # a regular expression found in the title
      projects: ["fw/*"]           # a group source: the projects (globs on their path)
      authors: [alice, bob]        # opened by one of them
      assignees: [carol]           # assigned to one of them
      created_after: 2026-01-01    # a date (YYYY-MM-DD, or a date and time), or a time ago: 90d, 12w, 6m, 1y
      updated_after: 90d
      created_before / updated_before

Labels compare case independent. ``state`` and ``labels`` are given to the tool too (fewer issues read); the rest is
applied to what it returns. A filter keeps out issues listed or searched; an issue named by its reference is read
whatever the filter.
"""
from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

from testsuit.issues.model import CLOSED, OPEN, Issue, IssueError

KEYS = ("state", "labels", "any_labels", "not_labels", "text", "not_text", "title", "projects", "authors", "assignees",
        "created_after", "created_before", "updated_after", "updated_before")
AGO = re.compile(r"(\d+)\s*([dwmy])", re.I)
DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}


def _list(value: Any, key: str, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        return [str(value)]
    if not isinstance(value, list):
        raise IssueError(f"{where}: filter '{key}': a list of names")
    return [str(x) for x in value if str(x)]


def _rx(value: Any, key: str, where: str) -> re.Pattern | None:
    if value in (None, ""):
        return None
    try:
        return re.compile(str(value), re.I)
    except re.error as e:
        raise IssueError(f"{where}: filter '{key}': not a regular expression ({e})") from e


def _when(value: Any, key: str, where: str):
    """A date limit: a datetime (UTC), or a timedelta (a time ago, from now when applied)."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    m = AGO.fullmatch(text)
    if m:
        return timedelta(days=int(m.group(1)) * DAYS[m.group(2).lower()])
    found = parse_time(text)
    if found is None:
        raise IssueError(f"{where}: filter '{key}': a date (2026-01-31), a date and time, or a time ago (90d, 12w, 6m, 1y)")
    return found


def parse_time(text: str) -> datetime | None:
    """The time of an issue's date (ISO 8601, a Z or an offset; Jira's +0100 too), in UTC; None if it does not read."""
    text = str(text or "").strip()
    if not text:
        return None
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text.replace("Z", "+00:00"))
    try:
        found = datetime.fromisoformat(text)
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


class IssueFilter:
    """The conditions of a source's ``filter``, checked when the file is read."""

    def __init__(self, spec: Any, where: str = "filter") -> None:
        if spec is None:
            spec = {}
        if not isinstance(spec, Mapping):
            raise IssueError(f"{where}: 'filter' is a mapping of conditions ({', '.join(KEYS)})")
        unknown = sorted(set(map(str, spec)) - set(KEYS))
        if unknown:
            raise IssueError(f"{where}: filter: unknown key(s) {', '.join(unknown)} ({', '.join(KEYS)})")
        self.spec = dict(spec)
        state = str(spec.get("state") or "all").lower()
        if state not in (OPEN, CLOSED, "all"):
            raise IssueError(f"{where}: filter 'state': open, closed or all")
        self.state = None if state == "all" else state
        self.labels = _list(spec.get("labels"), "labels", where)
        self.any_labels = {x.lower() for x in _list(spec.get("any_labels"), "any_labels", where)}
        self.not_labels = {x.lower() for x in _list(spec.get("not_labels"), "not_labels", where)}
        self.text = _rx(spec.get("text"), "text", where)
        self.not_text = _rx(spec.get("not_text"), "not_text", where)
        self.title = _rx(spec.get("title"), "title", where)
        self.projects = _list(spec.get("projects"), "projects", where)
        self.authors = {x.lower() for x in _list(spec.get("authors"), "authors", where)}
        self.assignees = {x.lower() for x in _list(spec.get("assignees"), "assignees", where)}
        self.times = {k: _when(spec.get(k), k, where) for k in ("created_after", "created_before", "updated_after",
                                                                 "updated_before")}

    def __bool__(self) -> bool:
        return self.state is not None or bool(self.labels) or self.post

    @property
    def post(self) -> bool:
        """Whether it has conditions the tool is not asked (applied to what it returns)."""
        return any(self.spec.get(k) not in (None, "", [], {}) for k in KEYS if k not in ("state", "labels"))

    def state_for(self, asked: str | None) -> str | None | bool:
        """The state to ask the tool for, given the one asked (None: any); False: nothing can match."""
        if self.state is None:
            return asked
        if asked is None or asked == self.state:
            return self.state
        return False

    def match(self, issue: Issue, now: datetime | None = None) -> bool:
        labels = {x.lower() for x in issue.labels}
        if self.state and issue.state != self.state:
            return False
        if any(x.lower() not in labels for x in self.labels):
            return False
        if self.any_labels and not self.any_labels & labels:
            return False
        if self.not_labels & labels:
            return False
        text = f"{issue.title}\n{issue.body}"
        if self.text and not self.text.search(text):
            return False
        if self.not_text and self.not_text.search(text):
            return False
        if self.title and not self.title.search(issue.title or ""):
            return False
        if self.projects and not any(fnmatch.fnmatchcase(str(issue.project or ""), p) for p in self.projects):
            return False
        if self.authors and str(issue.author or "").lower() not in self.authors:
            return False
        if self.assignees and not self.assignees & {str(a).lower() for a in issue.assignees}:
            return False
        now = now or datetime.now(timezone.utc)
        for key, limit in self.times.items():
            if limit is None:
                continue
            at = parse_time(issue.created if key.startswith("created") else issue.updated)
            if at is None:
                return False
            bound = now - limit if isinstance(limit, timedelta) else limit
            if (key.endswith("after") and at < bound) or (key.endswith("before") and at >= bound):
                return False
        return True
