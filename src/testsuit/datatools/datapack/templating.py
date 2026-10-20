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

import os
import re
import shutil
from collections import ChainMap
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import jinja2
import yaml

__all__ = ["Keys", "TemplateError", "expand_dotted_keys", "has_template", "load_yaml_mapping"]


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


def _environment(search_path: Iterable[Path] = ()) -> _Environment:
    return _Environment(
        loader=jinja2.FileSystemLoader([str(p) for p in search_path]),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


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
        self.root = _LazyMapping(self, (), keys.data)

    def value(self, path: tuple, raw: Any) -> Any:
        if isinstance(raw, Mapping):
            return _LazyMapping(self, path, raw)
        if self.used is not None:
            self.used.add(_dotted(path))
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

    def render_file(self, source: Path, search_path: Iterable[Path] = (), extra: Mapping | None = None) -> tuple[str, dict[str, str]]:
        """Render a template file.

        Includes (``{% include "x" %}``, ``{% import %}``) are looked up in the folder of the file,
        then in search_path.

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
        return text, {key: self.origins.get(key, "") for key in sorted(used) if key in self.origins}

    def render_tree(self, source: Path, target: Path, search_path: Iterable[Path] = (), extra: Mapping | None = None) -> dict[str, dict[str, str]]:
        """Render a file, or every file of a folder, into target. Files that are not UTF-8 text are copied as is.

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
            text, file_keys = self.render_file(file, search_path, extra)
            destination.write_text(text, encoding="utf-8")
            used[destination.name if source.is_file() else destination.relative_to(target).as_posix()] = file_keys
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
