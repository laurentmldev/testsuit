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

"""The built-in GitLab backend: the issues of a project, or of every project of a group (gitlab.com or self-hosted).

Options: ``url`` (default https://gitlab.com), ``project`` (its path, ``group/project``, or its id) or ``group`` (its
path: a group source, ids ``<project path>#<iid>``), ``verify`` (TLS: false, or a CA bundle), ``timeout``.
Credentials: ``token``, a personal, project or group access token (scope ``api``; ``read_api`` to read only), sent as
``PRIVATE-TOKEN``; ``oauth_token`` instead: an OAuth access token, sent as a bearer token.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote

from testsuit.issues.backend import Api, IssueBackend, register_issue_backend
from testsuit.issues.model import CLOSED, OPEN, Issue, IssueError


class GitLabBackend(IssueBackend):
    """GitLab issues, through its REST API (v4)"""

    description = "GitLab issues of a project or of a group (gitlab.com or self-hosted)"
    capabilities = frozenset({"create", "comment", "group"})
    options_help = {"url": "The GitLab server (default https://gitlab.com).",
                    "project": "The project: its path (group/project) or its id.",
                    "group": "A group instead: the issues of all its projects (ids <project>#<iid>).",
                    "verify": "TLS check of the server: false (a self-signed certificate: not checked), or the file of the CA bundle that signs it.", "timeout": "Seconds per call (20)."}
    credentials_help = {"token": "A personal, project or group access token (scope api; read_api to read only).",
                        "oauth_token": "An OAuth access token instead."}

    auth_methods = {"access_token": {"label": "Access token (personal, project or group)", "credentials": ["token"]},
                    "oauth": {"label": "OAuth2 access token", "credentials": ["oauth_token"]}}

    def __init__(self, options, credentials, base_dir) -> None:
        super().__init__(options, credentials, base_dir)
        self.server = str(self.options.get("url") or "https://gitlab.com").rstrip("/")
        self.project = str(self.options.get("project") or "")
        self.group = str(self.options.get("group") or "")
        if bool(self.project) == bool(self.group):
            raise IssueError("gitlab backend: give its 'project' or its 'group' (one of them)")
        headers = {}
        if self.credentials.get("token"):
            headers["PRIVATE-TOKEN"] = self.credentials["token"]
        elif self.credentials.get("oauth_token"):
            headers["Authorization"] = f"Bearer {self.credentials['oauth_token']}"
        self.api = Api(self, f"{self.server}/api/v4", headers)

    def with_token(self, token: str) -> "GitLabBackend":
        clone = GitLabBackend(self.options, {"token": token}, self.base_dir)
        clone.name = self.name
        return clone

    @staticmethod
    def _p(path: str) -> str:
        return quote(path, safe="")

    def _scope(self) -> str:
        return f"projects/{self._p(self.project)}" if self.project else f"groups/{self._p(self.group)}"

    def issue(self, item: dict[str, Any]) -> Issue:
        refs = item.get("references") or {}
        full = str(refs.get("full") or "")
        project = full.rsplit("#", 1)[0] if "#" in full else self.project
        iid = str(item.get("iid"))
        return Issue(source=self.name, id=iid if self.project else f"{project}#{iid}", title=str(item.get("title") or ""),
                     state=CLOSED if item.get("state") == "closed" else OPEN, url=str(item.get("web_url") or ""),
                     labels=[str(x) for x in item.get("labels") or []],
                     assignees=[str(a.get("username")) for a in item.get("assignees") or [] if a],
                     author=str((item.get("author") or {}).get("username") or ""), created=str(item.get("created_at") or ""),
                     updated=str(item.get("updated_at") or ""), body=str(item.get("description") or ""), project=project)

    def search(self, text: str = "", state: str | None = OPEN, labels: Iterable[str] = (), limit: int = 100,
               since: str | None = None) -> list[Issue]:
        params: dict[str, Any] = {"per_page": min(100, max(1, limit)), "order_by": "updated_at", "sort": "desc"}
        if state:
            params["state"] = "opened" if state == OPEN else "closed"
        if labels:
            params["labels"] = ",".join(labels)
        if text:
            params["search"] = text
        if since:
            params["updated_after"] = since
        out: list[Issue] = []
        for items, total, exact in self.api.page_lists(f"{self._scope()}/issues", params):
            batch = [self.issue(x) for x in items][:limit - len(out)]
            out += batch
            self.paged(batch, len(out), min(total, limit) if total is not None else None, exact)
            if len(out) >= limit:
                break
        return out

    def _where(self, id: str) -> tuple[str, str]:
        """(project, iid) of an id of this source."""
        if "#" in str(id):
            project, iid = str(id).rsplit("#", 1)
            return project, iid
        if not self.project:
            raise IssueError(f"gitlab backend: issue '{id}' of group '{self.group}': name its project (<project>#<iid>)")
        return self.project, str(id)

    def get(self, ids: Iterable[str]) -> dict[str, Issue | None]:
        out: dict[str, Issue | None] = {}
        for id in ids:
            project, iid = self._where(id)
            item = self.api.get(f"projects/{self._p(project)}/issues/{iid}", missing_ok=True)
            out[str(id)] = self.issue(item) if item else None
        return out

    def create(self, title: str, body: str, labels: Iterable[str] = (), project: str | None = None) -> Issue:
        project = project or self.project
        if not project:
            raise IssueError(f"gitlab backend: group '{self.group}': which project should the issue go to?")
        item = self.api.post(f"projects/{self._p(project)}/issues",
                             {"title": title, "description": body, **({"labels": ",".join(labels)} if labels else {})})
        return self.issue(item)

    def comment(self, id: str, body: str) -> None:
        project, iid = self._where(id)
        self.api.post(f"projects/{self._p(project)}/issues/{iid}/notes", {"body": body})

    def id_of_url(self, url: str) -> str | None:
        m = re.match(rf"{re.escape(self.server)}/(.+?)/-/(?:issues|work_items)/(\d+)", url.strip())
        if not m:
            return None
        project, iid = m.group(1), m.group(2)
        if self.project:
            return iid if project == self.project or str(self.project).isdigit() else None
        return f"{project}#{iid}" if project == self.group or project.startswith(self.group + "/") else None

    def projects(self) -> list[str]:
        if self.project:
            return [self.project]
        return [str(p.get("path_with_namespace")) for p in
                self.api.pages(f"groups/{self._p(self.group)}/projects", {"include_subgroups": "true", "per_page": 100,
                                                                         "archived": "false"}, 500)]


register_issue_backend("gitlab", GitLabBackend)
