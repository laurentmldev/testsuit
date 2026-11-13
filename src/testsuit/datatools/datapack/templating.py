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

"""Jinja2 templating shared by the datapack tool and exploit_runner.

Keys come from YAML dictionaries (nested mappings), merged in the order they are added: a later
dictionary overrides the values of an earlier one, mappings being merged key by key::

    # versions.yml
    bench_calib:
      version: v1.2
    datapack:
      output_dir: "{{ testdef.path }}/../.work/datapacks"

A dotted name (``bench_calib.version: v1.2``) is read as nested keys.

A value can reference other keys with a Jinja2 expression. Values are resolved lazily, on first use,
so a dictionary can reference keys of a dictionary loaded after it; circular references are
reported. A value made of a single expression keeps the type of its result (``"{{ n }}"`` with
``n: 3`` gives ``3``, not ``"3"``), so lists, numbers and booleans can be shared too.

Undefined keys are errors (``StrictUndefined``). In templates, ``a.b`` reads key ``b`` of mapping
``a`` even when a dict method has the same name (``a.values``, ``a.items``...).

Usage::

    keys = Keys()
    keys.add_file("versions.yml")
    keys.add({"testdef": {"id": "t1"}}, origin="<datapack>")
    keys.render_string("{{ bench_calib.version }}")           # 'v1.2'
    text, used = keys.render_file(Path("templates/bench_config.yml"), search_path=[setup_dir])
    snapshot = keys.resolve_all()                              # plain dict, every value resolved
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
from collections import ChainMap
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import jinja2
import jinja2.ext
import jinja2.lexer as tokens
import yaml

__all__ = ["Keys", "TemplateError", "expand_dotted_keys", "has_template", "load_yaml_mapping", "parse_key_value",
           "run_time_keys", "keys_set_by_hand", "undefined_keys"]


class TemplateError(Exception):
    """A dictionary or template could not be loaded or rendered."""


# '{{ ... }}' alone, possibly surrounded by blanks: rendered to the native type of the expression
_SINGLE_EXPRESSION = re.compile(r"^\s*\{\{(?P<expr>(?:(?!\}\}).)*)\}\}\s*$", re.DOTALL)
_TEMPLATE_MARKS = ("{{", "{%", "{#")


def has_template(text: str) -> bool:
    """True if text contains a Jinja2 expression, statement or comment."""
    return any(mark in text for mark in _TEMPLATE_MARKS)


class _Environment(jinja2.Environment):
    """Mappings are read by key first, so that 'a.values' is key 'values' and not dict.values()."""

    def getattr(self, obj: Any, attribute: str) -> Any:
        if isinstance(obj, Mapping):
            try:
                return obj[attribute]
            except (KeyError, TypeError):
                pass
        return super().getattr(obj, attribute)


# ── where each value of a rendered file comes from ───────────────────────
# Every '{{ expression }}' of a template rendered with value marks gets its output enclosed in marks:
# _OPEN before it, _CLOSE <record> _END after it (private use characters), stripped once rendered.
_OPEN, _CLOSE, _END = "\ue000", "\ue001", "\ue002"
_MARK = re.compile(f"{_OPEN}|{_CLOSE}(\\d+){_END}")
# no space before or after these tokens when an expression is written back as text
_NO_SPACE_BEFORE = {tokens.TOKEN_DOT, tokens.TOKEN_COMMA, tokens.TOKEN_RPAREN, tokens.TOKEN_RBRACKET, tokens.TOKEN_COLON}
_NO_SPACE_AFTER = {tokens.TOKEN_DOT, tokens.TOKEN_LPAREN, tokens.TOKEN_LBRACKET}
# beyond these, a file gets no value marks (its keys are still listed)
MAX_MARKED_SIZE = 2_000_000
MAX_MARKED_VALUES = 20_000


def _expression_text(body: list[tokens.Token]) -> str:
    text, previous = "", None
    for token in body:
        value = repr(token.value) if token.type == tokens.TOKEN_STRING else str(token.value)
        tight = previous is None or token.type in _NO_SPACE_BEFORE or previous.type in _NO_SPACE_AFTER \
            or (token.type in (tokens.TOKEN_LPAREN, tokens.TOKEN_LBRACKET)
                and previous.type in (tokens.TOKEN_NAME, tokens.TOKEN_RPAREN, tokens.TOKEN_RBRACKET))
        text += value if tight else " " + value
        previous = token
    return text


def _names(body: list[tokens.Token]) -> set[str]:
    """The variables an expression reads: names, not attributes, filters, tests or keyword arguments."""
    names = set()
    for i, token in enumerate(body):
        before = body[i - 1] if i else None
        after = body[i + 1] if i + 1 < len(body) else None
        if token.type == tokens.TOKEN_NAME \
                and not (before and (before.type in (tokens.TOKEN_DOT, tokens.TOKEN_PIPE) or before.test("name:is"))) \
                and not (after and after.type == tokens.TOKEN_ASSIGN):
            names.add(token.value)
    return names


class _ValueMarks(jinja2.ext.Extension):
    """Rewrites '{{ expr }}' as '{{ tsq_open() }}{{ tsq_close(<n>, (expr)) }}', n numbering the expressions."""

    def filter_stream(self, stream: tokens.TokenStream) -> Iterator[tokens.Token]:
        expressions: list[str] = self.environment.tsq_expressions
        for token in stream:
            if token.type != tokens.TOKEN_VARIABLE_BEGIN:
                yield token
                continue
            body = []
            for inner in stream:
                if inner.type == tokens.TOKEN_VARIABLE_END:
                    end = inner
                    break
                body.append(inner)
            else:
                raise jinja2.TemplateSyntaxError("unexpected end of template", token.lineno, stream.name, stream.filename)
            line = token.lineno
            expressions.append(_expression_text(body))
            self.environment.tsq_names.append(_names(body))

            def t(kind: str, value: Any = None, line: int = line) -> tokens.Token:
                return tokens.Token(line, kind, value)
            yield from (token, t(tokens.TOKEN_NAME, "tsq_open"), t(tokens.TOKEN_LPAREN, "("), t(tokens.TOKEN_RPAREN, ")"), end)
            yield from (token, t(tokens.TOKEN_NAME, "tsq_close"), t(tokens.TOKEN_LPAREN, "("),
                        t(tokens.TOKEN_INTEGER, len(expressions) - 1), t(tokens.TOKEN_COMMA, ","), t(tokens.TOKEN_LPAREN, "("))
            yield from body
            yield from (t(tokens.TOKEN_RPAREN, ")"), t(tokens.TOKEN_RPAREN, ")"), end)


def _utf16_length(text: str) -> int:
    return len(text) + sum(1 for c in text if ord(c) > 0xFFFF)


def _strip_marks(marked: str, records: list[tuple[int, list[str]]]) -> tuple[str, list[list]] | None:
    """Text without the marks, and the values: [start, end, expression, keys] (UTF-16 offsets, as editors count).

    Nested values (a macro called by an expression) keep the innermost ones. None if the marks are broken.
    """
    parts: list[str] = []
    opened: list[int] = []
    values: list[list] = []
    position = last = 0
    for match in _MARK.finditer(marked):
        chunk = marked[last:match.start()]
        parts.append(chunk)
        position += _utf16_length(chunk)
        last = match.end()
        if match.group(1) is None:
            opened.append(position)
            continue
        if not opened:
            return None
        start = opened.pop()
        expression, keys = records[int(match.group(1))]
        if position > start:
            values.append([start, position, expression, keys])
    if opened:
        return None
    parts.append(marked[last:])
    # values nest: one holds another when the next one (by start, longest first) starts within it
    values.sort(key=lambda v: (v[0], -v[1]))
    innermost = [v for i, v in enumerate(values) if i + 1 == len(values) or values[i + 1][0] >= v[1]]
    return "".join(parts), innermost


def _environment(search_path: Iterable[Path] = (), marks: bool = False) -> _Environment:
    env = _Environment(
        extensions=[_ValueMarks] if marks else [],
        loader=jinja2.FileSystemLoader([str(p) for p in search_path]),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    # structured data read from another component's generated input: read_input('pilot/pilot.yml') | from_yaml
    env.filters["from_yaml"] = yaml.safe_load
    env.filters["from_json"] = json.loads
    env.tsq_expressions = []
    env.tsq_names = []
    return env


# keys only a run defines (the sequencer binds slots to the lab when a run starts, after generation)
RUN_TIME_KEYS = {
    "component": "the lab component a slot is bound to",
    "stage_slots": "the lab stage each stage slot is bound to",
    "stage": "the lab stage of a slot's stage_slot",
    "lab": "the lab",
}


def run_time_keys(defined: Mapping) -> dict:
    """Placeholders for the run-time keys that ``defined`` (the dictionaries) does not hold: reading one
    fails with a message saying why it is not defined in a template, instead of a bare 'is undefined'."""
    found = {}
    for name, what in RUN_TIME_KEYS.items():
        if name not in defined:
            found[name] = jinja2.StrictUndefined(
                hint=f"'{name}' is a run-time key ({what}): a run binds slots to the lab when it starts, after the "
                     f"datapack is generated, so it is not defined in an input template. Use 'slot.*' here, or "
                     f"pass the value from a pipeline step (its env or command)", name=name)
    return found


def load_yaml_mapping(path: str | os.PathLike) -> dict:
    """Load a YAML file whose root is a mapping (an empty file gives an empty mapping)."""
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as e:
        raise TemplateError(f"cannot read '{path}': {e}") from e
    except yaml.YAMLError as e:
        raise TemplateError(f"invalid YAML in '{path}': {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TemplateError(f"'{path}' must contain a mapping at its root, not {type(data).__name__}")
    return data


def expand_dotted_keys(mapping: Mapping) -> dict:
    """{'a.b': 1, 'a.c': 2} -> {'a': {'b': 1, 'c': 2}}, for key/value pairs given on a command line or by code."""
    result: dict = {}
    for key, value in mapping.items():
        if isinstance(value, Mapping):
            value = expand_dotted_keys(value)
        node = result
        parts = str(key).split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise TemplateError(f"key '{key}': '{part}' is already a value, it cannot hold sub-keys")
        _merge_into(node, {parts[-1]: value})
    return result


def parse_key_value(text: str) -> tuple[str, Any]:
    """'name.sub=value' (a key set by hand, on a command line) -> ('name.sub', value). The value is read
    as YAML: ``3`` is a number, ``true`` a boolean, ``[a, b]`` a list; quote it to keep it as text."""
    name, sep, value = str(text).partition("=")
    name = name.strip()
    if not sep or not name or any(not part for part in name.split(".")):
        raise TemplateError(f"'{text}': a key set by hand is written name=value (name.sub=value for a nested key)")
    return name, parse_value(value)


def parse_value(value: Any) -> Any:
    """A value set by hand, as typed: read as YAML when it is text (an empty text stays empty text)."""
    if not isinstance(value, str) or not value.strip():
        return value
    try:
        return yaml.safe_load(value)
    except yaml.YAMLError:
        return value


def keys_set_by_hand(defined: Mapping, values: Mapping[str, Any], origins: Mapping[str, str] | None = None) -> list[dict]:
    """What each key set by hand does to the keys already defined: it **sets** a key no dictionary defines,
    or it **overrides** one that is defined (the value it replaces, and where that one comes from).

    :param defined: the keys before those set by hand (nested mappings)
    :param values: dotted name -> value set by hand
    :param origins: dotted name -> where the defined key comes from (Keys.origins)
    :return: one entry per key set by hand: ``{key, value, overrides}``, and ``was``, ``origin`` for an override
    """
    entries = []
    for name, value in values.items():
        node: Any = defined
        entry: dict[str, Any] = {"key": name, "value": value, "overrides": False}
        parts = str(name).split(".")
        for i, part in enumerate(parts):
            if not isinstance(node, Mapping):               # a value where sub-keys are set: it is replaced
                at = ".".join(parts[:i])
                entry.update(overrides=True, was=_plain(node), origin=(origins or {}).get(at, ""))
                break
            if part not in node:
                break
            node = node[part]
            if i == len(parts) - 1:
                origin = (origins or {}).get(name) or next(
                    (o for k, o in (origins or {}).items() if k.startswith(name + ".")), "")
                entry.update(overrides=True, was=_plain(node), origin=origin)
        entries.append(entry)
    return entries


def undefined_keys(template: str, keys: Mapping) -> list[str]:
    """The keys a template reads that ``keys`` does not define, as dotted names (``params.speed``): the keys
    to set by hand for it to render. Only plain names are seen (``a.b``, ``a['b']``), not computed ones."""
    from jinja2 import nodes
    try:
        tree = _environment().parse(template if has_template(template) else "{{ " + template + " }}")
    except jinja2.TemplateSyntaxError:
        return []
    local = {n.name for n in tree.find_all(nodes.Name) if n.ctx in ("store", "param")} | {"loop", "range", "namespace"}

    def chain(node) -> list[str] | None:
        if isinstance(node, nodes.Name):
            return [node.name] if node.ctx == "load" else None
        if isinstance(node, nodes.Getattr):
            base = chain(node.node)
            return base + [node.attr] if base is not None else None
        if isinstance(node, nodes.Getitem) and isinstance(node.arg, nodes.Const) and isinstance(node.arg.value, str):
            base = chain(node.node)
            return base + [node.arg.value] if base is not None else None
        return None

    found: list[str] = []

    def walk(node) -> None:
        path = chain(node) if isinstance(node, (nodes.Name, nodes.Getattr, nodes.Getitem)) else None
        if path is not None:
            if path[0] not in local:
                value: Any = keys
                for part in path:
                    if isinstance(value, Mapping) and part in value:
                        value = value[part]
                    elif isinstance(value, Mapping):
                        name = ".".join(path)
                        if name not in found:
                            found.append(name)
                        break
                    else:                                       # an attribute of a value (a method...): defined
                        break
            return
        for child in node.iter_child_nodes():
            walk(child)
    walk(tree)
    return found


def _merge_into(target: dict, source: Mapping) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _merge_into(target[key], value)
        elif isinstance(value, Mapping):
            target[key] = {}
            _merge_into(target[key], value)
        else:
            target[key] = value


def _leaves(data: Any, path: tuple = ()) -> Iterator[tuple[tuple, Any]]:
    if isinstance(data, Mapping) and data:
        for key, value in data.items():
            yield from _leaves(value, path + (key,))
    else:
        yield path, data


def _dotted(path: tuple) -> str:
    return ".".join(str(p) for p in path)


class _LazyMapping(Mapping):
    """Read-only view of a mapping of the dictionaries, resolving its values on access."""

    def __init__(self, resolver: _Resolver, path: tuple, raw: Mapping) -> None:
        self._resolver = resolver
        self._path = path
        self._raw = raw

    def __getitem__(self, key: Any) -> Any:
        return self._resolver.value(self._path + (key,), self._raw[key])

    def __iter__(self) -> Iterator:
        return iter(self._raw)

    def __len__(self) -> int:
        return len(self._raw)

    def __str__(self) -> str:
        return str(_plain(self))

    __repr__ = __str__


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


class _Resolver:
    """Resolves (renders) the values of the merged dictionaries, each one once, detecting cycles."""

    def __init__(self, keys: Keys) -> None:
        self._keys = keys
        self._cache: dict[tuple, Any] = {}
        self._stack: list[tuple] = []
        self.used: set[str] | None = None
        # keys read by each '{{ expression }}' being evaluated (value marks)
        self.frames: list[set[str]] = []
        self.root = _LazyMapping(self, (), keys.data)

    def value(self, path: tuple, raw: Any) -> Any:
        if isinstance(raw, Mapping):
            return _LazyMapping(self, path, raw)
        if self.used is not None:
            self.used.add(_dotted(path))
        for frame in self.frames:
            frame.add(_dotted(path))
        if isinstance(raw, list):
            return [self.value(path + (i,), v) for i, v in enumerate(raw)]
        if not isinstance(raw, str) or not has_template(raw):
            return raw
        if path in self._cache:
            return self._cache[path]
        if path in self._stack:
            cycle = self._stack[self._stack.index(path):] + [path]
            raise TemplateError("circular reference: " + " -> ".join(_dotted(p) for p in cycle))
        self._stack.append(path)
        try:
            result = self._keys._render(raw, self.root, where=f"key '{_dotted(path)}'")
        finally:
            self._stack.pop()
        self._cache[path] = result
        return result


class Keys:
    """Merged YAML dictionaries, the origin of every value, and the Jinja2 rendering based on them."""

    def __init__(self) -> None:
        self.data: dict = {}
        # dotted key -> file (or label) that last defined it
        self.origins: dict[str, str] = {}
        self._env = _environment()
        self._resolver: _Resolver | None = None

    # ── loading ──────────────────────────────────────────────────────────

    def add(self, mapping: Mapping, origin: str) -> None:
        """Merge mapping over the current keys; its values override the existing ones."""
        mapping = expand_dotted_keys(mapping)
        _merge_into(self.data, mapping)
        for path, _ in _leaves(mapping):
            if path:
                self.origins[_dotted(path)] = origin
        self._resolver = None

    def add_file(self, path: str | os.PathLike) -> dict:
        """Merge the YAML dictionary file; return its contents."""
        mapping = load_yaml_mapping(path)
        self.add(mapping, origin=str(path))
        return mapping

    def copy(self) -> Keys:
        other = Keys()
        _merge_into(other.data, self.data)
        other.origins = dict(self.origins)
        return other

    # ── rendering ────────────────────────────────────────────────────────

    @property
    def root(self) -> Mapping:
        """Lazy view of all the keys, for template contexts."""
        return self._get_resolver().root

    def _get_resolver(self) -> _Resolver:
        if self._resolver is None:
            self._resolver = _Resolver(self)
        return self._resolver

    def _context(self, template: jinja2.Template, root: Mapping, extra: Mapping | None) -> jinja2.runtime.Context:
        # shared context: names are looked up in 'root' on use, nothing is resolved in advance
        parent = ChainMap(dict(extra or {}), root, template.globals)
        return template.new_context(parent, shared=True)

    def _render(self, text: str, root: Mapping, where: str, extra: Mapping | None = None) -> Any:
        try:
            single = _SINGLE_EXPRESSION.match(text)
            if single and not has_template(single.group("expr")):
                template = self._env.from_string("{% set tsq_value = (" + single.group("expr") + ") %}")
                context = self._context(template, root, extra)
                for _ in template.root_render_func(context):
                    pass
                value = context.vars["tsq_value"]
                if isinstance(value, jinja2.Undefined):
                    value._fail_with_undefined_error()
                return _plain(value)
            template = self._env.from_string(text)
            return self._env.concat(template.root_render_func(self._context(template, root, extra)))
        except TemplateError:
            raise
        except jinja2.TemplateError as e:
            raise TemplateError(f"{where}: {e}") from e

    @contextlib.contextmanager
    def tracking(self) -> Iterator[dict[str, str]]:
        """Record the keys read through ``root`` within the block (dotted name -> origin)."""
        resolver = self._get_resolver()
        resolver.used = set()
        used: dict[str, str] = {}
        try:
            yield used
        finally:
            read, resolver.used = resolver.used, None
            used.update({key: self.origins.get(key, "") for key in sorted(read) if key in self.origins})

    def render_string(self, text: str, extra: Mapping | None = None, where: str = "string") -> Any:
        """Render text; a text made of one expression gives the native value of that expression."""
        if not isinstance(text, str) or not has_template(text):
            return text
        return self._render(text, self.root, where, extra)

    def render_data(self, data: Any, extra: Mapping | None = None, where: str = "") -> Any:
        """Render every string of a structure loaded from YAML (mappings, lists, scalars)."""
        if isinstance(data, Mapping):
            return {k: self.render_data(v, extra, f"{where}.{k}" if where else str(k)) for k, v in data.items()}
        if isinstance(data, list):
            return [self.render_data(v, extra, f"{where}[{i}]") for i, v in enumerate(data)]
        if isinstance(data, str):
            return self.render_string(data, extra, where=where or "string")
        return data

    def render_file(self, source: Path, search_path: Iterable[Path] = (), extra: Mapping | None = None,
                    values: dict | None = None) -> tuple[str, dict[str, str]]:
        """Render a template file.

        Includes (``{% include "x" %}``, ``{% import %}``) are looked up in the folder of the file,
        then in search_path.

        :param values: if given, gets where each '{{ expression }}' output is in the text, when it can be
            told (see :meth:`values_of`): ``{"expressions": [...], "values": [[start, end, expression, keys]]}``
        :return: rendered text, keys used by the template (dotted name -> origin)
        """
        source = Path(source)
        env = _environment([source.parent, *search_path])
        resolver = self._get_resolver()
        resolver.used = set()
        try:
            template = env.get_template(source.name)
            text = env.concat(template.root_render_func(self._context(template, resolver.root, extra)))
        except TemplateError as e:
            raise TemplateError(f"'{source}': {e}") from e
        except jinja2.TemplateError as e:
            location = f"'{source}'" + (f" line {e.lineno}" if getattr(e, "lineno", None) else "")
            raise TemplateError(f"{location}: {e}") from e
        finally:
            used, resolver.used = resolver.used, None
        if values is not None and len(text) <= MAX_MARKED_SIZE:
            values.update(self.values_of(source, text, search_path, extra) or {})
        return text, {key: self.origins.get(key, "") for key in sorted(used) if key in self.origins}

    def _defined(self, key: str) -> str | None:
        """The key defined in a dictionary that holds key ('a.list' for 'a.list.0'), if any."""
        while key not in self.origins and "." in key:
            key = key.rsplit(".", 1)[0]
        return key if key in self.origins else None

    def values_of(self, source: Path, text: str, search_path: Iterable[Path] = (), extra: Mapping | None = None) -> dict | None:
        """Where each '{{ expression }}' output is in text, the rendering of source: the template is
        rendered again with marks around each output. None when that does not give text back (a filter
        that counts characters of a marked value, for instance), or for too many values.

        :return: ``{"expressions": [expression text], "values": [[start, end, expression index, [keys]]]}``,
            offsets in UTF-16 code units (as editors count them)
        """
        env = _environment([source.parent, *search_path], marks=True)
        resolver = self._get_resolver()
        records: list[tuple[int, list[str]]] = []

        def tsq_open() -> str:
            resolver.frames.append(set())
            return _OPEN

        def tsq_close(expression: int, value: Any) -> str:
            # rendered here, so that keys read to write the value (a lazy mapping) are counted
            text = str(value)
            read = resolver.frames.pop() if resolver.frames else set()
            # template variables are looked up when the template starts, before any value is written
            read |= {name for name in env.tsq_names[expression] if name in self.origins}
            records.append((expression, sorted({self._defined(key) for key in read} - {None})))
            return f"{text}{_CLOSE}{len(records) - 1}{_END}"

        env.globals.update(tsq_open=tsq_open, tsq_close=tsq_close)
        try:
            template = env.get_template(Path(source).name)
            marked = env.concat(template.root_render_func(self._context(template, resolver.root, extra)))
        except Exception:  # noqa: BLE001 - the text is rendered already: no values, not an error
            return None
        finally:
            resolver.frames.clear()
        stripped = _strip_marks(marked, records)
        if stripped is None or stripped[0] != text or len(stripped[1]) > MAX_MARKED_VALUES:
            return None
        return {"expressions": env.tsq_expressions, "values": stripped[1]}

    def render_tree(self, source: Path, target: Path, search_path: Iterable[Path] = (), extra: Mapping | None = None,
                    values: dict | None = None) -> dict[str, dict[str, str]]:
        """Render a file, or every file of a folder, into target. Files that are not UTF-8 text are copied as is.

        :param values: if given, gets the values of each rendered file (see :meth:`render_file`), by the same names
        :return: keys used, by rendered file (path relative to target)
        """
        source, target = Path(source), Path(target)
        files = [source] if source.is_file() else sorted(p for p in source.rglob("*") if p.is_file())
        used: dict[str, dict[str, str]] = {}
        for file in files:
            destination = target if source.is_file() else target / file.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copy2(file, destination)
                continue
            file_values: dict | None = {} if values is not None else None
            text, file_keys = self.render_file(file, search_path, extra, file_values)
            destination.write_text(text, encoding="utf-8")
            name = destination.name if source.is_file() else destination.relative_to(target).as_posix()
            used[name] = file_keys
            if file_values:
                values[name] = file_values
        if source.is_dir():
            # keep empty folders too
            for folder in source.rglob("*"):
                if folder.is_dir():
                    (target / folder.relative_to(source)).mkdir(parents=True, exist_ok=True)
        return used

    def resolve_all(self) -> dict:
        """Every key resolved, as plain data.

        :raises TemplateError: listing every key that cannot be resolved
        """
        resolver = self._get_resolver()
        errors = []
        for path, raw in _leaves(self.data):
            if not path:
                continue
            try:
                resolver.value(path, raw)
            except TemplateError as e:
                origin = self.origins.get(_dotted(path), "?")
                errors.append(f"  - {e} (defined in {origin})")
        if errors:
            raise TemplateError("unresolved keys:\n" + "\n".join(errors))
        return _plain(resolver.root)
