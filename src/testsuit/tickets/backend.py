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

"""The plugin API of tickets tools (issue trackers), and what the built-in backends share.

A backend gives access to the issues of one project of one tool (or of a group of projects). It is created by a
factory ``factory(options, credentials, base_dir) -> TicketBackend``, as a requirements backend is
(testsuit.reqs.backend)::

    from testsuit.tickets import TicketBackend, Issue, register_ticket_backend

    class YouTrackBackend(TicketBackend):
        description = "YouTrack, through its REST API"
        capabilities = frozenset({"create", "comment"})
        markup = "markdown"                                 # how its issue texts are written: markdown, jira, textile
        options_help = {"url": "The server.", "project": "The project short name."}
        credentials_help = {"token": "A permanent token."}
        def search(self, text="", state="open", labels=(), limit=100) -> list[Issue]: ...
        def get(self, ids) -> dict[str, Issue | None]: ...
        def create(self, title, body, labels=(), project=None) -> Issue: ...
        def comment(self, id, body) -> None: ...

    register_ticket_backend("youtrack", YouTrackBackend)    # tickets.yml: {sources: {x: {backend: youtrack, ...}}}

Capabilities: ``create`` (new issues), ``comment``, ``remote_link`` (a link shown beside the issue, Jira), ``group``
(a source over several projects: ids are ``<project>#<n>``, ``create`` takes the project).
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from testsuit.tickets.model import Issue, TicketError

MASK = "***"
TIMEOUT = 20.0
TRACE_CALLS = 30
TRACE_ANSWER = 4000
MAX_PAGES = 20


class TicketBackend:
    """Access to the issues of one project of a tickets tool (read-only unless it has the create capability)."""

    capabilities: frozenset[str] = frozenset()
    description: str = ""
    markup: str = "markdown"                 # the markup of issue texts: markdown, jira (wiki markup) or textile
    options_help: dict[str, str] = {}
    credentials_help: dict[str, str] = {}
    trace: list[dict] | None = None          # set to a list by the Editor's test: each call, credentials masked

    def __init__(self, options: dict[str, Any], credentials: dict[str, str] | None, base_dir: Path | None) -> None:
        self.options = dict(options or {})
        self.credentials = dict(credentials or {})
        self.base_dir = base_dir
        self.name = ""                       # the source's name, set by TicketSources

    # ── what a backend implements ──────────────────────────────────────
    def search(self, text: str = "", state: str | None = "open", labels: Iterable[str] = (), limit: int = 100) -> list[Issue]:
        """Issues of the project, newest first: those whose title or text has ``text``, in ``state`` (open, closed,
        None: both), with every label of ``labels``; at most ``limit``."""
        raise NotImplementedError

    def get(self, ids: Iterable[str]) -> dict[str, Issue | None]:
        """The issues of these ids (None: no such issue)."""
        raise NotImplementedError

    def create(self, title: str, body: str, labels: Iterable[str] = (), project: str | None = None) -> Issue:
        raise TicketError(f"{type(self).__name__} cannot create issues")

    def comment(self, id: str, body: str) -> None:
        raise TicketError(f"{type(self).__name__} cannot comment issues")

    def remote_link(self, id: str, url: str, title: str) -> bool:
        """Link a page to an issue where the tool shows such links (Jira): whether it did."""
        return False

    def id_of_url(self, url: str) -> str | None:
        """The id of the issue a URL of this project opens (None: not one of its issues)."""
        return None

    def projects(self) -> list[str]:
        """The projects of a group source (where an issue can be created)."""
        return []

    @property
    def identity(self) -> str:
        """What tells this project apart from any other, whatever the source's name (a cache key)."""
        return json.dumps([type(self).__name__, {k: v for k, v in sorted(self.options.items()) if k != "labels"}],
                          default=str)

    def with_token(self, token: str) -> "TicketBackend":
        """The same source, calling with another user's token (the person who creates an issue)."""
        clone = type(self)(self.options, {**self.credentials, "token": token}, self.base_dir)
        clone.name = self.name
        return clone


class Api:
    """JSON over HTTP for the built-in backends: credentials masked in errors and traces, pages followed."""

    def __init__(self, backend: TicketBackend, base: str, headers: Mapping[str, str] | None = None,
                 auth: tuple[str, str] | None = None) -> None:
        import requests
        self.backend = backend
        self.base = base.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        self.session.headers.update(dict(headers or {}))
        self.session.auth = auth
        verify = backend.options.get("verify", True)
        self.verify = str(Path(backend.base_dir or ".") / verify) if isinstance(verify, str) else bool(verify)
        self.timeout = float(backend.options.get("timeout") or TIMEOUT)

    def _mask(self, text: str) -> str:
        for secret in sorted({str(v) for v in self.backend.credentials.values() if v and len(str(v)) >= 3},
                             key=len, reverse=True):
            text = text.replace(secret, MASK).replace(quote(secret, safe=""), MASK)
        return text

    def url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base}/{path.lstrip('/')}"

    def call(self, method: str, path: str, params: Mapping[str, Any] | None = None, body: Any = None,
             missing_ok: bool = False):
        """(answer as JSON, response). An error names the backend, the method and the URL."""
        import requests
        url = self.url(path)
        where = f"{self.backend.name or type(self.backend).__name__}: {method} {self._mask(url)}"
        record = None
        trace = self.backend.trace
        if trace is not None and len(trace) < TRACE_CALLS:
            record = {"method": method, "url": self._mask(url), **({"params": dict(params)} if params else {}),
                      **({"body": json.loads(self._mask(json.dumps(body)))} if body is not None else {})}
            trace.append(record)
        start = time.monotonic()
        try:
            r = self.session.request(method, url, params=params, json=body, timeout=self.timeout, verify=self.verify)
        except requests.RequestException as e:
            if record is not None:
                record["error"] = self._mask(str(e))
            raise TicketError(f"{where}: {self._mask(str(e))}") from e
        if record is not None:
            record.update(status=r.status_code, seconds=round(time.monotonic() - start, 3),
                          answer=self._mask(r.text[:TRACE_ANSWER]))
        if missing_ok and r.status_code == 404:
            return None, r
        if r.status_code >= 400:
            hint = {401: " (the token is wrong or expired)", 403: " (the token lacks a permission)",
                    404: " (no such project, or the token cannot see it)"}.get(r.status_code, "")
            error = f"HTTP {r.status_code} {r.reason}{hint}: {self._mask(r.text[:300])}"
            if record is not None:
                record["error"] = error
            raise TicketError(f"{where}: {error}")
        if not r.content:
            return None, r
        try:
            return r.json(), r
        except ValueError as e:
            raise TicketError(f"{where}: the answer is not JSON ({self._mask(r.text[:100])!r})") from e

    def get(self, path: str, params: Mapping[str, Any] | None = None, missing_ok: bool = False):
        return self.call("GET", path, params, missing_ok=missing_ok)[0]

    def post(self, path: str, body: Any):
        return self.call("POST", path, body=body)[0]

    def pages(self, path: str, params: Mapping[str, Any], limit: int) -> Iterable[Any]:
        """The items of a list, page by page as its Link header gives them (GitHub, GitLab), up to limit."""
        url, query, count = path, dict(params), 0
        for _ in range(MAX_PAGES):
            data, r = self.call("GET", url, query)
            for item in data or []:
                yield item
                count += 1
                if count >= limit:
                    return
            nxt = r.links.get("next", {}).get("url") if r is not None else None
            if not nxt or not data:
                return
            url, query = nxt, None


BackendFactory = Callable[[dict, "dict | None", "Path | None"], TicketBackend]
_backends: dict[str, BackendFactory] = {}


def register_ticket_backend(name: str, factory: BackendFactory) -> None:
    """Declare a tickets backend, usable as ``backend: <name>`` in tickets.yml (case-insensitive; a name registered
    again replaces it, the built-in ones included)."""
    _backends[name.lower()] = factory


def unregister_ticket_backend(name: str) -> None:
    _backends.pop(name.lower(), None)


def get_ticket_backends() -> list[str]:
    return list(_backends)


def describe_ticket_backends() -> list[dict[str, Any]]:
    out = []
    for name, factory in sorted(_backends.items()):
        doc = (factory.__doc__ or "").strip().split("\n\n")[0] if isinstance(factory, type) else ""
        out.append({"name": name, "description": str(getattr(factory, "description", "") or " ".join(doc.split())),
                    "capabilities": sorted(getattr(factory, "capabilities", ()) or ()),
                    "options": dict(getattr(factory, "options_help", {}) or {}),
                    "credentials": dict(getattr(factory, "credentials_help", {}) or {})})
    return out


def create_ticket_backend(name: str, options: dict, credentials: dict | None, base_dir: Path | None) -> TicketBackend:
    factory = _backends.get(str(name).lower())
    if factory is None:
        raise TicketError(f"unknown tickets backend '{name}' (backends: {', '.join(sorted(_backends)) or 'none'})")
    return factory(options or {}, credentials, base_dir)


def need(options: Mapping[str, Any], key: str, backend: str) -> str:
    value = options.get(key)
    if not value:
        raise TicketError(f"{backend} backend: option '{key}' is missing")
    return str(value)
