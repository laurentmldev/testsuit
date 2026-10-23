"""The lab: inventory and type library read from the lab folder, a git clone (README section 6.1)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from testsuit.sequencer.inventory import Inventory, load_inventory
from testsuit.sequencer.component_types import TypeLibrary
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
    types: TypeLibrary
    git: dict | None
    require_tag: bool = True
    loaded_at: str = field(default_factory=now)

    @property
    def warnings(self) -> list[str]:
        if self.git is None:
            return [f"lab folder '{self.folder}' is not a git work tree"]
        problems = []
        if not self.git["clean"]:
            problems.append(f"lab folder '{self.folder}' has local changes")
        if self.require_tag and not self.git["tag"]:
            problems.append(f"lab folder '{self.folder}' is not on a tag")
        return problems

    def describe(self) -> dict:
        return {"folder": str(self.folder), "inventory": str(self.inventory.file), "git": self.git,
                "warnings": self.warnings, "loaded_at": self.loaded_at,
                "stages": {s.id: {"type": s.type, "components": s.components} for s in self.inventory.stages.values()},
                "components": {c.id: {"type": c.type, "role": c.role, "host": c.host, "lock": c.lock,
                                      "stages": c.stages, "label": c.label}
                               for c in self.inventory.components.values()},
                "types": {t.name: {"extends": t.extends, "source": t.source, "commands": sorted(t.commands)}
                          for t in self.types.types.values()}}


def load_lab(folder: Path, inventory_file: Path, require_tag: bool = True) -> Lab:
    """Read and check the inventory and its type library (every component's type must exist)."""
    if not inventory_file.is_file():
        raise SequencerError(f"lab inventory not found: '{inventory_file}'")
    inventory = load_inventory(inventory_file)
    types = TypeLibrary.load(inventory.type_folders)
    for component in inventory.components.values():
        try:
            types.chain(component.type)
        except SequencerError as e:
            raise SequencerError(f"inventory '{inventory_file}': component '{component.id}': {e}") from e
    lab = Lab(folder=folder, inventory=inventory, types=types, git=git_state(folder), require_tag=require_tag)
    for warning in lab.warnings:
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
