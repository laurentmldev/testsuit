"""REST endpoints of the Editor mode (README section 13.2), added when ``server.editor`` is on.

Git credentials sent to ``push`` and ``rebase`` are used for that request only: they go to the
git process environment, never to a file, the logs or a response.
"""
from __future__ import annotations

import asyncio
import socket
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from testsuit.sequencer.api import zip_entry
from testsuit.sequencer.editor import Editor
from testsuit.sequencer.editor_schemas import schemas
from testsuit.sequencer.util import SequencerError


def add_editor_routes(app: FastAPI, seq) -> Editor:
    editor = Editor(seq)
    app.state.editor = editor

    def session(session_id: str):
        try:
            return editor.get(session_id)
        except SequencerError as e:
            raise HTTPException(404, str(e)) from e

    def run(fn, *args, **kwargs):
        return asyncio.to_thread(fn, *args, **kwargs)

    @app.get("/edit")
    async def edit_home():
        return {"enabled": True, "repositories": await run(editor.repositories),
                "sessions": [editor.describe(s) for s in editor.sessions.values()],
                "published": editor.published}

    @app.post("/edit/sessions", status_code=201)
    async def open_session(body: dict):
        s = await run(editor.open, str(body.get("repo") or "lab"), body.get("branch"))
        return editor.describe(s)

    @app.get("/edit/sessions/{sid}")
    async def get_session(sid: str):
        return await run(editor.describe, session(sid))

    @app.delete("/edit/sessions/{sid}")
    async def close_session(sid: str):
        session(sid)
        await run(editor.close, sid)
        return {"closed": sid}

    @app.get("/edit/sessions/{sid}/files")
    async def tree(sid: str):
        return await run(editor.tree, session(sid))

    @app.get("/edit/sessions/{sid}/files/{path:path}")
    async def read(sid: str, path: str):
        return await run(editor.read, session(sid), path)

    @app.put("/edit/sessions/{sid}/files/{path:path}")
    async def write(sid: str, path: str, body: dict):
        """{content: text} replaces the file; {data, at: [path]} merges data onto it, keeping comments."""
        if "content" not in body and "data" not in body:
            raise HTTPException(400, "give 'content' (text) or 'data' (merged, at the optional path 'at')")
        return await run(editor.write, session(sid), path, body.get("content"), body.get("data"), body.get("at"))

    @app.delete("/edit/sessions/{sid}/files/{path:path}")
    async def delete(sid: str, path: str):
        await run(editor.delete, session(sid), path)
        return {"deleted": path}

    @app.post("/edit/sessions/{sid}/rename")
    async def rename(sid: str, body: dict):
        await run(editor.rename, session(sid), str(body["from"]), str(body["to"]))
        return {"renamed": body["from"], "to": body["to"]}

    @app.get("/edit/sessions/{sid}/context")
    async def context(sid: str):
        return await run(editor.context, session(sid))

    @app.get("/edit/sessions/{sid}/schemas")
    async def session_schemas(sid: str):
        return schemas(await run(editor.context, session(sid)))

    @app.post("/edit/sessions/{sid}/validate")
    async def validate(sid: str, body: dict | None = None):
        s = session(sid)
        if body and body.get("path"):
            problems = await run(editor.check_file, s, str(body["path"]))
            return {"ok": not any(p["severity"] == "error" for p in problems), "problems": problems}
        return await run(editor.validate, s)

    @app.post("/edit/sessions/{sid}/flatten")
    async def flatten(sid: str, body: dict):
        return await run(editor.flatten, session(sid), str(body["testdef"]), body.get("setup_config"))

    @app.post("/edit/sessions/{sid}/palette")
    async def palette(sid: str, body: dict):
        return await run(editor.palette, session(sid), str(body["testdef"]), body.get("setup_config"))

    @app.post("/edit/sessions/{sid}/keys")
    async def keys(sid: str, body: dict):
        return await run(editor.keys, session(sid), str(body["testdef"]), body.get("setup_config"))

    @app.post("/edit/sessions/{sid}/preview")
    async def preview(sid: str, body: dict):
        return await run(editor.preview, session(sid), str(body["testdef"]), str(body["source"]), body.get("component"),
                         body.get("setup_config"))

    @app.post("/edit/sessions/{sid}/datapack")
    async def generate(sid: str, body: dict):
        return await run(editor.generate, session(sid), str(body["testdef"]), body.get("setup_config"),
                         bool(body.get("nocheck", True)))

    @app.get("/edit/sessions/{sid}/datapack/{name}")
    async def download(sid: str, name: str):
        path = editor.generated(session(sid), name)
        return FileResponse(path, media_type="application/zip", filename=path.name)

    @app.get("/edit/sessions/{sid}/datapack/{name}/files/{path:path}")
    async def datapack_file(sid: str, name: str, path: str):
        return zip_entry(editor.generated(session(sid), name), path)

    @app.post("/edit/sessions/{sid}/datapack/{name}/submit", status_code=201)
    async def submit(sid: str, name: str, body: dict | None = None):
        priority = (body or {}).get("priority")
        return await run(seq.submit, editor.generated(session(sid), name), int(priority) if priority is not None else None)

    @app.get("/edit/sessions/{sid}/diff")
    async def diff(sid: str):
        return await run(editor.diff, session(sid))

    def credentials(body: dict) -> dict:
        return {k: body.get(k) for k in ("username", "password", "passphrase") if body.get(k)}

    @app.post("/edit/sessions/{sid}/push")
    async def push(sid: str, body: dict):
        return await run(editor.push, session(sid), str(body.get("message") or ""), str(body.get("author_name") or ""),
                         str(body.get("author_email") or ""), body.get("tag") or None, credentials(body))

    @app.post("/edit/sessions/{sid}/rebase")
    async def rebase(sid: str, body: dict | None = None):
        return await run(editor.rebase, session(sid), credentials(body or {}))

    @app.post("/edit/reachability")
    async def reachability(body: dict):
        """Whether a host answers: local is always reachable, SSH needs its port open (a TCP connection)."""
        if body.get("transport") == "local":
            return {"ok": True, "detail": "local transport: commands run on the sequencer host"}
        host, port = str(body.get("host") or ""), int(body.get("port") or 22)
        if not host:
            raise HTTPException(400, "give a 'host'")
        start = time.monotonic()
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 3)
            writer.close()
            return {"ok": True, "detail": f"{host}:{port} answers ({(time.monotonic() - start) * 1000:.0f} ms)"}
        except (OSError, asyncio.TimeoutError, socket.gaierror) as e:
            return {"ok": False, "detail": f"{host}:{port}: {e or 'timeout'}"}

    @app.get("/edit/schemas")
    async def lab_schemas():
        lab = {"lab": Editor.describe_lab(seq.lab)}
        return schemas(lab)

    return editor
