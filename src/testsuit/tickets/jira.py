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

"""The built-in Jira backend: the issues of a project (Jira Cloud, Server or Data Center), through REST API v2 (plain
text bodies everywhere).

Options: ``url`` (the site, ``https://<site>.atlassian.net`` or the server), ``project`` (its key), ``issue_type``
(of the issues created, default Bug), ``jql`` (more conditions on the issues listed: ``component = Bench``),
``cloud`` (default: true for an atlassian.net site: the search API of Jira Cloud), ``verify``, ``timeout``.
Credentials: Jira Cloud: ``email`` and ``token`` (an API token, basic authentication); Server and Data Center:
``token`` alone (a personal access token, bearer), or ``user`` and ``password``.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from testsuit.tickets.backend import Api, TicketBackend, need, register_ticket_backend
from testsuit.tickets.model import CLOSED, OPEN, Issue, TicketError

FIELDS = "summary,status,labels,assignee,reporter,created,updated,description,issuelinks,project"


class JiraBackend(TicketBackend):
    """Jira issues, through its REST API (v2)"""

    description = "Jira issues of a project (Cloud, Server or Data Center)"
    capabilities = frozenset({"create", "comment", "remote_link"})
    options_help = {"url": "The site: https://<site>.atlassian.net, or the server.", "project": "The project key.",
                    "issue_type": "The type of the issues created (Bug).", "jql": "More conditions on the issues listed.",
                    "cloud": "Jira Cloud (default: for an atlassian.net site).",
                    "verify": "TLS check: false, or the file of a CA bundle.", "timeout": "Seconds per call (20)."}
    credentials_help = {"email": "Jira Cloud: the account's email, with its API token.",
                        "token": "Jira Cloud: an API token (with email); Server, Data Center: a personal access token.",
                        "user": "Server, Data Center: a user, with its password.", "password": "Its password."}

    def __init__(self, options, credentials, base_dir) -> None:
        super().__init__(options, credentials, base_dir)
        self.server = need(self.options, "url", "jira").rstrip("/")
        self.project = need(self.options, "project", "jira")
        c = self.credentials
        cloud = self.options.get("cloud")
        self.cloud = bool(cloud) if cloud is not None else ".atlassian.net" in self.server
        headers, auth = {}, None
        if c.get("email") and c.get("token"):
            auth = (c["email"], c["token"])
        elif c.get("user") and c.get("password"):
            auth = (c["user"], c["password"])
        elif c.get("token"):
            headers["Authorization"] = f"Bearer {c['token']}"
        self.api = Api(self, f"{self.server}/rest/api/2", headers, auth)

    def with_token(self, token: str) -> "JiraBackend":
        creds = {"token": token, **({"email": self.credentials["email"]} if self.cloud and self.credentials.get("email") else {})}
        if self.cloud and ":" in token:          # email:token, the user's own
            email, _, tok = token.partition(":")
            creds = {"email": email, "token": tok}
        clone = JiraBackend(self.options, creds, self.base_dir)
        clone.name = self.name
        return clone

    def issue(self, item: dict[str, Any]) -> Issue:
        f = item.get("fields") or {}
        status = f.get("status") or {}
        done = ((status.get("statusCategory") or {}).get("key") == "done")
        links = []
        for lk in f.get("issuelinks") or []:
            other = lk.get("outwardIssue") or lk.get("inwardIssue") or {}
            if other.get("key"):
                links.append(str(other["key"]))
        key = str(item.get("key"))
        return Issue(source=self.name, id=key, title=str(f.get("summary") or ""), state=CLOSED if done else OPEN,
                     url=f"{self.server}/browse/{key}", labels=[str(x) for x in f.get("labels") or []],
                     assignees=[str((f.get("assignee") or {}).get("displayName") or "")] if f.get("assignee") else [],
                     author=str((f.get("reporter") or {}).get("displayName") or ""), created=str(f.get("created") or ""),
                     updated=str(f.get("updated") or ""), body=str(f.get("description") or ""),
                     project=str((f.get("project") or {}).get("key") or self.project), links=links)

    @staticmethod
    def _quote(text: str) -> str:
        return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def search(self, text: str = "", state: str | None = OPEN, labels: Iterable[str] = (), limit: int = 100) -> list[Issue]:
        jql = [f"project = {self._quote(self.project)}"]
        if self.options.get("jql"):
            jql.append(f"({self.options['jql']})")
        if state == OPEN:
            jql.append("statusCategory != Done")
        elif state == CLOSED:
            jql.append("statusCategory = Done")
        jql += [f"labels = {self._quote(x)}" for x in labels]
        if text:
            jql.append(f"text ~ {self._quote(text)}")
        query = " AND ".join(jql) + " ORDER BY updated DESC"
        out: list[Issue] = []
        if self.cloud:                           # Jira Cloud: /search/jql, pages by token
            token = None
            for _ in range(20):
                data = self.api.get("search/jql", {"jql": query, "maxResults": min(100, limit), "fields": FIELDS,
                                                   **({"nextPageToken": token} if token else {})}) or {}
                out += [self.issue(x) for x in data.get("issues") or []]
                token = data.get("nextPageToken")
                if not token or len(out) >= limit or data.get("isLast"):
                    break
        else:
            start = 0
            for _ in range(20):
                data = self.api.get("search", {"jql": query, "startAt": start, "maxResults": min(100, limit),
                                               "fields": FIELDS}) or {}
                issues = data.get("issues") or []
                out += [self.issue(x) for x in issues]
                start += len(issues)
                if not issues or start >= int(data.get("total") or 0) or len(out) >= limit:
                    break
        return out[:limit]

    def get(self, ids: Iterable[str]) -> dict[str, Issue | None]:
        out: dict[str, Issue | None] = {}
        for id in ids:
            item = self.api.get(f"issue/{id}", {"fields": FIELDS}, missing_ok=True)
            out[str(id)] = self.issue(item) if item else None
        return out

    def create(self, title: str, body: str, labels: Iterable[str] = (), project: str | None = None) -> Issue:
        bad = [x for x in labels if " " in x]
        if bad:
            raise TicketError(f"jira backend: a label has no space: {', '.join(bad)}")
        made = self.api.post("issue", {"fields": {"project": {"key": project or self.project}, "summary": title,
                                                  "description": body, "labels": list(labels),
                                                  "issuetype": {"name": str(self.options.get("issue_type") or "Bug")}}})
        key = str((made or {}).get("key") or "")
        if not key:
            raise TicketError("jira backend: the issue was created, but Jira did not give its key")
        return self.get([key])[key] or Issue(source=self.name, id=key, title=title, url=f"{self.server}/browse/{key}")

    def comment(self, id: str, body: str) -> None:
        self.api.post(f"issue/{id}/comment", {"body": body})

    def remote_link(self, id: str, url: str, title: str) -> bool:
        self.api.post(f"issue/{id}/remotelink", {"object": {"url": url, "title": title[:250]}})
        return True

    def id_of_url(self, url: str) -> str | None:
        m = re.match(rf"{re.escape(self.server)}/(?:browse|jira/.*?/issues?)/?([A-Z][A-Z0-9_]+-\d+)", url.strip())
        if not m:
            m = re.match(rf"{re.escape(self.server)}/.*[?&]selectedIssue=([A-Z][A-Z0-9_]+-\d+)", url.strip())
        return m.group(1) if m and m.group(1).split("-")[0] == self.project else None


register_ticket_backend("jira", JiraBackend)
