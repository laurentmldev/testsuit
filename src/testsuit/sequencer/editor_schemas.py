"""JSON Schemas of the files the Editor edits (README section 13.2), with their help.

The descriptions are the contextual help of the Editor: the YAML view shows them on hover and
completion, and the forms show them next to each field. ``defaultSnippets`` are the blocks the
YAML view proposes when a new item is started.

``schemas(context)`` fills in what depends on the lab and the workspace: component types, roles,
stage types, transports, commands, importers, generators, logical components.
"""
from __future__ import annotations

import copy
from typing import Any


def h(text: str, **extra: Any) -> dict:
    """A schema fragment with its help, in plain text and Markdown."""
    plain = text.replace("`", "")
    return {"description": plain, "markdownDescription": text, **extra}


DURATION = h("A duration: `30s`, `2m`, `1h30m`, or a number of seconds.",
             type=["string", "number"], examples=["30s", "2m", "15m"])

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

WAIT_KINDS = {
    "state": h("The monitored state of the component (section 7.5): `running`, `idle`, `configured`, `broken`, "
               "`offline`, `unknown`, or a list of them.", type=["string", "array"],
               examples=["running", "idle", ["idle", "configured"]]),
    "tcp": h("A TCP port accepting connections: `host:port`, or `{host, port}` (host: the component's by default).",
             type=["string", "object", "integer"], examples=["10.42.0.10:5000", {"port": 8080}]),
    "http": h("An HTTP answer: a URL, or `{url, status (default 200), regex}` on the body.",
              type=["string", "object"], examples=["http://localhost:8080/health",
                                                   {"url": "http://localhost:8080/health", "status": 200}]),
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
             "`component` names the component checked when the step has none.",
             type="object",
             properties={**WAIT_KINDS, "timeout": DURATION, "interval": DURATION,
                         "component": h("The logical component checked (in a step without `component`).",
                                        type="string")},
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
    "- `wait_for`: a ready check (state, tcp, http, file, log, cmd);\n"
    "- `signal`: send a signal to the processes the run started on the component (`TERM`, then `KILL` after "
    "`timeout`, default 10 s);\n"
    "- `builtin`: a built-in command (`deploy`, `collect`, `kill`);\n"
    "- a plugin step type, given by its name.\n\n" + TEMPLATE_NOTE,
    type="object",
    properties={
        "run": h("Shell command run on the component. " + TEMPLATE_NOTE, type="string"),
        "python": PYTHON,
        "detach": h("Start the command and go on without waiting for it to end (a server, a simulation). "
                    "Its processes are recorded, then stopped by `signal` or `kill`.", type="boolean"),
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
        "probe": h("The probe: `{run: <shell command>}`, `{http: <url>}`, or a plugin state probe.",
                   type="object", properties={"run": h("Shell command run on the component (or locally with "
                                                       "`transport: local`).", type="string"),
                                              "http": {"type": ["string", "object"]}, "timeout": DURATION}),
        "states": h("Rules giving a state from the probe result, first match wins: `{regex: ...}` on the output, "
                    "or `{exit: 0 | nonzero}`.", type="object",
                    properties={s: h(f"`{s}`: {STATE_HELP[s]}.", type="object",
                                     properties={"regex": {"type": "string"},
                                                 "exit": {"type": ["integer", "string"], "examples": [0, "nonzero"]}})
                                for s in STATES},
                    additionalProperties=False),
        "interval": DURATION,
        "builtin": h("`linux_system`: `offline` when the transport fails, `running` while processes the "
                     "sequencer started are alive, `idle` otherwise.", type="string", enum=["linux_system"]),
    },
    defaultSnippets=[{"label": "probe with a command", "body": {
        "probe": {"run": "${1:systemctl is-active my-service}"},
        "states": {"running": {"regex": "^active"}, "broken": {"regex": "^failed"}, "idle": {"regex": "^inactive"}}}},
        {"label": "ping (offline when it fails)", "body": {"probe": {"run": "ping -c 1 -W 1 {{ component.host }}"},
                                                           "states": {"running": {"exit": 0},
                                                                      "offline": {"exit": "nonzero"}}}}])

SELFTEST = h(
    "Checks run on every bound component before each run, once its resources are locked and before anything is "
    "deployed (section 7.6). A component adds to its parent's checks; a check with the same `id` replaces it. "
    "When a check fails, nothing is deployed and the run goes back to the queue.",
    type="array",
    items={"type": "object", "required": ["id"],
           "properties": {"id": h("Name of the check, unique in the component chain.", type="string"),
                          "run": h("Shell command run on the component: exit 0 passes.", type="string"),
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
    "Lab components implement them; datapacks bind their logical components by role and run its commands. "
    "A role `extends` another one and adds commands (section 7.1).",
    type="object", additionalProperties=False,
    properties={
        "extends": h("The parent role: its commands are offered too. `linux_system` (deploy, collect, kill) for "
                     "anything reachable over SSH, `component` (no command) otherwise.", type="string"),
        "description": h("What this role is: shown in the Editor when a role is chosen.", type="string"),
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
    "ansible_host": h("Address or host name the sequencer connects to (default: the host's name in the inventory).",
                      type="string", examples=["10.42.1.10", "localhost"]),
    "ansible_user": h("SSH user (key-based authentication only).", type="string", examples=["tsq"]),
    "ansible_port": h("SSH port (default 22).", type="integer"),
    "ansible_ssh_private_key_file": h("Private key used to log in (no password prompt).", type="string",
                                      examples=["~/.ssh/id_lab"]),
    "tsq_role": h("Role of the component, a role of the lab library (section 7.1): the commands it must implement. "
                  "Not needed when the component it extends supports one role (it has that one); one of them when "
                  "it supports several; any role when it lists none. Datapacks bind their logical components by "
                  "role (`role: pilot` binds the host with role pilot in the stage). Unique within a stage.",
                  type="string"),
    "tsq_extends": h("The lab component this host is (section 7.2): a component of the library, or another host. "
                     "It gives the implementations of the commands, the status probe and the self-test "
                     "(default `linux_system`).", type="string"),
    "tsq_lock": LOCK,
    "tsq_label": h("Name shown in the UI and the CLI (default: the host name).", type="string"),
    "tsq_transport": h("How the sequencer talks to the component: `ssh` (default) or `local` (commands run on the "
                       "sequencer host, as the user running it), or a plugin transport.", type="string"),
    "tsq_remote_root": h("Folder on the component under which each logical component gets "
                         "`<root>/<component>/input` and `output`.", type="string", examples=["/opt/testrun"]),
    "tsq_commands": h("Command overrides, by name. On a stage group they apply to every component of the stage; on a "
                      "host, to that host only. They win over the component it extends (section 7.4).",
                      type="object", additionalProperties=COMMAND),
    "tsq_status": STATUS,
    "tsq_selftest": SELFTEST,
    "tsq_collect": h("Filters applied to what `collect` brings back from this host (`include`, `exclude`).",
                     type="object"),
}

HOST_VARS = h("Variables of a host. `tsq_` variables are the sequencer's; any other variable is kept and reachable "
              "in templates as `{{ component.vars.<name> }}`.", type=["object", "null"], properties=VARS,
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
                                             examples=[["roles/", "components/"]])}),
                               "hosts": h("Ad hoc components: in no stage, bound by role on their own "
                                          "(`role: sequencer` for the analysis on the sequencer host, simulations...).",
                                          type=["object", "null"], additionalProperties=HOST_VARS)}}},
}

# ── datapack side: setup ───────────────────────────────────────────────────

INPUT = h(
    "An input of the component: `source` (file, folder or glob pattern, relative to the test definition or setup "
    "folder) put at `target` in the component's input folder, by `method`.",
    type="object", required=["source", "target"], additionalProperties=False,
    properties={
        "source": h("File, folder or glob pattern, absolute or relative to the test definition then the setup folder. "
                    "Templates allowed (`{{ toytrain_sw.path }}/pilot/`).", type="string"),
        "target": h("Where it goes in the component's input folder (`app/`, `config.yml`). A folder ends with `/`.",
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

DATAPACK_TYPE = h(
    "A datapack component type: the inputs each configuration of it gets (section 8.2). The commands come from "
    "the lab role of the component's `role`.",
    type="object", required=["configurations"], additionalProperties=False,
    properties={
        "description": h("What this component is in the test.", type="string"),
        "configurations": h("Configurations of the component, by name (`standard`...): the inputs of each.",
                            type="object", additionalProperties={
                                "type": "object", "additionalProperties": False,
                                "properties": {"inputs": h("Input files of the component.", type="array", items=INPUT)}},
                            defaultSnippets=[{"label": "standard", "body": {"standard": {"inputs": [
                                {"source": "templates/${1:config.yml}", "target": "${1:config.yml}"}]}}}]),
    })

LOGICAL_COMPONENT = h(
    "A logical component: what the test needs, bound at activation to a lab component with the same `role` "
    "(in the bound stage when `stage` is given, among the ad hoc components otherwise).",
    type="object", required=["type", "configuration"], additionalProperties=False,
    properties={"name": h("Name shown in logs and the UI.", type="string"),
                "type": h("Its datapack component type (`component_types` of this setup).", type="string"),
                "configuration": h("Configuration of that type to use (its inputs).", type="string"),
                "stage": h("Logical stage it belongs to (`stages` of this configuration). Leave out for an ad hoc "
                           "component.", type="string"),
                "role": h("Role it binds to in the lab (`tsq_role`): a lab role, which gives its commands.",
                          type="string"),
                "needs": h("Components whose inputs are generated first, readable with `read_input('<id>/<file>')` "
                           "and `components.<id>.input_dir` (section 5.2).", type="array", items={"type": "string"}),
                })

SETUP_CONFIGURATION = h(
    "A setup configuration: the logical stages and components a datapack built with it needs (section 8.1).",
    type="object", required=["components"], additionalProperties=False,
    properties={
        "stages": h("Logical stages, by id, with the stage type they ask for: each is bound to any free stage of that "
                    "type.", type="object",
                    additionalProperties=h("`{type: <stage type>}`.", type=["object", "string"],
                                           properties={"type": h("Stage type (`tsq_stage_type` in the lab).",
                                                                 type="string")})),
        "components": h("Logical components, by id (the id used by the pipeline steps).", type="object",
                        additionalProperties=LOGICAL_COMPONENT),
    })

SETUP = {
    "$schema": "http://json-schema.org/draft-07/schema#", "title": "Datapack setup",
    **h("A setup file (datapack side): where the inputs come from, the datapack component types and the "
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
        "component_types": h("Datapack component types, by name.", type="object", additionalProperties=DATAPACK_TYPE),
        "configurations": h("Setup configurations, by name: a test definition picks one with `setup_config`.",
                            type="object", additionalProperties=SETUP_CONFIGURATION),
    },
}

# ── datapack side: test definition and pipeline ────────────────────────────

STEP_FIELDS = {
    "id": h("Step id, unique in its phase or sub-pipeline (no `.`, `[` or `/`). Steps of a sub-pipeline appear as "
            "`<uses step>.<id>`.", type="string"),
    "component": h("The logical component the step acts on.", type="string"),
    "for_each": h("Run the step on several components in parallel: a list of logical components, `components` for "
                  "every component of the phase, or a role.", type=["array", "string"], items={"type": "string"},
                  examples=["components"]),
    "do": h("A command of the component's type (`deploy`, `start`, `ready`, `stop`, `collect`...).", type="string"),
    "run": h("An inline shell command, on the component (or in the run folder on the sequencer host without one). "
             + TEMPLATE_NOTE, type="string"),
    "python": PYTHON,
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

STEP = h("A step: a target (`component` or `for_each`), one action (`do`, `run`, `python`, `push`, `pull`, `delay`, "
         "`wait_for`, `uses`, or a plugin step type), and options.", type="object", required=["id"],
         properties=STEP_FIELDS, additionalProperties=True,
         defaultSnippets=[
             {"label": "do a command", "body": {"id": "${1:start_app}", "component": "${2:component}",
                                                "do": "${3:start}", "needs": []}},
             {"label": "for each component", "body": {"id": "${1:deploy}", "for_each": "components", "do": "${2:deploy}"}},
             {"label": "run a shell command", "body": {"id": "${1:check}", "component": "${2:component}",
                                                       "run": "${3:command}"}},
             {"label": "run Python code", "body": {"id": "${1:check}", "python": {"code": "import sys\n${2:sys.exit(0)}\n"}}},
             {"label": "delay", "body": {"id": "${1:run}", "delay": "${2:30s}", "needs": []}},
             {"label": "wait for a state", "body": {"id": "${1:running}", "wait_for": {
                 "state": "running", "component": "${2:component}", "timeout": "20s"}}},
             {"label": "sub-pipeline", "body": {"id": "${1:up}", "uses": "${2:bring_up}"}}])

PHASE = h("A phase: the resources it locks (`stages`, `components`), its inputs from earlier phases, and its steps. "
          "Phases run in `needs` order.", type="object", required=["id"], additionalProperties=False,
          properties={
              "id": h("Phase id.", type="string"),
              "needs": h("Phases that must end before this one starts.", type="array", items={"type": "string"}),
              "stages": h("Logical stages locked for the phase (setup configuration `stages`): their components "
                          "come with them.", type="array", items={"type": "string"}),
              "components": h("Ad hoc logical components locked for the phase (those with no stage).", type="array",
                              items={"type": "string"}),
              "inputs": h("Outputs of earlier phases copied in the run folder before the phase starts: "
                          "`{from: '<phase>:<component>/output/<path>', to: '<component>/input/<path>'}`.",
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
    transports = {t: t for t in context.get("transports") or ["local", "ssh"]}
    commands: dict[str, str] = {}
    for name, r in lab_roles.items():
        for cmd, info in (r.get("commands") or {}).items():
            commands.setdefault(cmd, f"{(info or {}).get('description') or COMMAND_NAMES.get(cmd, '')}\n\nroles: ")
            commands[cmd] += name + " "
    ws = context.get("workspace") or {}
    logical = {c: f"{c} ({v.get('type', '')}, role {v.get('role', '')})"
               for s in ws.get("setups") or [] for cfg in (s.get("configurations") or {}).values()
               for c, v in (cfg.get("components") or {}).items()}
    logical_stages = {st: f"stage type {ty}" for s in ws.get("setups") or []
                      for cfg in (s.get("configurations") or {}).values() for st, ty in (cfg.get("stages") or {}).items()}
    dp_types = {t: f"datapack type: {v.get('description') or ', '.join(v.get('configurations') or [])}" for s in ws.get("setups") or []
                for t, v in (s.get("component_types") or {}).items()}
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
        if "role" in props and "configuration" in props and dp_types:
            props["type"].update(_choices(dp_types, False))
        if "importer" in props and importers:
            props["importer"].update(_choices(importers, False))
        if "generator" in props and generators:
            props["generator"].update(_choices(generators, False))
        if set(props) == {"type"} and stage_types:
            props["type"].update(_choices(stage_types, False))

    _walk(result["setup"], setup_fields)

    def step_fields(node: dict) -> None:
        props = node.get("properties") or {}
        if "do" in props and "uses" in props:
            if commands:
                props["do"].update(_choices(commands, False))
            if logical:
                props["component"].update(_choices(logical, False))
                props["for_each"]["examples"] = ["components"] + sorted(logical)
            if subs:
                props["uses"].update(_choices(subs, False))
            for name in step_types:
                props.setdefault(name, h(f"Plugin step type `{name}`."))
        if "stages" in props and "finally" in props:
            if logical_stages:
                props["stages"]["items"] = {"type": "string", **_choices(logical_stages, False)}
            if logical:
                props["components"]["items"] = {"type": "string", **_choices(logical, False)}
        if "cmd" in props and "log" in props:
            for name in ready_checks:
                props.setdefault(name, h(f"Plugin ready check `{name}`."))

    _walk(result["testdef"], step_fields)
    _walk(result["subpipelines"], step_fields)
    return result
