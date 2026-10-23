"""YAML editing that keeps the file as written (README section 13.2): comments, order, flow style.

The Editor forms send the new contents of a document, or of one of its subtrees, as plain data.
``merge`` applies it onto the round-trip document of ruamel.yaml: unchanged parts keep their
comments and layout, list items with an ``id`` are matched by id (a step moved keeps its comment),
and new mappings and lists take the flow style of their siblings.

``locate`` gives the line of a path in a document, to put validation messages on the right line.
"""
from __future__ import annotations

import difflib
import io
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from testsuit.sequencer.util import SequencerError


def _yaml() -> YAML:
    y = YAML()                                  # round-trip
    y.preserve_quotes = True
    y.width = 4096                              # never wrap a long command
    y.indent(mapping=2, sequence=4, offset=2)   # the layout of this project's files
    return y


def load(text: str) -> Any:
    try:
        return _yaml().load(text) if text.strip() else CommentedMap()
    except Exception as e:
        raise SequencerError(f"invalid YAML: {e}") from e


def dump(document: Any) -> str:
    out = io.StringIO()
    _yaml().dump(document, out)
    return out.getvalue()


def plain(node: Any) -> Any:
    """The data of a round-trip node, as plain dicts and lists."""
    if isinstance(node, dict):
        return {str(k) if not isinstance(k, (int, float, bool)) else k: plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [plain(v) for v in node]
    if hasattr(node, "value") and type(node).__module__.startswith("ruamel"):
        return node.value if not isinstance(node, str) else str(node)
    if type(node).__module__.startswith("ruamel"):
        for base in (bool, int, float, str):
            if isinstance(node, base):
                return base(node)
    return node


def _flow_like(siblings: list, kind: type) -> bool | None:
    """Flow style of the first sibling container of that kind, None if there is none."""
    for sibling in siblings:
        if isinstance(sibling, kind) and hasattr(sibling, "fa"):
            return sibling.fa.flow_style()
    return None


def _build(value: Any, siblings: list) -> Any:
    """A new round-trip node for plain data, styled like its siblings."""
    if isinstance(value, dict):
        node = CommentedMap()
        children = [v for s in siblings if isinstance(s, dict) for v in s.values()]
        for k, v in value.items():
            node[k] = _build(v, children)
        if _flow_like(siblings, CommentedMap):
            node.fa.set_flow_style()
        return node
    if isinstance(value, list):
        node = CommentedSeq()
        children = [v for s in siblings if isinstance(s, list) for v in s]
        for v in value:
            node.append(_build(v, children))
        flow = _flow_like(siblings, CommentedSeq)
        if flow or (flow is None and all(not isinstance(v, (dict, list)) for v in value)):
            node.fa.set_flow_style()            # short lists of scalars: [a, b]
        return node
    return value


class _Merger:
    """Merges plain data onto round-trip nodes. A node reached twice (a YAML alias) is changed once:
    a second place that wants other contents gets a copy of its own, so the alias is broken there only."""

    def __init__(self) -> None:
        self.visited: set[int] = set()

    def value(self, old: Any, new: Any, siblings: list) -> Any:
        if isinstance(old, (CommentedMap, CommentedSeq)) and id(old) in self.visited:
            return old if plain(old) == new else _build(new, siblings + [old])
        if isinstance(old, CommentedMap) and isinstance(new, dict):
            self.visited.add(id(old))
            self.map(old, new)
            return old
        if isinstance(old, CommentedSeq) and isinstance(new, list):
            self.visited.add(id(old))
            self.seq(old, new)
            return old
        if plain(old) == new:
            return old                              # keeps quoting and scalar style
        return _build(new, siblings + ([old] if isinstance(old, (dict, list)) else []))

    def map(self, old: CommentedMap, new: dict) -> None:
        for key in [k for k in old if k not in new]:
            del old[key]
        siblings = list(old.values())
        keys = list(new)
        for i, key in enumerate(keys):
            if key in old:
                merged = self.value(old[key], new[key], siblings)
                if merged is not old[key]:
                    old[key] = merged
            else:
                # insert after the previous key of the new order, to keep the order the form gave
                position = len(old)
                for previous in reversed(keys[:i]):
                    if previous in old:
                        position = list(old).index(previous) + 1
                        break
                old.insert(position, key, _build(new[key], siblings))

    def seq(self, old: CommentedSeq, new: list) -> None:
        old_ids, new_ids = _ids(list(old)), _ids(new)
        siblings = list(old)
        if old_ids is not None and new_ids is not None and old_ids != new_ids:
            by_id = dict(zip(old_ids, list(old)))
            result = [self.value(by_id[i], v, siblings) if i in by_id else _build(v, siblings)
                      for i, v in zip(new_ids, new)]
            # comments are attached by index: move those of the items that stay
            comments = {n: old.ca.items[old_ids.index(i)] for n, i in enumerate(new_ids)
                        if i in by_id and old_ids.index(i) in old.ca.items}
            old.ca.items.clear()
            while len(old):
                old.pop()
            old.ca.items.clear()
            old.extend(result)
            old.ca.items.update(comments)
            return
        for i, value in enumerate(new):
            if i < len(old):
                merged = self.value(old[i], value, siblings)
                if merged is not old[i]:
                    old[i] = merged
            else:
                old.append(_build(value, siblings))
        while len(old) > len(new):
            old.pop()
        for i in [i for i in old.ca.items if i >= len(new)]:
            del old.ca.items[i]


def _ids(items: list) -> list | None:
    ids = [item.get("id") if isinstance(item, dict) else None for item in items]
    if all(i is not None for i in ids) and len(set(map(str, ids))) == len(ids):
        return [str(i) for i in ids]
    return None


def merge(text: str, data: Any, path: list | None = None) -> str:
    """The text of a YAML document after replacing the subtree at path (the whole document by default)
    by data, keeping comments and layout where nothing changed."""
    document = load(text)
    if not path:
        return _patch(text, dump(load(text)), dump(_Merger().value(document, data, [])))
    parent = document
    for key in path[:-1]:
        if isinstance(parent, dict):
            if key not in parent or not isinstance(parent[key], (dict, list)):
                parent[key] = CommentedMap()
            parent = parent[key]
        elif isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
            parent = parent[key]
        else:
            raise SequencerError(f"no {'/'.join(map(str, path))} in the document")
    last = path[-1]
    if data is None and isinstance(parent, dict):
        parent.pop(last, None)
    elif isinstance(parent, dict):
        siblings = list(parent.values())
        if last in parent:
            parent[last] = _Merger().value(parent[last], data, siblings)
        else:
            parent[last] = _build(data, siblings)
    elif isinstance(parent, list) and isinstance(last, int):
        if last < len(parent):
            parent[last] = _Merger().value(parent[last], data, list(parent))
        elif last == len(parent):
            parent.append(_build(data, list(parent)))
        else:
            raise SequencerError(f"no {'/'.join(map(str, path))} in the document")
    return _patch(text, dump(load(text)), dump(document))


def _norm(line: str) -> str:
    return " ".join(line.split())


def _patch(original: str, before: str, after: str) -> str:
    """Apply to the original text the line changes between two dumps by ruamel (before: the original
    reloaded and dumped, after: the modified document). Lines no change touches stay byte for byte as
    written, alignment spaces included, which ruamel itself does not keep."""
    if before == after:
        return original
    a = original.splitlines(keepends=True)
    ap = before.splitlines(keepends=True)
    bp = after.splitlines(keepends=True)
    # where each line boundary of the dump falls in the original (equal up to spaces, or whole blocks)
    start = [len(a)] * (len(ap) + 1)
    end = [len(a)] * (len(ap) + 1)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, [_norm(x) for x in ap], [_norm(x) for x in a],
                                                       autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i1, i2 + 1):
                start[k] = end[k] = j1 + (k - i1)
        elif tag in ("replace", "delete"):
            start[i1] = j1
            for k in range(i1 + 1, i2):
                start[k], end[k] = j1, j2
            end[i2] = j2
            start[i2] = min(start[i2], j2) if i2 < len(ap) else start[i2]
        else:                                           # lines only in the original: comments ruamel moved
            start[i1] = min(start[i1], j1)
            end[i1] = max(end[i1], j2) if i1 else j1
    hunks = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ap, bp, autojunk=False).get_opcodes():
        if tag != "equal":
            x1 = start[i1]
            x2 = end[i2] if i2 > i1 else x1
            hunks.append((x1, max(x1, x2), bp[j1:j2]))
    lines = list(a)
    for x1, x2, new in reversed(hunks):
        lines[x1:x2] = new
    text = "".join(lines)
    if not text.endswith("\n") and text:
        text += "\n"
    return text


# ── locating paths, for messages ───────────────────────────────────────────

def locate(text: str, path: list) -> tuple[int, int] | None:
    """(line, column), from 1, of the deepest node of path found in the document."""
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return None
    found = None
    for key in path:
        if node is None:
            break
        if isinstance(node, yaml.MappingNode):
            match = next(((k, v) for k, v in node.value if str(k.value) == str(key)), None)
            if match is None:
                break
            found, node = match[0], match[1]
        elif isinstance(node, yaml.SequenceNode) and isinstance(key, int) and 0 <= key < len(node.value):
            node = node.value[key]
            found = node
        else:
            break
    if found is None and node is not None and path == []:
        found = node
    if found is None:
        return None
    return found.start_mark.line + 1, found.start_mark.column + 1


def syntax_error(text: str) -> dict | None:
    """The YAML syntax error of a text, with its position, None if it parses."""
    try:
        yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        return {"message": f"YAML syntax: {getattr(e, 'problem', None) or e}",
                "line": mark.line + 1 if mark else 1, "column": mark.column + 1 if mark else 1}
    return None
