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
    "state": h("The monitored state of the component (section 7.4): `running`, `idle`, `configured`, `broken`, "
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

COMMAND = h(
    "What a command does, one of:\n"
    "- `run`: a shell command on the component (over its transport), with `timeout`, `detach`, `expect`, `env`;\n"
    "- `wait_for`: a ready check (state, tcp, http, file, log, cmd);\n"
    "- `signal`: send a signal to the processes the run started on the component (`TERM`, then `KILL` after "
    "`timeout`, default 10 s);\n"
    "- `builtin`: a built-in command (`deploy`, `collect`, `kill`);\n"
    "- a plugin step type, given by its name.\n\n" + TEMPLATE_NOTE,
    type="object",
    properties={
        "run": h("Shell command run on the component. " + TEMPLATE_NOTE, type="string"),
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

COMMANDS = h("Commands of the type, by name, used by pipeline steps as `do: <name>`. A derived type inherits its "
             "parent's commands and overrides those it redefines (section 7.3).", type="object",
             properties={name: {**COMMAND, "markdownDescription": f"**{name}**: {text}\n\n" + COMMAND["markdownDescription"],
                                "description": f"{name}: {text.replace('`', '')}"}
                         for name, text in COMMAND_NAMES.items()},
             additionalProperties=COMMAND)

STATES = ("offline", "idle", "configured", "running", "broken", "unknown")
STATE_HELP = {"offline": "unreachable", "idle": "reachable, nothing running", "configured": "inputs deployed, not started",
              "running": "started", "broken": "reachable but faulty: not used for new runs",
              "unknown": "no probe can tell"}

STATUS = h(
    "How the state monitor finds the component's state (section 7.4): a `probe` command whose output and exit "
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
    "deployed (section 7.5). A derived type adds to its parent's checks; a check with the same `id` replaces it. "
    "When a check fails, nothing is deployed and the run goes back to the queue.",
    type="array",
    items={"type": "object", "required": ["id"],
           "properties": {"id": h("Name of the check, unique in the type chain.", type="string"),
                          "run": h("Shell command run on the component: exit 0 passes.", type="string"),
                          "timeout": DURATION, "expect": EXPECT,
                          "builtin": h("A built-in check: `status`, `login`, `remote_root`, `disk`, `clock`.",
                                       type="string", enum=["status", "login", "remote_root", "disk", "clock"])},
           "defaultSnippets": [{"label": "check command", "body": {"id": "${1:name}", "run": "${2:command}",
                                                                   "timeout": "20s"}}]})

COMPONENT_TYPE = h(
    "A component type: its commands, how its state is monitored, its self-test and its transport. A type "
    "`extends` another one and redefines only what differs (section 7).",
    type="object", additionalProperties=False,
    properties={
        "extends": h("The parent type: `linux_system` for anything reachable over SSH (deploy, collect and kill "
                     "built in), `component` for something that is not a Linux system (a switch, an instrument "
                     "driven through its own API), or another type of the library.", type="string"),
        "description": h("What this type is: shown in the Editor when a component type is chosen.", type="string"),
        "transport": h("How the sequencer talks to components of this type: `ssh` (default), `local` (on the "
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

TYPES_FILE = {
    "$schema": "http://json-schema.org/draft-07/schema#", "title": "Component type library file",
    **h("A file of the lab's type library (the `tsq_types` folders of the inventory): `component_types`, by name."),
    "type": "object", "required": ["component_types"], "additionalProperties": False,
    "properties": {"component_types": h("Component types, by name. The built-in `component` and `linux_system` "
                                        "cannot be redefined.", type="object", additionalProperties=COMPONENT_TYPE)},
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
    "tsq_type": h("Component type (lab type library, section 7): gives the commands, the status probe and the "
                  "self-test. Default `linux_system`.", type="string"),
    "tsq_role": h("Role of the component in its stage: datapacks bind their logical components by role "
                  "(`role: pilot` binds the host with `tsq_role: pilot` in the stage). Unique within a stage.",
                  type="string"),
    "tsq_lock": LOCK,
    "tsq_label": h("Name shown in the UI and the CLI (default: the host name).", type="string"),
    "tsq_transport": h("How the sequencer talks to the component: `ssh` (default) or `local` (commands run on the "
                       "sequencer host, as the user running it), or a plugin transport.", type="string"),
    "tsq_remote_root": h("Folder on the component under which each logical component gets "
                         "`<root>/<component>/input` and `output`.", type="string", examples=["/opt/testrun"]),
    "tsq_commands": h("Command overrides, by name. On a stage group they apply to every component of the stage; on a "
                      "host, to that host only. They win over the component type and the datapack (section 7.3).",
                      type="object", additionalProperties=COMMAND),
}

HOST_VARS = h("Variables of a host. `tsq_` variables are the sequencer's; any other variable is kept and reachable "
              "in templates as `{{ component.vars.<name> }}`.", type=["object", "null"], properties=VARS,
              defaultSnippets=[{"label": "SSH host", "body": {"ansible_host": "${1:10.0.0.10}",
                                                              "tsq_type": "${2:linux_system}", "tsq_role": "${3:role}"}},
                               {"label": "local process", "body": {"ansible_host": "localhost", "tsq_transport": "local",
                                                                   "tsq_type": "${1:linux_system}",
                                                                   "tsq_role": "${2:role}"}}])

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
                                                                 "hosts": {"${3:host}": {"tsq_type": "${4:linux_system}",
                                                                                         "tsq_role": "${5:role}"}}}}}])
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
                                         properties={**VARS, "tsq_types": h(
                                             "Folders of the component type library, relative to this file.",
                                             type=["array", "string"], items={"type": "string"},
                                             examples=[["types/"]])}),
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
    "A datapack component type: the inputs each configuration of it gets, and commands added for this test. "
    "`extends` a lab type, so that binding checks the lab component is of that type (or derives from it), "
    "and its commands are inherited (section 8.2).",
    type="object", required=["configurations"], additionalProperties=False,
    properties={
        "extends": h("The lab component type this one runs on (lab type library).", type="string"),
        "description": h("What this component is in the test.", type="string"),
        "configurations": h("Configurations of the component, by name (`standard`...): the inputs of each.",
                            type="object", additionalProperties={
                                "type": "object", "additionalProperties": False,
                                "properties": {"inputs": h("Input files of the component.", type="array", items=INPUT)}},
                            defaultSnippets=[{"label": "standard", "body": {"standard": {"inputs": [
                                {"source": "templates/${1:config.yml}", "target": "${1:config.yml}"}]}}}]),
        "commands": {**COMMANDS, **h("Commands added or redefined for this test (kept as written in the datapack, "
                                     "rendered at run time). The lab's stage and host `tsq_commands` still win.",
                                     type="object", additionalProperties=COMMAND)},
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
                "role": h("Role it binds to in the lab (`tsq_role`).", type="string"),
                "needs": h("Components whose inputs are generated first, readable with `read_input('<id>/<file>')` "
                           "and `components.<id>.input_dir` (section 5.2).", type="array", items={"type": "string"}),
                "commands": h("Commands for this component only (between the datapack type and the lab overrides).",
                              type="object", additionalProperties=COMMAND)})

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

STEP = h("A step: a target (`component` or `for_each`), one action (`do`, `run`, `push`, `pull`, `delay`, "
         "`wait_for`, `uses`, or a plugin step type), and options.", type="object", required=["id"],
         properties=STEP_FIELDS, additionalProperties=True,
         defaultSnippets=[
             {"label": "do a command", "body": {"id": "${1:start_app}", "component": "${2:component}",
                                                "do": "${3:start}", "needs": []}},
             {"label": "for each component", "body": {"id": "${1:deploy}", "for_each": "components", "do": "${2:deploy}"}},
             {"label": "run a shell command", "body": {"id": "${1:check}", "component": "${2:component}",
                                                       "run": "${3:command}"}},
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

BASE = {"inventory": INVENTORY, "types": TYPES_FILE, "setup": SETUP, "testdef": TESTDEF,
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
    types = {name: f"**{name}**" + (f" (extends {t['extends']})" if t.get("extends") else "")
             + (f": {t['description']}" if t.get("description") else "")
             + (f"\n\ncommands: {', '.join(sorted(t.get('commands') or {}))}" if t.get("commands") else "")
             for name, t in (lab.get("types") or {}).items()}
    roles = {r: f"used by {', '.join(c)}" for r, c in (lab.get("roles") or {}).items()}
    stage_types = {t: f"stages: {', '.join(s)}" for t, s in (lab.get("stage_types") or {}).items()}
    transports = {t: t for t in context.get("transports") or ["local", "ssh"]}
    commands: dict[str, str] = {}
    for name, t in (lab.get("types") or {}).items():
        for cmd in t.get("commands") or {}:
            commands.setdefault(cmd, f"{COMMAND_NAMES.get(cmd, '')}\n\nprovided by: ")
            commands[cmd] += name + " "
    ws = context.get("workspace") or {}
    logical = {c: f"{c} ({v.get('type', '')}, role {v.get('role', '')})"
               for s in ws.get("setups") or [] for cfg in (s.get("configurations") or {}).values()
               for c, v in (cfg.get("components") or {}).items()}
    logical_stages = {st: f"stage type {ty}" for s in ws.get("setups") or []
                      for cfg in (s.get("configurations") or {}).values() for st, ty in (cfg.get("stages") or {}).items()}
    dp_types = {t: f"datapack type, extends {v.get('extends', '')}" for s in ws.get("setups") or []
                for t, v in (s.get("component_types") or {}).items()}
    subs = {n: "sub-pipeline" for n in ws.get("subpipelines") or []}
    importers = {i: i for i in context.get("importers") or []}
    generators = {g: g for g in context.get("generators") or []}
    step_types = context.get("step_types") or []
    ready_checks = context.get("ready_checks") or []

    result = copy.deepcopy(BASE)

    def lab_fields(node: dict) -> None:
        props = node.get("properties") or {}
        if "tsq_type" in props and types:
            props["tsq_type"].update(_choices(types, True))
        if "tsq_role" in props and roles:
            props["tsq_role"].update(_choices(roles, False))
        if "tsq_stage_type" in props and stage_types:
            props["tsq_stage_type"].update(_choices(stage_types, False))
        if "tsq_transport" in props:
            props["tsq_transport"].update(_choices(transports, True))
        if "extends" in props and "commands" in props and types:
            props["extends"].update(_choices(types, True))
        if "transport" in props and "commands" in props:
            props["transport"].update(_choices(transports, True))

    _walk(result["inventory"], lab_fields)
    _walk(result["types"], lab_fields)

    def setup_fields(node: dict) -> None:
        props = node.get("properties") or {}
        if "configurations" in props and "extends" in props and types:
            props["extends"].update(_choices(types, True))
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
