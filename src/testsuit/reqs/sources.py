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

"""The sources file: where the requirements of a tests repository come from.

``requirements.yml``, at the root of the tests repository: the nearest one from the test definition's folder up to the
git root (a repository holding several trees of tests, such as an example, can have one per tree)::

    default: reqs                       # the source of the references without prefix (default: the first one)
    sources:
      reqs: {backend: yaml, options: {file: reqs/requirements.yml}}
      jama:
        backend: jama                   # a backend registered by a plugin
        options: {url: https://jama.example/rest, project: 12}
        credentials: {token: env:JAMA_TOKEN}      # env:<VARIABLE> or file:<path> (a Docker secret), never written here
        vars:                           # variables: {{ name }} in the options ($name in the rest backend's jq)
          project: 12
          version: {value: "{{ sut.version | default('3.2') }}", mandatory: true}

A variable's value can be a Jinja2 expression of the datapack keys (dictionaries, keys set by hand with
``datapack --set``, campaign keys), with a Jinja2 ``default`` filter for where there are none (the Editor's coverage view,
``tsq-reqs``). A mandatory variable must have a value (not empty), and is the context of the requirements: shown by
the Editor and the reports (``jama: version=3.2``). The variables, as resolved, are recorded in the datapack.

A sources file without ``sources`` but with ``requirements`` is itself the one source ``reqs`` of the built-in YAML
backend (the simplest setup: one file).
"""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from testsuit.credentials import CredentialError, is_reference, resolve_secret
from testsuit.reqs.backend import ReqBackend, ReqError, create_req_backend, list_requirements
from testsuit.reqs.model import Node, Ref, Requirement, ref_key, split_ref

SOURCES_FILE = "requirements.yml"
DEFAULT_SOURCE = "reqs"


def is_requirements_file(name: str, data: Any) -> bool:
    """Whether parsed YAML is a sources file (by its name) or a requirements file of the built-in YAML backend: only
    ``nodes`` and ``requirements``, each requirement a mapping (with a path, title or description)."""
    if not isinstance(data, dict):
        return False
    if name == SOURCES_FILE and ({"requirements", "sources"} & set(data)):
        return True
    reqs = data.get("requirements")
    return (bool(reqs) and isinstance(reqs, dict) and not set(data) - {"nodes", "requirements"}
            and all(isinstance(v, dict) for v in reqs.values())
            and any(set(v) & {"path", "title", "description"} for v in reqs.values()))


def repository_root(folder: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=folder, capture_output=True, text=True,
                             timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out.stdout.strip()).resolve() if out.returncode == 0 and out.stdout.strip() else None


def find_sources_file(start: Path) -> Path | None:
    """The sources file of the tests repository holding ``start`` (a file or a folder): the nearest one from its folder
    up to the git root of the repository (outside git, up to the file system root)."""
    folder = (start if start.is_dir() else start.parent).resolve()
    root = repository_root(folder)
    for parent in [folder, *folder.parents]:
        if (parent / SOURCES_FILE).is_file():
            return parent / SOURCES_FILE
        if root is not None and parent == root:
            break
    return None


def _credentials(name: str, spec: Any, base_dir: Path | None = None) -> dict[str, str] | None:
    """The credentials of a source, each read where its value says: ``env:<VARIABLE>`` (an environment variable) or
    ``file:<path>`` (the content of a file, such as a Docker or Kubernetes secret ``/run/secrets/<name>``; a relative
    path is relative to the sources file; trailing white space dropped). A value written in the file is refused."""
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise ReqError(f"source '{name}': 'credentials' is a mapping of names to 'env:<VARIABLE>' or 'file:<path>'")
    out = {}
    for key, value in spec.items():
        text = str(value)
        if not is_reference(text):
            raise ReqError(f"source '{name}': credential '{key}' must come from the environment ('env:<VARIABLE>') "
                           "or a file ('file:<path>', such as a Docker secret), never be written in the sources file")
        try:
            out[str(key)] = resolve_secret(text, base_dir, f"source '{name}': credential '{key}'")
        except CredentialError as e:
            raise ReqError(str(e)) from e
    return out


def var_spec(spec: Any, where: str) -> tuple[Any, bool, Any]:
    """(value, mandatory) of a variable: a value, or ``{value, mandatory}`` (a fallback: Jinja2's ``default``)."""
    if not isinstance(spec, dict):
        return spec, False
    unknown = sorted(set(spec) - {"value", "mandatory"})
    if unknown:
        raise ReqError(f"{where}: unknown key(s) {', '.join(unknown)} (value, mandatory; a fallback goes in the value: "
                       "{{ sut.version | default('3.2') }})")
    return spec.get("value"), bool(spec.get("mandatory"))


def _render_var(value: Any, keys: Mapping[str, Any] | None, where: str) -> Any:
    """A variable's value: a Jinja2 expression of the datapack keys rendered (one expression keeps its type)."""
    if not isinstance(value, str) or "{{" not in value and "{%" not in value:
        return value
    import jinja2
    import jinja2.sandbox
    class Undefined(jinja2.ChainableUndefined, jinja2.StrictUndefined):      # a.b | default(...) when a is not
        pass
    env = jinja2.sandbox.SandboxedEnvironment(undefined=Undefined)
    try:
        single = re.fullmatch(r"\s*\{\{(.*)\}\}\s*", value, re.S)
        if single and "{{" not in single.group(1) and "}}" not in single.group(1):
            result = env.compile_expression(single.group(1), undefined_to_none=False)(**dict(keys or {}))
            if isinstance(result, jinja2.Undefined):
                str(result)
            return result
        return env.from_string(value).render(**dict(keys or {}))
    except jinja2.UndefinedError as e:
        raise ReqError(f"{where}: {e}" + ("" if keys else
                       " (no datapack keys here: give it a default, such as {{ sut.version | default('3.2') }})")) from e
    except jinja2.TemplateError as e:
        raise ReqError(f"{where}: {e}") from e


class Sources:
    """The requirements sources of a sources file, their backends created on first use."""

    def __init__(self, file: Path, keys: Mapping[str, Any] | None = None) -> None:
        """``keys``: the datapack keys the variables of the sources are rendered with (None: none, only defaults)."""
        self.file = file.resolve()
        self.keys = keys
        try:
            data = yaml.safe_load(self.file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as e:
            raise ReqError(f"cannot read the requirements sources file '{self.file}': {e}") from e
        if not isinstance(data, dict):
            raise ReqError(f"'{self.file}': a mapping is expected")
        specs = data.get("sources")
        if specs is None:
            if "requirements" not in data:
                raise ReqError(f"'{self.file}': no 'sources', nor 'requirements'")
            specs = {DEFAULT_SOURCE: {"backend": "yaml", "options": {"file": self.file.name}}}
        if not isinstance(specs, dict) or not specs:
            raise ReqError(f"'{self.file}': 'sources' is a mapping of sources by name")
        self.specs: dict[str, dict] = {}
        for name, spec in specs.items():
            self.specs[str(name)] = self._checked(str(name), spec)
        self.default = str(data.get("default") or next(iter(self.specs)))
        if self.default not in self.specs:
            raise ReqError(f"'{self.file}': default source '{self.default}' is not one of its sources")
        self._backends: dict[str, ReqBackend] = {}
        self._vars: dict[str, dict[str, Any]] = {}

    def _checked(self, name: str, spec: Any) -> dict:
        if not isinstance(spec, dict) or not spec.get("backend"):
            raise ReqError(f"'{self.file}': source '{name}' names its 'backend'")
        unknown = sorted(set(spec) - {"backend", "options", "credentials", "label", "vars"})
        if unknown:
            raise ReqError(f"'{self.file}': source '{name}': unknown key(s) {', '.join(unknown)}")
        if spec.get("vars") is not None and not isinstance(spec["vars"], dict):
            raise ReqError(f"'{self.file}': source '{name}': 'vars' is a mapping of names to values")
        return spec

    def replace(self, name: str, spec: Any) -> None:
        """Use another spec for a source (one being edited, not saved yet: the Editor's tests)."""
        self.specs[name] = self._checked(name, spec)
        self._backends.pop(name, None)
        self._vars.pop(name, None)

    def vars(self, name: str) -> dict[str, Any]:
        """The variables of a source, resolved."""
        if name not in self._vars:
            out = {}
            for key, spec in (self.specs[name].get("vars") or {}).items():
                value, mandatory = var_spec(spec, f"'{self.file}': source '{name}': variable '{key}'")
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
                    raise ReqError(f"'{self.file}': source '{name}': variable '{key}' is not a name (letters, digits, _)")
                value = _render_var(value, self.keys, f"source '{name}': variable '{key}'")
                if mandatory and value in (None, ""):
                    raise ReqError(f"source '{name}': mandatory variable '{key}' has no value")
                out[str(key)] = "" if value is None else value
            self._vars[name] = out
        return self._vars[name]

    def mandatory(self, name: str) -> list[str]:
        """The names of the mandatory variables of a source (those it declares; a backend adds its own)."""
        return [str(k) for k, spec in (self.specs[name].get("vars") or {}).items()
                if isinstance(spec, dict) and spec.get("mandatory")]

    def context(self, name: str) -> dict[str, Any]:
        """The mandatory variables of a source and their values: the context of its requirements."""
        variables = {**self.vars(name), **(self._backends[name].resolved_vars() if name in self._backends else {})}
        names = self.mandatory(name) + (getattr(self._backends.get(name), "mandatory_vars", None) or [])
        return {k: variables[k] for k in dict.fromkeys(names) if k in variables}

    @property
    def names(self) -> list[str]:
        return list(self.specs)

    def backend(self, name: str) -> ReqBackend:
        if name not in self.specs:
            raise ReqError(f"unknown requirements source '{name}' (sources: {', '.join(self.specs)})")
        if name not in self._backends:
            spec = self.specs[name]
            self._backends[name] = create_req_backend(spec["backend"], spec.get("options") or {},
                                                      _credentials(name, spec.get("credentials"), self.file.parent),
                                                      self.file.parent, self.vars(name))
        return self._backends[name]

    def ref(self, text: str) -> Ref:
        return split_ref(text, self.names, self.default)

    def key(self, ref: Ref) -> str:
        return ref_key(ref, self.default)

    def get(self, refs: Iterable[Ref]) -> dict[Ref, Requirement | None]:
        """The requirement of each reference, by source in one call each."""
        by_source: dict[str, list[Ref]] = {}
        for r in refs:
            by_source.setdefault(r.source, []).append(r)
        out: dict[Ref, Requirement | None] = {}
        for source, items in by_source.items():
            found = self.backend(source).get(sorted({r.id for r in items}))
            for r in items:
                req = found.get(r.id)
                if req is not None:
                    req.source = source
                out[r] = req
        return out

    def tree(self, source: str | None = None) -> list[Node]:
        return self.backend(source or self.default).tree()

    def list(self, path: str = "", children: bool = False, source: str | None = None) -> dict[str, Requirement]:
        """The requirements under a path of a source, by key."""
        name = source or self.default
        out = {}
        for req in list_requirements(self.backend(name), path, children):
            req.source = name
            out[self.key(Ref(name, req.id))] = req
        return out

    def record(self) -> list[dict]:
        """What the baseline records of the sources used."""
        out = []
        for name, backend in self._backends.items():
            try:
                revision = backend.revision()
            except Exception:
                revision = None
            try:
                found = backend.resolved_vars()
            except Exception:
                found = {}
            variables = {**self.vars(name), **(found or {})}
            context = self.context(name)
            out.append({"name": name, "backend": self.specs[name]["backend"],
                        **({"context": context} if context else {}),
                        **({"vars": variables} if variables else {}),
                        **({"revision": revision} if revision else {})})
        return out
