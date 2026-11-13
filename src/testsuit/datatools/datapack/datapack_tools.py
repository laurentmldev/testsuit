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

"""Generation of a datapack: the input files of every slot of a test setup, packaged in a zip.

Steps, from a test definition file (see model for the file formats, templating for the keys):

1. the dictionaries of the test definition are loaded; the test definition is rendered with them;
2. the setup file it points to is loaded, with its own dictionaries;
3. the datasets of the setup data repositories are imported (or their version is checked when
   already there) and their keys added;
4. every key is resolved; the input files of each slot of the chosen setup configuration are
   rendered (or copied) into ``data/<slot>/input/``;
5. everything is written as a BagIt bag (RFC 8493) and zipped::

       <name>/
         bagit.txt, bag-info.txt
         manifest-sha256.txt         checksums of data/
         tagmanifest-sha256.txt      checksums of the other files
         datapack.yml                what the datapack was built from: ids, git state, datasets,
                                     slots and the keys used by each generated file
         context.yml                 every key, resolved: the datapack needs no dictionary to be used
         values.json                 where each '{{ expression }}' output is in the rendered files, and
                                     the keys it read (for viewers: hover a value to see where it comes from)
         baseline.yml                where the contents come from: sources (git), datasets, tools,
                                     and each generated file with its checksum (conform or not)
         definition/                 test definition, setup and dictionaries, as written
         data/<slot>/input/          generated files
         data/<slot>/output/         empty, for the test results

Keys always available (they override dictionary keys of the same name):

- ``testdef.{id, name, file, path, setup_file, setup_config}``
- ``setup.{id, name, file, path, configuration}``
- ``datapack.{name, user, host, timestamp, nocheck}``
- ``<dataset id>.{path, version}`` for every imported dataset
- ``slot.{id, name, type, configuration, input_dir}`` while rendering the files of a slot, and
  ``slots.<id>.{id, name, type, input_dir}`` for the slots it needs (and ``read_input('<slot>/<file>')``)

``datapack.output_dir`` (from a dictionary, or the ``output_dir`` argument) is where the zip is written.

Keys can also be **set by hand** (``datapack --set name=value``, the ``manual_keys`` argument): they come
over the dictionaries. Setting a key no dictionary defines keeps the datapack conform; overriding a key
that is defined makes it non-conform. Both are recorded in ``datapack.yml`` and ``baseline.yml``
(``keys_set_by_hand``). The built-in keys above cannot be set by hand.
"""
from __future__ import annotations

import contextlib
import getpass
import glob
import graphlib
import hashlib
import json
import os
import shutil
import socket
import stat
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import git
import yaml
from colorama import Fore, Style
from pydantic import ValidationError

from testsuit.datatools.datapack.baseline import check_baseline, tool_versions
from testsuit.datatools.datapack.generators import GeneratorError, run_generator
from testsuit.datatools.datapack.importers import create_data_importer
from testsuit.datatools.datapack.model import GONE, DataRepository, Setup, Testdef, former_names
from testsuit.datatools.datapack.processors import DATASET_KEYS_FILE, ProcessorError, run_processor
from testsuit.datatools.datapack.templating import (Keys, TemplateError, _leaves, expand_dotted_keys, keys_set_by_hand,
                                                    load_yaml_mapping, run_time_keys)
from testsuit.plugins import load_plugins

WORK_FOLDER = ".current_datapack"
SET_BY_HAND = "<set by hand>"
FORMAT_VERSION = 2


class DatapackError(Exception):
    """The datapack cannot be generated."""


def _info(text: str) -> None:
    print(text + Style.RESET_ALL)


def _warn(text: str) -> None:
    print(Fore.YELLOW + "WARNING: " + text + Style.RESET_ALL)


def _validate(model, data: Any, what: str):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise DatapackError(f"invalid {what}:\n{e}") from e


def _expand(path: str, base: Path) -> Path:
    """Path relative to base, '~' expanded."""
    p = Path(os.path.expanduser(str(path)))
    return (p if p.is_absolute() else base / p).resolve()


def git_state(folder: Path) -> dict | None:
    """Commit, tag and cleanliness of the git work tree holding folder (None if not in a work tree)."""
    try:
        repo = git.Repo(folder, search_parent_directories=True)
        commit = repo.head.commit.hexsha
    except (git.InvalidGitRepositoryError, git.NoSuchPathError, ValueError):
        return None
    try:
        tag = repo.git.describe("--tags", "--exact-match", "HEAD")
    except git.GitCommandError:
        tag = None
    rel = os.path.relpath(folder, repo.working_tree_dir)
    dirty = bool(repo.git.status("--porcelain", "--", rel))
    try:
        remote = repo.remotes.origin.url if "origin" in [r.name for r in repo.remotes] else None
    except Exception:
        remote = None
    return {"commit": commit, "tag": tag, "clean": not dirty, "repo": remote, "root": repo.working_tree_dir}


def normalize_dataset_path(version: str) -> str:
    """Folder name of a dataset version ('a/b@c' -> 'a.b_c')."""
    return version.replace("/", ".").replace("@", "_").replace(" ", "_").strip(".")


LAST_USED_SUFFIX = ".last_used"


def usage_marker(local_path: Path) -> Path:
    """File whose modification time is when a dataset folder was last used: next to the folder, not in it
    (a file in it would make a git dataset look modified)."""
    return local_path.parent / f".{local_path.name}{LAST_USED_SUFFIX}"


def mark_dataset_used(local_path: Path) -> None:
    marker = usage_marker(local_path)
    marker.touch()
    os.utime(marker)


def purge_unused_datasets(repo_path: Path, days: int, declared: set[str] = frozenset(),
                          keep: set[Path] = frozenset(), now: float | None = None) -> list[Path]:
    """Delete the dataset folders of a data repository that no datapack used for `days` days.

    Only folders ``<repo_path>/<dataset id>/<version>`` are considered. A folder without a usage marker
    (imported before markers existed) gets one now when its dataset id is in `declared`, which starts its
    clock; otherwise it is left alone, not being known as a dataset. Folders in `keep` are never deleted.
    A folder that cannot be deleted is reported and left.

    :param days: 0 or less: nothing is purged
    :return: the folders deleted
    """
    if days <= 0 or not repo_path.is_dir():
        return []
    limit = (time.time() if now is None else now) - days * 86400
    keep = {k.resolve() for k in keep}
    removed = []
    for id_dir in sorted(repo_path.iterdir()):
        if not id_dir.is_dir() or id_dir.name.startswith("."):
            continue
        for folder in sorted(id_dir.iterdir()):
            if not folder.is_dir() or folder.name.startswith(".") or folder.resolve() in keep:
                continue
            marker = usage_marker(folder)
            if not marker.is_file():
                if id_dir.name in declared:
                    mark_dataset_used(folder)
                continue
            if marker.stat().st_mtime >= limit:
                continue
            try:
                shutil.rmtree(folder, onerror=_on_rm_error)
            except OSError as e:
                _warn(f"could not purge unused dataset '{folder}': {e}")
                continue
            marker.unlink(missing_ok=True)
            removed.append(folder)
        with contextlib.suppress(OSError):              # its last version gone
            if not any(id_dir.iterdir()):
                id_dir.rmdir()
    return removed


def import_dataset(repo_path: Path, datasource, dataset, scripts_folder: Path, nocheck: bool = False) -> dict | None:
    """Import a dataset of a datasource into repo_path (or check its version if already there).

    :return: what the datapack records about it (None if its version is empty: skipped)
    """
    if not dataset.version:
        _info(Fore.YELLOW + "     [DATASET] " + Style.RESET_ALL + dataset.id + " -> skipped (no version)")
        return None

    local_path = repo_path / dataset.id / normalize_dataset_path(dataset.version)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        importer = create_data_importer(datasource.importer, str(local_path), datasource.source, dataset.version)
    except KeyError as e:
        raise DatapackError(f"dataset '{dataset.id}': {e.args[0]}") from e

    label = Fore.LIGHTBLUE_EX + "     [DATASET] " + Style.RESET_ALL + dataset.id + Fore.LIGHTBLACK_EX + " -> " + Fore.CYAN + dataset.version + Style.RESET_ALL
    confirmed = True
    if local_path.is_dir():
        _info(label + " [" + Fore.GREEN + "found" + Style.RESET_ALL + "]")
        if not importer.checkVersion(dataset.version):
            confirmed = False
            message = f"dataset '{dataset.id}': could not confirm it matches version '{dataset.version}'"
            if not nocheck:
                raise DatapackError(message)
            _warn(message + ", ignored (nocheck)")
            for change in importer.getChanges() or []:
                print("  - " + change)
    else:
        _info(label + " [" + Fore.LIGHTYELLOW_EX + "importing" + Style.RESET_ALL + "]")
        if not importer.retrieve():
            raise DatapackError(f"dataset '{dataset.id}': import of version '{dataset.version}' from '{datasource.source}' failed (the importer's messages are above)")
        for spec in dataset.postprocess:
            _info(Fore.LIGHTBLACK_EX + "        [POSTPROCESS] " + spec)
            try:
                run_processor(spec, local_path, scripts_folder)
            except ProcessorError as e:
                raise DatapackError(str(e)) from e

    mark_dataset_used(local_path)
    return {"id": dataset.id, "datasource": datasource.id, "importer": datasource.importer,
            "source": datasource.source, "version": dataset.version, "path": local_path.as_posix(),
            "confirmed": confirmed}


def _copy(source: Path, destination: Path) -> None:
    """Copy a file or a folder, saying every few seconds how far a long copy has gone."""
    if not source.is_dir():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return
    count, total, last = 0, 0, time.monotonic()

    def copy(src: str, dst: str) -> str:
        nonlocal count, total, last
        result = shutil.copy2(src, dst)
        count += 1
        total += os.path.getsize(dst)
        if time.monotonic() - last > 3:
            last = time.monotonic()
            _info(Fore.LIGHTBLACK_EX + f"        ... {count} files, {total / 1e6:.1f} MB copied")
        return result
    shutil.copytree(source, destination, copy_function=copy)


def _input_problem(source: str, target: str, input_dir: Path) -> str | None:
    """What makes an input unusable before looking for its files: an empty source or target (a key with
    an empty value, typically), or a target out of the component's input folder."""
    if not source.strip():
        return "empty source (a key with an empty value?)"
    if not target.strip():
        return f"input '{source}': empty target (a key with an empty value?)"
    if not (input_dir / target).resolve().is_relative_to(input_dir.resolve()):
        return f"input '{source}': target '{target}' is out of the component's input folder"
    return None


def _find_sources(source: str, folders: list[Path]) -> list[Path]:
    """Files or folders matching source (absolute, or relative to one of folders, first match wins)."""
    candidates = [Path(os.path.expanduser(source))]
    if not candidates[0].is_absolute():
        candidates = [folder / source for folder in folders]
    for candidate in candidates:
        if glob.has_magic(str(candidate)):
            matches = sorted(Path(p) for p in glob.glob(str(candidate), recursive=True))
            if matches:
                return matches
        elif candidate.exists():
            return [candidate]
    return []


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe(data: Any) -> Any:
    """Data that yaml.safe_dump accepts."""
    if isinstance(data, dict):
        return {str(k): _safe(v) for k, v in data.items()}
    if isinstance(data, (list, tuple, set)):
        return [_safe(v) for v in data]
    if data is None or isinstance(data, (str, int, float, bool)):
        return data
    return str(data)


def _dump_yaml(path: Path, data: Any, header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + yaml.safe_dump(_safe(data), sort_keys=False, allow_unicode=True), encoding="utf-8")


def _on_rm_error(function, path, excinfo):
    # datasets made read-only by 'finalize', or files of a previous run
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    function(path)


def write_bag(bag: Path, name: str, software: str) -> None:
    """Write the BagIt declaration and manifests of the bag folder (payload in bag/data)."""
    payload = sorted(p for p in (bag / "data").rglob("*") if p.is_file())
    (bag / "bagit.txt").write_text("BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in payload)
    (bag / "bag-info.txt").write_text(
        f"Bagging-Date: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"Bag-Software-Agent: {software}\n"
        f"External-Identifier: {name}\n"
        f"Payload-Oxum: {size}.{len(payload)}\n", encoding="utf-8")
    (bag / "manifest-sha256.txt").write_text(
        "".join(f"{_sha256(p)}  {p.relative_to(bag).as_posix()}\n" for p in payload), encoding="utf-8")
    tags = sorted(p for p in bag.rglob("*") if p.is_file() and p.relative_to(bag).parts[0] != "data"
                  and p.name != "tagmanifest-sha256.txt")
    (bag / "tagmanifest-sha256.txt").write_text(
        "".join(f"{_sha256(p)}  {p.relative_to(bag).as_posix()}\n" for p in tags), encoding="utf-8")


def zip_folder(folder: Path, zip_path: Path, root_name: str) -> Path:
    """Zip folder as '<root_name>/...' (empty folders included)."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(root_name + "/", "")
        for path in sorted(folder.rglob("*")):
            arcname = f"{root_name}/{path.relative_to(folder).as_posix()}"
            if path.is_dir():
                archive.writestr(arcname + "/", "")
            else:
                archive.write(path, arcname)
    return zip_path


def _sha256_member(archive: zipfile.ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with archive.open(name) as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_datapack(zip_path: str | os.PathLike) -> list[str]:
    """Check the checksums of a datapack zip against its manifests.

    :return: the problems found (empty if the datapack is intact)
    """
    problems = []
    with zipfile.ZipFile(zip_path) as archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        roots = {n.split("/", 1)[0] for n in archive.namelist()}
        if len(roots) != 1:
            return [f"expected a single root folder, found {sorted(roots)}"]
        root = roots.pop()
        files = {n[len(root) + 1:]: n for n in names}
        listed = set()
        # files kept out of the zip (results with large files in external mode): next to the zip
        fetched = {}
        if "fetch.txt" in files:
            for line in archive.read(files["fetch.txt"]).decode("utf-8").splitlines():
                parts = line.split(maxsplit=2)
                if len(parts) == 3:
                    fetched[parts[2]] = Path(zip_path).parent / parts[0]
        for manifest in ("manifest-sha256.txt", "tagmanifest-sha256.txt"):
            if manifest not in files:
                problems.append(f"missing {manifest}")
                continue
            for line in archive.read(files[manifest]).decode("utf-8").splitlines():
                checksum, _, path = line.partition("  ")
                listed.add(path)
                if path not in files and path in fetched:
                    if not fetched[path].is_file():
                        problems.append(f"missing external file: {fetched[path]}")
                    elif _sha256(fetched[path]) != checksum:
                        problems.append(f"checksum mismatch: {path} ({fetched[path]})")
                elif path not in files:
                    problems.append(f"missing file: {path}")
                elif _sha256_member(archive, files[path]) != checksum:
                    problems.append(f"checksum mismatch: {path}")
        for path in files:
            if path.startswith("data/") and path not in listed:
                problems.append(f"file not in manifest: {path}")
    return problems


def _depth(comp_id: str, components: dict) -> int:
    """Length of the longest needs chain below a slot (0: needs nothing)."""
    needs = components[comp_id].needs
    return 1 + max(_depth(n, components) for n in needs) if needs else 0


class _Builder:

    def __init__(self, testdef_file: Path, nocheck: bool, setup_config: str | None = None,
                 expected_baseline: dict | None = None, lenient: bool = False,
                 manual_keys: dict | None = None) -> None:
        self.lenient = lenient
        # keys set by hand: dotted name -> value, as given; and what each does to the keys (set in build)
        self.manual_values: dict[str, Any] = {str(k): v for k, v in (manual_keys or {}).items()}
        try:
            self.manual_keys = expand_dotted_keys(self.manual_values)
        except TemplateError as e:
            raise DatapackError(f"keys set by hand: {e}") from e
        self.set_by_hand: list[dict] = []
        self.skipped: list[str] = []                    # lenient: the inputs left out, and why
        self.values: dict[str, dict] = {}               # where each value of a rendered file comes from (values.json)
        nocheck = nocheck or lenient
        self.testdef_file = testdef_file
        self.expected_baseline = expected_baseline
        self.setup_config = setup_config
        self.testdef_dir = testdef_file.parent
        self.nocheck = nocheck
        self.now = datetime.now()
        self.work = self.testdef_dir / WORK_FOLDER
        self.manifest: dict[str, Any] = {}
        self.dictionaries: list[tuple[str, Path]] = []   # (layer, file)
        self.datasets: list[dict] = []
        self.dataset_keys: list[tuple[dict, str]] = []   # (keys, origin)
        self.builtin: dict[str, Any] = {
            "datapack": {"user": getpass.getuser(), "host": socket.gethostname(),
                         "timestamp": self.now.strftime("%Y-%m-%d %H:%M:%S"), "nocheck": nocheck},
            "testdef": {"file": testdef_file.as_posix(), "path": self.testdef_dir.as_posix()},
        }

    def keys(self, with_setup: bool = True, with_datasets: bool = True, with_manual: bool = True) -> Keys:
        """Keys, lowest precedence first: setup dictionaries, datasets, test definition dictionaries, keys set
        by hand, built-in keys."""
        keys = Keys()
        order = (["setup"] if with_setup else []) + (["datasets"] if with_datasets else []) + ["testdef"]
        for layer in order:
            if layer == "datasets":
                for mapping, origin in self.dataset_keys:
                    keys.add(mapping, origin)
            else:
                for file_layer, path in self.dictionaries:
                    if file_layer == layer:
                        keys.add_file(path)
        if with_manual and self.manual_keys:
            keys.add(self.manual_keys, origin=SET_BY_HAND)
        keys.add(self.builtin, origin="<datapack>")
        return keys

    def check_manual_keys(self) -> list[dict]:
        """What each key set by hand does: it sets a key no dictionary defines, or overrides a defined one.
        A built-in key (``testdef.id``, ``datapack.name``...) cannot be set by hand: it would not be used."""
        if not self.manual_values:
            return []
        builtin = {".".join(str(p) for p in path) for path, _ in _leaves(self.builtin) if path}
        for name in self.manual_values:
            clash = next((b for b in sorted(builtin) if b == name or b.startswith(name + ".") or name.startswith(b + ".")), None)
            if clash:
                raise DatapackError(f"key '{name}' cannot be set by hand: '{clash}' is a built-in key of the datapack tool")
        base = self.keys(with_manual=False)
        entries = keys_set_by_hand(base.data, self.manual_values, base.origins)
        for entry in entries:
            if entry["overrides"]:
                _warn(f"key '{entry['key']}' set by hand to {entry['value']!r}: it overrides {entry['was']!r}"
                      + (f" (from {entry['origin']})" if entry["origin"] else "") + ", the datapack is non-conform")
            else:
                _info(Fore.LIGHTBLACK_EX + f"key '{entry['key']}' set by hand to {entry['value']!r} (not defined by a dictionary)")
        return entries

    def _load_dictionaries(self, layer: str, files: list[str], base: Path) -> None:
        for name in files:
            path = _expand(name, base)
            if not path.is_file():
                raise DatapackError(f"{layer} dictionary not reachable: '{path}'")
            self.dictionaries.append((layer, path))

    def _check_git(self, label: str, folder: Path) -> dict | None:
        state = git_state(folder)
        _info(Fore.LIGHTBLACK_EX + f"{label} folder: " + Style.RESET_ALL + folder.as_posix())
        if state is None or not state["clean"] or not state["tag"]:
            why = "not in a git work tree" if state is None else ("has local changes" if not state["clean"] else "is not a git tag")
            if not self.nocheck:
                raise DatapackError(f"{label} folder {why}: '{folder}'. Try again with option '--nocheck' to force.")
            _warn(f"{label} folder {why}")
        return state

    # ── steps ────────────────────────────────────────────────────────────

    def load_testdef(self) -> Testdef:
        raw = load_yaml_mapping(self.testdef_file)
        keys = Keys()
        keys.add(self.builtin, origin="<datapack>")
        self._load_dictionaries("testdef", keys.render_data(raw.get("dictionaries", []), where="dictionaries"), self.testdef_dir)

        keys = self.keys(with_setup=False, with_datasets=False)
        pipeline = raw.pop("pipeline", None)
        testdef = _validate(Testdef, {**keys.render_data(raw), "pipeline": pipeline}, f"test definition '{self.testdef_file}'")
        if self.setup_config:
            testdef.setup_config = self.setup_config
        self.setup_file = _expand(testdef.setup_file, self.testdef_dir)
        self.builtin["testdef"].update({"id": testdef.id, "name": testdef.name,
                                        "setup_file": self.setup_file.as_posix(), "setup_config": testdef.setup_config})
        return testdef

    def load_setup(self, testdef: Testdef) -> Setup:
        if not self.setup_file.is_file():
            raise DatapackError(f"setup file not reachable: '{self.setup_file}'")
        setup_dir = self.setup_file.parent
        raw = load_yaml_mapping(self.setup_file)
        self.setup_git = self._check_git("Setup", setup_dir)

        keys = self.keys(with_datasets=False)
        self._load_dictionaries("setup", keys.render_data(raw.get("dictionaries", []), where="dictionaries"), setup_dir)
        keys = self.keys(with_datasets=False)
        self.builtin["setup"] = {"id": keys.render_string(raw.get("id", ""), where="id"),
                                 "name": keys.render_string(raw.get("name", ""), where="name"),
                                 "file": self.setup_file.as_posix(), "path": setup_dir.as_posix(),
                                 "configuration": testdef.setup_config}

        _info(Fore.GREEN + "\n *** [1] Importing datasets ***")
        keys = self.keys(with_datasets=False)
        repositories = [_validate(DataRepository, keys.render_data(r, where=f"datarepositories[{i}]"),
                                  f"data repository in '{self.setup_file}'")
                        for i, r in enumerate(raw.get("datarepositories", []))]
        for repository in repositories:
            repo_path = _expand(repository.path, setup_dir)
            _info("\n" + Fore.BLUE + "[REPO] " + Style.RESET_ALL + repository.id + Fore.LIGHTBLACK_EX + " -> " + Fore.CYAN + repo_path.as_posix())
            repo_path.mkdir(parents=True, exist_ok=True)
            for datasource in repository.datasources:
                _info(Fore.BLUE + " [DATASOURCE] " + Style.RESET_ALL + datasource.id + Fore.LIGHTBLACK_EX + f" ({datasource.importer}) -> " + Fore.CYAN + datasource.source)
                for dataset in datasource.datasets:
                    record = import_dataset(repo_path, datasource, dataset, setup_dir, self.nocheck)
                    if record is None:
                        continue
                    self.datasets.append(record)
                    keys_file = Path(record["path"]) / DATASET_KEYS_FILE
                    if keys_file.is_file():
                        self.dataset_keys.append((load_yaml_mapping(keys_file), keys_file.as_posix()))
                        self.dictionaries.append(("datasets", keys_file))
                    self.dataset_keys.append(({dataset.id: {"path": record["path"], "version": dataset.version}}, "<datapack>"))
            used = {Path(r["path"]) for r in self.datasets}
            declared = {ds.id for source in repository.datasources for ds in source.datasets}
            for folder in purge_unused_datasets(repo_path, repository.purge_after_days, declared, used):
                _info(Fore.LIGHTBLACK_EX + "     [PURGED] " + folder.relative_to(repo_path).as_posix()
                      + f" (unused for {repository.purge_after_days} days)")

        keys = self.keys()
        former = former_names(raw)                                                    # before rendering: a clear error
        if former:
            raise DatapackError(f"setup '{self.setup_file}': former names: " + "; ".join(former))
        for type_id, type_def in (raw.get("slot_types") or {}).items():
            for key in ("extends", "commands"):
                if isinstance(type_def, dict) and key in type_def:
                    raise DatapackError(f"setup '{self.setup_file}': slot type '{type_id}': '{key}' {GONE}")
        for config_id, config in (raw.get("configurations") or {}).items():
            for slot_id, slot in ((config or {}).get("slots") or {}).items():
                if isinstance(slot, dict) and "commands" in slot:
                    raise DatapackError(f"setup '{self.setup_file}': configuration '{config_id}', slot "
                                        f"'{slot_id}': 'commands' {GONE}")
        rendered = {**keys.render_data({k: v for k, v in raw.items() if k != "slot_types"}),
                    "slot_types": {
                        type_id: keys.render_data(type_def, where=f"slot_types.{type_id}") if isinstance(type_def, dict)
                        else type_def
                        for type_id, type_def in (raw.get("slot_types") or {}).items()}}
        setup = _validate(Setup, rendered, f"setup '{self.setup_file}'")
        if testdef.setup_config not in setup.configurations:
            raise DatapackError(f"no configuration '{testdef.setup_config}' in setup '{self.setup_file}' "
                                f"(available: {', '.join(setup.configurations)})")
        return setup

    def generate_slots(self, setup: Setup, testdef: Testdef, keys: Keys) -> dict:
        _info(Fore.GREEN + f"\n *** [2] Generating files for setup '{setup.id}' in configuration '{testdef.setup_config}' ***")
        setup_dir = self.setup_file.parent
        components = {}
        configured = setup.configurations[testdef.setup_config].slots
        for comp_id, component in configured.items():
            unknown = [n for n in component.needs if n not in configured]
            if unknown:
                raise DatapackError(f"slot '{comp_id}' needs unknown slot(s) {', '.join(unknown)}")
        try:
            order = list(graphlib.TopologicalSorter({c: comp.needs for c, comp in configured.items()}).static_order())
        except graphlib.CycleError as e:
            raise DatapackError(f"slots needing each other: {' -> '.join(e.args[1])}") from e
        order.sort(key=lambda c: (_depth(c, configured), list(configured).index(c)))
        for comp_id in order:
            component = configured[comp_id]
            if component.type not in setup.slot_types:
                raise DatapackError(f"slot '{comp_id}': no slot type '{component.type}' in setup '{self.setup_file}'")
            comp_type = setup.slot_types[component.type]
            if component.configuration not in comp_type.configurations:
                raise DatapackError(f"slot '{comp_id}': no configuration '{component.configuration}' for type "
                                    f"'{component.type}' (available: {', '.join(comp_type.configurations)})")
            _info("\n" + Fore.LIGHTBLUE_EX + f"[{comp_id}] " + Fore.LIGHTBLACK_EX + f"{component.type} / {component.configuration}")

            input_dir = self.work / "data" / comp_id / "input"
            input_dir.mkdir(parents=True)
            (self.work / "data" / comp_id / "output").mkdir()
            needed = {n: {"id": n, "name": configured[n].name, "type": configured[n].type,
                          "input_dir": (self.work / "data" / n / "input").as_posix()} for n in component.needs}

            def read_input(path: str, comp_id=comp_id, component=component, data_dir=self.work / "data") -> str:
                other = path.split("/", 1)[0]
                if other not in component.needs:
                    raise DatapackError(f"slot '{comp_id}': read_input('{path}'): '{other}' is not in its needs")
                file = (data_dir / other / "input" / path.split("/", 1)[-1]).resolve()
                if not file.is_relative_to((data_dir / other / "input").resolve()) or not file.is_file():
                    raise DatapackError(f"slot '{comp_id}': read_input('{path}'): no such generated input")
                return file.read_text(encoding="utf-8")
            comp_keys = {"id": comp_id, "name": component.name, "type": component.type,
                         "configuration": component.configuration, "input_dir": input_dir.as_posix()}
            extra = {**run_time_keys(keys.root), "slot": comp_keys, "slots": needed, "read_input": read_input}
            inputs = []
            for number, input_file in enumerate(comp_type.configurations[component.configuration].inputs, 1):
                problem = _input_problem(input_file.source, input_file.target, input_dir)
                sources = [] if problem else _find_sources(input_file.source, [self.testdef_dir, setup_dir])
                if not problem and not sources and not input_file.optional:
                    problem = (f"input '{input_file.source}' not found (absolute, or relative to "
                               f"'{self.testdef_dir}' or '{setup_dir}')")
                if not problem:
                    problem = next((f"input '{input_file.source}': '{s}' holds the datapack being generated"
                                    for s in sources if self.work.resolve().is_relative_to(s.resolve())), None)
                if problem:
                    problem = f"slot '{comp_id}', input {number}: {problem}"
                    if not self.lenient:
                        raise DatapackError(problem)
                    _warn(problem + ": skipped")
                    self.skipped.append(problem)
                    continue
                target = input_dir / input_file.target
                if not sources:
                    _info(Fore.YELLOW + "    [OPTIONAL] " + Fore.LIGHTBLACK_EX + f"{input_file.source} not found, skipped")
                    continue
                used: dict[str, dict[str, str]] = {}
                values: dict[str, dict] = {}
                for source in sources:
                    # a glob pattern always gives a folder, even when it matches one file
                    destination = target / source.name if glob.has_magic(input_file.source) else target
                    # said before it is done: a long copy, or one that fails, shows where the tool is
                    _info(Fore.MAGENTA + f"    [{input_file.method.upper()}] " + Fore.LIGHTBLACK_EX + source.as_posix()
                          + Style.RESET_ALL + " --> " + Fore.WHITE + destination.relative_to(self.work / "data").as_posix())
                    if input_file.method == "generate":
                        try:
                            with keys.tracking() as read:
                                tracked = run_generator(input_file.generator, source, destination, keys.root,
                                                        input_file.options, {**comp_keys, "slots": needed},
                                                        setup_dir, keys.resolve_all)
                        except GeneratorError as e:
                            raise DatapackError(f"slot '{comp_id}': {e}") from e
                        if tracked:
                            used[destination.relative_to(input_dir).as_posix()] = read
                    elif input_file.method == "copy":
                        try:
                            _copy(source, destination)
                        except OSError as e:
                            raise DatapackError(f"slot '{comp_id}': copy of '{source}' failed: {e}") from e
                    else:
                        try:
                            file_values: dict[str, dict] = {}
                            rendered = keys.render_tree(source, destination, [self.testdef_dir, setup_dir], extra, file_values)
                        except TemplateError as e:
                            raise DatapackError(f"slot '{comp_id}': {e}") from e
                        prefix = destination.relative_to(input_dir)
                        used.update({(prefix / name if source.is_dir() else prefix.parent / name).as_posix(): k
                                     for name, k in rendered.items()})
                        values.update({(prefix / name if source.is_dir() else prefix.parent / name).as_posix(): v
                                       for name, v in file_values.items()})
                if not input_file.postprocess:      # a postprocessed file no longer has its values where they were
                    self.values.update({f"data/{comp_id}/input/{name}": v for name, v in values.items()})
                for spec in input_file.postprocess:
                    _info(Fore.LIGHTBLACK_EX + "        [POSTPROCESS] " + spec)
                    try:
                        run_processor(spec, target, setup_dir)
                    except ProcessorError as e:
                        raise DatapackError(f"slot '{comp_id}': {e}") from e
                inputs.append({"source": input_file.source, "target": input_file.target, "method": input_file.method,
                               **({"generator": input_file.generator} if input_file.generator else {}),
                               "files": [s.as_posix() for s in sources],
                               "keys": {name: dict(k) for name, k in sorted(used.items())}})
            components[comp_id] = {"name": component.name, "type": component.type,
                                   "configuration": component.configuration, "inputs": inputs,
                                   **({"needs": component.needs} if component.needs else {})}
        return {c: components[c] for c in configured}

    def write_definition(self) -> None:
        definition = self.work / "definition"
        definition.mkdir()
        shutil.copy2(self.testdef_file, definition / "testdef.yml")
        shutil.copy2(self.setup_file, definition / "setup.yml")
        # files the pipeline refers to: shared sub-pipelines and the expected baseline (sequencer)
        pipeline = (load_yaml_mapping(self.testdef_file).get("pipeline") or {})
        extra = list(pipeline.get("include") or []) + ([pipeline["baseline"]] if pipeline.get("baseline") else [])
        for name in extra:
            source = _expand(name, self.testdef_dir)
            if not source.is_file():
                raise DatapackError(f"file '{name}' given in the pipeline of '{self.testdef_file}' not found")
            target = definition / name if not Path(name).is_absolute() and ".." not in Path(name).parts \
                else definition / "include" / Path(name).name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for layer, path in self.dictionaries:
            target = definition / "dictionaries" / layer / path.name
            if layer == "datasets":
                target = definition / "dictionaries" / layer / f"{path.parent.parent.name}.yml"
            n = 2
            while target.exists():
                target = target.with_name(f"{target.stem}_{n}{target.suffix}")
                n += 1
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            target.chmod(stat.S_IREAD | stat.S_IWRITE | stat.S_IRGRP | stat.S_IROTH)

    def _source(self, role: str, path: Path, state: dict | None) -> dict:
        entry = {"role": role, "path": path.as_posix()}
        if state is not None:
            root = state.get("root")
            if root:
                entry["path"] = Path(os.path.relpath(path, root)).as_posix()
            entry.update({"repo": state.get("repo"), "commit": state.get("commit"), "tag": state.get("tag"),
                          "clean": state.get("clean")})
        else:
            entry.update({"repo": None, "commit": None, "tag": None, "clean": False})
        return entry

    def _relative(self, file: str) -> str:
        for state in (self.setup_git, getattr(self, "testdef_git", None)):
            root = (state or {}).get("root")
            if root and os.path.abspath(file).startswith(os.path.abspath(root) + os.sep):
                return Path(os.path.relpath(file, root)).as_posix()
        return Path(file).as_posix()

    def baseline(self, name: str, components: dict, testdef_git: dict | None) -> dict:
        """The baseline: sources, tools and generated files (sequencer README, section 5.3)."""
        sources = [self._source("testdef", self.testdef_file, testdef_git),
                   self._source("setup", self.setup_file, self.setup_git)]
        for layer, path in self.dictionaries:
            if layer != "datasets":
                sources.append(self._source("dictionary", path, git_state(path.parent)))
        for ds in self.datasets:
            sources.append({"role": "dataset", "id": ds["id"], "importer": ds["importer"], "source": ds["source"],
                            "version": ds["version"], "confirmed": ds.get("confirmed", True)})
        files = {}
        for comp_id, comp in components.items():
            input_dir = self.work / "data" / comp_id / "input"
            targets = sorted(comp["inputs"], key=lambda i: -len(i["target"]))
            for path in sorted(p for p in input_dir.rglob("*") if p.is_file()):
                rel = path.relative_to(input_dir).as_posix()
                origin = next((i for i in targets if rel == i["target"].rstrip("/") or rel.startswith(i["target"].rstrip("/") + "/")), None)
                entry = {"sha256": _sha256(path)}
                if origin is not None:
                    entry = {"from": [self._relative(f) for f in origin["files"]], "method": origin["method"], **entry}
                files[f"data/{comp_id}/input/{rel}"] = entry
        conform = (not self.nocheck
                   and all(s.get("clean") and s.get("tag") for s in sources if s["role"] != "dataset")
                   and all(s.get("confirmed", True) for s in sources if s["role"] == "dataset")
                   and not any(k["overrides"] for k in self.set_by_hand))
        return {"datapack": {"name": name, "format": FORMAT_VERSION, "conform": conform},
                "sources": sources,
                **({"keys_set_by_hand": self._set_by_hand_record()} if self.set_by_hand else {}),
                "tools": tool_versions(), "files": files}

    def _set_by_hand_record(self) -> list[dict]:
        return [{**k, **({"origin": self._relative(k["origin"])} if k.get("origin", "").startswith("/") else {})}
                for k in self.set_by_hand]

    def build(self, output_dir: str | os.PathLike | None) -> Path:
        if self.work.exists():
            _info(Fore.LIGHTBLACK_EX + f"[cleaning existing '{self.work}' folder]")
            shutil.rmtree(self.work, onerror=_on_rm_error)
        self.work.mkdir(parents=True)

        testdef = self.load_testdef()
        testdef_git = self._check_git("Test definition", self.testdef_dir)
        setup = self.load_setup(testdef)

        name = f"{testdef.id}_{setup.id}_{getpass.getuser()}_{self.now.strftime('%Y%m%d_%H%M%S')}" + ("_DRAFT" if self.nocheck else "")
        self.builtin["datapack"]["name"] = name
        self.set_by_hand = self.check_manual_keys()
        keys = self.keys()
        try:
            context = keys.resolve_all()
        except TemplateError as e:
            raise DatapackError(str(e)) from e

        components = self.generate_slots(setup, testdef, keys)

        _info(Fore.GREEN + "\n *** [3] Packaging ***\n")
        if output_dir is None:
            output_dir = context.get("datapack", {}).get("output_dir")
            if not output_dir:
                raise DatapackError("no output folder: set key 'datapack.output_dir' in a dictionary, or give one explicitly")
        output_dir = _expand(str(output_dir), self.testdef_dir)

        from testsuit import __version__
        self.write_definition()
        _dump_yaml(self.work / "context.yml", context, "# every key of the datapack, resolved\n")
        _dump_yaml(self.work / "datapack.yml", {
            "format": FORMAT_VERSION,
            "name": name,
            "created": self.builtin["datapack"]["timestamp"],
            "user": self.builtin["datapack"]["user"],
            "host": self.builtin["datapack"]["host"],
            "nocheck": self.nocheck,
            "software": f"testsuit {__version__}",
            "testdef": {"id": testdef.id, "name": testdef.name, "file": self.testdef_file.as_posix(), "git": testdef_git},
            "setup": {"id": setup.id, "name": setup.name, "configuration": testdef.setup_config,
                      "file": self.setup_file.as_posix(), "git": self.setup_git},
            "datasets": self.datasets,
            "slots": components,
            **({"keys_set_by_hand": self._set_by_hand_record()} if self.set_by_hand else {}),
            **({"skipped": self.skipped} if self.skipped else {}),
        })
        if self.values:
            (self.work / "values.json").write_text(json.dumps({"format": 1, "files": self.values}, ensure_ascii=False,
                                                              separators=(",", ":")), encoding="utf-8")
        baseline = self.baseline(name, components, testdef_git)
        _dump_yaml(self.work / "baseline.yml", baseline, "# where the contents of this datapack come from (sequencer README, section 5.3)\n")
        problems = check_baseline(baseline, self.expected_baseline, parts=("datapack", "sources", "tools"))
        for problem in problems:
            _warn(f"baseline: {problem}")
        write_bag(self.work, name, f"testsuit {__version__} datapack")
        zip_path = zip_folder(self.work, output_dir / f"{name}.zip", name)

        _info(Fore.LIGHTBLACK_EX + "\nGenerated Datapack: " + Style.RESET_ALL + zip_path.as_posix() + "\n")
        if self.skipped:
            _warn(f"{len(self.skipped)} input(s) skipped (lenient), this datapack is incomplete.")
        if self.nocheck:
            _warn("option 'nocheck' was activated, this datapack shall not be used for a production run.")
        overridden = [k["key"] for k in self.set_by_hand if k["overrides"]]
        if overridden:
            _warn(f"{len(overridden)} defined key(s) overridden by hand ({', '.join(overridden)}): this datapack is non-conform.")
        return zip_path


def datapack(testdef_file: str | os.PathLike, nocheck: bool = False, output_dir: str | os.PathLike | None = None,
             setup_config: str | None = None, expected_baseline: dict | None = None, lenient: bool = False,
             manual_keys: dict | None = None) -> Path:
    """Generate the datapack of a test definition file.

    The datapack contents are left in '.current_datapack' next to the test definition file.

    :param nocheck: accept test definition and setup folders that are not clean git tags, and datasets
        whose version cannot be confirmed (the datapack is then named '..._DRAFT')
    :param output_dir: where to write the zip (default: key 'datapack.output_dir')
    :param setup_config: setup configuration to use instead of the test definition's 'setup_config'
    :param expected_baseline: an expected baseline to check the datapack against (mismatches are warnings)
    :param lenient: skip, with a warning, the inputs whose source is missing or empty, or whose target is
        empty (listed under 'skipped' in datapack.yml); implies nocheck
    :param manual_keys: keys set by hand, dotted name -> value, over the dictionaries: setting a key no
        dictionary defines keeps the datapack conform, overriding a defined one makes it non-conform
    :return: path of the zip
    :raises DatapackError: if the datapack cannot be generated
    """
    # importers and processors declared by external libraries
    load_plugins()
    try:
        return _Builder(Path(testdef_file).resolve(), nocheck, setup_config, expected_baseline, lenient,
                        manual_keys).build(output_dir)
    except TemplateError as e:
        raise DatapackError(str(e)) from e


def read_baseline(zip_path: str | os.PathLike) -> dict | None:
    """The baseline.yml of a datapack zip (None for a datapack without one)."""
    with zipfile.ZipFile(zip_path) as archive:
        names = [n for n in archive.namelist() if n.count("/") == 1 and n.endswith("/baseline.yml")]
        if not names:
            return None
        return yaml.safe_load(archive.read(names[0]))
