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

"""The built-in ReqIF backend: requirements exported by a requirements tool as a ReqIF file.

ReqIF (OMG Requirements Interchange Format) is what DOORS, DOORS Next, Polarion, Jama, Codebeamer, PTC and most
others export. A ``.reqif`` file (XML) or a ``.reqifz`` archive (a zip of them, with their attachments) is kept in the
tests repository, next to the sources file, and re-exported when the requirements change::

    sources:
      sys:
        backend: reqif
        options:
          file: reqs/system.reqifz
          url: "https://doors.example/rm/resources/{id}"     # optional

Each SPECIFICATION is a top node of the tree; in it, a SPEC-HIERARCHY entry whose object is a heading is a node
(its label: the heading's text), and any other object is a requirement whose path is the chain of headings above it.
Which attribute gives the id, title, text... is guessed from the usual names (``ReqIF.ForeignID``, ``ReqIF.Name``,
``ReqIF.Text``, ``ReqIF.ChapterName``...), or set by the options (see ``ReqifBackend.options_help``).
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from testsuit.reqs.backend import ReqBackend, ReqError, register_req_backend, under
from testsuit.reqs.model import Node, Requirement

# attribute names tried in order (LONG-NAME, case-insensitive) when no option names one
GUESS = {
    "id": ["ReqIF.ForeignID", "ID", "Identifier", "Object Identifier", "Req ID", "Requirement ID", "Key",
           "Absolute Number"],
    "title": ["ReqIF.Name", "Title", "Name", "Summary", "Object Heading"],
    "description": ["ReqIF.Text", "Description", "Text", "Object Text"],
    "heading": ["ReqIF.ChapterName", "Object Heading", "Heading", "Chapter"],
    "rationale": ["Rationale", "ReqIF.Rationale", "Justification"],
    "status": ["Status", "ReqIF.Status", "State", "Workflow State"],
    "comments": ["Comments", "Comment", "ReqIF.Comment", "Notes"],
    "tags": [],
}
HEADING_TYPES = re.compile(r"heading|chapter|section|folder", re.I)

_BLOCKS = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "table", "ul", "ol"}


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(el: ET.Element | None, name: str) -> list[ET.Element]:
    return [c for c in (el if el is not None else []) if _local(c.tag) == name]


def _child(el: ET.Element | None, name: str) -> ET.Element | None:
    found = _children(el, name)
    return found[0] if found else None


def _items(el: ET.Element | None) -> list[ET.Element]:
    return list(el) if el is not None else []


def _ref(el: ET.Element | None, container: str) -> str | None:
    """The text of the ``*-REF`` element under the child ``container`` of el (``<TYPE><SPEC-OBJECT-TYPE-REF>id``)."""
    holder = _child(el, container)
    if holder is None:
        return None
    for c in holder:
        if _local(c.tag).endswith("-REF") and c.text:
            return c.text.strip()
    return None


def xhtml_text(el: ET.Element) -> str:
    """The text of an XHTML value, as plain Markdown-ish text: blocks on their own lines, list items as '- '."""
    out: list[str] = []

    def walk(node: ET.Element) -> None:
        name = _local(node.tag).lower()
        if name in _BLOCKS and out and not out[-1].endswith("\n"):
            out.append("\n")
        if name == "li":
            out.append("- ")
        if name in ("b", "strong"):
            out.append("**")
        if node.text:
            out.append(node.text)
        for c in node:
            walk(c)
            if c.tail:
                out.append(c.tail)
        if name in ("b", "strong"):
            out.append("**")
        if name in _BLOCKS and not (out and out[-1].endswith("\n")):
            out.append("\n")

    for c in el:
        walk(c)
    text = "".join(out)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _node_id(text: str) -> str:
    """A path component from a heading: its text, without '/' and with single spaces."""
    return " ".join(text.replace("/", "-").split())[:80] or "node"


class ReqifDocument:
    """What a ReqIF file holds: its spec objects with their values (by attribute LONG-NAME), and its specifications."""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}        # identifier -> {type, last_change, values{name: value}}
        self.specifications: list[tuple[str, list[tuple[str, list]]]] = []   # (name, [(object id, children)])
        self.titles: list[str] = []

    def read(self, data: bytes, name: str) -> None:
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            raise ReqError(f"'{name}' is not a ReqIF file: {e}") from e
        if _local(root.tag) != "REQ-IF":
            raise ReqError(f"'{name}' is not a ReqIF file (root element {_local(root.tag)})")
        header = _child(_child(root, "THE-HEADER"), "REQ-IF-HEADER")
        title = _child(header, "TITLE")
        if title is not None and title.text:
            self.titles.append(title.text.strip())
        content = _child(_child(root, "CORE-CONTENT"), "REQ-IF-CONTENT")
        if content is None:
            raise ReqError(f"'{name}': no CORE-CONTENT/REQ-IF-CONTENT")
        # enumeration values, attribute definitions and spec object types: by identifier
        enums: dict[str, str] = {}
        for dt in _items(_child(content, "DATATYPES")):
            for ev in _children(_child(dt, "SPECIFIED-VALUES"), "ENUM-VALUE"):
                enums[ev.get("IDENTIFIER", "")] = ev.get("LONG-NAME") or ev.get("IDENTIFIER", "")
        attrs: dict[str, str] = {}
        types: dict[str, str] = {}
        for st in _items(_child(content, "SPEC-TYPES")):
            types[st.get("IDENTIFIER", "")] = st.get("LONG-NAME") or st.get("IDENTIFIER", "")
            for ad in _items(_child(st, "SPEC-ATTRIBUTES")):
                attrs[ad.get("IDENTIFIER", "")] = ad.get("LONG-NAME") or ad.get("IDENTIFIER", "")
        for so in _children(_child(content, "SPEC-OBJECTS"), "SPEC-OBJECT"):
            values: dict[str, Any] = {}
            for av in _items(_child(so, "VALUES")):
                kind = _local(av.tag)
                definition = _ref(av, "DEFINITION")
                attr = attrs.get(definition or "", definition or "")
                if not attr:
                    continue
                if kind == "ATTRIBUTE-VALUE-XHTML":
                    holder = _child(av, "THE-VALUE")
                    values[attr] = xhtml_text(holder) if holder is not None else ""
                elif kind == "ATTRIBUTE-VALUE-ENUMERATION":
                    refs = [r.text.strip() for r in (_items(_child(av, "VALUES"))) if r.text]
                    values[attr] = [enums.get(r, r) for r in refs]
                else:
                    value = av.get("THE-VALUE")
                    if value is None:
                        holder = _child(av, "THE-VALUE")
                        value = holder.text if holder is not None else None
                    if value is not None:
                        values[attr] = value
            ident = so.get("IDENTIFIER", "")
            self.objects[ident] = {"type": types.get(_ref(so, "TYPE") or "", ""), "last_change": so.get("LAST-CHANGE", ""),
                                   "long_name": so.get("LONG-NAME", ""), "values": values}

        def hierarchy(el: ET.Element | None) -> list[tuple[str, list]]:
            out = []
            for sh in _children(_child(el, "CHILDREN"), "SPEC-HIERARCHY"):
                obj = _ref(sh, "OBJECT")
                if obj:
                    out.append((obj, hierarchy(sh)))
            return out

        for spec in _children(_child(content, "SPECIFICATIONS"), "SPECIFICATION"):
            self.specifications.append((spec.get("LONG-NAME") or spec.get("IDENTIFIER", "specification"), hierarchy(spec)))


def read_reqif(file: Path) -> ReqifDocument:
    """A .reqif file, or every .reqif file of a .reqifz archive."""
    doc = ReqifDocument()
    try:
        if zipfile.is_zipfile(file):
            with zipfile.ZipFile(file) as z:
                names = sorted(n for n in z.namelist() if n.lower().endswith(".reqif"))
                if not names:
                    raise ReqError(f"'{file}': no .reqif file in the archive")
                for n in names:
                    doc.read(z.read(n), f"{file.name}:{n}")
        else:
            doc.read(file.read_bytes(), file.name)
    except OSError as e:
        raise ReqError(f"cannot read the ReqIF file '{file}': {e}") from e
    except zipfile.BadZipFile as e:
        raise ReqError(f"'{file}': {e}") from e
    return doc


class ReqifBackend(ReqBackend):
    """Requirements of a ReqIF file of the tests repository (``.reqif`` or ``.reqifz``), as exported by DOORS, Polarion,
    Jama, Codebeamer and most requirements tools."""

    capabilities = frozenset({"children_query", "versions"})
    description = "Built in: a ReqIF export (.reqif or .reqifz) of any requirements tool, kept in the tests repository."
    options_help = {
        "file": "The ReqIF file (`.reqif`, or `.reqifz`: a zip of them), relative to the sources file.",
        "url": "A link to each requirement in its tool, `{id}` replaced by its id (and `{identifier}` by its ReqIF "
               "identifier).",
        "specifications": "Only these specifications (their names), each a top node of the tree. Default: all.",
        "id_attribute": "The attribute holding the requirement's id, as test definitions name it (default: the first "
                        "of ReqIF.ForeignID, ID, Identifier, Object Identifier... the object has; else its ReqIF "
                        "IDENTIFIER).",
        "title_attribute": "The attribute holding its title (default: ReqIF.Name, Title, Name...).",
        "description_attribute": "The attribute holding its text (default: ReqIF.Text, Description, Text, Object Text).",
        "heading_attribute": "The attribute holding a heading's text (default: ReqIF.ChapterName, Object Heading...).",
        "rationale_attribute": "The attribute holding its rationale (default: Rationale...).",
        "status_attribute": "The attribute holding its lifecycle status (default: Status, State...); `deleted` and "
                            "`rejected` make links to it problems.",
        "comments_attribute": "The attribute holding comments (default: Comments, Comment, Notes).",
        "tags_attribute": "An attribute whose values are its tags (an enumeration, or a comma-separated string).",
        "heading_types": "Spec object types (their names) that are headings: nodes of the tree, not requirements. "
                         "Default: a type named like heading, chapter, section or folder, or an object with a heading "
                         "and no text.",
        "requirement_types": "Only objects of these types are requirements (default: every object that is not a "
                             "heading).",
        "version_attribute": "The attribute holding its version, or `LAST-CHANGE` for the date of its last change "
                             "(default: a hash of its content: a test that verified it before a change shows "
                             "*changed since*).",
    }

    def __init__(self, options: dict[str, Any], credentials: dict[str, str] | None, base_dir: Path) -> None:
        super().__init__(options, credentials, base_dir)
        if not self.options.get("file"):
            raise ReqError("the reqif backend needs its 'file' option (the .reqif or .reqifz file)")
        self.file = (base_dir / str(self.options["file"])).resolve()
        self.doc = read_reqif(self.file)
        self.labels: dict[str, str] = {}
        self.order: list[str] = []
        self.reqs: dict[str, Requirement] = {}
        wanted = self.options.get("specifications")
        wanted = [wanted] if isinstance(wanted, str) else list(wanted or [])
        names = [name for name, _ in self.doc.specifications]
        unknown = [w for w in wanted if w not in names]
        if unknown:
            raise ReqError(f"'{self.file.name}': no specification {', '.join(map(repr, unknown))} "
                           f"(specifications: {', '.join(names) or 'none'})")
        taken: set[str] = set()
        for name, children in self.doc.specifications:
            if wanted and name not in wanted:
                continue
            node = self._unique(_node_id(name), taken)
            self._add_node(node, name)
            self._walk(children, node)

    # ── attributes ────────────────────────────────────────────────────

    def _value(self, obj: dict, field: str) -> Any:
        values = obj["values"]
        lower = {k.lower(): k for k in values}
        option = self.options.get(f"{field}_attribute")
        if option:
            if str(option).upper() == "LAST-CHANGE":
                return obj["last_change"]
            key = lower.get(str(option).lower())
            return values.get(key) if key else None
        for name in GUESS[field]:
            key = lower.get(name.lower())
            if key is not None and values[key] not in ("", None, []):
                return values[key]
        return None

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value).strip()

    def _is_heading(self, obj: dict) -> bool:
        types = self.options.get("heading_types")
        if types:
            return obj["type"] in ([types] if isinstance(types, str) else types)
        if HEADING_TYPES.search(obj["type"] or ""):
            return True
        return bool(self._text(self._value(obj, "heading"))) and not self._text(self._value(obj, "description"))

    def _is_requirement(self, obj: dict) -> bool:
        types = self.options.get("requirement_types")
        return not types or obj["type"] in ([types] if isinstance(types, str) else types)

    # ── tree ──────────────────────────────────────────────────────────

    @staticmethod
    def _unique(node: str, taken: set[str]) -> str:
        out, n = node, 2
        while out in taken:
            out, n = f"{node} ({n})", n + 1
        taken.add(out)
        return out

    def _add_node(self, path: str, label: str) -> None:
        if path not in self.labels:
            self.labels[path] = label
            self.order.append(path)

    def _walk(self, children: list, path: str) -> None:
        taken: set[str] = set()
        for ident, sub in children:
            obj = self.doc.objects.get(ident)
            if obj is None:
                continue
            if self._is_heading(obj):
                label = self._text(self._value(obj, "heading")) or self._text(self._value(obj, "title")) or obj["long_name"] or ident
                node = f"{path}/{self._unique(_node_id(label), taken)}"
                self._add_node(node, label)
                self._walk(sub, node)
                continue
            if self._is_requirement(obj):
                self._add_requirement(ident, obj, path)
            self._walk(sub, path)           # objects under a requirement (sub-requirements) stay in its node

    def _add_requirement(self, ident: str, obj: dict, path: str) -> None:
        rid = self._text(self._value(obj, "id")) or ident
        if rid in self.reqs:                # the same object in several specifications: its first place
            return
        tags = self._value(obj, "tags")
        tags = tags if isinstance(tags, list) else [t.strip() for t in self._text(tags).split(",") if t.strip()]
        req = Requirement(id=rid, path=path, title=self._text(self._value(obj, "title")),
                          description=self._text(self._value(obj, "description")),
                          rationale=self._text(self._value(obj, "rationale")),
                          comments=self._text(self._value(obj, "comments")),
                          status=self._text(self._value(obj, "status")), tags=[str(t) for t in tags])
        if self.options.get("version_attribute"):
            req.version = self._text(self._value(obj, "version"))
        if not req.version:
            content = json.dumps({k: v for k, v in req.as_dict().items() if k not in ("version", "url", "path")},
                                 sort_keys=True)
            req.version = hashlib.sha256(content.encode()).hexdigest()[:8]
        if self.options.get("url"):
            req.url = str(self.options["url"]).replace("{id}", rid).replace("{identifier}", ident)
        self.reqs[rid] = req

    def tree(self) -> list[Node]:
        roots: list[Node] = []
        index: dict[str, Node] = {}
        for path in self.order:
            parent, _, leaf = path.rpartition("/")
            node = Node(leaf, self.labels[path] if self.labels[path] != leaf else "")
            index[path] = node
            (index[parent].children if parent in index else roots).append(node)
        return roots

    def list(self, path: str = "", children: bool = False) -> list[Requirement]:
        return [r for r in self.reqs.values() if under(r.path, path, children)]

    def get(self, ids: Iterable[str]) -> dict[str, Requirement | None]:
        return {i: self.reqs.get(i) for i in ids}

    def revision(self) -> str | None:
        try:
            return hashlib.sha256(self.file.read_bytes()).hexdigest()[:12]
        except OSError:
            return None


GUESS["version"] = []
register_req_backend("reqif", ReqifBackend)
