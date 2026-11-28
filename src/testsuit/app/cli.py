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

"""``testsuit``: start the testsuit app (one web UI on one port, the modes of testsuit.toml behind it),
``testsuit status``, ``testsuit stop``.

Launched again while it runs (a second click on the shortcut), it opens the browser on the running app
instead of failing on its port. What each launch did is in ``testsuit.log``, in the user's testsuit
configuration folder: read it when a launch from a shortcut, which shows no console, seems to do nothing.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

LOADED = time.monotonic()   # imported before the heavy imports: the launch times count from here


# ── the launch log ───────────────────────────────────────────────────────

def log_file() -> Path:
    from testsuit.app.config import user_config_dir
    return user_config_dir() / "testsuit.log"


def log(message: str) -> None:
    """Best effort: a line in log_file() (cut to its last half beyond 256 kB)."""
    import datetime
    path = log_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > 256_000:
            text = path.read_text(encoding="utf-8", errors="replace")
            path.write_text(text[len(text) // 2:], encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} [{os.getpid()}] {message}\n")
    except OSError:
        pass


def say(message: str, error: bool = False) -> None:
    """Print a line for the user, and log it: a process launched without a console (a shortcut) may have no
    stdout at all, or one that cannot take the text, and must go on all the same."""
    log(message)
    stream = sys.stderr if error else sys.stdout
    try:
        if stream is not None:
            print(message, file=stream, flush=True)
    except (OSError, ValueError, UnicodeError):
        pass


def open_browser(url: str) -> bool:
    """Open the url in the user's browser (testsuit.misc.browser). Logged; when no browser opens, says to
    open the url by hand. Never raises."""
    from testsuit.misc.browser import open_url
    try:
        opened, notes = open_url(url)
    except Exception as e:  # never: open_url catches what it runs; the log says so all the same
        opened, notes = False, [repr(e)]
    for note in notes:
        log(f"browser on {url}: {note}")
    if opened:
        log(f"browser opened on {url}")
    else:
        say(f"no browser could be opened here: open {url} in yours")
    return opened


def running_app(url: str) -> dict | None:
    """The /app/info of a testsuit app answering at url (None: none there)."""
    import requests
    try:
        info = requests.get(f"{url}/app/info", timeout=2).json()
    except (requests.RequestException, ValueError):
        return None
    return info if isinstance(info, dict) and info.get("app") == "testsuit" else None


# ── testsuit (start) ─────────────────────────────────────────────────────

def start(args) -> int:
    from testsuit.app.config import ConfigError, load_config, user_config_dir

    def step(what: str) -> None:
        log(f"{what} (+{time.monotonic() - LOADED:.1f} s)")

    try:
        config = load_config(args.config)
    except ConfigError as e:
        say(f"testsuit: {e}", error=True)
        return 2
    if args.listen:
        config.ui.listen = args.listen
    step(f"configuration {config.file or 'none (defaults)'}: modes {', '.join(config.modes)}, listen {config.ui.listen}")
    browser = config.ui.browser and not args.no_browser
    folder = None
    if args.folder is not None:
        if not config.on("exploit"):
            say("testsuit: --folder is for the exploit mode, which is off in testsuit.toml", error=True)
            return 2
        folder = Path(args.folder).expanduser().resolve()
        if not folder.is_dir():
            say(f"testsuit: no folder '{args.folder}'", error=True)
            return 2

    url = config.local_url
    running = running_app(url)
    if running is not None:
        # launched again (a second click on the shortcut): the app running already is the one to open
        if folder is not None and config.on("exploit"):
            import requests
            try:
                requests.post(f"{url}/x/folder", json={"path": str(folder)}, timeout=10).raise_for_status()
            except requests.RequestException as e:
                say(f"testsuit: the running app could not open '{folder}': {e}", error=True)
        say(f"testsuit already running: {url}/ui/")
        if browser:
            open_browser(f"{url}/ui/")
        return 0

    try:
        import uvicorn
    except ImportError:
        say("testsuit needs FastAPI and uvicorn: pip install testsuit", error=True)
        return 2
    if config.on("sequencer"):
        try:
            import asyncssh  # noqa: F401  (the sequencer's extra)
        except ImportError:
            say("the sequencer mode needs the sequencer extra: pip install 'testsuit[sequencer]'", error=True)
            return 2

    from testsuit.plugins import load_plugins
    plugins = load_plugins()
    step(f"plugins loaded: {', '.join(plugins) or 'none'}")

    tsqd = None
    if config.on("sequencer"):
        from testsuit.app.supervisor import Tsqd
        seq = config.sequencer
        tsqd = Tsqd(config.sequencer_url, None if seq.url else seq.listen, config.path(seq.config),
                    user_config_dir() / "tsqd.log", restart=seq.restart)
        tsqd.start()
        step(f"sequencer: {'tsqd at ' + seq.url if seq.url else 'tsqd started on ' + seq.listen} ({tsqd.state})")

    from testsuit.app.server import create_app
    server: uvicorn.Server | None = None

    def stop() -> None:
        if server is not None:
            server.should_exit = True

    app = create_app(config, tsqd=tsqd, stop=stop, folder=folder, log=log_file())
    if config.on("exploit"):
        current = app.state.exploit.state.workspace.folder
        step(f"exploit mode: folder {current or 'none, the folder picker'}")
    host, port = config.listen

    class Server(uvicorn.Server):
        async def shutdown(self, sockets=None):
            # the event streams passed through from tsqd never end by themselves: closed first
            await app.state.close_streams()
            await super().shutdown(sockets)

    # behind a reverse proxy: the client address and scheme of its X-Forwarded-For and X-Forwarded-Proto, when it
    # is one of ui.trusted_proxies
    proxies = config.ui.trusted_proxies
    server = Server(uvicorn.Config(app, host=host, port=port, log_level="warning", timeout_graceful_shutdown=10,
                                   proxy_headers=bool(proxies), forwarded_allow_ips=",".join(proxies) or None))

    def announce() -> None:
        try:
            while not server.started:
                if server.should_exit:
                    return
                time.sleep(0.1)
            step("listening")
            public = f"  (behind its reverse proxy: {config.public_url}/ui/)" if config.ui.public_url else ""
            say(f"testsuit ({', '.join(config.modes)}): {url}/ui/{public}  (Ctrl+C or 'testsuit stop' to stop)")
            if browser:
                open_browser(f"{url}/ui/")
        except Exception as e:  # the server runs on: the log tells what went wrong
            log(f"after the start: {e!r}")
    threading.Thread(target=announce, daemon=True).start()
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    except BaseException as e:
        # SystemExit(1) at the start: the port is taken (uvicorn says so on stderr)
        log(f"stopped by {e!r}")
        if tsqd is not None:
            tsqd.stop()
        raise
    finally:
        if tsqd is not None and tsqd.managed:
            say("stopping tsqd…")
            tsqd.stop()
    log("stopped")
    return 0


# ── testsuit status, testsuit stop ───────────────────────────────────────

def _config_url(args) -> str | None:
    from testsuit.app.config import ConfigError, load_config
    try:
        config = load_config(args.config)
    except ConfigError as e:
        say(f"testsuit: {e}", error=True)
        return None
    if args.listen:
        config.ui.listen = args.listen
    return config.local_url


def status(args) -> int:
    import json
    url = _config_url(args)
    if url is None:
        return 2
    info = running_app(url)
    if args.json:
        print(json.dumps(info, indent=1))
        return 0 if info else 1
    if info is None:
        print(f"testsuit is not running at {url}")
        return 1
    print(f"testsuit {info['testsuit']} running at {url}/ui/: {info['title']}")
    if info.get("url") and info["url"] != url:
        print(f"  public URL     {info['url']}/ui/")
    print(f"  configuration  {info.get('config') or 'none (defaults)'}")
    print(f"  modes          {', '.join(info['modes'])}")
    s = info.get("sequencer")
    if s:
        what = f"tsqd {s['state']}"
        what += f", pid {s['pid']}" if s.get("pid") else ""
        what += f", {s['restarts']} restart(s)" if s.get("restarts") else ""
        what += f" ({s['detail']})" if s.get("detail") and not s["detail"].startswith("pid ") else ""
        print(f"  sequencer      {what}, {s['url']}{', log ' + s['log'] if s.get('log') else ''}")
    return 0


def stop(args) -> int:
    import requests
    url = _config_url(args)
    if url is None:
        return 2
    if running_app(url) is None:
        print(f"testsuit is not running at {url}")
        return 1
    try:
        requests.post(f"{url}/app/stop", timeout=10).raise_for_status()
    except requests.RequestException as e:
        say(f"testsuit stop: {e}", error=True)
        return 2
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if running_app(url) is None:
            print("testsuit stopped")
            return 0
        time.sleep(0.5)
    say("testsuit is still stopping (runs going on are killed and their results packed): see 'testsuit status'",
        error=True)
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    log(f"launch: testsuit {' '.join(argv)} (cwd {Path.cwd()}, stdout {'none' if sys.stdout is None else 'ok'})")
    parser = argparse.ArgumentParser(prog="testsuit", description=
        "The testsuit app: one web UI on one port, with the modes of testsuit.toml. The exploit mode: the "
        "criteria editor, exploit_runner runs and their reports, Plot and Extract, on a folder of this machine. "
        "The sequencer mode: the test sequencer (tsqd, started and supervised by testsuit): runs, datapacks, "
        "the infrastructure.")
    # the options may come before or after the subcommand: SUPPRESS keeps a subcommand from overwriting them
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS,
                        help="testsuit.toml (default: $TESTSUIT_CONFIG, ./testsuit.toml, then the one of the user's "
                             "testsuit configuration folder; none: the exploit mode alone)")
    common.add_argument("--listen", default=argparse.SUPPRESS, help="host:port of the web UI, instead of ui.listen")
    starting = argparse.ArgumentParser(add_help=False)
    starting.add_argument("--folder", default=argparse.SUPPRESS,
                          help="the folder of the exploit mode, instead of exploit.folder or the folder opened last")
    starting.add_argument("--no-browser", action="store_true", default=argparse.SUPPRESS,
                          help="do not open the browser")
    for group in (common, starting):
        for action in group._actions:
            parser._add_action(action)
    sub = parser.add_subparsers(dest="command", metavar="{start,status,stop}")
    sub.add_parser("start", parents=[common, starting], help="start the app (the default)")
    s = sub.add_parser("status", parents=[common], help="whether the app runs, its modes and the state of its tsqd")
    s.add_argument("--json", action="store_true", help="print /app/info as JSON")
    sub.add_parser("stop", parents=[common], help="stop the app (and the tsqd it started)")
    args = parser.parse_args(argv)
    for name, default in (("config", None), ("listen", None), ("folder", None), ("no_browser", False)):
        if not hasattr(args, name):
            setattr(args, name, default)
    if args.command == "status":
        return status(args)
    if args.command == "stop":
        return stop(args)
    return start(args)


if __name__ == "__main__":
    sys.exit(main())
