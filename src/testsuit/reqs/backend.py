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

"""The plugin API of requirements tools, and the built-in YAML backend.

A backend gives access to the requirements of one tool. It is created by a factory
``factory(options, credentials, base_dir) -> ReqBackend``:

- ``options``: the ``options`` of its entry in the sources file (a URL, a project...);
- ``credentials``: ``{name: value}`` read from the environment or a file (``token: env:JAMA_TOKEN``, ``token: file:/run/secrets/jama``), or None;
- ``base_dir``: the folder of the sources file, for relative paths;
- ``vars`` (keyword, only if the factory takes it): the variables of the source, resolved. A factory without it gets its options with ``{{ name }}`` replaced by their values.

Example, from an external library::

    from testsuit.reqs import ReqBackend, Requirement, Node, register_req_backend

    class JamaBackend(ReqBackend):
        capabilities = frozenset({"children_query"})
        description = "Jama Connect, through its REST API"            # shown by the Editor's form
        options_help = {"url": "The REST API of the server.", "project": "The project id."}
        credentials_help = {"token": "An API token of a reader of the project."}
        def __init__(self, options, credentials, base_dir): ...
        def tree(self) -> list[Node]: ...                       # GET /folders
        def list(self, path, children=False) -> list[Requirement]: ...
        def get(self, ids) -> dict[str, Requirement | None]: ...

    register_req_backend("jama", JamaBackend)                   # sources: {jama: {backend: jama, ...}}

See testsuit.plugins to make the registration visible to the command-line tools.

A backend is read-only unless it declares the ``write`` capability and implements ``create`` and ``update``.
Without ``children_query``, ``list(path, children=True)`` is answered from ``tree()`` and ``list(node)``.
"""
from __future__ import annotations

from testsuit.plugins import note_registration

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from testsuit.reqs.model import Node, Requirement

CAPABILITIES = ("children_query", "write", "versions")


class ReqError(Exception):
    """A requirements source cannot be read, or is not configured right."""


class ReqBackend:
    """Access to the requirements of one tool. Read-only by default."""

    capabilities: frozenset[str] = frozenset()
    # one line on the tool it reads, shown in the Editor's form (default: the first paragraph of the class docstring)
    description: str = ""
    # the options of its entry in the sources file, by name, with their help: proposed by the Editor's form
    options_help: dict[str, str] = {}
    # the credentials it reads, by name, with their help (their values: 'env:<VARIABLE>' or 'file:<path>')
    credentials_help: dict[str, str] = {}
    # set to a list by the Editor's tests: a backend calling a server appends a description of each call to it
    # ({call, method, url, headers, status, answer, error...}, secrets masked), shown to help configure the source
    trace: list[dict] | None = None

    def __init__(self, options: dict[str, Any], credentials: dict[str, str] | None, base_dir: Path) -> None:
        self.options = dict(options or {})
        self.credentials = credentials
        self.base_dir = base_dir

    def tree(self) -> list[Node]:
        """The top nodes of the tree of the requirements (paths are their ids joined by '/')."""
        raise NotImplementedError

    def list(self, path: str = "", children: bool = False) -> list[Requirement]:
        """The requirements of a node (all of them for path ''), and of its descendants with children."""
        raise NotImplementedError

    def get(self, ids: Iterable[str]) -> dict[str, Requirement | None]:
        """The requirements of these ids; None for an id the tool does not know."""
        raise NotImplementedError

    def brief(self, ids: Iterable[str]) -> dict[str, Requirement | None]:
        """The requirements of these ids, possibly without the fields that cost more calls, but with their version:
        the coverage's check of the current versions. Default: get()."""
        return self.get(ids)

    def revision(self) -> str | None:
        """A revision of the whole source, recorded in the baseline (None: none)."""
        return None

    def resolved_vars(self) -> dict[str, Any]:
        """Variables the backend found itself (the rest backend's steps), recorded in the baseline and shown by the
        Editor's Test connection."""
        return {}

    def create(self, req: Requirement) -> Requirement:
        raise ReqError(f"{type(self).__name__} is read-only")

    def update(self, req: Requirement) -> Requirement:
        raise ReqError(f"{type(self).__name__} is read-only")


BackendFactory = Callable[[dict, "dict | None", Path], ReqBackend]

_backends: dict[str, BackendFactory] = {}


def register_req_backend(name: str, factory: BackendFactory) -> None:
    """Declare a requirements backend, usable as ``backend: <name>`` in the sources file (case-insensitive;
    registering a name again replaces it, the built-in one included)."""
    _backends[name.lower()] = factory
    note_registration("requirements backends", name.lower())


def unregister_req_backend(name: str) -> None:
    _backends.pop(name.lower(), None)


def get_req_backends() -> list[str]:
    return list(_backends)


def describe_req_backends() -> list[dict[str, Any]]:
    """The backends registered, with what the Editor's form proposes: capabilities, options and credentials (each
    with its help), read from the factory when it declares them (a ReqBackend class does)."""
    out = []
    for name, factory in sorted(_backends.items()):
        doc = (factory.__doc__ or "").strip().split("\n\n")[0] if isinstance(factory, type) else ""
        out.append({"name": name, "description": str(getattr(factory, "description", "") or " ".join(doc.split()).rstrip(":")),
                    "capabilities": sorted(getattr(factory, "capabilities", ()) or ()),
                    "options": dict(getattr(factory, "options_help", {}) or {}),
                    "credentials": dict(getattr(factory, "credentials_help", {}) or {})})
    return out


def accepts_vars(factory: Any) -> bool:
    """Whether a factory takes the variables of its source itself (a ``vars`` keyword argument)."""
    import inspect
    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return False
    return "vars" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def fill_vars(data: Any, vars: Mapping[str, Any]) -> Any:
    """``{{ name }}`` replaced by the variable's value in every string of ``data`` (others left as written)."""
    if isinstance(data, str):
        return _VAR.sub(lambda m: str(vars[m.group(1)]) if m.group(1) in vars else m.group(0), data)
    if isinstance(data, dict):
        return {k: fill_vars(v, vars) for k, v in data.items()}
    if isinstance(data, list):
        return [fill_vars(v, vars) for v in data]
    return data


def create_req_backend(name: str, options: dict, credentials: dict | None, base_dir: Path,
                       vars: Mapping[str, Any] | None = None) -> ReqBackend:
    """The backend of a source. Its variables go to a factory taking a ``vars`` keyword argument; for another,
    ``{{ name }}`` in its options is replaced by their values."""
    factory = _backends.get(str(name).lower())
    if factory is None:
        raise ReqError(f"unknown requirements backend '{name}' (known: {', '.join(sorted(_backends)) or 'none'}); "
                       "a plugin registers it with testsuit.reqs.register_req_backend()")
    vars = dict(vars or {})
    if accepts_vars(factory):
        return factory(dict(options or {}), credentials, base_dir, vars=vars)
    return factory(fill_vars(dict(options or {}), vars), credentials, base_dir)


def under(path: str, node: str, children: bool) -> bool:
    """Whether a requirement path is the node (or under it, with children). Node '' is the root."""
    path, node = path.strip("/"), node.strip("/")
    if not node:
        return children or not path
    return path == node or (children and path.startswith(node + "/"))


def walk(nodes: list[Node], prefix: str = "") -> Iterable[str]:
    """Every node path of a tree."""
    for n in nodes:
        path = f"{prefix}/{n.id}" if prefix else n.id
        yield path
        yield from walk(n.children, path)


def list_requirements(backend: ReqBackend, path: str = "", children: bool = False) -> list[Requirement]:
    """``backend.list``, emulated through the tree for a backend without the ``children_query`` capability."""
    if not children or "children_query" in backend.capabilities:
        return backend.list(path, children)
    found = {r.id: r for r in backend.list(path, False)}
    for node in walk(backend.tree()):
        if node != path.strip("/") and under(node, path, True):
            for r in backend.list(node, False):
                found.setdefault(r.id, r)
    return list(found.values())


# ── built-in: a YAML file ─────────────────────────────────────────────────

class YamlBackend(ReqBackend):
    """Requirements written in a YAML file of the tests repository (option ``file``, relative to the sources file;
    default: the sources file itself)::

        nodes:                          # OPTIONAL labels; nodes are implied by the paths
          SYS: System
          SYS/BRAKE: Braking
        requirements:
          REQ-12:
            path: SYS/BRAKE
            title: Stopping distance
            description: The train stops within 30 m from 10 m/s.
            rationale: Safety margin of the track.
            tags: [critical]
            status: approved            # 'deleted' and 'rejected' make links to it problems
            version: 3                  # OPTIONAL: default, a hash of the content

    Option ``url``: a link to each requirement, ``{id}`` replaced (``https://wiki/reqs#{id}``).
    """
    remote = False                       # nothing to keep for when a server is down


    capabilities = frozenset({"children_query", "versions"})
    description = "Built in: requirements written in a YAML file of the tests repository."
    options_help = {
        "file": "The YAML file of the requirements, relative to the sources file (default: the sources file itself).",
        "url": "A link to each requirement in a wiki or another tool, `{id}` replaced by its id "
               "(`https://wiki/reqs#{id}`).",
    }

    def __init__(self, options: dict[str, Any], credentials: dict[str, str] | None, base_dir: Path) -> None:
        super().__init__(options, credentials, base_dir)
        self.file = (base_dir / str(self.options.get("file") or "requirements.yml")).resolve()
        try:
            data = yaml.safe_load(self.file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as e:
            raise ReqError(f"cannot read the requirements file '{self.file}': {e}") from e
        if not isinstance(data, dict) or not isinstance(data.get("requirements") or {}, dict):
            raise ReqError(f"'{self.file}': 'requirements' is a mapping of requirements by id")
        self.labels = {str(k).strip("/"): str(v) for k, v in (data.get("nodes") or {}).items()}
        self.reqs: dict[str, Requirement] = {}
        for rid, body in (data.get("requirements") or {}).items():
            if not isinstance(body, dict):
                body = {"description": "" if body is None else str(body)}
            unknown = sorted(set(body) - set(Requirement.__dataclass_fields__) - {"source"})
            if unknown:
                raise ReqError(f"'{self.file}': requirement '{rid}': unknown field(s) {', '.join(unknown)}")
            req = Requirement.from_dict({**body, "id": str(rid)})
            req.path = str(req.path or "").strip("/")
            if not req.version:
                content = json.dumps({k: v for k, v in req.as_dict().items() if k not in ("version", "url")}, sort_keys=True)
                req.version = hashlib.sha256(content.encode()).hexdigest()[:8]
            if not req.url and self.options.get("url"):
                req.url = str(self.options["url"]).replace("{id}", req.id)
            self.reqs[req.id] = req

    def tree(self) -> list[Node]:
        paths = set(self.labels)
        for r in self.reqs.values():
            parts = [p for p in r.path.split("/") if p]
            paths.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        roots: list[Node] = []
        index: dict[str, Node] = {}
        for path in sorted(paths, key=lambda p: (p.count("/"), p)):
            parent, _, leaf = path.rpartition("/")
            node = Node(leaf, self.labels.get(path, ""))
            index[path] = node
            (index[parent].children if parent in index else roots).append(node)
        return roots

    def list(self, path: str = "", children: bool = False) -> list[Requirement]:
        return [r for r in self.reqs.values() if under(r.path, path, children)]

    def get(self, ids: Iterable[str]) -> dict[str, Requirement | None]:
        return {i: self.reqs.get(i) for i in ids}

    def revision(self) -> str | None:
        return hashlib.sha256(self.file.read_bytes()).hexdigest()[:12]


register_req_backend("yaml", YamlBackend)
