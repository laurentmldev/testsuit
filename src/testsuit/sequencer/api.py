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

"""REST API of the sequencer (README section 13), FastAPI. ``tsq`` and the UI are its clients.

Files are served from the run folder while it exists (queued, running, and done until the
retention period), from the results zip after that. ``FileResponse`` answers HTTP range requests.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

from testsuit.sequencer.logbuffer import install as install_log_buffer
from testsuit.sequencer.manualstep import awaiting
from testsuit.sequencer.model import comment_cards
from testsuit.sequencer.sequencer import Sequencer
from testsuit.sequencer.util import SequencerError, now, parse_duration

STEP_FILES = ("datapack.yml", "context.yml", "baseline.yml", "run.yml", "selftest.yml")


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The bytes [start, end] (end included) of a file of size bytes an HTTP Range header asks for ("bytes=a-b",
    "bytes=a-", "bytes=-n"; one range only); None: no range asked, or one this server does not read (several):
    the whole file. HTTPException 416 when it is outside the file."""
    m = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", header or "")
    if not m or not (m[1] or m[2]):
        return None
    if m[1]:
        start, end = int(m[1]), min(int(m[2]) if m[2] else size - 1, size - 1)
    else:
        start, end = max(0, size - int(m[2])), size - 1
    if start >= size or start > end:
        raise HTTPException(416, f"bytes {header} outside the {size} of the file", headers={"Content-Range": f"bytes */{size}"})
    return start, end


def zip_entry(zip_path: Path, path: str, range_header: str | None = None):
    """A file of a zip (its top folder left out of path), streamed; with range_header (an HTTP Range header), the
    bytes it asks for only (206): the viewers read a big file part by part. A folder: its entries, listed."""
    archive = zipfile.ZipFile(zip_path)
    root = archive.namelist()[0].split("/")[0]
    prefix = f"{root}/{path}".rstrip("/")
    names = archive.namelist()
    if prefix in names and not prefix.endswith("/"):
        info = archive.getinfo(prefix)
        media = mimetypes.guess_type(prefix)[0] or "application/octet-stream"
        try:
            asked = byte_range(range_header, info.file_size)
        except HTTPException:
            archive.close()
            raise
        start, end = asked or (0, info.file_size - 1)

        def stream():
            with archive, archive.open(info) as f:
                # a compressed entry is read up to start (not sent): only the bytes asked leave the server
                f.seek(start)
                left = end - start + 1
                while left > 0 and (chunk := f.read(min(1 << 20, left))):
                    left -= len(chunk)
                    yield chunk
        headers = {"Content-Length": str(max(0, end - start + 1)), "Accept-Ranges": "bytes"}
        if asked is None:
            return StreamingResponse(stream(), media_type=media, headers=headers)
        return StreamingResponse(stream(), status_code=206, media_type=media,
                                 headers={**headers, "Content-Range": f"bytes {start}-{end}/{info.file_size}"})
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
    log_buffer = install_log_buffer()
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

    # ── campaign runs ────────────────────────────────────────────────────
    @app.post("/campaigns", status_code=201)
    async def submit_campaign(manifest: Annotated[UploadFile, File()], datapacks: Annotated[list[UploadFile], File()],
                              priority: Annotated[int | None, Form()] = None):
        """Queue a generated campaign: its campaign.yml manifest and every datapack zip it lists."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "campaign.yml").write_bytes(await manifest.read())
            for upload in datapacks:
                with open(folder / Path(upload.filename or "datapack.zip").name, "wb") as f:
                    while chunk := await upload.read(1 << 20):
                        f.write(chunk)
            return await asyncio.to_thread(seq.submit_campaign, folder, priority)

    @app.get("/campaigns")
    async def campaigns(limit: int = 200):
        return await asyncio.to_thread(seq.campaign_views, limit)

    @app.get("/campaigns/{campaign_id}")
    async def campaign(campaign_id: str):
        return await asyncio.to_thread(seq.campaign_view, campaign_id)

    @app.get("/campaigns/{campaign_id}/report", include_in_schema=True)
    async def campaign_report(campaign_id: str, request: Request, download: bool = False):
        """The synthetic report of a campaign run, a self-contained HTML page linking to the runs' pages (by the
        sequencer's own URL when downloaded, so that its links work from the file)."""
        from fastapi.responses import HTMLResponse
        from testsuit.sequencer.campaigns import report_html
        view = await asyncio.to_thread(seq.campaign_view, campaign_id)
        headers = {"Content-Disposition": f'attachment; filename="{view["campaign"]}_{view["id"]}_report.html"'} if download else {}
        cov = await asyncio.to_thread(seq.campaign_requirements, campaign_id)
        return HTMLResponse(report_html(view, base=str(request.base_url) if download else "../../", requirements=cov),
                            headers=headers)

    @app.get("/campaigns/{campaign_id}/requirements")
    async def campaign_requirements(campaign_id: str):
        """Requirements coverage of a campaign run's runs (SPEC §5.5): ``linked: false`` when none links any."""
        cov = await asyncio.to_thread(seq.campaign_requirements, campaign_id)
        return {"linked": bool(cov.tests), **cov.as_dict()}

    @app.get("/campaigns/{campaign_id}/requirements/report")
    async def campaign_requirements_report(campaign_id: str, download: bool = False):
        from fastapi.responses import HTMLResponse
        from testsuit.reqs.render import as_html
        view = await asyncio.to_thread(seq.campaign_view, campaign_id)
        cov = await asyncio.to_thread(seq.campaign_requirements, campaign_id)
        headers = {"Content-Disposition": f'attachment; filename="{view["campaign"]}_{view["id"]}_requirements.html"'} if download else {}
        from testsuit.sequencer.reportlinks import OnlineLinks
        return HTMLResponse(as_html(cov, f"{view.get('title') or view['campaign']}: requirements coverage", OnlineLinks("../../../")),
                            headers=headers)

    def who(request: Request, body: dict | None) -> str:
        """Who makes a review entry: the user an authenticating reverse proxy names, else the one the body names."""
        for header in ("x-remote-user", "remote-user", "x-forwarded-user", "x-auth-request-user"):
            if request.headers.get(header):
                return request.headers[header]
        return str((body or {}).get("by") or "")

    @app.get("/campaigns/{campaign_id}/review")
    async def campaign_review(campaign_id: str):
        """The review of a campaign run (its own entries; its runs' reviews are on each run, SPEC §11.1)."""
        from testsuit.sequencer.campaigns import campaign_review_brief
        campaign = seq.require_campaign_run(campaign_id)
        return {"campaign_run": campaign["id"], **campaign_review_brief(seq.campaign_review(campaign["id"]))}

    @app.post("/campaigns/{campaign_id}/review")
    async def review_campaign(campaign_id: str, body: dict, request: Request):
        """Add to the review of a campaign run: {action: review, target: {kind: campaign}, comment}, {action:
        sign_off, comment?} (its runs' open reviews signed off with it), {action: reopen, reason}. "by": who."""
        return await asyncio.to_thread(seq.review_campaign, campaign_id, body, who(request, body))

    @app.post("/campaigns/{campaign_id}/export")
    async def export_campaign(campaign_id: str, request: Request, body: dict | None = None):
        """Export the report of a campaign run, in the background (SPEC §11.2): {mode: synthetic|complete,
        include_results}. Its progress comes as export_progress events; GET /exports/{id} gives it too."""
        body = body or {}
        return await seq.start_export("campaign", campaign_id, str(body.get("mode") or "complete"),
                                      bool(body.get("include_results")), str(request.base_url))

    @app.post("/campaigns/{campaign_id}/{command}")
    async def abort_campaign(campaign_id: str, command: str):
        if command not in ("cancel", "kill"):
            raise HTTPException(404, f"no campaign command '{command}'")
        return await seq.abort_campaign(campaign_id, command)

    @app.get("/runs")
    async def runs(state: str | None = None, limit: int = 200):
        out = seq.store.runs(state.split(",") if state else None, limit)
        for run in out:                                  # the verdict as reviewed, when it is (SPEC §11.1)
            reviewed = seq.reviewed(run)
            if reviewed is not run:
                run["review"] = {k: v for k, v in (reviewed.get("review") or {}).items()
                                 if k in ("state", "verdict", "raw_verdict", "accepted", "rejected")}
        return out

    @app.get("/queue")
    async def queue():
        return seq.store.queue()

    @app.get("/runs/{run_id}")
    async def show(run_id: str):
        run = run_or_404(run_id)
        execution = seq.executions.get(run["id"])
        if execution is not None and execution.ctx is not None:
            run["steps"] = [r.as_dict() for r in execution.ctx.records.values()]
            run["awaiting_manual"] = awaiting(run["steps"])
            ctx = execution.ctx
            run["paused"] = [{"step": k, "verdict": p["verdict"], "since": p["since"]} for k, p in ctx.pauses.items()]
            run["control"] = {**ctx.step_mode, "paused": run["paused"]}
            run["replaced_steps"] = ctx.history
            run["running_phase"] = ctx.phase_runner.id if ctx.phase_runner is not None else None
        run["wait"] = seq.wait_info(run)                 # why it, or its next phase, does not start yet
        reviewed = seq.reviewed(run)
        if reviewed is not run:                          # its review (SPEC §11.1): run.yml's results stay as they ran
            from testsuit.sequencer.review import review_view
            view = review_view(run, {"entries": []}, reviewed)
            run["review"] = {**reviewed.get("review"), "steps": view["steps"], "checks": view["checks"]}
        return run

    @app.get("/runs/{run_id}/review")
    async def run_review(run_id: str):
        """The review of a run: its state, the entries in force, its journal, its steps and checks as reviewed, and
        the reviews of its earlier attempt that fit it (reapplicable)."""
        return await asyncio.to_thread(seq.run_review_view, run_or_404(run_id)["id"])

    @app.post("/runs/{run_id}/review")
    async def review_run(run_id: str, body: dict, request: Request):
        """Add to the review of an ended run: {action: review, target: {kind: check|step|run, step, criteria,
        scenario, check}, status: accepted|rejected (with reason) or none (comment), comment, ref}, {action: revoke,
        entry, reason}, {action: sign_off, comment?}, {action: reopen, reason}, {action: reapply, entries?}. "by":
        who (the user named by the reverse proxy, when there is one)."""
        return await asyncio.to_thread(seq.review_run, run_or_404(run_id)["id"], body, who(request, body))

    @app.get("/runs/{run_id}/report")
    async def run_report(run_id: str, request: Request, download: bool = False):
        """The report of a run as reviewed, a self-contained HTML page (links to the sequencer)."""
        from fastapi.responses import HTMLResponse
        from testsuit.sequencer.reportlinks import OnlineLinks
        run = run_or_404(run_id)
        links = OnlineLinks(str(request.base_url) if download else "../../")
        page = await asyncio.to_thread(seq.run_report_html, run["id"], links)
        headers = {"Content-Disposition": f'attachment; filename="{run["id"]}_report.html"'} if download else {}
        return HTMLResponse(page, headers=headers)

    @app.post("/runs/{run_id}/export")
    async def export_run(run_id: str, request: Request, body: dict | None = None):
        """Export the report of a run, in the background: {mode: synthetic|complete, include_results}."""
        body = body or {}
        return await seq.start_export("run", run_or_404(run_id)["id"], str(body.get("mode") or "complete"),
                                      bool(body.get("include_results")), str(request.base_url))

    @app.get("/exports/{job_id}")
    async def export_job(job_id: str):
        return seq.export_job(job_id)

    @app.post("/exports/{job_id}/cancel")
    async def cancel_export(job_id: str):
        return seq.cancel_export(job_id)

    @app.get("/exports/{job_id}/download")
    async def download_export(job_id: str):
        job = seq.export_job(job_id)
        if job["state"] != "done" or not Path(job["file"] or "").is_file():
            raise HTTPException(409, f"export {job_id} is {job['state']}" + (f": {job['error']}" if job.get("error") else ""))
        media = "application/zip" if job["name"].endswith(".zip") else "text/html"
        return FileResponse(job["file"], media_type=media, filename=job["name"])

    @app.get("/runs/{run_id}/exploit/{step}/report")
    async def exploit_report(run_id: str, step: str):
        """The exploit report of an exploit step ('<phase>:<step>', or its id), its checks as reviewed."""
        from fastapi.responses import HTMLResponse
        run = run_or_404(run_id)
        return HTMLResponse(await asyncio.to_thread(seq.exploit_report_html, run["id"], step))

    @app.get("/runs/{run_id}/requirements")
    async def run_requirements(run_id: str):
        """Requirements coverage of a run (SPEC §5.5): ``linked: false`` when it links none."""
        run_or_404(run_id)
        cov = await asyncio.to_thread(seq.run_requirements, run_id)
        return {"linked": bool(cov.tests), **cov.as_dict()}

    @app.get("/runs/{run_id}/requirements/report")
    async def run_requirements_report(run_id: str, download: bool = False):
        from fastapi.responses import HTMLResponse
        from testsuit.reqs.render import as_html
        run = run_or_404(run_id)
        cov = await asyncio.to_thread(seq.run_requirements, run_id)
        headers = {"Content-Disposition": f'attachment; filename="{run["id"]}_requirements.html"'} if download else {}
        from testsuit.sequencer.reportlinks import OnlineLinks
        return HTMLResponse(as_html(cov, f"{run.get('testdef') or run['id']}: requirements coverage", OnlineLinks("../../../")),
                            headers=headers)

    def python_label(py: dict) -> str:
        if py.get("script"):
            return f"python {Path(str(py['script'])).name}"
        return "python code"

    @app.get("/runs/{run_id}/pipeline")
    async def pipeline(run_id: str):
        """The run's pipeline as it runs: phases, steps after sub-pipeline expansion, their needs (the UI graph)."""
        from testsuit.sequencer.intake import load_datapack
        run = run_or_404(run_id)
        if not Path(run["dir"]).is_dir():
            raise HTTPException(404, f"run {run['id']}: the run folder is gone (retention), only its records remain")
        dp = await asyncio.to_thread(load_datapack, Path(run["dir"]), run.get("routing"))
        phases = []
        for flat in dp.phases:
            steps = []
            for part, items in (("steps", flat.steps), ("finally", flat.finally_)):
                for fs in items:
                    s = fs.step
                    steps.append({"id": fs.id, "part": part, "needs": fs.needs, "group": fs.group, "action": s.action,
                                  "do": s.do, "slot": s.slot, "for_each": s.for_each,
                                  "if": s.if_, "label": s.do or s.run or s.uses or (python_label(s.python) if s.python else None)
                                  or (f"manual: {s.manual.get('title') or fs.id}" if s.manual else s.action)})
            phases.append({"id": flat.phase.id, "needs": flat.phase.needs, "stage_slots": flat.phase.stage_slots,
                           "slots": flat.phase.slots, "groups": flat.groups, "steps": steps,
                           "comments": comment_cards(flat.phase, dp.pipeline)})
        return {"phases": phases, "stage_slots": dp.needs.stage_slots,
                "slots": {c.id: {"slot_class": c.slot_class, "role": c.role, "stage_slot": c.stage_slot, "name": c.name}
                          for c in dp.needs.slots.values()}}

    @app.get("/runs/{run_id}/resources")
    async def run_resources(run_id: str):
        """What the run's datapack expects of the lab against what the lab defines, and why what it
        could use is not available now."""
        from testsuit.sequencer.intake import check_against_lab, load_datapack
        from testsuit.sequencer.scheduler import Binding, resource_overview
        run = run_or_404(run_id)
        if not Path(run["dir"]).is_dir():
            raise HTTPException(404, f"run {run['id']}: the run folder is gone (retention), only its records remain")
        dp = await asyncio.to_thread(load_datapack, Path(run["dir"]), run.get("routing"))
        binding = Binding.from_dict(run.get("binding"))
        overview = resource_overview(seq.lab, dp.needs, dp.pipeline.phases, seq.availability(), run["id"], binding)
        return {**overview, "problems": check_against_lab(dp, seq.lab), "state": run["state"],
                "waiting_for": run.get("waiting_for") or "", "reserve": dp.pipeline.reserve}

    @app.patch("/runs/{run_id}")
    async def patch(run_id: str, body: dict):
        if "priority" not in body:
            raise HTTPException(400, "only 'priority' can change")
        return seq.set_priority(run_or_404(run_id)["id"], int(body["priority"]))

    @app.post("/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        return await seq.abort(run_or_404(run_id)["id"], "cancel")

    @app.post("/runs/{run_id}/retry")
    async def retry(run_id: str):
        """Check again now whether a waiting run, or its waiting phase, can start."""
        try:
            return seq.retry_now(run_or_404(run_id)["id"])
        except SequencerError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/runs/{run_id}/manual/{step:path}")
    async def manual_answer(run_id: str, step: str, body: dict):
        """The operator's answer to a manual operation the run waits for: {"check": i, "done": bool} ticks a
        check, {"outcome": "done" | "failed", "note": "..."} ends it; "by" names who answered."""
        try:
            if body.get("outcome") == "redo":                 # redo from an earlier step it lists (redo_from)
                return await seq.redo_manual(run_or_404(run_id)["id"], step, str(body.get("from") or ""),
                                             str(body.get("by") or ""), str(body.get("note") or ""))
            return seq.answer_manual(run_or_404(run_id)["id"], step, body, str(body.get("by") or ""))
        except SequencerError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/runs/{run_id}/control")
    async def control(run_id: str, body: dict):
        """Step-by-step mode: {"mode": "off" | "each" | "breakpoints", "breakpoints": ["<phase>:<step>", ...]}."""
        try:
            return seq.set_control(run_or_404(run_id)["id"], body.get("mode"), body.get("breakpoints"))
        except SequencerError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/runs/{run_id}/steps/{step:path}/{action}")
    async def step_action(run_id: str, step: str, action: str, body: dict | None = None):
        """An operator action on a step of the phase running now: next (a paused step goes on), run_again (it and
        the steps that need it run again), continue (go on past it: {"reason"} required). "by" names who."""
        body = body or {}
        try:
            return await seq.step_action(run_or_404(run_id)["id"], step, action, str(body.get("by") or ""),
                                         str(body.get("reason") or ""))
        except SequencerError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/runs/{run_id}/rerun")
    async def rerun(run_id: str):
        """Run a datapack of a campaign run again: a new run of the same zip, the earlier attempt kept."""
        return await asyncio.to_thread(seq.rerun, run_or_404(run_id)["id"])

    @app.post("/runs/{run_id}/repost")
    async def repost(run_id: str):
        return await asyncio.to_thread(seq.repost, run_or_404(run_id)["id"])

    @app.post("/runs/{run_id}/kill")
    async def kill(run_id: str):
        return await seq.abort(run_or_404(run_id)["id"], "kill")

    @app.get("/runs/{run_id}/logs")
    async def logs(run_id: str):
        run = await show(run_id)
        return [{"step": f"{s['phase']}:{s['id']}" + (f"[{slot}]" if (slot := s.get("slot") or s.get("component")) else ""),
                 "state": s["state"], "log": s["log"]} for s in run.get("steps") or []]

    @app.get("/runs/{run_id}/logs/{step:path}")
    async def log(run_id: str, step: str, offset: int = 0):
        """A step's log ('<phase>:<step>[<slot>]', or a unique prefix of it) from a byte offset."""
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
    async def files(run_id: str, path: str = "", results: bool = False, list: bool = False,  # noqa: A002
                    range_header: Annotated[str | None, Header(alias="range")] = None):
        """List a folder or download a file of the run: run folder, or results zip (results=true, or once
        the run folder is gone). With results=true, the bare path downloads the zip, unless list=true."""
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
        if results and (path == "results.zip" or (path == "" and not list)):
            return FileResponse(run["results"], media_type="application/zip", filename=Path(run["results"]).name)
        return zip_entry(Path(run["results"]), path, range_header)

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
                row.update(type=item.type, components=item.components, description=item.description)
            else:
                row.update(role=item.role, extends=item.extends, host=item.host, host_alias=item.host_alias, lock=item.lock, stages=item.stages,
                           label=item.label, description=item.description, adhoc=item.adhoc, state=res.get("state") or "unknown",
                           expected=res.get("expected"), detail=res.get("detail"), probed_at=res.get("probed_at"),
                           selftest_failed=res.get("selftest_failed"), probing=item.id in seq.monitor.running,
                           bypass={c: b for c, b in (res.get("bypass") or {}).items() if b.get("until", "") > now()})
            rows.append(row)
        return rows

    @app.get("/stages")
    async def stages():
        return resources("stage")

    @app.get("/components")
    async def components():
        return resources("component")

    def probe_report(name: str) -> dict:
        """The probe going on now when there is one (``running``, its steps so far, the last one being run),
        else the last one."""
        res = seq.store.resources("component").get(("component", name)) or {}
        probe = seq.monitor.running.get(name) or seq.monitor.reports.get(name)
        return {"component": name, "state": res.get("state") or "unknown", "detail": res.get("detail"),
                "expected": res.get("expected"), "probed_at": res.get("probed_at"),
                "probe": probe and {**probe, "steps": [dict(st) for st in probe.get("steps") or []]},
                "selftest": seq.monitor.selftests.get(name),
                "selftest_failed": res.get("selftest_failed"), "bypass": seq.bypasses(name)}

    @app.get("/components/{name}/probe")
    async def component_probe(name: str):
        """The last probe of a component: what was tested, its answer, the rule that gave its state and why;
        and its last self-test, with each check."""
        if name not in seq.lab.inventory.components:
            raise HTTPException(404, f"no component '{name}'")
        return probe_report(name)

    @app.get("/components/{name}/commands")
    async def component_commands(name: str):
        """The commands of a component, which of them can be run by hand, and whether it is in maintenance."""
        if name not in seq.lab.inventory.components:
            raise HTTPException(404, f"no component '{name}'")
        try:
            return await seq.manual_commands(name)
        except SequencerError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/components/{name}/commands")
    async def component_commands_with_keys(name: str, body: dict | None = None):
        """The same, as its commands would run with keys set by hand (``keys``: dotted name -> value): what each
        key does (it sets a key the lab does not define, or overrides one). Nothing is run."""
        if name not in seq.lab.inventory.components:
            raise HTTPException(404, f"no component '{name}'")
        try:
            return await seq.manual_commands(name, (body or {}).get("keys") or None)
        except SequencerError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/components/{name}/commands/{command}")
    async def run_component_command(name: str, command: str, body: dict | None = None):
        """Run one command of a component in maintenance, by hand (README section 7.7); ``keys``: keys set by
        hand for this run (dotted name -> value), over the keys of the lab."""
        if name not in seq.lab.inventory.components:
            raise HTTPException(404, f"no component '{name}'")
        timeout = (body or {}).get("timeout")
        try:
            return await seq.manual_command(name, command, parse_duration(timeout) if timeout else None,
                                            (body or {}).get("keys") or None, (body or {}).get("token") or None)
        except SequencerError as e:
            raise HTTPException(409, str(e)) from e

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
            if action == "probe" and kind == "components":
                await seq.monitor.probe(name)
                return probe_report(name)
            if action == "bypass" and kind == "components":
                # bypass a failed self-test check for some hours (hours 0 or none: end it), then test it again
                b = body or {}
                try:
                    hours = float(b["hours"]) if b.get("hours") not in (None, "") else None
                    return await seq.bypass_check(name, str(b.get("check") or ""), hours, str(b.get("reason") or ""),
                                                  str(b.get("by") or ""))
                except (SequencerError, ValueError) as e:
                    raise HTTPException(400, str(e)) from e
            raise HTTPException(404, f"unknown action '{action}' (hold, release, maintenance, resume"
                                     + (", probe, bypass)" if kind == "components" else ")"))
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

    checked: dict = {}                          # the last /lab/changes: one look at the files for every open page

    @app.get("/lab/changes")
    async def lab_changes():
        """The lab files added, removed or modified since the lab was read (the Lab page asks every few seconds,
        to offer a reload): their modification times and sizes, compared; at most once a second."""
        import time
        lab = seq.lab
        if checked.get("lab") is not lab or time.monotonic() - checked["at"] > 1:
            checked.update(lab=lab, at=time.monotonic(), result=await asyncio.to_thread(lab.changes))
        return checked["result"]

    @app.get("/lab/view")
    async def lab_view():
        """The Lab view: links between components deduced from the lab configuration, their pictures, the active
        runs and the steps each component runs now."""
        from testsuit.sequencer.labview import lab_view as view
        return view(seq)

    @app.get("/lab/layout")
    async def lab_layout():
        """The arrangement of the Lab view saved for everyone (positions and opacities); empty: automatic."""
        from testsuit.sequencer.labview import read_layout
        return read_layout(seq)

    @app.put("/lab/layout")
    async def save_lab_layout(body: dict):
        """Save the arrangement of the Lab view for everyone (``{"components": {}}``: back to automatic)."""
        from testsuit.sequencer.labview import save_layout
        layout = save_layout(seq, body)
        seq.notify("lab_layout", {"components": len(layout["components"])})
        return layout

    @app.get("/lab/icons/{name}")
    async def lab_icon(name: str):
        """The image file of a component's tsq_icon (in the lab folder)."""
        from testsuit.sequencer.labview import icon_path
        component = seq.lab.inventory.components.get(name)
        path = icon_path(seq.lab, component) if component is not None else None
        if path is None:
            raise HTTPException(404, f"no icon file for '{name}'")
        # an SVG opened on its own runs no script of the lab repository
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    @app.get("/logs")
    async def get_logs(since: int = 0, level: str | None = None, limit: int = 1000, probes: bool = True):
        """The last log lines of tsqd (events, warnings, errors, and the probes of the lab), after line since."""
        return log_buffer.lines(since, level, max(1, min(limit, 3000)), probes)

    @app.post("/lab/probe")
    async def probe_lab():
        """Probe every component of the lab now (the Lab page, when it opens or on Probe now)."""
        await seq.monitor.probe_all()
        return {"probed": len(seq.lab.inventory.components)}

    @app.post("/lab/reload")
    async def reload(body: dict | None = None):
        body = body or {}
        lab = await seq.reload_lab(pull=bool(body.get("pull")), revision=body.get("revision"))
        return {"git": lab.git, "warnings": lab.warnings, "loaded_at": lab.loaded_at}

    @app.get("/config")
    async def config():
        return [{"key": k, "value": v if isinstance(v, (int, float, str, bool, list, type(None))) else str(v),
                 "source": s} for k, v, s in seq.config.describe()]

    def logo_url() -> str | None:
        logo = seq.config.server.logo
        if not logo:
            return None
        if logo.startswith(("http://", "https://")):
            return logo
        file = seq.config.logo_file
        return f"logo{file.suffix}" if file and file.is_file() else None

    @app.get("/health")
    async def health():
        return {"ok": True, "started_at": seq.started_at, "lab": seq.lab.git,
                "title": seq.config.server.title, "logo": logo_url()}

    @app.get("/logo{suffix}", include_in_schema=False)
    async def logo(suffix: str):
        """The UI's logo image (server.logo), when it is a file."""
        file = seq.config.logo_file
        if file is None or not file.is_file():
            raise HTTPException(404, "no logo file (server.logo)")
        return FileResponse(file)

    # ── events ───────────────────────────────────────────────────────────
    @app.get("/events")
    async def events(request: Request, run: str | None = None, logs: bool = False):
        """Server-Sent Events: run, phase, step, component (and each probe) and lab changes; log lines with logs=true."""
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
                    if event is None:                   # the server is stopping
                        return
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
