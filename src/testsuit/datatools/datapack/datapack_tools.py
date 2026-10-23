"""Generation of a datapack: the input files of every component of a test setup, packaged in a zip.

Steps, from a test definition file (see model for the file formats, templating for the keys):

1. the dictionaries of the test definition are loaded; the test definition is rendered with them;
2. the setup file it points to is loaded, with its own dictionaries;
3. the datasets of the setup data repositories are imported (or their version is checked when
   already there) and their keys added;
4. every key is resolved; the input files of each component of the chosen setup configuration are
   rendered (or copied) into ``data/<component>/input/``;
5. everything is written as a BagIt bag (RFC 8493) and zipped::

       <name>/
         bagit.txt, bag-info.txt
         manifest-sha256.txt         checksums of data/
         tagmanifest-sha256.txt      checksums of the other files
         datapack.yml                what the datapack was built from: ids, git state, datasets,
                                     components and the keys used by each generated file
         context.yml                 every key, resolved: the datapack needs no dictionary to be used
         baseline.yml                where the contents come from: sources (git), datasets, tools,
                                     and each generated file with its checksum (conform or not)
         definition/                 test definition, setup and dictionaries, as written
         data/<component>/input/     generated files
         data/<component>/output/    empty, for the test results

Keys always available (they override dictionary keys of the same name):

- ``testdef.{id, name, file, path, setup_file, setup_config}``
- ``setup.{id, name, file, path, configuration}``
- ``datapack.{name, user, host, timestamp, nocheck}``
- ``<dataset id>.{path, version}`` for every imported dataset
- ``component.{id, name, type, configuration}`` while rendering the files of a component

``datapack.output_dir`` (from a dictionary, or the ``output_dir`` argument) is where the zip is written.
"""
from __future__ import annotations

import getpass
import glob
import graphlib
import hashlib
import os
import shutil
import socket
import stat
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
from testsuit.datatools.datapack.model import DataRepository, Setup, Testdef
from testsuit.datatools.datapack.processors import DATASET_KEYS_FILE, ProcessorError, run_processor
from testsuit.datatools.datapack.templating import Keys, TemplateError, load_yaml_mapping
from testsuit.plugins import load_plugins

WORK_FOLDER = ".current_datapack"
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
            raise DatapackError(f"dataset '{dataset.id}': import failed")
        for spec in dataset.postprocess:
            _info(Fore.LIGHTBLACK_EX + "        [POSTPROCESS] " + spec)
            try:
                run_processor(spec, local_path, scripts_folder)
            except ProcessorError as e:
                raise DatapackError(str(e)) from e

    return {"id": dataset.id, "datasource": datasource.id, "importer": datasource.importer,
            "source": datasource.source, "version": dataset.version, "path": local_path.as_posix(),
            "confirmed": confirmed}


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
    """Length of the longest needs chain below a component (0: needs nothing)."""
    needs = components[comp_id].needs
    return 1 + max(_depth(n, components) for n in needs) if needs else 0


class _Builder:

    def __init__(self, testdef_file: Path, nocheck: bool, setup_config: str | None = None,
                 expected_baseline: dict | None = None) -> None:
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

    def keys(self, with_setup: bool = True, with_datasets: bool = True) -> Keys:
        """Keys, lowest precedence first: setup dictionaries, datasets, test definition dictionaries, built-in keys."""
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
        keys.add(self.builtin, origin="<datapack>")
        return keys

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

        keys = self.keys()
        # sequencer commands are kept as written: rendered at run time, with the run's keys
        commands = {}
        for config_id, config in (raw.get("configurations") or {}).items():
            for comp_id, comp in ((config or {}).get("components") or {}).items():
                if isinstance(comp, dict) and "commands" in comp:
                    commands[(config_id, comp_id)] = comp.pop("commands")
        rendered = {**keys.render_data({k: v for k, v in raw.items() if k != "component_types"}),
                    "component_types": {
                        type_id: {**keys.render_data({k: v for k, v in type_def.items() if k != "commands"}, where=f"component_types.{type_id}"),
                                  "commands": type_def.get("commands", {})}
                        for type_id, type_def in (raw.get("component_types") or {}).items()}}
        for (config_id, comp_id), value in commands.items():
            rendered["configurations"][config_id]["components"][comp_id]["commands"] = value
        setup = _validate(Setup, rendered, f"setup '{self.setup_file}'")
        if testdef.setup_config not in setup.configurations:
            raise DatapackError(f"no configuration '{testdef.setup_config}' in setup '{self.setup_file}' "
                                f"(available: {', '.join(setup.configurations)})")
        return setup

    def generate_components(self, setup: Setup, testdef: Testdef, keys: Keys) -> dict:
        _info(Fore.GREEN + f"\n *** [2] Generating files for setup '{setup.id}' in configuration '{testdef.setup_config}' ***")
        setup_dir = self.setup_file.parent
        components = {}
        configured = setup.configurations[testdef.setup_config].components
        for comp_id, component in configured.items():
            unknown = [n for n in component.needs if n not in configured]
            if unknown:
                raise DatapackError(f"component '{comp_id}' needs unknown component(s) {', '.join(unknown)}")
        try:
            order = list(graphlib.TopologicalSorter({c: comp.needs for c, comp in configured.items()}).static_order())
        except graphlib.CycleError as e:
            raise DatapackError(f"components needing each other: {' -> '.join(e.args[1])}") from e
        order.sort(key=lambda c: (_depth(c, configured), list(configured).index(c)))
        for comp_id in order:
            component = configured[comp_id]
            if component.type not in setup.component_types:
                raise DatapackError(f"component '{comp_id}': no component type '{component.type}' in setup '{self.setup_file}'")
            comp_type = setup.component_types[component.type]
            if component.configuration not in comp_type.configurations:
                raise DatapackError(f"component '{comp_id}': no configuration '{component.configuration}' for type "
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
                    raise DatapackError(f"component '{comp_id}': read_input('{path}'): '{other}' is not in its needs")
                file = (data_dir / other / "input" / path.split("/", 1)[-1]).resolve()
                if not file.is_relative_to((data_dir / other / "input").resolve()) or not file.is_file():
                    raise DatapackError(f"component '{comp_id}': read_input('{path}'): no such generated input")
                return file.read_text(encoding="utf-8")
            comp_keys = {"id": comp_id, "name": component.name, "type": component.type,
                         "configuration": component.configuration, "input_dir": input_dir.as_posix()}
            extra = {"component": comp_keys, "components": needed, "read_input": read_input}
            inputs = []
            for input_file in comp_type.configurations[component.configuration].inputs:
                sources = _find_sources(input_file.source, [self.testdef_dir, setup_dir])
                target = input_dir / input_file.target
                if not sources:
                    if input_file.optional:
                        _info(Fore.YELLOW + "    [OPTIONAL] " + Fore.LIGHTBLACK_EX + f"{input_file.source} not found, skipped")
                        continue
                    raise DatapackError(f"component '{comp_id}': input '{input_file.source}' not found (absolute, or "
                                        f"relative to '{self.testdef_dir}' or '{setup_dir}')")
                used: dict[str, dict[str, str]] = {}
                for source in sources:
                    # a glob pattern always gives a folder, even when it matches one file
                    destination = target / source.name if glob.has_magic(input_file.source) else target
                    if input_file.method == "generate":
                        try:
                            with keys.tracking() as read:
                                tracked = run_generator(input_file.generator, source, destination, keys.root,
                                                        input_file.options, {**comp_keys, "components": needed},
                                                        setup_dir, keys.resolve_all)
                        except GeneratorError as e:
                            raise DatapackError(f"component '{comp_id}': {e}") from e
                        if tracked:
                            used[destination.relative_to(input_dir).as_posix()] = read
                    elif input_file.method == "copy":
                        if source.is_dir():
                            shutil.copytree(source, destination)
                        else:
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source, destination)
                    else:
                        try:
                            rendered = keys.render_tree(source, destination, [self.testdef_dir, setup_dir], extra)
                        except TemplateError as e:
                            raise DatapackError(f"component '{comp_id}': {e}") from e
                        prefix = destination.relative_to(input_dir)
                        used.update({(prefix / name if source.is_dir() else prefix.parent / name).as_posix(): k
                                     for name, k in rendered.items()})
                    _info(Fore.MAGENTA + f"    [{input_file.method.upper()}] " + Fore.LIGHTBLACK_EX + source.as_posix()
                          + Style.RESET_ALL + " --> " + Fore.WHITE + destination.relative_to(self.work / "data").as_posix())
                for spec in input_file.postprocess:
                    _info(Fore.LIGHTBLACK_EX + "        [POSTPROCESS] " + spec)
                    try:
                        run_processor(spec, target, setup_dir)
                    except ProcessorError as e:
                        raise DatapackError(f"component '{comp_id}': {e}") from e
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
                   and all(s.get("confirmed", True) for s in sources if s["role"] == "dataset"))
        return {"datapack": {"name": name, "format": FORMAT_VERSION, "conform": conform},
                "sources": sources, "tools": tool_versions(), "files": files}

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
        keys = self.keys()
        try:
            context = keys.resolve_all()
        except TemplateError as e:
            raise DatapackError(str(e)) from e

        components = self.generate_components(setup, testdef, keys)

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
            "components": components,
        })
        baseline = self.baseline(name, components, testdef_git)
        _dump_yaml(self.work / "baseline.yml", baseline, "# where the contents of this datapack come from (sequencer README, section 5.3)\n")
        problems = check_baseline(baseline, self.expected_baseline, parts=("datapack", "sources", "tools"))
        for problem in problems:
            _warn(f"baseline: {problem}")
        write_bag(self.work, name, f"testsuit {__version__} datapack")
        zip_path = zip_folder(self.work, output_dir / f"{name}.zip", name)

        _info(Fore.LIGHTBLACK_EX + "\nGenerated Datapack: " + Style.RESET_ALL + zip_path.as_posix() + "\n")
        if self.nocheck:
            _warn("option 'nocheck' was activated, this datapack shall not be used for a production run.")
        return zip_path


def datapack(testdef_file: str | os.PathLike, nocheck: bool = False, output_dir: str | os.PathLike | None = None,
             setup_config: str | None = None, expected_baseline: dict | None = None) -> Path:
    """Generate the datapack of a test definition file.

    The datapack contents are left in '.current_datapack' next to the test definition file.

    :param nocheck: accept test definition and setup folders that are not clean git tags, and datasets
        whose version cannot be confirmed (the datapack is then named '..._DRAFT')
    :param output_dir: where to write the zip (default: key 'datapack.output_dir')
    :param setup_config: setup configuration to use instead of the test definition's 'setup_config'
    :param expected_baseline: an expected baseline to check the datapack against (mismatches are warnings)
    :return: path of the zip
    :raises DatapackError: if the datapack cannot be generated
    """
    # importers and processors declared by external libraries
    load_plugins()
    try:
        return _Builder(Path(testdef_file).resolve(), nocheck, setup_config, expected_baseline).build(output_dir)
    except TemplateError as e:
        raise DatapackError(str(e)) from e


def read_baseline(zip_path: str | os.PathLike) -> dict | None:
    """The baseline.yml of a datapack zip (None for a datapack without one)."""
    with zipfile.ZipFile(zip_path) as archive:
        names = [n for n in archive.namelist() if n.count("/") == 1 and n.endswith("/baseline.yml")]
        if not names:
            return None
        return yaml.safe_load(archive.read(names[0]))
