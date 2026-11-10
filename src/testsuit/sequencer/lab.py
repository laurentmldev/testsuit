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
    _view_links: list | None = field(default=None, repr=False)          # labview.lab_links, once per load

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
                             "roles": {}}
            for m in members.values():                  # several components of a role: the first one
                if m["role"]:
                    keys["stage"]["roles"].setdefault(m["role"], m)
        else:
            keys["stage"] = {"id": "", "type": "", "vars": {}, "components": {}, "roles": {}}
        return keys

    def roles_of(self, component: Component) -> list[str]:
        """The roles a component can take a slot of: its own and the roles it extends, its own first."""
        if not component.role:
            return []
        try:
            return [r.name for r in reversed(self.library.role_chain(component.role))]
        except SequencerError:
            return [component.role]

    def fits(self, component: Component, role: str, tags: list[str] = ()) -> bool:
        """True when the component can be bound to a slot of that role and tags: its role is that role or extends
        it, and it has every tag (its id is one)."""
        return role in self.roles_of(component) and set(tags) <= set(component.tags)

    def assign(self, slots: list, components: list[Component]) -> dict[str, Component] | None:
        """Bind each slot to a distinct component of a fitting role (None when no assignment exists).

        Slots with the fewest fitting components go first, so the only component with a specific role goes
        to the slot needing it; ties follow the slots' order. Each slot tries the components of its very role
        first, then those of a role extending it, each in their order. A choice that leaves a later slot without
        a component is undone (backtracking)."""
        options = {lc.id: sorted((c for c in components if self.fits(c, lc.role, lc.tags)),
                                 key=lambda c, role=lc.role: self.roles_of(c).index(role)) for lc in slots}
        order = sorted((lc.id for lc in slots), key=lambda i: len(options[i]))
        result: dict[str, Component] = {}

        def place(k: int) -> bool:
            if k == len(order):
                return True
            for comp in options[order[k]]:
                if all(comp.id != c.id for c in result.values()):
                    result[order[k]] = comp
                    if place(k + 1):
                        return True
                    del result[order[k]]
            return False
        return {lc.id: result[lc.id] for lc in slots} if place(0) else None

    def stage_assignment(self, stage: Stage, slots: list) -> dict[str, Component] | None:
        """The stage's components for the slots of a stage slot (see assign), in the stage's order."""
        return self.assign(slots, [self.inventory.components[c] for c in stage.components])

    def stage_shortfall(self, stage: Stage, slots: list) -> str:
        """Why the stage's components cannot take these slots."""
        comps = [self.inventory.components[c] for c in stage.components]
        for lc in slots:
            if not any(self.fits(c, lc.role) for c in comps):
                return f"{stage.id} has no '{lc.role}'"
            if not any(self.fits(c, lc.role, lc.tags) for c in comps):
                return f"{stage.id} has no '{lc.role}' tagged {', '.join(lc.tags)}"
        roles = sorted({lc.role for lc in slots})
        return f"{stage.id} has too few components for the slots of role(s) {', '.join(roles)}"

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
        if status.get("builtin"):
            raise SequencerError(f"{where}: 'via' needs a 'run', 'http' or plugin probe")
        return self.find_via(component, stage, name, where)

    def find_via(self, component: Component, stage: Stage | None, name: str, where: str) -> Component:
        """The component a 'via' names: the component of that role in the stage, or another host."""
        for cid in stage.components if stage else []:
            other = self.inventory.components[cid]
            if cid != component.id and other.role == name:
                return other
        other = self.inventory.components.get(name)
        if other is not None and other.id != component.id:
            return other
        raise SequencerError(f"{where}, which is neither the role of another component of "
                             + (f"its stage '{stage.id}'" if stage else "its stage (it has none)") + " nor another host")

    def command_vias(self, component: Component, stage: Stage | None = None,
                     resolved: Resolved | None = None) -> dict[str, Component]:
        """The components a component's commands run through (a command's ``via``), by command name: for a
        component the sequencer cannot reach itself, in a sub-network behind the stage server."""
        resolved = resolved or self.resolve(component, stage)
        vias = {}
        for name, spec in resolved.commands.items():
            if not isinstance(spec, dict) or not spec.get("via"):
                continue
            where = f"component '{component.id}': its command '{name}' goes via '{spec['via']}'"
            if "run" not in spec and "http" not in spec:
                raise SequencerError(f"{where}: 'via' needs a 'run' or 'http' command")
            vias[name] = self.find_via(component, stage, str(spec["via"]), where)
        return vias

    def describe(self) -> dict:
        return {"folder": str(self.folder), "inventory": str(self.inventory.file), "git": self.git,
                "description": self.inventory.description,
                "warnings": self.warnings, "loaded_at": self.loaded_at,
                "stages": {s.id: {"type": s.type, "components": s.components, "description": s.description}
                           for s in self.inventory.stages.values()},
                "components": {c.id: {"role": c.role, "extends": c.extends, "host": c.host, "lock": c.lock,
                                      "stages": c.stages, "label": c.label, "description": c.description}
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
                lab.command_vias(component, stage)
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
