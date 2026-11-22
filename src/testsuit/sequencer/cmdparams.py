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

"""Parameters of a lab command, and the keys a pipeline step gives an exploit step (README sections 7.1, 8.3).

- A **role** declares the parameters of each of its commands, by name, with what each is for (no value).
- A **component class** (or a stage, or a host) gives their default values where it implements the command:
  ``params:`` in the command, merged along the classes it extends (the last one wins).
- A ``do:`` **step** sets them with ``with:`` (its values are templates, rendered with the step's keys at run
  time: run-time keys work). The command reads them as ``params.*``, and only them: a step never overrides a
  plain key of the context for a command. A parameter with neither a default nor a value is an error.
- An ``exploit:`` **step** gives keys its criteria read with ``with:``: ``params.<name>`` keys (as commands
  do), or plain keys the context does not define (a warning: ``params.*`` keeps them consistent with the
  commands). A plain key that the context defines is refused: the criteria would not read the test's value.
"""
from __future__ import annotations

from typing import Any, Mapping

import jinja2
from jinja2 import nodes

from testsuit.sequencer.util import SequencerError

PARAMS = "params"                    # the key of a command's parameters, in its spec and in its templates


def declared_params(value: Any, where: str) -> dict[str, str]:
    """The parameters a role declares for a command: a mapping name -> what it is for, or a list of names."""
    if value is None:
        return {}
    if isinstance(value, list):
        value = {str(v): "" for v in value}
    if not isinstance(value, dict):
        raise SequencerError(f"{where}: 'params' lists the parameter names, each with what it is for")
    out = {}
    for name, text in value.items():
        name = str(name)
        if not name.isidentifier():
            raise SequencerError(f"{where}: parameter '{name}' must be a plain name (letters, digits, _)")
        if isinstance(text, dict):
            if set(text) - {"description"}:
                raise SequencerError(f"{where}: parameter '{name}': a role declares it with its description only "
                                     "(its default value goes in the component classes, with the command)")
            text = text.get("description")
        out[name] = str(text or "")
    return out


def default_params(spec: Any, where: str) -> dict[str, Any]:
    """The default values a command implementation gives its parameters (its ``params:`` mapping)."""
    value = spec.get(PARAMS) if isinstance(spec, dict) else None
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(k, str) and k.isidentifier() for k in value):
        raise SequencerError(f"{where}: 'params' gives the default value of each parameter, by name")
    return dict(value)


def without_params(spec: Any) -> Any:
    """A command spec without its default parameter values: what runs."""
    if isinstance(spec, dict) and PARAMS in spec:
        return {k: v for k, v in spec.items() if k != PARAMS}
    return spec


def params_read(value: Any) -> list[str]:
    """The ``params.<name>`` (or ``params['name']``) the templates of a value read (strings, lists, dicts)."""
    found: list[str] = []
    if isinstance(value, str):
        if "{{" in value or "{%" in value:
            try:
                tree = jinja2.Environment().parse(value)
            except jinja2.TemplateSyntaxError:
                return []
            for node in tree.find_all((nodes.Getattr, nodes.Getitem)):
                if isinstance(node.node, nodes.Name) and node.node.name == PARAMS:
                    if isinstance(node, nodes.Getattr):
                        found.append(node.attr)
                    elif isinstance(node.arg, nodes.Const) and isinstance(node.arg.value, str):
                        found.append(node.arg.value)
    elif isinstance(value, dict):
        for item in value.values():
            found += params_read(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found += params_read(item)
    return list(dict.fromkeys(found))


def undeclared_reads(spec: Any, declared: Mapping[str, str]) -> list[str]:
    """The parameters a command implementation reads (its templates) or gives a default to, that its role does
    not declare."""
    run = without_params(spec)
    if isinstance(run, dict):
        run = {k: v for k, v in run.items() if k != "comments"}
    defaults = spec.get(PARAMS) if isinstance(spec, dict) else None
    names = params_read(run) + (list(defaults) if isinstance(defaults, dict) else [])
    return [n for n in dict.fromkeys(names) if n not in declared]


def step_param_problems(where: str, command: str, given: Mapping[str, Any], declared: Mapping[str, str],
                        defaults: Mapping[str, Any] | None = None) -> list[str]:
    """What is wrong with the parameters a ``do:`` step gives a command: names its role does not declare (a
    plain key is never overridden for a command), and, with the defaults of the bound component, those that
    get no value."""
    problems = []
    for name in given:
        if name not in declared:
            known = f" (it declares: {', '.join(declared)})" if declared else " (it declares none)"
            hint = "; a command reads its parameters only, never a plain key set by a step" if "." in str(name) else ""
            problems.append(f"{where}: command '{command}' has no parameter '{name}'{known}{hint}")
    if defaults is not None:
        missing = [n for n in declared if n not in given and n not in defaults]
        if missing:
            problems.append(f"{where}: command '{command}' needs a value for {', '.join(repr(n) for n in missing)}: "
                            "no default in the component class, give it with 'with:'")
    return problems


def command_params(where: str, command: str, given: Mapping[str, Any], declared: Mapping[str, str],
                   defaults: Mapping[str, Any], keys: dict) -> dict[str, Any]:
    """The values of a command's parameters for one run of a step: the defaults of its implementation, then the
    step's ``with:``, each rendered with the step's keys (``render``). Raises on a problem."""
    from testsuit.sequencer.util import render
    problems = step_param_problems(where, command, given, declared, defaults)
    if problems:
        raise SequencerError("; ".join(problems))
    values = {}
    for name in declared:
        raw = given[name] if name in given else defaults[name]
        values[name] = render(raw, keys, f"{where}: parameter '{name}'")
    for name, raw in defaults.items():                 # a default the role does not declare: refused by the lab check
        values.setdefault(name, render(raw, keys, f"{where}: parameter '{name}'"))
    return values


# ── exploit steps ──

def exploit_with(given: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The keys an exploit step gives, as (params by name, plain keys by dotted name)."""
    params, plain = {}, {}
    for name, value in given.items():
        name = str(name)
        if name.startswith(PARAMS + ".") and len(name) > len(PARAMS) + 1:
            params[name[len(PARAMS) + 1:]] = value
        elif name == PARAMS and isinstance(value, dict):
            params.update({str(k): v for k, v in value.items()})
        else:
            plain[name] = value
    return params, plain


def _defined(keys: Mapping, dotted: str) -> bool:
    node: Any = keys
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False
        node = node[part]
    return True


def exploit_with_problems(where: str, given: Mapping[str, Any], context: Mapping | None) -> tuple[list[str], list[str]]:
    """(errors, warnings) of the keys an exploit step gives: a plain key the context defines is an error (it
    would hide the test's value), any plain key a warning (``params.*`` keeps them consistent with commands)."""
    errors, warnings = [], []
    _, plain = exploit_with(given)
    for name in plain:
        if context is not None and _defined(context, name):
            errors.append(f"{where}: 'with' key '{name}' is a key of the test (its dictionaries): an exploit step "
                          f"does not override it; give the criteria another key, as 'params.{name.split('.')[-1]}'")
        else:
            warnings.append(f"{where}: 'with' key '{name}' is not a 'params.*' key: write 'params.{name}' "
                            "(and read it as params." + name + " in the criteria), as commands do")
    return errors, warnings


def exploit_keys(where: str, given: Mapping[str, Any], keys: dict) -> dict:
    """The keys of an exploit step's criteria: the step's keys, its ``params`` set by ``with:``, and the plain
    keys of ``with:`` the context does not define, each rendered with the step's keys. Raises on a plain key the
    context defines."""
    from testsuit.datatools.datapack.templating import expand_dotted_keys
    from testsuit.sequencer.util import deep_merge, render
    errors, _ = exploit_with_problems(where, given, keys)
    if errors:
        raise SequencerError("; ".join(errors))
    params, plain = exploit_with(given)
    out = dict(keys)
    if params:
        out[PARAMS] = {**(keys.get(PARAMS) or {}), **{n: render(v, keys, f"{where}: 'with' key 'params.{n}'")
                                                      for n, v in params.items()}}
    if plain:
        rendered = {n: render(v, keys, f"{where}: 'with' key '{n}'") for n, v in plain.items()}
        out = deep_merge(out, expand_dotted_keys(rendered))
    return out


def criteria_missing_keys(folder: Any, keys: Mapping) -> list[tuple[str, str]]:
    """(file name, key) for each key the scenario files of a criteria folder (or a scenario file) read that
    ``keys`` does not define: exploit_runner would keep that scenario as is, and its checks fail."""
    from pathlib import Path

    from testsuit.datatools.datapack.templating import undefined_keys
    folder = Path(folder)
    if folder.is_file():
        files = [folder]
    elif folder.is_dir():
        files = sorted(p for p in folder.rglob("*") if p.suffix in (".yml", ".yaml") and p.is_file())
    else:
        files = []
    found = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "{{" in text or "{%" in text:
            found += [(path.name, key) for key in undefined_keys(text, keys)]
    return list(dict.fromkeys(found))
