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

"""The web server of the ``testsuit`` app: one port, one web UI, the modes of testsuit.toml behind it.

- ``/ui/``: the web UI (built from the repository's ui/ folder), which shows the tabs of the modes on;
- ``/app/...``: the app itself: its modes and their state (``/app/info``), its settings, its logo, stop;
- ``/x/...``: the exploit mode (testsuit.exploit.editor.standalone), in this process;
- ``/seq/...``: the sequencer mode: the REST API of tsqd, passed through to the tsqd testsuit started
  (testsuit.app.supervisor), or to the one of ``sequencer.url``.

The exploit mode runs the user's own code and reads any file of the machine: it answers only requests
from this machine, whatever ``ui.listen`` (stopping the app too); a request through a reverse proxy
(X-Forwarded-For, Forwarded, X-Real-IP) never counts as one, unless ``exploit.remote``. Every URL of the UI
is relative: behind a reverse proxy, the app works under a sub-path the proxy strips, or one it passes on
(``ui.base_path``). Live updates are Server-Sent Events (``/seq/events``, sent with ``X-Accel-Buffering: no``),
no websocket.
"""
from __future__ import annotations

import contextlib
import ipaddress
import sys
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from testsuit.app.config import AppConfig

UI_DIR = Path(__file__).resolve().parent / "ui"
DEFAULT_LOGO = Path(__file__).resolve().parent / "logo.svg"     # testsuit's icon, when testsuit.toml names no logo

# not passed through the proxy (RFC 9110, section 7.6.1), and those the server sets itself
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
               "transfer-encoding", "upgrade", "host"}


def is_local(host: str | None) -> bool:
    if not host:
        return False
    if host in ("localhost", "testclient"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def route_path(scope) -> str:
    """The path of a request under the base path (ui.base_path, ASGI root_path)."""
    path, root = scope["path"], scope.get("root_path", "")
    return path[len(root):] or "/" if root and (path == root or path.startswith(root + "/")) else path


class BasePath:
    """Serves an app under a base path (ui.base_path), for a reverse proxy that passes its sub-path on:
    /testsuit/ui/ is the UI, as /ui/ is without it (the ASGI root_path, the paths kept whole). Anything
    outside it is redirected to the UI."""

    def __init__(self, app, base: str) -> None:
        self.app, self.base = app, base
        self.state = app.state

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope["path"]
            if path == self.base and scope["type"] == "http":
                return await RedirectResponse(f"{self.base}/ui/")(scope, receive, send)
            if path.startswith(self.base + "/"):
                scope = {**scope, "root_path": scope.get("root_path", "") + self.base}
            elif scope["type"] == "http":
                response = RedirectResponse(f"{self.base}/ui/")
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


def is_local_request(request) -> bool:
    """Whether a request comes from this machine itself: not through a reverse proxy (which, on this machine,
    connects from 127.0.0.1 on behalf of any browser), so a forwarded request never counts as local."""
    headers = request.headers
    if any(h in headers for h in ("x-forwarded-for", "forwarded", "x-real-ip")):
        return False
    return is_local(request.client.host if request.client else None)


def create_app(config: AppConfig, tsqd=None, stop: Callable[[], None] | None = None,
               folder: str | Path | None = None, log: Path | None = None, plugins: bool = True):
    """:param tsqd: the Tsqd of the sequencer mode (testsuit.app.supervisor), None when it is off
    :param stop: stops the server (POST /app/stop: the Stop button, ``testsuit stop``)
    :param folder: the folder of the exploit mode (None: exploit.folder, else the last one, else the picker)
    :param log: the launch log of the app"""
    import httpx
    from starlette.background import BackgroundTask

    from testsuit import __version__

    exploit_app = None
    if config.on("exploit"):
        from testsuit.exploit.editor import standalone
        if folder is None:
            folder = config.path(config.exploit.folder) or standalone.last_folder()
        exploit_app = standalone.create_app(folder, plugins=plugins, results=config.path(config.exploit.results),
                                            remember=True, data_roots=sequencer_data_roots(config, tsqd), log=log)

    client: httpx.AsyncClient | None = None
    streams: set = set()     # the responses passed through and still open (the event streams)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        nonlocal client
        client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0))
        try:
            yield
        finally:
            await close_streams()
            await client.aclose()
            if exploit_app is not None:
                exploit_app.state.workspace.close()

    async def close_streams() -> None:
        for response in list(streams):
            with contextlib.suppress(Exception):
                await response.aclose()
        streams.clear()

    app = FastAPI(title="testsuit", version=__version__, lifespan=lifespan)
    app.state.config = config
    app.state.tsqd = tsqd
    app.state.exploit = exploit_app
    app.state.close_streams = close_streams

    remote = config.exploit.remote

    def allowed(request) -> bool:
        return remote or is_local_request(request)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # the exploit mode runs the user's code and reads any file: only from this machine (unless exploit.remote)
        path = route_path(request.scope)
        if (path.startswith("/x/") or path == "/app/stop") and not allowed(request):
            return JSONResponse({"detail": "only from the machine testsuit runs on"}, status_code=403)
        return await call_next(request)

    # ── the app ────────────────────────────────────────────────────────

    def logo_file() -> Path | None:
        # logo of testsuit.toml, else testsuit's own icon
        return config.logo_file if config.logo else DEFAULT_LOGO

    def logo_url() -> str | None:
        if config.logo and config.logo.startswith(("http://", "https://")):
            return config.logo
        file = logo_file()
        return f"app/logo{file.suffix}" if file and file.is_file() else None

    @app.get("/app/info")
    async def info(request: Request):
        """What the UI shows: the title and logo, the modes on (those this viewer can use: the exploit mode
        only from this machine), and the state of the sequencer's tsqd."""
        import asyncio
        local = allowed(request)
        sequencer = await asyncio.to_thread(tsqd.status) if tsqd is not None else None
        return {"app": "testsuit", "testsuit": __version__, "title": config.title, "logo": logo_url(),
                "modes": config.modes, "available": [m for m in config.modes if m != "exploit" or local],
                "local": local, "stoppable": stop is not None and local, "config": str(config.file) if config.file else None,
                "url": config.public_url, "sequencer": sequencer}

    @app.get("/app/config")
    async def settings():
        """testsuit.toml: every setting, its value and where it comes from."""
        return [{"key": k, "value": v if isinstance(v, (int, float, str, bool, list, type(None))) else str(v),
                 "source": s} for k, v, s in config.describe()]

    @app.get("/app/plugins")
    async def plugins_():
        """The testsuit plugins of this process (those of tsqd are at /seq/plugins)."""
        from testsuit.plugins import plugins_report
        return plugins_report()

    @app.get("/app/about")
    async def about():
        import platform
        return {"testsuit": __version__, "python": f"{platform.python_version()} ({sys.executable})",
                "config": str(config.file) if config.file else None, "log": str(log) if log else None}

    @app.get("/app/logo{suffix}", include_in_schema=False)
    async def logo(suffix: str):
        """The UI's logo image (logo of testsuit.toml), when it is a file."""
        file = logo_file()
        if file is None or not file.is_file():
            raise HTTPException(404, "no logo file (logo in testsuit.toml)")
        return FileResponse(file)

    @app.post("/app/stop")
    async def stop_server():
        """Stop testsuit: the tsqd it started, and the runs going on, stop too."""
        if stop is None:
            raise HTTPException(400, "this server cannot be stopped from the UI")
        stop()
        return {"stopping": True}

    @app.get("/app/sequencer/log")
    async def sequencer_log(lines: int = 300):
        """The end of what the tsqd testsuit started printed."""
        if tsqd is None:
            raise HTTPException(404, "the sequencer mode is off")
        return {"text": tsqd.log_tail(min(max(lines, 1), 5000)), "file": str(tsqd.log)}

    @app.post("/app/sequencer/restart")
    async def sequencer_restart():
        """Stop the tsqd testsuit started and start it again."""
        import asyncio
        if tsqd is None:
            raise HTTPException(404, "the sequencer mode is off")
        try:
            await close_streams()
            await asyncio.to_thread(tsqd.restart_now)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return await asyncio.to_thread(tsqd.status)

    # ── the modes ──────────────────────────────────────────────────────

    if exploit_app is not None:
        app.mount("/x", exploit_app)

    if tsqd is not None:
        @app.api_route("/seq/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
                       include_in_schema=False)
        async def sequencer(path: str, request: Request):
            """The REST API of tsqd, passed through (event streams and downloads streamed as they come)."""
            url = httpx.URL(f"{tsqd.url}/{path}", query=request.url.query.encode())
            headers = [(k, v) for k, v in request.headers.items() if k.lower() not in HOP_HEADERS]
            body = request.stream() if request.method not in ("GET", "HEAD") else None
            upstream = client.build_request(request.method, url, headers=headers, content=body)
            try:
                response = await client.send(upstream, stream=True)
            except httpx.HTTPError as e:
                state = tsqd.status()
                raise HTTPException(503, f"the sequencer (tsqd at {tsqd.url}) does not answer: {state['state']}"
                                         f"{', ' + state['detail'] if state['detail'] else ''} ({type(e).__name__})") from e
            streams.add(response)

            async def body_of():
                try:
                    async for chunk in response.aiter_raw():
                        yield chunk
                except (httpx.HTTPError, RuntimeError):
                    return      # tsqd stopped, or the stream was closed as testsuit stops

            async def done():
                streams.discard(response)
                await response.aclose()

            out_headers = {k: v for k, v in response.headers.items()
                           if k.lower() not in HOP_HEADERS and k.lower() != "content-length"}
            if response.headers.get("content-type", "").startswith("text/event-stream"):
                out_headers["x-accel-buffering"] = "no"      # nginx: the events as they come, not buffered
            if "content-length" in response.headers and "content-encoding" not in response.headers:
                out_headers["content-length"] = response.headers["content-length"]
            return StreamingResponse(body_of(), status_code=response.status_code, headers=out_headers,
                                     background=BackgroundTask(done))

    # ── the web UI ─────────────────────────────────────────────────────

    if (UI_DIR / "index.html").is_file():
        from fastapi.staticfiles import StaticFiles
        app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")
    else:
        @app.get("/ui/", include_in_schema=False)
        async def no_ui():
            return HTMLResponse("<h1>testsuit</h1><p>The web UI is not built in this installation: "
                                "<code>cd ui && npm ci && npm run build</code> in the testsuit repository, then "
                                "reinstall. The REST API is at <a href='../docs'>/docs</a>.</p>")

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("ui/")      # relative: right behind a reverse proxy that strips its sub-path too

    return BasePath(app, config.ui.base_path) if config.ui.base_path else app


def sequencer_data_roots(config: AppConfig, tsqd) -> list[tuple[str, Path]]:
    """The data of the sequencer's runs, for the exploit mode's pickers: the runs folder of the tsqd that
    testsuit starts (on this machine). Nothing for a tsqd running elsewhere."""
    if tsqd is None or not config.on("sequencer") or config.sequencer.url:
        return []
    try:
        from testsuit.sequencer.config import load_config
        runs = load_config(config.path(config.sequencer.config)).storage_path("runs")
    except Exception:   # tsq.yml not valid: tsqd says so in its log
        return []
    return [("sequencer runs", runs)]
