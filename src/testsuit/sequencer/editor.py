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
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from pydantic import ValidationError

from testsuit.exploit.editor.service import (CriteriaFolder, KeySource, criteria_folder, forget_criteria_folder,
                                             is_scenario, is_utests)
from testsuit.reqs.backend import describe_req_backends
from testsuit.reqs.backend import walk as req_paths
from testsuit.reqs.sources import is_requirements_file
from testsuit.sequencer import registry, yamledit
from testsuit.sequencer.lab import Lab, git_state, load_lab
from testsuit.sequencer.library import without_comments
from testsuit.sequencer.util import SequencerError, now

WORK_DIRS = (".current_datapack/", ".work/")     # what the datapack tool writes next to the definitions: never committed
SKIP_DIRS = {".git", ".work", ".current_datapack", "__pycache__", "node_modules", ".pytest_cache", ".venv"}
MAX_FILES = 2000
TEST_LIMIT = 30.0                                  # seconds: a test of a requirements source (Editor) stops calling after it
ANSI = re.compile(r"\x1b\[[0-9;]*m")


# file_version() hashes files up to this size; a bigger one is versioned by its size and modification time
VERSION_HASH_BYTES = 8 << 20


def file_version(path: Path) -> str | None:
    """The version of a file as the Editor read it: a hash of its bytes (None: no such file). A save giving the
    version it started from is refused when the file changed since (another browser, a pull, a hand edit).
    A file over VERSION_HASH_BYTES (a data file) is not read, asked every 2 s: its size and modification time
    stand for its bytes."""
    try:
        stat = path.stat()
        if stat.st_size > VERSION_HASH_BYTES:
            return "s" + hashlib.sha1(f"{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:15]
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
def _inline_spec(step) -> dict | None:
    """What a step runs without a lab command (``run:``, ``wait_for:``...), as the engine reads it."""
    try:
        action = step.action
    except SequencerError:
        return None
    if action in ("do", "uses"):
        return None
    return {action: step.plugin_actions[action] if action in step.plugin_actions else getattr(step, action)}


def _step_keys(lab: Lab, needs, binding, context: dict, phase: str, params: dict, bound, spec: dict) -> dict:
    """The keys a step's command is rendered with at run time (``engine.RunContext.keys``), with placeholder
    values where only the run knows them (its id, folders): what matters is which names are defined."""
    def slot_keys(slot_id: str) -> dict:
        lc = needs.slots[slot_id]
        return {"id": lc.id, "name": lc.name or lc.id, "slot_class": lc.slot_class, "role": lc.role, "stage_slot": lc.stage_slot,
                "component": binding.slots.get(slot_id, "") if binding else "", "input_dir": "<remote root>/<slot>/input",
                "output_dir": "<remote root>/<slot>/output", "remote_root": "<remote root>"}

    def verdict(step: str) -> str:
        return "pending"
    keys = dict(context)
    keys.update({"run": {"id": "<run id>", "dir": "<run folder>", "priority": 50}, "phase": {"id": phase},
                 "slots": {c: slot_keys(c) for c in needs.slots},
                 "stage_slots": {r: {"id": r, "stage": s} for r, s in (binding.stage_slots if binding else {}).items()},
                 "params": params or {}, "succeeded": verdict, "failed": verdict, "errored": verdict, "skipped": verdict,
                 "verdict": verdict})
    if lab is not None:
        keys.update(lab.lab_keys(bound[0] if bound else None, bound[2] if bound else None))
    if bound is not None:
        concrete, lc, stage = bound
        keys["slot"] = slot_keys(lc.id)
        keys["component"] = {"id": concrete.id, "host": concrete.host, "host_alias": concrete.host_alias,
                             "label": concrete.label, "role": concrete.role, "extends": concrete.extends, "slot": lc.id,
                             "stage": stage.id if stage else "", "vars": concrete.vars_in(stage),
                             "input_dir": "<remote root>/<slot>/input", "output_dir": "<remote root>/<slot>/output",
                             "remote_root": "<remote root>", "state": "<probed state>"}
        keys["remote_root"] = "<remote root>"
    if isinstance(spec, dict) and spec.get("via"):
        keys["via"] = {"id": "<via>", "host": "", "host_alias": "", "role": "", "label": "", "vars": {}}
    return keys


# the keys a run gives its commands over those of the datapack (engine.RunContext.keys): one set by hand is hidden
RUN_KEY_NAMES = {"run", "phase", "slots", "stage_slots", "params", "succeeded", "failed", "errored", "skipped", "verdict",
                 "lab", "stage", "slot", "component", "remote_root", "via"}


def _input_counts(setup_raw: dict, config: str) -> dict[str, int]:
    """The number of inputs the setup configuration gives each of its slots (slot id -> count)."""
    types = setup_raw.get("slot_classes") or {}
    slots = ((setup_raw.get("configurations") or {}).get(config) or {}).get("slots") or {}
    counts = {}
    for slot_id, slot in slots.items():
        slot = slot if isinstance(slot, dict) else {}
        cfg = (((types.get(slot.get("slot_class")) or {}).get("configurations") or {}).get(slot.get("configuration")) or {})
        counts[str(slot_id)] = len([i for i in cfg.get("inputs") or [] if isinstance(i, dict)])
    return counts


USER_KEY = "<user key>"                            # the origin of the placeholder of a key given at each generation

# where the keys of a run come from (the explorer), by name
RUN_KEY_ORIGINS = {"run": "the run", "phase": "the run (its phase)", "params": "the sub-pipeline (its parameters)",
                   "slot": "the setup, and the lab component the run binds to the slot",
                   "slots": "the setup, and the lab components the run binds",
                   "stage_slots": "the lab stages the run binds", "component": "the lab component the run binds to the slot",
                   "stage": "lab inventory: the stage of the component", "lab": "lab inventory",
                   "remote_root": "lab inventory (tsq_remote_root)"}


def _key_origins(layers: list[tuple[dict, str]]) -> dict:
    """Where each key comes from: dotted name -> ``from`` (what sets its value) and ``overrides`` (the values it
    replaces, each with where it was set), the layers given lowest precedence first."""
    from testsuit.datatools.datapack.templating import _dotted, _leaves, expand_dotted_keys
    found: dict[str, dict] = {}
    for mapping, origin in layers:
        try:
            mapping = expand_dotted_keys(mapping)
        except Exception:
            continue
        for path, value in _leaves(mapping):
            if not path:
                continue
            name = _dotted(path)
            before = found.get(name)
            overrides = (before or {}).get("overrides", [])
            if before and before["value"] != value:     # the same value given again: only where it is set last
                overrides = [{"from": before["from"], "value": before["value"]}] + overrides
            found[name] = {"from": origin, "value": value, "overrides": overrides}
    return {name: {"from": o["from"], "overrides": [{"from": x["from"], "value": _short(x["value"])} for x in o["overrides"]]}
            for name, o in found.items()}


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= 120 else text[:117] + "..."


def _key_tree(keys: dict) -> dict:
    """Keys as plain data to browse (functions left out: they are listed apart)."""
    def plain(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): plain(v) for k, v in value.items() if not callable(v)}
        if isinstance(value, (list, tuple)):
            return [plain(v) for v in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)
    return plain(keys)


def _eval_expression(expression: str, keys: dict, where: str) -> dict:
    """An expression evaluated as a template would (keys read by key first, undefined ones are errors), in a
    sandbox, with the functions of ``where`` giving placeholders. A text holding ``{{`` or ``{%`` other than one
    ``{{ … }}`` around it all is a template (``/tmp/{{ run.id }}-img``): rendered as a string, with
    ``template`` true; any other text is an expression, as within ``{{ }}``."""
    import jinja2
    from jinja2.sandbox import SandboxedEnvironment
    text = expression.strip()
    whole = text.startswith("{{") and text.endswith("}}") and "{{" not in text[2:-2] and "}}" not in text[2:-2]
    template = not whole and ("{{" in text or "{%" in text)
    if whole:
        text = text[2:-2].strip()
    if not text:
        return {"value": None, "error": "an expression is needed"}

    class Env(SandboxedEnvironment):
        def getattr(self, obj: Any, attribute: str) -> Any:
            if isinstance(obj, dict) and attribute in obj:
                return obj[attribute]
            return super().getattr(obj, attribute)

    env = Env(undefined=jinja2.StrictUndefined, autoescape=False)
    env.filters["from_yaml"] = yaml.safe_load
    env.filters["from_json"] = json.loads
    if where == "run":
        functions = {name: (lambda step: "<verdict>") for name in ("succeeded", "failed", "errored", "skipped", "verdict")}
    else:
        functions = {"read_input": lambda path: f"<content of {path}>"}
    extra = {"template": True} if template else {}
    try:
        if template:
            return {"value": env.from_string(text).render(**{**keys, **functions}), "error": None, **extra}
        value = env.compile_expression(text, undefined_to_none=False)(**{**keys, **functions})
        if isinstance(value, jinja2.Undefined):
            str(value)                              # raises: says what is undefined
        return {"value": _key_tree({"v": value})["v"], "error": None}
    except Exception as e:                           # a syntax error, an undefined key, a type error...
        return {"value": None, "error": f"{type(e).__name__}: {e}" if not isinstance(e, jinja2.TemplateError) else str(e),
                **extra}


class _Anything(dict):
    """A run-time key whose sub-keys are not known here: any of them is defined."""

    def __contains__(self, key) -> bool:
        return True

    def __getitem__(self, key):
        return _Anything()

    def get(self, key, default=None):
        return _Anything()


def _criteria_key_problems(where: str, step, definition_dir: Path, keys: dict) -> list[str]:
    """The keys the criteria of an exploit step read (their scenario files) that neither the step's keys nor
    its ``with:`` define: its checks would fail on them."""
    from testsuit.sequencer.cmdparams import criteria_missing_keys
    from testsuit.sequencer.exploitstep import as_list
    problems = []
    for name in as_list((step.exploit or {}).get("criteria")):
        for file, key in criteria_missing_keys(definition_dir / str(name), keys):
            hint = "" if key.startswith("params.") else f", as 'params.{key.split('.')[-1]}' (read it so in the criteria)"
            problems.append(f"{where}: criteria '{name}' ({file}) read '{key}', which no dictionary, run-time key nor "
                            f"the step's 'with' defines: give it in the step's 'with'{hint}")
    return problems


def _missing_keys(value: Any, keys: dict) -> list[str]:
    """The keys the templates of a value (strings, lists and dicts) read that ``keys`` does not define."""
    from testsuit.datatools.datapack.templating import undefined_keys
    found: list[str] = []
    if isinstance(value, str):
        if "{{" in value or "{%" in value:
            found += undefined_keys(value, keys)
    elif isinstance(value, dict):
        for item in value.values():
            found += _missing_keys(item, keys)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found += _missing_keys(item, keys)
    return list(dict.fromkeys(found))


RUNTIME_KEYS = {
    "slot.id": "the step's slot (setup configuration)",
    "slot.name": "its name",
    "slot.slot_class": "its slot class",
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
    "component.extends": "the component class it extends (its implementation)",
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
        # the git command, after its -c options: "git commit", not "git -c"
        name = next((a for i, a in enumerate(args) if not a.startswith("-") and (i == 0 or args[i - 1] != "-c")), "")
        raise SequencerError(f"git {name}: {(result.stderr or result.stdout).strip()}")
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
        repos = [{"id": "lab", "kind": "lab", "label": "Lab inventory, roles and classes",
                  "folder": str(self.seq.config.lab_folder), "git": git_state(self.seq.config.lab_folder)}]
        for i, folder in enumerate(self.seq.config.workspaces):
            git = git_state(folder) if folder.exists() else None
            repos.append({"id": f"workspace{i}", "kind": "workspace", "label": f"Tests: {folder.name}",
                          "folder": str(folder), "git": git, "url": (git or {}).get("repo"), "added": None})
        for entry in self._added():
            folder = self._added_folder(entry)
            git = git_state(folder) if folder.exists() else None
            repos.append({"id": entry["id"], "kind": "workspace", "label": f"Tests: {entry['id']}", "folder": str(folder),
                          "git": git, "url": entry["url"], "added": {k: entry.get(k) for k in ("by", "at", "branch", "path")}})
        return repos

    def workspace_folders(self) -> list[Path]:
        """The folders of the tests repositories: server.workspaces, then those added from the UI."""
        return [*self.seq.config.workspaces, *(self._added_folder(e) for e in self._added())]

    # ── tests repositories added by git URL ──────────────────────────────
    # Cloned into <storage.root>/workspaces/<id>, listed in <storage.root>/workspaces.yml, next to server.workspaces.

    _added_lock = threading.Lock()

    @property
    def _added_file(self) -> Path:
        return self.seq.config.root / "workspaces.yml"

    def _added(self) -> list[dict]:
        try:
            data = yaml.safe_load(self._added_file.read_text()) or []
        except FileNotFoundError:
            return []
        return [e for e in data if isinstance(e, dict) and e.get("id") and e.get("url")] if isinstance(data, list) else []

    def _added_folder(self, entry: dict) -> Path:
        return self.seq.config.root / "workspaces" / entry["id"] / (entry.get("path") or "")

    @staticmethod
    def _check_url(url: str) -> str:
        url = url.strip()
        if not url:
            raise SequencerError("give the git URL of the repository")
        if re.match(r"^[a-z][a-z0-9+.-]*://[^/@]*:[^/@]*@", url, re.I):
            raise SequencerError("the URL holds a password or token: give it as a credential instead, "
                                 "it would be stored with the clone otherwise")
        if url.startswith("-"):
            raise SequencerError(f"invalid git URL '{url}'")
        return url

    def add_workspace(self, url: str, branch: str, name: str | None = None, path: str | None = None,
                      credentials: dict | None = None, by: str | None = None) -> dict:
        """Clone a tests repository from its git URL, with the user's credentials (used for this clone only, never
        stored), and list it with the others. branch: the one branch cloned (single-branch), pulled
        and pushed to, required. path: the folder of the repository holding the datapack definitions."""
        credentials = credentials or {}
        url = self._check_url(url)
        repo_id = (name or "").strip() or re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
        repo_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", repo_id).strip("-.").lower()
        if not repo_id or repo_id == "lab" or re.fullmatch(r"workspace\d+", repo_id):
            raise SequencerError(f"invalid repository name '{repo_id}': give another one")
        path = (path or "").strip().strip("/")
        if path and (Path(path).is_absolute() or ".." in Path(path).parts):
            raise SequencerError(f"'{path}': give a folder inside the repository")
        branch = (branch or "").strip()
        if not branch:
            raise SequencerError("give the branch to clone: only that branch is cloned, pulled and pushed to")
        if branch.startswith("-"):
            raise SequencerError(f"invalid branch '{branch}'")
        with self._added_lock:
            if any(r["id"] == repo_id for r in self.repositories()):
                raise SequencerError(f"a repository '{repo_id}' is already listed: give another name")
            dest = self.seq.config.root / "workspaces" / repo_id
            if dest.exists():
                raise SequencerError(f"'{dest}' already exists: give another name, or remove that folder")
            dest.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(["git", *self._user_git_config(url, dest.parent), "clone", "--quiet", "--single-branch",
                                     "--branch", branch, "--", url, str(dest)],
                                    capture_output=True, text=True, timeout=600,
                                    env=self._git_env(credentials))
            if result.returncode:
                shutil.rmtree(dest, ignore_errors=True)
                raise SequencerError("cannot clone the repository: " + self._scrub((result.stderr or result.stdout).strip(), credentials))
            if path and not (dest / path).is_dir():
                shutil.rmtree(dest, ignore_errors=True)
                raise SequencerError(f"the repository has no folder '{path}'")
            if not _git(dest, "symbolic-ref", "--quiet", "HEAD", check=False).strip():
                shutil.rmtree(dest, ignore_errors=True)
                raise SequencerError(f"'{branch}' is a tag, not a branch: give a branch, to push to")
            entry = {"id": repo_id, "url": url, "branch": branch, "path": path, "by": by or None, "at": now()}
            self._added_file.parent.mkdir(parents=True, exist_ok=True)
            self._added_file.write_text(yaml.safe_dump([*self._added(), entry], sort_keys=False))
        return self._repo(repo_id)

    def remove_workspace(self, repo_id: str) -> None:
        """Remove a repository added by URL: its clone, and the editing sessions on it. Only when it is clean: no
        change not committed, no commit not pushed (push them, or revert them, first)."""
        entry = next((e for e in self._added() if e["id"] == repo_id), None)
        if entry is None:
            raise SequencerError(f"'{repo_id}' was not added from the UI: remove it from server.workspaces instead"
                                 if any(r["id"] == repo_id for r in self.repositories()) else f"no repository '{repo_id}'")
        clone = self.seq.config.root / "workspaces" / repo_id
        if clone.is_dir():
            dirty = _git(clone, "status", "--porcelain", "--untracked-files=all", check=False).strip()
            ahead = _git(clone, "rev-list", "--count", "@{u}..HEAD", check=False).strip()
            if dirty or (ahead and ahead != "0"):
                raise SequencerError(f"'{repo_id}' has changes that are not pushed: push or revert them first, "
                                     f"a repository is removed only when it is clean")
        for session in [s for s in self.sessions.values() if s.repo == repo_id]:
            self.close(session.id)
        with self._added_lock:
            self._added_file.write_text(yaml.safe_dump([e for e in self._added() if e["id"] != repo_id], sort_keys=False))
        shutil.rmtree(clone, ignore_errors=True)
        self.published.pop(repo_id, None)

    def _workspace_git(self, repo_id: str) -> tuple[Path, Path]:
        """(folder, git work tree root) of a tests repository that is in git."""
        repo = self._repo(repo_id)
        if repo["kind"] != "workspace":
            raise SequencerError(f"'{repo_id}' is not a tests repository")
        folder = Path(repo["folder"]).resolve()
        if not folder.is_dir():
            raise SequencerError(f"no folder '{folder}'")
        return folder, _repo_root(folder).resolve()

    def workspace_diff(self, repo_id: str) -> dict:
        """The local changes of a tests repository's folder: what is not committed, against its last commit
        (new files included), as a list of files and a patch to apply at the root of the repository with
        `git apply` (paths from that root; folder: the workspace's folder in it). Neither the folder nor its git index is changed. Commits not pushed are counted, not shown."""
        folder, root = self._workspace_git(repo_id)
        where = ["--", folder.relative_to(root).as_posix() or "."]
        with tempfile.TemporaryDirectory() as tmp:            # a scratch index: the user's one is left as it is
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            _git(root, "read-tree", "HEAD", env=env)
            _git(root, "add", "-A", *where, env=env)
            names = _git(root, "diff", "--cached", "--name-status", "HEAD", *where, env=env)
            patch = _git(root, "diff", "--cached", "--binary", "HEAD", *where, env=env)
        files = [{"status": line.split("\t")[0], "path": line.split("\t")[-1]} for line in names.splitlines() if line]
        ahead = _git(root, "rev-list", "--count", "@{u}..HEAD", check=False).strip()
        return {"repo": repo_id, "files": files, "patch": patch, "folder": "" if folder == root else folder.relative_to(root).as_posix(),
                "base": _git(root, "rev-parse", "HEAD").strip(),
                "branch": _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip() or None,
                "ahead": int(ahead or 0)}

    def revert_workspace(self, repo_id: str, paths: list[str] | None = None) -> dict:
        """Drop the local changes of a tests repository's folder, or of some of its files (paths relative to the
        folder): each file back as in the last commit, new files deleted. Commits are not touched: no merge, no
        reset of the branch. Files ignored by git are kept."""
        folder, root = self._workspace_git(repo_id)
        before = {f["path"] for f in self.workspace_diff(repo_id)["files"]}
        targets = []
        for rel in paths or ["."]:
            target = (folder / rel).resolve()
            if not target.is_relative_to(folder) or ".git" in target.relative_to(root).parts:
                raise SequencerError(f"'{rel}' is outside the repository's folder")
            targets.append(target.relative_to(root).as_posix() or ".")
        _git(root, "reset", "--quiet", "HEAD", "--", *targets, check=False)       # unstaged: the index as committed
        for target in targets:
            if _git(root, "ls-tree", "--name-only", "HEAD", "--", target, check=False).strip() or target == ".":
                _git(root, "checkout", "--quiet", "HEAD", "--", target, check=False)
        _git(root, "clean", "--quiet", "-f", "-d", "--", *targets)
        after = {f["path"] for f in self.workspace_diff(repo_id)["files"]}
        return {"reverted": sorted(before - after), "left": sorted(after)}

    def workspace_archive(self, repo_id: str, dest: Path) -> str:
        """A zip of a tests repository's folder as it is now (local changes included; no .git), written to dest.
        Returns the name to download it as: <repo>-<commit>[-modified].zip."""
        import zipfile
        repo = self._repo(repo_id)
        if repo["kind"] != "workspace":
            raise SequencerError(f"'{repo_id}' is not a tests repository")
        folder = Path(repo["folder"]).resolve()
        if not folder.is_dir():
            raise SequencerError(f"no folder '{folder}'")
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for dirpath, dirnames, filenames in os.walk(folder):
                dirnames[:] = sorted(d for d in dirnames if d not in (".git", ".current_datapack"))
                for name in sorted(filenames):
                    path = Path(dirpath) / name
                    if path.is_file():
                        z.write(path, Path(repo_id) / path.relative_to(folder))
        git = repo["git"] or {}
        label = git.get("tag") or (git.get("commit") or "")[:10] or "files"
        return f"{repo_id}-{label}{'' if git.get('clean', True) else '-modified'}.zip"

    def workspace_status(self, repo_id: str) -> dict:
        """What changed in a tests repository's folder, to follow the changes made meanwhile as on the Lab page:
        its git state, its local changes (files not committed, commits not pushed), the commits of the upstream
        branch it does not have (as last fetched), the last push from the Editor when it is not checked out
        there, and a stamp of its files that changes whenever one of them does."""
        repo = self._repo(repo_id)
        if repo["kind"] != "workspace":
            raise SequencerError(f"'{repo_id}' is not a tests repository")
        folder = Path(repo["folder"])
        if not folder.is_dir():
            raise SequencerError(f"no folder '{folder}'")
        git = repo["git"]
        stamp = hashlib.sha1((git or {}).get("commit", "").encode() if git else b"")
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                try:
                    st = (Path(dirpath) / name).stat()
                except OSError:
                    continue
                stamp.update(f"{dirpath}/{name}:{st.st_mtime_ns}:{st.st_size}\n".encode())
        result = {"repo": repo_id, "git": git, "stamp": stamp.hexdigest()[:16], "changes": [], "ahead": 0,
                  "behind": 0, "published": None}
        if git is None:
            return result
        status = _git(folder, "status", "--porcelain", "--untracked-files=all", "--", ".", check=False)
        result["changes"] = [{"status": line[:2].strip(), "path": line[3:]} for line in status.splitlines() if line.strip()]
        counts = _git(folder, "rev-list", "--left-right", "--count", "HEAD...@{u}", check=False).split()
        if len(counts) == 2:
            result["ahead"], result["behind"] = int(counts[0]), int(counts[1])
        published = self.published.get(repo_id)
        if published:
            result["published"] = {**published, "newer": not self._contains(folder, published["commit"])}
        return result

    @staticmethod
    def _contains(folder: Path, commit: str) -> bool:
        return subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=folder,
                              capture_output=True).returncode == 0

    def pull_workspace(self, repo_id: str, credentials: dict | None = None) -> dict:
        """Fetch a tests repository's folder and bring its branch to the latest upstream commit (fast-forward
        only). Local changes not committed stay as they are, unless upstream changed one of those files: git
        then refuses, and nothing is changed (commit and push them, or revert them, first). Refused too when the
        folder has commits not pushed. The credentials are the user's, used for this fetch only (none: a public
        repository, or one tsqd may read)."""
        credentials = credentials or {}
        repo = self._repo(repo_id)
        if repo["kind"] != "workspace":
            raise SequencerError(f"'{repo_id}' is not a tests repository: the lab is pulled with a reload")
        folder = Path(repo["folder"])
        root = _repo_root(folder)
        branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip()
        if not branch:
            raise SequencerError(f"'{repo_id}' is on no branch (detached HEAD): nothing to pull")
        before = _git(root, "rev-parse", "HEAD").strip()
        result = subprocess.run(["git", *self._user_git_config(self._origin(root), root), "fetch", "--quiet", "--tags", "origin"],
                                cwd=root, capture_output=True, text=True, timeout=300,
                                env=self._git_env(credentials))
        if result.returncode:
            return {"ok": False, "error": self._scrub((result.stderr or result.stdout).strip(), credentials),
                    "hint": "check the credentials and their rights on the repository"}
        merged = subprocess.run(["git", "merge", "--quiet", "--ff-only", f"origin/{branch}"], cwd=root,
                                capture_output=True, text=True)
        if merged.returncode:
            error = (merged.stderr or merged.stdout).strip()
            overwritten = "would be overwritten" in error
            return {"ok": False, "error": error, "conflict": overwritten,
                    "hint": ("upstream changed files that have local changes here: commit and push them, or revert "
                             "them, first" if overwritten else "the folder has commits that are not pushed: push them first")}
        after = _git(root, "rev-parse", "HEAD").strip()
        return {"ok": True, "before": before, "commit": after, "updated": before != after, "git": git_state(folder)}

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
            f.write("".join(f"\n{d}" for d in WORK_DIRS) + "\n")
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
            known = (exclude.read_text() if exclude.is_file() else "").split()
            missing = [d for d in WORK_DIRS if d not in known]
            if missing:
                exclude.parent.mkdir(parents=True, exist_ok=True)
                with open(exclude, "a") as f:
                    f.write("".join(f"\n{d}" for d in missing) + "\n")
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
            if not session.local:
                forget_criteria_folder(session.folder)
            shutil.rmtree(session.dir, ignore_errors=True)

    def criteria(self, session: EditSession) -> CriteriaFolder:
        """The criteria editor's services on the session's folder: scenarios rendered with the keys of their
        test definition, as the datapack tool resolves them."""
        return criteria_folder(session.folder, keys_for=lambda folder: self.criteria_keys(session, folder))

    def criteria_keys(self, session: EditSession, folder: Path) -> KeySource | None:
        """The keys of the test definition a scenario folder belongs to (None: not in a test definition, or
        its keys cannot be resolved: the criteria editor then reads its dictionaries itself)."""
        for parent in [folder, *folder.parents]:
            testdef = next((parent / n for n in ("testdef.yml", "testdef.yaml") if (parent / n).is_file()), None)
            if testdef is not None:
                break
            if parent == session.folder:
                return None
        else:
            return None
        try:
            builder, _, _ = self._builder(session, self.rel(session, testdef))
            values = builder.keys().resolve_all()
        except Exception:
            return None
        return KeySource([], values, f"keys of {self.rel(session, testdef)}")

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
        """What a file is: inventory, library, setup, testdef, subpipelines, dictionary, template, criteria (a
        mexploit scenario file), utests (unit tests of a scenario), or other."""
        path = self.path(session, rel)
        if session.kind == "lab" and path == self._inventory(session):
            return "inventory"
        if path.suffix not in (".yml", ".yaml", ".j2", ".jinja", ".tmpl") and "templates" not in path.parts:
            return "other"
        if is_scenario(path.name):
            return "criteria"
        if is_utests(path.name):
            return "utests"
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
        if is_requirements_file(path.name, data):
            return "requirements"
        if "setup_file" in data or "pipeline" in data:
            return "testdef"
        if "configurations" in data and ({"slot_classes", "component_types", "datarepositories"} & set(data)):
            return "setup"
        if ({"roles", "component_classes", "models", "components"} & set(data)) and not set(data) - {"roles", "component_classes", "models", "components"}:
            return "library"                 # (models:, components: former names of component_classes, refused when loaded)
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
        if kind in ("inventory", "library", "setup", "testdef", "subpipelines", "dictionary", "requirements"):
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
        section = "roles" if kind == "role" else "component_classes"

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
                    hit = follow((data or {}).get("component_classes"), ("roles",)) or hit
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
                outputs = lib.role_outputs(name)
            except SequencerError:                        # extends a role that is not there (renamed?): itself only
                chain = [name]
                commands = {c: {"description": d, "from": name, "params": r.params.get(c, {})} for c, d in r.commands.items()}
                inputs = [{**i, "from": name} for i in r.inputs]
                outputs = [{**o, "from": name} for o in r.outputs]
            roles[name] = {"extends": r.extends, "description": r.description, "source": r.source, "chain": chain,
                           "commands": commands, "own_commands": sorted(r.commands), "inputs": inputs, "outputs": outputs,
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
        from testsuit.datatools.datapack.campaign import is_campaign_file, looks_like_campaign
        result = {"testdefs": [], "setups": [], "subpipelines": {}, "subpipeline_files": [], "shared_subpipelines": {},
                  "dictionaries": [], "templates": [], "campaigns": [], "requirements": []}
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
                if is_requirements_file(name, data):
                    result["requirements"].append(rel)
                    continue
                if is_campaign_file(rel) or looks_like_campaign(data):
                    result["campaigns"].append({"path": rel, "id": data.get("id"), "name": data.get("name"),
                                                "testdefs": len(data.get("testdefs") or [])})
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
                elif "configurations" in data and ({"slot_classes", "component_types", "datarepositories"} & set(data)):
                    result["setups"].append(self._setup_summary(rel, data))
                elif "subpipelines" in data and isinstance(data["subpipelines"], dict):
                    result["subpipeline_files"].append(rel)
                    for sub, spec in data["subpipelines"].items():
                        result["subpipelines"].setdefault(sub, {"file": rel, **self._sub_summary(spec)})
                        # only these can be used by another test definition (pipeline.include)
                        result["shared_subpipelines"].setdefault(sub, {"file": rel, **self._sub_summary(spec)})
                elif "all" not in data and not (({"roles", "component_classes", "models", "components"} & set(data))
                                                 and not set(data) - {"roles", "component_classes", "models", "components"}):
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
            slots = {c: {k: v.get(k) for k in ("name", "slot_class", "configuration", "stage_slot", "role", "needs")}
                     for c, v in (cfg.get("slots") or {}).items() if isinstance(v, dict)}
            configurations[name] = {"stage_slots": stage_slots, "slots": slots}
        types = {}
        for name, t in (data.get("slot_classes") or {}).items():
            t = t if isinstance(t, dict) else {}
            types[name] = {"configurations": sorted(t.get("configurations") or {}),
                           "description": t.get("description", ""), "role": t.get("role", "")}
        datasets = [{"id": ds.get("id"), "version": ds.get("version")}
                    for repo in data.get("datarepositories") or [] for src in repo.get("datasources") or []
                    for ds in src.get("datasets") or [] if isinstance(ds, dict)]
        return {"path": rel, "id": data.get("id"), "name": data.get("name"), "configurations": configurations,
                "slot_classes": types, "datasets": datasets, "dictionaries": data.get("dictionaries") or []}

    def context(self, session: EditSession) -> dict:
        """What the helpers of the forms and the YAML view need (README section 13.2)."""
        from testsuit.datatools.datapack import generators, importers, processors
        lab, lab_error = self.lab_for(session)
        if session.kind == "workspace":
            workspace = self.scan_workspace(session.folder, session.folder)
        else:                                          # what the datapacks of the workspaces ask of the lab
            workspace = {"testdefs": [], "setups": [], "subpipelines": {}, "campaigns": []}
            for folder in self.workspace_folders():
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
        repository = next((r for r in self.repositories() if r["id"] == session.repo), None)
        return {
            "session": self.describe(session),
            "repository": repository and {k: repository.get(k) for k in ("id", "kind", "label", "url", "added")}
            | {"branch": (repository.get("git") or {}).get("branch"), "workspaces": len(self.workspace_folders())},
            "lab": described, "lab_error": lab_error,
            "workspace": workspace,
            "transports": sorted({"local", "ssh", *registry.transports}),
            "step_types": sorted(registry.step_types), "ready_checks": sorted(registry.ready_checks),
            "state_probes": sorted(registry.state_probes),
            "importers": sorted(importers._importers), "generators": sorted(generators._generators),
            "processors": sorted(processors._processors),
            "req_backends": describe_req_backends(),
            "runtime_keys": RUNTIME_KEYS,
            "inventory": self.rel(session, self._inventory(session)) if session.kind == "lab" else None,
        }

    # ── checks ───────────────────────────────────────────────────────────

    def check_file(self, session: EditSession, rel: str) -> list[dict]:
        """Problems of one file: YAML syntax, then what it means for the lab or the datapacks; and the requirements
        its @tr tags name."""
        path = self.path(session, rel)
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
        tags = self.check_req_tags(session, rel, text) if "@tr{" in text or "@uid{" in text else []
        return self._check_file(session, rel, text) + tags

    def check_req_tags(self, session: EditSession, rel: str, text: str) -> list[dict]:
        """The @uid and @tr tags of an input file (sequencer README section 10.3): a requirement a @tr names that
        is unknown, deleted or rejected is an error (generation would fail), a test case id used twice in the file
        an error too."""
        from testsuit.reqs.links import scan_text
        from testsuit.reqs.snapshot import resolve
        from testsuit.reqs.sources import Sources, find_sources_file
        if session.kind != "workspace":
            return []
        cases, links, problems = scan_text(text, rel)
        out = []

        def add(line: int, message: str, severity: str = "error") -> None:
            out.append({"file": rel, "line": line, "column": 1, "severity": severity, "message": message})
        for p in problems:
            line = int(p.split(":", 2)[1]) if p.count(":") >= 2 and p.split(":", 2)[1].isdigit() else 1
            add(line, p.split(": ", 1)[-1])
        seen: dict[str, int] = {}
        for c in cases:
            if c["uid"] in seen:
                add(c["line"], f"test case '{c['uid']}' already defined line {seen[c['uid']]}")
            seen.setdefault(c["uid"], c["line"])
        if not links:
            return out
        file = find_sources_file(self.path(session, rel))
        if file is None:
            add(links[0]["line"], "@tr names requirements, but there is no requirements.yml in this repository", "warning")
            return out
        try:
            sources = Sources(file)
            refs = list(dict.fromkeys(r for lk in links for r in lk["reqs"]))
            _, keys, found, warnings = resolve(sources, refs)      # one call per source
            lines: dict[str, list[int]] = {}
            for lk in links:
                for r in lk["reqs"]:
                    lines.setdefault(keys.get(r, r), []).append(lk["line"])
            for severity, messages in (("error", found), ("warning", warnings)):
                for p in messages:
                    m = re.search(r"requirement '([^']+)'", p)
                    for line in (lines.get(m[1]) if m and m[1] in lines else [links[0]["line"]]):
                        add(line, f"requirements: {p}", severity)
        except Exception as e:                     # a source that cannot be read here: said once
            add(links[0]["line"], f"requirements not checked: {e}", "warning")
        return out

    def _check_file(self, session: EditSession, rel: str, text: str) -> list[dict]:
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
            if kind == "requirements":
                return self.check_requirements(session, rel, text)
        except SequencerError as e:
            line, column = _find_line(text, str(e))
            return [{"file": rel, "line": line, "column": column, "severity": "error", "message": str(e)}]
        return []

    def check_requirements(self, session: EditSession, rel: str, text: str) -> list[dict]:
        """A sources file reads, and so does each of its sources (a backend no plugin registers, a file that is not
        there; a credential whose variable is not set, or file not readable, here is a warning: it may be set where datapacks are generated);
        a requirements file of the YAML backend reads."""
        from testsuit.reqs.backend import ReqError, YamlBackend
        from testsuit.reqs.sources import SOURCES_FILE, Sources
        path = self.path(session, rel)
        problems = []

        def add(message: str, severity: str = "error", near: str | None = None) -> None:
            message = message.replace(str(path.parent) + os.sep, "")
            line, column = _find_line(text, near or message)
            problems.append({"file": rel, "line": line, "column": column, "severity": severity, "message": message})

        try:
            if path.name != SOURCES_FILE:
                YamlBackend({"file": path.name}, None, path.parent)
                return []
            sources = Sources(path)
        except ReqError as e:
            add(str(e))
            return problems
        for name in sources.names:
            try:
                sources.backend(name)
            except ReqError as e:
                add(str(e), "warning" if ("is not set" in str(e) or "is not readable" in str(e)) else "error", f"'{name}'")
        return problems

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
        for folder in self.workspace_folders():
            for setup in self.scan_workspace(folder, folder)["setups"]:
                for cfg_name, cfg in setup["configurations"].items():
                    needs = self._needs_or_none(setup, cfg_name, folder)
                    if needs is None:
                        continue
                    from testsuit.sequencer.intake import check_against_lab
                    for problem in check_against_lab(SimpleNamespace(needs=needs), lab):
                        line, column = _find_line(text, problem)
                        problems.append({"file": inv_rel, "line": line, "column": column, "severity": "warning",
                                         "message": f"Datapack setup {setup['id']} ({cfg_name}, workspace "
                                                    f"{folder.name}) does not match this lab: {problem}"})
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
        from testsuit.sequencer.model import Pipeline, comment_problems
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
        flat = self._flatten(lab, needs, pipeline, flats, self._datapack_keys(session, rel, None), self.path(session, rel).parent)
        for severity, found in (("error", flat["problems"] + flat["exploit_problems"]), ("warning", flat["warnings"])):
            for problem in found:
                line, column = _find_line(text, problem)
                problems.append({"file": rel, "line": line, "column": column, "severity": severity, "message": problem})
        for problem in comment_problems(pipeline):
            line, column = _find_line(text, problem)
            problems.append({"file": rel, "line": line, "column": column, "severity": "warning", "message": problem})
        for name, problem in self._criteria_problems(session, rel):
            line, column = _find_line(text, name)
            problems.append({"file": rel, "line": line, "column": column, "severity": "error", "message": problem})
        for severity, problem in self._reqs_problems(session, rel, raw):
            line, column = _find_line(text, problem)
            problems.append({"file": rel, "line": line, "column": column, "severity": severity, "message": problem})
        return problems

    def requirements(self, session: EditSession, source: str | None = None, campaign: str | None = None) -> dict:
        out, cov = self.requirements_coverage(session, source, campaign)
        return {**out, **({"coverage": cov.as_dict()} if cov is not None else {})}

    def requirement_item(self, session: EditSession, source: str, req_id: str) -> dict:
        """One requirement in full, as generation reads it (the rest backend: with its details calls): what the
        Requirements tab shows when it is opened."""
        from testsuit.reqs.backend import ReqError
        from testsuit.reqs.model import Ref
        from testsuit.reqs.sources import Sources, find_sources_file
        file = find_sources_file(session.folder)
        if file is None:
            raise SequencerError("no requirements.yml in this repository")
        try:
            sources = Sources(file)
            if source not in sources.names:
                raise SequencerError(f"no source '{source}' (sources: {', '.join(sources.names)})")
            req = sources.get([Ref(source, req_id)])[Ref(source, req_id)]
        except ReqError as e:
            return {"error": str(e)}
        if req is None:
            return {"error": f"no requirement '{req_id}' in the source '{source}'"}
        return {"requirement": req.as_dict()}

    def requirements_search(self, session: EditSession, path: str | None = None) -> dict:
        """Every active requirement of every source of the sources file of the tests repository, by the reference a
        test definition writes (its id, prefixed by its source unless the default one): the quick search of the
        Requirements field of the test definition form. ``path``: the file being edited (the nearest sources file)."""
        from testsuit.reqs.backend import ReqError, list_requirements
        from testsuit.reqs.sources import Sources, find_sources_file
        start = self.path(session, path) if path else session.folder
        file = find_sources_file(start if start.exists() else session.folder) or find_sources_file(session.folder)
        out: dict[str, Any] = {"sources_file": None, "default": None, "items": [], "errors": []}
        if file is None:
            return out
        out["sources_file"] = self.rel(session, file) if file.is_relative_to(session.folder) else str(file)
        try:
            sources = Sources(file)
        except ReqError as e:
            out["errors"].append(str(e))
            return out
        out["default"] = sources.default
        for name in sources.names:
            try:
                reqs = list_requirements(sources.backend(name), "", True)
            except ReqError as e:
                out["errors"].append(f"{name}: {e}")
                continue
            for r in reqs:
                if r.inactive:
                    continue
                key = r.id if name == sources.default else f"{name}:{r.id}"
                out["items"].append({"key": key, "id": r.id, "source": name, "title": r.title, "path": r.path,
                                     "status": r.status})
        return out

    def requirements_test(self, session: EditSession, path: str, source: str, spec: dict | None = None,
                          upto: str | None = None, expr: str | None = None, on: str = "list",
                          classes: dict | None = None, klass: str | None = None, values: dict | None = None) -> dict:
        """Read one source of a sources file of the session, as datapack generation would: how many requirements, how
        long it took, the tree and the first requirements, or what went wrong (Editor form). ``spec``: the source as
        being edited (default: as saved). ``upto``: only the calls up to a stage of a backend that has stages (the rest
        backend: ``step:N``, ``list``). ``calls``: each call to the server, for a backend that describes them (its
        URL, headers, status, answer, what it extracted; credentials masked). ``classes``: the classes as being edited.
        ``klass``: test that class instead of a source (``spec``: the class as being edited), with ``values`` for its
        variables (the test values, given at each test: a class has none of its own)."""
        import time

        from testsuit.reqs.backend import ReqError, list_requirements
        from testsuit.reqs.sources import Sources
        start = time.monotonic()
        out: dict[str, Any] = {"source": source, "ok": False, **({"upto": upto} if upto else {})}
        backend = None
        try:
            sources = Sources(self.path(session, path))
            for name, cls in (classes or {}).items():
                sources.replace_class(str(name), cls)
            if klass:
                if spec is not None:
                    sources.replace_class(klass, spec)
                elif klass not in sources.classes:
                    raise ReqError(f"no class '{klass}' in the file")
                source = klass
                given = {str(k): v for k, v in (values or {}).items() if v not in (None, "")}
                sources.replace(source, {"class": klass, **({"vars": given} if given else {})})
            elif spec is not None:
                sources.replace(source, spec)
            backend = sources.backend(source)
            backend.trace = []
            if hasattr(backend, "deadline"):        # a test answers within about 30 s, whatever the server does
                backend.test_limit = TEST_LIMIT
                backend.deadline = start + TEST_LIMIT
            if upto:
                if not hasattr(backend, "probe"):
                    raise ReqError(f"the {sources.specs[source]['backend']} backend has no stages to test")
                probe = backend.probe(upto, expr=expr, on=on) if expr is not None else backend.probe(upto)
            else:
                reqs = list_requirements(backend, "", True)
                tree = backend.tree()
        except ReqError as e:
            out["error"] = str(e).replace(str(session.folder) + os.sep, "")
        except Exception as e:                 # a plugin's bug: shown, not raised
            out["error"] = f"{type(e).__name__}: {e}"
        else:
            out["ok"] = True
            if upto:
                out.update({**probe, "vars": {**sources.vars(source), **probe.get("vars", {})}})
            else:
                out.update({"count": len(reqs), "nodes": sum(1 for _ in req_paths(tree)),
                            "sample": [r.as_dict() for r in reqs[:5]], "context": sources.context(source),
                            "vars": {**sources.vars(source), **backend.resolved_vars()}})
        if backend is not None and backend.trace:
            out["calls"] = backend.trace
            if getattr(backend, "trace_more", 0):
                out["calls_more"] = backend.trace_more
        out["seconds"] = round(time.monotonic() - start, 2)
        return out

    def _source_edit(self, session: EditSession, file: Path, sources, source: str) -> dict | None:
        """Where the Editor edits a source: the YAML file of the requirements of a yaml source, else the sources
        file at that source (its backend, options, credentials); None outside the session."""
        spec = sources.specs.get(source) or {}
        if str(spec.get("backend", "")).lower() == "yaml":
            data = (file.parent / str((spec.get("options") or {}).get("file") or file.name)).resolve()
            if data.is_relative_to(session.folder.resolve()) and data.is_file():
                return {"file": self.rel(session, data), "source": None}
        if not file.is_relative_to(session.folder):
            return None
        return {"file": self.rel(session, file), "source": source}

    def requirements_coverage(self, session: EditSession, source: str | None = None, campaign: str | None = None,
                              node: str = "", children: bool = True):
        """(description, Coverage or None): the requirements of a tests repository and how its test definitions cover them (Datapacks, Requirements; SPEC
        §5.5): the tree of a source, every requirement of it, and the coverage by the latest run of each test
        definition that has one (else as linked); with no ``source`` (and several), every source, each a top node of
        the tree and of the paths. ``campaign``: only the test definitions of that campaign file. ``node``: only the
        requirements of that node of the tree (``children``: and of its descendants), as the page shows them."""
        from testsuit.reqs.backend import ReqError
        from testsuit.reqs.coverage import Coverage, add_sources, add_test, read_testdef, snapshot_of_run, test_of_run
        from testsuit.reqs.sources import Sources, find_sources_file
        if session.kind != "workspace":
            raise SequencerError("requirements are those of a tests repository")
        testdefs = self.scan_workspace(session.folder, session.folder)["testdefs"]
        if campaign:
            from testsuit.datatools.datapack.campaign import load_campaign
            path = self.path(session, campaign)
            try:
                entries = load_campaign(path).testdefs
            except Exception as e:
                raise SequencerError(f"campaign '{campaign}': {e}") from e
            wanted = {self.rel(session, path.parent / e.testdef) for e in entries}
            testdefs = [t for t in testdefs if t["path"] in wanted]
        file = find_sources_file(session.folder) or next(
            (f for f in (find_sources_file(self.path(session, t["path"])) for t in testdefs) if f), None)
        out: dict[str, Any] = {"sources_file": None, "sources": [], "source": None, "tree": []}
        if file is None:
            return out, None
        out["sources_file"] = self.rel(session, file) if file.is_relative_to(session.folder) else str(file)
        try:
            sources = Sources(file)
            # the source picked; none (or an unknown one): every source, a top node each (the only one when alone)
            source = source if source in sources.names else (sources.names[0] if len(sources.names) == 1 else None)
            out.update({"sources": sources.names, "source": source,                 # kept when the source fails
                        "source_cards": [{"name": n, "backend": str(sources.specs[n].get("backend")),
                                          **({"label": str(sources.specs[n]["label"])} if sources.specs[n].get("label") else {}),
                                          **({"default": True} if n == sources.default else {})} for n in sources.names],
                        "edit": self._source_edit(session, file, sources, source)})
            out["tree"] = [n.as_dict() for n in sources.tree(source)] if source else [
                {"id": n, "label": f"{sources.specs[n]['label']} ({n})" if sources.specs[n].get("label") else n, "children": [c.as_dict() for c in sources.tree(n)]}
                for n in sources.names]
        except ReqError as e:
            out["error"] = str(e)
            if "edit" not in out and file.is_relative_to(session.folder):      # the sources file does not read
                out["edit"] = {"file": out["sources_file"], "source": None}
            return out, None
        latest: dict[str, dict] = {}
        for run in self.seq.store.runs(["done", "cancelled"], limit=2000):     # newest first
            if run.get("testdef") and run["testdef"] not in latest and snapshot_of_run(run) is not None:
                latest[run["testdef"]] = run
        cov = Coverage()
        for t in testdefs:
            run = latest.get(str(t.get("id")))
            try:
                if run is not None:
                    snap = snapshot_of_run(run)
                    test = test_of_run(run, snap)
                    found = (snap, test)
                else:
                    found = read_testdef(self.path(session, t["path"]))
            except Exception as e:
                cov.problems.append(f"{t['path']}: {e}")
                continue
            if found is not None:
                found[1].name = str(t.get("name") or "")
                found[1].origin = t["path"]
                add_test(cov, *found)
        if source:              # one source picked: only its requirements, and the links of the tests to them
            for t in cov.tests:
                t.links = [lk for lk in t.links if sources.ref(lk.req).source == source]
        try:
            add_sources(cov, sources, [f"{n}:" for n in ([source] if source else sources.names)], True, live=True)
        except Exception as e:
            cov.problems.append(f"requirements of '{source or ', '.join(sources.names)}' not read: {e}")
        if not source:          # every source: each one a top node of the paths, as in the tree
            for key in cov.rows():
                req = cov.requirements.setdefault(key, {})
                req["path"] = "/".join(p for p in (sources.ref(key).source, str(req.get("path") or "").strip("/")) if p)
        node = node.strip("/")
        if node:                # the node picked in the tree: its requirements only, and the links to them
            def under(path: str) -> bool:
                return path == node or (children and path.startswith(node + "/"))
            keep = {k for k in cov.rows() if under(str(cov.requirements.get(k, {}).get("path") or "").strip("/"))}
            cov.universe = [k for k in cov.universe or [] if k in keep]
            for t in cov.tests:
                t.links = [lk for lk in t.links if lk.req in keep]
        # what the page and the report cover, said in words
        names = [source] if source else sources.names
        out["scope"] = {"sources": [{"name": n, **({"label": str(sources.specs[n]["label"])} if sources.specs[n].get("label") else {})}
                                    for n in names],
                        "all_sources": not source and len(sources.names) > 1,
                        **({"node": node, "children": children} if node else {}),
                        **({"campaign": campaign} if campaign else {})}
        return out, cov

    def _reqs_problems(self, session: EditSession, testdef_rel: str, raw: dict) -> list[tuple[str, str]]:
        """(severity, problem) of the requirements the test definition and its checks list (SPEC section 5.4):
        unknown, deleted or rejected ones are errors (the generation fails without --nocheck), pinned versions that
        changed since are warnings."""
        from testsuit.reqs.snapshot import snapshot_of_testdef
        path = self.path(session, testdef_rel)
        try:
            snap = snapshot_of_testdef(path, raw)
        except Exception as e:                     # a backend that fails: say so, the rest of the checks go on
            return [("warning", f"requirements not checked: {e}")]
        if snap is None:
            return []
        where: dict[str, str] = {}
        for c in snap.get("checks") or []:
            for r in c["reqs"]:
                where.setdefault(r, f"check '{c['check']}' of {c['criteria']}/{c['scenario']}/{c['file']}")
        own = set(snap.get("testdef") or [])

        def located(problem: str) -> str:
            m = re.search(r"requirement '([^']+)'", problem)
            if m and m[1] not in own and m[1] in where:
                return f"{problem} (listed by {where[m[1]]})"
            return problem
        return ([("error", "requirements: " + located(p)) for p in snap.get("problems") or []]
                + [("warning", "requirements: " + located(w)) for w in snap.get("warnings") or []])

    def _criteria_problems(self, session: EditSession, testdef_rel: str) -> list[tuple[str, str]]:
        """(criteria, problem) for each criteria an exploit step names that is not there: the datapack could
        not be generated (paths relative to the test definition)."""
        from testsuit.sequencer.exploitstep import pipeline_exploit_steps
        path = self.path(session, testdef_rel)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return []
        out = []
        for step in pipeline_exploit_steps(raw.get("pipeline") if isinstance(raw, dict) else None, path.parent):
            for name in step["criteria"]:
                if not (path.parent / name).exists():
                    out.append((name, f"step '{step['id']}': criteria '{name}' not found (exploit criteria are "
                                      "scenario folders, relative to the test definition)"))
        return out

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

    def run_problems(self, session: EditSession, testdef_rel: str, setup_config: str | None = None,
                     by_hand: dict | None = None) -> list[str]:
        """The pipeline and lab checks of the synthesis, on their own (the checks before generating, step by step).
        ``by_hand``: the keys set by hand for the generation (dotted name -> value), defined for the commands too."""
        return self.run_checks(session, testdef_rel, setup_config, by_hand)["problems"]

    def run_checks(self, session: EditSession, testdef_rel: str, setup_config: str | None = None,
                   by_hand: dict | None = None) -> dict:
        """``problems``: those of ``run_problems``; ``missing_keys``: the keys the commands of the steps read that
        nothing defines and that can be set by hand for the generation (not those of the run, ``component.*``...)."""
        try:
            _, testdef, _ = self._builder(session, testdef_rel, setup_config)
        except SequencerError:
            raise
        except Exception as e:
            raise SequencerError(str(e)) from e
        missing: list[str] = []
        problems = self._run_problems(session, testdef_rel, testdef.setup_config, by_hand, missing)
        return {"problems": problems, "missing_keys": missing}

    def slot_keys(self, session: EditSession, testdef_rel: str, slot: str, setup_config: str | None = None,
                  stage: str | None = None, component: str | None = None) -> dict:
        """Every key a slot of a test definition can use, as trees to browse (the keys explorer of the Datapacks
        page): ``generation``, those of its template files, rendered at generation (the dictionary and built-in
        keys there are also those of its commands, frozen in the datapack), and ``run``, the keys only the
        commands its steps run have, on the lab component the slot would be bound to now (``component``,
        ``stage``, ``lab``, ``run``...). Computed on demand. ``stage`` and ``component`` choose the lab stage and
        component it is bound to, among ``stages`` and ``components``, those that fit it (any of them by default)."""
        from testsuit.datatools.datapack.datapack_tools import load_yaml_mapping
        from testsuit.datatools.datapack.templating import TemplateError
        try:
            builder, testdef, raw = self._builder(session, testdef_rel, setup_config)
            keys = builder.keys()
        except SequencerError:
            raise
        except Exception as e:
            raise SequencerError(str(e)) from e
        try:
            context, error = keys.resolve_all(), None
        except TemplateError as e:                   # the names are still there, as written
            context, error = keys.data, str(e)
        slots = ((raw.get("configurations") or {}).get(testdef.setup_config) or {}).get("slots") or {}
        if slot not in slots:
            raise SequencerError(f"no slot '{slot}' in setup configuration '{testdef.setup_config}'")
        comp = slots[slot] if isinstance(slots[slot], dict) else {}
        generation = {**context,
                      "slot": {"id": slot, "name": comp.get("name", ""), "slot_class": comp.get("slot_class", ""),
                               "configuration": comp.get("configuration", ""), "input_dir": f"<{slot} input folder>"},
                      "slots": {n: {"id": n, "input_dir": f"<{n} input folder>"} for n in comp.get("needs") or []}}
        def shown(origin: str) -> str:
            path = Path(origin)
            if origin.startswith("/") and path.resolve().is_relative_to(session.root.resolve()):
                return self.rel(session, path)
            return {"<datapack>": "built in", "<dataset placeholder>": "the baseline (a dataset)",
                    USER_KEY: "given at each generation (user_keys of the test definition)"}.get(origin, origin)
        layers: list[tuple[dict, str]] = []                # as builder.keys() merges them, lowest precedence first
        for layer in ("setup", "datasets", "testdef"):
            if layer == "datasets":
                layers += [(mapping, shown(origin)) for mapping, origin in builder.dataset_keys]
            else:
                for file_layer, path in builder.dictionaries:
                    if file_layer == layer:
                        try:
                            layers.append((load_yaml_mapping(path), shown(path.as_posix())))
                        except Exception:              # an unreadable file: the problems of the page say so
                            pass
        layers.append((builder.builtin, "built in"))
        layers.append(({"slot": generation["slot"], "slots": generation["slots"]}, "the setup (this slot)"))
        result: dict[str, Any] = {"slot": slot, "error": error, "generation": _key_tree(generation),
                                  "generation_origins": _key_origins(layers),
                                  "generation_functions": ["read_input('<slot>/<file>')"] if comp.get("needs") else [],
                                  "run": None, "component": None, "stages": [], "components": [], "why_not": None}
        lab, _ = self.lab_for(session)
        try:
            _, _, _, _, needs, pipeline, _ = self._testdef_parts(session, testdef_rel, testdef.setup_config)
        except Exception as e:                       # an invalid pipeline: no binding to show
            result["why_not"] = f"the pipeline cannot be read: {_describe(e) if not isinstance(e, SequencerError) else e}"
            return result
        binding, reasons = self._slot_binding(lab, needs, pipeline.phases, slot, stage, component, result)
        if binding is None or slot not in binding.slots:
            result["why_not"] = "the slot cannot be bound to the lab now" + (f": {'; '.join(reasons)}" if reasons else "")
            return result
        concrete = lab.inventory.components[binding.slots[slot]]
        stage = lab.inventory.stages.get(binding.stage_slots.get(needs.slots[slot].stage_slot, ""))
        run_keys = _step_keys(lab, needs, binding, {}, "<phase>", {}, (concrete, needs.slots[slot], stage), {})
        from testsuit.sequencer.inventory import _own
        def without(tree: dict, removed: dict) -> dict:     # the variables of its groups: without those of the host
            return {k: (without(v, removed[k]) if isinstance(v, dict) and isinstance(removed.get(k), dict) else v)
                    for k, v in tree.items() if k not in removed or isinstance(removed[k], dict) and isinstance(v, dict)}
        var_layers = [({"component": {"vars": without(concrete.vars, concrete._host_tree)}},
                       f"lab inventory: the groups of {concrete.id}")]
        if stage is not None:
            var_layers.append(({"component": {"vars": _own(stage.vars)}}, f"lab inventory: stage {stage.id}"))
        var_layers.append(({"component": {"vars": concrete._host_tree}}, f"lab inventory: host {concrete.id}"))
        run_origins = _key_origins(var_layers)
        run_origins.update({name: {"from": what} for name, what in RUN_KEY_ORIGINS.items()})
        result.update(run_origins=run_origins)
        result.update(component=concrete.id, stage=stage.id if stage else None, run=_key_tree(run_keys),
                      run_functions=["succeeded('<step>')", "failed('<step>')", "errored('<step>')", "skipped('<step>')",
                                     "verdict('<step>')"])
        return result

    @staticmethod
    def _slot_binding(lab, needs, phases, slot: str, stage: str | None, component: str | None, result: dict):
        """A binding of the run's needs to the lab (locks ignored), with the slot on the chosen lab stage and
        component when they fit it. Puts in result the ``stages`` (of its stage slot) and ``components`` (of the
        stage, or ad hoc) the slot can be bound to."""
        from testsuit.sequencer.scheduler import (Availability, Binding, find_binding, unusable_components,
                                                  usable_assignment)
        inv = lab.inventory
        lc = needs.slots.get(slot)
        def bind(fixed: Binding | None = None):
            binding, reasons = find_binding(lab, needs, phases, Availability(), "editor", fixed=fixed, check_free=False)
            for logical, stage_id in (fixed.stage_slots.items() if fixed and binding else []):
                stage_ = inv.stages[stage_id]           # a stage given: find_binding keeps it as it is, slots left to give
                avail = Availability()
                for slot_id, comp in (usable_assignment(lab, stage_, needs.in_stage_slot(logical), avail) or {}).items():
                    binding.slots[slot_id] = comp.id
                binding.left_out += [c for c in unusable_components(lab, stage_, avail) if c not in binding.left_out]
            return binding, reasons
        if lc is None:
            return bind()
        if lc.stage_slot:
            stages = [s.id for s in inv.stages_of_type(needs.stage_slots[lc.stage_slot])
                      if bind(Binding(stage_slots={lc.stage_slot: s.id}))[0] is not None]
            result["stages"] = stages
            binding, reasons = bind(Binding(stage_slots={lc.stage_slot: stage}) if stage in stages else None)
            if binding is None or slot not in binding.slots:
                return binding, reasons
            chosen = inv.stages[binding.stage_slots[lc.stage_slot]]
            fitting = [c for c in chosen.components if c in inv.components and c not in binding.left_out
                       and lab.fits(inv.components[c], lc.role, lc.tags)]
            result["components"] = fitting
            if component in fitting and component != binding.slots[slot]:
                before = binding.slots[slot]
                for other, comp in binding.slots.items():        # the slot that had it gets the one it leaves
                    if comp == component:
                        binding.slots[other] = before
                binding.slots[slot] = component
            return binding, reasons
        comps = [c.id for c in inv.adhoc if lab.fits(c, lc.role, lc.tags)
                 and bind(Binding(slots={slot: c.id}))[0] is not None]
        result["components"] = comps
        return bind(Binding(slots={slot: component}) if component in comps else None)

    def eval_key(self, session: EditSession, testdef_rel: str, slot: str, expression: str, where: str = "generation",
                 setup_config: str | None = None, stage: str | None = None, component: str | None = None) -> dict:
        """The value of a key expression (``lab.components[slots.my_slot.component].host``, with or without its
        ``{{ }}``) on the keys of a slot the explorer shows: ``where`` is ``generation`` or ``run``. Returns
        ``value`` (plain data) or ``error``."""
        found = self.slot_keys(session, testdef_rel, slot, setup_config, stage, component)
        keys = found["run"] if where == "run" else found["generation"]
        if keys is None:
            return {"value": None, "error": found.get("why_not") or "no run-time keys for this slot"}
        return _eval_expression(expression, keys, where)

    def _run_problems(self, session: EditSession, testdef_rel: str, setup_config: str, by_hand: dict | None = None,
                      missing: list[str] | None = None) -> list[str]:
        """What would make the sequencer refuse the datapack when it is posted: the pipeline against the
        setup configuration (phases, stage slots and slots, step targets), and what the lab cannot provide."""
        from testsuit.sequencer.intake import check_against_lab
        mismatches = self._mismatches(session, testdef_rel, setup_config)
        if mismatches:
            return [f"pipeline: {m}" for m in mismatches]
        try:
            _, _, setup_raw, _, needs, pipeline, flats = self._testdef_parts(session, testdef_rel, setup_config)
        except SequencerError as e:
            return [f"pipeline: {e}"]
        except Exception as e:                    # an invalid pipeline (pydantic), a missing include...
            return [f"pipeline: {_describe(e)}"]
        lab, _ = self.lab_for(session)
        problems = [f"lab: {p}" for p in check_against_lab(SimpleNamespace(needs=needs), lab)]
        context = self._datapack_keys(session, testdef_rel, setup_config)
        if context is not None and by_hand:
            from testsuit.datatools.datapack.templating import expand_dotted_keys
            from testsuit.sequencer.util import deep_merge
            try:
                context = deep_merge(context, expand_dotted_keys({str(k): v for k, v in by_hand.items() if str(k).strip()}))
            except Exception:                       # a name that is not one: the generation says so
                pass
        flat = self._flatten(lab, needs, pipeline, flats, context, self.path(session, testdef_rel).parent)
        if missing is not None:
            missing += flat["missing_keys"]
        undeployed = [f"slot '{slot}': {count} input(s) generated for it, but no step deploys them (do: deploy, or a "
                      f"push, on it): they stay in the datapack, its lab component does not get them"
                      for slot, count in _input_counts(setup_raw, setup_config).items()
                      if count and slot in flat["used"] and slot not in flat["deployed"]]
        return (problems + [f"pipeline: {p}" for p in flat["problems"]] + [f"keys: {p}" for p in flat["key_problems"]]
                + [f"exploit: {p}" for p in flat["exploit_problems"]]
                + [f"inputs: {p}" for p in undeployed]
                + [f"criteria: {p}" for _, p in self._criteria_problems(session, testdef_rel)])

    def _datapack_keys(self, session: EditSession, testdef_rel: str, setup_config: str | None) -> dict | None:
        """The keys a datapack of this test definition would hold (its ``context.yml``), unresolved: their
        names are what the key checks of the pipeline need. None when the dictionaries cannot be read."""
        try:
            builder, _, _ = self._builder(session, testdef_rel, setup_config)
            return builder.keys().data
        except Exception:
            return None

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
        types = raw.get("slot_classes") or {}
        for name, t in types.items():
            role = str(t.get("role") or "")
            expected = []
            if role and lab is not None:
                try:
                    expected = lab.library.role_inputs(role)
                except SequencerError:
                    where = yamledit.locate(text, ["slot_classes", name, "role"]) or (1, 1)
                    problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "warning",
                                     "message": f"slot class '{name}': '{role}' is not a lab role"})
            for cfg_name, cfg in (t.get("configurations") or {}).items():
                targets = {str(i.get("target", "")).rstrip("/") for i in (cfg or {}).get("inputs") or [] if isinstance(i, dict)}
                for item in expected:                # what its role expects and this configuration does not give
                    if not item["optional"] and item["name"].rstrip("/") not in targets:
                        where = yamledit.locate(text, ["slot_classes", name, "configurations", cfg_name]) or (1, 1)
                        problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "warning",
                                         "message": f"slot class '{name}', configuration '{cfg_name}': no input '{item['name']}', "
                                                    f"which its role {role} expects"})
                for i, inp in enumerate((cfg or {}).get("inputs") or []):
                    source = str(inp.get("source", ""))
                    if source and "{{" not in source and not _find_sources(source, [path.parent]):
                        where = yamledit.locate(text, ["slot_classes", name, "configurations", cfg_name,
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
                type_role = (types.get(comp.get("slot_class")) or {}).get("role")
                if type_role and comp.get("role") and comp["role"] != type_role:
                    where = yamledit.locate(text, ["configurations", cfg_name, "slots", comp_id, "role"]) or (1, 1)
                    problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "warning",
                                     "message": f"slot '{comp_id}': role '{comp['role']}', but its class "
                                                f"'{comp['slot_class']}' is for role '{type_role}'"})
                if comp.get("slot_class") in types and comp.get("configuration") not in (types[comp["slot_class"]].get("configurations") or {}):
                    where = yamledit.locate(text, ["configurations", cfg_name, "slots", comp_id]) or (1, 1)
                    problems.append({"file": rel, "line": where[0], "column": where[1], "severity": "error",
                                     "message": f"slot '{comp_id}': slot class '{comp['slot_class']}' has no configuration "
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
        from testsuit.sequencer.model import Pipeline, SubPipeline, comment_problems
        raw = yaml.safe_load(text) or {}
        problems = []
        subs = {}
        for name, sub in (raw.get("subpipelines") or {}).items():
            try:
                subs[name] = SubPipeline.model_validate(sub)
            except ValidationError as e:
                problems += _pydantic_problems(e, rel, text, ["subpipelines", name])
        for problem in comment_problems(Pipeline(subpipelines=subs)):
            line, column = _find_line(text, problem)
            problems.append({"file": rel, "line": line, "column": column, "severity": "warning", "message": problem})
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

    def _flatten(self, lab: Lab, needs, pipeline, flats, context: dict | None = None,
                 definition_dir: Path | None = None) -> dict:
        """The pipeline bound to the lab. With ``context`` (the datapack keys): ``key_problems``, the keys the
        commands of its steps read that nothing defines at run time (the step would fail on them), and, with
        ``definition_dir`` (the test definition's folder), ``exploit_problems`` and ``warnings``: the keys exploit
        steps give (``with:``) and those their criteria read that nothing defines."""
        from testsuit.sequencer.cmdparams import PARAMS, default_params, exploit_keys, exploit_with_problems, \
            step_param_problems, without_params
        from testsuit.sequencer.model import comment_cards, phase_slots
        from testsuit.sequencer.scheduler import Availability, find_binding
        problems: list[str] = []
        key_problems: list[str] = []
        exploit_problems: list[str] = []
        warnings: list[str] = []
        missing_keys: list[str] = []                  # those that can be set by hand: not the keys of the run
        deployed: set[str] = set()                    # the slots a step deploys inputs to (do: deploy, push)
        used: set[str] = set()                        # the slots a step acts on
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
                    where = f"phase '{flat.phase.id}', step '{fs.id}'"
                    if step.action == "do":                # its parameters against the role of each slot
                        for target in targets:
                            lc = needs.slots.get(target)
                            if lc is not None and lc.role in lab.library.roles and step.do in lab.library.role_commands(lc.role):
                                for p in step_param_problems(where, step.do, step.with_, lab.library.command_params(lc.role)[step.do]):
                                    if p not in problems:
                                        problems.append(p)
                    if step.action == "exploit" and context is not None:
                        errors, warns = exploit_with_problems(where, step.with_, context)
                        exploit_problems += errors
                        warnings += warns
                        if not errors and definition_dir is not None:
                            base = _step_keys(lab, needs, binding, context, flat.phase.id, fs.params, None, {})
                            for name in _missing_keys(step.with_, base):
                                exploit_problems.append(f"{where}: its 'with' reads '{name}', which no dictionary nor "
                                                        "run-time key defines")
                            try:                       # the values do not matter here: which names are defined
                                keys = exploit_keys(where, {k: "<value>" for k in step.with_}, base)
                            except SequencerError as e:
                                exploit_problems.append(str(e))
                                keys = base
                            exploit_problems += _criteria_key_problems(where, step, definition_dir, keys)
                    expanded = []
                    for target in targets or [None]:
                        entry: dict[str, Any] = {"slot": target}
                        if target:
                            used.add(target)
                        if target and (step.action == "push" or (step.action == "do" and step.do == "deploy")):
                            deployed.add(target)
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
                                    if spec is not None:
                                        declared = resolved.command_params.get(step.do, {})
                                        defaults = default_params(spec, step.do)
                                        for p in step_param_problems(where + f" on slot '{target}' ({concrete.id})", step.do,
                                                                     step.with_, declared, defaults):
                                            if "needs a value" in p:
                                                problems.append(p)
                                        if declared:
                                            entry["params"] = {n: step.with_.get(n, defaults.get(n)) for n in declared}
                                    if isinstance(spec, dict) and spec.get("builtin") == "deploy":
                                        deployed.add(target)
                        if context is not None and (target is None or "concrete" in entry):
                            spec = entry.get("command") if step.action == "do" else _inline_spec(step)
                            if spec is not None:
                                bound = (lab.inventory.components[entry["concrete"]], needs.slots[target],
                                         lab.inventory.stages.get(binding.stage_slots.get(needs.slots[target].stage_slot, ""))) \
                                    if target is not None else None
                                keys = _step_keys(lab, needs, binding, context, flat.phase.id, fs.params, bound, spec)
                                checked = {"spec": without_comments(spec), "env": step.env, "timeout": step.timeout}
                                if step.action == "do":        # its parameters: rendered with the step's keys
                                    given = {**default_params(spec, step.do), **step.with_}
                                    checked = {"params": given, "env": step.env, "timeout": step.timeout}
                                    command_keys = {**keys, PARAMS: {n: f"<{n}>" for n in
                                                                     [*resolved.command_params.get(step.do, {}), *given]}}
                                    for name in _missing_keys(without_comments(without_params(spec)), command_keys):
                                        message = (f"{where}: command '{step.do}' of slot '{target}' ({entry['concrete']}) "
                                                   f"reads '{name}', which no dictionary nor run-time key defines")
                                        if message not in key_problems:
                                            key_problems.append(message)
                                what = (f"command '{step.do}' of slot '{target}' ({entry['concrete']})" if step.action == "do"
                                        else f"its '{step.action}'" + (f" on slot '{target}'" if target else ""))
                                for name in _missing_keys(checked, keys):
                                    settable = name.split(".")[0] not in RUN_KEY_NAMES
                                    if settable and name not in missing_keys:
                                        missing_keys.append(name)
                                    message = (f"phase '{flat.phase.id}', step '{fs.id}': {what} reads '{name}', which no "
                                               f"dictionary nor run-time key defines" + (
                                                   ": define it in a dictionary, set it by hand when generating, or "
                                                   "declare it in the user_keys of the test definition" if settable else ""))
                                    if message not in key_problems:
                                        key_problems.append(message)
                        expanded.append(entry)
                    rows.append({"id": fs.id, "part": part, "group": fs.group, "needs": fs.needs, "action": step.action,
                                 "do": step.do, "params": fs.params, "targets": expanded,
                                 "step": step.model_dump(by_alias=True, exclude_defaults=True)})
            phases.append({"id": flat.phase.id, "needs": flat.phase.needs, "stage_slots": flat.phase.stage_slots,
                           "slots": held, "groups": flat.groups, "steps": rows,
                           "comments": comment_cards(flat.phase, pipeline)})
        return {"binding": binding.as_dict() if binding is not None else None, "phases": phases, "problems": problems,
                "key_problems": key_problems, "exploit_problems": exploit_problems, "warnings": warnings, "missing_keys": missing_keys, "deployed": sorted(deployed), "used": sorted(used)}

    def flatten(self, session: EditSession, rel: str, setup_config: str | None = None) -> dict:
        """The pipeline as the sequencer runs it: sub-pipelines expanded, every command resolved against the lab."""
        _, _, _, setup_path, needs, pipeline, flats = self._testdef_parts(session, rel, setup_config)
        lab, _ = self.lab_for(session)
        result = self._flatten(lab, needs, pipeline, flats, self._datapack_keys(session, rel, setup_config),
                               self.path(session, rel).parent)
        result["key_problems"] += result["exploit_problems"]
        result["setup"] = self.rel(session, setup_path)
        result["logical"] = {"stage_slots": needs.stage_slots,
                             "slots": {c.id: {"slot_class": c.slot_class, "stage_slot": c.stage_slot, "role": c.role, "name": c.name}
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
                              "help": info["description"] or _command_help(cmd), "params": info.get("params", {}),
                              "implementations": implementations.get(cmd, {})}
                        for cmd, info in sorted(offered.items())}
            slots[lc.id] = {"name": lc.name, "slot_class": lc.slot_class, "stage_slot": lc.stage_slot, "role": lc.role,
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
                "shared_subpipelines": (workspace.get("shared_subpipelines") or {}),
                "step_types": sorted(registry.step_types), "ready_checks": sorted(registry.ready_checks)}

    def exploit_keys(self, session: EditSession, testdef_rel: str, criteria: list[str], given: dict | None = None,
                     setup_config: str | None = None) -> dict:
        """The keys the criteria of an exploit step read that neither the test (its dictionaries), the run nor the
        step's ``with:`` (``given``) define, for the exploit step form to propose them; ``problems`` and
        ``warnings``: those of its ``with:``."""
        from testsuit.sequencer.cmdparams import criteria_missing_keys, exploit_keys, exploit_with_problems
        context = self._datapack_keys(session, testdef_rel, setup_config) or {}
        given = dict(given or {})
        errors, warnings = exploit_with_problems("its 'with'", given, context)
        keys: dict = {**context, **{name: _Anything() for name in RUN_KEY_NAMES if name not in context}}
        keys["params"] = {}
        try:
            keys = exploit_keys("its 'with'", {k: "<value>" for k in given}, keys) if not errors else keys
        except SequencerError:
            pass
        folder = self.path(session, testdef_rel).parent
        missing = []
        for name in criteria:
            for file, key in criteria_missing_keys(folder / str(name), keys):
                missing.append({"key": key, "criteria": name, "file": file})
        return {"missing": missing, "problems": errors, "warnings": warnings}

    # ── keys, template preview, datapack generation ──────────────────────

    def _builder(self, session: EditSession, testdef_rel: str, setup_config: str | None = None):
        """A datapack builder with the test definition and setup keys loaded, datasets as placeholders
        (nothing is imported)."""
        from testsuit.datatools.datapack.datapack_tools import _Builder, load_yaml_mapping
        with self._quiet():
            builder = _Builder(self.path(session, testdef_rel), nocheck=True, setup_config=setup_config)
            testdef = builder.load_testdef()
            for name in testdef.user_keys:           # given at each generation: placeholders until then
                builder.dataset_keys.append(({name: f"<{name}: given at generation>"}, USER_KEY))
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
            builder, testdef, _ = self._builder(session, testdef_rel, setup_config)
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
                "user_keys": {name: spec.model_dump() for name, spec in testdef.user_keys.items()},
                "datapack": {"slot.id": "id of the slot being generated (also slot.name, slot.slot_class, slot.configuration)",
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
                cfg = ((raw.get("slot_classes") or {}).get(item.get("slot_class")) or {}).get("configurations") or {}
                inputs = (cfg.get(item.get("configuration")) or {}).get("inputs") or []
                if any(path in _find_sources(str(i.get("source", "")), [builder.testdef_dir, setup_dir]) for i in inputs):
                    slot = slot_id
                    break
        comp = slots.get(slot or "", {})
        extra = {"slot": {"id": slot or "", "name": comp.get("name", ""), "slot_class": comp.get("slot_class", ""),
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
        types = raw.get("slot_classes") or {}
        components = []
        for comp_id, comp in (cfg.get("slots") or {}).items():
            comp = comp if isinstance(comp, dict) else {}
            configuration = ((types.get(comp.get("slot_class")) or {}).get("configurations") or {}).get(comp.get("configuration")) or {}
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
            components.append({"id": comp_id, "name": comp.get("name", ""), "slot_class": comp.get("slot_class", ""),
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
            if comp["slot_class"] not in types:
                problems.append(f"slot '{comp['id']}': no slot class '{comp['slot_class']}'")
            elif comp["configuration"] not in ((types[comp["slot_class"]] or {}).get("configurations") or {}):
                problems.append(f"slot '{comp['id']}': no configuration '{comp['configuration']}' for class '{comp['slot_class']}'")
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
                "baseline": baseline, "requirements": self._requirements_of(session, testdef_rel), "problems": problems}

    def _requirements_of(self, session: EditSession, testdef_rel: str) -> dict | None:
        """The requirements a test definition covers, as generation would snapshot them: each with its title, path,
        status and link, and what verifies it (the run verdict, or checks of the criteria); None when it lists none."""
        from testsuit.reqs.snapshot import snapshot_of_testdef
        path = self.path(session, testdef_rel)
        try:
            snap = snapshot_of_testdef(path, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        except Exception as e:                     # a backend that fails: said, the overview goes on
            return {"items": [], "problems": [f"requirements not read: {e}"], "warnings": []}
        if snap is None:
            return None
        by: dict[str, list[str]] = {}
        for key in snap.get("testdef") or []:
            by.setdefault(key, []).append("run verdict")
        for c in snap.get("checks") or []:
            for key in c.get("reqs") or []:
                by.setdefault(key, []).append(f"check {c.get('check')} ({c.get('step')}: {c.get('scenario')})")
        reqs = snap.get("requirements") or {}
        items = []
        for key, verified_by in by.items():
            r = reqs.get(key) or {}
            items.append({"key": key, "title": r.get("title", ""), "path": r.get("path", ""), "status": r.get("status", ""),
                          "url": r.get("url", ""), "version": r.get("version", ""), "found": bool(r),
                          "by": list(dict.fromkeys(verified_by))})
        return {"items": items, "problems": snap.get("problems") or [], "warnings": snap.get("warnings") or []}

    _version_cache: dict[tuple[str, str], tuple[float, Any, Any]] = {}
    _tag_cache: dict[tuple[str, str], list[str] | None] = {}    # the versions that are tags (None: not known)

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
        return {"versions": versions, "tags": self._tag_cache.get((importer_name, source)), "error": error, "seconds": round(time.monotonic() - started, 2)}

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
            tags = importer.listTags() if hasattr(importer, "listTags") else None
            self._tag_cache[(importer_name, source)] = list(tags) if tags is not None else None
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
                tags = None if pending else self._tag_cache.get((str(src.get("importer", "")), source))
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
                                     "versions": versions, "tags": tags, "versions_error": error, "versions_pending": pending})
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
                 nocheck: bool = True, lenient: bool = False, manual_keys: dict | None = None,
                 dataset_versions: dict | None = None) -> dict:
        """Run the datapack tool on a test definition of the session, as ``datapack <testdef>`` would;
        manual_keys: keys set by hand (dotted name -> value, ``datapack --set``); dataset_versions: dataset
        versions for this generation only (dataset id -> version, ``datapack --dataset-version``)."""
        job_id = self.start_generate(session, testdef_rel, setup_config, nocheck, lenient, manual_keys, dataset_versions)["job"]
        self.jobs[job_id]["thread"].join()
        return self.jobs[job_id]["result"]

    def start_generate(self, session: EditSession, testdef_rel: str, setup_config: str | None = None,
                       nocheck: bool = True, lenient: bool = False, manual_keys: dict | None = None,
                       dataset_versions: dict | None = None, campaign: bool = False) -> dict:
        """The same as generate, in the background: its log can be read while it runs (generation_job).

        The datapack tool runs in a process of its own: a stopped generation ends for sure (the process and
        the commands it started are killed), whatever it was doing.
        """
        for name in manual_keys or {}:
            if not str(name).strip() or any(not part.strip() for part in str(name).split(".")) or "=" in str(name):
                raise SequencerError(f"'{name}' is not a key name (name, or name.sub for a nested key)")
        dataset_versions = {str(k).strip(): str(v).strip() for k, v in (dataset_versions or {}).items() if str(v).strip()}
        for name in dataset_versions:
            if not name or "=" in name:
                raise SequencerError(f"'{name}' is not a dataset id")
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
                job["result"] = self._generate(session, testdef_rel, setup_config, nocheck, lenient, job, manual_keys,
                                               dataset_versions, campaign)
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
                    logger.info(f"editor: {'campaign ' + job['result']['name'] if campaign else 'datapack ' + job['result']['zip']} generated")
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
                  lenient: bool, job: dict, manual_keys: dict | None = None,
                  dataset_versions: dict | None = None, campaign: bool = False) -> dict:
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
        for dataset, version in (dataset_versions or {}).items():
            command += ["--dataset-version", f"{dataset}={version}"]
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
        marker = "Generated campaign:" if campaign else "Generated Datapack:"
        zip_path = next((Path(line.split(marker, 1)[1].strip()) for line in reversed(output)
                         if marker in line), None)
        if returncode != 0 or zip_path is None or not zip_path.exists():
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
        if campaign:                                     # a folder: its manifest and datapacks
            from testsuit.datatools.datapack.campaign import read_manifest
            manifest = read_manifest(zip_path)
            for entry in manifest["datapacks"]:
                entry["baseline"] = read_baseline(zip_path / entry["zip"])
            return {"ok": True, "campaign": True, "name": zip_path.name, "log": text, "manifest": manifest}
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            files = [{"path": n.split("/", 1)[1], "size": z.getinfo(n).file_size} for n in z.namelist()
                     if "/" in n and not n.endswith("/")]
            root = z.namelist()[0].split("/")[0]
            manifest = yaml.safe_load(z.read(f"{root}/datapack.yml"))
        return {"ok": True, "name": zip_path.stem, "zip": zip_path.name, "log": text, "files": files,
                "baseline": read_baseline(zip_path), "datapack": manifest}

    def generated_campaign(self, session: EditSession, name: str) -> Path:
        path = Path(session.dir) / "generated" / Path(name).name
        if not (path / "campaign.yml").is_file():
            raise SequencerError(f"no generated campaign '{name}' in this session")
        return path

    # ── campaigns ────────────────────────────────────────────────────────

    def campaign_synthesis(self, session: EditSession, campaign_rel: str) -> dict:
        """A campaign at a glance (the Campaigns subtab of the Datapacks page): its test definitions, each with
        its setup configuration and priority; the datasets they import, the version each test definition writes and
        the one the campaign pins (every one must be pinned); its routing tags, against the lab; and its problems."""
        from pydantic import ValidationError

        from testsuit.datatools.datapack.campaign import Campaign
        path = self.path(session, campaign_rel)
        problems: list[str] = []
        warnings: list[str] = []
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as e:
            raise SequencerError(f"campaign '{campaign_rel}': {e}") from e
        if not isinstance(raw, dict):
            raise SequencerError(f"campaign '{campaign_rel}': not a YAML mapping")
        campaign = None
        try:
            campaign = Campaign.model_validate(raw)
        except ValidationError as e:
            problems += [f"{'.'.join(str(x) for x in err['loc']) or 'campaign'}: {err['msg']}" for err in e.errors()]
        entries = campaign.testdefs if campaign else []
        pinned = dict(campaign.datasets) if campaign else {}
        routing = campaign.routing.model_dump() if campaign else {}
        base = path.parent
        testdefs, datasets = [], {}
        user_keys: dict[str, dict] = {}                  # the keys its test definitions ask at each generation, by name
        lab = self.seq.lab
        for entry in entries:
            file = (base / entry.testdef).resolve()
            rel = self.rel(session, file) if file.is_relative_to(session.folder.resolve()) else entry.testdef
            item: dict[str, Any] = {"testdef": entry.testdef, "path": rel, "setup_config": entry.setup_config,
                                    "priority": entry.priority if entry.priority is not None else campaign.priority,
                                    "id": None, "name": None, "problems": []}
            testdefs.append(item)
            if not file.is_file():
                problems.append(f"test definition '{entry.testdef}' not found (paths are relative to the campaign file)")
                item["problems"].append("not found")
                continue
            try:
                found = self.synthesis(session, rel, entry.setup_config, versions=False, run_checks=False)
            except SequencerError as e:
                problems.append(f"{entry.testdef}: {e}")
                item["problems"].append(str(e))
                continue
            item.update({"id": found["testdef"]["id"], "name": found["testdef"]["name"],
                         "description": found["testdef"]["description"],
                         "configuration": found["testdef"]["setup_config"], "setup_file": found["testdef"]["setup_file"]})
            item["problems"] += found["problems"]
            for d in found["baseline"]:
                row = datasets.setdefault(d["id"], {"id": d["id"], "name": d.get("name"), "importer": d.get("importer"),
                                                    "source": d.get("source"), "pinned": pinned.get(d["id"]), "used_by": []})
                row["used_by"].append({"testdef": entry.testdef, "written": d.get("version") or d.get("written"),
                                       "defined_in": d.get("defined_in")})
            try:
                asked = self._builder_testdef(session, rel, entry.setup_config).user_keys
            except Exception:                            # its problems are reported by the synthesis above
                asked = {}
            for name, spec in asked.items():
                row = user_keys.setdefault(name, {"key": name, "asked_by": []})
                row["asked_by"].append({"testdef": entry.testdef, "path": rel, "description": spec.description,
                                        "choices": spec.choices})
            item["routing_problems"] = self._routing_problems(lab, found, routing)
            problems += [f"{entry.testdef}: {p}" for p in item["routing_problems"]]
        for row in datasets.values():
            if row["pinned"] is None:
                problems.append(f"dataset '{row['id']}' is imported by "
                                f"{', '.join(u['testdef'] for u in row['used_by'])}: the campaign must give its version "
                                "(datasets:)")
            written = {u["written"] for u in row["used_by"]}
            row["differs"] = len(written) > 1
        keys = self._campaign_keys(campaign, entries, user_keys, problems, warnings) if campaign else []
        for name, version in pinned.items():
            if name not in datasets:
                warnings.append(f"dataset '{name}' is pinned to {version} but no test definition of the campaign imports it")
                datasets[name] = {"id": name, "pinned": version, "used_by": [], "unused": True}
        return {"path": campaign_rel, "raw": raw,
                "campaign": {"id": raw.get("id"), "name": raw.get("name"), "description": raw.get("description") or "",
                             "priority": raw.get("priority"), "baseline": raw.get("baseline")},
                "testdefs": testdefs, "datasets": list(datasets.values()), "routing": routing, "keys": keys,
                "problems": problems, "warnings": warnings, "git": git_state(path.parent)}

    def _builder_testdef(self, session: EditSession, testdef_rel: str, setup_config: str | None):
        """The test definition as the datapack tool reads it (its user_keys)."""
        from testsuit.datatools.datapack.datapack_tools import _Builder
        with self._quiet():
            return _Builder(self.path(session, testdef_rel), nocheck=True, setup_config=setup_config).load_testdef()

    @staticmethod
    def _campaign_keys(campaign, entries, user_keys: dict[str, dict], problems: list[str], warnings: list[str]) -> list[dict]:
        """The keys of a campaign, one row per name (shared by its test definitions): those its test definitions ask
        at each generation (``asked_by``); what the campaign pins (``pinned``: its value for every test definition,
        ``pinned_for``: an entry's own); the test definitions that share one value (``shared_by``: those it is not
        pinned for, by entry), with the choices they have in common (None: free text); whether it is given at
        generation."""
        rows: dict[str, dict] = {}

        def row(name: str) -> dict:
            return rows.setdefault(name, {"key": name, "asked_by": [], "description": "", "choices": None,
                                          "pinned": campaign.keys.get(name), "is_pinned": name in campaign.keys,
                                          "pinned_for": [], "shared_by": []})
        for name, asked in user_keys.items():
            r = row(name)
            r["asked_by"] = asked["asked_by"]
            r["description"] = next((a["description"] for a in asked["asked_by"] if a.get("description")), "")
        for entry in entries:
            for name, value in entry.keys.items():
                row(name)["pinned_for"].append({"testdef": entry.testdef, "value": value})
        for name in campaign.keys:
            row(name)
        for r in rows.values():
            name = r["key"]
            if not r["asked_by"]:
                warnings.append(f"key '{name}' is pinned by the campaign but no test definition asks it "
                                "(user_keys): it is set like a key set by hand")
            own = {p["testdef"]: p["value"] for p in r["pinned_for"]}
            for asked in r["asked_by"]:               # an entry's own value: one of its own choices
                choices = [str(c) for c in asked.get("choices") or []]
                if asked["testdef"] in own and choices and str(own[asked["testdef"]]) not in choices:
                    problems.append(f"key '{name}' pinned for {asked['testdef']}: '{own[asked['testdef']]}' is not "
                                    f"one of its choices ({', '.join(choices)})")
            r["shared_by"] = [a for a in r["asked_by"] if a["testdef"] not in own]
            lists = [[str(c) for c in a["choices"]] for a in r["shared_by"] if a.get("choices")]
            if lists:
                first = next(a["choices"] for a in r["shared_by"] if a.get("choices"))
                r["choices"] = [c for c in first if all(str(c) in other for other in lists)]
                if not r["choices"]:
                    problems.append(f"key '{name}': its test definitions have no choice in common ("
                                    + "; ".join(f"{a['testdef']}: {', '.join(str(c) for c in a['choices'])}"
                                                for a in r["shared_by"] if a.get("choices"))
                                    + "): pin it for each one in the campaign file (keys: of its entry)")
                elif r["is_pinned"] and str(r["pinned"]) not in [str(c) for c in r["choices"]]:
                    problems.append(f"key '{name}': '{r['pinned']}' is not one of its choices "
                                    f"({', '.join(str(c) for c in r['choices'])})")
            r["given_at_generation"] = not r["is_pinned"] and bool(r["shared_by"])
        return list(rows.values())

    @staticmethod
    def _routing_problems(lab, found: dict, routing: dict) -> list[str]:
        """What the lab lacks for a test definition with the campaign's routing tags (checked again at submission)."""
        if lab is None or not routing or (not routing.get("stage_tags") and not routing.get("component_tags")):
            return []
        inv = lab.inventory
        problems = []
        stage_tags = routing.get("stage_tags") or []
        for stage_slot, stage_type in (found.get("stage_slots") or {}).items():
            extra = stage_tags if isinstance(stage_tags, list) else (stage_tags.get(stage_type) or [])
            if extra and not [s for s in inv.stages_of_type(stage_type) if set(extra) <= set(s.tags)]:
                problems.append(f"stage slot '{stage_slot}': no '{stage_type}' stage of the lab tagged {', '.join(extra)}")
        for slot in found.get("slots") or []:
            extra = (routing.get("component_tags") or {}).get(slot.get("role")) or []
            if extra and not any(lab.fits(c, slot["role"], extra) for c in inv.components.values()):
                problems.append(f"slot '{slot['id']}': no component with role '{slot['role']}' tagged {', '.join(extra)}")
        return problems

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

    def _git_env(self, credentials: dict) -> dict:
        """The environment of a git command run with the user's credentials: its prompts are answered from them
        only; no ssh-agent of tsqd's account and no credential helper (each command passes -c credential.helper=)."""
        env = {k: v for k, v in os.environ.items() if k not in ("SSH_AUTH_SOCK", "SSH_AGENT_PID")}
        return {**env, "GIT_TERMINAL_PROMPT": "0", **self._push_env(credentials)}

    @staticmethod
    def _user_git_config(url: str, cwd: Path | None = None, push: bool = False) -> list[str]:
        """The -c options of a git command that talks to url with the user's credentials: no credential helper, and
        no url.<base>.insteadOf / pushInsteadOf rewriting of that URL (a rule of tsqd's account or of the host may
        put a token of its own in it, which git would then use instead of asking for the user's credentials). The
        URL is mapped to itself, which wins over any shorter rule; the rest of git's configuration (proxy,
        certificates) is kept. A rule for the whole URL still rewriting it is refused, naming it."""
        options = ["-c", "credential.helper=", "-c", f"url.{url}.insteadOf={url}", "-c", f"url.{url}.pushInsteadOf={url}"]
        where = cwd if cwd is not None and cwd.is_dir() else Path.cwd()
        used = _git(where, *options, "ls-remote", "--get-url", url, check=False).strip()
        if push and cwd is not None:
            used = _git(where, *options, "remote", "get-url", "--push", "origin", check=False).strip() or used
        if used and used != url:
            raise SequencerError(f"a url.<base>.insteadOf or pushInsteadOf rule of git's configuration rewrites "
                                 f"'{url}' entirely, so git would not use your credentials: remove that rule "
                                 f"(git config --show-origin --get-regexp '^url\\.')")
        return options

    @staticmethod
    def _origin(root: Path, push: bool = False) -> str:
        """The URL of the remote origin as configured, before any insteadOf rewriting. Refused when it holds a password
        or a token: git would use it instead of the user's credentials."""
        url = (_git(root, "config", "--get", "remote.origin.pushurl", check=False).strip() if push else "") \
            or _git(root, "config", "--get", "remote.origin.url", check=False).strip()
        if not url:
            raise SequencerError("the repository has no remote 'origin'")
        if re.match(r"^[a-z][a-z0-9+.-]*://[^/@]*:[^/@]*@", url, re.I):
            raise SequencerError("the URL of the remote 'origin' holds a password or token, which git would use instead "
                                 "of your credentials: remove it (git remote set-url origin <URL without it>)")
        return url

    @staticmethod
    def _require_credentials(credentials: dict) -> None:
        """A push (and its tag) is made with the user's own credentials, never with tsqd's (README section 13.2)."""
        if not ((credentials.get("username") and credentials.get("password")) or credentials.get("passphrase")):
            raise SequencerError("your own git credentials are needed to push: a user and a password or access "
                                 "token, or the passphrase of the SSH key (used for this push only, never stored)")

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
        self._require_credentials(credentials)
        remote = self._origin(session.root, push=True)            # before committing: refused, nothing to undo
        remote_options = self._user_git_config(remote, session.root, push=True)
        ident = ["-c", f"user.name={author_name}", "-c", f"user.email={author_email}"]
        # what is left to commit; the changes may already be in commits of the working copy (made by hand), which
        # are then pushed as they are
        staged = subprocess.run(["git", "diff", "--cached", "--quiet", "--", session.sub or "."], cwd=session.root).returncode
        committed = bool(staged)
        if committed:
            _git(session.root, *ident, "commit", "--quiet", "-m", message, "--", session.sub or ".")
        commit = _git(session.root, "rev-parse", "HEAD").strip()
        if tag:
            _git(session.root, *ident, "tag", "-a", tag, "-m", message)
        refs = [f"HEAD:refs/heads/{session.branch}"] + ([f"refs/tags/{tag}"] if tag else [])
        result = subprocess.run(["git", *remote_options, "push",
                                 "--porcelain", "origin", *refs],
                                cwd=session.root, capture_output=True, text=True, timeout=300,
                                env=self._git_env(credentials))
        if result.returncode:
            if tag:
                _git(session.root, "tag", "-d", tag, check=False)
            if committed:
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
        result = subprocess.run(["git", *self._user_git_config(self._origin(session.root), session.root), "fetch", "--quiet",
                                 "--tags", "origin"], cwd=session.root, capture_output=True, text=True, timeout=300,
                                env=self._git_env(credentials))
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
