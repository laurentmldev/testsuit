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

"""The built-in generic REST backend: the requirements of a tool with a JSON REST API, configured in the sources file
alone, its fields picked from the answers with jq expressions::

    sources:
      alm:
        backend: rest
        credentials: {token: env:ALM_TOKEN}
        options:
          base: https://alm.example/api/v2
          auth: {bearer: token}                  # or basic: [user, password], header: {X-Api-Key: token}
          list:                                  # every requirement, page by page
            url: /projects/12/items?type=requirement&page={page}
            items: .data[]                       # the requirements in an answer
            next: .links.next                    # optional: the next page's URL (relative to the page; none: the last)
          get: {url: "/items?ids={ids}", items: .data[]}     # optional: by ids (else: from the list)
          fields:                                # jq, on each item
            id: .fields.documentKey
            title: .fields.name
            description: .fields.description
            path: '[.location.folder[]?.name] | join("/")'
            status: .fields.status.name
            tags: '[.tags[]?.name]'
            version: .version
          url: https://alm.example/items/{id}

Pagination: ``next`` (a jq expression giving the URL of the next page), else ``{page}`` in the URL (from ``first_page``,
default 1) or ``{offset}`` (stepping by the number of items received), until a page has no item. Without any of them,
one call. The tree is made of the paths of the requirements.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

from testsuit.reqs.backend import ReqBackend, ReqError, register_req_backend, under
from testsuit.reqs.model import Node, Requirement

FIELDS = ("id", "path", "title", "description", "rationale", "tags", "comments", "status", "version", "url")
MAX_PAGES = 1000


def _jq(expr: str, where: str):
    try:
        import jq
    except ImportError as e:            # a core dependency: only a broken install lacks it
        raise ReqError("the rest backend needs the 'jq' Python package (pip install jq)") from e
    try:
        return jq.compile(str(expr))
    except ValueError as e:
        raise ReqError(f"{where}: jq expression '{expr}': {str(e).splitlines()[0]}") from e


def _all(program, value: Any, where: str) -> list[Any]:
    try:
        return program.input_value(value).all()
    except ValueError as e:
        raise ReqError(f"{where}: {str(e).splitlines()[0]}") from e


class RestBackend(ReqBackend):
    """Requirements of any tool with a JSON REST API: URLs, authentication and jq expressions picking the fields."""

    capabilities = frozenset({"children_query", "versions"})
    description = "Built in: any tool with a JSON REST API, its fields picked with jq expressions."
    options_help = {
        "base": "The base URL of the API; the URLs below are relative to it (or absolute).",
        "auth": "How to authenticate, with the names of the credentials: `{bearer: token}` (Authorization: Bearer), "
                "`{basic: [user, password]}`, `{header: {X-Api-Key: token}}` or `{query: {api_key: token}}`.",
        "headers": "Other headers sent with each request (not secrets: those are credentials).",
        "list": "How to get every requirement: `{url, items, next}`. `items`: jq giving the requirements of an answer "
                "(`.data[]`); pagination with `next` (jq giving the next page's URL), or `{page}` / `{offset}` in the "
                "URL (until a page has no item). Optional `method` (GET) and `body` (JSON, for a POST).",
        "get": "Optional: how to get requirements by id: `{url, items}`, with `{ids}` (comma-separated) or `{id}` "
               "(one call each, a 404 meaning unknown) in the URL. Default: from the list.",
        "fields": "jq expressions giving each field of a requirement from an item: `id` (required), `title`, "
                  "`description`, `path` (nodes joined by `/`), `rationale`, `status`, `tags` (a list), `comments`, "
                  "`version`, `url`. Default for a field not given (but `url`): `.<field>`.",
        "url": "A link to each requirement in its tool, `{id}` replaced by its id (when no `url` field).",
        "first_page": "The first `{page}` (default 1).",
        "timeout": "Seconds to wait for each answer (default 30).",
        "verify": "Check the server's TLS certificate: true (default), false, or the path of a CA bundle.",
    }
    credentials_help = {
        "token": "A token, used as `auth: {bearer: token}` or in a header.",
        "user": "A user name, for `auth: {basic: [user, password]}`.",
        "password": "Its password.",
    }

    def __init__(self, options: dict[str, Any], credentials: dict[str, str] | None, base_dir: Path) -> None:
        super().__init__(options, credentials, base_dir)
        o = self.options
        if not isinstance(o.get("list"), dict) or not o["list"].get("url"):
            raise ReqError("the rest backend needs its 'list' option: {url, items[, next]}")
        self.base = str(o.get("base") or "")
        self.timeout = float(o.get("timeout") or 30)
        verify = o.get("verify", True)
        self.verify = str((base_dir / str(verify)).resolve()) if isinstance(verify, str) else bool(verify)
        fields = o.get("fields") or {}
        if not isinstance(fields, dict):
            raise ReqError("rest backend: 'fields' is a mapping of field names to jq expressions")
        unknown = sorted(set(fields) - set(FIELDS))
        if unknown:
            raise ReqError(f"rest backend: unknown field(s) {', '.join(unknown)} (fields: {', '.join(FIELDS)})")
        self.fields = {f: _jq(fields.get(f) or f".{f}?", f"field '{f}'") for f in FIELDS if f in fields or f != "url"}
        self.list_spec = self._spec(o["list"], "list")
        self.get_spec = self._spec(o["get"], "get") if o.get("get") else None
        self.session = self._session()
        self._all: dict[str, Requirement] | None = None

    def _spec(self, spec: dict, name: str) -> dict:
        if not isinstance(spec, dict) or not spec.get("url"):
            raise ReqError(f"rest backend: '{name}' is {{url, items, ...}}")
        out = dict(spec)
        out["items"] = _jq(spec.get("items") or ".[]", f"{name}.items")
        out["next"] = _jq(spec["next"], f"{name}.next") if spec.get("next") else None
        return out

    def _credential(self, name: Any) -> str:
        creds = self.credentials or {}
        if str(name) not in creds:
            raise ReqError(f"rest backend: auth names the credential '{name}', not in 'credentials' of the source")
        return creds[str(name)]

    def _session(self):
        import requests
        s = requests.Session()
        s.headers["Accept"] = "application/json"
        s.headers.update({str(k): str(v) for k, v in (self.options.get("headers") or {}).items()})
        self.params: dict[str, str] = {}
        auth = self.options.get("auth") or {}
        if not isinstance(auth, dict):
            raise ReqError("rest backend: 'auth' is {bearer: <credential>}, {basic: [<user>, <password>]}, "
                           "{header: {<name>: <credential>}} or {query: {<param>: <credential>}}")
        if auth.get("bearer"):
            s.headers["Authorization"] = f"Bearer {self._credential(auth['bearer'])}"
        if auth.get("basic"):
            user, password = (list(auth["basic"]) + [None])[:2] if isinstance(auth["basic"], list) else (auth["basic"], None)
            s.auth = (self._credential(user), self._credential(password) if password else "")
        for header, cred in (auth.get("header") or {}).items():
            s.headers[str(header)] = self._credential(cred)
        for param, cred in (auth.get("query") or {}).items():
            self.params[str(param)] = self._credential(cred)
        return s

    # ── HTTP ──────────────────────────────────────────────────────────

    def _url(self, url: str) -> str:
        url = str(url)
        if re.match(r"^https?://", url) or not self.base:
            return url
        return urljoin(self.base.rstrip("/") + "/", url.lstrip("/"))

    def _call(self, spec: dict, url: str, missing_ok: bool = False) -> Any:
        import requests
        url = self._url(url)
        try:
            r = self.session.request(str(spec.get("method") or "GET").upper(), url, params=self.params or None,
                                     json=spec.get("body"), timeout=self.timeout, verify=self.verify)
        except requests.RequestException as e:
            raise ReqError(f"rest backend: {url}: {e}") from e
        if missing_ok and r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise ReqError(f"rest backend: {url}: HTTP {r.status_code} {r.reason}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise ReqError(f"rest backend: {url}: the answer is not JSON ({r.text[:100]!r})") from e

    def _pages(self, spec: dict, url: str) -> Iterable[list[Any]]:
        page, offset = int(self.options.get("first_page") or 1), 0
        paged = "{page}" in url or "{offset}" in url
        for _ in range(MAX_PAGES):
            called = self._url(url.replace("{page}", str(page)).replace("{offset}", str(offset)))
            answer = self._call(spec, called)
            items = _all(spec["items"], answer, "items")
            yield items
            if spec["next"] is not None:
                found = [n for n in _all(spec["next"], answer, "next") if n]
                if not found or not items:
                    return
                url = urljoin(called, str(found[0]))         # a next link is relative to the page it comes in
                continue
            if not paged or not items:
                return
            page, offset = page + 1, offset + len(items)
        raise ReqError(f"rest backend: more than {MAX_PAGES} pages: check the pagination ('next', {{page}}, {{offset}})")

    # ── fields ────────────────────────────────────────────────────────

    def requirement(self, item: Any) -> Requirement | None:
        values: dict[str, Any] = {}
        for name, program in self.fields.items():
            found = _all(program, item, f"field '{name}'")
            value = found[0] if found else None
            if name == "tags":
                value = [str(t) for t in (value if isinstance(value, list) else [value] if value not in (None, "") else [])]
            elif isinstance(value, list):
                value = ", ".join(str(v) for v in value if v is not None)
            elif value is not None:
                value = str(value)
            if value not in (None, ""):
                values[name] = value
        if not values.get("id"):
            return None
        req = Requirement.from_dict(values)
        req.path = req.path.strip("/")
        if not req.url and self.options.get("url"):
            req.url = str(self.options["url"]).replace("{id}", req.id)
        if not req.version:
            import hashlib
            import json
            content = json.dumps({k: v for k, v in req.as_dict().items() if k not in ("version", "url", "path")},
                                 sort_keys=True)
            req.version = hashlib.sha256(content.encode()).hexdigest()[:8]
        return req

    def _everything(self) -> dict[str, Requirement]:
        if self._all is None:
            found: dict[str, Requirement] = {}
            for items in self._pages(self.list_spec, str(self.list_spec["url"])):
                for item in items:
                    req = self.requirement(item)
                    if req is not None:
                        found.setdefault(req.id, req)
            self._all = found
        return self._all

    # ── API ───────────────────────────────────────────────────────────

    def tree(self) -> list[Node]:
        paths: set[str] = set()
        for r in self._everything().values():
            parts = [p for p in r.path.split("/") if p]
            paths.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        roots: list[Node] = []
        index: dict[str, Node] = {}
        for path in sorted(paths, key=lambda p: (p.count("/"), p)):
            parent, _, leaf = path.rpartition("/")
            node = Node(leaf)
            index[path] = node
            (index[parent].children if parent in index else roots).append(node)
        return roots

    def list(self, path: str = "", children: bool = False) -> list[Requirement]:
        return [r for r in self._everything().values() if under(r.path, path, children)]

    def get(self, ids: Iterable[str]) -> dict[str, Requirement | None]:
        ids = list(ids)
        spec = self.get_spec
        if spec is None or self._all is not None or not ids:
            everything = self._everything()
            return {i: everything.get(i) for i in ids}
        out: dict[str, Requirement | None] = {i: None for i in ids}
        url = str(spec["url"])
        calls = [url.replace("{ids}", ",".join(quote(i, safe="") for i in ids))] if "{ids}" in url else \
            [url.replace("{id}", quote(i, safe="")) for i in ids]
        for call in calls:
            answer = self._call(spec, call, missing_ok="{id}" in url)
            if answer is None:
                continue
            for item in _all(spec["items"], answer, "get.items"):
                req = self.requirement(item)
                if req is not None and req.id in out:
                    out[req.id] = req
        return out


register_req_backend("rest", RestBackend)
