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

"""tsq: the sequencer command line (sequencer README, section 13).

Most commands are clients of a tsqd daemon (--server, $TSQ_SERVER, or server.listen of tsq.yml).
``run --local``, ``validate``, ``config`` and ``lab status`` work without a daemon.

Exit codes: with --wait (and for run --local) the run verdict, 0 succeeded, 1 failed, 2 error;
otherwise 0, or 2 when the command could not be done.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import yaml

VERDICT_CODES = {"succeeded": 0, "failed": 1, "error": 2}


class CliError(Exception):
    pass


# ── output ───────────────────────────────────────────────────────────────────

def table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("(none)")
        return
    cells = [[_cell(r.get(c)) for c in columns] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(columns)]
    print("  ".join(c.upper().ljust(w) for c, w in zip(columns, widths)).rstrip())
    for row in cells:
        print("  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip())


def _cell(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ",".join(_cell(v) for v in value) or "-"
    if isinstance(value, dict):
        return " ".join(f"{k}={_cell(v)}" for k, v in value.items()) or "-"
    return str(value)


def dump(data) -> None:
    print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip())


# ── daemon client ────────────────────────────────────────────────────────────

class Client:

    def __init__(self, server: str):
        import requests
        self.server = server.rstrip("/")
        self.http = requests.Session()

    def request(self, method: str, path: str, **kwargs):
        import requests
        try:
            response = self.http.request(method, self.server + path, timeout=kwargs.pop("timeout", 60), **kwargs)
        except requests.ConnectionError as e:
            raise CliError(f"cannot reach the sequencer at {self.server} ({e.__class__.__name__}); "
                           "is tsqd running? (--server, $TSQ_SERVER)") from e
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text
            raise CliError(f"{method} {path}: {detail}")
        return response

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs).json()

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs).json()

    def events(self, run: str | None = None, logs: bool = False):
        """Server-Sent Events as dicts."""
        params = {"logs": str(logs).lower()}
        if run:
            params["run"] = run
        response = self.request("GET", "/events", params=params, stream=True, timeout=None)
        data = []
        for line in response.iter_lines(decode_unicode=True):
            if line is None:
                continue
            if line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "" and data:
                yield json.loads("\n".join(data))
                data = []


def server_url(args) -> str:
    if args.server:
        return args.server
    if os.environ.get("TSQ_SERVER"):
        return os.environ["TSQ_SERVER"]
    from testsuit.sequencer.config import load_config
    try:
        listen = load_config(args.config).server.listen
    except Exception:
        listen = "127.0.0.1:8640"
    host, _, port = listen.rpartition(":")
    return f"http://{'127.0.0.1' if host in ('', '0.0.0.0', '::') else host}:{port}"


def print_step(event: dict) -> None:
    key = f"{event['phase']}:{event['id']}" + (f"[{event['slot']}]" if event.get("slot") else "")
    if event["state"] in ("running", "succeeded", "failed", "error", "skipped"):
        reason = f" ({event['reason']})" if event.get("reason") else ""
        print(f"{time.strftime('%H:%M:%S')} {event['state']:<9} {key}{reason}", flush=True)


def follow(client: Client, run_id: str, logs: bool) -> dict:
    for event in client.events(run_id, logs=logs):
        if event["kind"] == "log":
            for line in event["text"].splitlines():
                print(f"[{event['step']}] {line}", flush=True)
        elif event["kind"] == "step":
            print_step(event)
        elif event["kind"] == "run" and event.get("state"):
            print(f"{time.strftime('%H:%M:%S')} run {run_id}: {event['state']}"
                  + (f", {event['verdict']}" if event.get("verdict") else "")
                  + (f" ({event['reason']})" if event.get("reason") else ""), flush=True)
            if event["state"] in ("done", "cancelled"):
                break
    return client.get(f"/runs/{run_id}")


def wait(client: Client, run_id: str) -> dict:
    while True:
        run = client.get(f"/runs/{run_id}")
        if run["state"] in ("done", "cancelled"):
            return run
        time.sleep(2)


def verdict_exit(run: dict) -> int:
    print(f"run {run['id']}: {run['verdict']}" + (f" ({run['reason']})" if run.get("reason") else "")
          + (f"\nresults: {run['results']}" if run.get("results") else ""))
    return VERDICT_CODES.get(run.get("verdict") or "error", 2)


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_submit(args, client: Client) -> int:
    files = {"datapack": (Path(args.datapack).name, open(args.datapack, "rb"), "application/zip")}
    if args.baseline:
        files["baseline"] = (Path(args.baseline).name, open(args.baseline, "rb"), "application/yaml")
    priority = args.priority if args.priority is not None else os.environ.get("TSQ_PRIORITY") or None
    data = {"priority": str(priority)} if priority is not None else {}
    run = client.post("/runs", files=files, data=data, timeout=600)
    if os.environ.get("TSQ_RUN_FILE"):                      # for the GitLab executor's cleanup
        Path(os.environ["TSQ_RUN_FILE"]).write_text(run["id"])
    print(f"run {run['id']} queued, priority {run['priority']}"
          + ("" if run["conform"] else ", NON-CONFORM: " + "; ".join(run["nonconform"] or [])))
    if args.follow:
        return verdict_exit(follow(client, run["id"], logs=True))
    if args.wait:
        return verdict_exit(wait(client, run["id"]))
    return 0


def cmd_submit_campaign(args, client: Client) -> int:
    from testsuit.datatools.datapack.campaign import CampaignError, read_manifest
    folder = Path(args.campaign)
    folder = folder if folder.is_dir() else folder.parent
    try:
        manifest = read_manifest(folder)
    except CampaignError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    files = [("manifest", ("campaign.yml", open(folder / "campaign.yml", "rb"), "application/yaml"))]
    files += [("datapacks", (d["zip"], open(folder / d["zip"], "rb"), "application/zip")) for d in manifest["datapacks"]]
    data = {"priority": str(args.priority)} if args.priority is not None else {}
    campaign = client.post("/campaigns", files=files, data=data, timeout=1800)
    print(f"campaign run {campaign['id']} ({campaign['campaign']}): {len(campaign['runs'])} run(s) queued"
          + ("" if campaign["conform"] else ", NON-CONFORM"))
    for r in campaign["runs"]:
        print(f"  {r['run']}  priority {r.get('priority')}  {r.get('testdef')}")
    if not args.wait:
        return 0
    while True:
        campaign = client.get(f"/campaigns/{campaign['id']}")
        if campaign["state"] == "done":
            break
        time.sleep(5)
    from testsuit.sequencer.campaigns import report_text
    print("\n".join(report_text(campaign)))
    return VERDICT_CODES.get(campaign.get("verdict") or "error", 2)


def cmd_campaigns(args, client: Client) -> int:
    rows = client.get("/campaigns")
    for row in rows:
        row["runs"] = "{succeeded}/{total} succeeded".format(**row["counts"])
    table(rows, ["id", "campaign", "state", "verdict", "conform", "runs", "submitted_at", "ended_at"])
    return 0


def cmd_campaign(args, client: Client) -> int:
    from testsuit.sequencer.campaigns import report_text
    if args.cancel or args.kill:
        view = client.post(f"/campaigns/{args.id}/{'kill' if args.kill else 'cancel'}")
    else:
        view = client.get(f"/campaigns/{args.id}")
    print("\n".join(report_text(view)))
    return 0


def cmd_priority(args, client: Client) -> int:
    run = client.request("PATCH", f"/runs/{args.run}", json={"priority": args.priority}).json()
    print(f"run {run['id']}: priority {run['priority']}")
    return 0


def cmd_queue(args, client: Client) -> int:
    table(client.get("/queue"), ["id", "name", "priority", "state", "submitted_at", "waiting_for"])
    return 0


def cmd_runs(args, client: Client) -> int:
    params = {"state": args.state} if args.state else {}
    table(client.get("/runs", params=params), ["id", "name", "priority", "state", "verdict", "conform", "submitted_at",
                                               "ended_at"])
    return 0


def cmd_show(args, client: Client) -> int:
    run = client.get(f"/runs/{args.run}")
    steps = run.pop("steps", None) or []
    dump({k: v for k, v in run.items() if v not in (None, "", [])})
    if steps:
        print()
        table([{"step": f"{s['phase']}:{s['id']}" + (f"[{slot}]" if (slot := s.get("slot") or s.get("component")) else ""), **s}
               for s in steps], ["step", "state", "exit_status", "attempts", "started", "ended", "reason"])
    return 0


def cmd_logs(args, client: Client) -> int:
    if not args.step:
        table(client.get(f"/runs/{args.run}/logs"), ["step", "state", "log"])
        return 0
    offset = 0
    while True:
        response = client.request("GET", f"/runs/{args.run}/logs/{args.step}", params={"offset": offset})
        sys.stdout.write(response.text)
        sys.stdout.flush()
        offset = int(response.headers.get("X-Log-Size", offset))
        if not args.follow or client.get(f"/runs/{args.run}")["state"] in ("done", "cancelled"):
            return 0
        time.sleep(1)


def cmd_abort(args, client: Client) -> int:
    run = client.post(f"/runs/{args.run}/{args.command}")
    print(f"run {run['id']}: {run['state']}" + (f" ({args.command} requested)" if run["state"] != "cancelled" else ""))
    return 0


def cmd_repost(args, client: Client) -> int:
    run = client.post(f"/runs/{args.run}/repost")
    print(f"run {run['id']}: {run['state']} again, outputs cleaned")
    return 0


def cmd_rerun(args, client: Client) -> int:
    run = client.post(f"/runs/{args.run}/rerun")
    print(f"run {run['id']}: {run['state']}, runs again {args.run} in campaign run {run.get('campaign_run')}")
    return 0


def _review_target(text: str, view: dict) -> dict:
    """A review target from the command line: ``run``, ``step:<phase>:<step>``, ``check:<step>::<check>`` (or
    ``check:<step>::<scenario>/<check>`` when the name alone is ambiguous; <step> is ``<phase>:<step>`` or its id)."""
    if text == "run":
        return {"kind": "run"}
    kind, _, rest = text.partition(":")
    if kind == "step" and rest:
        steps = [s["step"] for s in view.get("steps") or []]
        found = [s for s in steps if s == rest] or [s for s in steps if s.split(":", 1)[-1] == rest]
        if len(found) != 1:
            raise CliError(f"no single step '{rest}' in run {view.get('run')}" + (f": {', '.join(found)}" if found else ""))
        return {"kind": "step", "step": found[0]}
    if kind == "check" and "::" in rest:
        step, _, name = rest.partition("::")
        scenario, _, check = name.rpartition("/")
        checks = [c for c in view.get("checks") or [] if (c["step"] == step or c["step"].split(":", 1)[-1] == step)
                  and c["check"] == check and (not scenario or c["scenario"] == scenario)]
        if len(checks) != 1:
            raise CliError(f"no single check '{name}' in step {step} of run {view.get('run')}"
                           + (" (name its scenario: <scenario>/<check>)" if checks else ""))
        return {k: checks[0][k] for k in ("kind", "step", "criteria", "scenario", "check")}
    raise CliError(f"unknown review target '{text}': run, step:<phase>:<step>, check:<step>::[<scenario>/]<check>")


def _print_review(view: dict) -> None:
    state = {"none": "nobody reviewed it", "open": "open", "signed_off": "signed off"}.get(view.get("state"), view.get("state"))
    verdict = view.get("verdict")
    print(f"review: {state}" + (f"; verdict {verdict}" if verdict else "")
          + (f" (raw {view['raw_verdict']})" if view.get("raw_verdict") and view["raw_verdict"] != verdict else ""))
    for w in view.get("warnings") or []:
        print(f"  warning: {w}")
    gone = {e.get("revokes") for e in view.get("journal") or [] if e.get("action") == "revoke"}
    for e in view.get("journal") or []:
        if e["action"] == "review":
            from testsuit.sequencer.review import target_label
            what = f"{e.get('status') or 'comment'} {target_label(e['target'])}" + (" [revoked]" if e["id"] in gone else "")
        elif e["action"] == "revoke":
            what = f"revoke {e['revokes']}"
        else:
            what = e["action"].replace("_", " ")
        print(f"  {e['id']:>3} {e['at']} {e['by']}: {what}" + (f" (was {e['was']})" if e.get("was") and e.get("status") else "")
              + (f": {e['reason']}" if e.get("reason") else "") + (f" -- {e['comment']}" if e.get("comment") else "")
              + (f" [{e['ref']}]" if e.get("ref") else ""))
    for e in view.get("reapplicable") or []:
        print(f"  reapplicable from run {e['run']}: {e.get('status') or 'comment'} {e['label']} ({e['by']}: {e.get('reason') or e.get('comment')})")


def cmd_review(args, client: Client) -> int:
    """Show the review of a run (or a campaign run), or add to it (SPEC section 11.1)."""
    by = args.by or os.environ.get("TSQ_USER") or os.environ.get("USER") or ""
    if args.campaign:
        path = f"/campaigns/{args.id}/review"
        body = None
        if args.sign_off:
            body = {"action": "sign_off", "comment": args.comment}
        elif args.reopen:
            body = {"action": "reopen", "reason": args.reason}
        elif args.comment:
            body = {"action": "review", "target": {"kind": "campaign"}, "comment": args.comment}
        elif args.accept or args.reject or args.revoke or args.reapply:
            raise CliError("a campaign run is reviewed through its runs: review each run (tsq review <run>)")
        if body is None:
            view = client.get(path)
        else:
            client.post(path, json={**body, "by": by})
            view = client.get(path)
        _print_review({**view, "journal": view.get("journal")})
        return 0
    path = f"/runs/{args.id}/review"
    view = client.get(path)
    body = None
    if args.accept or args.reject:
        body = {"action": "review", "target": _review_target(args.accept or args.reject, view),
                "status": "accepted" if args.accept else "rejected", "reason": args.reason, "comment": args.comment,
                "ref": args.ref}
    elif args.revoke is not None:
        body = {"action": "revoke", "entry": args.revoke, "reason": args.reason}
    elif args.sign_off:
        body = {"action": "sign_off", "comment": args.comment}
    elif args.reopen:
        body = {"action": "reopen", "reason": args.reason}
    elif args.reapply:
        body = {"action": "reapply"}
    elif args.comment:
        body = {"action": "review", "target": _review_target(args.on or "run", view), "comment": args.comment,
                "ref": args.ref}
    if body is not None:
        view = client.post(path, json={**{k: v for k, v in body.items() if v is not None}, "by": by})
    _print_review(view)
    return 0


def _issue_line(issue: dict) -> str:
    return (f"{issue.get('key')} [{issue.get('state')}] {issue.get('title') or ''}"
            + (f" ({', '.join(issue['assignees'])})" if issue.get("assignees") else "") + (f" {issue['url']}" if issue.get("url") else ""))


def cmd_issue(args, client: Client) -> int:
    """The issues bound to a run's results: list them, bind one, create one, unbind one (SPEC section 11.3)."""
    by = args.by or os.environ.get("TSQ_USER") or os.environ.get("USER") or ""
    if args.unbind is not None:
        if not args.reason:
            raise CliError("--unbind needs --reason")
        b = client.post(f"/issues/bindings/{args.unbind}/unbind", json={"reason": args.reason, "by": by})
        print(f"unbound {b['issue'].get('key')} from {b['testdef']}")
        return 0
    if not args.run:
        raise CliError("give the run (or --unbind N --reason R)")
    target = _review_target(args.on or "run", client.get(f"/runs/{args.run}/review")) if (args.link or args.create) else None
    if args.link:
        b = client.post(f"/runs/{args.run}/issues", json={"action": "bind", "target": target, "issue": args.link,
                                                           "note": args.note, "by": by})
        print(f"binding {b['id']}: {_issue_line(b['issue'])} bound to {b['testdef']}")
        return 0
    if args.create:
        d = client.post(f"/runs/{args.run}/issues/draft", json={"target": target, "source": args.source, "by": by})
        for k in d.get("known") or []:
            print(f"already bound: {_issue_line(k['issue'])}")
        for k in d.get("duplicates") or []:
            print(f"already open for this failure: {_issue_line(k)}")
        title = args.title or d["title"]
        labels = list(dict.fromkeys([*(d.get("labels") or []), *(args.label or [])]))
        if not args.yes:
            print(f"--- {d['source']}{' (' + args.project + ')' if args.project else ''}: {title}\nlabels: {', '.join(labels)}\n"
                  f"{d['body']}\n---")
            if not sys.stdin.isatty():
                raise CliError("give --yes to create it (not asked: no terminal)")
            if input("create this issue? [y/N] ").strip().lower() not in ("y", "yes"):
                print("not created")
                return 1
        token = os.environ.get("TSQ_ISSUES_TOKEN") if args.my_token else None
        if args.my_token and not token:
            raise CliError("--my-token reads your token from $TSQ_ISSUES_TOKEN, which is not set")
        b = client.post(f"/runs/{args.run}/issues", json={"action": "create", "target": target, "source": d["source"],
                                                           "project": args.project, "title": title, "body": d["body"],
                                                           "labels": labels, "by": by, **({"token": token} if token else {})})
        print(f"created and bound: {_issue_line(b['issue'])}")
        return 0
    view = client.get(f"/runs/{args.run}/issues" + ("?refresh=true" if args.refresh else ""))
    if view.get("error"):
        print(f"issues: {view['error']}")
    if not view.get("sources"):
        print(f"run {view['run']}: its datapack names no issue source (issues.yml)")
    else:
        print("sources: " + ", ".join(f"{c['name']} ({c['backend']} {c.get('project', '')})" + (" default" if c.get("default") else "")
                                      for c in view["sources"]))
    for t in view.get("targets") or []:
        print(f"{t['status']:>8}  {t['label']}" + (f"  -> new issues go to {t['route']}" if t.get("route") else ""))
        for b in t.get("issues") or []:
            print(f"          known issue {_issue_line(b['issue'])} (binding {b['id']})")
    others = [b for b in view.get("bindings") or [] if not b.get("failed_here")]
    if others:
        print(f"other issues bound to {view['testdef']}:")
        for b in others:
            print(f"  binding {b['id']}: {b['label']}: {_issue_line(b['issue'])}")
    return 0


def _bar(done: int, total: int, width: int = 30) -> str:
    filled = int(width * done / total) if total else 0
    return "[" + "#" * filled + "." * (width - filled) + f"] {done}/{total}"


def cmd_export(args, client: Client) -> int:
    """Export the report of a run or of a campaign run (SPEC section 11.2): its progress shown, the file downloaded."""
    path = f"/campaigns/{args.id}/export" if args.campaign else f"/runs/{args.id}/export"
    job = client.post(path, json={"mode": "synthetic" if args.synthetic else "complete",
                                  "include_results": args.with_results})
    tty = sys.stdout.isatty()
    last = None
    while job["state"] == "running":
        line = f"{job.get('stage') or 'starting'} {_bar(job.get('done') or 0, job.get('total') or 0)}"
        if tty:
            print(f"\r{line:<78}", end="", flush=True)
        elif line != last and (job.get("done") or 0) % 10 == 0:
            print(line, flush=True)
        last = line
        time.sleep(0.5)
        job = client.get(f"/exports/{job['id']}")
    if tty:
        print()
    if job["state"] != "done":
        raise CliError(f"export {job['id']} {job['state']}" + (f": {job['error']}" if job.get("error") else ""))
    out = Path(args.output or ".")
    if out.is_dir() or not out.suffix:
        out.mkdir(parents=True, exist_ok=True)
        out = out / job["name"]
    response = client.request("GET", f"/exports/{job['id']}/download", stream=True, timeout=None)
    with open(out, "wb") as f:
        for chunk in response.iter_content(1 << 20):
            f.write(chunk)
    print(f"{out} ({job['size']} bytes)" + ("; open it with: tsq view " + str(out) if out.suffix == ".zip" else ""))
    return 0


def cmd_view(args, client: Client | None) -> int:
    """Serve an exported report zip on localhost, without unpacking it."""
    from testsuit.sequencer.export import serve_zip
    path = Path(args.zip)
    if not zipfile.is_zipfile(path):
        raise CliError(f"{path}: not a zip (an exported report as one .html file opens in a browser as it is)")
    serve_zip(path, args.port, not args.no_browser)
    return 0


def cmd_manual(args, client: Client) -> int:
    """The manual operations a run waits for; tick checks, answer done or failed."""
    run = client.get(f"/runs/{args.run}")
    waiting = run.get("awaiting_manual") or []
    if not waiting:
        print(f"run {run['id']} ({run['state']}) waits for no manual operation")
        return 1 if (args.tick or args.bypass or args.done or args.fail is not None) else 0
    step = args.step or (waiting[0]["step"] if len(waiting) == 1 else None)
    if step is None:
        print(f"run {run['id']} waits for {len(waiting)} manual operations, give --step: "
              + ", ".join(w["step"] for w in waiting), file=sys.stderr)
        return 2
    by = os.environ.get("USER", "")
    record = None
    for index in args.tick or []:
        record = client.post(f"/runs/{run['id']}/manual/{step}", json={"check": index - 1, "done": True, "by": by})
    if args.redo_from:
        client.post(f"/runs/{run['id']}/manual/{step}", json={"outcome": "redo", "from": args.redo_from,
                                                            "note": args.comment or "", "by": by})
        print(f"{step}: redone from {args.redo_from}")
        return 0
    for index, reason in args.bypass or []:
        record = client.post(f"/runs/{run['id']}/manual/{step}", json={"check": int(index) - 1, "bypass": True,
                                                                     "comment": reason, "by": by,
                                                                     "nonconform": args.nonconform})
    if args.done or args.fail is not None:
        body = {"outcome": "done", **({"note": args.comment} if args.comment else {})} if args.done else \
            {"outcome": "failed", "note": args.fail}
        record = client.post(f"/runs/{run['id']}/manual/{step}", json={**body, "by": by})
    if record is None:
        record = next(s for s in run.get("steps") or [] if
                      f"{s['phase']}:{s['id']}" + (f"[{s['slot']}]" if s.get("slot") else "") == step)
    manual = record.get("manual") or {}
    print(f"{step}: {manual.get('title')}" + (f" [{manual['outcome']}]" if manual.get("outcome") else " (waiting)"))
    if manual.get("text") and not manual.get("outcome"):
        print("\n".join("  " + line for line in manual["text"].splitlines()))
    for i, check in enumerate(manual.get("checks") or [], 1):
        mark = "x" if check.get("done") else ("bypassed" if check.get("bypassed") else " ")
        print(f"  {i}. [{mark}] {check['text']}" + (f": {check['comment']}" if check.get("comment") else ""))
    return 0


def cmd_step(args, client: Client) -> int:
    """An operator action on a step of the phase running now (SPEC section 9.7)."""
    action = args.action.replace("-", "_")
    run = client.post(f"/runs/{args.run}/steps/{args.step}/{action}",
                      json={"reason": args.reason or "", "by": os.environ.get("USER", "")})
    print(f"run {run['id']}: {args.action} {args.step}" + ("" if run["conform"] else " (run not conform)"))
    return 0


def cmd_step_mode(args, client: Client) -> int:
    body = {"mode": args.mode, **({"breakpoints": args.breakpoint} if args.breakpoint is not None else {})}
    run = client.post(f"/runs/{args.run}/control", json=body)
    control = run.get("control") or {}
    print(f"run {run['id']}: step mode {control.get('mode')}"
          + (f", breakpoints {', '.join(control.get('breakpoints') or [])}" if control.get("breakpoints") else "")
          + "".join(f"\n  paused after {p['step']} ({p['verdict']})" for p in run.get("paused") or []))
    return 0


def cmd_resources(args, client: Client) -> int:
    rows = client.get(f"/{args.command}")
    for row in rows:
        row["holder"] = [f"{h['run']}({h['mode']})" for h in row.pop("holders")]
    if args.command == "stages":
        table(rows, ["id", "type", "components", "holder", "maintenance", "hold", "note"])
    else:
        table(rows, ["id", "role", "extends", "host", "state", "lock", "holder", "maintenance", "hold", "detail"])
    return 0


def cmd_resource_action(args, client: Client) -> int:
    lab = client.get("/lab")
    kind = "stages" if args.name in lab["stages"] else "components"
    result = client.post(f"/{kind}/{args.name}/{args.command}", json={"note": args.note or ""})
    print(f"{args.name}: {args.command}" + (f" (released from {', '.join(result['released_from'])})"
                                            if result.get("released_from") else ""))
    return 0


def cmd_selftest(args, client: Client) -> int:
    results = client.post("/selftest", json={"target": args.target}, timeout=600)
    for r in results:
        print(f"{'ok    ' if r['ok'] else 'FAILED'} {r['component']}" + (f" -> {r['state']}" if r["state"] else ""))
        for check in r["checks"]:
            mark = ("skipped" if check["ok"] else "not run") if check.get("skipped") else "ok" if check["ok"] else "FAILED"
            print(f"    {mark} {check['id']}: {check['detail']}")
    return 0 if all(r["ok"] for r in results) else 1


def cmd_files(args, client: Client | None) -> int:
    target = Path(args.target)
    if target.suffix == ".zip" and target.is_file():           # a datapack zip on this host
        with zipfile.ZipFile(target) as z:
            root = z.namelist()[0].split("/")[0]
            prefix = f"{root}/{args.path}".rstrip("/")
            if args.get:
                data = z.read(prefix)
                Path(Path(args.path).name).write_bytes(data)
                print(f"{Path(args.path).name}: {len(data)} bytes")
                return 0
            for info in z.infolist():
                if info.filename.startswith(prefix):
                    print(f"{info.file_size:>12}  {info.filename[len(root) + 1:]}")
        return 0
    client = client or Client(server_url(args))
    params = {"results": "true"} if args.results else {}
    response = client.request("GET", f"/runs/{args.target}/files/{args.path}", params=params, stream=True)
    if response.headers.get("content-type", "").startswith("application/json"):
        for entry in response.json():
            print(f"{'' if entry['size'] is None else entry['size']:>12}  {entry['path']}{'/' if entry['dir'] else ''}")
        return 0
    if args.get:
        name = Path(args.path).name or f"{args.target}_results.zip"
        with open(name, "wb") as f:
            for chunk in response.iter_content(1 << 20):
                f.write(chunk)
        print(f"{name}: {Path(name).stat().st_size} bytes")
    else:
        for chunk in response.iter_content(1 << 16):
            sys.stdout.buffer.write(chunk)
    return 0


def cmd_lab(args, client: Client | None) -> int:
    if args.action == "status":
        if args.server or os.environ.get("TSQ_SERVER"):
            lab = Client(server_url(args)).get("/lab")
        else:
            from testsuit.sequencer.config import load_config
            from testsuit.sequencer.lab import load_lab
            config = load_config(args.config)
            lab = load_lab(config.lab_folder, config.inventory_file, config.lab.require_tag).describe()
        dump({"folder": lab["folder"], "inventory": lab["inventory"], "git": lab["git"],
              "warnings": lab["warnings"], "loaded_at": lab["loaded_at"]})
        print()
        table([{"id": k, **v} for k, v in lab["stages"].items()], ["id", "type", "components"])
        print()
        table([{"id": k, **v} for k, v in lab["components"].items()], ["id", "role", "extends", "host", "lock", "stages"])
        return 0
    client = client or Client(server_url(args))
    body = {"pull": args.pull is not None, "revision": args.pull or None}
    result = client.post("/lab/reload", json=body, timeout=300)
    git = result["git"] or {}
    print(f"lab reloaded: commit {git.get('commit')}" + (f", tag {git['tag']}" if git.get("tag") else "")
          + ("" if git.get("clean", True) else ", local changes"))
    for warning in result["warnings"]:
        print(f"warning: {warning}")
    return 0


def cmd_config(args, client: Client | None) -> int:
    from testsuit.sequencer.config import load_config
    config = load_config(args.config)
    print(f"configuration file: {config.file or '(none: defaults)'}")
    table([{"key": k, "value": v, "source": s} for k, v, s in config.describe()], ["key", "value", "source"])
    return 0


def cmd_validate(args, client: Client | None) -> int:
    """Resources, pipeline and commands of a datapack, fully expanded against the lab."""
    from testsuit.sequencer.config import load_config
    from testsuit.sequencer.intake import check_against_lab, load_datapack, unpack
    from testsuit.sequencer.lab import load_lab
    from testsuit.sequencer.scheduler import Availability, find_binding
    from testsuit.sequencer.sequencer import load_config_plugins

    config = load_config(args.config)
    load_config_plugins(config)
    inventory = Path(args.lab) if args.lab else config.inventory_file
    lab = load_lab(inventory.parent if args.lab else config.lab_folder, inventory, config.lab.require_tag)
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(args.file)
        if source.suffix in (".yml", ".yaml"):
            from testsuit.datatools.datapack.datapack_tools import datapack
            source = datapack(source, nocheck=True, output_dir=Path(tmp) / "zip", setup_config=args.setup_config)
        dp = load_datapack(unpack(source, Path(tmp) / "bag"))
        problems = check_against_lab(dp, lab)
        print(f"datapack {dp.name}: test definition {dp.testdef_id}, setup configuration {dp.configuration}, "
              f"{'conform' if dp.conform else 'NOT conform'}")
        binding, reasons = find_binding(lab, dp.needs, dp.pipeline.phases, Availability(), "validate",
                                        check_free=False)
        if binding is None:
            problems += [f"cannot bind: {r}" for r in reasons]
        else:
            print("binding (locks ignored): " + _cell(binding.as_dict()["stage_slots"]) + " " +
                  _cell(binding.as_dict()["slots"]))
        for flat in dp.phases:
            print(f"\nphase {flat.phase.id}" + (f" (needs {', '.join(flat.phase.needs)})" if flat.phase.needs else "")
                  + f": stage_slots {_cell(flat.phase.stage_slots)}, slots {_cell(flat.phase.slots)}")
            for part, steps in (("steps", flat.steps), ("finally", flat.finally_)):
                for fs in steps:
                    step = fs.step
                    what = step.action + (f" {step.do}" if step.action == "do" else "")
                    target = step.slot or (f"for_each {step.for_each}" if step.for_each else "-")
                    command = ""
                    if binding is not None and step.action == "do" and step.slot in binding.slots:
                        concrete = lab.inventory.components[binding.slots[step.slot]]
                        lc = dp.needs.slots[step.slot]
                        stage = lab.inventory.stages.get(binding.stage_slots.get(lc.stage_slot, ""))
                        resolved = lab.resolve(concrete, stage)
                        spec = resolved.commands.get(step.do)
                        if spec is None:
                            problems.append(f"phase {flat.phase.id}, step {fs.id}: slot {step.slot} "
                                            f"({concrete.id}, role {concrete.role}) has no command '{step.do}'")
                        command = f"  = {json.dumps(spec)} ({resolved.command_sources.get(step.do, '?')})"
                    if step.action == "python":
                        command = f"  = {json.dumps(step.python)}"
                    print(f"  {'finally ' if part == 'finally' else ''}{fs.id}: {what} on {target}"
                          + (f", needs {', '.join(fs.needs)}" if fs.needs else "") + command)
    if problems:
        print("\nproblems:")
        for p in problems:
            print(f"  - {p}")
        return 2
    print("\nvalid")
    return 0


def cmd_run_local(args, client: Client | None) -> int:
    """No daemon: run one datapack in the foreground, with the same storage and locks as tsqd."""
    import logging

    from testsuit.sequencer.config import load_config
    from testsuit.sequencer.sequencer import Sequencer, load_config_plugins

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    config = load_config(args.config)
    load_config_plugins(config)

    async def main() -> dict:
        seq = Sequencer(config, local=True)
        await seq.recover()                              # earlier local runs whose process died
        queue: asyncio.Queue = asyncio.Queue()
        seq.subscribers.add(queue)
        expected = yaml.safe_load(Path(args.baseline).read_text()) if args.baseline else None
        run = seq.submit(Path(args.datapack), args.priority, expected)
        print(f"run {run['id']} (local)" + ("" if run["conform"] else ", NON-CONFORM: " + "; ".join(run["nonconform"])))
        loop = asyncio.ensure_future(seq.scheduler_loop())
        try:
            while True:
                event = await queue.get()
                if event.get("run") != run["id"]:
                    continue
                if event["kind"] == "log" and args.follow:
                    for line in event["text"].splitlines():
                        print(f"[{event['step']}] {line}", flush=True)
                elif event["kind"] == "step":
                    print_step(event)
                elif event["kind"] == "run" and event.get("state") == "waiting_resources":
                    print(f"waiting: {event.get('waiting_for', '')}", flush=True)
                elif event["kind"] == "run" and event.get("state") in ("done", "cancelled"):
                    break
        except (KeyboardInterrupt, asyncio.CancelledError):
            await seq.abort(run["id"], "cancel")
            while seq.store.get_run(run["id"])["state"] not in ("done", "cancelled"):
                await asyncio.sleep(0.2)
        finally:
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)
        result = seq.store.get_run(run["id"])
        await seq.close()
        return result
    return verdict_exit(asyncio.run(main()))


# ── parser ───────────────────────────────────────────────────────────────────

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tsq", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", help="sequencer URL (default: $TSQ_SERVER, then server.listen of tsq.yml)")
    p.add_argument("--config", help="tsq.yml (default: $TSQ_CONFIG, then /etc/testsuit/tsq.yml)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("submit", help="queue a datapack")
    s.add_argument("datapack")
    s.add_argument("--priority", type=int, help="0 to 100 (default: $TSQ_PRIORITY, then queue.default_priority)")
    s.add_argument("--baseline", help="expected baseline YAML (default: the pipeline's baseline)")
    s.add_argument("--wait", action="store_true", help="wait for the verdict: exit code 0, 1 or 2")
    s.add_argument("--follow", action="store_true", help="stream the steps and logs (implies --wait)")
    s.set_defaults(fn=cmd_submit)

    s = sub.add_parser("submit-campaign", help="queue the datapacks of a generated campaign, as one campaign run")
    s.add_argument("campaign", help="the folder 'datapack <file>.campaign.yml' wrote (or its campaign.yml)")
    s.add_argument("--priority", type=int, help="for all its runs, instead of the campaign's")
    s.add_argument("--wait", action="store_true", help="wait for the campaign's verdict: exit code 0, 1 or 2")
    s.set_defaults(fn=cmd_submit_campaign)
    sub.add_parser("campaigns", help="campaign runs, newest first").set_defaults(fn=cmd_campaigns)
    s = sub.add_parser("campaign", help="a campaign run and its runs")
    s.add_argument("id", help="campaign run id, or a unique prefix")
    s.add_argument("--cancel", action="store_true", help="cancel its runs that have not ended")
    s.add_argument("--kill", action="store_true", help="kill its runs that have not ended")
    s.set_defaults(fn=cmd_campaign)

    s = sub.add_parser("priority", help="change the priority of a queued run")
    s.add_argument("run")
    s.add_argument("priority", type=int)
    s.set_defaults(fn=cmd_priority)

    s = sub.add_parser("validate", help="resources, pipeline and commands of a datapack against the lab")
    s.add_argument("file", help="datapack zip, or test definition YAML (a draft datapack is built)")
    s.add_argument("--lab", help="lab inventory file, instead of the one of tsq.yml")
    s.add_argument("--setup-config", help="setup configuration, for a test definition")
    s.set_defaults(fn=cmd_validate, local=True)

    s = sub.add_parser("run", help="run a datapack in the foreground (--local: no daemon)")
    s.add_argument("datapack")
    s.add_argument("--local", action="store_true", required=True)
    s.add_argument("--priority", type=int)
    s.add_argument("--baseline", help="expected baseline YAML")
    s.add_argument("--follow", action="store_true", help="print the logs too")
    s.set_defaults(fn=cmd_run_local, local=True)

    sub.add_parser("queue", help="waiting runs, priorities, what they wait for").set_defaults(fn=cmd_queue)
    s = sub.add_parser("runs", help="runs, newest first")
    s.add_argument("--state", help="comma-separated states (queued, running, done...)")
    s.set_defaults(fn=cmd_runs)
    s = sub.add_parser("show", help="a run and its steps")
    s.add_argument("run")
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("logs", help="the steps of a run, or the log of one")
    s.add_argument("run")
    s.add_argument("--step", help="'<phase>:<step>[<component>]' or a unique prefix")
    s.add_argument("-f", "--follow", action="store_true")
    s.set_defaults(fn=cmd_logs)
    for name in ("cancel", "kill"):
        s = sub.add_parser(name, help=f"{name} a run")
        s.add_argument("run")
        s.set_defaults(fn=cmd_abort)
    s = sub.add_parser("repost", help="queue an ended run again, its outputs cleaned")
    s.add_argument("run")
    s.set_defaults(fn=cmd_repost)
    s = sub.add_parser("rerun", help="run a datapack of a campaign run again (a new run, the earlier one kept)")
    s.add_argument("run")
    s.set_defaults(fn=cmd_rerun)
    s = sub.add_parser("review", help="the review of a run's results: show it, accept or reject a check, a step or "
                                      "the run, comment, revoke, sign off (--campaign: of a campaign run)")
    s.add_argument("id", help="run (or campaign run, with --campaign)")
    s.add_argument("--campaign", action="store_true", help="the review of a campaign run: --comment, --sign-off, --reopen")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--accept", metavar="TARGET", help="accept a result not met: run, step:<phase>:<step>, "
                                                     "check:<step>::[<scenario>/]<check> (needs --reason)")
    g.add_argument("--reject", metavar="TARGET", help="reject a result (needs --reason)")
    g.add_argument("--revoke", type=int, metavar="ENTRY", help="revoke a review entry (needs --reason)")
    g.add_argument("--sign-off", action="store_true", help="close the review: its reports are final")
    g.add_argument("--reopen", action="store_true", help="open a signed-off review again (needs --reason)")
    g.add_argument("--reapply", action="store_true", help="take the reviews of the earlier attempt that fit this re-run")
    s.add_argument("--reason", help="why")
    s.add_argument("--comment", help="a comment (alone: on --on, the run by default)")
    s.add_argument("--on", metavar="TARGET", help="what --comment alone is about (default: run)")
    s.add_argument("--ref", help="a reference: a ticket, a deviation report...")
    s.add_argument("--by", help="who (default: $TSQ_USER, else $USER; a reverse proxy's user wins)")
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("issue", aliases=["ticket"], help="issues bound to a run's results: list its failures and their known issues, bind "
                                      "an issue, create one (pre-filled), unbind one")
    s.add_argument("run", nargs="?")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--link", metavar="ISSUE", help="bind an existing issue: <source>#<id>, #<id>, a Jira key, or its URL")
    g.add_argument("--create", action="store_true", help="create an issue, pre-filled from the failure, and bind it")
    g.add_argument("--unbind", type=int, metavar="BINDING", help="unbind an issue (needs --reason)")
    s.add_argument("--on", metavar="TARGET", help="what the issue is about: run (default), step:<phase>:<step>, "
                                                  "check:<step>::[<scenario>/]<check>")
    s.add_argument("--source", help="the issue source of a new issue (default: its route, else the default source)")
    s.add_argument("--project", help="its project, in a group source")
    s.add_argument("--title", help="another title than the one proposed")
    s.add_argument("--label", action="append", help="a label more (repeat)")
    s.add_argument("--note", help="a note on a binding")
    s.add_argument("--reason", help="why (--unbind)")
    s.add_argument("--yes", action="store_true", help="create without showing it first")
    s.add_argument("--my-token", action="store_true", help="create it in your name: your token from $TSQ_ISSUES_TOKEN")
    s.add_argument("--refresh", action="store_true", help="read the issue states again")
    s.add_argument("--by", help="who (default: $TSQ_USER, else $USER; a reverse proxy's user wins)")
    s.set_defaults(fn=cmd_issue)
    s = sub.add_parser("export", help="export the report of a run or a campaign run, as reviewed: one HTML file "
                                      "(or a zip when too large), downloaded")
    s.add_argument("id", help="run (or campaign run, with --campaign)")
    s.add_argument("--campaign", action="store_true")
    s.add_argument("--synthetic", action="store_true", help="the report alone, its links to the sequencer")
    s.add_argument("--with-results", action="store_true", help="the results zips too (a zip)")
    s.add_argument("-o", "--output", help="file or folder (default: here)")
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("view", help="serve an exported report zip on localhost, without unpacking it")
    s.add_argument("zip")
    s.add_argument("--port", type=int, default=0)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_view, local=True)
    s = sub.add_parser("manual", help="the manual operation a run waits for: show it, tick checks, answer it")
    s.add_argument("run")
    s.add_argument("--step", help="'<phase>:<step>' when the run waits for several")
    s.add_argument("--tick", type=int, action="append", metavar="N", help="tick check N (from 1), repeatable")
    s.add_argument("--bypass", nargs=2, action="append", metavar=("N", "REASON"), help="bypass check N, and why")
    s.add_argument("--nonconform", action="store_true",
                   help="the checks bypassed by this command make the run non-conform (once the operation is done)")
    s.add_argument("--comment", help="a global comment with --done or --redo-from")
    s.add_argument("--redo-from", metavar="STEP", help="run that earlier step (one its redo_from lists) and the next "
                                                       "ones again, then ask again")
    answer = s.add_mutually_exclusive_group()
    answer.add_argument("--done", action="store_true", help="the operation is done (every check ticked or bypassed)")
    answer.add_argument("--fail", metavar="NOTE", help="the operation failed, and why")
    s.set_defaults(fn=cmd_manual)
    s = sub.add_parser("step", help="operator action on a step of the running phase: next (a paused step goes on), "
                                    "run-again (it and the steps that need it), continue (go on past it)")
    s.add_argument("run")
    s.add_argument("step", help="'<phase>:<step>' or '<step>'")
    s.add_argument("action", choices=["next", "run-again", "continue"])
    s.add_argument("--reason", help="why (required by continue)")
    s.set_defaults(fn=cmd_step)
    s = sub.add_parser("step-mode", help="pause a run after each step, or at breakpoints, or not")
    s.add_argument("run")
    s.add_argument("mode", choices=["off", "each", "breakpoints"])
    s.add_argument("--breakpoint", action="append", metavar="STEP", help="'<phase>:<step>', repeatable")
    s.set_defaults(fn=cmd_step_mode)
    for name in ("stages", "components"):
        sub.add_parser(name, help=f"{name}: monitored state, lock state, holder").set_defaults(fn=cmd_resources)
    for name in ("hold", "release", "maintenance", "resume"):
        s = sub.add_parser(name, help=f"{name} a stage or component")
        s.add_argument("name")
        s.add_argument("--note")
        s.set_defaults(fn=cmd_resource_action)
    s = sub.add_parser("selftest", help="platform self-test on demand")
    s.add_argument("target", nargs="?")
    s.set_defaults(fn=cmd_selftest)
    s = sub.add_parser("files", help="browse or download a run's (or a datapack zip's) contents")
    s.add_argument("target", help="run id, or datapack zip")
    s.add_argument("path", nargs="?", default="")
    s.add_argument("--get", action="store_true", help="download into the current folder")
    s.add_argument("--results", action="store_true", help="the results zip, not the run folder")
    s.set_defaults(fn=cmd_files, local=True)
    s = sub.add_parser("lab", help="lab status, or reload")
    s.add_argument("action", choices=["status", "reload"])
    s.add_argument("--pull", nargs="?", const="", default=None, metavar="REV",
                   help="reload: git pull first (optionally a revision)")
    s.set_defaults(fn=cmd_lab, local=True)
    sub.add_parser("config", help="settings in use, and where each comes from").set_defaults(fn=cmd_config, local=True)
    return p


def main(argv=None) -> int:
    from testsuit.sequencer.util import SequencerError
    args = parser().parse_args(argv)
    try:
        client = None if getattr(args, "local", False) else Client(server_url(args))
        return args.fn(args, client)
    except (CliError, SequencerError) as e:
        print(f"tsq: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:                                  # output piped into head, less...
        sys.stderr.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
