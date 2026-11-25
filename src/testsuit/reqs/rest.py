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
        vars:                                    # see testsuit.reqs.sources
          subsystem: Power unit
          branch: {value: "{{ sut.branch | default('main') }}", mandatory: true}
        options:
          base: https://alm.example/api/v2
          auth: {bearer: token}                  # or basic: [user, password], header: {X-Api-Key: token}
          steps:                                 # optional: calls run first, each extracting variables
            - url: /systems?branch={{ branch }}
              extract:                           # jq; $name: a variable
                subsystem_id: '.items[] | select(.name == $subsystem) | .id'
              cache: 1h                          # the answer kept that long (default: one generation)
          list:                                  # every requirement, page by page
            url: /subsystems/{{ subsystem_id }}/items?type=requirement&page={{ page }}
            items: .data[]                       # the requirements in an answer
            next: .links.next                    # optional: the next page's URL (relative to the page; none: the last)
          get: {url: "/items?ids={{ ids }}", items: .data[]}     # optional: by ids (else: from the list)
          fields:                                # jq, on each item
            id: .fields.documentKey
            title: .fields.name
            description: .fields.description
            path: '[.location.folder[]?.name] | join("/")'
            status: .fields.status.name
            tags: '[.tags[]?.name]'
            version: .version
          url: https://alm.example/items/{{ id }}
          item_vars: {item_id: .id}              # optional: jq on each list item, for the details calls
          details:                               # optional: calls per requirement, adding fields
            - url: /items/{{ item_id }}
              fields: {description: .data.text, rationale: .data.custom.rationale}
              cache: 1d
            - url: /items/{{ item_id }}/comments
              fields: {comments: '[.data[].body] | join("\\n\\n")'}

Variables: ``{{ name }}`` in URLs (URL-encoded), headers and bodies (a body string made of one ``{{ name }}`` keeps the
value's type); ``$name`` in jq expressions. Those of the source, those the steps extract, and ``page``, ``offset``,
``id`` and ``ids`` where they apply (also written ``{page}``, ``{offset}``, ``{id}``, ``{ids}``).

A step's variable is ``name: <jq>`` or ``name: {jq, mandatory}``. It must give one value at most (several: an
error); none gives it empty, an error when it is mandatory (a fallback: jq's ``//``, ``.id // 0``). Mandatory ones are
the context of the requirements, recorded and shown with them, like the source's mandatory variables.

Details: calls made for each requirement that is needed in full (one linked by a test definition, at generation; one
opened in the Requirements tab), never to list or to build the tree. Each gives fields with jq on its answer, added
to (or replacing) those of the list item; a later call wins. ``id``, ``path`` and ``version`` come from the list
only, so that a requirement compares the same whether its details were read or not (the version, when not a field:
a hash of the list's fields; give the list a modification field, ``version: .updated``, to see every change).
``item_vars`` are variables taken from each list item (jq), for the URLs and bodies of the details calls (``{{ id }}``
is there too). A 404 leaves the fields of that call out. ``cache``: how long to keep an answer (by request, and by
the version of the requirement when the list gives one: a changed requirement is read again). At most
``DETAILS_PARALLEL`` calls at once.

Pagination: ``next`` (a jq expression giving the URL of the next page), else ``{{ page }}`` in the URL (from
``first_page``, default 1) or ``{{ offset }}`` (stepping by the number of items received), until a page has no item.
Without any of them, one call. The tree is made of the paths of the requirements.

Debugging: an error names the call (``step 2``, ``list, page 3``, ``get``), its method and its full URL, credentials
masked (``***``). With a ``trace`` (the Editor's tests), each call is described in it: URL, headers, body, status, answer,
what a step extracted, a page's items; ``probe`` runs the calls up to a step or the list call's first page.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

from testsuit.reqs import progress
from testsuit.reqs.backend import ReqBackend, ReqError, register_req_backend, under
from testsuit.reqs.model import Node, Requirement

FIELDS = ("id", "path", "title", "description", "rationale", "tags", "comments", "status", "version", "url")
MAX_PAGES = 1000
DETAILS_PARALLEL = 8                    # details calls at once
LIST_ONLY = ("id", "path", "version")   # the fields the details calls cannot give
TRACE_CALLS = 30                        # the calls a test describes (the others are counted)
TRACE_ANSWER = 16000                    # the characters of an answer it shows
MASK = "***"
_MASKED = "MASKEDCREDENTIAL"            # a credential in a URL being shown, before it is written MASK
BUILT_IN = ("page", "offset", "id", "ids")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}|\{(page|offset|ids|id)\}")
_ONE_VAR = re.compile(r"\s*\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}\s*")

# answers of steps with a cache, by request: (expiry, answer). Shared by the backends of the process (the daemon).
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _jq(expr: str, where: str, args: Mapping[str, Any] | None = None):
    try:
        import jq
    except ImportError as e:            # a core dependency: only a broken install lacks it
        raise ReqError("the rest backend needs the 'jq' Python package (pip install jq)") from e
    try:
        return jq.compile(str(expr), args=dict(args or {}))
    except ValueError as e:
        message = str(e).splitlines()[0]
        if "is not defined" in message:
            message += " (variables: those of the source and of earlier steps)"
        shown = " ".join(str(expr).split())     # one line, an expression written over several
        raise ReqError(f"{where}: jq expression '{shown}': {message}") from e


def _all(program, value: Any, where: str) -> list[Any]:
    try:
        return program.input_value(value).all()
    except ValueError as e:
        raise ReqError(f"{where}: {str(e).splitlines()[0]}") from e


def duration(value: Any, where: str) -> float:
    """Seconds of a duration: a number of seconds, or 30s, 10m, 2h, 1d."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", str(value))
    if not m:
        raise ReqError(f"{where}: '{value}' is not a duration (seconds, or 30s, 10m, 2h, 1d)")
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


_fresh: ContextVar[bool] = ContextVar("rest_fresh", default=False)


@contextmanager
def fresh() -> Iterator[None]:
    """Within it, the calls go to the servers even when the cache has their answer (still in its time): a refresh
    asked for. Their answers fill the cache again."""
    token = _fresh.set(True)
    try:
        yield
    finally:
        _fresh.reset(token)


_SUBST = (" `{{ name }}`: a plain substitution of a variable (of the source, or extracted by a step), not Jinja2 (no "
          "filters, no datapack keys: those go through a variable of the source).")


class RestBackend(ReqBackend):
    """Requirements of any tool with a JSON REST API: URLs, authentication and jq expressions picking the fields."""

    capabilities = frozenset({"children_query", "versions"})
    description = "Built in: any tool with a JSON REST API, its fields picked with jq expressions."
    options_help = {
        "base": "The base URL of the API; the URLs below are relative to it (or absolute)." + _SUBST,
        "auth": "How to authenticate, with the names of the credentials: `{bearer: token}` (Authorization: Bearer), "
                "`{basic: [user, password]}`, `{header: {X-Api-Key: token}}` or `{query: {api_key: token}}`.",
        "headers": "Other headers sent with each request (not secrets: those are credentials)." + _SUBST,
        "steps": "Optional calls made first, each extracting variables for the next ones: `[{url, extract, cache}]`. "
                 "`extract`: `{name: <jq>}` or `{name: {jq, mandatory}}`, one value at most (a fallback: `.id // 0`); `$name` in jq "
                 "and `{{ name }}` in URLs read variables. `cache`: how long to keep the answer (`1h`). Optional "
                 "`method` and `body`." + _SUBST,
        "list": "How to get every requirement: `{url, items, next}`. `items`: jq giving the requirements of an answer "
                "(`.data[]`); pagination with `next` (jq giving the next page's URL), or `{{ page }}` / "
                "`{{ offset }}` in the URL (until a page has no item). Optional `method` (GET) and `body` (JSON, "
                "for a POST)." + _SUBST,
        "get": "Optional: how to get requirements by id: `{url, items}`, with `{{ ids }}` (comma-separated) or "
               "`{{ id }}` (one call each, a 404 meaning unknown) in the URL. Default: from the list." + _SUBST,
        "fields": "jq expressions giving each field of a requirement from an item: `id` (required), `title`, "
                  "`description`, `path` (nodes joined by `/`), `rationale`, `status`, `tags` (a list), `comments`, "
                  "`version`, `url`. Default for a field not given (but `url`): `.<field>`.",
        "url": "A link to each requirement in its tool, `{{ id }}` replaced by its id (when no `url` field)." + _SUBST,
        "item_vars": "Optional: variables taken from each list item with jq (`{item_id: .id}`), for the URLs and bodies "
                     "of the details calls (an internal id the tool's API wants, the displayed one being `id`).",
        "details": "Optional calls made per requirement, each adding fields from its answer: `[{url, fields, cache}]`, "
                   "with `{{ id }}` and the item variables in the URL. Made only for the requirements needed in full "
                   "(linked by a test definition, at generation; opened in the Requirements tab), never for listing. "
                   "`fields`: jq on the answer, as `fields` (not `id`, `path`, `version`: those come from the list; none: the call is not made); "
                   "a later call wins. `cache`: how long to keep an answer (`1d`); a requirement whose version "
                   "changed is read again. A 404 leaves its fields out. Optional `method` and `body`." + _SUBST,
        "first_page": "The first `{{ page }}` (default 1).",
        "timeout": "Seconds to wait for each answer (default 30).",
        "verify": "Check the server's TLS certificate: true (default), false, or the path of a CA bundle.",
    }
    credentials_help = {
        "token": "A token, used as `auth: {bearer: token}` or in a header.",
        "user": "A user name, for `auth: {basic: [user, password]}`.",
        "password": "Its password.",
    }

    def __init__(self, options: dict[str, Any], credentials: dict[str, str] | None, base_dir: Path,
                 vars: Mapping[str, Any] | None = None) -> None:
        super().__init__(options, credentials, base_dir)
        o = self.options
        self.vars: dict[str, Any] = dict(vars or {})
        if not isinstance(o.get("list"), dict) or not o["list"].get("url"):
            raise ReqError("the rest backend needs its 'list' option: {url, items[, next]}")
        self.base = str(o.get("base") or "")
        self.timeout = float(o.get("timeout") or 30)
        self.deadline: float | None = None           # time.monotonic() when a test of the Editor stops calling
        self.test_limit = 0.0
        verify = o.get("verify", True)
        self.verify = str((base_dir / str(verify)).resolve()) if isinstance(verify, str) else bool(verify)
        self.steps = self._steps(o.get("steps"))
        self.mandatory_vars = [n for s in self.steps for n, e in s["extract"].items() if e["mandatory"]]
        names = [*self.vars, *(n for s in self.steps for n in s["extract"])]
        known = {n: None for n in names}
        fields = o.get("fields") or {}
        if not isinstance(fields, dict):
            raise ReqError("rest backend: 'fields' is a mapping of field names to jq expressions")
        unknown = sorted(set(fields) - set(FIELDS))
        if unknown:
            raise ReqError(f"rest backend: unknown field(s) {', '.join(unknown)} (fields: {', '.join(FIELDS)})")
        self.fields = {f: str(fields.get(f) or f".{f}?") for f in FIELDS if f in fields or f != "url"}
        for f, expr in self.fields.items():
            _jq(expr, f"field '{f}'", known)
        self.list_spec = self._spec(o["list"], "list", known)
        self.get_spec = self._spec(o["get"], "get", known) if o.get("get") else None
        self.item_vars = self._item_vars(o.get("item_vars"), names, known)
        self.details = self._details(o.get("details"), known)
        self.version_given = "version" in fields
        self._check_names(names)
        self.session = self._session()
        self._all: dict[str, Requirement] | None = None
        self._found: dict[str, Any] | None = None           # the variables the steps extracted, once run
        self._programs: dict[str, Any] = {}
        self.trace_more = 0                                  # the calls made beyond the TRACE_CALLS described
        self._record: dict | None = None                     # the description of the latest call, when traced
        self._items: dict[str, dict[str, Any]] = {}          # the item variables of each requirement read
        self._detailed: dict[str, Requirement] = {}          # the requirements with their details, by id

    def _steps(self, steps: Any) -> list[dict]:
        if steps in (None, []):
            return []
        if not isinstance(steps, list):
            raise ReqError("rest backend: 'steps' is a list of calls: [{url, extract}]")
        out, names = [], list(self.vars)
        for n, step in enumerate(steps, 1):
            where = f"rest backend: step {n}"
            if not isinstance(step, dict) or not step.get("url"):
                raise ReqError(f"{where}: {{url, extract[, cache, method, body]}} expected")
            extract = step.get("extract") or {}
            if not isinstance(extract, dict) or not extract:
                raise ReqError(f"{where}: 'extract' gives variables: {{name: <jq>}}")
            spec = {**step, "extract": {}, "cache": duration(step["cache"], f"{where}: cache") if step.get("cache") else 0}
            for name, e in extract.items():
                if not NAME.fullmatch(str(name)) or name in BUILT_IN:
                    raise ReqError(f"{where}: '{name}' cannot be a variable name"
                                   + (" (a built-in one)" if name in BUILT_IN else " (letters, digits, _)"))
                if isinstance(e, dict):
                    bad = sorted(set(e) - {"jq", "mandatory"})
                    if bad or not e.get("jq"):
                        raise ReqError(f"{where}: variable '{name}': {{jq, mandatory}} expected (a fallback goes in the jq "
                                       "expression: .id // 0)")
                    e = {"jq": str(e["jq"]), "mandatory": bool(e.get("mandatory"))}
                else:
                    e = {"jq": str(e), "mandatory": False}
                _jq(e["jq"], f"{where}: variable '{name}'", {k: None for k in names})
                spec["extract"][str(name)] = e
            names += list(spec["extract"])
            out.append(spec)
        return out

    def _item_vars(self, spec: Any, names: list[str], known: Mapping[str, Any]) -> dict[str, str]:
        if not spec:
            return {}
        if not isinstance(spec, dict):
            raise ReqError("rest backend: 'item_vars' is a mapping of names to jq expressions on a list item")
        out = {}
        for name, expr in spec.items():
            if not NAME.fullmatch(str(name)) or name in BUILT_IN or name in names:
                raise ReqError(f"rest backend: item_vars: '{name}' cannot be a variable name"
                               + (" (a built-in one)" if name in BUILT_IN else " (already a variable)" if name in names
                                  else " (letters, digits, _)"))
            _jq(str(expr), f"rest backend: item_vars: '{name}'", known)
            out[str(name)] = str(expr)
        return out

    def _details(self, spec: Any, known: Mapping[str, Any]) -> list[dict]:
        if spec in (None, []):
            return []
        if isinstance(spec, dict):
            spec = [spec]
        if not isinstance(spec, list):
            raise ReqError("rest backend: 'details' is a list of calls: [{url, fields}]")
        out = []
        for n, call in enumerate(spec, 1):
            where = f"rest backend: details {n}"
            if not isinstance(call, dict) or not call.get("url"):
                raise ReqError(f"{where}: {{url, fields[, cache, method, body]}} expected")
            fields = call.get("fields") or {}
            if not isinstance(fields, dict):
                raise ReqError(f"{where}: 'fields' gives fields of the requirement: {{description: <jq>}}")
            bad = sorted(set(fields) - set(FIELDS))
            if bad:
                raise ReqError(f"{where}: unknown field(s) {', '.join(bad)} (fields: {', '.join(FIELDS)})")
            only = sorted(set(fields) & set(LIST_ONLY))
            if only:
                raise ReqError(f"{where}: {', '.join(only)} come(s) from the list only (fields of the list)")
            for f, expr in fields.items():
                _jq(str(expr), f"{where}: field '{f}'", known)
            out.append({**call, "fields": {str(f): str(e) for f, e in fields.items()},
                        "cache": duration(call["cache"], f"{where}: cache") if call.get("cache") else 0,
                        "name": f"details {n}"})
        return out

    def _check_names(self, names: list[str]) -> None:
        """Each {{ name }} of the URLs, headers and bodies is a variable."""
        def texts(value: Any, where: str) -> Iterable[tuple[str, str]]:
            if isinstance(value, str):
                yield value, where
            elif isinstance(value, dict):
                for k, v in value.items():
                    yield from texts(v, f"{where}.{k}")
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    yield from texts(v, f"{where}[{i}]")
        o = self.options
        for i, step in enumerate(self.steps):
            earlier = [*self.vars, *(n for s in self.steps[:i] for n in s["extract"])]
            for text, where in texts({k: step.get(k) for k in ("url", "body")}, f"step {i + 1}"):
                self._names(text, where, earlier)
        for key in ("list", "get"):
            for text, where in texts({k: (o.get(key) or {}).get(k) for k in ("url", "body")}, key):
                self._names(text, where, [*names, *BUILT_IN])
        for call in self.details:
            for text, where in texts({k: call.get(k) for k in ("url", "body")}, call["name"]):
                self._names(text, where, [*names, *self.item_vars, "id"])
        for text, where in texts({"headers": o.get("headers"), "url": o.get("url")}, "options"):
            self._names(text, where, [*names, *BUILT_IN])

    @staticmethod
    def _names(text: str, where: str, known: list[str]) -> None:
        for m in _VAR.finditer(text):
            name = m.group(1) or m.group(2)
            if name not in known:
                raise ReqError(f"rest backend: {where}: unknown variable '{name}' (known here: "
                               f"{', '.join(dict.fromkeys(known)) or 'none'})")

    def _spec(self, spec: dict, name: str, known: Mapping[str, Any]) -> dict:
        if not isinstance(spec, dict) or not spec.get("url"):
            raise ReqError(f"rest backend: '{name}' is {{url, items, ...}}")
        out = dict(spec)
        out["items"] = str(spec.get("items") or ".[]")
        _jq(out["items"], f"{name}.items", known)
        out["next"] = str(spec["next"]) if spec.get("next") else None
        if out["next"]:
            _jq(out["next"], f"{name}.next", known)
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

    # ── variables ─────────────────────────────────────────────────────

    def _variables(self) -> dict[str, Any]:
        """The variables, after the steps (run on first use)."""
        if self._found is None:
            self._found = self._run_steps(len(self.steps))
        return {**self.vars, **self._found}

    def _run_steps(self, count: int) -> dict[str, Any]:
        """The variables extracted by the first ``count`` steps."""
        found: dict[str, Any] = {}
        for n, step in enumerate(self.steps[:count], 1):
            self._record = None
            answer = self._step_answer(step, {**self.vars, **found}, f"step {n}")
            record, extracted = self._record, {}
            try:
                for name, e in step["extract"].items():
                    where = f"rest backend: step {n}: variable '{name}'"
                    values = [v for v in _all(_jq(e["jq"], where, {**self.vars, **found}), answer, where)
                              if v not in (None, "")]
                    if len(values) > 1:
                        shown = ", ".join(json.dumps(v)[:40] for v in values[:5])
                        raise ReqError(f"{where}: {len(values)} values ({shown}{'...' if len(values) > 5 else ''}): "
                                       "one at most (make the jq expression select one)")
                    if values:
                        found[name] = extracted[name] = values[0]
                    elif e["mandatory"]:
                        raise ReqError(f"{where}: no value, and it is mandatory (check the jq expression, or give "
                                       "it a fallback: .id // 0)")
                    else:
                        found[name] = extracted[name] = ""
            except ReqError as e:
                if record is not None:
                    record["error"] = self._mask(str(e))
                raise
            finally:
                if record is not None:
                    record["extracted"] = extracted
        return found

    def resolved_vars(self) -> dict[str, Any]:
        return dict(self._found or {})

    def _program(self, expr: str, where: str):
        """A jq expression compiled with the variables as $name."""
        if expr not in self._programs:
            self._programs[expr] = _jq(expr, where, self._variables())
        return self._programs[expr]

    def _fill(self, text: str, extra: Mapping[str, Any] | None = None, encode: bool = False,
              raw: Iterable[str] = (), variables: Mapping[str, Any] | None = None) -> str:
        """{{ name }} (and {page}, {offset}, {id}, {ids}) replaced by the values of the variables."""
        values = {**(self._variables() if variables is None else variables), **(extra or {})}
        raw = set(raw)

        def value(m: re.Match) -> str:
            name = m.group(1) or m.group(2)
            if name not in values:
                raise ReqError(f"rest backend: '{text}': unknown variable '{name}'")
            v = values[name]
            v = json.dumps(v) if isinstance(v, (dict, list, bool)) else "" if v is None else str(v)
            return quote(v, safe="") if encode and name not in raw else v
        return _VAR.sub(value, text)

    def _fill_data(self, data: Any, variables: Mapping[str, Any] | None = None) -> Any:
        """A body: {{ name }} replaced in its strings; a string of one {{ name }} is the value itself."""
        variables = self._variables() if variables is None else variables
        if isinstance(data, str):
            one = _ONE_VAR.fullmatch(data)
            if one and one.group(1) in variables:
                return variables[one.group(1)]
            return self._fill(data, variables=variables)
        if isinstance(data, dict):
            return {k: self._fill_data(v, variables) for k, v in data.items()}
        if isinstance(data, list):
            return [self._fill_data(v, variables) for v in data]
        return data

    # ── HTTP ──────────────────────────────────────────────────────────

    def _url(self, url: str, variables: Mapping[str, Any] | None = None) -> str:
        url = str(url)
        base = self._fill(self.base, variables=variables)
        if re.match(r"^https?://", url) or not base:
            return url
        return urljoin(base.rstrip("/") + "/", url.lstrip("/"))

    # ── what is shown of a call: its URL, headers and answer, the credentials masked ──

    def _secrets(self) -> list[str]:
        values = {str(v) for v in (self.credentials or {}).values() if v and len(str(v)) >= 3}
        return sorted(values | {quote(v, safe="") for v in values}, key=len, reverse=True)

    def _mask(self, text: str) -> str:
        """A text with the values of the credentials (as they are, and URL-encoded) replaced by ***."""
        for secret in self._secrets():
            text = text.replace(secret, MASK)
        return text

    def _mask_data(self, data: Any) -> Any:
        if isinstance(data, str):
            return self._mask(data)
        if isinstance(data, dict):
            return {k: self._mask_data(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self._mask_data(v) for v in data]
        return data

    def shown_url(self, url: str) -> str:
        """The URL a call requests, in full (the query parameters of the authentication added), credentials masked."""
        from requests.models import PreparedRequest
        p = PreparedRequest()
        try:
            p.prepare_url(url, {k: _MASKED for k in self.params} or None)
        except Exception:                       # an invalid URL: shown as it is (the call fails on it)
            return self._mask(url)
        return self._mask(str(p.url)).replace(_MASKED, MASK)

    def _shown_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        out = {str(k): str(v) for k, v in {**self.session.headers, **headers}.items()}
        auth = self.options.get("auth") or {}
        if "Authorization" in out and auth.get("bearer"):
            out["Authorization"] = f"Bearer {MASK}"
        for header in auth.get("header") or {}:
            if str(header) in out:
                out[str(header)] = MASK
        if self.session.auth:
            out["Authorization"] = f"Basic {MASK}"
        return {k: self._mask(v) for k, v in out.items()}

    def _answer(self, r) -> tuple[str, bool]:
        """An answer as shown: indented when JSON, its first characters, credentials masked; and whether it is cut."""
        try:
            text = json.dumps(r.json(), indent=1, ensure_ascii=False)
        except ValueError:
            text = r.text
        return self._mask(text[:TRACE_ANSWER]), len(text) > TRACE_ANSWER

    def _call(self, spec: dict, url: str, missing_ok: bool = False, variables: Mapping[str, Any] | None = None,
              label: str = "call") -> Any:
        """The JSON answer of a call. An error names the call, its method and its full URL (credentials masked);
        with a trace, the call is described in it (self._record)."""
        import requests
        url = self._url(url, variables)
        method = str(spec.get("method") or "GET").upper()
        body = self._fill_data(spec.get("body"), variables) if spec.get("body") is not None else None
        headers = {str(k): self._fill(str(v), variables=variables) for k, v in (self.options.get("headers") or {}).items()}
        shown = self.shown_url(url)
        where = f"rest backend: {label}: {method} {shown}"
        record: dict | None = None
        if self.trace is not None:
            if len(self.trace) < TRACE_CALLS:
                record = {"call": label, "method": method, "url": shown, "headers": self._shown_headers(headers),
                          **({"body": self._mask_data(body)} if body is not None else {})}
                self.trace.append(record)
            else:
                self.trace_more += 1
        self._record = record
        progress.call(f"{label}: {method} {shown}")
        start = time.monotonic()
        timeout = self.timeout
        if self.deadline is not None:                # a test of the Editor: the whole of it within a limit
            left = self.deadline - start
            if left <= 0:
                message = f"stopped: the test takes more than {self.test_limit:g} s"
                if record is not None:
                    record.update(seconds=0, error=message)
                raise ReqError(f"{where}: {message}")
            timeout = min(timeout, left)
        from testsuit import netguard
        if self.trace is None:                       # a server down is not called again for a while (a test tries)
            try:
                netguard.check(url)
            except netguard.ServerDown as e:
                raise ReqError(f"{where}: {e}") from e
        try:
            r = self.session.request(method, url, params=self.params or None,
                                     json=body, timeout=timeout, verify=self.verify, headers=headers or None)
        except requests.RequestException as e:
            message = self._mask(str(e)).replace(_MASKED, MASK)
            if record is not None:
                record.update(seconds=round(time.monotonic() - start, 3), error=message)
            if isinstance(e, (requests.ConnectionError, requests.Timeout)):
                netguard.failed(url, message)
            raise ReqError(f"{where}: {message}") from e
        if r.status_code in netguard.DOWN_STATUSES:
            netguard.failed(url, f"HTTP {r.status_code}")
        else:
            netguard.answered(url)
        if record is not None:
            answer, cut = self._answer(r)
            record.update(seconds=round(time.monotonic() - start, 3), status=r.status_code, reason=r.reason,
                          content_type=r.headers.get("Content-Type", ""), size=len(r.content), answer=answer,
                          **({"cut": True} if cut else {}))
        if missing_ok and r.status_code == 404:
            return None
        if r.status_code >= 400:
            error = f"HTTP {r.status_code} {r.reason}: {self._mask(r.text[:200])}"
            if record is not None:
                record["error"] = error
            raise ReqError(f"{where}: {error}")
        try:
            return r.json()
        except ValueError as e:
            error = f"the answer is not JSON ({self._mask(r.text[:100])!r})"
            if record is not None:
                record["error"] = error
            raise ReqError(f"{where}: {error}") from e

    def _step_answer(self, step: dict, variables: Mapping[str, Any], label: str = "step", missing_ok: bool = False,
                     version: str | None = None) -> Any:
        """The answer of a step's (or details) call, from the cache when it has one (keyed by the request, credentials
        included, and the version of the requirement it is about). A traced call (a test) does not use the cache: it
        shows what the server answers now."""
        url = self._url(self._fill(str(step["url"]), encode=True, variables=variables), variables)
        if not step["cache"] or self.trace is not None:
            return self._call(step, url, variables=variables, label=label, missing_ok=missing_ok)
        key = hashlib.sha256(json.dumps([str(step.get("method") or "GET").upper(), url,
                                         self._fill_data(step.get("body"), variables),
                                         sorted(self.session.headers.items()), sorted(self.params.items()),
                                         repr(self.session.auth), version], default=str).encode()).hexdigest()
        now = time.monotonic()
        with _cache_lock:
            hit = _cache.get(key)
        if hit and hit[0] > now and not _fresh.get():
            return hit[1]
        answer = self._call(step, url, variables=variables, label=label, missing_ok=missing_ok)
        with _cache_lock:
            _cache[key] = (now + step["cache"], answer)
        return answer

    def _pages(self, spec: dict, url: str) -> Iterable[list[Any]]:
        """The items of each page of the list call (a traced call tells how many items, and the next page's URL)."""
        page, offset = int(self.options.get("first_page") or 1), 0
        paged = any((m.group(1) or m.group(2)) in ("page", "offset") for m in _VAR.finditer(url))
        items_program = self._program(spec["items"], f"{spec['name']}.items")
        next_program = self._program(spec["next"], f"{spec['name']}.next") if spec["next"] else None
        filled = None
        for n in range(1, MAX_PAGES + 1):
            called = self._url(filled or self._fill(url, {"page": page, "offset": offset}, encode=True))
            answer = self._call(spec, called, label=f"{spec['name']}, page {n}")
            record = self._record
            try:
                items = _all(items_program, answer, f"{spec['name']}.items")
                found = [u for u in _all(next_program, answer, f"{spec['name']}.next") if n] if next_program else []
            except ReqError as e:
                if record is not None:
                    record["error"] = self._mask(str(e))
                raise
            if record is not None:
                record["items"] = len(items)
                if found:
                    record["next"] = self.shown_url(urljoin(called, str(found[0])))
            yield items
            if next_program is not None:
                if not found or not items:
                    return
                filled = urljoin(called, str(found[0]))         # a next link is relative to the page it comes in
                continue
            if not paged or not items:
                return
            page, offset = page + 1, offset + len(items)
        raise ReqError(f"rest backend: more than {MAX_PAGES} pages: check the pagination ('next', {{{{ page }}}}, "
                       "{{ offset }})")

    # ── fields ────────────────────────────────────────────────────────

    def requirement(self, item: Any) -> Requirement | None:
        values: dict[str, Any] = {}
        for name, expr in self.fields.items():
            found = _all(self._program(expr, f"field '{name}'"), item, f"field '{name}'")
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
        if self.item_vars:
            item_values = {}
            for name, expr in self.item_vars.items():
                found = _all(self._program(expr, f"item_vars '{name}'"), item, f"item_vars '{name}'")
                item_values[name] = found[0] if found and found[0] is not None else ""
            self._items[str(values["id"])] = item_values
        req = Requirement.from_dict(values)
        req.path = req.path.strip("/")
        if not req.url and self.options.get("url"):
            req.url = self._fill(str(self.options["url"]), {"id": req.id})
        if not req.version:
            content = json.dumps({k: v for k, v in req.as_dict().items() if k not in ("version", "url", "path")},
                                 sort_keys=True)
            req.version = hashlib.sha256(content.encode()).hexdigest()[:8]
        return req

    def _everything(self) -> dict[str, Requirement]:
        if self._all is None:
            found: dict[str, Requirement] = {}
            for items in self._pages({**self.list_spec, "name": "list"}, str(self.list_spec["url"])):
                for item in items:
                    req = self.requirement(item)
                    if req is not None:
                        found.setdefault(req.id, req)
            self._all = found
        return self._all
    # ── the Editor's tests ────────────────────────────────────────────

    def probe(self, upto: str, expr: str | None = None, on: str = "list") -> dict[str, Any]:
        """Run the calls up to a stage, to see what they do (with a trace: each call described): ``step:N`` (the first
        N steps: the variables they extract), ``list`` (the steps, then the first page of the list call: how many
        items, and the first requirements their fields give). With ``expr`` (Debug): that jq expression alone, on the
        answer of the stage (see ``_debug``)."""
        if expr is not None:
            return self._debug(upto, expr, on)
        m = re.fullmatch(r"step:(\d+)", upto)
        if m:
            count = int(m.group(1))
            if not 1 <= count <= len(self.steps):
                raise ReqError(f"rest backend: no step {count} (steps: {len(self.steps)})")
            return {"vars": self._run_steps(count)}
        m = re.fullmatch(r"details:(\d+)", upto)
        if m:                                              # the list's first page, then the details of its first one
            count = int(m.group(1))
            if not 1 <= count <= len(self.details):
                raise ReqError(f"rest backend: no details call {count} (details calls: {len(self.details)})")
            self._variables()
            first = None
            for item in next(iter(self._pages({**self.list_spec, "name": "list"}, str(self.list_spec["url"]))), []):
                first = self.requirement(item)
                if first is not None:
                    break
            if first is None:
                raise ReqError("rest backend: the list's first page has no requirement to read the details of")
            calls, self.details = self.details, self.details[count - 1:count]
            try:
                self._with_details([first])
            finally:
                self.details = calls
            return {"vars": dict(self._found or {}), "item_vars": self._items.get(first.id, {}),
                    "requirement": self._detailed[first.id].as_dict()}
        if upto == "ids":
            return self._probe_ids()
        if upto.startswith("item:"):
            return self._probe_item(upto[len("item:"):])
        if upto != "list":
            raise ReqError(f"rest backend: '{upto}': step:<N>, list, details:<N>, ids or item:<id> expected")
        self._variables()
        items = next(iter(self._pages({**self.list_spec, "name": "list"}, str(self.list_spec["url"]))), [])
        sample = []
        for n, item in enumerate(items[:5], 1):
            try:
                req = self.requirement(item)
            except ReqError as e:
                raise ReqError(f"rest backend: list, item {n}: {e}") from e
            sample.append(req.as_dict() if req is not None else {"id": None})
        return {"vars": dict(self._found or {}), "items": len(items), "sample": sample,
                "first_item": self._mask(json.dumps(items[0], indent=1, ensure_ascii=False)[:TRACE_ANSWER]) if items else None}

    PICK_IDS = 5000                         # the requirements the Editor's field test proposes

    def _list_items(self) -> Iterable[Any]:
        for items in self._pages({**self.list_spec, "name": "list"}, str(self.list_spec["url"])):
            yield from items

    def _one(self, expr: str, data: Any, where: str, field: str) -> Any:
        """A field's value, from jq on an item or an answer, as the requirement keeps it (ReqError on a jq error)."""
        found = _all(self._program(expr, where), data, where)
        value = found[0] if found else None
        if field == "tags":
            return [str(t) for t in (value if isinstance(value, list) else [value] if value not in (None, "") else [])]
        if isinstance(value, list):
            return ", ".join(str(v) for v in value if v is not None)
        return None if value is None else str(value)

    def _probe_ids(self) -> dict[str, Any]:
        """The steps, then every page of the list: the id and title of each requirement (the field test's choices)."""
        self._variables()
        out, count, bad = [], 0, 0
        for item in self._list_items():
            count += 1
            try:
                rid = self._one(self.fields["id"], item, "field 'id'", "id")
                title = self._one(self.fields["title"], item, "field 'title'", "title") if "title" in self.fields else None
            except ReqError:
                bad += 1
                continue
            if rid and len(out) < self.PICK_IDS:
                out.append({"id": rid, **({"title": title} if title else {})})
        return {"vars": dict(self._found or {}), "items": count, "ids": out, **({"bad": bad} if bad else {})}

    def _probe_item(self, wanted: str) -> dict[str, Any]:
        """The steps, the list until the requirement of this id, then each field (from the list item or a details call)
        evaluated on its own: its value, or its error; and the requirement they make."""
        item = self._item(wanted)
        rows: list[dict[str, Any]] = []
        values: dict[str, Any] = {}
        given = self.options.get("fields") or {}
        for name, expr in self.fields.items():
            row: dict[str, Any] = {"field": name, "from": "list", "jq": expr, **({} if name in given else {"default": True})}
            try:
                row["value"] = self._one(expr, item, f"field '{name}'", name)
            except ReqError as e:
                row["error"] = str(e)
            if row.get("value") not in (None, "", []):
                values[name] = row["value"]
            if name in given or row.get("value") not in (None, "", []):
                rows.append(row)
        item_values: dict[str, Any] = {}
        for name, expr in self.item_vars.items():
            try:
                found = _all(self._program(expr, f"item_vars '{name}'"), item, f"item_vars '{name}'")
                item_values[name] = found[0] if found and found[0] is not None else ""
            except ReqError as e:
                item_values[name] = f"<error: {e}>"
        local = {**self._variables(), **item_values, "id": wanted}
        for n, call in enumerate(self.details, 1):
            if not call["fields"]:
                continue
            try:
                answer = self._call(call, self._url(self._fill(str(call["url"]), encode=True, variables=local), local),
                                    variables=local, label=f"{call['name']} of {wanted}", missing_ok=True)
                failed = None if answer is not None else "HTTP 404: no answer, the fields are left out"
            except ReqError as e:
                answer, failed = None, str(e)
            for name, expr in call["fields"].items():
                row = {"field": name, "from": f"details:{n}", "jq": expr}
                if failed:
                    row["error"] = failed
                else:
                    try:
                        row["value"] = self._one(expr, answer, f"{call['name']}: field '{name}'", name)
                    except ReqError as e:
                        row["error"] = str(e)
                    if row.get("value") not in (None, "", []):
                        values[name] = row["value"]
                rows.append(row)
        return {"vars": dict(self._found or {}), "item_vars": item_values, "fields": self._mask_data(rows),
                "first_item": self._mask(json.dumps(item, indent=1, ensure_ascii=False)[:TRACE_ANSWER])}

    EXPR_VALUES = 50                        # the values of a tested expression shown at most

    def _item(self, wanted: str) -> Any:
        """The list item of the requirement of this id (the steps, then the list until it)."""
        self._variables()
        for candidate in self._list_items():
            try:
                if self._one(self.fields["id"], candidate, "field 'id'", "id") == wanted:
                    return candidate
            except ReqError:
                continue
        raise ReqError(f"rest backend: no requirement '{wanted}' in the list")

    def _debug(self, upto: str, expr: str, on: str) -> dict[str, Any]:
        """Any jq expression, with the variables known at that stage, on what the stage's jq reads: ``step:N``, the
        answer of step N (the variables of the steps before it); ``list``, the list call's first page; ``details:N``,
        the answer of that call for the list's first requirement; ``item:<id>``, the list item of that requirement, or
        (``on``: ``details:N``) the answer of that details call for it. Every value it gives (as JSON, not as a field
        keeps it), or its error."""
        m = re.fullmatch(r"step:(\d+)", upto)
        if m:
            n = int(m.group(1))
            if not 1 <= n <= len(self.steps):
                raise ReqError(f"rest backend: no step {n} (steps: {len(self.steps)})")
            found = self._run_steps(n - 1)
            variables = {**self.vars, **found}
            answer = self._step_answer(self.steps[n - 1], variables, f"step {n}")
            return self._evaluate(expr, answer, variables, f"the answer of step {n}", {"vars": found})
        if upto == "list":
            variables = self._variables()
            url = self._fill(str(self.list_spec["url"]), {"page": int(self.options.get("first_page") or 1), "offset": 0},
                             encode=True)
            answer = self._call({**self.list_spec, "name": "list"}, self._url(url), label="list, page 1")
            return self._evaluate(expr, answer, variables, "the answer of the list call (first page)",
                                  {"vars": dict(self._found or {})})
        m = re.fullmatch(r"details:(\d+)", upto)
        if m:
            self._variables()
            first = None
            for item in self._list_items():                 # its id only: the other fields may be the bug
                try:
                    first = self._one(self.fields["id"], item, "field 'id'", "id")
                except ReqError:
                    continue
                if first:
                    break
            if not first:
                raise ReqError("rest backend: the list has no requirement to read the details of")
            return self._debug(f"item:{first}", expr, upto)
        if not upto.startswith("item:"):
            raise ReqError(f"rest backend: '{upto}': step:<N>, list, details:<N> or item:<id> expected")
        wanted = upto[len("item:"):]
        item = self._item(wanted)
        m = re.fullmatch(r"details:(\d+)", on)
        if not m:
            if on != "list":
                raise ReqError(f"rest backend: '{on}': list or details:<N> expected")
            return self._evaluate(expr, item, self._variables(), f"the list item of {wanted}",
                                  {"vars": dict(self._found or {})})
        n = int(m.group(1))
        if not 1 <= n <= len(self.details):
            raise ReqError(f"rest backend: no details call {n} (details calls: {len(self.details)})")
        item_values: dict[str, Any] = {}
        for name, e in self.item_vars.items():
            found = _all(self._program(e, f"item_vars '{name}'"), item, f"item_vars '{name}'")
            item_values[name] = found[0] if found and found[0] is not None else ""
        local = {**self._variables(), **item_values, "id": wanted}
        call = self.details[n - 1]
        answer = self._call(call, self._url(self._fill(str(call["url"]), encode=True, variables=local), local),
                            variables=local, label=f"{call['name']} of {wanted}", missing_ok=True)
        if answer is None:
            raise ReqError(f"rest backend: {call['name']} of {wanted}: HTTP 404, no answer")
        return self._evaluate(expr, answer, self._variables(), f"the answer of {call['name']} of {wanted}",
                              {"vars": dict(self._found or {}), "item_vars": item_values})

    def _evaluate(self, expr: str, data: Any, variables: Mapping[str, Any], on: str, out: dict) -> dict[str, Any]:
        result: dict[str, Any] = {"jq": expr, "on": on}
        try:
            values = _all(_jq(expr, "expression", variables), data, "expression")
        except ReqError as e:
            result["error"] = str(e)
        else:
            result.update(values=self._mask_data(values[:self.EXPR_VALUES]), count=len(values))
        return {**out, "expr": result}

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
        """The requirements of these ids, with their details (the details calls, when there are some)."""
        found = self._get(ids)
        missing = [r for r in found.values() if r is not None and r.id not in self._detailed]
        if self.details and missing:
            self._with_details(missing)
        return {i: (self._detailed.get(i, r) if r is not None else None) for i, r in found.items()}

    def brief(self, ids: Iterable[str]) -> dict[str, Requirement | None]:
        """Without the details calls (the version comes from the list)."""
        return self._get(ids)

    def _with_details(self, reqs: list[Requirement]) -> None:
        """Each requirement with the fields of the details calls (several at once, but in order when traced)."""
        variables = self._variables()
        for call in self.details:                          # compiled once, before the threads
            for f, expr in call["fields"].items():
                self._program(expr, f"{call['name']}: field '{f}'")

        def one(req: Requirement) -> Requirement:
            values = {k: v for k, v in req.as_dict().items() if v not in (None, "", [])}
            local = {**variables, **self._items.get(req.id, {}), "id": req.id}
            for call in self.details:
                if not call["fields"]:                     # no field read from it (yet): not made
                    continue
                answer = self._step_answer(call, local, f"{call['name']} of {req.id}", missing_ok=True,
                                           version=req.version if self.version_given else None)
                if answer is None:
                    continue
                for f, expr in call["fields"].items():
                    where = f"rest backend: {call['name']} of {req.id}: field '{f}'"
                    got = _all(self._program(expr, where), answer, where)
                    value = got[0] if got else None
                    if f == "tags":
                        value = [str(t) for t in (value if isinstance(value, list) else [value] if value not in (None, "") else [])]
                    elif isinstance(value, list):
                        value = ", ".join(str(v) for v in value if v is not None)
                    elif value is not None:
                        value = str(value)
                    if value not in (None, "", []):
                        values[f] = value
            out = Requirement.from_dict(values)
            out.version = req.version                      # from the list: compares the same with or without details
            return out

        if self.trace is not None or len(reqs) == 1:
            done = [one(r) for r in reqs]
        else:
            import contextvars
            from concurrent.futures import ThreadPoolExecutor
            contexts = [contextvars.copy_context() for _ in reqs]      # the progress reported goes on in the pool
            with ThreadPoolExecutor(max_workers=DETAILS_PARALLEL) as pool:
                done = list(pool.map(lambda c, r: c.run(one, r), contexts, reqs))
        for r in done:
            self._detailed[r.id] = r

    def _get(self, ids: Iterable[str]) -> dict[str, Requirement | None]:
        ids = list(ids)
        spec = self.get_spec
        if spec is None or self._all is not None or not ids:
            everything = self._everything()
            return {i: everything.get(i) for i in ids}
        out: dict[str, Requirement | None] = {i: None for i in ids}
        url = str(spec["url"])
        names = {m.group(1) or m.group(2) for m in _VAR.finditer(url)}
        if "ids" in names:
            calls = [self._fill(url, {"ids": ",".join(quote(i, safe="") for i in ids)}, encode=True, raw=["ids"])]
        else:
            calls = [self._fill(url, {"id": i}, encode=True) for i in ids]
        items_program = self._program(spec["items"], "get.items")
        for call in calls:
            answer = self._call(spec, call, missing_ok="ids" not in names, label="get")
            if answer is None:
                continue
            items = _all(items_program, answer, "get.items")
            if self._record is not None:
                self._record["items"] = len(items)
            for item in items:
                req = self.requirement(item)
                if req is not None and req.id in out:
                    out[req.id] = req
        return out


register_req_backend("rest", RestBackend)
