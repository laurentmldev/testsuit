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

"""An issue of an issue tracker, and the references to it: ``<source>#<id>`` (``bench#42``, ``jira#BENCH-12``, a group
source ``infra#lab/power#3``), or its URL."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

OPEN, CLOSED = "open", "closed"


class IssueError(Exception):
    """An issue source cannot be read or written, or is not configured right."""


@dataclass
class Issue:
    source: str
    id: str                                   # 42, PROJ-12, or <project>#42 in a group source
    title: str = ""
    state: str = OPEN                         # open or closed
    url: str = ""
    labels: list[str] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)
    author: str = ""
    created: str = ""
    updated: str = ""
    body: str = ""
    project: str = ""                         # its repository or project
    links: list[str] = field(default_factory=list)   # keys of the issues it links (Jira issue links)

    @property
    def key(self) -> str:
        return f"{self.source}#{self.id}"

    def as_dict(self, body: bool = False) -> dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if v not in ("", [], None) or k in ("source", "id", "state")}
        if not body:
            out.pop("body", None)
        return {"key": self.key, **out}

    def brief(self) -> dict[str, Any]:
        """What a binding keeps of it: enough to show it without calling the tool."""
        return {"source": self.source, "id": self.id, "key": self.key, "title": self.title, "state": self.state,
                "url": self.url}
