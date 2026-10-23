"""Editor mode (README section 13.2): editing sessions on scratch clones, checks, helpers, push.

An editing session is a clone of the repository holding the lab folder, or one of
``server.workspaces``, in ``<storage.root>/editor/<session>/repo``. The sequencer's own lab
folder is never written: a change reaches the lab when it is pushed, then reloaded on demand.

Everything the Editor does can be done with a text editor and git: it reads and writes the same
YAML files, and keeps them as written (``yamledit``).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
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

ASKPASS = """#!/bin/sh
# Answers git and ssh prompts from the environment of one push (never stored, never logged).
case "$1" in
  *[Uu]sername*) printf '%s\\n' "$TSQ_GIT_USERNAME" ;;
  *) printf '%s\\n' "$TSQ_GIT_SECRET" ;;
esac
"""

# run-time keys of commands and steps (README section 8.3), for completion and help
RUNTIME_KEYS = {
    "component.id": "logical component id (setup configuration)",
    "component.name": "its name",
    "component.type": "its datapack component type",
    "component.lab_type": "the type of the lab component it is bound to",
    "component.role": "its role",
    "component.host": "address of the bound lab component (ansible_host)",
    "component.concrete": "id of the bound lab component",
    "component.label": "label of the bound lab component",
    "component.stage": "its logical stage",
    "component.concrete_stage": "the lab stage it is bound to",
    "component.input_dir": "<remote_root>/<component>/input: where deploy puts its inputs",
    "component.output_dir": "<remote_root>/<component>/output: what collect brings back",
    "component.remote_root": "the component's remote root (tsq_remote_root)",
    "component.vars": "every inventory variable of the bound lab component (component.vars.tsq_start_args...)",
    "component.state": "its monitored state",
    "remote_root": "the component's remote root",
    "run.id": "run id", "run.dir": "run folder on the sequencer host", "run.priority": "run priority",
    "phase.id": "current phase",
    "components.<id>": "the same keys for any component of the run (components.pilot.host)",
    "stages.<id>.id": "lab stage a logical stage is bound to",
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

    @property
    def root(self) -> Path:
        return Path(self.dir) / "repo"

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


class Editor:

    _output_lock = threading.Lock()                 # stdout is captured while the datapack tool prints

    def __init__(self, seq):
        self.seq = seq
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
        repos = [{"id": "lab", "kind": "lab", "label": "Lab inventory and component types",
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

    def open(self, repo_id: str, branch: str | None = None) -> EditSession:
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

    def _save(self, session: EditSession) -> None:
        (Path(session.dir) / "session.json").write_text(json.dumps(asdict(session), indent=1))

    def get(self, session_id: str) -> EditSession:
        if session_id not in self.sessions:
            raise SequencerError(f"no editing session '{session_id}'")
        return self.sessions[session_id]

    def close(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is not None:
            shutil.rmtree(session.dir, ignore_errors=True)

    def describe(self, session: EditSession) -> dict:
        status = _git(session.root, "status", "--porcelain", "--untracked-files=all", check=False)
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
        """What a file is: inventory, types, setup, testdef, subpipelines, dictionary, template, or other."""
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
        if "configurations" in data and ("component_types" in data or "datarepositories" in data):
            return "setup"
        if "component_types" in data:
            return "types"
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
        result = {"path": rel, "kind": kind, "content": text}
        if kind in ("inventory", "types", "setup", "testdef", "subpipelines", "dictionary"):
            try:
                result["data"] = yaml.safe_load(text)
                result["aliases"] = "*" in text and bool(re.search(r"(^|\s)\*[A-Za-z_]", text))
            except yaml.YAMLError:
                pass
        return result

    def write(self, session: EditSession, rel: str, content: str | None = None, data: Any = None,
              at: list | None = None) -> dict:
        """Write a file: its text (content), or its data merged onto it (data, at a path) keeping comments."""
        path = self.path(session, rel)
        if content is None:
            current = path.read_text(encoding="utf-8") if path.is_file() else ""
            content = yamledit.merge(current, data, at)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        result = self.read(session, rel)
        result["problems"] = self.check_file(session, rel)
        return result

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
        """The lab the session's helpers and checks use: its own files for a lab session (the running lab
        when they are invalid), the running lab for a workspace."""
        if session.kind == "lab":
            try:
                return self.session_lab(session), None
            except SequencerError as e:
                return self.seq.lab, str(e)
        return self.seq.lab, None

    @staticmethod
    def describe_types(lab: Lab) -> dict:
        """Every type with what it inherits: commands (with the type each comes from), status, self-test."""
        result = {}
        for name, t in lab.types.types.items():
            chain = lab.types.chain(name)
            commands, status, selftest, transport = {}, None, {}, None
            for link in chain:
                for cmd, spec in link.commands.items():
                    commands[cmd] = {"spec": spec, "from": link.name}
                if link.status is not None:
                    status = {"spec": link.status, "from": link.name}
                for check in link.selftest:
                    selftest[check["id"]] = {"spec": check, "from": link.name}
                transport = link.transport or transport
            used = [c.id for c in lab.inventory.components.values() if c.type == name]
            result[name] = {"extends": t.extends, "description": t.description, "source": t.source,
                            "chain": [link.name for link in chain], "transport": transport or "ssh",
                            "commands": commands, "status": status, "selftest": list(selftest.values()),
                            "own_commands": sorted(t.commands), "used_by": used,
                            "abstract": not used and any(o.extends == name for o in lab.types.types.values())}
        return result

    @classmethod
    def describe_lab(cls, lab: Lab) -> dict:
        inv = lab.inventory
        roles: dict[str, list[str]] = {}
        for c in inv.components.values():
            if c.role:
                roles.setdefault(c.role, []).append(c.id)
        stage_types: dict[str, list[str]] = {}
        stage_type_roles: dict[str, dict[str, str]] = {}
        for s in inv.stages.values():
            stage_types.setdefault(s.type, []).append(s.id)
            for comp_id in s.components:
                comp = inv.components[comp_id]
                if comp.role:
                    stage_type_roles.setdefault(s.type, {})[comp.role] = comp.type
        return {
            "types": cls.describe_types(lab),
            "roles": roles, "stage_types": stage_types, "stage_type_roles": stage_type_roles,
            "adhoc_roles": {c.role: c.type for c in inv.adhoc if c.role},
            "stages": {s.id: {"type": s.type, "components": s.components, "commands": sorted(s.commands)}
                       for s in inv.stages.values()},
            "components": {c.id: {"type": c.type, "role": c.role, "host": c.host, "lock": c.lock, "label": c.label,
                                  "stages": c.stages, "transport": str(c.vars.get("tsq_transport") or "")}
                           for c in inv.components.values()},
            "type_folders": [str(f) for f in inv.type_folders],
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
                elif "configurations" in data and ("component_types" in data or "datarepositories" in data):
                    result["setups"].append(self._setup_summary(rel, data))
                elif "subpipelines" in data and isinstance(data["subpipelines"], dict):
                    result["subpipeline_files"].append(rel)
                    for sub, spec in data["subpipelines"].items():
                        result["subpipelines"].setdefault(sub, {"file": rel, **self._sub_summary(spec)})
                elif "component_types" not in data and "all" not in data:
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
            stages = {s: (v.get("type") if isinstance(v, dict) else v) for s, v in (cfg.get("stages") or {}).items()}
            comps = {c: {k: v.get(k) for k in ("name", "type", "configuration", "stage", "role", "needs")}
                     for c, v in (cfg.get("components") or {}).items() if isinstance(v, dict)}
            configurations[name] = {"stages": stages, "components": comps}
        types = {}
        for name, t in (data.get("component_types") or {}).items():
            t = t if isinstance(t, dict) else {}
            types[name] = {"extends": t.get("extends", ""), "configurations": sorted(t.get("configurations") or {}),
                           "commands": sorted(t.get("commands") or {}), "description": t.get("description", "")}
        datasets = [{"id": ds.get("id"), "version": ds.get("version")}
                    for repo in data.get("datarepositories") or [] for src in repo.get("datasources") or []
                    for ds in src.get("datasets") or [] if isinstance(ds, dict)]
        return {"path": rel, "id": data.get("id"), "name": data.get("name"), "configurations": configurations,
                "component_types": types, "datasets": datasets, "dictionaries": data.get("dictionaries") or []}

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
        for t in described["types"].values():             # where each type is defined, as the user sees it
            source = Path(t["source"]) if t["source"].startswith("/") else None
            if source is not None and source.is_relative_to(session.folder):
                t["source"] = self.rel(session, source)
            elif source is not None and source.is_relative_to(self.seq.config.lab_folder):
                t["source"] = "lab: " + os.path.relpath(source, self.seq.config.lab_folder)
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
            if kind in ("inventory", "types"):
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
        """Like ``tsq lab reload``: the inventory and its type library load; plus what workspace datapacks would miss."""
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
        for comp in lab.inventory.components.values():
            if not comp.role and not comp.adhoc:
                line, column = _find_line(text, f"'{comp.id}'")
                problems.append({"file": inv_rel, "line": line, "column": column, "severity": "warning",
                                 "message": f"component '{comp.id}' has no tsq_role: no datapack can bind it"})
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

    def check_setup(self, session: EditSession, rel: str) -> list[dict]:
        from testsuit.datatools.datapack.datapack_tools import _find_sources
        from testsuit.datatools.datapack.model import Setup
        from testsuit.sequencer.intake import check_against_lab
        from testsuit.sequencer.model import needs_from_setup
        path = self.path(session, rel)
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        problems = []
        try:
            Setup.model_validate(raw)
        except ValidationError as e:
            return _pydantic_problems(e, rel, text)
        lab, _ = self.lab_for(session)
        types = raw.get("component_types") or {}
        for name, t in types.items():
            base = t.get("extends") or ""
            if base and base not in types and base not in lab.types.types:
                where = yamledit.locate(text, ["component_types", name, "extends"]) or (1, 1)
                problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "error",
                                 "message": f"type '{name}' extends '{base}', unknown in the lab type library "
                                            f"({', '.join(sorted(lab.types.types))})"})
            for cfg_name, cfg in (t.get("configurations") or {}).items():
                for i, inp in enumerate((cfg or {}).get("inputs") or []):
                    source = str(inp.get("source", ""))
                    if source and "{{" not in source and not _find_sources(source, [path.parent]):
                        where = yamledit.locate(text, ["component_types", name, "configurations", cfg_name,
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
            for comp_id, comp in ((cfg or {}).get("components") or {}).items():
                if comp.get("type") in types and comp.get("configuration") not in (types[comp["type"]].get("configurations") or {}):
                    where = yamledit.locate(text, ["configurations", cfg_name, "components", comp_id]) or (1, 1)
                    problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "error",
                                     "message": f"component '{comp_id}': type '{comp['type']}' has no configuration "
                                                f"'{comp.get('configuration')}'"})
            for problem in check_against_lab(SimpleNamespace(needs=needs), lab):
                name = re.search(r"component '([^']+)'", problem)
                where = (yamledit.locate(text, ["configurations", cfg_name, "components", name.group(1)])
                         if name else yamledit.locate(text, ["configurations", cfg_name])) or (1, 1)
                problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "warning",
                                 "message": f"{cfg_name}: {problem}"})
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
            elif f["kind"] in ("inventory", "types"):
                text = self.path(session, f["path"]).read_text(encoding="utf-8", errors="replace")
                error = yamledit.syntax_error(text)
                if error:
                    problems.append({"file": f["path"], "severity": "error", **error})
        return {"ok": not any(p["severity"] == "error" for p in problems), "problems": problems}

    # ── pipelines ────────────────────────────────────────────────────────

    def _flatten(self, lab: Lab, needs, pipeline, flats) -> dict:
        from testsuit.sequencer.model import phase_components
        from testsuit.sequencer.scheduler import Availability, find_binding
        problems: list[str] = []
        binding, reasons = find_binding(lab, needs, pipeline.phases, Availability(), "editor", check_free=False)
        if binding is None:
            problems += [f"cannot bind: {r}" for r in reasons]
        phases = []
        for flat in flats:
            held = phase_components(flat.phase, needs)
            rows = []
            for part, steps in (("steps", flat.steps), ("finally", flat.finally_)):
                for fs in steps:
                    step = fs.step
                    if step.component:
                        targets = [step.component]
                    elif isinstance(step.for_each, list):
                        targets = list(step.for_each)
                    elif step.for_each == "components":
                        targets = list(held)
                    elif isinstance(step.for_each, str) and "{{" not in step.for_each:
                        targets = [c for c in held if needs.components[c].role == step.for_each]
                    else:
                        targets = [str(step.for_each)] if step.for_each else []
                    expanded = []
                    for target in targets or [None]:
                        entry: dict[str, Any] = {"component": target}
                        if binding is not None and target in binding.components:
                            concrete = lab.inventory.components[binding.components[target]]
                            lc = needs.components[target]
                            stage = lab.inventory.stages.get(binding.stages.get(lc.stage, ""))
                            entry.update(concrete=concrete.id, lab_type=concrete.type)
                            if step.action == "do":
                                try:
                                    resolved = lab.types.resolve(concrete, stage, needs.component_types, lc.type,
                                                                 lc.commands)
                                except SequencerError as e:
                                    problems.append(str(e))
                                    resolved = None
                                if resolved is not None:
                                    spec = resolved.commands.get(step.do)
                                    if spec is None:
                                        problems.append(f"phase '{flat.phase.id}', step '{fs.id}': '{target}' "
                                                        f"({concrete.type}) has no command '{step.do}' (it has: "
                                                        f"{', '.join(sorted(resolved.commands)) or 'none'})")
                                    entry.update(command=spec, source=resolved.command_sources.get(step.do))
                        expanded.append(entry)
                    rows.append({"id": fs.id, "part": part, "group": fs.group, "needs": fs.needs, "action": step.action,
                                 "do": step.do, "params": fs.params, "targets": expanded,
                                 "step": step.model_dump(by_alias=True, exclude_defaults=True)})
            phases.append({"id": flat.phase.id, "needs": flat.phase.needs, "stages": flat.phase.stages,
                           "components": held, "groups": flat.groups, "steps": rows})
        return {"binding": binding.as_dict() if binding is not None else None, "phases": phases, "problems": problems}

    def flatten(self, session: EditSession, rel: str, setup_config: str | None = None) -> dict:
        """The pipeline as the sequencer runs it: sub-pipelines expanded, every command resolved against the lab."""
        _, _, _, setup_path, needs, pipeline, flats = self._testdef_parts(session, rel, setup_config)
        lab, _ = self.lab_for(session)
        result = self._flatten(lab, needs, pipeline, flats)
        result["setup"] = self.rel(session, setup_path)
        result["logical"] = {"stages": needs.stages,
                             "components": {c.id: {"type": c.type, "stage": c.stage, "role": c.role, "name": c.name}
                                            for c in needs.components.values()}}
        return result

    def palette(self, session: EditSession, rel: str, setup_config: str | None = None) -> dict:
        """What a step can do on each logical component of a test definition: the commands its datapack type
        and lab type give (README section 13.2, pipelines)."""
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
        lab_types = self.describe_types(lab)
        components = {}
        for lc in needs.components.values():
            commands: dict[str, dict] = {}
            chain = []
            current, seen = lc.type, set()
            while current in needs.component_types and current not in seen:
                seen.add(current)
                chain.insert(0, current)
                current = (needs.component_types[current] or {}).get("extends") or ""
            if current in lab_types:
                for cmd, info in lab_types[current]["commands"].items():
                    commands[cmd] = {"spec": info["spec"], "from": f"lab type {info['from']}"}
            for t in chain:
                for cmd, spec in ((needs.component_types[t] or {}).get("commands") or {}).items():
                    commands[cmd] = {"spec": spec, "from": f"datapack type {t}"}
            for cmd, spec in (lc.commands or {}).items():
                commands[cmd] = {"spec": spec, "from": "setup component"}
            # lab overrides on the stages of that type
            stage_type = needs.stages.get(lc.stage)
            for stage in lab.inventory.stages_of_type(stage_type) if stage_type else []:
                for cmd, spec in stage.commands.items():
                    commands.setdefault(cmd, {"spec": spec, "from": f"stage {stage.id}"})
            components[lc.id] = {"name": lc.name, "type": lc.type, "lab_type": current, "stage": lc.stage,
                                 "role": lc.role, "commands": {c: {**v, "help": _command_help(c)}
                                                                for c, v in sorted(commands.items())}}
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
                "stages": needs.stages, "components": components, "subpipelines": subs,
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
                "datapack": {"component.id": "id of the component being generated",
                             "component.input_dir": "its input folder in the datapack",
                             "components.<id>.input_dir": "input folder of a component it needs",
                             "read_input('<id>/<file>')": "text of an input of a component it needs"}}

    def preview(self, session: EditSession, testdef_rel: str, source: str, component: str | None = None,
                setup_config: str | None = None) -> dict:
        """A template rendered as the datapack tool would, for a component of the test definition's setup."""
        from testsuit.datatools.datapack.datapack_tools import _find_sources
        from testsuit.datatools.datapack.templating import TemplateError
        builder, testdef, raw = self._builder(session, testdef_rel, setup_config)
        setup_dir = builder.setup_file.parent
        path = self.path(session, source)
        if not path.is_file():
            found = _find_sources(source, [builder.testdef_dir, setup_dir])
            if not found or not found[0].is_file():
                raise SequencerError(f"template '{source}' not found")
            path = found[0]
        comps = ((raw.get("configurations") or {}).get(testdef.setup_config) or {}).get("components") or {}
        if component is None:                         # the first component with this template as an input
            for comp_id, comp in comps.items():
                cfg = ((raw.get("component_types") or {}).get(comp.get("type")) or {}).get("configurations") or {}
                inputs = (cfg.get(comp.get("configuration")) or {}).get("inputs") or []
                if any(path in _find_sources(str(i.get("source", "")), [builder.testdef_dir, setup_dir]) for i in inputs):
                    component = comp_id
                    break
        comp = comps.get(component or "", {})
        extra = {"component": {"id": component or "", "name": comp.get("name", ""), "type": comp.get("type", ""),
                               "configuration": comp.get("configuration", ""), "input_dir": f"<{component} input folder>"},
                 "components": {n: {"id": n, "input_dir": f"<{n} input folder>"} for n in comp.get("needs") or []},
                 "read_input": lambda p: f"<read_input('{p}'): available when the datapack is generated>"}
        keys = builder.keys()
        try:
            text, used = keys.render_file(path, [builder.testdef_dir, setup_dir], extra)
        except TemplateError as e:
            message, _ = self._message_file(session, str(e), source)
            line = re.search(r"line (\d+)", message)
            return {"ok": False, "error": message, "line": int(line.group(1)) if line else None, "component": component}
        return {"ok": True, "text": text, "component": component, "components": sorted(comps),
                "keys": {k: (self.rel(session, Path(o)) if o.startswith("/") and Path(o).is_relative_to(session.root) else o)
                         for k, o in used.items()}}

    def generate(self, session: EditSession, testdef_rel: str, setup_config: str | None = None,
                 nocheck: bool = True) -> dict:
        """Run the datapack tool on a test definition of the session, as ``datapack <testdef>`` would."""
        from testsuit.datatools.datapack.datapack_tools import DatapackError, datapack, read_baseline
        out_dir = Path(session.dir) / "generated"
        out_dir.mkdir(exist_ok=True)
        with self._quiet() as log:
            try:
                zip_path = datapack(self.path(session, testdef_rel), nocheck=nocheck, output_dir=out_dir,
                                    setup_config=setup_config)
                error = None
            except (DatapackError, SequencerError, OSError) as e:
                zip_path, error = None, str(e)
        text = ANSI.sub("", log.getvalue()).replace(f"{session.folder}/", "").replace(f"{session.dir}/", "")
        if zip_path is None:
            message, _ = self._message_file(session, error or "", testdef_rel)
            return {"ok": False, "error": message, "log": text}
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
        _git(session.root, "add", "-A")
        names = _git(session.root, "diff", "--cached", "--name-status", session.base)
        patch = _git(session.root, "diff", "--cached", session.base)
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
        _git(session.root, *ident, "commit", "--quiet", "-m", message)
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
        result = subprocess.run(["git", "-c", "credential.helper=", "fetch", "--quiet", "--tags", "origin"],
                                cwd=session.root, capture_output=True, text=True, timeout=300,
                                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **self._push_env(credentials)})
        if result.returncode:
            return {"ok": False, "error": self._scrub(result.stderr.strip(), credentials)}
        _git(session.root, "add", "-A")
        stashed = _git(session.root, "stash", "push", "--quiet", "--include-untracked", "-m", "editor", check=False)
        has_stash = bool(_git(session.root, "stash", "list").strip())
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
