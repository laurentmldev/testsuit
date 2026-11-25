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

"""The built-in Redmine backend: the issues of a project (and of its subprojects), on any Redmine server.

Options: ``url`` (the server, required), ``project`` (its identifier or id, required), ``tracker`` (the tracker of new
issues, by name or id; default: the project's first), ``markup`` (the server's text formatting: ``textile``, the
default, or ``markdown`` for CommonMark/Markdown), ``verify`` (TLS: false, or a CA bundle), ``timeout``.
Credentials: ``api_key`` (My account > API access key; the REST API must be enabled in Administration > Settings >
API), sent as ``X-Redmine-API-Key``; or ``user`` and ``password`` (basic authentication).

Redmine issues have no labels: an issue's labels are its tracker and its category, and the labels of a new issue are
not sent (an issue about requirements names them in its text, which is enough to relate them). Issue ids are global
to the server, so one source reads the issues of a project and of its subprojects.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from testsuit.issues.backend import MAX_PAGES, Api, IssueBackend, need, register_issue_backend
from testsuit.issues.model import CLOSED, OPEN, Issue, IssueError

PAGE = 100


class RedmineBackend(IssueBackend):
    """Redmine issues, through its REST API"""

    description = "Redmine issues of a project and its subprojects"
    capabilities = frozenset({"create", "comment"})
    options_help = {"url": "The Redmine server.", "project": "The project: its identifier or its id.",
                    "tracker": "The tracker of new issues: its name or id (default: the project's first).",
                    "markup": "The server's text formatting: textile (default) or markdown.",
                    "verify": "TLS check of the server: false (a self-signed certificate: not checked), or the file of the CA bundle that signs it.", "timeout": "Seconds per call (20)."}
    credentials_help = {"api_key": "Your API access key (My account); the server must enable its REST API.",
                        "user": "A user, with password (basic authentication) instead of a key.",
                        "password": "That user's password."}

    auth_methods = {"api_key": {"label": "API access key", "credentials": ["api_key"]},
                    "basic": {"label": "User and password", "credentials": ["user", "password"]}}

    def __init__(self, options, credentials, base_dir) -> None:
        super().__init__(options, credentials, base_dir)
        self.server = need(self.options, "url", "redmine").rstrip("/")
        self.project = need(self.options, "project", "redmine")
        markup = str(self.options.get("markup") or "textile").lower()
        if markup not in ("textile", "markdown", "common_mark", "commonmark"):
            raise IssueError(f"redmine backend: markup '{markup}': textile or markdown")
        self.markup = "textile" if markup == "textile" else "markdown"
        headers, auth = {}, None
        key = self.credentials.get("api_key") or self.credentials.get("token")
        if key:
            headers["X-Redmine-API-Key"] = key
        elif self.credentials.get("user"):
            auth = (self.credentials["user"], self.credentials.get("password") or "")
        self.api = Api(self, self.server, headers, auth)
        self._tracker: int | None = None

    def with_token(self, token: str) -> "RedmineBackend":
        clone = RedmineBackend(self.options, {"api_key": token}, self.base_dir)
        clone.name = self.name
        return clone

    def issue(self, item: dict[str, Any]) -> Issue:
        status = item.get("status") or {}
        closed = status["is_closed"] if "is_closed" in status else bool(item.get("closed_on"))
        labels = [str(x["name"]) for x in (item.get("tracker"), item.get("category")) if x and x.get("name")]
        links = []
        for rel in item.get("relations") or []:
            other = rel.get("issue_to_id") if str(rel.get("issue_id")) == str(item.get("id")) else rel.get("issue_id")
            if other:
                links.append(str(other))
        return Issue(source=self.name, id=str(item.get("id")), title=str(item.get("subject") or ""),
                     state=CLOSED if closed else OPEN, url=f"{self.server}/issues/{item.get('id')}", labels=labels,
                     assignees=[str((item.get("assigned_to") or {}).get("name"))] if item.get("assigned_to") else [],
                     author=str((item.get("author") or {}).get("name") or ""), created=str(item.get("created_on") or ""),
                     updated=str(item.get("updated_on") or ""), body=str(item.get("description") or ""),
                     project=str((item.get("project") or {}).get("name") or self.project), links=links)

    def search(self, text: str = "", state: str | None = OPEN, labels: Iterable[str] = (), limit: int = 100,
               since: str | None = None) -> list[Issue]:
        params: dict[str, Any] = {"project_id": self.project, "sort": "updated_on:desc",
                                  "status_id": {OPEN: "open", CLOSED: "closed"}.get(state or "", "*")}
        if text:                                 # subject, description and notes (Redmine 5.0+)
            params.update({"f[]": ["status_id", "any_searchable"], "op[status_id]": "o" if state == OPEN else
                           "c" if state == CLOSED else "*", "op[any_searchable]": "~", "v[any_searchable][]": text})
            params.pop("status_id")
        if since:
            params["updated_on"] = f">={str(since).replace('+00:00', 'Z')}"
        wanted = {str(x).lower() for x in labels}
        out: list[Issue] = []
        for page in range(MAX_PAGES):
            data = self.api.get("issues.json", {**params, "limit": PAGE, "offset": page * PAGE}) or {}
            items = data.get("issues") or []
            batch = []
            for item in items:
                issue = self.issue(item)
                if wanted <= {x.lower() for x in issue.labels}:
                    batch.append(issue)
                    if len(out) + len(batch) >= limit:
                        break
            out += batch
            total = data.get("total_count")
            self.paged(batch, len(out), min(int(total), limit) if str(total).isdigit() and not wanted else None)
            if len(out) >= limit:
                return out
            if len(items) < PAGE or (page + 1) * PAGE >= int(data.get("total_count") or 0):
                return out
        return out

    def get(self, ids: Iterable[str]) -> dict[str, Issue | None]:
        out: dict[str, Issue | None] = {}
        for id in ids:
            n = str(id).lstrip("#")
            if not n.isdigit():
                raise IssueError(f"redmine backend: '{id}' is not an issue number")
            data = self.api.get(f"issues/{n}.json", {"include": "relations"}, missing_ok=True)
            out[str(id)] = self.issue(data["issue"]) if data and data.get("issue") else None
        return out

    def tracker(self) -> int | None:
        """The id of the tracker of new issues (None: the server's default)."""
        wanted = self.options.get("tracker")
        if not wanted:
            return None
        if str(wanted).isdigit():
            return int(wanted)
        if self._tracker is None:
            trackers = (self.api.get("trackers.json") or {}).get("trackers") or []
            match = [t for t in trackers if str(t.get("name")).lower() == str(wanted).lower()]
            if not match:
                raise IssueError(f"redmine backend: no tracker '{wanted}' (trackers: "
                                  f"{', '.join(str(t.get('name')) for t in trackers) or 'none'})")
            self._tracker = int(match[0]["id"])
        return self._tracker

    def create(self, title: str, body: str, labels: Iterable[str] = (), project: str | None = None) -> Issue:
        fields: dict[str, Any] = {"project_id": project or self.project, "subject": title, "description": body}
        tracker = self.tracker()
        if tracker is not None:
            fields["tracker_id"] = tracker
        data = self.api.post("issues.json", {"issue": fields}) or {}
        if not data.get("issue"):
            raise IssueError("redmine backend: the server did not return the new issue")
        return self.issue(data["issue"])

    def comment(self, id: str, body: str) -> None:
        self.api.call("PUT", f"issues/{str(id).lstrip('#')}.json", body={"issue": {"notes": body}})

    def id_of_url(self, url: str) -> str | None:
        m = re.match(rf"{re.escape(self.server)}/issues/(\d+)\b", url.strip())
        return m.group(1) if m else None


register_issue_backend("redmine", RedmineBackend)
