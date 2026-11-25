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

"""The issues file: where the issues of a tests repository go, and how they name requirements.

``issues.yml``, at the root of the tests repository (the nearest one from a test definition's folder up to the git
root, as ``requirements.yml``)::

    default: bench                         # where an issue goes, and the source of a reference without prefix
    sources:
      bench:
        backend: gitlab                    # gitlab, github, jira, redmine, or a plugin's
        label: Bench                       # OPTIONAL, shown instead of the name
        options: {url: https://gitlab.example.com, project: test/bench}
        credentials: {token: env:GITLAB_TOKEN}       # env:<VARIABLE> or file:<path>, never written here
        labels: [tsq]                      # OPTIONAL: given to the issues created
        filter: {labels: [bench], not_labels: [wontfix], updated_after: 90d}   # OPTIONAL: the issues it keeps
      firmware: {backend: gitlab, options: {url: https://gitlab.example.com, project: fw/controller},
                 credentials: {token: env:GITLAB_TOKEN}}
      infra: {backend: gitlab, options: {group: lab-infra}, credentials: {token: file:/run/secrets/gitlab}}
      jira: {backend: jira, options: {url: https://acme.atlassian.net, project: BENCH},
             credentials: {email: env:JIRA_EMAIL, token: env:JIRA_TOKEN}}
      hw: {class: corp, label: Hardware, vars: {team: hw}}        # a source of a class: its label and variables
    classes:                               # OPTIONAL: what several sources share, {{ variables }} in it (Jinja2)
      corp: {backend: gitlab, options: {url: https://gitlab.example.com, project: "{{ team }}/bench"},
             credentials: {token: env:GITLAB_TOKEN}, vars: {team: {mandatory: true}}}
    routes:                                # where a new issue goes: the first that matches (globs), else default
      - {source: firmware, step: "firmware:*"}       # on: testdef, step (<phase>:<step>), criteria, check
      - {source: firmware, criteria: "criteria/firmware/*"}
    requirements:                          # how an issue names requirements (a source can have its own)
      labels: "req:{id}"                   # a label per requirement ({id}: its id; false: none)
      text: true                           # the ids of the known requirements found in its title or text
      jira_links: true                     # Jira: the issues it links (requirements kept in Jira)
      add: true                            # the issues created get a label per requirement of what failed

References: ``<source>#<id>`` (``bench#42``, ``jira#BENCH-12``, ``infra#lab/power#3`` for a group source), ``#42`` or
``42`` (the default source), a Jira key alone (the Jira source of its project), or the URL of the issue.
"""
from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from testsuit.credentials import CredentialError, is_reference, resolve_secret
from testsuit.issues.backend import IssueBackend, create_issue_backend
from testsuit.issues.filter import IssueFilter
from testsuit.issues.model import IssueError

ISSUES_FILE = "issues.yml"
OLD_FILE = "tickets.yml"                     # its former name, still read
SNAPSHOT = "issues.yml"                     # in a datapack's definition/ folder
ROUTE_KEYS = ("testdef", "step", "criteria", "check")
REQ_DEFAULTS = {"labels": "req:{id}", "text": True, "jira_links": True, "add": True}


def find_issues_file(start: Path) -> Path | None:
    """The issues file of the tests repository holding ``start``: the nearest from its folder up to its git root."""
    from testsuit.reqs.sources import repository_root
    folder = (start if start.is_dir() else start.parent).resolve()
    root = repository_root(folder)
    for parent in [folder, *folder.parents]:
        for name in (ISSUES_FILE, OLD_FILE):
            if (parent / name).is_file():
                return parent / name
        if root is not None and parent == root:
            break
    return None


def is_issues_file(name: str, data: Any) -> bool:
    return name in (ISSUES_FILE, OLD_FILE) and isinstance(data, dict) and isinstance(data.get("sources"), dict)


SOURCE_KEYS = {"backend", "label", "options", "credentials", "labels", "requirements", "filter", "vars"}
CLASS_KEYS = {"backend", "label", "description", "options", "credentials", "labels", "requirements", "filter", "vars"}


def _var(spec: Any, where: str, declaration: bool = False) -> dict[str, Any]:
    """A variable: a value, or {value, mandatory} (and help, for a class variable)."""
    if not isinstance(spec, dict):
        return {"value": spec, "mandatory": False}
    bad = sorted(set(spec) - ({"value", "mandatory", "help"} if declaration else {"value", "mandatory"}))
    if bad:
        raise IssueError(f"{where}: unknown key(s) {', '.join(bad)} (value, mandatory{', help' if declaration else ''})")
    return {"value": spec.get("value"), "mandatory": bool(spec.get("mandatory"))}


def _render(value: Any, variables: Mapping[str, Any], where: str) -> Any:
    """A value with the variables of its source put in ({{ name }}, Jinja2; one expression keeps its type)."""
    if isinstance(value, dict):
        return {k: _render(v, variables, where) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, variables, where) for v in value]
    if not isinstance(value, str) or ("{{" not in value and "{%" not in value):
        return value
    import jinja2
    import jinja2.sandbox
    env = jinja2.sandbox.SandboxedEnvironment(undefined=jinja2.StrictUndefined)
    try:
        single = re.fullmatch(r"\s*\{\{(.*)\}\}\s*", value, re.S)
        if single and "{{" not in single.group(1) and "}}" not in single.group(1):
            result = env.compile_expression(single.group(1), undefined_to_none=False)(**variables)
            if isinstance(result, jinja2.Undefined):
                str(result)                      # raises: an unknown variable
            return result
        return env.from_string(value).render(**variables)
    except jinja2.UndefinedError as e:
        raise IssueError(f"{where}: {e} (the variables of this source: {', '.join(variables) or 'none'})") from e
    except jinja2.TemplateError as e:
        raise IssueError(f"{where}: {e}") from e


FILTER_SCAN = 5000                           # issues read at most to find those a filter keeps


def _filtered(backend: IssueBackend, f: IssueFilter) -> None:
    """Make a backend's search keep only the issues of its source's filter (its state and labels asked of the tool)."""
    inner = backend.search

    def search(text: str = "", state: str | None = "open", labels=(), limit: int = 100):
        state = f.state_for(state)
        if state is False:
            return []
        asked = list(dict.fromkeys([*labels, *f.labels]))
        found = inner(text=text, state=state, labels=asked, limit=max(limit, FILTER_SCAN) if f.post else limit)
        return [i for i in found if f.match(i)][:limit]
    backend.search = search                      # type: ignore[method-assign]


class IssueSources:
    """The sources of an issues file, their backends created on first use."""

    def __init__(self, data: Any, base_dir: Path | None = None, where: str = ISSUES_FILE) -> None:
        self.where = where
        self.base_dir = base_dir
        if not isinstance(data, dict):
            raise IssueError(f"{where}: a mapping is expected")
        unknown = sorted(set(data) - {"default", "sources", "classes", "routes", "requirements"})
        if unknown:
            raise IssueError(f"{where}: unknown key(s) {', '.join(unknown)} (default, sources, classes, routes, "
                             "requirements)")
        specs = data.get("sources")
        if not isinstance(specs, dict) or not specs:
            raise IssueError(f"{where}: 'sources' is a mapping of sources by name")
        classes = data.get("classes") or {}
        if not isinstance(classes, dict):
            raise IssueError(f"{where}: 'classes' is a mapping of classes by name")
        self.data = data
        self.classes: dict[str, dict] = {str(n): self._class_checked(str(n), c) for n, c in classes.items()}
        self.raw: dict[str, dict] = {}           # the sources as written
        self.specs: dict[str, dict] = {}         # as used: of their class, variables rendered
        self.filters: dict[str, IssueFilter] = {}
        for name, spec in specs.items():
            self._add(str(name), spec)
        self.default = str(data.get("default") or next(iter(self.specs)))
        if self.default not in self.specs:
            raise IssueError(f"{where}: default source '{self.default}' is not one of its sources")
        self.routes = data.get("routes") or []
        if not isinstance(self.routes, list):
            raise IssueError(f"{where}: 'routes' is a list of {{source, testdef|step|criteria|check: <glob>}}")
        for i, route in enumerate(self.routes, 1):
            if not isinstance(route, dict) or str(route.get("source")) not in self.specs:
                raise IssueError(f"{where}: route {i}: 'source' names one of the sources ({', '.join(self.specs)})")
            bad = sorted(set(route) - {"source", *ROUTE_KEYS})
            if bad:
                raise IssueError(f"{where}: route {i}: unknown key(s) {', '.join(bad)} ({', '.join(ROUTE_KEYS)})")
        self._req_spec(data.get("requirements"), "requirements")
        self._backends: dict[str, IssueBackend] = {}

    def _add(self, name: str, spec: Any) -> None:
        where = self.where
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
            raise IssueError(f"{where}: source '{name}': not a name (letters, digits, _ and -)")
        if isinstance(spec, dict) and spec.get("class"):
            used = self._of_class(name, spec)
        else:
            if not isinstance(spec, dict) or not spec.get("backend"):
                raise IssueError(f"{where}: source '{name}' names its 'backend' (or its 'class')")
            bad = sorted(set(spec) - SOURCE_KEYS)
            if bad:
                raise IssueError(f"{where}: source '{name}': unknown key(s) {', '.join(bad)}")
            used = dict(spec)
            if used.get("vars") is not None and not isinstance(used["vars"], dict):
                raise IssueError(f"{where}: source '{name}': 'vars' is a mapping of names to values")
            used["vars"] = {str(k): _var(v, f"{where}: source '{name}': variable '{k}'")
                            for k, v in (spec.get("vars") or {}).items()}
        self._check_credentials(used.get("credentials"), f"source '{name}'")
        used = self._rendered(name, used)
        self._auth(name, used)
        self._req_spec(used.get("requirements"), f"source '{name}'")
        self.filters[name] = IssueFilter(used.get("filter"), f"{where}: source '{name}'")
        self.raw[name] = spec
        self.specs[name] = used

    def _check_credentials(self, creds: Any, what: str) -> None:
        if creds is not None and not isinstance(creds, dict):
            raise IssueError(f"{self.where}: {what}: 'credentials' is a mapping of names to env:<VARIABLE> or file:<path>")
        for key, value in (creds or {}).items():
            if not is_reference(str(value)):
                raise IssueError(f"{self.where}: {what}: credential '{key}' must come from the environment "
                                 "('env:<VARIABLE>') or a file ('file:<path>'), never be written in the file")

    def _class_checked(self, name: str, spec: Any) -> dict:
        where = f"{self.where}: class '{name}'"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
            raise IssueError(f"{where}: not a name (letters, digits, _ and -)")
        if not isinstance(spec, dict) or not spec.get("backend"):
            raise IssueError(f"{where} names its 'backend'")
        bad = sorted(set(spec) - CLASS_KEYS)
        if bad:
            raise IssueError(f"{where}: unknown key(s) {', '.join(bad)}")
        if spec.get("vars") is not None and not isinstance(spec["vars"], dict):
            raise IssueError(f"{where}: 'vars' is a mapping of the class variables by name")
        for key, decl in (spec.get("vars") or {}).items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
                raise IssueError(f"{where}: variable '{key}' is not a name (letters, digits, _)")
            _var(decl, f"{where}: variable '{key}'", declaration=True)
        self._check_credentials(spec.get("credentials"), f"class '{name}'")
        return spec

    def _of_class(self, name: str, spec: dict) -> dict:
        """A source of a class, as used: the class, with the values the source gives to its variables."""
        where = f"{self.where}: source '{name}'"
        cls = str(spec["class"])
        if cls not in self.classes:
            raise IssueError(f"{where}: unknown class '{cls}'" + (f" (classes: {', '.join(self.classes)})"
                                                                  if self.classes else " (no 'classes' in the file)"))
        other = sorted(set(spec) - {"class", "label", "vars"})
        if other:
            raise IssueError(f"{where}: {', '.join(other)}: a source of a class sets only its label and the values of "
                             f"the class variables (to change the rest, detach it from class '{cls}' in the Editor)")
        given = spec.get("vars") or {}
        if not isinstance(given, dict):
            raise IssueError(f"{where}: 'vars' is a mapping of the class variables to their values")
        declared = self.classes[cls].get("vars") or {}
        unknown = sorted(set(map(str, given)) - set(map(str, declared)))
        if unknown:
            raise IssueError(f"{where}: {', '.join(unknown)}: not a variable of class '{cls}'"
                             + (f" (its variables: {', '.join(map(str, declared))})" if declared else " (it has none)"))
        variables = {}
        for key, decl in declared.items():
            d = _var(decl, f"{self.where}: class '{cls}': variable '{key}'", declaration=True)
            value = given.get(key, d["value"])
            if isinstance(value, dict):
                raise IssueError(f"{where}: variable '{key}': a value is expected (mandatory is the class's)")
            variables[str(key)] = {"value": value, "mandatory": d["mandatory"]}
        c = self.classes[cls]
        label = spec.get("label") or c.get("label")
        return {"backend": c["backend"], "class": cls, **({"label": label} if label else {}),
                **{k: c[k] for k in ("options", "credentials", "labels", "requirements", "filter") if c.get(k) is not None},
                "vars": variables}

    def _rendered(self, name: str, spec: dict) -> dict:
        """A source with its variables put in its label, options, credentials, labels and filter ({{ name }})."""
        variables = {}
        for key, v in (spec.get("vars") or {}).items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
                raise IssueError(f"{self.where}: source '{name}': variable '{key}' is not a name (letters, digits, _)")
            if v["mandatory"] and v["value"] in (None, ""):
                raise IssueError(f"{self.where}: source '{name}': mandatory variable '{key}' has no value")
            variables[key] = "" if v["value"] is None else v["value"]
        out = dict(spec)
        out["vars"] = variables
        for key in ("label", "options", "credentials", "labels", "filter"):
            if out.get(key) is not None:
                out[key] = _render(out[key], variables, f"{self.where}: source '{name}': {key}")
        return out

    def vars(self, name: str) -> dict[str, Any]:
        """The variables of a source, with their values."""
        return dict(self.specs[name].get("vars") or {})

    def _auth(self, name: str, spec: dict) -> None:
        """A source signs in one way: its credentials are those of one sign-in method of its backend."""
        from testsuit.issues.backend import _backends
        given = {str(k) for k, v in (spec.get("credentials") or {}).items() if v}
        methods = getattr(_backends.get(str(spec["backend"]).lower()), "auth_methods", None) or {}
        if not given or not methods or any(given <= set(m.get("credentials") or []) for m in methods.values()):
            return
        ways = "; ".join(f"{m.get('label') or k}: {', '.join(m.get('credentials') or [])}" for k, m in methods.items())
        raise IssueError(f"{self.where}: source '{name}': credentials {', '.join(sorted(given))} are not one way to sign "
                          f"in ({ways})")

    @classmethod
    def from_file(cls, path: Path) -> "IssueSources":
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as e:
            raise IssueError(f"cannot read the issues file '{path}': {e}") from e
        return cls(data, path.parent, str(path))

    def _req_spec(self, spec: Any, where: str) -> None:
        if spec is None:
            return
        if not isinstance(spec, dict) or set(spec) - set(REQ_DEFAULTS):
            raise IssueError(f"{self.where}: {where}: 'requirements' takes {', '.join(REQ_DEFAULTS)}")
        if spec.get("labels") not in (None, False) and "{id}" not in str(spec["labels"]):
            raise IssueError(f"{self.where}: {where}: requirements labels: a pattern with {{id}} (req:{{id}}), or false")

    @property
    def names(self) -> list[str]:
        return list(self.specs)

    def label(self, name: str) -> str:
        return str(self.specs[name].get("label") or name)

    def req_rules(self, name: str) -> dict[str, Any]:
        """How the issues of a source name requirements (REQ_DEFAULTS, the file's, the source's)."""
        return {**REQ_DEFAULTS, **(self.data.get("requirements") or {}), **(self.specs[name].get("requirements") or {})}

    def credentials(self, name: str) -> dict[str, str]:
        out = {}
        for key, value in (self.specs[name].get("credentials") or {}).items():
            try:
                out[str(key)] = resolve_secret(str(value), self.base_dir, f"issue source '{name}': credential '{key}'")
            except CredentialError as e:
                raise IssueError(str(e)) from e
        return out

    def backend(self, name: str) -> IssueBackend:
        if name not in self.specs:
            raise IssueError(f"unknown issue source '{name}' (sources: {', '.join(self.specs)})")
        if name not in self._backends:
            spec = self.specs[name]
            backend = create_issue_backend(str(spec["backend"]), spec.get("options") or {}, self.credentials(name),
                                            self.base_dir)
            backend.name = name
            if self.filters[name]:
                _filtered(backend, self.filters[name])
            self._backends[name] = backend
        return self._backends[name]

    def card(self, name: str) -> dict[str, Any]:
        """A source as the UI shows it: its backend, project, label, whether default, and what it can do."""
        spec = self.specs[name]
        options = spec.get("options") or {}
        where = next((str(options[k]) for k in ("project", "repo", "group", "org") if options.get(k)), "")
        from testsuit.issues.backend import _backends
        factory = _backends.get(str(spec["backend"]).lower())
        return {"name": name, "backend": str(spec["backend"]), "project": where,
                **({"class": str(spec["class"])} if spec.get("class") else {}),
                **({"vars": spec["vars"]} if spec.get("vars") else {}),
                **({"filter": self.filters[name].spec} if self.filters[name] else {}),
                **({"label": str(spec["label"])} if spec.get("label") else {}),
                **({"url": str(options["url"])} if options.get("url") else {}),
                **({"default": True} if name == self.default else {}),
                "group": bool(options.get("group") or options.get("org")),
                "capabilities": sorted(getattr(factory, "capabilities", ()) or ()) if factory else [],
                **({"error": f"unknown backend '{spec['backend']}'"} if factory is None else {})}

    def route(self, target: Mapping[str, Any]) -> str:
        """The source a new issue about a target goes to (routes: the first that matches, else default)."""
        for route in self.routes:
            if all(fnmatch.fnmatchcase(str(target.get(k) or ""), str(route[k])) for k in ROUTE_KEYS if k in route):
                return str(route["source"])
        return self.default

    def parse(self, text: str) -> tuple[str, str]:
        """(source, id) of a reference or a URL."""
        text = str(text or "").strip()
        if not text:
            raise IssueError("no issue given")
        if re.match(r"https?://", text):
            for name in self.specs:
                try:
                    found = self.backend(name).id_of_url(text)
                except IssueError:
                    found = None
                if found:
                    return name, found
            raise IssueError(f"{text}: not an issue of an issue source ({', '.join(self.specs)})")
        prefix, sep, rest = text.partition("#")
        if sep and prefix in self.specs and rest:
            return prefix, rest
        if sep and not prefix and rest:
            return self.default, rest
        m = re.fullmatch(r"([A-Z][A-Z0-9_]+)-\d+", text)
        if m:
            for name, spec in self.specs.items():
                if str(spec["backend"]).lower() == "jira" and str((spec.get("options") or {}).get("project")) == m.group(1):
                    return name, text
        if sep and prefix not in self.specs and "/" not in prefix:
            raise IssueError(f"{text}: unknown issue source '{prefix}' (sources: {', '.join(self.specs)})")
        return self.default, text

    def snapshot(self) -> dict[str, Any]:
        """What a datapack keeps of the file (definition/issues.yml): as written, credentials as references."""
        return self.data
