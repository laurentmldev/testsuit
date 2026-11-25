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

"""The built-in GitHub backend: the issues of a repository, or of every repository of an organization or user
(github.com or GitHub Enterprise Server).

Options: ``repo`` (``owner/name``) or ``org`` (an organization or user: a group source, ids ``<owner/name>#<n>``),
``url`` (the API, default https://api.github.com; GitHub Enterprise: ``https://<host>/api/v3``), ``verify``,
``timeout``. Credentials: ``token``, a fine-grained token (Issues: read and write) or a classic one (scope ``repo``),
or a GitHub App installation token.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from testsuit.issues.backend import Api, IssueBackend, register_issue_backend
from testsuit.issues.model import CLOSED, OPEN, Issue, IssueError


class GitHubBackend(IssueBackend):
    """GitHub issues, through its REST API"""

    description = "GitHub issues of a repository or of an organization (github.com or Enterprise Server)"
    capabilities = frozenset({"create", "comment", "group"})
    options_help = {"repo": "The repository: owner/name.",
                    "org": "An organization (or user) instead: the issues of all its repositories (ids <owner/name>#<n>).",
                    "url": "The API (default https://api.github.com; Enterprise: https://<host>/api/v3).",
                    "web": "Where its pages are, when not the API's host (https://github.com for github.com).",
                    "verify": "TLS check of the server: false (a self-signed certificate: not checked), or the file of the CA bundle that signs it.", "timeout": "Seconds per call (20)."}
    credentials_help = {"token": "A fine-grained token (Issues: read and write), a classic one (scope repo), or an "
                                 "App installation token."}

    auth_methods = {"token": {"label": "Token (fine-grained, classic, or App installation)", "credentials": ["token"]}}

    def __init__(self, options, credentials, base_dir) -> None:
        super().__init__(options, credentials, base_dir)
        self.server = str(self.options.get("url") or "https://api.github.com").rstrip("/")
        self.repo = str(self.options.get("repo") or "")
        self.org = str(self.options.get("org") or "")
        if bool(self.repo) == bool(self.org):
            raise IssueError("github backend: give its 'repo' (owner/name) or its 'org' (one of them)")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.credentials.get("token"):
            headers["Authorization"] = f"Bearer {self.credentials['token']}"
        self.api = Api(self, self.server, headers)
        host = re.sub(r"^https?://(api\.)?", "", self.server).split("/")[0]
        self.web = str(self.options.get("web") or ("https://github.com" if host == "github.com" else f"https://{host}")).rstrip("/")

    def issue(self, item: dict[str, Any]) -> Issue:
        repo = "/".join(str(item.get("repository_url") or "").rstrip("/").split("/")[-2:]) or self.repo
        n = str(item.get("number"))
        kind = item.get("type") if isinstance(item.get("type"), dict) else {}
        return Issue(source=self.name, id=n if self.repo else f"{repo}#{n}", title=str(item.get("title") or ""),
                     state=CLOSED if item.get("state") == "closed" else OPEN, url=str(item.get("html_url") or ""),
                     labels=[str(x.get("name") if isinstance(x, dict) else x) for x in item.get("labels") or []],
                     assignees=[str(a.get("login")) for a in item.get("assignees") or [] if a],
                     type=str(kind.get("name") or ""), type_color=str(kind.get("color") or ""),
                     author=str((item.get("user") or {}).get("login") or ""), created=str(item.get("created_at") or ""),
                     updated=str(item.get("updated_at") or ""), body=str(item.get("body") or ""), project=repo)

    def search(self, text: str = "", state: str | None = OPEN, labels: Iterable[str] = (), limit: int = 100,
               since: str | None = None) -> list[Issue]:
        labels = list(labels)
        if text or self.org:                     # the search API: text, or every repository of an owner
            q = [f"repo:{self.repo}" if self.repo else f"user:{self.org}", "is:issue"]
            if state:
                q.append(f"is:{state}")
            q += [f'label:"{x}"' for x in labels]
            if text:
                q.append(text)
            if since:
                q.append(f"updated:>={since}")
            out = []
            page = 1
            while len(out) < limit and page <= 10:          # the search API gives 1,000 at most
                data = self.api.get("search/issues", {"q": " ".join(q), "per_page": 100, "page": page,
                                                      "sort": "updated", "order": "desc"}) or {}
                items = data.get("items") or []
                batch = [self.issue(x) for x in items if "pull_request" not in x]
                out += batch
                total = data.get("total_count")
                self.paged(batch, len(out), min(int(total), 1000) if str(total).isdigit() else None)
                if len(items) < 100:
                    break
                page += 1
            return out[:limit]
        params: dict[str, Any] = {"state": state or "all", "per_page": 100, "sort": "updated", "direction": "desc"}
        if labels:
            params["labels"] = ",".join(labels)
        if since:
            params["since"] = since
        out: list[Issue] = []
        for items, total, exact in self.api.page_lists(f"repos/{self.repo}/issues", params):
            # the issues API lists pull requests too: the total is an estimate
            batch = [self.issue(x) for x in items if "pull_request" not in x][:limit - len(out)]
            out += batch
            self.paged(batch, len(out), min(total, limit) if total is not None else None, False)
            if len(out) >= limit:
                break
        return out

    def _where(self, id: str) -> tuple[str, str]:
        if "#" in str(id):
            repo, n = str(id).rsplit("#", 1)
            return repo, n
        if not self.repo:
            raise IssueError(f"github backend: issue '{id}' of '{self.org}': name its repository (<owner/name>#<n>)")
        return self.repo, str(id)

    def get(self, ids: Iterable[str]) -> dict[str, Issue | None]:
        out: dict[str, Issue | None] = {}
        for id in ids:
            repo, n = self._where(id)
            item = self.api.get(f"repos/{repo}/issues/{n}", missing_ok=True)
            out[str(id)] = self.issue(item) if item and "pull_request" not in item else None
        return out

    def issue_types(self, project: str | None = None) -> list[dict[str, Any]]:
        """The issue types of the organization that owns the repository (none for a user's repository)."""
        owner = str(project or self.repo or self.org).split("/")[0]
        try:
            found = self.api.get(f"orgs/{owner}/issue-types", missing_ok=True) or []
        except IssueError:
            return []
        return [{"name": str(t.get("name"))} | ({"color": str(t["color"])} if t.get("color") else {}) for t in found if isinstance(t, dict) and t.get("is_enabled", True)]

    def create(self, title: str, body: str, labels: Iterable[str] = (), project: str | None = None,
               issue_type: str | None = None) -> Issue:
        repo = project or self.repo
        if not repo:
            raise IssueError(f"github backend: '{self.org}': which repository should the issue go to?")
        fields: dict[str, Any] = {"title": title, "body": body, "labels": list(labels)}
        if issue_type:
            fields["type"] = issue_type
        return self.issue(self.api.post(f"repos/{repo}/issues", fields))

    def comment(self, id: str, body: str) -> None:
        repo, n = self._where(id)
        self.api.post(f"repos/{repo}/issues/{n}/comments", {"body": body})

    def id_of_url(self, url: str) -> str | None:
        m = re.match(rf"{re.escape(self.web)}/([^/]+/[^/]+)/issues/(\d+)", url.strip())
        if not m:
            return None
        repo, n = m.group(1), m.group(2)
        if self.repo:
            return n if repo.lower() == self.repo.lower() else None
        return f"{repo}#{n}" if repo.split("/")[0].lower() == self.org.lower() else None

    def projects(self) -> list[str]:
        if self.repo:
            return [self.repo]
        try:
            repos = list(self.api.pages(f"orgs/{self.org}/repos", {"per_page": 100}, 500))
        except IssueError:
            repos = list(self.api.pages(f"users/{self.org}/repos", {"per_page": 100}, 500))
        return [str(r.get("full_name")) for r in repos if not r.get("archived") and r.get("has_issues", True)]


register_issue_backend("github", GitHubBackend)
