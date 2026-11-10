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

"""Editor mode of the sequencer (README section 13.2): comment-keeping YAML edits, editing sessions on clones of
the lab and tests repositories, their checks and helpers, and pushing to the upstream repository."""
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from sequencer_t import Bench

from testsuit.sequencer import yamledit
from testsuit.sequencer.config import Config
from testsuit.sequencer.editor import Editor
from testsuit.sequencer.editor_schemas import schemas
from testsuit.sequencer.sequencer import Sequencer, SequencerError

# ── yamledit ────────────────────────────────────────────────────────────

TEXT = """\
# A test definition
id: count            # kept
name: Count
pipeline:
  phases:
    - id: run
      steps:
        - {id: start, slot: dut, do: start}
        - id: wait     # a comment on a step
          delay: 1s
          needs: [start]
"""


def test_round_trip_is_identical():
    assert yamledit.merge(TEXT, yaml.safe_load(TEXT)) == TEXT


def test_merge_keeps_comments_and_style():
    data = yaml.safe_load(TEXT)
    data["name"] = "Counter"
    data["pipeline"]["phases"][0]["steps"][1]["delay"] = "2s"
    data["pipeline"]["phases"][0]["steps"].append({"id": "stop", "slot": "dut", "do": "stop", "needs": ["wait"]})
    out = yamledit.merge(TEXT, data)
    assert yaml.safe_load(out) == data
    assert "# A test definition" in out and "id: count            # kept" in out
    assert "- id: wait     # a comment on a step" in out and "delay: 2s" in out
    # the new step takes the flow style of its siblings? the first one is flow, the second block: either is
    # fine, but only the changed lines differ
    changed = [line for line in out.splitlines() if line not in TEXT.splitlines()]
    assert len(changed) <= 6, changed


def test_merge_keeps_lists_indented_as_written():
    # lists right under their key ("- a"), as PyYAML writes them, while the dump indents them
    text = "inputs:\n- source: a.txt   # first\n  target: a\n- source: b.txt\n  target: b\nother: 1\n"
    data = {"inputs": [{"source": "a.txt", "target": "a"}, {"source": "c.txt", "target": "b"}, {"source": "d"}],
            "other": 1}
    merged = yamledit.merge(text, data)
    assert yaml.safe_load(merged) == data
    assert merged.startswith("inputs:\n- source: a.txt   # first\n  target: a\n- source: c.txt\n"), merged


def test_merge_empties_a_mapping_or_a_list():
    """Removing the last item of a commented block leaves "key: {}" (or "[]"), not a bare "{}" line (a broken file)."""
    text = ("all:\n  hosts:                 # ad hoc components (no stage)\n    abc:\n      ansible_host: 10.0.1.2\n"
            "      tsq_role: plop\n  children:\n    st:\n      hosts: {x: {}}\n")
    data = yaml.safe_load(text)
    data["all"]["hosts"] = {}
    merged = yamledit.merge(text, data)
    assert yaml.safe_load(merged) == data
    assert "  hosts: {}" in merged and "# ad hoc components" in merged and "\n{}" not in merged, merged
    text = "steps:              # the steps\n  - id: a\n    run: true\nother: 1\n"
    merged = yamledit.merge(text, {"steps": [], "other": 1})
    assert yaml.safe_load(merged) == {"steps": [], "other": 1} and "steps: []" in merged, merged


def test_merge_keeps_a_renamed_key_as_written():
    text = ("types:\n  sim:   # the simulator\n    # how it starts\n    start: {run: \"go\"}\n"
            "    checks:\n      - {id: a, run: \"x\"}\n  other: {}\n")
    data = yaml.safe_load(text)
    data["types"] = {"simx": data["types"]["sim"], "other": {}}
    merged = yamledit.merge(text, data)
    assert yaml.safe_load(merged) == data
    assert merged == text.replace("  sim:   #", "  simx:  #"), merged          # the comment stays in its column


def test_merge_matches_list_items_by_id():
    data = yaml.safe_load(TEXT)
    steps = data["pipeline"]["phases"][0]["steps"]
    steps.reverse()                                  # same items, another order: comments follow their item
    out = yamledit.merge(TEXT, data)
    assert yaml.safe_load(out) == data
    assert "- id: wait     # a comment on a step" in out


def test_merge_breaks_an_alias_only_where_it_changes():
    text = "a: &shared\n  x: 1\n  y: 2\nb: *shared\nc: *shared\n"
    data = json.loads(json.dumps(yaml.safe_load(text)))     # as a form sends it: no shared objects
    data["b"]["x"] = 5
    out = yamledit.merge(text, data)
    assert yaml.safe_load(out) == {"a": {"x": 1, "y": 2}, "b": {"x": 5, "y": 2}, "c": {"x": 1, "y": 2}}
    assert "c: *shared" in out


def test_locate_and_syntax_error():
    assert yamledit.locate(TEXT, ["pipeline", "phases", 0, "steps", 1, "delay"])[0] == 10
    error = yamledit.syntax_error("a: [1, 2\nb: 3\n")
    assert error and error["line"] >= 1
    assert yamledit.syntax_error(TEXT) is None


# ── editing sessions ────────────────────────────────────────────────────

def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


class Repos:
    """The bench lab and datapack in one repository, with a bare upstream; tsqd has a clone of each."""

    def __init__(self, root: Path):
        bench = Bench(root / "work")
        work = root / "work"
        (work / ".gitignore").write_text(".current_datapack/\n")
        git(work, "init", "-q", "-b", "main")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "init")
        git(work, "tag", "v1")
        git(root, "init", "-q", "--bare", "-b", "main", "upstream.git")
        git(work, "push", "-q", str(root / "upstream.git"), "main", "--tags")
        git(root, "clone", "-q", "upstream.git", "lab-clone")
        git(root / "lab-clone", "checkout", "-q", "v1")     # a lab on a tag
        git(root, "clone", "-q", "upstream.git", "tests-clone")
        config = bench.config.model_dump(exclude={"file", "given"})
        config["lab"]["folder"] = str(root / "lab-clone" / "lab")
        config["server"] = {"editor": True, "workspaces": [str(root / "tests-clone" / "datapack")]}
        self.config = Config.model_validate(config)
        self.root = root
        self.editor = Editor(Sequencer(self.config))

    def upstream_file(self, path: str) -> str:
        return git(self.root / "upstream.git", "show", f"main:{path}")


@pytest.fixture
def repos(tmp_path):
    return Repos(tmp_path)


def test_workspace_session(repos):
    editor = repos.editor
    session = editor.open("workspace0")
    assert session.branch == "main" and session.kind == "workspace"
    kinds = {f["path"]: f["kind"] for f in editor.tree(session)}
    assert kinds["setup/bench_setup.yml"] == "setup" and kinds["setup/globals.yml"] == "dictionary"

    # a test definition, written as data: checked, then the helpers
    testdef = {"id": "count", "name": "Count", "setup_file": "../../setup/bench_setup.yml", "setup_config": "bench",
               "pipeline": {"phases": [{"id": "run", "stage_slots": ["bench"], "steps": [
                   {"id": "start", "slot": "dut", "do": "start"},
                   {"id": "wait", "delay": "{{ duration }}", "needs": ["start"]},
                   {"id": "stop", "slot": "dut", "do": "stop", "needs": ["wait"]}]}]}}
    written = editor.write(session, "testdef/count/testdef.yml", data=testdef)
    assert written["kind"] == "testdef"
    assert not [p for p in written["problems"] if p["severity"] == "error"], written["problems"]
    assert not [p for p in editor.validate(session)["problems"] if p["severity"] == "error"]

    palette = editor.palette(session, "testdef/count/testdef.yml")
    assert {"start", "stop", "ready", "deploy"} <= set(palette["slots"]["dut"]["commands"])
    start_cmd = palette["slots"]["dut"]["commands"]["start"]
    assert start_cmd["from"] == "role dut" and start_cmd["description"] == "Starts the counter."
    assert start_cmd["implementations"]["counter_1"]["from"] == "component counter_host"
    flat = editor.flatten(session, "testdef/count/testdef.yml")
    start = next(s for s in flat["phases"][0]["steps"] if s["id"] == "start")
    assert start["targets"][0]["concrete"] == "counter_1" and "app.sh" in str(start["targets"][0]["command"])

    keys = {k["key"]: k for k in editor.keys(session, "testdef/count/testdef.yml")["keys"]}
    assert keys["min_count"]["value"] == "3" and keys["min_count"]["origin"].endswith("globals.yml")
    preview = editor.preview(session, "testdef/count/testdef.yml", "setup/files/check.sh")
    assert preview["ok"] and "min 3" in preview["text"] and preview["slot"] == "analysis"

    # a run-time key in an input template: said to be one, not a bare 'undefined'
    check = session.folder / "setup/files/check.sh"
    original = check.read_text()
    check.write_text(original + "# on {{ component.host }}\n")
    preview = editor.preview(session, "testdef/count/testdef.yml", "setup/files/check.sh")
    assert not preview["ok"] and "run-time key" in preview["error"] and "slot.*" in preview["error"], preview
    check.write_text(original)

    # an unknown command is a problem of the step, located in the file
    testdef["pipeline"]["phases"][0]["steps"][0]["do"] = "launch"
    problems = editor.write(session, "testdef/count/testdef.yml", data=testdef)["problems"]
    assert any("launch" in p["message"] for p in problems)
    testdef["pipeline"]["phases"][0]["steps"][0]["do"] = "start"
    editor.write(session, "testdef/count/testdef.yml", data=testdef)

    generated = editor.generate(session, "testdef/count/testdef.yml")
    assert generated["ok"], generated
    assert "datapack.yml" in {f["path"] for f in generated["files"]}
    assert editor.generated(session, generated["name"]).is_file()

    # push in the user's name, with a tag
    diff = editor.diff(session)
    assert [f["path"] for f in diff["files"]] == ["datapack/testdef/count/testdef.yml"]
    pushed = editor.push(session, "Add the count test", "Ada", "ada@example.com", tag="v2")
    assert pushed["ok"], pushed
    assert "id: count" in repos.upstream_file("datapack/testdef/count/testdef.yml")
    assert git(repos.root / "upstream.git", "log", "-1", "--format=%an <%ae> %s", "v2").strip() == \
        "Ada <ada@example.com> Add the count test"


def test_datapacks_synthesis_and_baseline(repos, tmp_path):
    editor = repos.editor
    session = editor.open("workspace0", local=True)
    # a dataset from a git repository, its version written as a key of a setup dictionary
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.txt").write_text("a\n")
    git(data, "init", "-q", "-b", "main")
    git(data, "add", "-A")
    git(data, "commit", "-qm", "data")
    for tag in ("v1.9.0", "v1.10.0"):
        git(data, "tag", tag)
    setup = yaml.safe_load(editor.read(session, "setup/bench_setup.yml")["content"])
    setup["datarepositories"] = [{"id": "repo", "path": str(tmp_path / "datarepo"), "datasources": [
        {"id": "src", "importer": "git", "source": str(data), "datasets": [{"id": "ds", "version": "{{ data_version }}"}]}]}]
    editor.write(session, "setup/bench_setup.yml", data=setup)
    editor.write(session, "setup/globals.yml",
                 content="duration: 0.5s\nmin_count: 3\ndata_version: v1.9.0   # the data in use\n")
    editor.write(session, "testdef/count/testdef.yml", data={
        "id": "count", "name": "Count", "setup_file": "../../setup/bench_setup.yml", "setup_config": "bench",
        "pipeline": {"phases": [{"id": "run", "stage_slots": ["bench"], "steps": [{"id": "start", "slot": "dut", "do": "start"}]}]}})

    quick = editor.synthesis(session, "testdef/count/testdef.yml", versions=False)   # the versions listed after
    assert quick["baseline"][0]["versions_pending"] and quick["baseline"][0]["versions"] is None
    listed = editor.versions(quick["baseline"][0]["importer"], quick["baseline"][0]["source"])
    assert listed["versions"] == ["v1.10.0", "v1.9.0", "main"] and listed["error"] is None
    found = editor.synthesis(session, "testdef/count/testdef.yml")
    assert not found["baseline"][0]["versions_pending"]                 # listed less than a minute ago: kept
    # the checks before generating, one at a time: the same problems as the synthesis in one go
    split = editor.synthesis(session, "testdef/count/testdef.yml", versions=False, run_checks=False)["problems"]
    assert split + editor.run_problems(session, "testdef/count/testdef.yml") == found["problems"]
    assert found["testdef"]["id"] == "count" and found["testdef"]["setup_config"] == "bench"
    assert "dut" in {c["id"] for c in found["slots"]}
    assert any(i["files"] for c in found["slots"] for i in c["inputs"])
    dictionary = next(d for d in found["dictionaries"] if d["path"] == "setup/globals.yml")
    assert {"key": "min_count", "value": "3", "overridden_by": None} in dictionary["keys"]
    (ds,) = found["baseline"]
    assert ds["version"] == "v1.9.0" and ds["key"] == "data_version" and ds["defined_in"] == "setup/globals.yml"
    assert ds["editable"] and ds["versions"] == ["v1.10.0", "v1.9.0", "main"], ds

    assert ds["source"] == str(data) and ds["local_path"] == str((tmp_path / "datarepo" / "ds" / "v1.9.0").resolve())
    assert ds["repository_path"] == str((tmp_path / "datarepo").resolve()) and not ds["imported"]
    resolved = editor.datasets(session, "setup/bench_setup.yml")
    assert resolved["testdef"] == "testdef/count/testdef.yml" and resolved["datasets"][0]["version"] == "v1.9.0"
    assert editor.datasets(session, "setup/other.yml")["error"]

    changed = editor.set_version(session, "testdef/count/testdef.yml", "ds", "v1.10.0")
    assert changed["file"] == "setup/globals.yml"
    assert "data_version: v1.10.0  # the data in use" in editor.read(session, "setup/globals.yml")["content"]
    with pytest.raises(SequencerError, match="no dataset"):
        editor.set_version(session, "testdef/count/testdef.yml", "other", "v1")

    # generation in the background: the git output (stderr included) is in its log, line by line
    editor.set_version(session, "testdef/count/testdef.yml", "ds", "v404")
    job = editor.start_generate(session, "testdef/count/testdef.yml")["job"]
    lines, since, state = [], 0, {"done": False}
    for _ in range(300):
        state = editor.generation_job(session, job, since)
        lines += state["lines"]
        since = state["next"]
        if state["done"]:
            break
        time.sleep(0.1)
    assert state["done"] and not state["result"]["ok"] and "v404" in state["result"]["error"]
    assert any("v404" in line and "not found" in line.lower() for line in lines), lines
    assert any("git clone" in line and "failed" in line for line in lines), lines
    with pytest.raises(SequencerError, match="no generation"):
        editor.generation_job(session, "nope")


def _follow(editor, session, job):
    lines, since, state = [], 0, {"done": False}
    for _ in range(300):
        state = editor.generation_job(session, job, since)
        lines += state["lines"]
        since = state["next"]
        if state["done"]:
            break
        time.sleep(0.1)
    return state, lines


def test_generation_errors_and_stop(repos):
    editor = repos.editor
    session = editor.open("workspace0", local=True)
    editor.write(session, "testdef/count/testdef.yml", data={
        "id": "count", "name": "Count", "setup_file": "../../setup/bench_setup.yml", "setup_config": "bench",
        "pipeline": {"phases": [{"id": "run", "stage_slots": ["bench"], "steps": [{"id": "start", "slot": "dut", "do": "start"}]}]}})
    assert editor.synthesis(session, "testdef/count/testdef.yml")["problems"] == []
    setup = yaml.safe_load(editor.read(session, "setup/bench_setup.yml")["content"])
    inputs = setup["slot_types"]["dut_app"]["configurations"]["standard"]["inputs"]

    # what the sequencer would refuse at posting (a phase naming a slot the setup does not have): listed before generating
    editor.write(session, "testdef/count/testdef.yml", data={
        "id": "count", "name": "Count", "setup_file": "../../setup/bench_setup.yml", "setup_config": "bench",
        "pipeline": {"phases": [{"id": "run", "stage_slots": ["bench", "rig"], "slots": ["test_sequencer"],
                                 "steps": [{"id": "start", "slot": "dut", "do": "start"}]}]}})
    problems = editor.synthesis(session, "testdef/count/testdef.yml")["problems"]
    assert problems == ["pipeline: phase 'run': unknown stage slot 'rig' (setup stage_slots: bench)",
                        "pipeline: phase 'run': unknown slot 'test_sequencer' (setup ad hoc slots: analysis)"], problems
    # the same, on the setup file: a setup changed without the pipelines using it
    setup_warnings = [p["message"] for p in editor.check_file(session, "setup/bench_setup.yml")]
    assert "testdef/count/testdef.yml (bench): phase 'run': unknown stage slot 'rig' (setup stage_slots: bench)" in setup_warnings, setup_warnings
    editor.write(session, "testdef/count/testdef.yml", data={
        "id": "count", "name": "Count", "setup_file": "../../setup/bench_setup.yml", "setup_config": "bench",
        "pipeline": {"phases": [{"id": "run", "stage_slots": ["bench"], "steps": [{"id": "start", "slot": "dut", "do": "start"}]}]}})

    # a missing template: the error, in the result and as the last line of the log
    inputs[1]["source"] = "files/nope.txt"
    editor.write(session, "setup/bench_setup.yml", data=setup)
    state, lines = _follow(editor, session, editor.start_generate(session, "testdef/count/testdef.yml")["job"])
    assert state["done"] and "files/nope.txt' not found" in state["result"]["error"]
    assert lines[-1].startswith("ERROR: ") and "nope.txt" in lines[-1], lines

    # empty source and target (keys with an empty value): listed before generating, an error when generating,
    # skipped with a warning when generating anyway (lenient)
    editor.write(session, "setup/globals.yml", content="duration: 0.5s\nmin_count: 3\nempty: ''\n")
    inputs[0]["source"] = "{{ empty }}"
    inputs[1]["target"] = ""
    editor.write(session, "setup/bench_setup.yml", data=setup)
    problems = editor.synthesis(session, "testdef/count/testdef.yml")["problems"]
    assert any("input 1: empty source" in p for p in problems) and any("input 2: empty target" in p for p in problems), problems
    result = editor.generate(session, "testdef/count/testdef.yml")
    assert not result["ok"] and "input 1: empty source" in result["error"], result["error"]
    result = editor.generate(session, "testdef/count/testdef.yml", lenient=True)
    assert result["ok"], result["error"]
    assert len(result["datapack"]["skipped"]) == 2 and result["name"].endswith("_DRAFT")
    inputs[0]["source"] = "files/app.sh"
    inputs[1]["target"] = "conf.txt"

    # a generator script that never ends: its output comes live, and Stop ends it
    editor.write(session, "setup/files/slow.py", content="import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                 "print('working', flush=True)\ntime.sleep(60)\n")
    inputs[1] = {"source": "files/conf.txt", "target": "conf.bin", "method": "generate", "generator": "files/slow.py"}
    editor.write(session, "setup/bench_setup.yml", data=setup)
    job = editor.start_generate(session, "testdef/count/testdef.yml")["job"]
    for _ in range(200):
        if any("| working" in line for line in editor.generation_job(session, job)["lines"]):
            break
        time.sleep(0.1)
    else:
        raise AssertionError(editor.generation_job(session, job)["lines"])
    started = time.monotonic()
    assert editor.stop_generation(session, job) == {"stopping": True}
    state, lines = _follow(editor, session, job)
    assert state["done"] and state["result"]["error"] == "Generation stopped." and time.monotonic() - started < 10
    assert any("[GENERATE]" in line and "conf.txt" in line for line in lines), lines
    assert editor.stop_generation(session, job) == {"stopping": False}


def test_rejected_push_then_rebase(repos):
    editor = repos.editor
    first, second = editor.open("workspace0"), editor.open("workspace0")
    editor.write(first, "setup/globals.yml", data={"duration": "1s", "min_count": 3})
    assert editor.push(first, "Longer", "Ada", "ada@example.com")["ok"]

    editor.write(second, "setup/globals.yml", data={"duration": "0.5s", "min_count": 5})
    rejected = editor.push(second, "More counts", "Bob", "bob@example.com", tag="v9")
    assert not rejected["ok"] and "rebase" in rejected["hint"]
    assert "v9" not in git(editor.get(second.id).root, "tag")         # undone: no tag, no commit
    assert editor.diff(second)["files"]                               # the change is still there

    rebased = editor.rebase(second)
    assert not rebased["ok"] and rebased["conflicts"] == ["datapack/setup/globals.yml"]
    editor.write(second, "setup/globals.yml", content="duration: 1s\nmin_count: 5\n")   # fixed in the YAML view
    assert editor.push(second, "More counts", "Bob", "bob@example.com")["ok"]
    assert yaml.safe_load(repos.upstream_file("datapack/setup/globals.yml")) == {"duration": "1s", "min_count": 5}


def test_in_place_session(repos, tmp_path):
    editor = repos.editor
    folder = repos.root / "tests-clone" / "datapack"
    session = editor.open("workspace0", local=True)
    assert session.local and session.root == (repos.root / "tests-clone").resolve() and session.branch == "main"
    assert editor.open("workspace0", local=True).id == session.id          # one in-place session per repository
    with pytest.raises(SequencerError, match="checked-out branch"):
        editor.open("workspace0", "other", local=True)

    (repos.root / "tests-clone" / "notes.txt").write_text("mine")               # outside the edited folder
    editor.write(session, "setup/globals.yml", data={"duration": "2s", "min_count": 4})
    assert yaml.safe_load((folder / "setup" / "globals.yml").read_text())["duration"] == "2s"   # written in place
    assert editor.describe(session)["changed"] == 1
    assert [f["path"] for f in editor.diff(session)["files"]] == ["datapack/setup/globals.yml"]
    assert editor.push(session, "Longer", "Ada", "ada@example.com")["ok"]
    assert yaml.safe_load(repos.upstream_file("datapack/setup/globals.yml"))["duration"] == "2s"
    assert "notes.txt" in git(repos.root / "tests-clone", "status", "--porcelain")   # not pushed, left as is
    # someone pushed meanwhile: the push is refused, the rebase replays the working copy on top
    other = editor.open("workspace0")
    editor.write(other, "setup/extra.yml", data={"extra": 1})
    assert editor.push(other, "Extra", "Bob", "bob@example.com")["ok"]
    editor.write(session, "setup/globals.yml", data={"duration": "2s", "min_count": 6})
    assert "rebase" in editor.push(session, "More", "Ada", "ada@example.com")["hint"]
    assert editor.rebase(session)["ok"]
    assert (folder / "setup" / "extra.yml").is_file()
    assert editor.push(session, "More", "Ada", "ada@example.com")["ok"]
    assert yaml.safe_load(repos.upstream_file("datapack/setup/globals.yml"))["min_count"] == 6

    editor.close(session.id)
    assert (folder / "setup" / "globals.yml").is_file()                     # closing keeps the folder

    # the lab, checked out on a tag: edited in place, not pushed from a detached HEAD
    lab = editor.open("lab", local=True)
    assert lab.branch == "" and editor.kind(lab, "inventory.yml") == "inventory"
    with pytest.raises(SequencerError, match="detached"):
        editor.push(lab, "x", "Ada", "ada@example.com")

    # a folder outside git: files only
    plain = tmp_path / "plain"
    shutil.copytree(folder, plain)
    editor.seq.config.server.workspaces.append(str(plain))
    loose = editor.open("workspace1", local=True)
    assert not loose.git and editor.describe(loose)["changed"] is None
    editor.write(loose, "setup/globals.yml", data={"duration": "3s", "min_count": 1})
    assert "3s" in (plain / "setup" / "globals.yml").read_text()
    with pytest.raises(SequencerError, match="not a git work tree"):
        editor.diff(loose)


def test_lab_session(repos):
    editor = repos.editor
    session = editor.open("lab")
    assert session.branch == "main" and session.kind == "lab"          # the lab is on a tag: the default branch
    kinds = {f["path"]: f["kind"] for f in editor.tree(session)}
    assert kinds["inventory.yml"] == "inventory" and kinds["roles/bench.yml"] == "library"

    described = editor.describe_lab(editor.session_lab(session))
    assert described["stage_types"] == {"counter_bench": ["bench_1", "bench_2"]}
    assert described["roles"]["dut"]["commands"]["start"]["from"] == "dut"
    assert described["roles"]["dut"]["commands"]["deploy"]["from"] == "linux_system"
    assert described["roles"]["dut"]["used_by"] == ["counter_1", "counter_2"]
    assert described["library"]["counter_host"]["commands"]["start"]["from"] == "counter_host"
    assert described["library"]["counter_host"]["used_by"] == ["counter_1", "counter_2"]
    host = described["components"]["counter_1"]
    assert host["chain"] == ["component", "linux_system", "counter_host", "counter_1"]
    assert host["commands"]["deploy"]["from"] == "component linux_system" and host["missing"] == []
    assert described["library"]["counter_host"]["roles"] == ["dut"] and described["roles"]["dut"]["implemented_by"] == ["counter_host"]
    assert (host["role"], host["role_given"], host["role_choices"]) == ("dut", "", ["dut"])   # the role of its component

    # the choices of the schemas come from the lab: the roles a host can have, the components it can extend
    ctx = editor.context(session)
    schema = schemas(ctx)["inventory"]
    host_vars = schema["properties"]["all"]["properties"]["hosts"]["additionalProperties"]["properties"]
    assert {"dut", "sequencer", "linux_system"} <= set(host_vars["tsq_role"]["enum"])
    assert "counter_host" not in host_vars["tsq_role"]["enum"]
    assert {"counter_host", "linux_system", "counter_1"} <= set(host_vars["tsq_extends"]["examples"])

    # an inventory naming an unknown role: a problem, and the push is refused
    inventory = editor.read(session, "inventory.yml")["data"]
    inventory["all"]["children"]["bench_1"]["hosts"]["counter_1"]["tsq_role"] = "nope"
    problems = editor.write(session, "inventory.yml", data=inventory)["problems"]
    assert any("nope" in p["message"] for p in problems)
    with pytest.raises(Exception, match="invalid"):
        editor.push(session, "Break it", "Ada", "ada@example.com")
    inventory["all"]["children"]["bench_1"]["hosts"]["counter_1"]["tsq_role"] = "dut"
    inventory["all"]["children"]["bench_1"]["hosts"]["counter_1"]["tsq_label"] = "Counter 1"
    editor.write(session, "inventory.yml", data=inventory)
    pushed = editor.push(session, "Label counter_1", "Ada", "ada@example.com", tag="lab-2")
    assert pushed["ok"]
    assert editor.published["lab"]["tag"] == "lab-2"


def test_lab_session_icons(repos):
    """A component's picture on the Lab view: an image uploaded to the lab repository, named by tsq_icon; an icon
    neither built in nor a file of the lab is a warning."""
    editor = repos.editor
    session = editor.open("lab")
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'/>"
    assert editor.upload(session, "icons/counter.svg", svg) == {"path": "icons/counter.svg", "size": len(svg)}
    assert editor.raw(session, "icons/counter.svg").read_bytes() == svg
    with pytest.raises(SequencerError, match="only images"):
        editor.upload(session, "icons/run.sh", b"rm -rf /")
    with pytest.raises(SequencerError, match="outside the repository"):
        editor.upload(session, "../../evil.png", b"x")
    inventory = editor.read(session, "inventory.yml")["data"]
    hosts = inventory["all"]["children"]["bench_1"]["hosts"]
    hosts["counter_1"]["tsq_icon"] = "icons/counter.svg"
    hosts["counter_1"]["tsq_links"] = {"seq_host": "serial"}
    inventory["all"]["children"]["bench_2"]["hosts"]["counter_2"]["tsq_icon"] = "rocket"
    problems = editor.write(session, "inventory.yml", data=inventory)["problems"]
    assert [p["message"] for p in problems if "tsq_icon" in p["message"]] == [
        "component 'counter_2': tsq_icon 'rocket' is neither one of server, workstation, switch, router, tap, embedded, "
        "board, sensor, instrument, power, simulator, storage, network, camera, generic nor an image file (.png, .jpg, "
        ".jpeg, .gif, .svg, .webp) (the Lab view draws a plain box)"]
    assert not [p for p in problems if p["severity"] == "error"]


def test_renaming_roles_and_components(repos):
    """While the inventory still names a renamed role, the helpers list the session's roles (not the running
    lab's); rename_references then makes the inventory and the deriving roles follow. Same for a component."""
    editor = repos.editor
    session = editor.open("lab")
    roles = editor.read(session, "roles/bench.yml")["data"]
    roles["roles"]["counter_dut"] = roles["roles"].pop("dut")
    roles["roles"]["fast_counter"] = {"extends": "dut", "description": "derived"}
    editor.write(session, "roles/bench.yml", data=roles)
    ctx = editor.context(session)
    assert ctx["lab_error"] and "dut" in ctx["lab_error"]
    assert "counter_dut" in ctx["lab"]["roles"] and "dut" not in ctx["lab"]["roles"]
    assert ctx["lab"]["roles"]["fast_counter"]["chain"] == ["fast_counter"]      # its parent is gone: itself only

    roles["roles"]["fast_counter"]["extends"] = "counter_dut"        # the form renames those of its own file
    editor.write(session, "roles/bench.yml", data=roles)
    # another inventory with its own library, where dut is another role: left alone
    other = "all:\n  vars: {tsq_library: [other_lib/]}\n  hosts:\n    c9: {tsq_role: dut}\n"
    editor.write(session, "other.yml", content=other)
    editor.write(session, "other_lib/t.yml", content="roles:\n  dut: {extends: linux_system}\n")
    assert editor.rename_references(session, "role", "dut", "counter_dut", skip="roles/bench.yml") == \
        ["components/bench.yml"]                                  # the roles its components list
    assert editor.read(session, "components/bench.yml")["data"]["components"]["counter_host"]["roles"] == ["counter_dut"]
    ctx = editor.context(session)
    assert ctx["lab_error"] is None
    assert sorted(ctx["lab"]["roles"]["counter_dut"]["used_by"]) == ["counter_1", "counter_2"]
    assert "tsq_role: dut" not in editor.read(session, "inventory.yml")["content"]
    assert editor.read(session, "other.yml")["content"] == other

    components = editor.read(session, "components/bench.yml")["data"]
    components["components"]["counter_app"] = components["components"].pop("counter_host")
    editor.write(session, "components/bench.yml", data=components)
    assert editor.context(session)["lab_error"]
    assert editor.rename_references(session, "component", "counter_host", "counter_app",
                                    skip="components/bench.yml") == ["inventory.yml"]
    ctx = editor.context(session)
    assert ctx["lab_error"] is None and ctx["lab"]["library"]["counter_app"]["used_by"] == ["counter_1", "counter_2"]


def test_sessions_survive_a_restart(repos):
    session = repos.editor.open("workspace0")
    again = Editor(repos.editor.seq)
    assert again.get(session.id).branch == "main"
    again.close(session.id)
    assert not Path(session.dir).exists()


def test_api(repos, tmp_path):
    """The Editor routes and the run pipeline, through tsqd's REST API."""
    import socket
    import threading
    import time

    import requests
    import uvicorn

    from testsuit.sequencer.api import create_app

    seq = repos.editor.seq
    app = create_app(seq)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            requests.get(f"{url}/health", timeout=1)
            break
        except requests.ConnectionError:
            time.sleep(0.1)
    try:
        home = requests.get(f"{url}/edit").json()
        assert {r["id"] for r in home["repositories"]} == {"lab", "workspace0"}
        sid = requests.post(f"{url}/edit/sessions", json={"repo": "workspace0"}).json()["id"]
        assert requests.get(f"{url}/edit/sessions/{sid}/schemas").json()["testdef"]["title"] == "Test definition"
        assert "counter_host" in requests.get(f"{url}/edit/sessions/{sid}/context").json()["lab"]["library"]
        put = requests.put(f"{url}/edit/sessions/{sid}/files/setup/globals.yml",
                           json={"data": {"duration": "0.2s", "min_count": 2}})
        assert put.status_code == 200 and put.json()["data"]["min_count"] == 2
        assert requests.get(f"{url}/edit/sessions/{sid}/files/../../etc/passwd").status_code in (400, 404)
        assert requests.post(f"{url}/edit/sessions/{sid}/validate", json={}).json()["problems"] == []
        diff = requests.get(f"{url}/edit/sessions/{sid}/diff").json()
        assert diff["files"] == [{"status": "M", "path": "datapack/setup/globals.yml"}]
        assert requests.post(f"{url}/edit/reachability", json={"host": "localhost", "transport": "local"}).json()["ok"]

        # the pipeline of a queued run, for the run graph
        from sequencer_t import Bench
        zip_path = Bench(tmp_path / "other").build()
        logging.getLogger("testsuit").setLevel(logging.INFO)
        run = seq.submit(zip_path)
        # the event, in the log of the sequencer
        logs = requests.get(f"{url}/logs").json()
        assert any(line["message"].startswith(f"run: run={run['id']} state=queued") for line in logs["lines"]), logs
        assert requests.get(f"{url}/logs", params={"since": logs["next"]}).json()["lines"] == []
        # every component probed now; probes are not kept as events
        assert requests.post(f"{url}/lab/probe").json()["probed"] == len(seq.lab.inventory.components)
        assert all(c["probed_at"] for c in requests.get(f"{url}/components").json())
        assert not [e for e in seq.store.events() if e["kind"] == "probe"]
        pipeline = requests.get(f"{url}/runs/{run['id']}/pipeline").json()
        assert [p["id"] for p in pipeline["phases"]] == ["test_run", "exploit"]
        start = next(s for s in pipeline["phases"][0]["steps"] if s["id"] == "start")
        assert start["needs"] == ["deploy"] and start["do"] == "start" and start["part"] == "steps"
        assert pipeline["slots"]["dut"]["role"] == "dut"

        # the UI, when it is built, or its placeholder
        assert requests.get(f"{url}/ui/").status_code == 200
    finally:
        server.should_exit = True
        thread.join(30)


def test_schemas_match_the_datapack_models():
    """Every key the Editor's forms offer in a setup or a test definition is one the datapack tool accepts."""
    from testsuit.datatools.datapack import model
    from testsuit.sequencer import editor_schemas as schemas
    for schema, cls in ((schemas.SLOT_TYPE, model.SlotType), (schemas.SLOT, model.Slot),
                        (schemas.SETUP_CONFIGURATION, model.SetupConfiguration), (schemas.INPUT, model.InputFile),
                        (schemas.SETUP, model.Setup), (schemas.TESTDEF, model.Testdef)):
        assert set(schema.get("properties") or {}) <= set(cls.model_fields), cls.__name__
    model.SlotType(configurations={}, description="a counter")


def test_setup_type_role_inputs(repos):
    """A slot type bound to a lab role: the inputs the role requires and a configuration lacks are
    warned about, and so is a slot of another role using that type."""
    editor = repos.editor
    session = editor.open("workspace0", local=True)
    lab, _ = editor.lab_for(session)
    lab.library.roles["dut"].inputs = [
        {"name": "app.sh", "type": "file", "optional": False, "description": ""},
        {"name": "extra/", "type": "folder", "optional": False, "description": "How to make it."},
        {"name": "notes.txt", "type": "file", "optional": True, "description": ""}]
    setup = yaml.safe_load(editor.read(session, "setup/bench_setup.yml")["content"])
    setup["slot_types"]["dut_app"]["role"] = "dut"
    setup["configurations"]["bench"]["slots"]["analysis"]["type"] = "dut_app"
    editor.write(session, "setup/bench_setup.yml", data=setup)
    messages = [p["message"] for p in editor.check_file(session, "setup/bench_setup.yml")]
    assert "type 'dut_app', configuration 'standard': no input 'extra/', which its role dut expects" in messages, messages
    assert not any("'app.sh'" in m or "notes.txt" in m for m in messages), messages
    assert any(m.startswith("slot 'analysis': role 'sequencer', but its type 'dut_app' is for role 'dut'") for m in messages), messages
    described = editor.context(session)["lab"]["roles"]["dut"]["inputs"]
    assert [i["name"] for i in described] == ["app.sh", "extra/", "notes.txt"] and described[1]["from"] == "dut"
