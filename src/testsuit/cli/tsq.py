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
    key = f"{event['phase']}:{event['id']}" + (f"[{event['component']}]" if event.get("component") else "")
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
        table([{"step": f"{s['phase']}:{s['id']}" + (f"[{s['component']}]" if s["component"] else ""), **s}
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


def cmd_resources(args, client: Client) -> int:
    rows = client.get(f"/{args.command}")
    for row in rows:
        row["holder"] = [f"{h['run']}({h['mode']})" for h in row.pop("holders")]
    if args.command == "stages":
        table(rows, ["id", "type", "components", "holder", "maintenance", "hold", "note"])
    else:
        table(rows, ["id", "type", "role", "host", "state", "lock", "holder", "maintenance", "hold", "detail"])
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
            print(f"    {'ok' if check['ok'] else 'FAILED'} {check['id']}: {check['detail']}")
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
        table([{"id": k, **v} for k, v in lab["components"].items()], ["id", "type", "role", "host", "lock", "stages"])
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
            print("binding (locks ignored): " + _cell(binding.as_dict()["stages"]) + " " +
                  _cell(binding.as_dict()["components"]))
        for flat in dp.phases:
            print(f"\nphase {flat.phase.id}" + (f" (needs {', '.join(flat.phase.needs)})" if flat.phase.needs else "")
                  + f": stages {_cell(flat.phase.stages)}, components {_cell(flat.phase.components)}")
            for part, steps in (("steps", flat.steps), ("finally", flat.finally_)):
                for fs in steps:
                    step = fs.step
                    what = step.action + (f" {step.do}" if step.action == "do" else "")
                    target = step.component or (f"for_each {step.for_each}" if step.for_each else "-")
                    command = ""
                    if binding is not None and step.action == "do" and step.component in binding.components:
                        concrete = lab.inventory.components[binding.components[step.component]]
                        lc = dp.needs.components[step.component]
                        stage = lab.inventory.stages.get(binding.stages.get(lc.stage, ""))
                        resolved = lab.types.resolve(concrete, stage, dp.needs.component_types, lc.type, lc.commands)
                        spec = resolved.commands.get(step.do)
                        if spec is None:
                            problems.append(f"phase {flat.phase.id}, step {fs.id}: {step.component} "
                                            f"({concrete.type}) has no command '{step.do}'")
                        command = f"  = {json.dumps(spec)} ({resolved.command_sources.get(step.do, '?')})"
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
