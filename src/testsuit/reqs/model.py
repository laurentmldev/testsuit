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

"""A requirement, a node of a requirements tree, and a reference to a requirement.

A reference is ``[<source>:]<id>[@<version>]``: the source is a name of the sources file (none: its default source),
the version pins the revision the test was written against (none: the current one, recorded at generation).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# statuses of a requirement that make a link to it a problem
INACTIVE = ("deleted", "rejected")

_REF = re.compile(r"^(?P<body>[^@\s]+)(?:@(?P<version>[^@\s]+))?$")


@dataclass
class Requirement:
    id: str
    path: str = ""                       # node ids joined by '/'
    description: str = ""                # markdown
    title: str = ""
    rationale: str = ""
    tags: list[str] = field(default_factory=list)
    comments: str = ""
    version: str = ""                    # revision given by the tool (the YAML backend: a hash of the content)
    status: str = ""                     # lifecycle status; 'deleted' and 'rejected' make links to it problems
    url: str = ""                        # where to read it in its tool
    source: str = ""                     # name of its source in the sources file (set when fetched)
    dynamic_tags: list[str] = field(default_factory=list)   # the source's dynamic tags its texts match (not in its version)
    stale: str = ""                      # its server down: when this copy of it was read (the last known one)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Requirement:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        req = cls(**known)
        req.id = str(req.id)
        req.tags = [str(t) for t in (req.tags or [])]
        req.version = "" if req.version is None else str(req.version)
        return req

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in ("", [], None) or k == "id"}

    @property
    def inactive(self) -> bool:
        return str(self.status).lower() in INACTIVE


@dataclass
class Node:
    """A node of a requirements tree: its id (path component), a label to show, its children."""
    id: str
    label: str = ""
    children: list[Node] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label or self.id, "children": [c.as_dict() for c in self.children]}


@dataclass(frozen=True)
class Ref:
    source: str
    id: str
    version: str = ""


def split_ref(text: str, sources: tuple[str, ...] | list[str], default: str) -> Ref:
    """``[<source>:]<id>[@<version>]`` -> Ref. A prefix that is not a source name is part of the id."""
    m = _REF.match(str(text).strip())
    if not m:
        raise ValueError(f"'{text}' is not a requirement reference ([<source>:]<id>[@<version>])")
    body, version = m["body"], m["version"] or ""
    prefix, sep, rest = body.partition(":")
    if sep and prefix in sources and rest:
        return Ref(prefix, rest, version)
    return Ref(default, body, version)


def ref_key(ref: Ref, default: str) -> str:
    """How a requirement is named in the snapshot and the matrices: its id, prefixed by its source when not the default."""
    return ref.id if ref.source == default else f"{ref.source}:{ref.id}"
