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

"""The testsuit app (testsuit.app): testsuit.toml, its server (the exploit mode, the sequencer's API passed
through to a tsqd), the tsqd it starts and watches, and the testsuit command (start, status, stop)."""
import os
import socket
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
import requests

from testsuit.app import cli
from testsuit.app.config import AppConfig, ConfigError, load_config

# a stand-in for tsqd: /health, an event stream, an echo of what it receives; --listen and --config as tsqd
FAKE_TSQD = textwrap.dedent('''
    import argparse, asyncio, json, os, sys
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import StreamingResponse
    p = argparse.ArgumentParser(); p.add_argument("--listen"); p.add_argument("--config"); a = p.parse_args()
    if os.environ.get("FAKE_TSQD_FAIL"):
        print("tsq.yml not valid", flush=True); sys.exit(2)
    app = FastAPI()
    @app.get("/health")
    async def health():
        return {"ok": True, "pid": os.getpid(), "config": a.config}
    @app.post("/echo")
    async def echo(request: Request):
        return {"body": (await request.body()).decode(), "query": str(request.url.query),
                "type": request.headers.get("content-type")}
    @app.get("/events")
    async def events():
        async def stream():
            yield ": connected\\n\\n"
            while True:
                await asyncio.sleep(0.2)
                yield "event: run\\ndata: {}\\n\\n"
        return StreamingResponse(stream(), media_type="text/event-stream")
    @app.get("/plugins")
    async def plugins():
        return []
    host, _, port = a.listen.rpartition(":")
    print("fake tsqd on", a.listen, flush=True)
    uvicorn.run(app, host=host, port=int(port), log_level="warning")
''')


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(check, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("timed out")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The user's configuration folder (testsuit.toml, the logs, the folders opened) in the test folder."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    monkeypatch.delenv("TESTSUIT_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path / "config" / "testsuit"


@pytest.fixture
def fake_tsqd(tmp_path):
    script = tmp_path / "fake_tsqd.py"
    script.write_text(FAKE_TSQD)
    return [sys.executable, str(script)]


def tsqd_of(config: AppConfig, command, log: Path, **kw):
    from testsuit.app.supervisor import Tsqd
    seq = config.sequencer
    return Tsqd(config.sequencer_url, None if seq.url else seq.listen, config.path(seq.config), log,
                command=command, restart=seq.restart, **kw)


def serve(app, port: int):
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for(lambda: server.started)
    server.thread = thread
    return server


# ── testsuit.toml ────────────────────────────────────────────────────────

def test_config_defaults_and_lookup(home, tmp_path, monkeypatch):
    config = load_config()
    assert config.file is None and config.modes == ["exploit"] and config.ui.listen == "127.0.0.1:8650"
    assert config.local_url == "http://127.0.0.1:8650" and config.loopback_only

    # the user's testsuit.toml, then ./testsuit.toml, then $TESTSUIT_CONFIG, then --config
    home.mkdir(parents=True)
    (home / "testsuit.toml").write_text('title = "mine"\n')
    assert load_config().title == "mine"
    (tmp_path / "testsuit.toml").write_text('title = "here"\n')
    assert load_config().title == "here"
    other = tmp_path / "other.toml"
    other.write_text('title = "env"\nmodes = ["sequencer", "exploit", "sequencer"]\n')
    monkeypatch.setenv("TESTSUIT_CONFIG", str(other))
    config = load_config()
    assert config.title == "env" and config.modes == ["sequencer", "exploit"]
    assert load_config(tmp_path / "testsuit.toml").title == "here"
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "none.toml")


def test_config_sections_and_errors(tmp_path):
    file = tmp_path / "testsuit.toml"
    file.write_text(textwrap.dedent('''
        modes = ["sequencer"]
        logo = "logo.svg"
        [ui]
        listen = "0.0.0.0:9000"
        [sequencer]
        config = "lab/tsq.yml"
        listen = "127.0.0.1:9001"
    '''))
    config = load_config(file)
    assert config.on("sequencer") and not config.on("exploit")
    assert config.local_url == "http://127.0.0.1:9000" and not config.loopback_only
    assert config.sequencer_url == "http://127.0.0.1:9001"
    assert config.path(config.sequencer.config) == tmp_path / "lab" / "tsq.yml"
    assert config.logo_file == tmp_path / "logo.svg"
    rows = {k: (v, s) for k, v, s in config.describe()}
    assert rows["ui.listen"] == ("0.0.0.0:9000", str(file)) and rows["ui.browser"] == (True, "default")
    assert rows["sequencer.url"] == (None, "default")

    for text, message in (('modes = []', "at least one"), ('modes = ["nope"]', "modes"),
                          ('[ui]\nlisten = "nope"', "host:port"), ('[sequencer]\nurl = "lab:8640"', "http"),
                          ('colour = "red"', "colour"), ('modes = [', "testsuit.toml")):
        file.write_text(text)
        with pytest.raises(ConfigError, match=message):
            load_config(file)
    file.write_text('[sequencer]\nurl = "http://lab:8640/"')
    assert load_config(file).sequencer_url == "http://lab:8640"


# ── the tsqd of the sequencer mode ───────────────────────────────────────

def test_tsqd_started_restarted_and_stopped(tmp_path, fake_tsqd, monkeypatch):
    from testsuit.app import supervisor

    monkeypatch.setattr(supervisor, "BACKOFF", (0.2,))
    port = free_port()
    config = AppConfig(modes=["sequencer"], sequencer={"listen": f"127.0.0.1:{port}", "config": "tsq.yml"})
    tsqd = tsqd_of(config, fake_tsqd, tmp_path / "tsqd.log")
    assert tsqd.status()["state"] == "stopped"
    tsqd.start()
    try:
        wait_for(lambda: tsqd.state == "running")
        status = tsqd.status()
        assert status["managed"] and status["pid"] and status["url"] == f"http://127.0.0.1:{port}"
        first = status["pid"]
        assert supervisor.healthy(tsqd.url)["config"] == str(Path.cwd() / "tsq.yml")
        # it ends by itself: started again
        os.kill(first, 9)
        wait_for(lambda: tsqd.state == "running" and tsqd.pid not in (None, first))
        assert tsqd.restarts == 1 and tsqd.last_exit != 0
        assert "fake tsqd on" in tsqd.log_tail()
        # restarted from the Configuration tab
        second = tsqd.pid
        tsqd.restart_now()
        wait_for(lambda: tsqd.state == "running" and tsqd.pid not in (None, second))
    finally:
        tsqd.stop()
    assert tsqd.status()["state"] == "stopped" and supervisor.healthy(tsqd.url) is None


def test_tsqd_that_cannot_start_is_given_up(tmp_path, fake_tsqd, monkeypatch):
    from testsuit.app import supervisor

    monkeypatch.setattr(supervisor, "BACKOFF", (0.05,))
    monkeypatch.setenv("FAKE_TSQD_FAIL", "1")
    config = AppConfig(modes=["sequencer"], sequencer={"listen": f"127.0.0.1:{free_port()}"})
    tsqd = tsqd_of(config, fake_tsqd, tmp_path / "tsqd.log")
    tsqd.start()
    try:
        wait_for(lambda: tsqd.state == "failed", 60)
        assert "right after its start" in tsqd.detail and tsqd.last_exit == 2
        assert tsqd.log_tail().count("tsq.yml not valid") == supervisor.MAX_QUICK_EXITS
    finally:
        tsqd.stop()


def test_tsqd_elsewhere_or_already_running(tmp_path, fake_tsqd):
    import subprocess
    port = free_port()
    proc = subprocess.Popen([*fake_tsqd, "--listen", f"127.0.0.1:{port}"])
    try:
        # sequencer.url: not started, only probed
        remote = tsqd_of(AppConfig(sequencer={"url": f"http://127.0.0.1:{port}"}), fake_tsqd, tmp_path / "x.log")
        remote.start()
        wait_for(lambda: remote.status()["state"] == "running")
        assert remote.status()["managed"] is False
        with pytest.raises(RuntimeError):
            remote.restart_now()
        # one already answering on its port: used as it is, not stopped with testsuit
        adopted = tsqd_of(AppConfig(sequencer={"listen": f"127.0.0.1:{port}"}), fake_tsqd, tmp_path / "y.log")
        adopted.start()
        assert adopted.adopted and adopted.status()["state"] == "running" and not adopted.status()["managed"]
        adopted.stop()
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()
    assert remote.status()["state"] == "unreachable"


# ── the server: one port, the modes behind it ────────────────────────────

def test_server_both_modes(tmp_path, home, fake_tsqd):
    from testsuit.app.server import create_app

    folder = tmp_path / "tests"
    (folder / "scenario").mkdir(parents=True)
    (tmp_path / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    toml = tmp_path / "testsuit.toml"
    tsq_port = free_port()
    toml.write_text(textwrap.dedent(f'''
        modes = ["sequencer", "exploit"]
        title = "Rail lab"
        logo = "logo.svg"
        [exploit]
        folder = "tests"
        [sequencer]
        listen = "127.0.0.1:{tsq_port}"
    '''))
    config = load_config(toml)
    tsqd = tsqd_of(config, fake_tsqd, home / "tsqd.log")
    tsqd.start()
    stopped = []
    app = create_app(config, tsqd=tsqd, stop=lambda: stopped.append(1), plugins=False)
    port = free_port()
    server = serve(app, port)
    url = f"http://127.0.0.1:{port}"
    try:
        wait_for(lambda: tsqd.state == "running")
        info = requests.get(f"{url}/app/info").json()
        assert info["app"] == "testsuit" and info["title"] == "Rail lab" and info["logo"] == "app/logo.svg"
        assert info["modes"] == info["available"] == ["sequencer", "exploit"] and info["local"] and info["stoppable"]
        assert info["sequencer"]["state"] == "running" and info["config"] == str(toml)
        assert requests.get(f"{url}/app/logo.svg").status_code == 200
        assert {r["key"] for r in requests.get(f"{url}/app/config").json()} >= {"modes", "exploit.folder"}
        assert isinstance(requests.get(f"{url}/app/plugins").json(), list)
        # the exploit mode, under /x, on its folder
        assert requests.get(f"{url}/x/mode").json()["folder"] == str(folder)
        assert requests.get(f"{url}/x/criteria").status_code == 200
        # the sequencer's API, passed through: query, body and headers; an event stream as it comes
        echo = requests.post(f"{url}/seq/echo?a=1&b=two", json={"x": 1}).json()
        assert echo == {"body": '{"x": 1}', "query": "a=1&b=two", "type": "application/json"}
        assert requests.get(f"{url}/seq/health").json()["ok"] is True
        with requests.get(f"{url}/seq/events", stream=True, timeout=10) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            lines = r.iter_lines()
            assert next(lines) == b": connected"
            assert b"event: run" in [next(lines) for _ in range(3)]
        assert "fake tsqd on" in requests.get(f"{url}/app/sequencer/log").json()["text"]
        # the UI is served (when built), and the root leads to it
        assert requests.get(f"{url}/", allow_redirects=False).headers["location"] == "ui/"
        # tsqd gone: a clear 503
        tsqd.stop()
        r = requests.get(f"{url}/seq/health")
        assert r.status_code == 503 and "does not answer" in r.json()["detail"]
        assert requests.post(f"{url}/app/stop").json() == {"stopping": True} and stopped == [1]
    finally:
        tsqd.stop()
        server.should_exit = True
        server.thread.join(10)


def test_server_exploit_only_and_local_only_routes(tmp_path, home):
    from fastapi.testclient import TestClient

    from testsuit.app.server import create_app, is_local
    app = create_app(AppConfig(), plugins=False)
    with TestClient(app) as client:
        info = client.get("/app/info").json()
        assert info["modes"] == ["exploit"] and info["sequencer"] is None and info["stoppable"] is False
        assert client.get("/x/mode").json()["folder"] is None          # nothing remembered: the folder picker
        assert client.get("/seq/health").status_code == 404
        assert client.post("/app/stop").status_code == 400
        assert client.get("/app/sequencer/log").status_code == 404
        # through a reverse proxy on this machine: not local
        assert client.get("/x/mode", headers={"X-Forwarded-For": "10.1.2.3"}).status_code == 403
        assert client.get("/app/info", headers={"Forwarded": "for=10.1.2.3"}).json()["available"] == []
    # the exploit mode and stop answer only this machine
    assert is_local("127.0.0.1") and is_local("::1") and not is_local("10.1.2.3") and not is_local(None)
    with TestClient(app, client=("10.1.2.3", 5000)) as client:
        assert client.get("/x/mode").status_code == 403 and client.post("/app/stop").status_code == 403
        info = client.get("/app/info").json()
        assert info["available"] == [] and info["local"] is False


# ── the testsuit command ─────────────────────────────────────────────────

def test_command_start_status_stop(tmp_path, home, fake_tsqd, monkeypatch, capsys):
    from testsuit.app import supervisor
    from testsuit.misc import browser

    opened = []
    monkeypatch.setattr(browser, "uses_desktop_openers", lambda: False)
    monkeypatch.setattr(browser.webbrowser, "open", lambda u: opened.append(u) or True)
    monkeypatch.setattr(supervisor, "tsqd_command", lambda: fake_tsqd)
    port, tsq_port = free_port(), free_port()
    (tmp_path / "testsuit.toml").write_text(textwrap.dedent(f'''
        modes = ["exploit", "sequencer"]
        [ui]
        listen = "127.0.0.1:{port}"
        [sequencer]
        listen = "127.0.0.1:{tsq_port}"
    '''))
    (tmp_path / "work").mkdir()
    assert cli.main(["status"]) == 1
    assert "not running" in capsys.readouterr().out
    assert cli.main(["--folder", str(tmp_path / "nope")]) == 2
    result = []
    thread = threading.Thread(target=lambda: result.append(cli.main(["start", "--folder", str(tmp_path / "work")])), daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    wait_for(lambda: cli.running_app(url) and cli.running_app(url)["sequencer"]["state"] == "running", 60)
    wait_for(lambda: opened)
    assert opened == [f"{url}/ui/"]
    assert requests.get(f"{url}/x/mode").json()["folder"] == str(tmp_path / "work")

    capsys.readouterr()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert f"running at {url}/ui/" in out and "exploit, sequencer" in out and "tsqd running, pid" in out
    tsqd_pid = cli.running_app(url)["sequencer"]["pid"]

    # launched again (a second click on the shortcut): the running app opens, on the folder asked for
    assert cli.main(["--folder", str(tmp_path)]) == 0
    assert opened[-1] == f"{url}/ui/" and requests.get(f"{url}/x/mode").json()["folder"] == str(tmp_path)

    assert cli.main(["stop"]) == 0
    thread.join(30)
    assert result == [0] and cli.running_app(url) is None
    with pytest.raises(OSError):        # its tsqd stopped with it
        wait_for(lambda: os.kill(tsqd_pid, 0), 5)
    text = cli.log_file().read_text()
    assert "launch: testsuit start --folder" in text and "listening (+" in text and "stopped" in text
    assert cli.main(["stop"]) == 1


def test_command_errors(tmp_path, home, capsys):
    (tmp_path / "testsuit.toml").write_text('modes = ["exploit"]\n[ui]\nlisten = "x"\n')
    assert cli.main(["--no-browser"]) == 2
    assert "invalid configuration" in capsys.readouterr().err
    (tmp_path / "testsuit.toml").write_text('modes = ["sequencer"]\n')
    assert cli.main(["--folder", str(tmp_path)]) == 2
    assert "exploit mode, which is off" in capsys.readouterr().err
