"""REST API of the sequencer (README section 13), FastAPI. ``tsq`` and the UI are its clients.

Files are served from the run folder while it exists (queued, running, and done until the
retention period), from the results zip after that. ``FileResponse`` answers HTTP range requests.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

from testsuit.sequencer.sequencer import Sequencer
from testsuit.sequencer.util import SequencerError

STEP_FILES = ("datapack.yml", "context.yml", "baseline.yml", "run.yml", "selftest.yml")


def zip_entry(zip_path: Path, path: str):
    archive = zipfile.ZipFile(zip_path)
    root = archive.namelist()[0].split("/")[0]
    prefix = f"{root}/{path}".rstrip("/")
    names = archive.namelist()
    if prefix in names and not prefix.endswith("/"):
        info = archive.getinfo(prefix)

        def stream():
            with archive, archive.open(info) as f:
                while chunk := f.read(1 << 20):
                    yield chunk
        return StreamingResponse(stream(), media_type=mimetypes.guess_type(prefix)[0] or "application/octet-stream",
                                 headers={"Content-Length": str(info.file_size)})
    children = {}
    for name in names:
        if name.startswith(prefix + "/") and name != prefix + "/":
            rest = name[len(prefix) + 1:]
            head = rest.split("/")[0]
            is_dir = "/" in rest
            rel = f"{path.strip('/')}/{head}".strip("/")
            if head and head not in children:
                children[head] = {"name": head, "path": rel, "dir": is_dir,
                                  "size": None if is_dir else archive.getinfo(name).file_size}
    archive.close()
    if not children and prefix + "/" not in names and path:
        raise HTTPException(404, f"no '{path}' in {zip_path.name}")
    return sorted(children.values(), key=lambda c: c["name"])


def create_app(seq: Sequencer, lifespan=None) -> FastAPI:
    app = FastAPI(title="testsuit sequencer", version="1", lifespan=lifespan)
    app.state.seq = seq

    @app.exception_handler(SequencerError)
    async def sequencer_error(request: Request, exc: SequencerError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    def run_or_404(run_id: str) -> dict:
        run = seq.store.get_run(run_id)
        if run is None:
            raise HTTPException(404, f"no run '{run_id}'")
        return run

    # ── runs ─────────────────────────────────────────────────────────────
    @app.post("/runs", status_code=201)
    async def submit(datapack: Annotated[UploadFile, File()], priority: Annotated[int | None, Form()] = None,
                     baseline: Annotated[UploadFile | None, File()] = None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / (Path(datapack.filename or "datapack.zip").name)
            with open(path, "wb") as f:
                while chunk := await datapack.read(1 << 20):
                    f.write(chunk)
            expected = yaml.safe_load(await baseline.read()) if baseline is not None else None
            return await asyncio.to_thread(seq.submit, path, priority, expected)

    @app.get("/runs")
    async def runs(state: str | None = None, limit: int = 200):
        return seq.store.runs(state.split(",") if state else None, limit)

    @app.get("/queue")
    async def queue():
        return seq.store.queue()

    @app.get("/runs/{run_id}")
    async def show(run_id: str):
        run = run_or_404(run_id)
        execution = seq.executions.get(run["id"])
        if execution is not None and execution.ctx is not None:
            run["steps"] = [r.as_dict() for r in execution.ctx.records.values()]
        return run

    @app.get("/runs/{run_id}/pipeline")
    async def pipeline(run_id: str):
        """The run's pipeline as it runs: phases, steps after sub-pipeline expansion, their needs (the UI graph)."""
        from testsuit.sequencer.intake import load_datapack
        run = run_or_404(run_id)
        if not Path(run["dir"]).is_dir():
            raise HTTPException(404, f"run {run['id']}: the run folder is gone (retention), only its records remain")
        dp = await asyncio.to_thread(load_datapack, Path(run["dir"]))
        phases = []
        for flat in dp.phases:
            steps = []
            for part, items in (("steps", flat.steps), ("finally", flat.finally_)):
                for fs in items:
                    s = fs.step
                    steps.append({"id": fs.id, "part": part, "needs": fs.needs, "group": fs.group, "action": s.action,
                                  "do": s.do, "component": s.component, "for_each": s.for_each,
                                  "if": s.if_, "label": s.do or s.run or s.uses or s.action})
            phases.append({"id": flat.phase.id, "needs": flat.phase.needs, "stages": flat.phase.stages,
                           "components": flat.phase.components, "groups": flat.groups, "steps": steps})
        return {"phases": phases, "stages": dp.needs.stages,
                "components": {c.id: {"type": c.type, "role": c.role, "stage": c.stage, "name": c.name}
                               for c in dp.needs.components.values()}}

    @app.patch("/runs/{run_id}")
    async def patch(run_id: str, body: dict):
        if "priority" not in body:
            raise HTTPException(400, "only 'priority' can change")
        return seq.set_priority(run_or_404(run_id)["id"], int(body["priority"]))

    @app.post("/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        return await seq.abort(run_or_404(run_id)["id"], "cancel")

    @app.post("/runs/{run_id}/kill")
    async def kill(run_id: str):
        return await seq.abort(run_or_404(run_id)["id"], "kill")

    @app.get("/runs/{run_id}/logs")
    async def logs(run_id: str):
        run = await show(run_id)
        return [{"step": f"{s['phase']}:{s['id']}" + (f"[{s['component']}]" if s["component"] else ""),
                 "state": s["state"], "log": s["log"]} for s in run.get("steps") or []]

    @app.get("/runs/{run_id}/logs/{step:path}")
    async def log(run_id: str, step: str, offset: int = 0):
        """A step's log ('<phase>:<step>[<component>]', or a unique prefix of it) from a byte offset."""
        run = await show(run_id)
        matches = [s for s in await logs(run_id) if s["step"] == step] or \
                  [s for s in await logs(run_id) if s["step"].startswith(step)]
        if len(matches) != 1:
            raise HTTPException(404, f"no single step '{step}' in run {run['id']}"
                                + (f" ({', '.join(m['step'] for m in matches)})" if matches else ""))
        data = read_file(run, matches[0]["log"])
        return PlainTextResponse(data[offset:].decode("utf-8", "replace"), headers={"X-Log-Size": str(len(data))})

    def read_file(run: dict, rel: str) -> bytes:
        folder = Path(run["dir"])
        path = (folder / rel).resolve()
        if folder.is_dir():
            if not path.is_relative_to(folder.resolve()) or not path.is_file():
                return b""
            return path.read_bytes()
        if run.get("results") and Path(run["results"]).is_file():
            with zipfile.ZipFile(run["results"]) as z:
                root = z.namelist()[0].split("/")[0]
                try:
                    return z.read(f"{root}/{rel}")
                except KeyError:
                    return b""
        return b""

    @app.get("/runs/{run_id}/files")
    @app.get("/runs/{run_id}/files/{path:path}")
    async def files(run_id: str, path: str = "", results: bool = False):
        """List a folder or download a file of the run: run folder, or results zip (results=true, or once
        the run folder is gone)."""
        run = run_or_404(run_id)
        folder = Path(run["dir"])
        if folder.is_dir() and not results:
            target = (folder / path).resolve()
            if not target.is_relative_to(folder.resolve()) or not target.exists():
                raise HTTPException(404, f"no '{path}' in run {run['id']}")
            if target.is_dir():
                return [{"name": p.name, "path": p.relative_to(folder).as_posix(), "dir": p.is_dir(),
                         "size": p.stat().st_size if p.is_file() else None} for p in sorted(target.iterdir())]
            return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        if not run.get("results") or not Path(run["results"]).is_file():
            raise HTTPException(404, f"run {run['id']} has no files")
        if path in ("", "results.zip") and results:
            return FileResponse(run["results"], media_type="application/zip", filename=Path(run["results"]).name)
        return zip_entry(Path(run["results"]), path)

    # ── lab resources ────────────────────────────────────────────────────
    def resources(kind: str) -> list[dict]:
        inv = seq.lab.inventory
        stored = seq.store.resources()
        holders = seq.store.holders()
        rows = []
        items = inv.stages.values() if kind == "stage" else inv.components.values()
        for item in items:
            res = stored.get((kind, item.id), {})
            row = {"id": item.id, "maintenance": bool(res.get("maintenance")), "hold": bool(res.get("hold")),
                   "note": res.get("note"), "holders": [{"run": r, "mode": m} for r, m in holders.get((kind, item.id), [])]}
            if kind == "stage":
                row.update(type=item.type, components=item.components)
            else:
                row.update(type=item.type, role=item.role, host=item.host, lock=item.lock, stages=item.stages,
                           label=item.label, adhoc=item.adhoc, state=res.get("state") or "unknown",
                           expected=res.get("expected"), detail=res.get("detail"), probed_at=res.get("probed_at"))
            rows.append(row)
        return rows

    @app.get("/stages")
    async def stages():
        return resources("stage")

    @app.get("/components")
    async def components():
        return resources("component")

    def resource_action(kind: str):
        async def endpoint(name: str, action: str, body: dict | None = None):
            items = seq.lab.inventory.stages if kind == "stages" else seq.lab.inventory.components
            if name not in items:
                raise HTTPException(404, f"no {kind[:-1]} '{name}'")
            note = (body or {}).get("note", "")
            if action == "hold":
                return seq.set_flag(name, "hold", True, note)
            if action == "maintenance":
                return seq.set_flag(name, "maintenance", True, note)
            if action == "resume":
                seq.set_flag(name, "maintenance", False)
                return seq.set_flag(name, "hold", False)
            if action == "release":
                return seq.release(name)
            raise HTTPException(404, f"unknown action '{action}' (hold, release, maintenance, resume)")
        return endpoint

    for kind in ("stages", "components"):
        app.post(f"/{kind}/{{name}}/{{action}}", name=f"{kind}_action")(resource_action(kind))

    @app.post("/selftest")
    async def selftest(body: dict | None = None):
        return await seq.selftest((body or {}).get("target"))

    @app.get("/lab")
    async def lab():
        described = seq.lab.describe()
        editor = getattr(app.state, "editor", None)
        if editor is not None and "lab" in editor.published:
            published = editor.published["lab"]
            current = (seq.lab.git or {}).get("commit")
            described["published"] = {**published, "newer": published["commit"] != current}
        return described

    @app.post("/lab/reload")
    async def reload(body: dict | None = None):
        body = body or {}
        lab = await seq.reload_lab(pull=bool(body.get("pull")), revision=body.get("revision"))
        return {"git": lab.git, "warnings": lab.warnings, "loaded_at": lab.loaded_at}

    @app.get("/config")
    async def config():
        return [{"key": k, "value": v if isinstance(v, (int, float, str, bool, list, type(None))) else str(v),
                 "source": s} for k, v, s in seq.config.describe()]

    @app.get("/health")
    async def health():
        return {"ok": True, "started_at": seq.started_at, "lab": seq.lab.git}

    # ── events ───────────────────────────────────────────────────────────
    @app.get("/events")
    async def events(request: Request, run: str | None = None, logs: bool = False):
        """Server-Sent Events: run, phase, step, component and lab changes; log lines with logs=true."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        seq.subscribers.add(queue)

        async def stream():
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), 15)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": keepalive\n\n"
                        continue
                    if event["kind"] == "log" and not logs:
                        continue
                    if run and event.get("run") and not str(event["run"]).startswith(run):
                        continue
                    if run and not event.get("run") and event["kind"] in ("log", "step", "phase"):
                        continue
                    yield f"event: {event['kind']}\ndata: {json.dumps(event, default=str)}\n\n"
            finally:
                seq.subscribers.discard(queue)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    if seq.config.server.editor:
        from testsuit.sequencer.editor_api import add_editor_routes
        add_editor_routes(app, seq)
    if seq.config.server.ui:
        add_ui(app)
    return app


UI_DIR = Path(__file__).parent / "ui"


def add_ui(app: FastAPI) -> None:
    """The web UI (README section 13.1), a static build served under /ui (built from the repository's ui/ folder)."""
    from fastapi.responses import HTMLResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles

    if (UI_DIR / "index.html").is_file():
        app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")
    else:
        @app.get("/ui/", include_in_schema=False)
        async def no_ui():
            return HTMLResponse("<h1>testsuit sequencer</h1><p>The web UI is not built in this installation: "
                                "<code>cd ui && npm ci && npm run build</code> in the testsuit repository, then "
                                "reinstall. The REST API is at <a href='../docs'>/docs</a>.</p>")

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("ui/")
