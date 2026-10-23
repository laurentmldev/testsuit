"""Editor mode of the sequencer (README section 13.2): comment-keeping YAML edits, editing sessions on clones of
the lab and tests repositories, their checks and helpers, and pushing to the upstream repository."""
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from sequencer_t import Bench

from testsuit.sequencer import yamledit
from testsuit.sequencer.config import Config
from testsuit.sequencer.editor import Editor
from testsuit.sequencer.editor_schemas import schemas
from testsuit.sequencer.sequencer import Sequencer

# ── yamledit ────────────────────────────────────────────────────────────

TEXT = """\
# A test definition
id: count            # kept
name: Count
pipeline:
  phases:
    - id: run
      steps:
        - {id: start, component: dut, do: start}
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
    data["pipeline"]["phases"][0]["steps"].append({"id": "stop", "component": "dut", "do": "stop", "needs": ["wait"]})
    out = yamledit.merge(TEXT, data)
    assert yaml.safe_load(out) == data
    assert "# A test definition" in out and "id: count            # kept" in out
    assert "- id: wait     # a comment on a step" in out and "delay: 2s" in out
    # the new step takes the flow style of its siblings? the first one is flow, the second block: either is
    # fine, but only the changed lines differ
    changed = [line for line in out.splitlines() if line not in TEXT.splitlines()]
    assert len(changed) <= 6, changed


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
               "pipeline": {"phases": [{"id": "run", "stages": ["bench"], "steps": [
                   {"id": "start", "component": "dut", "do": "start"},
                   {"id": "wait", "delay": "{{ duration }}", "needs": ["start"]},
                   {"id": "stop", "component": "dut", "do": "stop", "needs": ["wait"]}]}]}}
    written = editor.write(session, "testdef/count/testdef.yml", data=testdef)
    assert written["kind"] == "testdef"
    assert not [p for p in written["problems"] if p["severity"] == "error"], written["problems"]
    assert not [p for p in editor.validate(session)["problems"] if p["severity"] == "error"]

    palette = editor.palette(session, "testdef/count/testdef.yml")
    assert {"start", "stop", "ready", "deploy"} <= set(palette["components"]["dut"]["commands"])
    assert palette["components"]["dut"]["commands"]["start"]["from"].endswith("counter_host")
    flat = editor.flatten(session, "testdef/count/testdef.yml")
    start = next(s for s in flat["phases"][0]["steps"] if s["id"] == "start")
    assert start["targets"][0]["concrete"] == "counter_1" and "app.sh" in str(start["targets"][0]["command"])

    keys = {k["key"]: k for k in editor.keys(session, "testdef/count/testdef.yml")["keys"]}
    assert keys["min_count"]["value"] == "3" and keys["min_count"]["origin"].endswith("globals.yml")
    preview = editor.preview(session, "testdef/count/testdef.yml", "setup/files/check.sh")
    assert preview["ok"] and "min 3" in preview["text"] and preview["component"] == "analysis"

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


def test_lab_session(repos):
    editor = repos.editor
    session = editor.open("lab")
    assert session.branch == "main" and session.kind == "lab"          # the lab is on a tag: the default branch
    kinds = {f["path"]: f["kind"] for f in editor.tree(session)}
    assert kinds["inventory.yml"] == "inventory" and kinds["types/bench.yml"] == "types"

    described = editor.describe_lab(editor.session_lab(session))
    assert described["stage_types"] == {"counter_bench": ["bench_1", "bench_2"]}
    assert described["types"]["counter_host"]["commands"]["start"]["from"] == "counter_host"
    assert described["types"]["counter_host"]["commands"]["deploy"]["from"] == "linux_system"

    # the choices of the schemas come from the lab: the types a component can have
    ctx = editor.context(session)
    schema = schemas(ctx)["inventory"]
    assert "counter_host" in yaml.safe_dump(schema)

    # an inventory naming an unknown type: a problem, and the push is refused
    inventory = editor.read(session, "inventory.yml")["data"]
    inventory["all"]["children"]["bench_1"]["hosts"]["counter_1"]["tsq_type"] = "nope"
    problems = editor.write(session, "inventory.yml", data=inventory)["problems"]
    assert any("nope" in p["message"] for p in problems)
    with pytest.raises(Exception, match="invalid"):
        editor.push(session, "Break it", "Ada", "ada@example.com")
    inventory["all"]["children"]["bench_1"]["hosts"]["counter_1"]["tsq_type"] = "counter_host"
    inventory["all"]["children"]["bench_1"]["hosts"]["counter_1"]["tsq_label"] = "Counter 1"
    editor.write(session, "inventory.yml", data=inventory)
    pushed = editor.push(session, "Label counter_1", "Ada", "ada@example.com", tag="lab-2")
    assert pushed["ok"]
    assert editor.published["lab"]["tag"] == "lab-2"


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
        assert "counter_host" in requests.get(f"{url}/edit/sessions/{sid}/context").json()["lab"]["types"]
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
        run = seq.submit(zip_path)
        pipeline = requests.get(f"{url}/runs/{run['id']}/pipeline").json()
        assert [p["id"] for p in pipeline["phases"]] == ["test_run", "exploit"]
        start = next(s for s in pipeline["phases"][0]["steps"] if s["id"] == "start")
        assert start["needs"] == ["deploy"] and start["do"] == "start" and start["part"] == "steps"
        assert pipeline["components"]["dut"]["role"] == "dut"

        # the UI, when it is built, or its placeholder
        assert requests.get(f"{url}/ui/").status_code == 200
    finally:
        server.should_exit = True
        thread.join(30)
