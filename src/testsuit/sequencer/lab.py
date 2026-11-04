"""The lab: inventory and library (roles and components) read from the lab folder, a git clone (README section 6.1)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from testsuit.sequencer.inventory import Component, Inventory, Stage, load_inventory
from testsuit.sequencer.library import ComponentDef, Library, Resolved
from testsuit.sequencer.util import SequencerError, now

log = logging.getLogger("testsuit.sequencer")


def git_state(folder: Path) -> dict | None:
    """Commit, tag, branch, remote and cleanliness of the git work tree holding folder (None if not in one)."""
    try:
        import git
        repo = git.Repo(folder, search_parent_directories=True)
    except Exception:
        return None
    try:
        commit = repo.head.commit.hexsha
    except ValueError:                       # no commit yet
        return {"commit": None, "tag": None, "clean": False, "branch": None, "repo": None, "path": None}
    tags = [t.name for t in repo.tags if t.commit.hexsha == commit]
    try:
        branch = repo.active_branch.name
    except TypeError:                        # detached head
        branch = None
    try:
        remote = repo.remotes.origin.url if "origin" in [r.name for r in repo.remotes] else None
    except Exception:
        remote = None
    try:
        path = folder.resolve().relative_to(Path(repo.working_tree_dir).resolve()).as_posix()
    except ValueError:
        path = None
    return {"commit": commit, "tag": tags[0] if tags else None, "clean": not repo.is_dirty(untracked_files=True),
            "branch": branch, "repo": remote, "path": path}


@dataclass
class Lab:
    folder: Path
    inventory: Inventory
    library: Library
    git: dict | None
    require_tag: bool = True
    loaded_at: str = field(default_factory=now)
    extra_commands: dict[str, list[str]] = field(default_factory=dict)   # host -> commands its role does not list

    @property
    def warnings(self) -> list[str]:
        problems = []
        if self.git is None:
            problems.append(f"lab folder '{self.folder}' is not a git work tree")
        else:
            if not self.git["clean"]:
                problems.append(f"lab folder '{self.folder}' has local changes")
            if self.require_tag and not self.git["tag"]:
                problems.append(f"lab folder '{self.folder}' is not on a tag")
        return problems

    @property
    def hosts(self) -> dict[str, ComponentDef]:
        """The inventory's hosts as components (a host can extend another one)."""
        if self._hosts is None:
            self._hosts = self.library.host_defs(self.inventory)
        return self._hosts

    _hosts: dict | None = field(default=None, repr=False)

    def resolve(self, component: Component, stage: Stage | None = None) -> Resolved:
        return self.library.resolve(component, stage, self.hosts)

    def lab_keys(self, component: Component | None = None, stage: Stage | None = None) -> dict:
        """The keys giving the other components to commands, probes and self-tests: ``lab.components.<host>``
        (every host of the inventory) and ``stage`` (the component's stage: ``stage.components.<host>``,
        ``stage.roles.<role>``, ``stage.vars``), each component with its id, host, role, label and variables
        as seen in that stage (``{{ stage.roles.stage_server.host }}``, ``{{ stage.components.srv_1.vars.ip }}``)."""
        def info(c: Component, where: Stage | None) -> dict:
            return {"id": c.id, "host": c.host, "role": c.role, "label": c.label, "vars": c.vars_in(where)}
        inv = self.inventory
        keys: dict = {"lab": {"components": {c.id: info(c, inv.stages[c.stages[0]] if c.stages else None)
                                             for c in inv.components.values()},
                              "stages": {s.id: {"id": s.id, "type": s.type, "components": list(s.components)}
                                         for s in inv.stages.values()}}}
        if stage is not None:
            members = {cid: info(inv.components[cid], stage) for cid in stage.components}
            keys["stage"] = {"id": stage.id, "type": stage.type, "vars": dict(stage.vars), "components": members,
                             "roles": {m["role"]: m for m in members.values() if m["role"]}}
        else:
            keys["stage"] = {"id": "", "type": "", "vars": {}, "components": {}, "roles": {}}
        return keys

    def via(self, component: Component, stage: Stage | None = None, status: dict | None = None) -> Component | None:
        """The component a status probe (and its self-test) runs on when the component cannot be reached itself,
        for example in a sub-network behind the stage server: ``status.via`` names a role of its stage, or another
        host of the inventory. None: the probe runs on the component itself."""
        if status is None:
            status = self.resolve(component, stage).status
        name = (status or {}).get("via")
        if not name:
            return None
        name = str(name)
        where = f"component '{component.id}': its status probe goes via '{name}'"
        if status.get("builtin") or "http" in (status.get("probe") or {}):
            raise SequencerError(f"{where}: 'via' needs a 'run' or plugin probe (on the component it goes through, "
                                 "'curl' does an HTTP check)")
        for cid in stage.components if stage else []:
            other = self.inventory.components[cid]
            if cid != component.id and other.role == name:
                return other
        other = self.inventory.components.get(name)
        if other is not None and other.id != component.id:
            return other
        raise SequencerError(f"{where}, which is neither the role of another component of "
                             + (f"its stage '{stage.id}'" if stage else "its stage (it has none)") + " nor another host")

    def describe(self) -> dict:
        return {"folder": str(self.folder), "inventory": str(self.inventory.file), "git": self.git,
                "warnings": self.warnings, "loaded_at": self.loaded_at,
                "stages": {s.id: {"type": s.type, "components": s.components} for s in self.inventory.stages.values()},
                "components": {c.id: {"role": c.role, "extends": c.extends, "host": c.host, "lock": c.lock,
                                      "stages": c.stages, "label": c.label}
                               for c in self.inventory.components.values()},
                "roles": {r.name: {"extends": r.extends, "source": r.source, "commands": sorted(r.commands)}
                          for r in self.library.roles.values()}}


def load_lab(folder: Path, inventory_file: Path, require_tag: bool = True, strict: bool = True) -> Lab:
    """Read and check the inventory and its library: every host has a role, and implements its commands.

    :param strict: False to accept unknown roles and components, for parents too (the Editor, while one is renamed)
    """
    if not inventory_file.is_file():
        raise SequencerError(f"lab inventory not found: '{inventory_file}'")
    inventory = load_inventory(inventory_file)
    library = Library.load(inventory.library_folders, strict)
    lab = Lab(folder=folder, inventory=inventory, library=library, git=git_state(folder), require_tag=require_tag)
    for component in inventory.components.values():           # roles first: the stage checks need them
        try:
            component._role = library.host_role(component, lab.hosts)
        except SequencerError as e:
            if strict:
                raise SequencerError(f"inventory '{inventory_file}': component '{component.id}': {e}") from e
    for stage in inventory.stages.values() if strict else []:
        roles = [inventory.components[c].role for c in stage.components if inventory.components[c].role]
        duplicates = sorted({r for r in roles if roles.count(r) > 1})
        if duplicates:
            raise SequencerError(f"inventory '{inventory_file}': stage '{stage.id}' has several components with "
                                 f"role(s) {duplicates}")
    for component in inventory.components.values():
        for stage in [inventory.stages[s] for s in component.stages] or [None]:
            try:
                _, missing, extra = library.check_host(component, stage, lab.hosts)
            except SequencerError as e:
                if strict:
                    raise SequencerError(f"inventory '{inventory_file}': component '{component.id}': {e}") from e
                continue
            if missing and strict:
                raise SequencerError(f"inventory '{inventory_file}': component '{component.id}' does not implement "
                                     f"{', '.join(missing)}, listed by its role '{component.role}'"
                                     + (f" (in stage {stage.id})" if stage else ""))
            if extra:
                lab.extra_commands[component.id] = extra
            try:
                lab.via(component, stage)
            except SequencerError as e:
                if strict:
                    raise SequencerError(f"inventory '{inventory_file}': {e}") from e
    for warning in lab.warnings if strict else []:
        log.warning(warning)
    return lab


def pull_lab(folder: Path, revision: str | None = None) -> str:
    """Fetch the lab repository and check out revision (default: the latest commit of the current branch).

    :return: the commit checked out before, to roll back if the new revision is invalid
    """
    import git
    try:
        repo = git.Repo(folder, search_parent_directories=True)
    except Exception as e:
        raise SequencerError(f"lab folder '{folder}' is not a git work tree: cannot pull") from e
    if repo.is_dirty(untracked_files=False):
        raise SequencerError(f"lab folder '{folder}' has local changes: cannot pull")
    previous = repo.head.commit.hexsha
    try:
        for remote in repo.remotes:
            remote.fetch(tags=True)
        if revision:
            repo.git.checkout(revision)
        else:
            branch = repo.active_branch
            tracking = branch.tracking_branch()
            if tracking is not None:
                repo.git.merge("--ff-only", tracking.name)
    except git.GitCommandError as e:
        raise SequencerError(f"cannot update lab folder '{folder}': {e}") from e
    return previous


def checkout(folder: Path, revision: str) -> None:
    import git
    git.Repo(folder, search_parent_directories=True).git.checkout(revision)
