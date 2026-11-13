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

"""Editor mode (README section 13.2): editing sessions on scratch clones, checks, helpers, push.

An editing session is a clone of the repository holding the lab folder, or one of
``server.workspaces``, in ``<storage.root>/editor/<session>/repo``: the sequencer's own copy is not
written, and a change reaches the lab when it is pushed, then reloaded on demand.

An *in-place* session (``local``) edits the folder itself instead, without a clone: the files are
saved where the sequencer reads them, with whatever else that working copy holds. The folder need
not be a git work tree; when it is, review and push commit and push from it. A lab change still
applies only on ``tsq lab reload``. One in-place session per repository at a time.

Everything the Editor does can be done with a text editor and git: it reads and writes the same
YAML files, and keeps them as written (``yamledit``).
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from pydantic import ValidationError

from testsuit.sequencer import registry, yamledit
from testsuit.sequencer.lab import Lab, git_state, load_lab
from testsuit.sequencer.util import SequencerError, now

SKIP_DIRS = {".git", ".work", ".current_datapack", "__pycache__", "node_modules", ".pytest_cache", ".venv"}
MAX_FILES = 2000
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def file_version(path: Path) -> str | None:
    """The version of a file as the Editor read it: a hash of its bytes (None: no such file). A save giving the
    version it started from is refused when the file changed since (another browser, a pull, a hand edit)."""
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    except (FileNotFoundError, IsADirectoryError):
        return None


class EditConflict(SequencerError):
    """A save refused: the file changed since the version the save started from. current: the file as it is now
    (read()), or None when it was deleted."""

    def __init__(self, rel: str, current: dict | None):
        super().__init__(f"'{rel}' was changed elsewhere since you opened it (another browser, a pull or a hand "
                         f"edit)" if current is not None else f"'{rel}' was deleted elsewhere since you opened it")
        self.current = current

ASKPASS = """#!/bin/sh
# Answers git and ssh prompts from the environment of one push (never stored, never logged).
case "$1" in
  *[Uu]sername*) printf '%s\\n' "$TSQ_GIT_USERNAME" ;;
  *) printf '%s\\n' "$TSQ_GIT_SECRET" ;;
esac
"""

# run-time keys of commands and steps (README section 8.3), for completion and help
RUNTIME_KEYS = {
    "slot.id": "the step's slot (setup configuration)",
    "slot.name": "its name",
    "slot.type": "its slot type",
    "slot.role": "its role",
    "slot.stage_slot": "its stage slot (setup configuration), '' for an ad hoc slot",
    "slot.component": "id of the lab component it is bound to",
    "slot.input_dir": "<remote_root>/<slot>/input: where deploy puts its inputs",
    "slot.output_dir": "<remote_root>/<slot>/output: what collect brings back",
    "slot.remote_root": "the remote root of its lab component (tsq_remote_root)",
    "slots.<id>": "the same keys for any slot of the run (slots.pilot.component)",
    "component.id": "the lab component the step's slot is bound to (inventory name)",
    "component.host": "its address (ansible_host, a name of tsq_hosts translated to its address)",
    "component.host_alias": "the tsq_hosts name its address is given by ( when it gives the address itself)",
    "component.label": "its label",
    "component.role": "its role",
    "component.extends": "the library component it extends (its implementation)",
    "component.slot": "the slot it is bound to",
    "component.stage": "its lab stage",
    "component.vars": "every inventory variable of it (component.vars.tsq_start_args, component.vars.custom_grp.keyA...)",
    "component.input_dir": "same as slot.input_dir",
    "component.output_dir": "same as slot.output_dir",
    "component.remote_root": "its remote root (tsq_remote_root)",
    "component.state": "its monitored state",
    "remote_root": "the remote root of the step's lab component",
    "run.id": "run id", "run.dir": "run folder on the sequencer host", "run.priority": "run priority",
    "phase.id": "current phase",
    "stage_slots.<id>.stage": "the lab stage a stage slot of the setup is bound to",
    "stage.roles.<role>": "the lab component of that role in the lab stage of the step's component: id, host, role, "
                          "label, vars (stage.roles.stage_server.host)",
    "stage.components.<host>": "a lab component of that lab stage by inventory name (also stage.id, "
                               "stage.type, stage.vars)",
    "lab.hosts.<name>": "the address of a name of the lab's tsq_hosts table (lab.hosts.bench-pi)",
    "lab.components.<host>": "any host of the lab inventory: id, host, role, label, vars "
                             "(lab.components.psu_1.vars.ansible_host)",
    "via.host": "in a status probe with 'via': the component it runs on (also via.id, via.vars)",
    "params.<name>": "parameters of the sub-pipeline (uses ... with:)",
    "succeeded('<step>')": "in 'if': the step succeeded (also failed, errored, skipped, verdict)",
}


@dataclass
class EditSession:
    id: str
    repo: str                       # 'lab', 'workspace0'...
    kind: str                       # 'lab' or 'workspace'
    source: str                     # the folder edited, in the sequencer's copy
    dir: str                        # session folder: repo/ (the clone), generated/
    sub: str                        # the edited folder, relative to the clone root
    branch: str
    base: str                       # commit the session started from (or last pushed)
    upstream: str | None
    created_at: str = field(default_factory=now)
    pushed: list[dict] = field(default_factory=list)
    local: bool = False             # in place: edits the folder itself, no clone
    local_root: str = ""            # in place: the git work tree root, or the folder when it is not in one
    git: bool = True                # in place: whether local_root is a git work tree

    @property
    def root(self) -> Path:
        return Path(self.local_root) if self.local else Path(self.dir) / "repo"

    @property
    def folder(self) -> Path:
        return (self.root / self.sub).resolve()

    def describe(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "dir"} | {"folder": self.sub or "."}


def _git(cwd: Path, *args: str, env: dict | None = None, check: bool = True, timeout: float = 120) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})})
    if check and result.returncode:
        raise SequencerError(f"git {args[0]}: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def _repo_root(folder: Path) -> Path:
    try:
        return Path(_git(folder, "rev-parse", "--show-toplevel").strip())
    except (SequencerError, FileNotFoundError, NotADirectoryError) as e:
        raise SequencerError(f"'{folder}' is not in a git work tree: the Editor edits git repositories") from e


def _pydantic_problems(e: ValidationError, file: str, text: str, prefix: list | None = None) -> list[dict]:
    problems = []
    for err in e.errors():
        path = list(prefix or []) + [p for p in err["loc"] if not (isinstance(p, str) and p.startswith("function-"))]
        where = yamledit.locate(text, path) or (1, 1)
        problems.append({"file": file, "line": where[0], "column": where[1], "severity": "error",
                         "message": f"{'.'.join(map(str, path)) or 'document'}: {err['msg']}"})
    return problems


def _find_line(text: str, message: str) -> tuple[int, int]:
    """Best guess of the line a message is about: the first quoted name it mentions, as a key or an id."""
    for name in re.findall(r"'([^'/\s]{1,80})'", message):
        for pattern in (rf"^\s*-?\s*\{{?\s*id:\s*['\"]?{re.escape(name)}\b", rf"^\s*{re.escape(name)}\s*:",
                        rf"\b{re.escape(name)}\b"):
            for n, line in enumerate(text.splitlines(), 1):
                if re.search(pattern, line):
                    return n, max(1, len(line) - len(line.lstrip()) + 1)
    return 1, 1


def _flatten_keys(data: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(data, dict) and data:
        out = []
        for k, v in data.items():
            out += _flatten_keys(v, f"{prefix}.{k}" if prefix else str(k))
        return out
    return [(prefix, data)]


logger = logging.getLogger("testsuit.sequencer")


class _LiveLog(io.TextIOBase):
    """Output of a generation, readable line by line while it is written (from another thread)."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._partial = ""
        self._lock = threading.Lock()

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        with self._lock:
            parts = (self._partial + text.replace("\r\n", "\n").replace("\r", "\n")).split("\n")
            self._partial = parts.pop()
            self._lines.extend(parts)
        return len(text)

    def lines(self, since: int = 0) -> list[str]:
        with self._lock:
            return self._lines[since:]

    def text(self) -> str:
        with self._lock:
            return "\n".join(self._lines + ([self._partial] if self._partial else []))


def _signal_group(process: subprocess.Popen, name: str) -> None:
    """Send a signal to a process and to the commands it started (its own session)."""
    import signal
    try:
        if hasattr(os, "killpg"):
            os.killpg(process.pid, getattr(signal, name))
        else:                                       # no process groups (Windows): the process alone
            process.kill()
    except OSError:
        pass


def _describe(e: BaseException) -> str:
    """An exception as a message that is never empty."""
    text = str(e).strip()
    return f"{type(e).__name__}: {text}" if text else f"{type(e).__name__} (no message: see the log)"


class Editor:

    _output_lock = threading.Lock()                 # stdout is captured while the datapack tool prints

    def __init__(self, seq):
        self.seq = seq
        self.jobs: dict[str, dict] = {}                 # datapack generations in the background
        self.base = seq.config.root / "editor"
        self.base.mkdir(parents=True, exist_ok=True)
        self.askpass = self.base / "askpass.sh"
        self.askpass.write_text(ASKPASS)
        self.askpass.chmod(0o700)
        self.sessions: dict[str, EditSession] = {}
        self.published: dict[str, dict] = {}        # repo -> last push (commit, tag, author, message)
        for meta in sorted(self.base.glob("*/session.json")):
            try:
                session = EditSession(**json.loads(meta.read_text()))
                if session.root.is_dir():
                    self.sessions[session.id] = session
            except Exception:
                continue

    # ── repositories and sessions ────────────────────────────────────────

    def repositories(self) -> list[dict]:
        repos = [{"id": "lab", "kind": "lab", "label": "Lab inventory, roles and components",
                  "folder": str(self.seq.config.lab_folder), "git": git_state(self.seq.config.lab_folder)}]
        for i, folder in enumerate(self.seq.config.workspaces):
            repos.append({"id": f"workspace{i}", "kind": "workspace", "label": f"Tests: {folder.name}",
                          "folder": str(folder), "git": git_state(folder) if folder.exists() else None})
        return repos

    def _repo(self, repo_id: str) -> dict:
        for repo in self.repositories():
            if repo["id"] == repo_id:
                return repo
        raise SequencerError(f"no repository '{repo_id}' (lab, or workspace0.. from server.workspaces)")

    def open(self, repo_id: str, branch: str | None = None, local: bool = False) -> EditSession:
        if local:
            return self._open_in_place(repo_id, branch)
        repo = self._repo(repo_id)
        source = Path(repo["folder"])
        src_root = _repo_root(source)
        sub = source.resolve().relative_to(src_root.resolve()).as_posix() if source.resolve() != src_root.resolve() else ""
        session_id = uuid.uuid4().hex[:8]
        folder = self.base / session_id
        folder.mkdir(parents=True)
        root = folder / "repo"
        _git(folder, "clone", "--quiet", "--no-hardlinks", str(src_root), str(root))
        upstream = _git(src_root, "remote", "get-url", "origin", check=False).strip() or None
        if upstream:
            _git(root, "remote", "set-url", "origin", upstream)
            # what the sequencer's copy knows of the upstream branches, without network access
            _git(root, "fetch", "--quiet", str(src_root), "+refs/remotes/origin/*:refs/remotes/origin/*", check=False)
        else:
            upstream = str(src_root)
        if not branch:
            branch = _git(src_root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip()
        if not branch:                                # detached (a lab on a tag): the upstream default branch
            head = _git(src_root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False).strip()
            branch = head.split("/", 1)[1] if "/" in head else "main"
        remote_refs = _git(root, "branch", "-r", "--format=%(refname:short)").split()
        if f"origin/{branch}" in remote_refs:
            _git(root, "checkout", "--quiet", "-B", branch, f"origin/{branch}")
        else:
            _git(root, "checkout", "--quiet", "-B", branch)
        with open(root / ".git" / "info" / "exclude", "a") as f:
            f.write("\n.current_datapack/\n")
        session = EditSession(id=session_id, repo=repo_id, kind=repo["kind"], source=str(source), dir=str(folder),
                              sub=sub, branch=branch, base=_git(root, "rev-parse", "HEAD").strip(), upstream=upstream)
        self.sessions[session_id] = session
        self._save(session)
        return session

    def _open_in_place(self, repo_id: str, branch: str | None) -> EditSession:
        """A session on the folder itself. The one already open on that repository is returned as is."""
        for existing in self.sessions.values():
            if existing.local and existing.repo == repo_id:
                if branch and branch != existing.branch:
                    raise SequencerError(f"an in-place session works on the checked-out branch "
                                         f"('{existing.branch or 'detached'}'), not '{branch}'")
                return existing
        repo = self._repo(repo_id)
        source = Path(repo["folder"]).resolve()
        if not source.is_dir():
            raise SequencerError(f"no folder '{source}'")
        try:
            root, git = _repo_root(source).resolve(), True
        except SequencerError:
            root, git = source, False
        current = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip() if git else ""
        if branch and branch != current:
            raise SequencerError(f"an in-place session works on the checked-out branch ('{current or 'detached'}'), "
                                 f"not '{branch}': check it out first, or open a session on a clone")
        session_id = uuid.uuid4().hex[:8]
        folder = self.base / session_id
        folder.mkdir(parents=True)
        upstream = (_git(root, "remote", "get-url", "origin", check=False).strip() or None) if git else None
        if git:                                         # datapacks generated here leave their work folder
            exclude = Path(_git(root, "rev-parse", "--git-path", "info/exclude").strip())
            exclude = exclude if exclude.is_absolute() else root / exclude
            if ".current_datapack/" not in (exclude.read_text() if exclude.is_file() else ""):
                exclude.parent.mkdir(parents=True, exist_ok=True)
                with open(exclude, "a") as f:
                    f.write("\n.current_datapack/\n")
        session = EditSession(id=session_id, repo=repo_id, kind=repo["kind"], source=str(source), dir=str(folder),
                              sub=source.relative_to(root).as_posix() if source != root else "", branch=current,
                              base=_git(root, "rev-parse", "HEAD", check=False).strip() if git else "",
                              upstream=upstream, local=True, local_root=str(root), git=git)
        self.sessions[session_id] = session
        self._save(session)
        return session

    def _need_git(self, session: EditSession) -> None:
        if not session.git:
            raise SequencerError(f"'{session.source}' is not a git work tree: there is nothing to review or push")

    def _save(self, session: EditSession) -> None:
        (Path(session.dir) / "session.json").write_text(json.dumps(asdict(session), indent=1))

    def get(self, session_id: str) -> EditSession:
        if session_id not in self.sessions:
            raise SequencerError(f"no editing session '{session_id}'")
        return self.sessions[session_id]

    def close(self, session_id: str) -> None:
        """Drop the session: its clone, generated datapacks and notes. An in-place session's folder is kept."""
        session = self.sessions.pop(session_id, None)
        if session is not None:
            shutil.rmtree(session.dir, ignore_errors=True)

    def describe(self, session: EditSession) -> dict:
        if not session.git:
            return {**session.describe(), "changed": None, "head": None}
        status = _git(session.root, "status", "--porcelain", "--untracked-files=all", "--", session.sub or ".", check=False)
        return {**session.describe(), "changed": len([line for line in status.splitlines() if line.strip()]),
                "head": _git(session.root, "rev-parse", "HEAD", check=False).strip()}

    # ── files ────────────────────────────────────────────────────────────

    def path(self, session: EditSession, rel: str) -> Path:
        target = (session.folder / rel).resolve()
        if not target.is_relative_to(session.root.resolve()) or ".git" in target.relative_to(session.root.resolve()).parts:
            raise SequencerError(f"'{rel}' is outside the repository")
        return target

    def rel(self, session: EditSession, path: Path) -> str:
        return os.path.relpath(path.resolve(), session.folder).replace(os.sep, "/")

    def tree(self, session: EditSession) -> list[dict]:
        files = []
        for dirpath, dirnames, filenames in os.walk(session.folder):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info"))
            for name in sorted(filenames):
                path = Path(dirpath) / name
                rel = self.rel(session, path)
                files.append({"path": rel, "size": path.stat().st_size, "kind": self.kind(session, rel)})
                if len(files) >= MAX_FILES:
                    return files
        return files

    def kind(self, session: EditSession, rel: str, text: str | None = None) -> str:
        """What a file is: inventory, library, setup, testdef, subpipelines, dictionary, template, or other."""
        path = self.path(session, rel)
        if session.kind == "lab" and path == self._inventory(session):
            return "inventory"
        if path.suffix not in (".yml", ".yaml", ".j2", ".jinja", ".tmpl") and "templates" not in path.parts:
            return "other"
        if "templates" in Path(rel).parts:
            return "template"
        if text is None:
            try:
                text = path.read_text(encoding="utf-8") if path.is_file() and path.stat().st_size < (2 << 20) else ""
            except (UnicodeDecodeError, OSError):
                return "other"
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            return "template" if "{%" in text or "{{" in text else "yaml"
        if not isinstance(data, dict):
            return "yaml"
        if "all" in data and isinstance(data.get("all"), dict):
            return "inventory"
        if "setup_file" in data or "pipeline" in data:
            return "testdef"
        if "configurations" in data and ({"slot_types", "component_types", "datarepositories"} & set(data)):
            return "setup"
        if ({"roles", "components"} & set(data)) and not set(data) - {"roles", "components"}:
            return "library"
        if "subpipelines" in data:
            return "subpipelines"
        return "dictionary"

    def read(self, session: EditSession, rel: str) -> dict:
        path = self.path(session, rel)
        if not path.is_file():
            raise SequencerError(f"no file '{rel}'")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise SequencerError(f"'{rel}' is not a text file") from e
        kind = self.kind(session, rel, text)
        result = {"path": rel, "kind": kind, "content": text, "version": file_version(path)}
        if kind in ("inventory", "library", "setup", "testdef", "subpipelines", "dictionary"):
            try:
                result["data"] = yaml.safe_load(text)
                result["aliases"] = "*" in text and bool(re.search(r"(^|\s)\*[A-Za-z_]", text))
            except yaml.YAMLError:
                pass
        return result

    _write_lock = threading.Lock()          # the version check and the write, one save at a time

    def version(self, session: EditSession, rel: str) -> str | None:
        """The version of a file of the session (file_version), None when there is none."""
        return file_version(self.path(session, rel))

    def write(self, session: EditSession, rel: str, content: str | None = None, data: Any = None,
              at: list | None = None, base: str | None = None) -> dict:
        """Write a file: its text (content), or its data merged onto it (data, at a path) keeping comments.

        :param base: the version the edit started from (read()): EditConflict if the file is another one now.
            None writes whatever the file is.
        """
        path = self.path(session, rel)
        with self._write_lock:
            if base is not None and file_version(path) != base:
                raise EditConflict(rel, self.read(session, rel) if path.is_file() else None)
            if content is None:
                current = path.read_text(encoding="utf-8") if path.is_file() else ""
                content = yamledit.merge(current, data, at)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        result = self.read(session, rel)
        result["problems"] = self.check_file(session, rel)
        return result

    MAX_UPLOAD = 2 << 20

    def upload(self, session: EditSession, rel: str, data: bytes) -> dict:
        """Write an image of the session (a component's ``tsq_icon``), as it was uploaded."""
        from testsuit.sequencer.inventory import ICON_EXTENSIONS
        if Path(rel).suffix.lower() not in ICON_EXTENSIONS:
            raise SequencerError(f"'{rel}': only images can be uploaded ({', '.join(ICON_EXTENSIONS)})")
        if len(data) > self.MAX_UPLOAD:
            raise SequencerError(f"'{rel}': {len(data)} bytes, more than {self.MAX_UPLOAD >> 20} MB")
        path = self.path(session, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": self.rel(session, path), "size": len(data)}

    def raw(self, session: EditSession, rel: str) -> Path:
        """A file of the session as it is (an image to show)."""
        path = self.path(session, rel)
        if not path.is_file():
            raise SequencerError(f"no file '{rel}'")
        return path

    def delete(self, session: EditSession, rel: str) -> None:
        path = self.path(session, rel)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    def rename(self, session: EditSession, rel: str, to: str) -> None:
        source, target = self.path(session, rel), self.path(session, to)
        if target.exists():
            raise SequencerError(f"'{to}' already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)

    # ── lab side ─────────────────────────────────────────────────────────

    def _inventory(self, session: EditSession) -> Path:
        rel = os.path.relpath(self.seq.config.inventory_file, self.seq.config.lab_folder)
        return (session.folder / rel).resolve()

    def session_lab(self, session: EditSession) -> Lab:
        """The lab as the session's files define it (raises SequencerError when they are invalid)."""
        return load_lab(session.folder, self._inventory(session), require_tag=False)

    def lab_for(self, session: EditSession) -> tuple[Lab, str | None]:
        """The lab the session's helpers and checks use: its own files for a lab session, the running lab
        for a workspace. A lab session whose files are inconsistent (a role or a component just renamed)
        still gets its own files, read without that check; the running lab only when they do not load at all."""
        if session.kind == "lab":
            try:
                return self.session_lab(session), None
            except SequencerError as e:
                try:
                    return load_lab(session.folder, self._inventory(session), require_tag=False, strict=False), str(e)
                except Exception:
                    return self.seq.lab, str(e)
        return self.seq.lab, None

    def rename_references(self, session: EditSession, kind: str, old: str, new: str, skip: str | None = None) -> list[str]:
        """After a lab role or a library component is renamed in its file: the references to it in the other
        files of the session follow, where its library is used: the inventories reading it (``tsq_role`` for a
        role, ``tsq_extends`` for a component) and the other library files of its folders (their ``extends``,
        and for a role the ``roles`` its components list).
        Returns the files changed. (A datapack type of a setup is only referred to in its own file.)"""
        if kind not in ("role", "component"):
            raise SequencerError(f"rename: 'role' or 'component', not '{kind}'")
        host_key = "tsq_role" if kind == "role" else "tsq_extends"
        section = "roles" if kind == "role" else "components"

        def follow(node: Any, keys: tuple[str, ...]) -> bool:
            changed = False
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in keys and v == old:
                        node[k] = new
                        changed = True
                    elif k in keys and isinstance(v, list) and old in v:
                        node[k] = [new if x == old else x for x in v]
                        changed = True
                    else:
                        changed = follow(v, keys) or changed
            elif isinstance(node, list):
                for v in node:
                    changed = follow(v, keys) or changed
            return changed

        from testsuit.sequencer.inventory import load_inventory
        files = self.tree(session)
        renamed = self.path(session, skip).resolve() if skip else None
        targets: set[str] = set()
        for f in files:
            if f["kind"] != "inventory":
                continue
            try:
                folders = load_inventory(self.path(session, f["path"])).library_folders
            except Exception:
                continue
            if renamed is None or any(renamed.is_relative_to(folder) for folder in folders):
                targets.add(f["path"])
                targets |= {t["path"] for t in files if t["kind"] == "library"
                            and any(self.path(session, t["path"]).resolve().is_relative_to(folder) for folder in folders)}
        changed = []
        for f in files:
            if f["path"] == skip or f["path"] not in targets:
                continue
            try:
                data = yaml.safe_load(self.path(session, f["path"]).read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if f["kind"] == "inventory":
                hit = follow(data, (host_key,))
            else:
                hit = follow((data or {}).get(section), ("extends",))
                if kind == "role":
                    hit = follow((data or {}).get("components"), ("roles",)) or hit
            if hit:
                self.write(session, f["path"], data=data)
                changed.append(f["path"])
        return changed

    @staticmethod
    def describe_library(lab: Lab) -> dict:
        """Every role with the commands it lists (and the role each comes from), every library component with
        what it implements (and the component each comes from), status and self-test."""
        lib = lab.library

        def self_roles(name: str) -> list[str]:
            try:
                return lib.component_roles(name)
            except SequencerError:
                return list(lib.components[name].roles or [])

        roles = {}
        for name, r in lib.roles.items():
            try:
                chain = [x.name for x in lib.role_chain(name)]
                commands = lib.role_commands(name)
                inputs = lib.role_inputs(name)
            except SequencerError:                        # extends a role that is not there (renamed?): itself only
                chain = [name]
                commands = {c: {"description": d, "from": name} for c, d in r.commands.items()}
                inputs = [{**i, "from": name} for i in r.inputs]
            roles[name] = {"extends": r.extends, "description": r.description, "source": r.source, "chain": chain,
                           "commands": commands, "own_commands": sorted(r.commands), "inputs": inputs,
                           "used_by": [c.id for c in lab.inventory.components.values() if c.role == name],
                           "implemented_by": [c for c in lib.components if name in self_roles(c)],
                           "abstract": any(o.extends == name for o in lib.roles.values())}
        components = {}
        for name, c in lib.components.items():
            try:
                chain = lib.component_chain(name)
            except SequencerError:
                chain = [c]
            commands, status, selftest, transport = {}, None, {}, None
            for link in chain:
                for cmd, spec in link.commands.items():
                    commands[cmd] = {"spec": spec, "from": link.name}
                if link.status is not None:
                    status = {"spec": link.status, "from": link.name}
                for check in link.selftest:
                    selftest[check["id"]] = {"spec": check, "from": link.name}
                transport = link.transport or transport
            try:
                role_missing = lib.check_component(name)
            except SequencerError:
                role_missing = {}
            components[name] = {"extends": c.extends, "roles": self_roles(name), "own_roles": c.roles,
                                "role_missing": role_missing, "description": c.description, "source": c.source,
                                "chain": [link.name for link in chain], "transport": transport or "ssh",
                                "commands": commands, "status": status, "selftest": list(selftest.values()),
                                "own_commands": sorted(c.commands),
                                "used_by": [h.id for h in lab.inventory.components.values() if h.extends == name]}
        return {"roles": roles, "components": components}

    @classmethod
    def describe_lab(cls, lab: Lab) -> dict:
        inv = lab.inventory
        stage_types: dict[str, list[str]] = {}
        stage_type_roles: dict[str, dict[str, list[str]]] = {}
        # the most slots of a role one stage of the type can take: its components of that role or extending it
        stage_type_role_counts: dict[str, dict[str, int]] = {}
        for s in inv.stages.values():
            stage_types.setdefault(s.type, []).append(s.id)
            counts: dict[str, int] = {}
            for comp_id in s.components:
                comp = inv.components[comp_id]
                if comp.role:
                    stage_type_roles.setdefault(s.type, {}).setdefault(comp.role, []).append(comp_id)
                for role in lab.roles_of(comp):
                    counts[role] = counts.get(role, 0) + 1
            best = stage_type_role_counts.setdefault(s.type, {})
            for role, n in counts.items():
                best[role] = max(best.get(role, 0), n)
        adhoc_roles: dict[str, list[str]] = {}
        for c in inv.adhoc:
            if c.role:
                adhoc_roles.setdefault(c.role, []).append(c.id)
        library = cls.describe_library(lab)
        hosts = {}
        for c in inv.components.values():
            info = {"role": c.role, "roles": lab.roles_of(c), "tags": c.tags, "role_given": str(c.vars.get("tsq_role") or ""), "extends": c.extends, "host": c.host, "host_alias": c.host_alias, "lock": c.lock, "label": c.label,
                    "stages": c.stages, "transport": str(c.vars.get("tsq_transport") or "")}
            try:
                stage = inv.stages[c.stages[0]] if c.stages else None
                resolved = lab.resolve(c, stage)
                offered = lab.library.role_commands(c.role) if c.role in lab.library.roles else {}
                info["role_choices"] = lab.library.component_roles(c.id, {**lab.hosts})
                info.update(chain=resolved.chain,
                            commands={k: {"spec": v, "from": resolved.command_sources.get(k, "")}
                                      for k, v in resolved.commands.items()},
                            status=resolved.status, selftest=[x["id"] for x in resolved.selftest],
                            missing=sorted(set(offered) - set(resolved.commands)),
                            extra=lab.library.extra_commands(c, resolved, lab.hosts))
            except SequencerError as e:
                info["error"] = str(e)
            hosts[c.id] = info
        return {
            "roles": library["roles"], "library": library["components"],
            "stage_types": stage_types, "stage_type_roles": stage_type_roles,
            "stage_type_role_counts": stage_type_role_counts, "adhoc_roles": adhoc_roles,
            "stages": {s.id: {"type": s.type, "tags": s.tags, "components": s.components, "commands": sorted(s.commands)}
                       for s in inv.stages.values()},
            "components": hosts, "hosts": inv.hosts,
            "library_folders": [str(f) for f in inv.library_folders],
        }

    # ── workspace side ───────────────────────────────────────────────────

    def scan_workspace(self, folder: Path, rel_to: Path) -> dict:
        """Test definitions, setups, sub-pipelines, dictionaries and templates of a folder."""
        result = {"testdefs": [], "setups": [], "subpipelines": {}, "subpipeline_files": [], "dictionaries": [],
                  "templates": []}
        if not folder.is_dir():
            return result
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info"))
            for name in sorted(filenames):
                path = Path(dirpath) / name
                rel = os.path.relpath(path, rel_to).replace(os.sep, "/")
                if "templates" in Path(rel).parts:
                    result["templates"].append(rel)
                    continue
                if path.suffix not in (".yml", ".yaml") or path.stat().st_size > (2 << 20):
                    continue
                try:
                    data = yaml.safe_load(path.read_text(encoding="utf-8"))
                except (yaml.YAMLError, UnicodeDecodeError):
                    continue
                if not isinstance(data, dict):
                    continue
                if "setup_file" in data or "pipeline" in data:
                    setup = (path.parent / str(data.get("setup_file", ""))).resolve()
                    pipeline = data.get("pipeline") if isinstance(data.get("pipeline"), dict) else {}
                    for sub, spec in (pipeline.get("subpipelines") or {}).items():
                        result["subpipelines"].setdefault(sub, {"file": rel, **self._sub_summary(spec)})
                    result["testdefs"].append({
                        "path": rel, "id": data.get("id"), "name": data.get("name"),
                        "setup_file": os.path.relpath(setup, rel_to).replace(os.sep, "/") if data.get("setup_file") else None,
                        "setup_config": data.get("setup_config"),
                        "phases": [p.get("id") for p in pipeline.get("phases") or [] if isinstance(p, dict)],
                        "include": pipeline.get("include") or []})
                elif "configurations" in data and ({"slot_types", "component_types", "datarepositories"} & set(data)):
                    result["setups"].append(self._setup_summary(rel, data))
                elif "subpipelines" in data and isinstance(data["subpipelines"], dict):
                    result["subpipeline_files"].append(rel)
                    for sub, spec in data["subpipelines"].items():
                        result["subpipelines"].setdefault(sub, {"file": rel, **self._sub_summary(spec)})
                elif "all" not in data and not (({"roles", "components"} & set(data))
                                                 and not set(data) - {"roles", "components"}):
                    result["dictionaries"].append(rel)
        return result

    @staticmethod
    def _sub_summary(spec: Any) -> dict:
        spec = spec if isinstance(spec, dict) else {}
        return {"params": spec.get("params") or {},
                "steps": [s.get("id") for s in spec.get("steps") or [] if isinstance(s, dict)]}

    @staticmethod
    def _setup_summary(rel: str, data: dict) -> dict:
        configurations = {}
        for name, cfg in (data.get("configurations") or {}).items():
            cfg = cfg if isinstance(cfg, dict) else {}
            stage_slots = {s: (v.get("type") if isinstance(v, dict) else v) for s, v in (cfg.get("stage_slots") or {}).items()}
            slots = {c: {k: v.get(k) for k in ("name", "type", "configuration", "stage_slot", "role", "needs")}
                     for c, v in (cfg.get("slots") or {}).items() if isinstance(v, dict)}
            configurations[name] = {"stage_slots": stage_slots, "slots": slots}
        types = {}
        for name, t in (data.get("slot_types") or {}).items():
            t = t if isinstance(t, dict) else {}
            types[name] = {"configurations": sorted(t.get("configurations") or {}),
                           "description": t.get("description", ""), "role": t.get("role", "")}
        datasets = [{"id": ds.get("id"), "version": ds.get("version")}
                    for repo in data.get("datarepositories") or [] for src in repo.get("datasources") or []
                    for ds in src.get("datasets") or [] if isinstance(ds, dict)]
        return {"path": rel, "id": data.get("id"), "name": data.get("name"), "configurations": configurations,
                "slot_types": types, "datasets": datasets, "dictionaries": data.get("dictionaries") or []}

    def context(self, session: EditSession) -> dict:
        """What the helpers of the forms and the YAML view need (README section 13.2)."""
        from testsuit.datatools.datapack import generators, importers, processors
        lab, lab_error = self.lab_for(session)
        if session.kind == "workspace":
            workspace = self.scan_workspace(session.folder, session.folder)
        else:                                          # what the datapacks of the workspaces ask of the lab
            workspace = {"testdefs": [], "setups": [], "subpipelines": {}}
            for folder in self.seq.config.workspaces:
                found = self.scan_workspace(folder, folder)
                workspace["setups"] += found["setups"]
        described = self.describe_lab(lab)
        for t in [*described["roles"].values(), *described["library"].values()]:   # where each is defined, as the user sees it
            source = Path(t["source"]) if t["source"].startswith("/") else None
            if source is not None and source.is_relative_to(session.folder):
                t["source"] = self.rel(session, source)
            elif source is not None and source.is_relative_to(self.seq.config.lab_folder):
                t["source"] = "lab: " + os.path.relpath(source, self.seq.config.lab_folder)
        described["library_folders"] = [self.rel(session, Path(f)) if Path(f).is_relative_to(session.folder) else f
                                         for f in described["library_folders"]]
        return {
            "session": self.describe(session),
            "lab": described, "lab_error": lab_error,
            "workspace": workspace,
            "transports": sorted({"local", "ssh", *registry.transports}),
            "step_types": sorted(registry.step_types), "ready_checks": sorted(registry.ready_checks),
            "state_probes": sorted(registry.state_probes),
            "importers": sorted(importers._importers), "generators": sorted(generators._generators),
            "processors": sorted(processors._processors),
            "runtime_keys": RUNTIME_KEYS,
            "inventory": self.rel(session, self._inventory(session)) if session.kind == "lab" else None,
        }

    # ── checks ───────────────────────────────────────────────────────────

    def check_file(self, session: EditSession, rel: str) -> list[dict]:
        """Problems of one file: YAML syntax, then what it means for the lab or the datapacks."""
        path = self.path(session, rel)
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
        kind = self.kind(session, rel, text)
        if kind in ("other", "template"):
            return []
        error = yamledit.syntax_error(text)
        if error:
            return [{"file": rel, "severity": "error", **error}]
        try:
            if kind in ("inventory", "library"):
                return self.check_lab(session, only=rel)
            if kind == "testdef":
                return self.check_testdef(session, rel)
            if kind == "setup":
                return self.check_setup(session, rel)
            if kind == "subpipelines":
                return self.check_subpipelines(session, rel, text)
        except SequencerError as e:
            line, column = _find_line(text, str(e))
            return [{"file": rel, "line": line, "column": column, "severity": "error", "message": str(e)}]
        return []

    def _message_file(self, session: EditSession, message: str, default: str) -> tuple[str, str]:
        """The session file a message names, and the message with session-relative paths."""
        found = default
        for path in sorted(set(re.findall(r"'(/[^']+)'", message)), key=len, reverse=True):
            p = Path(path)
            if p.is_relative_to(session.root):
                rel = self.rel(session, p)
                message = message.replace(path, rel)
                if p.is_file():
                    found = rel
        return found, message

    def check_lab(self, session: EditSession, only: str | None = None) -> list[dict]:
        """Like ``tsq lab reload``: the inventory and its library load, every host implements its role; plus
        the commands a host implements that its role does not list, and what workspace datapacks would miss."""
        inventory = self._inventory(session)
        inv_rel = self.rel(session, inventory)
        try:
            lab = self.session_lab(session)
        except SequencerError as e:
            file, message = self._message_file(session, str(e), inv_rel)
            text = self.path(session, file).read_text(encoding="utf-8", errors="replace") if self.path(session, file).is_file() else ""
            line, column = _find_line(text, message)
            return [{"file": file, "line": line, "column": column, "severity": "error", "message": message}]
        problems = []
        text = inventory.read_text(encoding="utf-8")
        for host, extra in lab.extra_commands.items():
            line, column = _find_line(text, f"'{host}'")
            role = lab.inventory.components[host].role
            problems.append({"file": inv_rel, "line": line, "column": column, "severity": "warning",
                             "message": f"component '{host}' implements {', '.join(extra)}, which its role '{role}' "
                                        "does not list: datapacks cannot use them (add them to the role?)"})
        from testsuit.sequencer.labview import icon_of
        for host, component in lab.inventory.components.items():
            icon = icon_of(lab, component)
            if icon and icon.get("error"):
                line, column = _find_line(text, component.icon)
                problems.append({"file": inv_rel, "line": line, "column": column, "severity": "warning",
                                 "message": f"component '{host}': {icon['error']} (the Lab view draws a plain box)"})
        for folder in self.seq.config.workspaces:
            for setup in self.scan_workspace(folder, folder)["setups"]:
                for cfg_name, cfg in setup["configurations"].items():
                    needs = self._needs_or_none(setup, cfg_name, folder)
                    if needs is None:
                        continue
                    from testsuit.sequencer.intake import check_against_lab
                    for problem in check_against_lab(SimpleNamespace(needs=needs), lab):
                        line, column = _find_line(text, problem)
                        problems.append({"file": inv_rel, "line": line, "column": column, "severity": "warning",
                                         "message": f"setup {setup['id']} ({cfg_name}), in {folder.name}: {problem}"})
        return [p for p in problems if only is None or p["file"] == only or only == inv_rel] if only else problems

    def _needs_or_none(self, setup: dict, cfg_name: str, folder: Path):
        from testsuit.sequencer.model import needs_from_setup
        try:
            raw = yaml.safe_load((folder / setup["path"]).read_text(encoding="utf-8"))
            return needs_from_setup(raw, cfg_name)
        except Exception:
            return None

    def _testdef_parts(self, session: EditSession, rel: str, setup_config: str | None = None):
        """(raw test definition, its text, setup raw, setup path, needs, pipeline, flat phases) of a test definition."""
        from testsuit.sequencer.model import load_pipeline, needs_from_setup, validate_pipeline
        path = self.path(session, rel)
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        setup_file = str(raw.get("setup_file") or "")
        if not setup_file:
            raise SequencerError("the test definition has no 'setup_file'")
        if "{{" in setup_file:
            raise SequencerError(f"setup_file '{setup_file}' is a template: cannot check it here")
        setup_path = (path.parent / setup_file).resolve()
        if not setup_path.is_file():
            raise SequencerError(f"setup file '{setup_file}' not found")
        setup_raw = yaml.safe_load(setup_path.read_text(encoding="utf-8")) or {}
        needs = needs_from_setup(setup_raw, setup_config or str(raw.get("setup_config") or ""))
        pipeline = load_pipeline(raw.get("pipeline"), path.parent)
        flats = validate_pipeline(pipeline, needs)
        return raw, text, setup_raw, setup_path, needs, pipeline, flats

    def check_testdef(self, session: EditSession, rel: str) -> list[dict]:
        from testsuit.datatools.datapack.model import Testdef
        from testsuit.sequencer.intake import check_against_lab
        from testsuit.sequencer.model import Pipeline
        text = self.path(session, rel).read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        problems = []
        try:
            Testdef.model_validate({**raw, "pipeline": None})
        except ValidationError as e:
            problems += _pydantic_problems(e, rel, text)
        try:
            Pipeline.model_validate(raw.get("pipeline") or {})
        except ValidationError as e:
            return problems + _pydantic_problems(e, rel, text, ["pipeline"])
        if problems:
            return problems
        try:
            raw, text, _, _, needs, pipeline, flats = self._testdef_parts(session, rel)
        except SequencerError as e:
            line, column = _find_line(text, str(e))
            return [{"file": rel, "line": line, "column": column, "severity": "error", "message": str(e)}]
        lab, _ = self.lab_for(session)
        for problem in check_against_lab(SimpleNamespace(needs=needs), lab):
            problems.append({"file": rel, "line": 1, "column": 1, "severity": "warning", "message": f"lab: {problem}"})
        flat = self._flatten(lab, needs, pipeline, flats)
        for problem in flat["problems"]:
            line, column = _find_line(text, problem)
            problems.append({"file": rel, "line": line, "column": column, "severity": "error", "message": problem})
        return problems

    def _mismatches(self, session: EditSession, testdef_rel: str, setup_config: str | None = None,
                    setup_path: Path | None = None) -> list[str]:
        """Every stage and slot a test definition's pipeline names that its setup
        configuration does not define (empty when either cannot be read: other checks say why).
        With setup_path, only for a test definition using that setup file."""
        from testsuit.sequencer.model import load_pipeline, needs_from_setup, pipeline_mismatches
        try:
            path = self.path(session, testdef_rel)
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            setup_file = str(raw.get("setup_file") or "")
            if not setup_file or "{{" in setup_file:
                return []
            own = (path.parent / setup_file).resolve()
            if setup_path is not None and own != setup_path.resolve():
                return []
            needs = needs_from_setup(yaml.safe_load(own.read_text(encoding="utf-8")) or {},
                                     setup_config or str(raw.get("setup_config") or ""))
            return pipeline_mismatches(load_pipeline(raw.get("pipeline"), path.parent), needs)
        except Exception:
            return []

    def run_problems(self, session: EditSession, testdef_rel: str, setup_config: str | None = None) -> list[str]:
        """The pipeline and lab checks of the synthesis, on their own (the checks before generating, step by step)."""
        try:
            _, testdef, _ = self._builder(session, testdef_rel, setup_config)
        except SequencerError:
            raise
        except Exception as e:
            raise SequencerError(str(e)) from e
        return self._run_problems(session, testdef_rel, testdef.setup_config)

    def _run_problems(self, session: EditSession, testdef_rel: str, setup_config: str) -> list[str]:
        """What would make the sequencer refuse the datapack when it is posted: the pipeline against the
        setup configuration (phases, stage slots and slots, step targets), and what the lab cannot provide."""
        from testsuit.sequencer.intake import check_against_lab
        mismatches = self._mismatches(session, testdef_rel, setup_config)
        if mismatches:
            return [f"pipeline: {m}" for m in mismatches]
        try:
            _, _, _, _, needs, pipeline, flats = self._testdef_parts(session, testdef_rel, setup_config)
        except SequencerError as e:
            return [f"pipeline: {e}"]
        except Exception as e:                    # an invalid pipeline (pydantic), a missing include...
            return [f"pipeline: {_describe(e)}"]
        lab, _ = self.lab_for(session)
        problems = [f"lab: {p}" for p in check_against_lab(SimpleNamespace(needs=needs), lab)]
        return problems + [f"pipeline: {p}" for p in self._flatten(lab, needs, pipeline, flats)["problems"]]

    def check_setup(self, session: EditSession, rel: str) -> list[dict]:
        from testsuit.datatools.datapack.datapack_tools import _find_sources
        from testsuit.datatools.datapack.model import FORMER_CONFIG, FORMER_SETUP, FORMER_SLOT, Setup
        from testsuit.sequencer.intake import check_against_lab
        from testsuit.sequencer.model import needs_from_setup
        path = self.path(session, rel)
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        problems = []
        former = [([old], f"'{old}' is now '{new}'") for old, new in FORMER_SETUP.items() if old in raw]
        former += [(["configurations", name, old], f"configuration '{name}': '{old}' is now '{new}'")
                   for name, cfg in (raw.get("configurations") or {}).items() if isinstance(cfg, dict)
                   for old, new in FORMER_CONFIG.items() if old in cfg]
        former += [(["configurations", name, "slots", slot_id, old], f"slot '{slot_id}': '{old}' is now '{new}'")
                   for name, cfg in (raw.get("configurations") or {}).items() if isinstance(cfg, dict)
                   for slot_id, slot in (cfg.get("slots") or {}).items() if isinstance(slot, dict)
                   for old, new in FORMER_SLOT.items() if old in slot]
        if former:
            return [{"file": rel, "line": w[0], "column": w[1], "severity": "error", "message": message}
                    for keys, message in former for w in [yamledit.locate(text, keys) or (1, 1)]]
        try:
            Setup.model_validate(raw)
        except ValidationError as e:
            return _pydantic_problems(e, rel, text)
        lab, _ = self.lab_for(session)
        types = raw.get("slot_types") or {}
        for name, t in types.items():
            role = str(t.get("role") or "")
            expected = []
            if role and lab is not None:
                try:
                    expected = lab.library.role_inputs(role)
                except SequencerError:
                    where = yamledit.locate(text, ["slot_types", name, "role"]) or (1, 1)
                    problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "warning",
                                     "message": f"type '{name}': '{role}' is not a lab role"})
            for cfg_name, cfg in (t.get("configurations") or {}).items():
                targets = {str(i.get("target", "")).rstrip("/") for i in (cfg or {}).get("inputs") or [] if isinstance(i, dict)}
                for item in expected:                # what its role expects and this configuration does not give
                    if not item["optional"] and item["name"].rstrip("/") not in targets:
                        where = yamledit.locate(text, ["slot_types", name, "configurations", cfg_name]) or (1, 1)
                        problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "warning",
                                         "message": f"type '{name}', configuration '{cfg_name}': no input '{item['name']}', "
                                                    f"which its role {role} expects"})
                for i, inp in enumerate((cfg or {}).get("inputs") or []):
                    source = str(inp.get("source", ""))
                    if source and "{{" not in source and not _find_sources(source, [path.parent]):
                        where = yamledit.locate(text, ["slot_types", name, "configurations", cfg_name,
                                                       "inputs", i, "source"]) or (1, 1)
                        problems.append({"file": rel, "line": where[0], "column": where[1],
                                         "severity": "warning" if inp.get("optional") else "error",
                                         "message": f"input '{source}' not found next to the setup (it may be "
                                                    "relative to the test definitions)"})
        for cfg_name, cfg in (raw.get("configurations") or {}).items():
            try:
                needs = needs_from_setup(raw, cfg_name)
            except SequencerError as e:
                where = yamledit.locate(text, ["configurations", cfg_name]) or (1, 1)
                problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "error",
                                 "message": str(e)})
                continue
            for comp_id, comp in ((cfg or {}).get("slots") or {}).items():
                type_role = (types.get(comp.get("type")) or {}).get("role")
                if type_role and comp.get("role") and comp["role"] != type_role:
                    where = yamledit.locate(text, ["configurations", cfg_name, "slots", comp_id, "role"]) or (1, 1)
                    problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "warning",
                                     "message": f"slot '{comp_id}': role '{comp['role']}', but its type "
                                                f"'{comp['type']}' is for role '{type_role}'"})
                if comp.get("type") in types and comp.get("configuration") not in (types[comp["type"]].get("configurations") or {}):
                    where = yamledit.locate(text, ["configurations", cfg_name, "slots", comp_id]) or (1, 1)
                    problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "error",
                                     "message": f"slot '{comp_id}': type '{comp['type']}' has no configuration "
                                                f"'{comp.get('configuration')}'"})
            for problem in check_against_lab(SimpleNamespace(needs=needs), lab):
                name = re.search(r"slot '([^']+)'", problem)
                where = (yamledit.locate(text, ["configurations", cfg_name, "slots", name.group(1)])
                         if name else yamledit.locate(text, ["configurations", cfg_name])) or (1, 1)
                problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "warning",
                                 "message": f"{cfg_name}: {problem}"})
        for f in self.tree(session):                 # test definitions on this setup whose pipeline no longer matches it
            if f["kind"] != "testdef":
                continue
            for mismatch in self._mismatches(session, f["path"], setup_path=path):
                cfg_name = str((yaml.safe_load(self.path(session, f["path"]).read_text(encoding="utf-8")) or {}).get("setup_config") or "")
                where = yamledit.locate(text, ["configurations", cfg_name]) or (1, 1)
                problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "warning",
                                 "message": f"{f['path']} ({cfg_name}): {mismatch}"})
        return problems

    def check_subpipelines(self, session: EditSession, rel: str, text: str) -> list[dict]:
        from testsuit.sequencer.model import SubPipeline
        raw = yaml.safe_load(text) or {}
        problems = []
        for name, sub in (raw.get("subpipelines") or {}).items():
            try:
                SubPipeline.model_validate(sub)
            except ValidationError as e:
                problems += _pydantic_problems(e, rel, text, ["subpipelines", name])
        return problems

    def validate(self, session: EditSession) -> dict:
        """Every file of the session; the lab files as a whole, like ``tsq lab reload``."""
        problems = []
        if session.kind == "lab":
            problems += self.check_lab(session)
        for f in self.tree(session):
            if f["kind"] in ("testdef", "setup", "subpipelines", "dictionary", "yaml"):
                problems += self.check_file(session, f["path"])
            elif f["kind"] in ("inventory", "library"):
                text = self.path(session, f["path"]).read_text(encoding="utf-8", errors="replace")
                error = yamledit.syntax_error(text)
                if error:
                    problems.append({"file": f["path"], "severity": "error", **error})
        return {"ok": not any(p["severity"] == "error" for p in problems), "problems": problems}

    # ── pipelines ────────────────────────────────────────────────────────

    def _flatten(self, lab: Lab, needs, pipeline, flats) -> dict:
        from testsuit.sequencer.model import phase_slots
        from testsuit.sequencer.scheduler import Availability, find_binding
        problems: list[str] = []
        binding, reasons = find_binding(lab, needs, pipeline.phases, Availability(), "editor", check_free=False)
        if binding is None:
            problems += [f"cannot bind: {r}" for r in reasons]
        phases = []
        for flat in flats:
            held = phase_slots(flat.phase, needs)
            rows = []
            for part, steps in (("steps", flat.steps), ("finally", flat.finally_)):
                for fs in steps:
                    step = fs.step
                    if step.slot:
                        targets = [step.slot]
                    elif isinstance(step.for_each, list):
                        targets = list(step.for_each)
                    elif step.for_each == "slots":
                        targets = list(held)
                    elif isinstance(step.for_each, str) and "{{" not in step.for_each:
                        targets = [c for c in held if c in needs.slots and needs.slots[c].role == step.for_each]
                    else:
                        targets = [str(step.for_each)] if step.for_each else []
                    expanded = []
                    for target in targets or [None]:
                        entry: dict[str, Any] = {"slot": target}
                        if binding is not None and target in binding.slots:
                            concrete = lab.inventory.components[binding.slots[target]]
                            lc = needs.slots[target]
                            stage = lab.inventory.stages.get(binding.stage_slots.get(lc.stage_slot, ""))
                            entry.update(concrete=concrete.id, role=concrete.role)
                            if step.action == "do":
                                try:
                                    resolved = lab.resolve(concrete, stage)
                                except SequencerError as e:
                                    problems.append(str(e))
                                    resolved = None
                                if resolved is not None:
                                    spec = resolved.commands.get(step.do)
                                    if spec is None:
                                        problems.append(f"phase '{flat.phase.id}', step '{fs.id}': slot '{target}' "
                                                        f"({concrete.id}, role {concrete.role}) has no command '{step.do}' (it has: "
                                                        f"{', '.join(sorted(resolved.commands)) or 'none'})")
                                    entry.update(command=spec, source=resolved.command_sources.get(step.do))
                        expanded.append(entry)
                    rows.append({"id": fs.id, "part": part, "group": fs.group, "needs": fs.needs, "action": step.action,
                                 "do": step.do, "params": fs.params, "targets": expanded,
                                 "step": step.model_dump(by_alias=True, exclude_defaults=True)})
            phases.append({"id": flat.phase.id, "needs": flat.phase.needs, "stage_slots": flat.phase.stage_slots,
                           "slots": held, "groups": flat.groups, "steps": rows})
        return {"binding": binding.as_dict() if binding is not None else None, "phases": phases, "problems": problems}

    def flatten(self, session: EditSession, rel: str, setup_config: str | None = None) -> dict:
        """The pipeline as the sequencer runs it: sub-pipelines expanded, every command resolved against the lab."""
        _, _, _, setup_path, needs, pipeline, flats = self._testdef_parts(session, rel, setup_config)
        lab, _ = self.lab_for(session)
        result = self._flatten(lab, needs, pipeline, flats)
        result["setup"] = self.rel(session, setup_path)
        result["logical"] = {"stage_slots": needs.stage_slots,
                             "slots": {c.id: {"type": c.type, "stage_slot": c.stage_slot, "role": c.role, "name": c.name}
                                       for c in needs.slots.values()}}
        return result

    def palette(self, session: EditSession, rel: str, setup_config: str | None = None) -> dict:
        """What a step can do on each slot of a test definition: the commands its role lists, with
        how each lab component that can be bound implements them (README section 13.2, pipelines)."""
        from testsuit.sequencer.model import needs_from_setup
        path = self.path(session, rel)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        setup_path = (path.parent / str(raw.get("setup_file") or "")).resolve()
        if not raw.get("setup_file") or not setup_path.is_file():
            raise SequencerError("the test definition has no reachable 'setup_file'")
        setup_raw = yaml.safe_load(setup_path.read_text(encoding="utf-8")) or {}
        config = setup_config or str(raw.get("setup_config") or "")
        needs = needs_from_setup(setup_raw, config)
        lab, _ = self.lab_for(session)
        slots = {}
        for lc in needs.slots.values():
            offered = lab.library.role_commands(lc.role) if lc.role in lab.library.roles else {}
            # the lab components that can be bound, with how each implements the commands
            stage_type = needs.stage_slots.get(lc.stage_slot)
            if lc.stage_slot:
                tags = set(needs.stage_slot_tags.get(lc.stage_slot, []))
                hosts = [(lab.inventory.components[c], st) for st in lab.inventory.stages_of_type(stage_type)
                         if tags <= set(st.tags)
                         for c in st.components if lab.fits(lab.inventory.components[c], lc.role, lc.tags)]
            else:
                hosts = [(c, None) for c in lab.inventory.adhoc if lab.fits(c, lc.role, lc.tags)]
            implementations: dict[str, dict[str, Any]] = {}
            for host, stage in hosts:
                if host is None:
                    continue
                try:
                    resolved = lab.resolve(host, stage)
                except SequencerError:
                    continue
                for cmd in offered:
                    if cmd in resolved.commands:
                        implementations.setdefault(cmd, {})[host.id] = {"spec": resolved.commands[cmd],
                                                                         "from": resolved.command_sources.get(cmd, "")}
            commands = {cmd: {"description": info["description"], "from": f"role {info['from']}",
                              "help": info["description"] or _command_help(cmd),
                              "implementations": implementations.get(cmd, {})}
                        for cmd, info in sorted(offered.items())}
            slots[lc.id] = {"name": lc.name, "type": lc.type, "stage_slot": lc.stage_slot, "role": lc.role,
                                 "role_known": lc.role in lab.library.roles, "commands": commands}
        workspace = self.scan_workspace(session.folder, session.folder) if session.kind == "workspace" else {}
        own = (raw.get("pipeline") or {}).get("subpipelines") or {}
        subs = {n: self._sub_summary(s) | {"file": rel} for n, s in own.items()}
        for name in (raw.get("pipeline") or {}).get("include") or []:
            inc = (path.parent / name).resolve()
            if inc.is_file():
                data = yaml.safe_load(inc.read_text(encoding="utf-8")) or {}
                for n, s in ((data.get("subpipelines", data) if isinstance(data, dict) else {}) or {}).items():
                    subs.setdefault(n, self._sub_summary(s) | {"file": self.rel(session, inc)})
        return {"setup": self.rel(session, setup_path), "configuration": config,
                "configurations": sorted((setup_raw.get("configurations") or {})),
                "stage_slots": needs.stage_slots, "slots": slots, "subpipelines": subs,
                "shared_subpipelines": (workspace.get("subpipelines") or {}),
                "step_types": sorted(registry.step_types), "ready_checks": sorted(registry.ready_checks)}

    # ── keys, template preview, datapack generation ──────────────────────

    def _builder(self, session: EditSession, testdef_rel: str, setup_config: str | None = None):
        """A datapack builder with the test definition and setup keys loaded, datasets as placeholders
        (nothing is imported)."""
        from testsuit.datatools.datapack.datapack_tools import _Builder, load_yaml_mapping
        with self._quiet():
            builder = _Builder(self.path(session, testdef_rel), nocheck=True, setup_config=setup_config)
            testdef = builder.load_testdef()
            setup_dir = builder.setup_file.parent
            if not builder.setup_file.is_file():
                raise SequencerError(f"setup file not reachable: '{self.rel(session, builder.setup_file)}'")
            raw = load_yaml_mapping(builder.setup_file)
            keys = builder.keys(with_datasets=False)
            builder._load_dictionaries("setup", keys.render_data(raw.get("dictionaries", []), where="dictionaries"),
                                       setup_dir)
            keys = builder.keys(with_datasets=False)
            builder.builtin["setup"] = {"id": keys.render_string(raw.get("id", ""), where="id"),
                                        "name": keys.render_string(raw.get("name", ""), where="name"),
                                        "file": builder.setup_file.as_posix(), "path": setup_dir.as_posix(),
                                        "configuration": testdef.setup_config}
            keys = builder.keys(with_datasets=False)
            for repo in raw.get("datarepositories") or []:
                for src in repo.get("datasources") or []:
                    for ds in src.get("datasets") or []:
                        version = keys.render_string(str(ds.get("version", "")), where=f"dataset {ds.get('id')}")
                        builder.dataset_keys.append(({ds["id"]: {"path": f"<{ds['id']} dataset folder>",
                                                                  "version": version}}, "<dataset placeholder>"))
            return builder, testdef, raw

    @contextlib.contextmanager
    def _quiet(self):
        out = io.StringIO()
        with self._output_lock, contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            yield out

    def keys(self, session: EditSession, testdef_rel: str, setup_config: str | None = None) -> dict:
        """Every key a template of this test definition can use, with its value and the file it comes from."""
        from testsuit.datatools.datapack.templating import TemplateError
        try:
            builder, _, _ = self._builder(session, testdef_rel, setup_config)
            keys = builder.keys()
            try:
                values = keys.resolve_all()
                error = None
            except TemplateError as e:
                values, error = {}, str(e)
        except Exception as e:
            raise SequencerError(str(e)) from e
        rows = []
        for name, value in _flatten_keys(values):
            origin = keys.origins.get(name, "")
            if origin.startswith("/") and Path(origin).is_relative_to(session.root):
                origin = self.rel(session, Path(origin))
            text = value if isinstance(value, str) else json.dumps(value, default=str)
            rows.append({"key": name, "value": text[:200], "origin": origin})
        return {"keys": rows, "error": error, "runtime": RUNTIME_KEYS,
                "datapack": {"slot.id": "id of the slot being generated (also slot.name, slot.type, slot.configuration)",
                             "slot.input_dir": "its input folder in the datapack",
                             "slots.<id>.input_dir": "input folder of a slot it needs",
                             "read_input('<id>/<file>')": "text of an input of a slot it needs"}}

    def preview(self, session: EditSession, testdef_rel: str, source: str, slot: str | None = None,
                setup_config: str | None = None) -> dict:
        """A template rendered as the datapack tool would, for a slot of the test definition's setup."""
        from testsuit.datatools.datapack.datapack_tools import _find_sources
        from testsuit.datatools.datapack.templating import TemplateError, run_time_keys
        builder, testdef, raw = self._builder(session, testdef_rel, setup_config)
        setup_dir = builder.setup_file.parent
        path = self.path(session, source)
        if not path.is_file():
            found = _find_sources(source, [builder.testdef_dir, setup_dir])
            if not found or not found[0].is_file():
                raise SequencerError(f"template '{source}' not found")
            path = found[0]
        slots = ((raw.get("configurations") or {}).get(testdef.setup_config) or {}).get("slots") or {}
        if slot is None:                              # the first slot with this template as an input
            for slot_id, item in slots.items():
                cfg = ((raw.get("slot_types") or {}).get(item.get("type")) or {}).get("configurations") or {}
                inputs = (cfg.get(item.get("configuration")) or {}).get("inputs") or []
                if any(path in _find_sources(str(i.get("source", "")), [builder.testdef_dir, setup_dir]) for i in inputs):
                    slot = slot_id
                    break
        comp = slots.get(slot or "", {})
        extra = {"slot": {"id": slot or "", "name": comp.get("name", ""), "type": comp.get("type", ""),
                          "configuration": comp.get("configuration", ""), "input_dir": f"<{slot} input folder>"},
                 "slots": {n: {"id": n, "input_dir": f"<{n} input folder>"} for n in comp.get("needs") or []},
                 "read_input": lambda p: f"<read_input('{p}'): available when the datapack is generated>"}
        keys = builder.keys()
        extra = {**run_time_keys(keys.root), **extra}
        try:
            text, used = keys.render_file(path, [builder.testdef_dir, setup_dir], extra)
        except TemplateError as e:
            _, message = self._message_file(session, str(e), source)
            line = re.search(r"line (\d+)", message)
            return {"ok": False, "error": message, "line": int(line.group(1)) if line else None, "slot": slot}
        return {"ok": True, "text": text, "slot": slot, "slots": sorted(slots),
                "keys": {k: (self.rel(session, Path(o)) if o.startswith("/") and Path(o).is_relative_to(session.root) else o)
                         for k, o in used.items()}}

    # ── Datapacks page: what a test definition is made of, its baseline ──

    def synthesis(self, session: EditSession, testdef_rel: str, setup_config: str | None = None,
                  versions: bool = True, run_checks: bool = True) -> dict:
        """A test definition at a glance: the logical configuration of its setup, the inputs of each
        slot, its dictionaries with their keys, and the datasets of its baseline with the versions
        their importer offers. versions=False: only the versions already listed less than a minute ago,
        the others marked ``versions_pending`` (listed then one source at a time: ``versions()``).
        run_checks=False: without the pipeline and lab checks (asked then on their own: ``run_problems()``)."""
        from testsuit.datatools.datapack.datapack_tools import _find_sources
        from testsuit.datatools.datapack.templating import TemplateError
        try:
            builder, testdef, raw = self._builder(session, testdef_rel, setup_config)
            keys = builder.keys()
        except SequencerError:
            raise
        except Exception as e:
            raise SequencerError(str(e)) from e

        def shown(path: Path | str) -> str:
            path = Path(path)
            if path.is_absolute() and path.resolve().is_relative_to(session.folder.resolve()):
                return self.rel(session, path)
            return path.as_posix()

        def render(text: Any) -> str | None:
            try:
                return keys.render_string(str(text), where="synthesis")
            except (TemplateError, Exception):
                return None

        setup_dir = builder.setup_file.parent
        config = testdef.setup_config
        cfg = (raw.get("configurations") or {}).get(config) or {}
        types = raw.get("slot_types") or {}
        components = []
        for comp_id, comp in (cfg.get("slots") or {}).items():
            comp = comp if isinstance(comp, dict) else {}
            configuration = ((types.get(comp.get("type")) or {}).get("configurations") or {}).get(comp.get("configuration")) or {}
            inputs = []
            for item in configuration.get("inputs") or []:
                if not isinstance(item, dict):
                    continue
                source = str(item.get("source", ""))
                resolved = render(source)
                found = _find_sources(resolved, [builder.testdef_dir, setup_dir]) if resolved and "<" not in resolved else []
                inputs.append({"source": source, "resolved": resolved, "target": item.get("target", ""),
                               "method": item.get("method") or "template", "generator": item.get("generator"),
                               "optional": bool(item.get("optional")),
                               "files": [shown(f) for f in found[:20]],
                               "holds_datapack": any(builder.work.resolve().is_relative_to(f.resolve()) for f in found),
                               "dataset": next((d for d in re.findall(r"\{\{\s*(\w+)\.path\s*\}\}", source)), None)})
            components.append({"id": comp_id, "name": comp.get("name", ""), "type": comp.get("type", ""),
                               "configuration": comp.get("configuration", ""),
                               "stage_slot": comp.get("stage_slot"), "role": comp.get("role"), "needs": comp.get("needs") or [],
                               "inputs": inputs})
        stage_slots = {s: (v.get("type") if isinstance(v, dict) else v) for s, v in (cfg.get("stage_slots") or {}).items()}

        dictionaries = []
        for layer, path in builder.dictionaries:
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                data = {}
            rows = []
            for name, value in _flatten_keys(data if isinstance(data, dict) else {}):
                text = value if isinstance(value, str) else json.dumps(value, default=str)
                origin = keys.origins.get(name, "")
                rows.append({"key": name, "value": text[:200],
                             "overridden_by": shown(origin) if origin and origin != path.as_posix() else None})
            dictionaries.append({"layer": layer, "path": shown(path), "keys": rows})

        baseline = self._datasets(session, builder, raw, keys, shown, render, versions)
        ids = {d["id"] for d in baseline}
        problems = []
        if config not in (raw.get("configurations") or {}):
            problems.append(f"no setup configuration '{config}' in {shown(builder.setup_file)}")
        for comp in components:
            if comp["type"] not in types:
                problems.append(f"slot '{comp['id']}': no slot type '{comp['type']}'")
            elif comp["configuration"] not in ((types[comp["type"]] or {}).get("configurations") or {}):
                problems.append(f"slot '{comp['id']}': no configuration '{comp['configuration']}' for type '{comp['type']}'")
            for number, item in enumerate(comp["inputs"], 1):
                item["dataset"] = item["dataset"] if item["dataset"] in ids else None
                where = f"slot '{comp['id']}', input {number}"
                if item["resolved"] is None:
                    problems.append(f"{where}: source '{item['source']}' cannot be resolved (a key missing?)")
                elif not item["resolved"].strip():
                    problems.append(f"{where}: empty source{'' if not item['source'].strip() else ' (' + repr(item['source']) + ' has an empty value)'}")
                elif not str(item["target"]).strip() or render(item["target"]) == "":
                    problems.append(f"{where}: empty target for '{item['source']}'")
                elif item["holds_datapack"]:
                    problems.append(f"{where}: '{item['resolved']}' holds the datapack being generated (a key with an empty value?)")
                elif not item["files"] and not item["optional"] and "<" not in item["resolved"]:   # <a dataset folder>: not checked
                    problems.append(f"{where}: '{item['resolved']}' not found")
        if run_checks:
            problems += self._run_problems(session, testdef_rel, config)
        return {"testdef": {"path": testdef_rel, "id": testdef.id, "name": testdef.name,
                            "description": getattr(testdef, "description", "") or "",
                            "setup_file": shown(builder.setup_file), "setup_config": config,
                            "own_config": config == (yaml.safe_load(self.path(session, testdef_rel).read_text(encoding="utf-8")) or {}).get("setup_config"),
                            "configurations": sorted(raw.get("configurations") or {})},
                "setup": {"id": raw.get("id"), "name": raw.get("name")},
                "stage_slots": stage_slots, "slots": components, "dictionaries": dictionaries,
                "baseline": baseline, "problems": problems}

    _version_cache: dict[tuple[str, str], tuple[float, Any, Any]] = {}

    def _versions_cached(self, importer_name: str, source: str) -> bool:
        import time
        cached = self._version_cache.get((importer_name, source))
        return bool(cached) and time.monotonic() - cached[0] < 60

    def versions(self, importer_name: str, source: str) -> dict:
        """The versions an importer lists at a source, and how long that took (the Datapacks page, one
        source at a time, after the rest of the synthesis)."""
        import time
        started = time.monotonic()
        versions, error = self._versions(importer_name, source)
        return {"versions": versions, "error": error, "seconds": round(time.monotonic() - started, 2)}

    def _versions(self, importer_name: str, source: str) -> tuple[list[str] | None, str | None]:
        """Versions an importer lists at a source (kept a minute: the forms ask again at each change)."""
        import time
        from testsuit.datatools.datapack import importers
        cached = self._version_cache.get((importer_name, source))
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1], cached[2]
        versions, error = None, None
        try:
            importer = importers.create_data_importer(importer_name, "", source, "")
            if hasattr(importer, "listVersions"):
                versions = importer.listVersions()
        except Exception as e:                       # unreachable remote, unknown importer...
            error = str(e).strip().splitlines()[-1] if str(e).strip() else type(e).__name__
        self._version_cache[(importer_name, source)] = (time.monotonic(), versions, error)
        return versions, error

    def datasets(self, session: EditSession, setup_rel: str, testdef_rel: str | None = None) -> dict:
        """The datasets of a setup as the datapack tool will import them, with the keys of a test definition
        that uses it (the first one, unless one is named): where each is read from and stored."""
        testdefs = [t["path"] for t in self.scan_workspace(session.folder, session.folder)["testdefs"]
                    if t.get("setup_file") == setup_rel]
        if testdef_rel not in testdefs:
            testdef_rel = testdefs[0] if testdefs else None
        if testdef_rel is None:
            return {"testdef": None, "testdefs": [], "datasets": [],
                    "error": "no test definition uses this setup yet: the keys of its paths cannot be resolved"}
        try:
            found = self.synthesis(session, testdef_rel)
        except SequencerError as e:
            return {"testdef": testdef_rel, "testdefs": testdefs, "datasets": [], "error": str(e)}
        return {"testdef": testdef_rel, "testdefs": testdefs, "setup_config": found["testdef"]["setup_config"],
                "datasets": found["baseline"], "error": None}

    def _datasets(self, session: EditSession, builder, raw: dict, keys, shown, render,
                  list_versions: bool = True) -> list[dict]:
        from testsuit.datatools.datapack.datapack_tools import _expand, normalize_dataset_path
        datasets = []
        for r, repo in enumerate(raw.get("datarepositories") or []):
            rendered_path = render(repo.get("path", "")) if repo.get("path") else None
            repo_path = _expand(rendered_path, builder.setup_file.parent) if rendered_path else None
            for s, src in enumerate(repo.get("datasources") or []):
                source = render(src.get("source", "")) or str(src.get("source", ""))
                if source and not source.startswith(("/", "~")) and "://" not in source and not re.match(r"^[\w.-]+@", source):
                    local = (builder.setup_file.parent / source)
                    source = local.as_posix() if local.exists() else source
                if source.startswith("/"):
                    source = os.path.normpath(source)
                pending = not list_versions and not self._versions_cached(str(src.get("importer", "")), source)
                versions, error = (None, None) if pending else self._versions(str(src.get("importer", "")), source)
                for d, ds in enumerate(src.get("datasets") or []):
                    if not isinstance(ds, dict):
                        continue
                    written = str(ds.get("version", ""))
                    match = re.fullmatch(r"\{\{\s*([\w.]+)\s*\}\}", written.strip())
                    key = match.group(1) if match else None
                    origin = keys.origins.get(key, "") if key else builder.setup_file.as_posix()
                    editable = bool(origin) and origin.startswith("/") and Path(origin).resolve().is_relative_to(session.root.resolve()) \
                        and (key is not None or "{{" not in written)
                    version = render(written)
                    local = None
                    if repo_path is not None and version:
                        local = shown(repo_path / str(ds.get("id")) / normalize_dataset_path(version))
                    datasets.append({"id": ds.get("id"), "datarepository": repo.get("id"), "datasource": src.get("id"),
                                     "repository_path": shown(repo_path) if repo_path is not None else None,
                                     "local_path": local, "imported": bool(local) and Path(repo_path / str(ds.get("id")) / normalize_dataset_path(version)).is_dir(),
                                     "name": src.get("name", ""), "importer": src.get("importer", ""),
                                     "source": source,
                                     "written": written, "version": version, "key": key,
                                     "defined_in": shown(origin) if origin.startswith("/") else origin or None,
                                     "editable": editable, "at": [r, s, d],
                                     "versions": versions, "versions_error": error, "versions_pending": pending})
        return datasets

    def set_version(self, session: EditSession, testdef_rel: str, dataset: str, version: str,
                    setup_config: str | None = None) -> dict:
        """Change the version of a dataset of the baseline where it is written: the dictionary key its
        version names (``version: "{{ key }}"``), or the setup file itself."""
        version = str(version).strip()
        if not version:
            raise SequencerError("no version given")
        found = next((d for d in self.synthesis(session, testdef_rel, setup_config)["baseline"] if d["id"] == dataset), None)
        if found is None:
            raise SequencerError(f"no dataset '{dataset}' in the setup of '{testdef_rel}'")
        if not found["editable"]:
            raise SequencerError(f"the version of '{dataset}' is not written in a file of this repository: change it in "
                                 f"'{found['defined_in'] or found['written']}'")
        rel = found["defined_in"]
        if found["key"]:
            at: list = found["key"].split(".")
        else:
            r, s, d = found["at"]
            at = ["datarepositories", r, "datasources", s, "datasets", d, "version"]
        result = self.write(session, rel, data=version, at=at)
        return {"file": rel, "key": found["key"], "version": version, "problems": result.get("problems", [])}

    STOP_GRACE_S = 5          # a stopped generation is killed if it has not ended by then

    def generate(self, session: EditSession, testdef_rel: str, setup_config: str | None = None,
                 nocheck: bool = True, lenient: bool = False, manual_keys: dict | None = None) -> dict:
        """Run the datapack tool on a test definition of the session, as ``datapack <testdef>`` would;
        manual_keys: keys set by hand (dotted name -> value, ``datapack --set``)."""
        job_id = self.start_generate(session, testdef_rel, setup_config, nocheck, lenient, manual_keys)["job"]
        self.jobs[job_id]["thread"].join()
        return self.jobs[job_id]["result"]

    def start_generate(self, session: EditSession, testdef_rel: str, setup_config: str | None = None,
                       nocheck: bool = True, lenient: bool = False, manual_keys: dict | None = None) -> dict:
        """The same as generate, in the background: its log can be read while it runs (generation_job).

        The datapack tool runs in a process of its own: a stopped generation ends for sure (the process and
        the commands it started are killed), whatever it was doing.
        """
        for name in manual_keys or {}:
            if not str(name).strip() or any(not part.strip() for part in str(name).split(".")) or "=" in str(name):
                raise SequencerError(f"'{name}' is not a key name (name, or name.sub for a nested key)")
        job_id = uuid.uuid4().hex[:8]
        log = _LiveLog()
        job: dict[str, Any] = {"session": session.id, "log": log, "result": None, "done": False,
                               "process": None, "stop": threading.Event()}
        finished = [k for k, v in self.jobs.items() if v["done"]]
        for k in finished[:max(0, len(self.jobs) - 20)]:   # keep the last ones only
            del self.jobs[k]
        self.jobs[job_id] = job

        def work() -> None:
            logger.info(f"editor: datapack generation of {testdef_rel} ({session.repo}) started")
            try:
                job["result"] = self._generate(session, testdef_rel, setup_config, nocheck, lenient, job, manual_keys)
            except BaseException as e:                   # never a silent end
                job["result"] = {"ok": False, "error": _describe(e), "log": self._clean(session, log.text())}
            finally:
                if job["result"] is None:
                    job["result"] = {"ok": False, "error": "The generation ended without a result.",
                                     "log": self._clean(session, log.text())}
                if not job["result"]["ok"]:                  # the reason as the last line of the log too
                    log.write(f"ERROR: {job['result']['error']}\n")
                    logger.warning(f"editor: datapack generation of {testdef_rel} ({session.repo}) failed: "
                                   f"{job['result']['error']}")
                else:
                    logger.info(f"editor: datapack {job['result']['zip']} generated")
                job["done"] = True

        job["thread"] = threading.Thread(target=work, name=f"generate-{job_id}", daemon=True)
        job["thread"].start()
        return {"job": job_id}

    def stop_generation(self, session: EditSession, job_id: str) -> dict:
        """Stop a generation: its process (and every command it started) is terminated, then killed if it
        has not ended after STOP_GRACE_S seconds."""
        job = self.jobs.get(job_id)
        if job is None or job["session"] != session.id:
            raise SequencerError(f"no generation '{job_id}' in this session")
        if job["done"]:
            return {"stopping": False}
        job["stop"].set()
        process = job["process"]
        if process is not None:
            _signal_group(process, "SIGTERM")

            def kill_later() -> None:
                try:
                    process.wait(self.STOP_GRACE_S)
                except subprocess.TimeoutExpired:
                    job["log"].write(f"(still running {self.STOP_GRACE_S} s after Stop: killed)\n")
                _signal_group(process, "SIGKILL")       # and any command it started that is left
            threading.Thread(target=kill_later, daemon=True).start()
        return {"stopping": True}

    def generation_job(self, session: EditSession, job_id: str, since: int = 0) -> dict:
        """The log lines of a generation from line since, and its result once it is done."""
        job = self.jobs.get(job_id)
        if job is None or job["session"] != session.id:
            raise SequencerError(f"no generation '{job_id}' in this session")
        done = job["done"]                                   # read before the lines: none is missed
        lines = job["log"].lines(since)
        return {"lines": [self._clean(session, line) for line in lines], "next": since + len(lines),
                "done": done, "result": job["result"] if done else None}

    @staticmethod
    def _clean(session: EditSession, text: str) -> str:
        return ANSI.sub("", text).replace(f"{session.folder}/", "").replace(f"{session.dir}/", "")

    _generation_lock = threading.Lock()     # one generation at a time: they may share a working folder

    def _generate(self, session: EditSession, testdef_rel: str, setup_config: str | None, nocheck: bool,
                  lenient: bool, job: dict, manual_keys: dict | None = None) -> dict:
        from testsuit.datatools.datapack.datapack_tools import read_baseline
        log: _LiveLog = job["log"]
        out_dir = Path(session.dir) / "generated"
        out_dir.mkdir(exist_ok=True)
        command = [sys.executable, "-m", "testsuit.cli.datapack", str(self.path(session, testdef_rel)),
                   "--output-dir", str(out_dir)]
        command += ["--nocheck"] if nocheck else []
        command += ["--lenient"] if lenient else []
        command += ["--setup-config", setup_config] if setup_config else []
        for name, value in (manual_keys or {}).items():     # a text value is read as YAML by the tool, as typed
            command += ["--set", f"{str(name).strip()}={value if isinstance(value, str) else json.dumps(value)}"]
        popen_args: dict[str, Any] = {"start_new_session": True} if hasattr(os, "killpg") else {}

        waited = False
        while not self._generation_lock.acquire(timeout=0.5):
            if job["stop"].is_set():
                return {"ok": False, "error": "Generation stopped.", "log": self._clean(session, log.text())}
            if not waited:
                log.write("(waiting for another generation of this server to end)\n")
                waited = True
        try:
            if job["stop"].is_set():
                return {"ok": False, "error": "Generation stopped.", "log": self._clean(session, log.text())}
            output: list[str] = []
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  text=True, errors="replace", bufsize=1, **popen_args,
                                  env={**os.environ, "PYTHONUNBUFFERED": "1", "GIT_TERMINAL_PROMPT": "0"}) as process:
                job["process"] = process
                assert process.stdout is not None
                for line in process.stdout:
                    log.write(line)
                    output.append(ANSI.sub("", line.rstrip("\n")))
            returncode = process.returncode
        finally:
            job["process"] = None
            self._generation_lock.release()

        text = self._clean(session, log.text())
        if job["stop"].is_set():
            return {"ok": False, "error": "Generation stopped.", "log": text}
        zip_path = next((Path(line.split("Generated Datapack:", 1)[1].strip()) for line in reversed(output)
                         if "Generated Datapack:" in line), None)
        if returncode != 0 or zip_path is None or not zip_path.is_file():
            # the tool's last error, with the lines it spans (a list of keys that cannot be resolved), or the
            # last line it wrote (the exception of a traceback)
            start = next((i for i in range(len(output) - 1, -1, -1) if output[i].startswith("ERROR: ")), None)
            if start is not None:
                error = "\n".join(output[start:]).removeprefix("ERROR: ").strip()
            else:
                last = next((line.strip() for line in reversed(output) if line.strip()), "no output")
                error = f"the datapack tool failed (exit status {returncode}): {last}"
            _, message = self._message_file(session, error, testdef_rel)
            return {"ok": False, "error": self._clean(session, message), "log": text}
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            files = [{"path": n.split("/", 1)[1], "size": z.getinfo(n).file_size} for n in z.namelist()
                     if "/" in n and not n.endswith("/")]
            root = z.namelist()[0].split("/")[0]
            manifest = yaml.safe_load(z.read(f"{root}/datapack.yml"))
        return {"ok": True, "name": zip_path.stem, "zip": zip_path.name, "log": text, "files": files,
                "baseline": read_baseline(zip_path), "datapack": manifest}

    def generated(self, session: EditSession, name: str) -> Path:
        path = (Path(session.dir) / "generated" / Path(name).name).with_suffix(".zip")
        if not path.is_file():
            raise SequencerError(f"no generated datapack '{name}' in this session")
        return path

    # ── git: diff, push, rebase ──────────────────────────────────────────

    def diff(self, session: EditSession) -> dict:
        self._need_git(session)
        # only the edited folder: an in-place session's working copy may hold other changes, left alone
        where = ["--", session.sub or "."]
        _git(session.root, "add", "-A", *where)
        names = _git(session.root, "diff", "--cached", "--name-status", session.base, *where)
        patch = _git(session.root, "diff", "--cached", session.base, *where)
        files = [{"status": line.split("\t")[0], "path": line.split("\t")[-1]} for line in names.splitlines() if line]
        ahead = _git(session.root, "rev-list", "--count", f"{session.base}..HEAD").strip()
        return {"files": files, "patch": patch, "branch": session.branch, "base": session.base,
                "local_commits": int(ahead or 0)}

    def _push_env(self, credentials: dict) -> dict:
        env = {"GIT_ASKPASS": str(self.askpass), "SSH_ASKPASS": str(self.askpass), "SSH_ASKPASS_REQUIRE": "force",
               "DISPLAY": os.environ.get("DISPLAY", ":0"), "TSQ_GIT_USERNAME": credentials.get("username") or "",
               "TSQ_GIT_SECRET": credentials.get("password") or credentials.get("passphrase") or ""}
        return env

    @staticmethod
    def _scrub(text: str, credentials: dict) -> str:
        for secret in (credentials.get("password"), credentials.get("passphrase")):
            if secret:
                text = text.replace(secret, "***")
        return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", text)

    def push(self, session: EditSession, message: str, author_name: str, author_email: str,
             tag: str | None = None, credentials: dict | None = None) -> dict:
        """Commit the session's changes as the user, tag, and push to the session's branch. On a refused
        push nothing is applied: the commit and the tag are undone, the changes stay in the session."""
        credentials = credentials or {}
        self._need_git(session)
        if not session.branch:
            raise SequencerError("the working copy is on no branch (detached HEAD): check out a branch to push from it")
        if not message.strip():
            raise SequencerError("a commit message is needed")
        if not author_name.strip() or not author_email.strip():
            raise SequencerError("the commit author (name and e-mail) is needed: commits are made in the user's name")
        if session.kind == "lab":
            problems = [p for p in self.check_lab(session) if p["severity"] == "error"]
            if problems:
                raise SequencerError("the lab files are invalid, not pushed: " + "; ".join(p["message"] for p in problems))
        diff = self.diff(session)
        if not diff["files"]:
            raise SequencerError("nothing to push: no change since the session started")
        ident = ["-c", f"user.name={author_name}", "-c", f"user.email={author_email}"]
        _git(session.root, *ident, "commit", "--quiet", "-m", message, "--", session.sub or ".")
        commit = _git(session.root, "rev-parse", "HEAD").strip()
        if tag:
            _git(session.root, *ident, "tag", "-a", tag, "-m", message)
        env = self._push_env(credentials)
        refs = [f"HEAD:refs/heads/{session.branch}"] + ([f"refs/tags/{tag}"] if tag else [])
        result = subprocess.run(["git", "-c", "credential.helper=", "push", "--porcelain", "origin", *refs],
                                cwd=session.root, capture_output=True, text=True, timeout=300,
                                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **env})
        if result.returncode:
            if tag:
                _git(session.root, "tag", "-d", tag, check=False)
            _git(session.root, "reset", "--quiet", "--soft", "HEAD~1")
            why = self._scrub((result.stderr or result.stdout).strip(), credentials)
            hint = ("the branch moved meanwhile: rebase the session on the latest revision and push again"
                    if re.search(r"rejected|non-fast-forward|fetch first", why) else
                    "check the credentials and their rights on the repository")
            return {"ok": False, "error": why, "hint": hint}
        record = {"commit": commit, "tag": tag, "author": f"{author_name} <{author_email}>", "message": message,
                  "branch": session.branch, "at": now(), "session": session.id}
        session.base = commit
        session.pushed.append(record)
        self._save(session)
        self.published[session.repo] = record
        return {"ok": True, **record}

    def rebase(self, session: EditSession, credentials: dict | None = None) -> dict:
        """Fetch the upstream branch and put the session's changes on top of it."""
        credentials = credentials or {}
        self._need_git(session)
        result = subprocess.run(["git", "-c", "credential.helper=", "fetch", "--quiet", "--tags", "origin"],
                                cwd=session.root, capture_output=True, text=True, timeout=300,
                                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **self._push_env(credentials)})
        if result.returncode:
            return {"ok": False, "error": self._scrub(result.stderr.strip(), credentials)}
        _git(session.root, "add", "-A")
        before = len(_git(session.root, "stash", "list").splitlines())
        stashed = _git(session.root, "stash", "push", "--quiet", "--include-untracked", "-m", "editor", check=False)
        has_stash = len(_git(session.root, "stash", "list").splitlines()) > before
        if session.local:
            # the user's working copy: its own commits are replayed on the upstream branch, never dropped
            moved = subprocess.run(["git", "rebase", "--quiet", f"origin/{session.branch}"], cwd=session.root,
                                   capture_output=True, text=True)
            if moved.returncode:
                _git(session.root, "rebase", "--abort", check=False)
                if has_stash:
                    _git(session.root, "stash", "pop", "--quiet", check=False)
                return {"ok": False, "error": "the local commits do not apply on the latest revision: "
                        + (moved.stderr or moved.stdout).strip(), "conflicts": []}
        else:
            _git(session.root, "reset", "--quiet", "--hard", f"origin/{session.branch}")
        session.base = _git(session.root, "rev-parse", "HEAD").strip()
        self._save(session)
        conflicts = []
        if has_stash:
            pop = subprocess.run(["git", "stash", "pop", "--quiet"], cwd=session.root, capture_output=True, text=True)
            if pop.returncode:
                conflicts = [line[3:] for line in _git(session.root, "status", "--porcelain").splitlines()
                             if line[:2] in ("UU", "AA", "DU", "UD")]
        del stashed
        return {"ok": not conflicts, "base": session.base, "conflicts": conflicts,
                **({"error": "conflicts with the latest revision: fix the marked files"} if conflicts else {})}


def _command_help(name: str) -> str:
    from testsuit.sequencer.editor_schemas import COMMAND_NAMES
    return COMMAND_NAMES.get(name, "")
