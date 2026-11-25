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

A class is a source written once and used by several sources, each giving only the values of its variables (the same
requirements tool, for another subsystem)::

    classes:
      alm:
        backend: rest
        options: {base: https://alm.example/api, list: {url: "systems/{{ subsystem }}/items", items: ".data[]"}}
        credentials: {token: "env:ALM_TOKEN_{{ subsystem }}"}   # credentials can use the class variables
        vars:                           # declared here, given by each source: {mandatory, value (a default), help}
          subsystem: {mandatory: true, help: The subsystem in the ALM}
    sources:
      brakes: {class: alm, vars: {subsystem: BRAKE}}
      speed: {class: alm, label: Speed, vars: {subsystem: SPEED}}

A source of a class sets only its ``label`` and the values of the class variables: nothing else of the class is
overridden (the Editor's Detach turns it into a source of its own, a copy of the class).

Dynamic tags (``dynamic_tags``, on a source or a class): tags given to the requirements whose title, description,
rationale or comments hold one of their words or phrases (whole words, case independent; spaces match any white
space)::

    dynamic_tags:
      Maturity Warning: [TBC, TBD, to be defined, to be completed]
      Open question: {words: [TBC, open point], color: "#c0392b"}     # with the color it is shown in

They are found when the requirements are read, after their version (a rule changed does not change the versions).

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


def _credentials(name: str, spec: Any, base_dir: Path | None = None,
                 variables: Mapping[str, Any] | None = None) -> dict[str, str] | None:
    """The credentials of a source, each read where its value says: ``env:<VARIABLE>`` (an environment variable) or
    ``file:<path>`` (the content of a file, such as a Docker or Kubernetes secret ``/run/secrets/<name>``; a relative
    path is relative to the sources file; trailing white space dropped). A value written in the file is refused. A
    reference can use the variables of the source (``env:ALM_TOKEN_{{ subsystem }}``)."""
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise ReqError(f"source '{name}': 'credentials' is a mapping of names to 'env:<VARIABLE>' or 'file:<path>'")
    out = {}
    for key, value in spec.items():
        text = str(value)
        if is_reference(text) and "{{" in text:
            text = str(_render_var(text, variables, f"source '{name}': credential '{key}'"))
        if not is_reference(text):
            raise ReqError(f"source '{name}': credential '{key}' must come from the environment ('env:<VARIABLE>') "
                           "or a file ('file:<path>', such as a Docker secret), never be written in the sources file")
        try:
            out[str(key)] = resolve_secret(text, base_dir, f"source '{name}': credential '{key}'")
        except CredentialError as e:
            raise ReqError(str(e)) from e
    return out


def var_spec(spec: Any, where: str) -> tuple[Any, bool, Any]:
    """(value, mandatory) of a variable: a value, or ``{value, mandatory}`` (a fallback: Jinja2's ``default``); a
    class variable has a ``help`` too."""
    if not isinstance(spec, dict):
        return spec, False
    unknown = sorted(set(spec) - {"value", "mandatory", "help"})
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


def _tag_rules(spec: Any, where: str) -> list[tuple[str, re.Pattern]]:
    """The dynamic tags of a source: (tag, the pattern of its words), each word or phrase whole, case independent,
    a space matching any white space."""
    if spec is None:
        return []
    if not isinstance(spec, dict):
        raise ReqError(f"{where}: 'dynamic_tags' is a mapping of tags to the words that give them")
    rules = []
    for tag, words in spec.items():
        if isinstance(words, dict):                   # {words, color}
            unknown = sorted(set(words) - {"words", "color"})
            if unknown:
                raise ReqError(f"{where}: dynamic tag '{tag}': unknown key(s) {', '.join(unknown)} (words, color)")
            color = words.get("color")
            if color is not None and not re.fullmatch(r"#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?", str(color)):
                raise ReqError(f"{where}: dynamic tag '{tag}': color '{color}' is not #rgb or #rrggbb")
            words = words.get("words") or []
        if isinstance(words, str):
            words = [w for w in words.split(",")]
        if not isinstance(words, list):
            raise ReqError(f"{where}: dynamic tag '{tag}': a list of words or phrases is expected")
        words = [str(w).strip() for w in words if str(w or "").strip()]
        if not words:
            continue
        alternatives = "|".join(r"\s+".join(re.escape(part) for part in w.split()) for w in words)
        rules.append((str(tag), re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)))
    return rules


def _plain(text: str) -> str:
    """A long text as words: HTML tags and escaped line breaks (\\n) as spaces."""
    return re.sub(r"<[^>]+>|\\[rn]", " ", text) if text else ""


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
            specs = {DEFAULT_SOURCE: {"backend": "yaml", "options": {"file": self.file.name},
                                      **({"label": str(data["label"])} if data.get("label") else {})}}
        if not isinstance(specs, dict) or not specs:
            raise ReqError(f"'{self.file}': 'sources' is a mapping of sources by name")
        classes = data.get("classes") or {}
        if not isinstance(classes, dict):
            raise ReqError(f"'{self.file}': 'classes' is a mapping of classes by name")
        self.classes: dict[str, dict] = {}
        for name, spec in classes.items():
            self.classes[str(name)] = self._class_checked(str(name), spec)
        self.raw: dict[str, Any] = {}          # each source as written (a source of a class: its class and values)
        self.specs: dict[str, dict] = {}       # each source as used (a source of a class: the class, with its values)
        for name, spec in specs.items():
            self.raw[str(name)] = spec
            self.specs[str(name)] = self._checked(str(name), spec)
        self.default = str(data.get("default") or next(iter(self.specs)))
        if self.default not in self.specs:
            raise ReqError(f"'{self.file}': default source '{self.default}' is not one of its sources")
        self._backends: dict[str, ReqBackend] = {}
        self._vars: dict[str, dict[str, Any]] = {}
        self._rules: dict[str, list[tuple[str, re.Pattern]]] = {}

    def _class_checked(self, name: str, spec: Any) -> dict:
        where = f"'{self.file}': class '{name}'"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
            raise ReqError(f"{where}: not a name (letters, digits, _ and -)")
        if not isinstance(spec, dict) or not spec.get("backend"):
            raise ReqError(f"{where} names its 'backend'")
        unknown = sorted(set(spec) - {"backend", "options", "credentials", "label", "vars", "description", "dynamic_tags"})
        if unknown:
            raise ReqError(f"{where}: unknown key(s) {', '.join(unknown)}")
        if spec.get("vars") is not None and not isinstance(spec["vars"], dict):
            raise ReqError(f"{where}: 'vars' is a mapping of the class variables by name")
        _tag_rules(spec.get("dynamic_tags"), where)
        for key, decl in (spec.get("vars") or {}).items():
            var_spec(decl, f"{where}: variable '{key}'")
        return spec

    def _of_class(self, name: str, spec: dict) -> dict:
        """A source of a class, as used: the class, with the values the source gives to its variables."""
        where = f"'{self.file}': source '{name}'"
        cls = str(spec["class"])
        if cls not in self.classes:
            raise ReqError(f"{where}: unknown class '{cls}'" + (f" (classes: {', '.join(self.classes)})" if self.classes
                                                                else " (no 'classes' in the file)"))
        other = sorted(set(spec) - {"class", "label", "vars"})
        if other:
            raise ReqError(f"{where}: {', '.join(other)}: a source of a class sets only its label and the values of the "
                           f"class variables (to change the rest, detach it from class '{cls}' in the Editor)")
        given = spec.get("vars") or {}
        if not isinstance(given, dict):
            raise ReqError(f"{where}: 'vars' is a mapping of the class variables to their values")
        declared = self.classes[cls].get("vars") or {}
        unknown = sorted(set(map(str, given)) - set(map(str, declared)))
        if unknown:
            raise ReqError(f"{where}: {', '.join(unknown)}: not a variable of class '{cls}'"
                           + (f" (its variables: {', '.join(map(str, declared))})" if declared else " (it has none)"))
        variables = {}
        for key, decl in declared.items():
            default, mandatory = var_spec(decl, f"'{self.file}': class '{cls}': variable '{key}'")
            value = given.get(key, default)
            if isinstance(value, dict):          # a value of a source is a value: no qualifiers
                raise ReqError(f"{where}: variable '{key}': a value is expected (mandatory is the class's)")
            variables[str(key)] = {"value": value, "mandatory": mandatory}
        c = self.classes[cls]
        label = spec.get("label") or c.get("label")
        return {"backend": c["backend"], "class": cls, **({"label": label} if label else {}),
                **{k: c[k] for k in ("options", "credentials", "dynamic_tags") if c.get(k) is not None},
                **({"vars": variables} if variables else {})}

    def tagged(self, name: str, reqs: Iterable[Requirement]) -> None:
        """Give the requirements of a source its dynamic tags (those their texts match)."""
        if name not in self._rules:
            self._rules[name] = _tag_rules(self.specs[name].get("dynamic_tags"), f"source '{name}'")
        rules = self._rules[name]
        if not rules:
            return
        for req in reqs:
            if req is None:
                continue
            text = "\n".join(_plain(str(t or "")) for t in (req.title, req.description, req.rationale, req.comments))
            req.dynamic_tags = [tag for tag, pattern in rules if pattern.search(text)]

    def tag_colors(self) -> dict[str, str]:
        """The color of each dynamic tag that has one, of every source (the first one given for a tag)."""
        out: dict[str, str] = {}
        for spec in self.specs.values():
            for tag, rule in (spec.get("dynamic_tags") or {}).items():
                if isinstance(rule, dict) and rule.get("color"):
                    out.setdefault(str(tag), str(rule["color"]))
        return out

    def class_version(self, name: str) -> str:
        """A short hash of a class as written: which version of it a source used (recorded in the datapack)."""
        import hashlib
        import json
        return hashlib.sha256(json.dumps(self.classes[name], sort_keys=True, default=str).encode()).hexdigest()[:8]

    def _checked(self, name: str, spec: Any) -> dict:
        if isinstance(spec, dict) and spec.get("class"):
            return self._of_class(name, spec)
        if not isinstance(spec, dict) or not spec.get("backend"):
            raise ReqError(f"'{self.file}': source '{name}' names its 'backend' (or its 'class')")
        unknown = sorted(set(spec) - {"backend", "options", "credentials", "label", "vars", "dynamic_tags"})
        if unknown:
            raise ReqError(f"'{self.file}': source '{name}': unknown key(s) {', '.join(unknown)}")
        if spec.get("vars") is not None and not isinstance(spec["vars"], dict):
            raise ReqError(f"'{self.file}': source '{name}': 'vars' is a mapping of names to values")
        _tag_rules(spec.get("dynamic_tags"), f"'{self.file}': source '{name}'")
        return spec

    def replace(self, name: str, spec: Any) -> None:
        """Use another spec for a source (one being edited, not saved yet: the Editor's tests)."""
        self.raw[name] = spec
        self.specs[name] = self._checked(name, spec)
        self._backends.pop(name, None)
        self._vars.pop(name, None)
        self._rules.pop(name, None)

    def replace_class(self, name: str, spec: Any) -> None:
        """Use another spec for a class (one being edited), and for the sources of it."""
        self.classes[name] = self._class_checked(name, spec)
        for source, raw in self.raw.items():
            if isinstance(raw, dict) and str(raw.get("class") or "") == name:
                self.replace(source, raw)

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
                                                      _credentials(name, spec.get("credentials"), self.file.parent,
                                                                   self.vars(name)),
                                                      self.file.parent, self.vars(name))
        return self._backends[name]

    def ref(self, text: str) -> Ref:
        return split_ref(text, self.names, self.default)

    def key(self, ref: Ref) -> str:
        return ref_key(ref, self.default)

    def get(self, refs: Iterable[Ref], brief: bool = False) -> dict[Ref, Requirement | None]:
        """The requirement of each reference, by source in one call each. ``brief``: perhaps without the fields that
        cost more calls (the rest backend's details), with the versions (ReqBackend.brief)."""
        by_source: dict[str, list[Ref]] = {}
        for r in refs:
            by_source.setdefault(r.source, []).append(r)
        out: dict[Ref, Requirement | None] = {}
        for source, items in by_source.items():
            backend = self.backend(source)
            ids = sorted({r.id for r in items})
            found = backend.brief(ids) if brief else backend.get(ids)
            self.tagged(source, [r for r in found.values() if r is not None])
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
        reqs = list_requirements(self.backend(name), path, children)
        self.tagged(name, reqs)
        for req in reqs:
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
            cls = self.specs[name].get("class")
            out.append({"name": name, "backend": self.specs[name]["backend"],
                        **({"class": cls, "class_version": self.class_version(cls)} if cls else {}),
                        **({"context": context} if context else {}),
                        **({"vars": variables} if variables else {}),
                        **({"revision": revision} if revision else {})})
        return out
