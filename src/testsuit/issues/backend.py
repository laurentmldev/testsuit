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

"""The plugin API of issue trackers (issue trackers), and what the built-in backends share.

A backend gives access to the issues of one project of one tool (or of a group of projects). It is created by a
factory ``factory(options, credentials, base_dir) -> IssueBackend``, as a requirements backend is
(testsuit.reqs.backend)::

    from testsuit.issues import IssueBackend, Issue, register_issue_backend

    class YouTrackBackend(IssueBackend):
        description = "YouTrack, through its REST API"
        capabilities = frozenset({"create", "comment"})
        markup = "markdown"                                 # how its issue texts are written: markdown, jira, textile
        options_help = {"url": "The server.", "project": "The project short name."}
        credentials_help = {"token": "A permanent token."}
        def search(self, text="", state="open", labels=(), limit=100, since=None) -> list[Issue]: ...
        def get(self, ids) -> dict[str, Issue | None]: ...
        def create(self, title, body, labels=(), project=None) -> Issue: ...
        def comment(self, id, body) -> None: ...

    register_issue_backend("youtrack", YouTrackBackend)    # issues.yml: {sources: {x: {backend: youtrack, ...}}}

Capabilities: ``create`` (new issues), ``comment``, ``remote_link`` (a link shown beside the issue, Jira), ``group``
(a source over several projects: ids are ``<project>#<n>``, ``create`` takes the project).

Long lists: ``search`` MAY take ``since`` (an ISO time: only the issues updated at or after it, for a refresh that
reads only what changed) and report each page it reads with ``self.paged(issues, read, total)`` (``total``: how many
the tool says it holds, when it says; ``exact=False`` for an estimate), so that the first ones are shown at once and
the reading's progress too.
"""
from __future__ import annotations

import inspect
import threading
import json
import re
import time
import warnings
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from testsuit.issues.model import FAILURE_TYPES, Issue, IssueError

MASK = "***"
TIMEOUT = 20.0
TRACE_CALLS = 30
TRACE_ANSWER = 4000
MAX_PAGES = 100                              # of 100 issues: 10,000 at most per list


_reporting = threading.local()
Progress = Callable[[list[Issue], int, "int | None", bool], None]


@contextmanager
def reporting(fn: Progress | None):
    """Within it, in this thread, each page of a list read calls fn(issues of the page, read so far, total or None,
    whether the total is exact)."""
    before = getattr(_reporting, "fn", None)
    _reporting.fn = fn
    try:
        yield
    finally:
        _reporting.fn = before


def current_reporting() -> Progress | None:
    return getattr(_reporting, "fn", None)


class IssueBackend:
    """Access to the issues of one project of an issue tracker (read-only unless it has the create capability)."""

    capabilities: frozenset[str] = frozenset()
    description: str = ""
    markup: str = "markdown"                 # the markup of issue texts: markdown, jira (wiki markup) or textile
    options_help: dict[str, str] = {}
    credentials_help: dict[str, str] = {}
    # the ways to sign in, each with the credentials it takes (one is used: the Editor's form asks which):
    # {method: {"label": ..., "credentials": [names]}}; empty: every credential of credentials_help together
    auth_methods: dict[str, dict[str, Any]] = {}
    trace: list[dict] | None = None          # set to a list by the Editor's test: each call, credentials masked

    def __init__(self, options: dict[str, Any], credentials: dict[str, str] | None, base_dir: Path | None) -> None:
        self.options = dict(options or {})
        self.credentials = dict(credentials or {})
        self.base_dir = base_dir
        self.name = ""                       # the source's name, set by IssueSources

    # ── what a backend implements ──────────────────────────────────────
    def search(self, text: str = "", state: str | None = "open", labels: Iterable[str] = (), limit: int = 100,
               since: str | None = None) -> list[Issue]:
        """Issues of the project, newest first: those whose title or text has ``text``, in ``state`` (open, closed,
        None: both), with every label of ``labels``, updated at or after ``since`` (an ISO time); at most ``limit``."""
        raise NotImplementedError

    def paged(self, issues: list[Issue], read: int, total: int | None = None, exact: bool = True) -> None:
        """A page of a list was read (for the progress of a long list)."""
        fn = getattr(_reporting, "fn", None)
        if fn is not None:
            fn(issues, read, total, exact)

    @property
    def supports_since(self) -> bool:
        """Whether its search takes ``since`` (a plugin's may not)."""
        try:
            return "since" in inspect.signature(type(self).search).parameters      # not a filter's wrapper
        except (TypeError, ValueError):
            return False

    def get(self, ids: Iterable[str]) -> dict[str, Issue | None]:
        """The issues of these ids (None: no such issue)."""
        raise NotImplementedError

    def create(self, title: str, body: str, labels: Iterable[str] = (), project: str | None = None,
               issue_type: str | None = None) -> Issue:
        """A new issue; ``issue_type``: one of ``issue_types()`` (None: the tool's default)."""
        raise IssueError(f"{type(self).__name__} cannot create issues")

    def issue_types(self, project: str | None = None) -> list[dict[str, Any]]:
        """The types a new issue can have: ``[{name, default?}]`` (empty: no choice)."""
        return []

    @property
    def supports_types(self) -> bool:
        """Whether its create takes ``issue_type`` (a plugin's may not)."""
        try:
            return "issue_type" in inspect.signature(type(self).create).parameters
        except (TypeError, ValueError):
            return False

    def comment(self, id: str, body: str) -> None:
        raise IssueError(f"{type(self).__name__} cannot comment issues")

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

    def with_token(self, token: str) -> "IssueBackend":
        """The same source, calling with another user's token (the person who creates an issue)."""
        clone = type(self)(self.options, {**self.credentials, "token": token}, self.base_dir)
        clone.name = self.name
        return clone


class Api:
    """JSON over HTTP for the built-in backends: credentials masked in errors and traces, pages followed."""

    def __init__(self, backend: IssueBackend, base: str, headers: Mapping[str, str] | None = None,
                 auth: tuple[str, str] | None = None) -> None:
        import requests
        self.backend = backend
        self.base = base.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        self.session.headers.update(dict(headers or {}))
        self.session.auth = auth
        verify = backend.options.get("verify", True)
        if isinstance(verify, str) and verify.strip().lower() in ("false", "no", "off", "0", "true", "yes", "on", "1", ""):
            verify = verify.strip().lower() in ("true", "yes", "on", "1", "")
        # false: the server's certificate is not checked (a self-signed one); a file: the CA bundle that signs it
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
        from testsuit import netguard
        if trace is None:                        # a server down is not called again for a while (a test tries)
            try:
                netguard.check(url)
            except netguard.ServerDown as e:
                raise IssueError(f"{where}: {e}") from e
        start = time.monotonic()
        try:
            with warnings.catch_warnings():      # not checking was asked for: no warning at each call
                if self.verify is False:
                    warnings.simplefilter("ignore")
                r = self.session.request(method, url, params=params, json=body, timeout=self.timeout, verify=self.verify)
        except requests.RequestException as e:
            if record is not None:
                record["error"] = self._mask(str(e))
            if isinstance(e, (requests.ConnectionError, requests.Timeout)):
                netguard.failed(url, self._mask(str(e)))
            raise IssueError(f"{where}: {self._mask(str(e))}") from e
        if r.status_code in netguard.DOWN_STATUSES:
            netguard.failed(url, f"HTTP {r.status_code}")
        else:
            netguard.answered(url)
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
            raise IssueError(f"{where}: {error}")
        if not r.content:
            return None, r
        try:
            return r.json(), r
        except ValueError as e:
            raise IssueError(f"{where}: the answer is not JSON ({self._mask(r.text[:100])!r})") from e

    def get(self, path: str, params: Mapping[str, Any] | None = None, missing_ok: bool = False):
        return self.call("GET", path, params, missing_ok=missing_ok)[0]

    def post(self, path: str, body: Any):
        return self.call("POST", path, body=body)[0]

    def pages(self, path: str, params: Mapping[str, Any], limit: int) -> Iterable[Any]:
        """The items of a list, page by page as its Link header gives them (GitHub, GitLab), up to limit."""
        count = 0
        for items, _, _ in self.page_lists(path, params):
            for item in items:
                yield item
                count += 1
                if count >= limit:
                    return

    def page_lists(self, path: str, params: Mapping[str, Any]) -> Iterable[tuple[list, int | None, bool]]:
        """(items, total, exact) of each page of a list, as its Link header gives them (GitHub, GitLab): the total
        from ``X-Total`` (GitLab), else estimated from the last page's number (GitHub)."""
        url, query = path, dict(params)
        per_page = int(dict(params).get("per_page") or 100)
        total: int | None = None
        exact = True
        for _ in range(MAX_PAGES):
            data, r = self.call("GET", url, query)
            if total is None and r is not None:
                if str(r.headers.get("X-Total") or "").isdigit():
                    total = int(r.headers["X-Total"])
                else:
                    last = r.links.get("last", {}).get("url") or ""
                    m = re.search(r"[?&]page=(\d+)", last)
                    if m:
                        total, exact = int(m.group(1)) * per_page, False
            yield list(data or []), total, exact
            nxt = r.links.get("next", {}).get("url") if r is not None else None
            if not nxt or not data:
                return
            url, query = nxt, None


BackendFactory = Callable[[dict, "dict | None", "Path | None"], IssueBackend]
_backends: dict[str, BackendFactory] = {}


OTHER_TYPES = re.compile(r"improv|enhanc|feature|story|task|request", re.I)


def default_type(types: list[dict[str, Any]], failed: bool) -> str | None:
    """The type proposed for a new issue: about a failure, the source's default (else a bug); else an improvement."""
    if not types:
        return None
    configured = [t["name"] for t in types if t.get("default")]
    likely = [t["name"] for t in types if (FAILURE_TYPES if failed else OTHER_TYPES).search(str(t["name"]))]
    if failed:
        return (configured or likely or [types[0]["name"]])[0]
    return (likely or configured or [types[0]["name"]])[0]


def register_issue_backend(name: str, factory: BackendFactory) -> None:
    """Declare an issues backend, usable as ``backend: <name>`` in issues.yml (case-insensitive; a name registered
    again replaces it, the built-in ones included)."""
    _backends[name.lower()] = factory


def unregister_issue_backend(name: str) -> None:
    _backends.pop(name.lower(), None)


def get_issue_backends() -> list[str]:
    return list(_backends)


def describe_issue_backends() -> list[dict[str, Any]]:
    out = []
    for name, factory in sorted(_backends.items()):
        doc = (factory.__doc__ or "").strip().split("\n\n")[0] if isinstance(factory, type) else ""
        out.append({"name": name, "description": str(getattr(factory, "description", "") or " ".join(doc.split())),
                    "capabilities": sorted(getattr(factory, "capabilities", ()) or ()),
                    "options": dict(getattr(factory, "options_help", {}) or {}),
                    "credentials": dict(getattr(factory, "credentials_help", {}) or {}),
                    "auth": [{"name": k, **v} for k, v in (getattr(factory, "auth_methods", {}) or {}).items()]})
    return out


def create_issue_backend(name: str, options: dict, credentials: dict | None, base_dir: Path | None) -> IssueBackend:
    factory = _backends.get(str(name).lower())
    if factory is None:
        raise IssueError(f"unknown issues backend '{name}' (backends: {', '.join(sorted(_backends)) or 'none'})")
    return factory(options or {}, credentials, base_dir)


def need(options: Mapping[str, Any], key: str, backend: str) -> str:
    value = options.get(key)
    if not value:
        raise IssueError(f"{backend} backend: option '{key}' is missing")
    return str(value)
