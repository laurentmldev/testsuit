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

"""JSON Schemas of the files the Editor edits (README section 13.2), with their help.

The descriptions are the contextual help of the Editor: the YAML view shows them on hover and
completion, and the forms show them next to each field. ``defaultSnippets`` are the blocks the
YAML view proposes when a new item is started.

``schemas(context)`` fills in what depends on the lab and the workspace: slot types, roles,
stage types, transports, commands, importers, generators, slots.
"""
from __future__ import annotations

import copy
from typing import Any

from testsuit.sequencer.inventory import ICON_EXTENSIONS, ICONS


def h(text: str, **extra: Any) -> dict:
    """A schema fragment with its help, in plain text and Markdown."""
    plain = text.replace("`", "")
    return {"description": plain, "markdownDescription": text, **extra}


DURATION = h("A duration: `30s`, `2m`, `1h30m`, or a number of seconds.",
             type=["string", "number"], examples=["30s", "2m", "15m"])

STRICT_NOTE = ("Its lines run in one shell (a `cd` or `export` holds for the next lines) and it stops at the first "
               "line that fails (`set -e`); `|| true` lets a line fail.")

TEMPLATE_NOTE = ("Jinja2 templates are rendered at run time: `{{ component.input_dir }}`, `{{ component.output_dir }}`, "
                 "`{{ component.role }}`, `{{ component.host }}`, `{{ component.vars.<variable> }}`, `{{ remote_root }}`, "
                 "`{{ run.id }}`, and every key of the datapack (`{{ scenario.duration_s }}`).")

EXPECT = h("What a successful command gives. `exit`: accepted exit codes (default `[0]`, `any` for all); "
           "`stdout` or `stderr`: a regular expression the output must match. A command that ran without the "
           "expected result is `failed`; one that could not run or did not finish is `error`.",
           type="object", additionalProperties=False,
           properties={"exit": h("Accepted exit codes, or `any`.", type=["array", "integer", "string"],
                                 items={"type": ["integer", "string"]}, examples=[[0], [0, 1], "any"]),
                       "stdout": h("Regular expression the standard output must match (multi-line).", type="string"),
                       "stderr": h("Regular expression the error output must match.", type="string")},
           defaultSnippets=[{"label": "exit codes", "body": {"exit": [0]}}])

HTTP = h(
    "An HTTP request (a REST API call): a URL, or a mapping. Sent from the sequencer host, or with `via` from "
    "another component (with curl there). It passes when the answer is the expected one: by default a status "
    "below 400. Templates allowed in every field.",
    type=["string", "object"], additionalProperties=False,
    properties={
        "url": h("The URL (`{{ component.vars.api }}/jobs`).", type="string"),
        "method": h("HTTP method (default GET).", type="string",
                    enum=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
        "headers": h("Request headers. A header whose name looks secret (`Authorization`, `X-Api-Key`, "
                     "token...) is hidden in the logs: take its value from a variable.", type="object",
                     additionalProperties={"type": "string"},
                     examples=[{"Authorization": "Bearer {{ component.vars.token }}"}]),
        "params": h("Query string parameters.", type="object", additionalProperties={"type": ["string", "number", "boolean"]}),
        "json": h("JSON body (sets `Content-Type: application/json`).", type=["object", "array", "string", "number", "boolean"]),
        "body": h("Raw body, as written.", type="string"),
        "timeout": DURATION,
        "verify": h("Check the server's TLS certificate (default true; false for a self-signed lab server).",
                    type="boolean"),
        "extract": h("Take a part of the JSON answer, with a jq-style path: `.state`, `.jobs[0].state`, `.jobs[-1]`, "
                     "`.jobs[].id` (a list), `.\"a key\".x`. The expectations, a probe's states and the logs see that "
                     "part (a string as is, like `jq -r`, anything else as JSON); `save` keeps the whole answer.",
                     type="string", examples=[".status.state", ".jobs[0].state", ".items[].name"]),
        "expect": h("The expected answer (default: a status below 400).", type="object", additionalProperties=False,
                    properties={"status": h("Accepted status codes.", type=["integer", "array"], items={"type": "integer"},
                                            examples=[200, [200, 201]]),
                                "regex": h("Regular expression the body (or the extracted part) must match.", type="string"),
                                "json": h("Values the JSON answer must contain (a subset: other fields are ignored).",
                                          type=["object", "array", "string", "number", "boolean"],
                                          examples=[{"state": "started"}])}),
        "save": h("Keep the answer with the results: `data/<slot>/http/<file>` (commands and steps).",
                  type="string", examples=["answer.json"]),
    },
    defaultSnippets=[{"label": "GET", "body": {"url": "${1:http://localhost:8080/api/status}"}},
                     {"label": "POST JSON", "body": {"url": "${1:http://localhost:8080/api/jobs}", "method": "POST",
                                                     "json": {}, "expect": {"status": [200, 201]}}}])

WAIT_KINDS = {
    "state": h("The monitored state of the component (section 7.5): `running`, `idle`, `configured`, `broken`, "
               "`offline`, `unknown`, or a list of them.", type=["string", "array"],
               examples=["running", "idle", ["idle", "configured"]]),
    "tcp": h("A TCP port accepting connections: `host:port`, or `{host, port}` (host: the component's by default).",
             type=["string", "object", "integer"], examples=["10.42.0.10:5000", {"port": 8080}]),
    "http": HTTP,
    "file": h("A file or folder that exists on the component.", type="string",
              examples=["{{ component.output_dir }}/done"]),
    "log": h("A line of a log file on the component matching a regular expression: `{file, regex}`.",
             type="object", properties={"file": {"type": "string"}, "regex": {"type": "string"}},
             required=["file", "regex"],
             defaultSnippets=[{"label": "log line", "body": {"file": "{{ component.output_dir }}/${1:app.log}",
                                                             "regex": "${2:READY}"}}]),
    "cmd": h("A shell command run on the component until it exits with 0.", type="string"),
}

WAIT_FOR = h("Wait until a check passes, polling every `interval` (default 0.5 s) until `timeout` "
             "(default 60 s, then the step is `error`). Exactly one check kind: " +
             ", ".join(f"`{k}`" for k in WAIT_KINDS) + ", or a plugin ready check. "
             "`slot` names the slot checked when the step has none.",
             type="object",
             properties={**WAIT_KINDS, "timeout": DURATION, "interval": DURATION,
                         "slot": h("The slot checked (in a step without `slot`).", type="string")},
             defaultSnippets=[
                 {"label": "state running", "body": {"state": "running", "timeout": "20s"}},
                 {"label": "log line", "body": {"log": {"file": "{{ component.output_dir }}/${1:app.log}",
                                                        "regex": "${2:READY}"}, "timeout": "30s"}},
                 {"label": "tcp port", "body": {"tcp": "${1:host}:${2:port}", "timeout": "30s"}},
                 {"label": "http health", "body": {"http": {"url": "${1:http://localhost:8080/health}"},
                                                   "timeout": "30s"}}])

PYTHON = h("A Python script, run like a shell command: on the component, or on the sequencer host for a step "
           "without one (with the daemon's interpreter). Give `script` (a path on the target, usually an input of the "
           "component) or inline `code`. The keys of the step are in the JSON file named by `TSQ_KEYS`, and already "
           "loaded as `keys` in the script. Exit 0 succeeds, as for `run` (`expect`, `timeout`, `detach` apply); an "
           "uncaught exception makes the step `error`, not `failed`.", type="object", additionalProperties=False,
           properties={
               "script": h("Path of the script on the target: `{{ component.input_dir }}/check.py` for a script "
                           "deployed with the component's inputs, or relative to the run folder on the sequencer "
                           "host. " + TEMPLATE_NOTE, type="string"),
               "code": h("Inline Python code (not a template: read the keys from `keys`). `sys.exit(1)` fails "
                         "the step.", type="string"),
               "args": h("Arguments of the script (`sys.argv[1:]`), templates allowed.", type="array",
                         items={"type": ["string", "number"]}),
               "interpreter": h("Python interpreter to use: default the component's `tsq_python` variable, "
                                "else `python3`.", type="string", examples=["python3", "{{ component.vars.venv }}/bin/python"]),
           },
           defaultSnippets=[{"label": "inline code", "body": {"code": "import sys\n${1:print(keys['run']['id'])}\nsys.exit(0)\n"}},
                            {"label": "script", "body": {"script": "{{ component.input_dir }}/${1:check.py}", "args": []}}])

COMMAND = h(
    "What a command does, one of:\n"
    "- `run`: a shell command on the component (over its transport), with `timeout`, `detach`, `expect`, `env`;\n"
    "- `python`: a Python script, `script` or inline `code`, with the same options as `run`;\n"
    "- `http`: an HTTP request (a REST API call): `url`, `method`, `headers`, `json` or `body`, `expect`, `save`;\n"
    "- `wait_for`: a ready check (state, tcp, http, file, log, cmd);\n"
    "- `signal`: send a signal to the processes the run started on the component (`TERM`, then `KILL` after "
    "`timeout`, default 10 s);\n"
    "- `builtin`: a built-in command (`deploy`, `collect`, `kill`);\n"
    "- a plugin step type, given by its name.\n\n" + TEMPLATE_NOTE,
    type="object",
    properties={
        "run": h("Shell command run on the component. " + STRICT_NOTE + " " + TEMPLATE_NOTE, type="string"),
        "python": PYTHON,
        "http": HTTP,
        "detach": h("Start the command and go on without waiting for it to end (a server, a simulation). "
                    "Its processes are recorded, then stopped by `signal` or `kill`.", type="boolean"),
        "via": h("Run the command on another component, for a component the sequencer cannot reach itself (in a "
                 "sub-network behind the stage server): a role of its stage (`stage_server`), else of an ad hoc component, or another host. It "
                 "gets `{{ component.host }}` (this component) and `{{ via.host }}`; its processes are recorded "
                 "there, so `signal`, `kill` and the end of the run stop them. A `run` or `http` command (sent with curl "
                 "from there).",
                 type="string"),
        "timeout": DURATION,
        "expect": EXPECT,
        "env": h("Environment variables of the command (templates allowed).", type="object",
                 additionalProperties={"type": ["string", "number", "boolean"]}),
        "wait_for": WAIT_FOR,
        "signal": h("Signal sent to the processes the run started on the component, `TERM` or `INT` for example. "
                    "Those still alive after `timeout` (default 10 s) get `KILL`.", type="string",
                    examples=["TERM", "INT"]),
        "builtin": h("A built-in command: `deploy` (push the datapack inputs to `<remote_root>/<component>/input`), "
                     "`collect` (pull `<remote_root>/<component>/output` back into the results), `kill` (kill every "
                     "process tree the run started).", type="string", enum=["deploy", "collect", "kill"]),
    },
    defaultSnippets=[
        {"label": "run a shell command", "body": {"run": "${1:command}", "timeout": "${2:30s}"}},
        {"label": "start a process (detached)", "body": {"run": "${1:command}", "detach": True}},
        {"label": "run a Python script", "body": {"python": {"script": "{{ component.input_dir }}/${1:script.py}"}}},
        {"label": "wait for a log line", "body": {"wait_for": {"log": {"file": "{{ component.output_dir }}/${1:app.log}",
                                                                        "regex": "${2:READY}"}, "timeout": "30s"}}},
        {"label": "stop: signal TERM", "body": {"signal": "TERM", "timeout": "10s"}}])

# What the engine and the sequencer do with well-known command names
COMMAND_NAMES = {
    "deploy": "Pushes the component's datapack inputs to `<remote_root>/<component>/input` "
              "(built in for `linux_system`). Expected state afterwards: `configured`.",
    "collect": "Pulls `<remote_root>/<component>/output` back into the results datapack (built in for `linux_system`). "
               "Usually in `finally`, so outputs are kept whatever happened.",
    "kill": "Kills what the run started on the component. Called by `tsq kill`, and usually in `finally` "
            "when the normal stop failed (built in for `linux_system`).",
    "start": "Starts the component's application. Expected state afterwards: `running`. Often `detach: true`.",
    "stop": "Stops the application cleanly. Called on `tsq cancel` for components with live processes. "
            "Expected state afterwards: `idle`.",
    "ready": "Waits until the application is ready, usually a `wait_for` on a log line or a port.",
    "install": "Installs or flashes what was deployed (firmware, packages) before starting.",
    "version": "Prints the version of what runs on the component: recorded in the run part of the results baseline.",
}

COMMANDS = h("How this component implements the commands of its role, by name (pipeline steps run them as "
             "`do: <name>`). A component inherits its parent's implementations and overrides those it redefines "
             "(section 7.4).", type="object",
             properties={name: {**COMMAND, "markdownDescription": f"**{name}**: {text}\n\n" + COMMAND["markdownDescription"],
                                "description": f"{name}: {text.replace('`', '')}"}
                         for name, text in COMMAND_NAMES.items()},
             additionalProperties=COMMAND)

STATES = ("offline", "idle", "configured", "running", "broken", "unknown")
STATE_HELP = {"offline": "unreachable", "idle": "reachable, nothing running", "configured": "inputs deployed, not started",
              "running": "started", "broken": "reachable but faulty: not used for new runs",
              "unknown": "no probe can tell"}

STATUS = h(
    "How the state monitor finds the component's state (section 7.5): a `probe` command whose output and exit "
    "status are matched against `states`, first match wins. `interval`: time between probes (default 30 s, "
    "more often during runs).",
    type="object",
    properties={
        "probe": h("The probe: `{run: <shell command>}`, `{http: <request>}` (its states match the answer's body; "
                   "`exit` is 0 when the answer is the expected one, else the HTTP status), or a plugin state probe.",
                   type="object", properties={"run": h("Shell command run on the component (locally with "
                                                       "`transport: local`, on the `via` component with `via`). "
                                                       "A list of commands: several checks, each run whatever the "
                                                       "others gave and OK when it exits with 0; the rules match "
                                                       "their outputs one after the other, `exit` rules the first "
                                                       "non-zero exit status, each check in a shell of its own. "
                                                       + STRICT_NOTE,
                                                       type=["string", "array"], items={"type": "string"}),
                                              "http": HTTP, "timeout": DURATION}),
        "states": h("Rules giving a state from the probe result, first match wins: `{regex: ...}` on the output, "
                    "or `{exit: 0 | nonzero}`.", type="object",
                    properties={s: h(f"`{s}`: {STATE_HELP[s]}.", type="object",
                                     properties={"regex": {"type": "string"},
                                                 "exit": {"type": ["integer", "string"], "examples": [0, "nonzero"]}})
                                for s in STATES},
                    additionalProperties=False),
        "interval": DURATION,
        "via": h("Run the probe on another component, for a component the sequencer cannot reach itself (in a "
                 "sub-network behind the stage server): a role of its stage (`stage_server`), else of an ad hoc component, or another host. Its "
                 "commands get `{{ component.host }}` (the component probed) and `{{ via.host }}`; the self-test's "
                 "`run` checks run there too. Map a powered-off component to `idle` in `states`, not `offline`, "
                 "so that its stage stays available.", type="string"),
        "builtin": h("`linux_system`: `offline` when the transport fails, `running` while processes the "
                     "sequencer started are alive, `idle` otherwise.", type="string", enum=["linux_system"]),
    },
    defaultSnippets=[{"label": "probe with a command", "body": {
        "probe": {"run": "${1:systemctl is-active my-service}"},
        "states": {"running": {"regex": "^active"}, "broken": {"regex": "^failed"}, "idle": {"regex": "^inactive"}}}},
        {"label": "ping (offline when it fails)", "body": {"probe": {"run": "ping -c 1 -W 1 {{ component.host }}"},
                                                           "states": {"running": {"exit": 0},
                                                                      "offline": {"exit": "nonzero"}}}},
        {"label": "probe through the stage server (a sub-network)", "body": {
            "via": "${1:stage_server}",
            "probe": {"run": "${2:psu-ctl get ch1}; ping -c 1 -W 1 {{ component.host }} >/dev/null && echo UP || echo DOWN"},
            "states": {"running": {"regex": "ON[\\s\\S]*UP"}, "broken": {"regex": "ON[\\s\\S]*DOWN"},
                       "idle": {"regex": "OFF"}}}}])

SELFTEST = h(
    "Checks run on every bound component before each run, once its resources are locked and before anything is "
    "deployed (section 7.6). A component adds to its parent's checks; a check with the same `id` replaces it. "
    "When a check fails, nothing is deployed and the run goes back to the queue.",
    type="array",
    items={"type": "object", "required": ["id"],
           "properties": {"id": h("Name of the check, unique in the component chain.", type="string"),
                          "run": h("Shell command run on the component: exit 0 passes. " + STRICT_NOTE, type="string"),
                          "timeout": DURATION, "expect": EXPECT,
                          "builtin": h("A built-in check: `status`, `login`, `remote_root`, `disk`, `clock`.",
                                       type="string", enum=["status", "login", "remote_root", "disk", "clock"])},
           "defaultSnippets": [{"label": "check command", "body": {"id": "${1:name}", "run": "${2:command}",
                                                                   "timeout": "20s"}}]})

COMPONENT = h(
    "A lab component: how it implements the commands of a role, how its state is monitored, its self-test and its "
    "transport. A component `extends` another one and redefines only what differs (section 7.2). Inventory hosts "
    "extend a component with `tsq_extends`.",
    type="object", additionalProperties=False,
    properties={
        "extends": h("The parent component: `linux_system` for anything reachable over SSH (deploy, collect and "
                     "kill built in), `component` for something that is not a Linux system (a switch, an instrument "
                     "driven through its own API), or another component of the library.", type="string"),
        "roles": h("The roles it supports (section 7.2): it must implement every command each lists. Inherited by "
                   "the components extending it, unless they list their own. A host extending a component with "
                   "one role has that role; with several, it picks one with `tsq_role`. Leave out for a base "
                   "component (`rpi5` hardware) whose hosts give their role.", type="array",
                   items={"type": "string"}),
        "description": h("What this component is: shown in the Editor when a host extends it.", type="string"),
        "transport": h("How the sequencer talks to this component: `ssh` (default), `local` (on the "
                       "sequencer host), or a plugin transport. The inventory variable `tsq_transport` overrides it.",
                       type="string"),
        "commands": COMMANDS,
        "status": STATUS,
        "selftest": SELFTEST,
        "collect": h("Filters applied to what `collect` brings back: `include` and `exclude` lists of glob patterns, "
                     "relative to the output folder.", type="object",
                     properties={"include": {"type": "array", "items": {"type": "string"}},
                                 "exclude": {"type": "array", "items": {"type": "string"}}}),
    },
    defaultSnippets=[{"label": "Linux computer with a service", "body": {
        "extends": "linux_system", "description": "${1:what it is}",
        "commands": {"start": {"run": "sudo systemctl start ${2:service}", "timeout": "30s"},
                     "stop": {"run": "sudo systemctl stop ${2:service}", "timeout": "30s"}},
        "status": {"probe": {"run": "systemctl is-active ${2:service}"},
                   "states": {"running": {"regex": "^active"}, "idle": {"regex": "^inactive"}}}}},
        {"label": "process started by the sequencer", "body": {
            "extends": "linux_system", "description": "${1:what it is}",
            "commands": {"start": {"run": "cd {{ component.input_dir }} && exec ${2:./app}", "detach": True},
                         "stop": {"signal": "TERM", "timeout": "10s"}}}},
        {"label": "monitored only (switch, instrument)", "body": {
            "extends": "component", "transport": "local",
            "status": {"probe": {"run": "ping -c 1 -W 1 {{ component.host }}"},
                       "states": {"running": {"exit": 0}, "offline": {"exit": "nonzero"}}}}}])

ROLE = h(
    "A lab role: an abstract definition of a component, the commands it offers, by name, with what each does. "
    "Lab components implement them; datapacks bind their slots by role and run its commands. "
    "A role `extends` another one and adds commands (section 7.1).",
    type="object", additionalProperties=False,
    properties={
        "extends": h("The parent role: its commands are offered too. `linux_system` (deploy, collect, kill) for "
                     "anything reachable over SSH, `component` (no command) otherwise.", type="string"),
        "description": h("What this role is: shown in the Editor when a role is chosen.", type="string"),
        "inputs": h("The inputs a component of this role expects, in its input folder: slot types "
                    "bound to the role get them by default (section 7.1). A role extending another one adds to its "
                    "inputs, or redefines one by name.", type="array", items=h(
                        "An input the role expects.", type="object", required=["name"], additionalProperties=False,
                        properties={
                            "name": h("Its path in the component's input folder (`config.yml`, `app/`): the `target` "
                                      "of the datapack input. A folder ends with `/`.", type="string"),
                            "type": h("`file` (default) or `folder`.", type="string", enum=["file", "folder"]),
                            "optional": h("The component works without it.", type="boolean"),
                            "description": h("What it is and how to make it (Markdown, as long as needed): shown in "
                                             "the Editor next to the datapack input.", type="string")})),
        "commands": h("The commands the role offers, by name, with what each does. Implementations are in the "
                      "components.", type="object",
                      properties={name: h(f"**{name}**: {text}", type=["string", "null"])
                                  for name, text in COMMAND_NAMES.items()},
                      additionalProperties=h("What the command does.", type=["string", "null"])),
    },
    defaultSnippets=[{"label": "application on a Linux computer", "body": {
        "extends": "linux_system", "description": "${1:what it is}",
        "commands": {"start": "Starts the application.", "stop": "Stops the application.",
                     "ready": "Waits until the application is ready."}}}])

LIBRARY_FILE = {
    "$schema": "http://json-schema.org/draft-07/schema#", "title": "Lab library file",
    **h("A file of the lab library (the `tsq_library` folders of the inventory): `roles` (abstract: the commands "
        "offered) and/or `components` (concrete: their implementations), by name."),
    "type": "object", "additionalProperties": False, "anyOf": [{"required": ["roles"]}, {"required": ["components"]}],
    "properties": {"roles": h("Lab roles, by name. The built-in `component` and `linux_system` cannot be redefined.",
                              type="object", additionalProperties=ROLE),
                   "components": h("Lab components, by name. The built-in `component` and `linux_system` cannot be "
                                   "redefined.", type="object", additionalProperties=COMPONENT)},
}

# ── lab inventory ──────────────────────────────────────────────────────────

LOCK = h("Lock mode: `exclusive` (default): one datapack at a time; `shared`: several datapacks at once, for a "
         "passive component nobody configures (a network switch); `none`: never locked (the sequencer host).",
         type="string", enum=["exclusive", "shared", "none"])

VARS = {
    "ansible_host": h("Address the sequencer connects to: an IP address, a DNS name, or a name of the lab's host "
                      "names (`tsq_hosts` in `all.vars`), which the sequencer translates to its address (default: "
                      "the host's name in the inventory). Commands get the address as `{{ component.host }}`, and "
                      "the name as `{{ component.host_alias }}`.",
                      type="string", examples=["10.42.1.10", "localhost", "bench-pi"]),
    "ansible_user": h("SSH user (key-based authentication only).", type="string", examples=["tsq"]),
    "ansible_port": h("SSH port (default 22).", type="integer"),
    "ansible_ssh_private_key_file": h("Private key used to log in (no password prompt).", type="string",
                                      examples=["~/.ssh/id_lab"]),
    "tsq_ssh_host_key": h(
        "Pins the host's SSH key: its public key (`ssh-ed25519 AAAA...`) or its `SHA256:` fingerprint, or a list of "
        "them, one per line in the editor. When given, the server must present one of them, and no `known_hosts` "
        "file is read for that host: a changed or spoofed key fails the login. Without it, the `known_hosts` of the "
        "user running the sequencer is checked (in a container, often empty: the login then fails with `Host key "
        "is not trusted`).\n\n"
        "How to get it, best first:\n"
        "- **On the server itself** (the most trustworthy): `cat /etc/ssh/ssh_host_ed25519_key.pub`, and copy the "
        "line (the trailing comment, such as `root@server`, can stay). Other key types are in the other "
        "`/etc/ssh/ssh_host_*_key.pub` files.\n"
        "- **From a machine that already logs in to it** (for example the docker host): `ssh-keygen -F <address> "
        "-f ~/.ssh/known_hosts` (`-F '[<address>]:<port>'` for a port other than 22) prints lines "
        "`<address> ssh-ed25519 AAAA...`: copy them from `ssh-ed25519` on, without the address.\n"
        "- **A fingerprint instead**: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server prints "
        "`256 SHA256:... root@server (ED25519)`: copy the `SHA256:...` part.\n\n"
        "Avoid `ssh-keyscan` from the sequencer's network alone: it trusts whatever answers. If you use it, "
        "compare its fingerprint with the one the server's console shows.",
        type=["string", "array"], items={"type": "string"},
        examples=["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...", "SHA256:nThbg6kXUpJWGl7E1IGOCspRomTxdCARLviKw6E5SY8"]),
    "tsq_role": h("Role of the component, a role of the lab library (section 7.1): the commands it must implement. "
                  "Not needed when the component it extends supports one role (it has that one); one of them when "
                  "it supports several; any role when it lists none. Datapacks bind their slots by "
                  "role (`role: pilot` binds a host with role pilot, or a role extending it, in the stage). Several "
                  "components of a stage can have the same role: each slot gets its own.",
                  type="string"),
    "tsq_extends": h("The lab component this host is (section 7.2): a component of the library, or another host. "
                     "It gives the implementations of the commands, the status probe and the self-test "
                     "(default `linux_system`).", type="string"),
    "tsq_lock": LOCK,
    "tsq_tags": h("Tags, on a host (the component) or a stage group (the stage): a datapack slot or stage slot "
                  "listing `tags` only binds to a component or stage having them all. The id is always a tag.",
                  type="array", items={"type": "string"}, examples=[["calibrated", "rev_b"]]),
    "tsq_label": h("Name shown in the UI and the CLI (default: the host name).", type="string"),
    "tsq_icon": h("How the Lab view draws the component: one of the pictures it has ("
                  + ", ".join(f"`{i}`" for i in ICONS) + "), or an image file of the lab repository ("
                  + ", ".join(ICON_EXTENSIONS) + "), relative to the inventory. Default: a plain box. Set on a group, "
                  "it applies to its hosts.", type="string", examples=["switch", "icons/front_board.png"]),
    "tsq_links": h("The components it is wired to that its configuration does not show, drawn as links on the Lab view "
                   "(a tap between a switch and a board, a serial cable). Links its commands and probe give "
                   "(`via`, `{{ stage.roles.<role> }}`, `{{ lab.components.<host> }}`) are drawn without it. A list "
                   "of hosts, or a mapping host: label. For the diagram only: the sequencer does nothing with "
                   "these links (no command, probe, lock or binding uses them).",
                   type=["array", "object", "string"], items={"type": "string"},
                   additionalProperties={"type": "string"}, examples=[["switch_1"], {"au": "USB GPIO adapter"}]),
    "tsq_description": h("What it is, shown in the editor and on the Lab page: in `all.vars`, the lab; on a stage "
                         "group, the stage; on a host, the component. Not inherited: each level has its own.",
                         type="string"),
    "tsq_transport": h("How the sequencer talks to the component: `ssh` (default) or `local` (commands run on the "
                       "sequencer host, as the user running it), or a plugin transport.", type="string"),
    "tsq_shell": h("The shell commands run under on the component, for example `bash` or `bash -l` (login profile "
                   "read). Default: `/bin/sh` for steps and commands, the login shell for probes and self-tests.",
                   type="string", examples=["bash", "bash -l"]),
    "tsq_remote_root": h("Folder on the component under which each slot bound to it gets "
                         "`<root>/<slot>/input` and `output`.", type="string", examples=["/opt/testrun"]),
    "tsq_commands": h("Command overrides, by name. On a stage group they apply to every component of the stage; on a "
                      "host, to that host only. They win over the component it extends (section 7.4).",
                      type="object", additionalProperties=COMMAND),
    "tsq_status": STATUS,
    "tsq_selftest": SELFTEST,
    "tsq_collect": h("Filters applied to what `collect` brings back from this host (`include`, `exclude`).",
                     type="object"),
}

HOST_VARS = h("Variables of a host. `tsq_` variables are the sequencer's; any other variable is kept and reachable "
              "in templates as `{{ component.vars.<name> }}`. A variable can hold keys of its own (`custom_grp: {keyA: v}`, "
              "or `custom_grp.keyA: v`), read as `{{ component.vars.custom_grp.keyA }}`.", type=["object", "null"], properties=VARS,
              defaultSnippets=[{"label": "SSH host", "body": {"ansible_host": "${1:10.0.0.10}",
                                                              "tsq_role": "${2:role}"}},
                               {"label": "local process", "body": {"ansible_host": "localhost", "tsq_transport": "local",
                                                                   "tsq_role": "${1:role}"}}])

GROUP_VARS = h("Variables of the group. A group with `tsq_stage_type` is a stage; its variables apply to its "
               "components (host variables win).", type=["object", "null"],
               properties={**VARS, "tsq_stage_type": h(
                   "Makes this group a **stage** of that type: datapacks ask for a stage by type "
                   "(`stages: {train: {type: toytrain}}`) and get any free stage of it.", type="string")})


def _group(depth: int) -> dict:
    group = {"type": ["object", "null"], "additionalProperties": False,
             "properties": {"vars": GROUP_VARS,
                            "hosts": h("Hosts of the group. In a stage: its components. A host in several stage "
                                       "groups is a shared component.", type=["object", "null"],
                                       additionalProperties=HOST_VARS)}}
    if depth < 3:
        group["properties"]["children"] = h("Sub-groups: stages, or plain groups sharing variables.",
                                            type=["object", "null"], additionalProperties=_group(depth + 1),
                                            defaultSnippets=[{"label": "stage", "body": {
                                                "${1:stage_1}": {"vars": {"tsq_stage_type": "${2:type}"},
                                                                 "hosts": {"${3:host}": {"tsq_extends": "${4:component}"}}}}}])
    return group


INVENTORY = {
    "$schema": "http://json-schema.org/draft-07/schema#", "title": "Lab inventory",
    **h("The lab inventory, in the Ansible YAML inventory format (section 6): stages are groups with "
        "`tsq_stage_type`, their hosts are their components; hosts directly under `all` are ad hoc components."),
    "type": "object", "required": ["all"], "additionalProperties": False,
    "properties": {"all": {**h("The root group."), **_group(0),
                           "properties": {
                               **_group(0)["properties"],
                               "vars": h("Variables of every host (defaults).", type=["object", "null"],
                                         properties={**VARS, "tsq_library": h(
                                             "Folders of the lab library (roles and components), relative to "
                                             "this file.", type=["array", "string"], items={"type": "string"},
                                             examples=[["roles/", "components/"]]),
                                             "tsq_hosts": h(
                                             "The lab's host names: a table `name: address` (an IP address or a "
                                             "DNS name). A component's address (`ansible_host`) can be one of "
                                             "these names: the sequencer translates it itself wherever it uses "
                                             "the address (SSH, HTTP calls, `{{ component.host }}` in commands, "
                                             "so a command run from another machine still gets the real address). "
                                             "Nothing is written to `/etc/hosts`, and other programs on the "
                                             "sequencer host do not know these names. A command can read any of "
                                             "them: `{{ lab.hosts.<name> }}`.",
                                             type=["object", "null"], additionalProperties={"type": "string"},
                                             examples=[{"bench-pi": "10.0.1.20", "psu-a": "10.0.1.31"}])}),
                               "hosts": h("Ad hoc components: in no stage, bound by role on their own "
                                          "(`role: sequencer` for the analysis on the sequencer host, simulations...).",
                                          type=["object", "null"], additionalProperties=HOST_VARS)}}},
}

# ── datapack side: setup ───────────────────────────────────────────────────

INPUT = h(
    "An input of the slot: `source` (file, folder or glob pattern, relative to the test definition or setup "
    "folder) put at `target` in the slot's input folder, by `method`.",
    type="object", required=["source", "target"], additionalProperties=False,
    properties={
        "source": h("File, folder or glob pattern, absolute or relative to the test definition then the setup folder. "
                    "Templates allowed (`{{ toytrain_sw.path }}/pilot/`).", type="string"),
        "target": h("Where it goes in the slot's input folder (`app/`, `config.yml`). A folder ends with `/`.",
                    type="string"),
        "method": h("`template` (default): rendered with the datapack keys (Jinja2); `copy`: copied as is; "
                    "`generate`: produced by a `generator` (a plugin or a script) from the source and the keys.",
                    type="string", enum=["template", "copy", "generate"]),
        "generator": h("For `method: generate`: a registered generator, or the path of a script relative to the setup "
                       "folder, run as `<script> <source> <target> <keys.json>`.", type="string"),
        "options": h("Options given to the generator (templates allowed).", type="object"),
        "optional": h("Skip this input when the source is not found, instead of failing.", type="boolean"),
        "postprocess": h("Processors run on the target after it is written: `finalize`, `replace <old> <new>`, or a "
                         "plugin processor.", type="array", items={"type": "string"}),
    },
    defaultSnippets=[{"label": "template", "body": {"source": "templates/${1:config.yml}", "target": "${1:config.yml}"}},
                     {"label": "copy a folder", "body": {"source": "${1:path}/", "target": "${2:app/}",
                                                         "method": "copy"}},
                     {"label": "generate", "body": {"source": "${1:source}", "target": "${2:target}",
                                                    "method": "generate", "generator": "${3:name}"}}])

SLOT_TYPE = h(
    "A slot type: the inputs each configuration of it gets (section 8.2). The commands come from "
    "the lab role of the slot's `role`.",
    type="object", required=["configurations"], additionalProperties=False,
    properties={
        "description": h("What this slot is in the test.", type="string"),
        "role": h("The lab role its slots have: the inputs that role expects are proposed for each "
                  "configuration, and missing ones reported.", type="string"),
        "configurations": h("Configurations of the slot type, by name (`standard`...): the inputs of each.",
                            type="object", additionalProperties={
                                "type": "object", "additionalProperties": False,
                                "properties": {"inputs": h("Input files of the slot.", type="array", items=INPUT)}},
                            defaultSnippets=[{"label": "standard", "body": {"standard": {"inputs": [
                                {"source": "templates/${1:config.yml}", "target": "${1:config.yml}"}]}}}]),
    })

SLOT = h(
    "A slot: what the test needs, bound at activation to a lab component with the same `role` "
    "(in the lab stage bound to its `stage_slot` when given, among the ad hoc lab components otherwise).",
    type="object", required=["type", "configuration"], additionalProperties=False,
    properties={"name": h("Name shown in logs and the UI.", type="string"),
                "type": h("Its slot type (`slot_types` of this setup).", type="string"),
                "configuration": h("Configuration of that type to use (its inputs).", type="string"),
                "stage_slot": h("Stage slot it belongs to (`stage_slots` of this configuration): the slots of a stage slot are bound in "
                           "one lab stage. Leave out for an ad hoc "
                           "slot.", type="string"),
                "role": h("Role it binds to in the lab (`tsq_role`): a lab role, which gives its commands. A "
                          "component whose role extends it fits too.", type="string"),
                "tags": h("Tags the lab component must have (`tsq_tags`, its id being one): routes the slot to "
                          "given hardware or a subset of it.", type="array", items={"type": "string"}),
                "needs": h("Slots whose inputs are generated first, readable with `read_input('<id>/<file>')` "
                           "and `slots.<id>.input_dir` (section 5.2).", type="array", items={"type": "string"}),
                })

SETUP_CONFIGURATION = h(
    "A setup configuration: the stage slots and slots a datapack built with it needs (section 8.1).",
    type="object", required=["slots"], additionalProperties=False,
    properties={
        "stage_slots": h("Stage slots, by id, with the lab stage type they ask for: each is bound to any free lab stage of that "
                    "type, and its slots to the lab components of that lab stage.", type="object",
                    additionalProperties=h("`{type: <stage type>, tags: [...]}`.", type=["object", "string"],
                                           properties={"type": h("Stage type (`tsq_stage_type` in the lab).",
                                                                 type="string"),
                                                       "tags": h("Tags the lab stage must have (`tsq_tags` of the "
                                                                 "stage group, its id being one).", type="array",
                                                                 items={"type": "string"})})),
        "slots": h("Slots, by id (the id used by the pipeline steps).", type="object", additionalProperties=SLOT),
    })

SETUP = {
    "$schema": "http://json-schema.org/draft-07/schema#", "title": "Datapack setup",
    **h("A setup file (datapack side): where the inputs come from, the slot types and the "
        "configurations the test definitions choose from."),
    "type": "object", "required": ["id", "configurations"], "additionalProperties": False,
    "properties": {
        "id": h("Identifier of the setup, part of the datapack name.", type="string"),
        "name": h("Readable name.", type="string"),
        "dictionaries": h("Key files (YAML) relative to this file; templates allowed "
                          "(`platforms/{{ testdef.setup_config }}.yml`).", type="array", items={"type": "string"}),
        "datarepositories": h("Where datasets are imported from (software releases, simulation models...): each "
                              "dataset gives keys `<id>.path` and `<id>.version`.", type="array", items={
            "type": "object", "required": ["id", "path"], "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "name": {"type": "string"},
                           "path": h("Local folder the datasets are imported into.", type="string"),
                           "purge_after_days": h("Days after which a dataset folder of `path` that no datapack "
                                                 "used is deleted (it is downloaded again when needed): 90 by "
                                                 "default, 0 to keep them all.", type="integer", minimum=0,
                                                 default=90),
                           "datasources": {"type": "array", "items": {
                               "type": "object", "required": ["id", "importer", "source"], "additionalProperties": False,
                               "properties": {"id": {"type": "string"}, "name": {"type": "string"},
                                              "importer": h("Importer: `git`, or a plugin importer.", type="string"),
                                              "source": h("Where the importer reads (repository URL, folder).",
                                                          type="string"),
                                              "datasets": {"type": "array", "items": {
                                                  "type": "object", "required": ["id", "version"],
                                                  "additionalProperties": False,
                                                  "properties": {"id": h("Dataset id: keys `<id>.path`, `<id>.version`.",
                                                                         type="string"),
                                                                 "version": h("Version (git tag...).", type="string"),
                                                                 "postprocess": {"type": "array",
                                                                                 "items": {"type": "string"}}}}}}}}}}),
        "slot_types": h("Slot types, by name.", type="object", additionalProperties=SLOT_TYPE),
        "configurations": h("Setup configurations, by name: a test definition picks one with `setup_config`.",
                            type="object", additionalProperties=SETUP_CONFIGURATION),
    },
}

# ── datapack side: test definition and pipeline ────────────────────────────

STEP_FIELDS = {
    "id": h("Step id, unique in its phase or sub-pipeline (no `.`, `[` or `/`). Steps of a sub-pipeline appear as "
            "`<uses step>.<id>`.", type="string"),
    "slot": h("The slot the step acts on (it runs on the lab component bound to it).", type="string"),
    "for_each": h("Run the step on several slots in parallel: a list of slots, `slots` for "
                  "every slot of the phase, or a role.", type=["array", "string"], items={"type": "string"},
                  examples=["slots"]),
    "do": h("A command of the slot's role, as its lab component implements it (`deploy`, `start`, `ready`, `stop`, `collect`...).", type="string"),
    "run": h("An inline shell command, on the component (or in the run folder on the sequencer host without one). "
             + TEMPLATE_NOTE, type="string"),
    "python": PYTHON,
    "http": HTTP,
    "push": h("Copy files to the component: `{from: <path in the run folder>, to: <path on the component>}`.",
              type="object"),
    "pull": h("Copy files from the component: `{from: <path on the component>, to: <path in the run folder>}`.",
              type="object"),
    "delay": h("Wait for a duration: a test with no objective end (`{{ scenario.duration_s }}s`).",
               type=["string", "number"]),
    "wait_for": WAIT_FOR,
    "uses": h("Run a sub-pipeline (`pipeline.subpipelines`, or an included file) as one step.", type="string"),
    "with": h("Parameters of the sub-pipeline (`uses`), read as `params.*` in it.", type="object"),
    "needs": h("Steps that must have ended successfully before this one starts: the edges of the graph. A step "
               "with no `needs` starts with the phase.", type="array", items={"type": "string"}),
    "if": h("Run the step only when this expression is true, otherwise it is `skipped`: `succeeded('<step>')`, "
            "`failed('<step>')`, `errored(...)`, `skipped(...)`, `verdict(...)`, and the datapack keys.",
            type="string", examples=["not succeeded('down')"]),
    "expect": EXPECT,
    "timeout": DURATION,
    "retries": h("Times the step is tried again when it does not succeed.", type="integer", minimum=0),
    "detach": h("Start the command and go on (overrides the command's `detach`).", type="boolean"),
    "continue_on_error": h("Steps that need this one run even when it fails; the phase verdict still counts it.",
                           type="boolean"),
    "env": h("Environment variables of the step's command.", type="object"),
}

STEP = h("A step: a target (`slot` or `for_each`), one action (`do`, `run`, `python`, `http`, `push`, `pull`, `delay`, "
         "`wait_for`, `uses`, or a plugin step type), and options.", type="object", required=["id"],
         properties=STEP_FIELDS, additionalProperties=True,
         defaultSnippets=[
             {"label": "do a command", "body": {"id": "${1:start_app}", "slot": "${2:slot}",
                                                "do": "${3:start}", "needs": []}},
             {"label": "for each slot", "body": {"id": "${1:deploy}", "for_each": "slots", "do": "${2:deploy}"}},
             {"label": "run a shell command", "body": {"id": "${1:check}", "slot": "${2:slot}",
                                                       "run": "${3:command}"}},
             {"label": "run Python code", "body": {"id": "${1:check}", "python": {"code": "import sys\n${2:sys.exit(0)}\n"}}},
             {"label": "delay", "body": {"id": "${1:run}", "delay": "${2:30s}", "needs": []}},
             {"label": "wait for a state", "body": {"id": "${1:running}", "wait_for": {
                 "state": "running", "slot": "${2:slot}", "timeout": "20s"}}},
             {"label": "sub-pipeline", "body": {"id": "${1:up}", "uses": "${2:bring_up}"}}])

PHASE = h("A phase: the resources it locks (`stage_slots`, `slots`), its inputs from earlier phases, and its steps. "
          "Phases run in `needs` order.", type="object", required=["id"], additionalProperties=False,
          properties={
              "id": h("Phase id.", type="string"),
              "needs": h("Phases that must end before this one starts.", type="array", items={"type": "string"}),
              "stage_slots": h("Stage slots locked for the phase (setup configuration `stage_slots`): their slots, and the whole "
                          "lab stage each is bound to, come with them.", type="array", items={"type": "string"}),
              "slots": h("Ad hoc slots locked for the phase (those with no stage slot).", type="array",
                         items={"type": "string"}),
              "inputs": h("Outputs of earlier phases copied in the run folder before the phase starts: "
                          "`{from: '<phase>:<slot>/output/<path>', to: '<slot>/input/<path>'}`.",
                          type="array", items={"type": "object", "required": ["from", "to"],
                                               "properties": {"from": {"type": "string"}, "to": {"type": "string"}}}),
              "steps": h("Steps of the phase.", type="array", items=STEP),
              "finally": h("Steps that always run at the end of the phase, even on failure, cancel or kill: stop "
                           "leftovers, collect outputs.", type="array", items=STEP),
              "timeout": DURATION,
          })

SUBPIPELINE = h("A sub-pipeline: named steps used as one step with `uses` (section 8.4). Inside, steps keep their "
                "own `needs`.", type="object", required=["steps"], additionalProperties=False,
                properties={"params": h("Parameters with their defaults, given by `with:` and read as `params.*`.",
                                        type="object"),
                            "steps": h("Steps of the sub-pipeline.", type="array", items=STEP)})

PIPELINE = h("The pipeline run by the sequencer (section 8.3), kept as written in the datapack.", type="object",
             additionalProperties=False,
             properties={"timeout": DURATION,
                         "include": h("Files of shared sub-pipelines, relative to the test definition.", type="array",
                                      items={"type": "string"}),
                         "subpipelines": h("Sub-pipelines, by name.", type="object", additionalProperties=SUBPIPELINE),
                         "phases": h("Phases.", type="array", items=PHASE),
                         "baseline": h("Expected baseline file the results are checked against.", type="string"),
                         "reserve": h("`phase` (default): lock each phase's resources when it starts; `all`: lock "
                                      "everything at activation.", type="string", enum=["phase", "all"]),
                         "on_state_mismatch": h("When a probed state differs from the expected one: `warn` (default) "
                                                "or `fail` the step.", type="string", enum=["warn", "fail"])})

TESTDEF = {
    "$schema": "http://json-schema.org/draft-07/schema#", "title": "Test definition",
    **h("A test definition: the setup and configuration it uses, its key files, and its pipeline."),
    "type": "object", "required": ["id", "name", "setup_file", "setup_config"], "additionalProperties": False,
    "properties": {
        "id": h("Test identifier, part of the datapack name.", type="string"),
        "name": h("Readable name.", type="string"),
        "description": h("What the test checks.", type="string"),
        "setup_file": h("The setup file, relative to this file.", type="string"),
        "setup_config": h("The setup configuration used (`datapack --setup-config` can choose another).",
                          type="string"),
        "dictionaries": h("Key files of the test (YAML), relative to this file.", type="array", items={"type": "string"}),
        "pipeline": PIPELINE,
    },
}

SUBPIPELINES_FILE = {
    "$schema": "http://json-schema.org/draft-07/schema#", "title": "Shared sub-pipelines",
    **h("Sub-pipelines shared by several test definitions (`pipeline.include`)."),
    "type": "object", "properties": {"subpipelines": h("Sub-pipelines, by name.", type="object",
                                                       additionalProperties=SUBPIPELINE)},
}

BASE = {"inventory": INVENTORY, "library": LIBRARY_FILE, "setup": SETUP, "testdef": TESTDEF,
        "subpipelines": SUBPIPELINES_FILE}


# ── filled in from the lab and the workspace ───────────────────────────────

def _choices(values: dict[str, str], strict: bool) -> dict:
    """enum (strict) or examples, with a description per value for completion."""
    names = sorted(values)
    if not names:
        return {}
    if strict:
        return {"enum": names, "markdownEnumDescriptions": [values[n] for n in names]}
    return {"examples": names}


def _walk(node: Any, fn) -> None:
    if isinstance(node, dict):
        fn(node)
        for value in node.values():
            _walk(value, fn)
    elif isinstance(node, list):
        for value in node:
            _walk(value, fn)


def schemas(context: dict) -> dict:
    """The schemas, with the choices of the lab and workspace of an editing session (``Editor.context``)."""
    lab = context.get("lab") or {}
    def text(name: str, d: dict, commands) -> str:
        return (f"**{name}**" + (f" (extends {d['extends']})" if d.get("extends") else "")
                + (f": {d['description']}" if d.get("description") else "")
                + (f"\n\ncommands: {', '.join(sorted(commands))}" if commands else ""))

    lab_roles = lab.get("roles") or {}
    roles = {name: text(name, r, r.get("commands") or {})
             + (f"\n\nused by {', '.join(r['used_by'])}" if r.get("used_by") else "")
             for name, r in lab_roles.items()}
    components = {name: text(name, c, c.get("commands") or {}) for name, c in (lab.get("library") or {}).items()}
    hosts = {name: f"host **{name}** (role {c.get('role')})" for name, c in (lab.get("components") or {}).items()}
    stage_types = {t: f"stages: {', '.join(s)}" for t, s in (lab.get("stage_types") or {}).items()}
    component_tags: dict[str, str] = {}
    for name, c in (lab.get("components") or {}).items():
        for tag in c.get("tags") or []:
            component_tags[tag] = (component_tags.get(tag, "components:") + " " + name)
    stage_tags: dict[str, str] = {}
    for name, s in (lab.get("stages") or {}).items():
        for tag in s.get("tags") or []:
            stage_tags[tag] = (stage_tags.get(tag, "stages:") + " " + name)
    transports = {t: t for t in context.get("transports") or ["local", "ssh"]}
    commands: dict[str, str] = {}
    for name, r in lab_roles.items():
        for cmd, info in (r.get("commands") or {}).items():
            commands.setdefault(cmd, f"{(info or {}).get('description') or COMMAND_NAMES.get(cmd, '')}\n\nroles: ")
            commands[cmd] += name + " "
    ws = context.get("workspace") or {}
    logical = {c: f"{c} ({v.get('type', '')}, role {v.get('role', '')})"
               for s in ws.get("setups") or [] for cfg in (s.get("configurations") or {}).values()
               for c, v in (cfg.get("slots") or {}).items()}
    stage_slots = {st: f"lab stage type {ty}" for s in ws.get("setups") or []
              for cfg in (s.get("configurations") or {}).values() for st, ty in (cfg.get("stage_slots") or {}).items()}
    dp_types = {t: f"slot type: {v.get('description') or ', '.join(v.get('configurations') or [])}" for s in ws.get("setups") or []
                for t, v in (s.get("slot_types") or {}).items()}
    subs = {n: "sub-pipeline" for n in ws.get("subpipelines") or []}
    importers = {i: i for i in context.get("importers") or []}
    generators = {g: g for g in context.get("generators") or []}
    step_types = context.get("step_types") or []
    ready_checks = context.get("ready_checks") or []

    result = copy.deepcopy(BASE)

    def lab_fields(node: dict) -> None:
        props = node.get("properties") or {}
        if "tsq_role" in props and roles:
            props["tsq_role"].update(_choices(roles, True))
        if "tsq_stage_type" in props and stage_types:
            props["tsq_stage_type"].update(_choices(stage_types, False))
        if "tsq_transport" in props:
            props["tsq_transport"].update(_choices(transports, True))
        if "tsq_extends" in props and (components or hosts):
            props["tsq_extends"].update(_choices({**components, **hosts}, False))
        if "extends" in props and "transport" in props and components:
            props["extends"].update(_choices(components, True))
        if "roles" in props and "transport" in props and roles:
            props["roles"]["items"] = {"type": "string", **_choices(roles, True)}
        if "extends" in props and "transport" not in props and "commands" in props and roles:
            props["extends"].update(_choices(roles, True))
        if "transport" in props and "commands" in props:
            props["transport"].update(_choices(transports, True))

    _walk(result["inventory"], lab_fields)
    _walk(result["library"], lab_fields)

    def setup_fields(node: dict) -> None:
        props = node.get("properties") or {}
        if "role" in props and "configuration" in props and roles:
            props["role"].update(_choices(roles, False))
        if "role" in props and "configuration" in props and component_tags:
            props["tags"]["items"] = {"type": "string", **_choices(component_tags, False)}
        if "role" in props and "configuration" in props and dp_types:
            props["type"].update(_choices(dp_types, False))
        if "role" in props and "configurations" in props and roles:          # a slot type
            props["role"].update(_choices(roles, False))
        if "importer" in props and importers:
            props["importer"].update(_choices(importers, False))
        if "generator" in props and generators:
            props["generator"].update(_choices(generators, False))
        if set(props) == {"type", "tags"}:
            if stage_types:
                props["type"].update(_choices(stage_types, False))
            if stage_tags:
                props["tags"]["items"] = {"type": "string", **_choices(stage_tags, False)}

    _walk(result["setup"], setup_fields)

    def step_fields(node: dict) -> None:
        props = node.get("properties") or {}
        if "do" in props and "uses" in props:
            if commands:
                props["do"].update(_choices(commands, False))
            if logical:
                props["slot"].update(_choices(logical, False))
                props["for_each"]["examples"] = ["slots"] + sorted(logical)
            if subs:
                props["uses"].update(_choices(subs, False))
            for name in step_types:
                props.setdefault(name, h(f"Plugin step type `{name}`."))
        if "stage_slots" in props and "finally" in props:
            if stage_slots:
                props["stage_slots"]["items"] = {"type": "string", **_choices(stage_slots, False)}
            if logical:
                props["slots"]["items"] = {"type": "string", **_choices(logical, False)}
        if "cmd" in props and "log" in props:
            for name in ready_checks:
                props.setdefault(name, h(f"Plugin ready check `{name}`."))

    _walk(result["testdef"], step_fields)
    _walk(result["subpipelines"], step_fields)
    return result
