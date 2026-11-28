# TSQ-1: Test sequencer, technical specification

```
Category:   Standards Track (project-internal)
Status:     Describes the implemented system (tsqd, tsq, REST API, web UI, Editor)
Companion:  README.md (architecture overview, user manual, rationale, examples)
```

## Status of this memo

This document is the condensed, normative description of the testsuit sequencer: its concepts, data model, execution semantics and interfaces. It omits tutorials, rationale, UI detail and examples; those are in [README.md](README.md), referenced as `[R §n]`. Where the two differ, the code is authoritative and the difference is a defect of one of the documents.

## Table of contents

1. Introduction
2. Architecture
3. Configuration
4. Lab model
5. Datapack model
6. Pipeline
7. Template keys
8. Binding, scheduling and locking
9. Run execution
10. Abort and recovery
11. Results
12. Interfaces
13. Extension points
14. Security considerations
15. Known deviations

---

## 1. Introduction

### 1.1 Scope

The sequencer (`tsqd`) accepts **datapacks**, queues them by priority, binds the resources each one needs to free resources of one **lab**, runs the datapack's **pipeline** on them and returns a **results datapack** with a **verdict**. One sequencer instance manages exactly one lab.

### 1.2 Conventions

The key words MUST, MUST NOT, REQUIRED, SHALL, SHOULD, SHOULD NOT, MAY and OPTIONAL are to be interpreted as described in RFC 2119.

`<x>` is a placeholder. `a | b` is an alternative. Durations are written `30s`, `2m`, `15m`, `1h`; sizes `200MB`, `20GB`.

### 1.3 Terminology

The datapack side and the lab side use disjoint vocabularies; a term of one never denotes an entity of the other.

| Datapack side (what a test needs) | Lab side (what exists) |
|---|---|
| **slot**: one resource needed, typed by a role | **component**: one managed element (host, instrument, emulation) |
| **stage slot**: a group of slots to be bound inside one stage, typed by a stage type | **stage**: a named set of components, unit of locking; typed by a **stage type** |
| **ad hoc slot**: a slot in no stage slot | **ad hoc component**: a component in no stage |
| **slot class**: the inputs of a slot, per configuration | **lab role**: abstract set of named commands |
| **setup configuration**: the named set of slots and stage slots | **component class**: implementation of the commands of some roles |
| **test definition**: setup + dictionaries + pipeline | **lab inventory**: stages, components, variables |

Other terms:

- **Run**: one activation of one datapack. Identified by a run id.
- **Campaign**: a list of test definitions generated with the same dataset versions and run together (§5.4). Its runs, submitted together, form a **campaign run** (§9.6).
- **Binding**: the mapping slot → component and stage slot → stage of a run.
- **Phase / step**: units of the pipeline (§6).
- **Verdict**: `succeeded | failed | error` (§9.4).
- **Baseline**: provenance record of a datapack, checked for **conformity** (§5.3).
- **Sequencer component**: the sequencer host, modelled as an ad hoc component with `transport: local` and `tsq_lock: none`.

---

## 2. Architecture

- A single process, `tsqd`, runs one asyncio event loop. There is no broker and no worker pool; all remote work is I/O-bound.
- State (queue, locks, resource states, history, events) is held in one SQLite database in WAL mode.
- `tsq run --local` executes one datapack in the foreground against the same store and locks as `tsqd`.
- All functionality MUST be reachable from the CLI and the REST API. The web UI is a client of the REST API and the kernel MUST NOT depend on it.
- The lab definition and the test definitions are YAML files in git repositories. The sequencer reads them; only the optional Editor (§12.4) writes them.

Modules (`src/testsuit/sequencer/`): `config` (tsq.yml), `inventory`, `library` (roles, classes, command resolution), `lab` (git state, reload), `model` (pipeline), `intake`, `store`, `scheduler`, `monitor` (probes, self-test), `engine` (steps, DAG), `sequencer` (run lifecycle), `results`, `transports/`, `api`, `editor*`, `daemon`.

---

## 3. Configuration

One YAML file, `tsq.yml`, located by `--config`, else `$TSQ_CONFIG`, else `/etc/testsuit/tsq.yml`. Every key has a default; the minimal file sets `lab.folder`. `storage.*` paths are relative to `storage.root`; other relative paths are relative to the file.

| Key | Default | Meaning |
|---|---|---|
| `lab.folder` | REQUIRED | git clone of the lab repository |
| `lab.inventory` | `lab.yml` | inventory file in `lab.folder` |
| `lab.require_tag` | `true` | warn unless the clone is on a clean tag |
| `storage.root` | | base of the storage paths |
| `storage.{incoming, runs, results}` | | queued datapacks, run folders, results zips |
| `storage.database` | `tsq.sqlite` | state store |
| `storage.min_free_space` | | a run MUST NOT be activated below it |
| `storage.max_run_output` | | collected outputs above it end the run in `error` |
| `storage.large_files` | `{threshold: 200MB, mode: zip}` | `mode: zip \| external` (§11) |
| `storage.retention` | `{runs: 30d, results: forever}` | `{runs: <duration>, results: <duration> \| forever}`; `runs` counts from the end of the run, or from when its folder was unpacked again (§11) |
| `server.listen` | `0.0.0.0:8640` | REST API and UI |
| `server.ui`, `server.editor` | `true`, `false` | serve the UI; enable the Editor |
| `server.workspaces` | `[]` | tests repositories offered by the Editor |
| `server.title`, `server.logo` | | UI branding |
| `queue.default_priority` | `50` | |
| `queue.priority_aging` | `off` | e.g. `+1 per 30 min` |
| `selftest.before_each_run` | `true` | §4.7 |
| `selftest.when_idle` | `true` | §4.7 |
| `selftest.timeout`, `selftest.max_attempts` | `60s`, `3` | |
| `monitor.interval` | `30s` | default probe period |
| `plugins` | `[]` | plugin modules in addition to installed entry points |

`tsq config` MUST print every effective setting with its source (file or default).

---

## 4. Lab model

### 4.1 Inventory

The inventory is an Ansible YAML inventory, read by the sequencer's own loader (Ansible is not a dependency).

- Each group with `tsq_stage_type` is a **stage**; its hosts are the stage's components.
- A host in several stage groups is **shared** by them. A host under `all.hosts` only is **ad hoc**.
- Variable precedence: host > stage group > `all.vars`. A component shared by stages receives only group values its stages agree on.
- Variables MAY be nested or written with dotted names (`a.b: v` ≡ `a: {b: v}`); levels merge key by key. `tsq_*` variables MUST be given whole.

Sequencer variables (prefix `tsq_`, plus the standard `ansible_host`, `ansible_user`, `ansible_port`, `ansible_ssh_private_key_file`):

| Variable | Level | Semantics |
|---|---|---|
| `tsq_stage_type` | group | makes the group a stage of that type |
| `tsq_extends` | host | the component class (or another host) it extends. Default `linux_system` |
| `tsq_role` | host | its role, when the class supports several roles or none |
| `tsq_commands`, `tsq_status`, `tsq_selftest`, `tsq_collect` | host, group | overrides (§4.4) |
| `tsq_transport` | any | `ssh` (default), `local`, or a plugin's |
| `tsq_remote_root` | any | folder of the component's inputs, outputs and pid files |
| `tsq_lock` | host | `exclusive` (default) \| `shared` \| `none` |
| `tsq_tags` | host, group | tags matched by slot `tags` (§8.1). A component's or stage's id is implicitly one of its tags |
| `tsq_hosts` | `all.vars` | name → address table. Addresses equal to a name are translated by the sequencer (SSH, HTTP, `component.host`); nothing is written to `/etc/hosts`. Names MUST NOT chain |
| `tsq_shell` | any | shell for commands. Default: `/bin/sh` for steps and commands, login shell for probes and self-tests. MUST be POSIX |
| `tsq_ssh_host_key` | any | pinned host key(s) or `SHA256:` fingerprint(s). When set, `known_hosts` is not consulted. A malformed value MUST fail the load |
| `tsq_python` | any | interpreter for `python` steps |
| `tsq_library` | `all.vars` | folders of the lab library (§4.2) |
| `tsq_description`, `tsq_label`, `tsq_icon`, `tsq_links` | | documentation and diagram only; MUST NOT affect execution, locking or binding. A `tsq_links` target absent from the inventory MUST fail the load |

Removed names (`tsq_type`, `tsq_types`; library keys `components:` and `models:`) MUST be refused with the replacement named.

### 4.2 Library: roles and classes

Library files (in `tsq_library` folders) hold `roles:`, `component_classes:` or both. Plugins MAY register roles and classes in Python.

A **lab role** is abstract:

```yaml
roles:
  <role>:
    extends: <role>                 # inherits its commands, inputs and outputs
    description: <text>
    commands:
      <name>: <description>
      <name>: {description: <text>, params: {<param>: <description>, ...}}   # parameters: no value here
    inputs:                         # expected in the input folder
      - {name: <path>[/], type: file|folder, optional: <bool>, description: <markdown>}
    outputs:                        # foreseen in the output folder, once collected
      - {name: <path or glob>[/], type: file|folder, optional: <bool>, description: <markdown>}
```

`outputs` MUST stay in the output folder (no `..`). After a `do: collect` step succeeds on a slot, every output of its role not `optional` MUST be present in `data/<slot>/output/` (a pattern: at least one match of its type), else the step is `failed` naming the missing ones; an optional one missing is only noted in the log. The Editor proposes the outputs of each slot's role as `exploit` data (§6.8).

A **component class** is concrete:

```yaml
component_classes:
  <class>:
    extends: <class>                # inherits implementations, status, self-test, roles
    roles: [<role>, ...]            # roles it supports; inherited unless redefined; none for a base class
    transport: <name>
    commands: {<name>: <implementation>, ...}   # an implementation MAY hold params: {<param>: <default template>}
    status: {probe: <implementation>, states: {<state>: <rule>, ...}, via: <ref>, interval: <duration>}
    selftest: [{id: <id>, run: <cmd>, timeout: <duration>}, ...]
```

Constraints (checked at load; a violation MUST fail the load unless marked *warning*):

- A class MUST implement every command of every role it lists.
- A host MUST implement every command of its role. Implementing a command its role does not list is a *warning*.
- A host's role is: the single role its class supports; else `tsq_role`, which MUST be one of the class's roles when the class lists any.
- `extends` chains MUST be acyclic.
- Command parameters: a role declares them by name per command; a sub-role MAY add parameters to an inherited command. A role MUST NOT give them values. An implementation of a command its roles list MUST read (`params.<name>`) and give defaults to only the parameters they declare.

### 4.3 Built-ins

| Name | As role | As class |
|---|---|---|
| `component` | root, no commands | state `unknown` unless a `status` probe is defined |
| `linux_system` (extends `component`) | `deploy`, `collect`, `kill` | `deploy`: SFTP `data/<slot>/input/` → `<input_dir>`; `collect`: `<output_dir>` → `data/<slot>/output/`; `kill`: §10.2. State: `offline` if SSH fails, `running` while a process group of the sequencer is alive, else `idle` |

### 4.4 Command resolution

The set of commands of a component is the command set of its role and all its ancestor roles. The implementation of each is resolved in this order, the later entry winning:

1. the class chain, root first;
2. `tsq_commands` of the component's stage group;
3. `tsq_commands` of the host;
4. an inline action in a pipeline step (§6.3).

The default parameter values (`params`) of an overriding implementation are merged over those of the one it overrides, its own winning.

A datapack MUST NOT define commands. `commands` or `extends` in a slot class, and `commands` in a slot, MUST be refused.

### 4.5 Command implementations

An implementation is exactly one of:

| Kind | Form | Semantics |
|---|---|---|
| `run` | `{run: <script>, detach, timeout, via, env}` | §4.5.1 |
| `http` | `{http: {...}, via}` | §4.5.2 |
| `python` | `{python: {...}}` | §6.4 |
| `signal` | `{signal: <SIG>}` | sent to the process groups the run started on the component |
| `wait_for` | `{wait_for: {...}}` | §6.5 |
| plugin | `{<step type>: ...}` | §13 |

Every implementation MAY carry `comments` (Markdown). Comments MUST NOT be executed or rendered.

#### 4.5.1 `run`

- The script MUST be executed as one shell script with `set -e`: lines share one shell, and the first failing line ends it with its status.
- Steps and commands run by hand MUST be traced (`set -x`, prefix `+tsq$ `). Standalone comment lines are emitted as no-op commands so they appear in the trace. `expect.stdout/stderr` MUST be matched against output excluding trace lines.
- `detach: true` starts the process and returns; it stays tracked by its pid file (§10.2).
- `via: <ref>` runs the shell on another component (`<ref>` is a role of the component's stage, an ad hoc role, or a host id). Pid files are on the `via` component. Inputs are NOT copied to it: `deploy`/`collect` always target the component itself, and `component.input_dir` names the folder on the component.

#### 4.5.2 `http`

```yaml
http:
  url: <url>                         # a plain string ≡ GET of that URL
  method: GET|PUT|POST|...           # default GET
  headers: {...}
  params: {...}
  json: <value> | body: <text> | body_file: <path>   # at most one
  extract: <jq expression>           # a path (., .a.b, .a[0], .a[-1], .a[].id: a list, ."k", .["k"]) or any jq
                                     # filter, MAY span several lines; several values: their list, none: null
  expect: {status: [<int>...], regex: <re>, json: <subset>}   # default: status < 400
  save: <file>                       # whole answer to data/<slot>/http/
  timeout: <duration>
  verify: <bool>
```

- Expected answer → success; unexpected answer → `failed`; no answer (refused, timeout, unreadable `body_file`) → `error`.
- Sent from the sequencer host, or with `curl` from the `via` component.
- `body_file` relative paths resolve in the run folder on the sequencer host.
- Header values whose name matches a secret pattern (`Authorization`, `*token*`, `*key*`, `cookie`...) MUST be masked in logs and UI.

### 4.6 State monitoring

States: `offline` (unreachable), `idle`, `configured` (deployed, not started), `running`, `broken` (reachable, faulty), `unknown`.

- The probe runs every `status.interval` (default `monitor.interval`), more often for components held by an active run.
- `states` rules are evaluated in order on the probe output; the first match wins. Rules are `{regex: <re>}` or `{exit: 0 | nonzero | <int>}`.
- A probe `run: [<check>, ...]` runs every check, whatever the others gave; a check is a shell command (`<cmd>` or `{run: <cmd>, timeout}`, each in its own shell, OK on exit 0), `{http: <request>}` (OK when the answer is the expected one) or `{<plugin probe>: <spec>}`, mixed freely. Regex rules see the outputs in order (a request: its body), `exit` sees the first non-zero status (a request not as expected: its HTTP status; 0 if none). The state is `offline` only when no check answered.
- The sequencer tracks an **expected** state (`configured` after `deploy`, `running` after `start`, `idle` after `stop`). A mismatch raises an event and, per `on_state_mismatch: warn | fail` (default `warn`), fails the step.
- `broken` and `offline` components MUST NOT be bound by new runs.
- **Lock state** (free / held, holder) is independent of the monitored state.

### 4.7 Self-test

Before deploying anything, once its resources are locked, a run MUST self-test every bound component in parallel (when `selftest.before_each_run`).

- Checks are cumulative along the class chain: `component`: probe answers and state ∉ {`broken`, `offline`}; `linux_system`: + SSH login, `<remote_root>/<component>/` writable, free disk ≥ `min_free_space`, clock offset ≤ `max_clock_offset` (default 1s); classes and hosts add `selftest` entries.
- Under `status.via`, the `linux_system` checks are `skipped`, and `run` checks execute on the `via` component.
- **Pass**: results are written to `selftest.yml`; the run proceeds.
- **Fail**: nothing is deployed; failing components are marked `broken` or `offline`; the run's locks are released and the run returns to the head of its priority in the queue. After `selftest.max_attempts` failures the run ends with verdict `error`.
- A component marked by a failed self-test MUST stay unavailable until a self-test passes, regardless of its probe. With `selftest.when_idle`, a component that becomes `idle` and is not held is re-tested (at most once a minute when held back by a failed self-test).

**Bypass.** A check other than `login` and `status` MAY be bypassed for ≤ 72 h with a mandatory reason (`POST /components/<id>/bypass {check, hours, reason, by}`; `hours: 0` ends it). A bypassed check still runs and is reported. A run whose self-test sees a bypassed check fail MUST be flagged non-conform (§5.3) with the check, its failure, the bypass end, author and reason.

### 4.8 Maintenance, hold and manual commands

- `maintenance` (repair) and `hold` (personal use) take a stage or component out of scheduling, with an optional note, until `resume` / `release`.
- A component in maintenance (or in a stage in maintenance) and not held by a run MAY have its `run`, `signal`, built-in `kill` and `http` commands executed by hand. `deploy`, `collect`, `wait_for` and `python` require a run and MUST NOT be offered.
- Manual commands render with lab keys only (§7), the defaults of their parameters as `params.*`, plus keys set by hand. Detached processes go under `<remote_root>/.tsq/manual/<component>/`.
- A component none of whose `run` commands runs on itself (each has a `via`) MUST NOT be reached directly to list or run its manual commands: its remote root is used as configured (`~` not expanded), with no note.
- Each manual command MUST be recorded as an auditable `manual_command` event, including the keys set by hand and the values they overrode.

### 4.9 Lab folder and reload

- `lab.folder` SHOULD be a git work tree on a clean tag. Otherwise a warning MUST be shown (startup, `tsq lab status`, UI banner, and every run activated meanwhile); it MUST NOT stop the sequencer. The git state (commit, tag, clean) is recorded in each results baseline.
- The inventory is loaded at startup and reloaded ONLY on demand: `tsq lab reload` (folder as is) or `tsq lab reload --pull [<rev>]` (fetch, then a fast-forward of the folder's branch when <rev> is further on it, else a checkout; without <rev>: the latest commit of the branch, or for a detached HEAD of the branch it pushes to, §12 Sessions). A reload MUST be validated first and refused if invalid, leaving the previous revision in place.
- Running runs keep their binding; a reload applies to subsequent activations.

---

## 5. Datapack model

A datapack is the BagIt zip built by the `datapack` tool from a test definition. It MUST be self-sufficient: a run uses its contents only (inputs, `context.yml` with every resolved key, `definition/` with the test definition, setup, included sub-pipeline files and the criteria its exploit steps name (§6.8) and the pictures its manual steps show (§6.9), `datapack.yml`, `baseline.yml`).

### 5.1 Setup

```yaml
slot_classes:
  <slot class>:
    role: <role>                       # OPTIONAL; Editor uses the role's `inputs`
    configurations:
      <name>:
        inputs:
          - {source: <path>, target: <path>, method: template|copy|generate,
             generator: <name>, options: {...}, postprocess: [...]}
configurations:                        # setup configurations
  <name>:
    stage_slots:
      <id>: {type: <stage type>, tags: [...]}
    slots:
      <id>: {slot_class: <slot class>, configuration: <name>, role: <role>,
             stage_slot: <id>, tags: [...], needs: [<slot>...]}
```

- `method: generate` calls a registered generator `fn(source, target, keys, options, slot)`; an unregistered name is executed as a script relative to the setup folder with `source target keys.json`.
- `needs` orders slot generation topologically (cycles MUST be rejected). A dependent slot sees `slots.<id>.input_dir` and `read_input('<id>/<file>')`.
- Templates are rendered at **generation**, before binding: a run-time key (§7) in an input template MUST be an error that says so.
- Legacy names (`component_types`, `slot_types`, a slot's `type`, `components`, `stages`, `realms`, `stage`, `realm`, step `component`, `for_each: components`) MUST be refused with the replacement.

### 5.2 Test definition

```yaml
id: <id>                                          # part of the datapack name
name: <text>
description: <text>                               # OPTIONAL
setup_file: <setup file>                          # relative to the test definition
setup_config: <name>                              # a configuration of the setup; `datapack --setup-config` MAY replace it
dictionaries: [<file>...]
user_keys: {<key>: {description: <text>, choices: [<value>...]}}   # asked at each generation; no choices: free text
reqs: [<requirement>...]                          # OPTIONAL, §5.5
pipeline: {...}                                   # §6
```

Unknown fields MUST be refused. Every field but `pipeline` is rendered with the dictionary keys at generation; `pipeline` is kept as written and rendered at run time (§7). A key of `user_keys` MUST be set by hand at each generation, to one of its `choices` when it lists some.

`docs/ai/testdef_prompt.txt` (repository root) is a prompt that lets an AI write a test definition from a test specification. It condenses §5.1, §5.2, §6, §7 and the scenario format, and MUST be kept consistent with them.

### 5.3 Baseline and conformity

`baseline.yml` (covered by the BagIt tag manifest) records:

- `datapack`: `{name, format, conform}`;
- `sources`: each original input with `{role, path, repo, commit, tag, clean}`, and each dataset with `{id, importer, source, version}`;
- `tools`: name and version of testsuit, plugins and processing libraries;
- `files`: each generated file with its sources, method, generator and sha256;
- `keys_set_by_hand`, `versions_set_by_hand`;
- `requirements` (§5.5): `{sources_file, sources: [{name, backend, revision}], linked, problems}`;
- for results only, `run`: sequencer and plugin versions, lab inventory and library git state, the binding, each component's `version` command output (read before the first phase), and for a run of a campaign its `campaign_run` and `routing`.

A datapack is **conform** iff every source is a clean tagged git state, every dataset version is confirmed, no defined key is overridden by hand, every dataset version set by hand is a tag of its source, and it links no unknown, deleted or rejected requirement (§5.5). `--nocheck` produces `conform: false`.

An **expected baseline** is a partial baseline (values MAY be glob patterns) checked by `datapack --check-baseline`, at build (`--baseline`), at submission (`tsq submit --baseline` or `pipeline.baseline`), and before the first phase for the `run` part.

A non-conform datapack or run MUST be accepted, never rejected. It MUST be flagged in every interface, its mismatches recorded in the run record and in the results baseline (`run.conform: false`), and its results zip suffixed `_NONCONFORM`. The verdict is unaffected.

### 5.4 Campaign

A campaign file (`<name>.campaign.yml`, not templated):

```yaml
id: <id>
name: <text>
description: <markdown>
priority: <0..100>                   # OPTIONAL, of its runs
testdefs:                            # relative to the file; at least one; (testdef, setup_config) unique
  - <testdef file> | {testdef: <file>, setup_config: <name>, priority: <0..100>, keys: {<dotted.key>: <value>}}
routing:                             # OPTIONAL (§8.1)
  stage_tags: [<tag>...] | {<stage type>: [<tag>...]}
  component_tags: {<role>: [<tag>...]}
datasets: {<dataset id>: <version>}  # every dataset its test definitions import
baseline: <expected baseline file>   # OPTIONAL, replaces the test definitions' own
keys: {<dotted.key>: <value>}        # OPTIONAL, pinned for every entry (an entry's own keys over them)
```

- `datapack <file>.campaign.yml` MUST generate the datapack of every entry into one folder with a `campaign.yml` manifest (`id`, `git`, `conform`, `priority`, `routing`, `datasets`, `keys`, `given_keys`, `expected_baseline`, `datapacks: [{testdef, setup_config, priority, zip, conform, keys}]`), or none: a failure MUST leave no folder.
- Keys are shared by name: a key given at generation (`--set`) applies to every entry. The keys a campaign pins (`keys`, then the entry's `keys`) are set like keys set by hand, recorded in `keys_set_by_hand` with `pinned_by_campaign: true` (§5.3: overriding a defined key makes the datapack non-conform). A key the campaign pins in `keys` given at generation MUST be refused; an entry's own `keys` are kept over those given. Each entry MUST satisfy its test definition's `user_keys` (given, one of its choices).
- Every dataset a setup imports MUST be in `datasets`, else the generation MUST fail naming it. Its version replaces the written one. Versions set by hand MUST be refused. A dataset pinned and imported by no entry is a warning.
- Each datapack records `versions_pinned_by_campaign` (`{dataset, version, overrides, was, tag}`), `pinned_by: campaign` on its dataset sources, `campaign: {id, name, file}`, and the campaign file as a source of role `campaign` (§5.3). It is conform iff the §5.3 conditions hold, the campaign source included, and each pinned version that overrides the written one is a tag of its source. Keys `campaign.{id, name, file}` are available at generation.

### 5.5 Requirements coverage

A **requirement** is `{id, path, title, description, rationale, tags, comments, version, status, url}`, read from a **source** by a **backend** (§13). Its `path` is node ids joined by `/`. `status` `deleted` or `rejected` makes it **inactive**.

Sources file: `requirements.yml`, the nearest one from the test definition's folder up to the git root of its repository.

```yaml
default: <source>                    # OPTIONAL, default: the first source
sources:
  <source>: {backend: <name>, options: {...}, credentials: {<name>: env:<VARIABLE> | file:<path>},
             vars: {<name>: <value> | {value, mandatory}}}
```

Variables: a value MAY be a Jinja2 expression of the datapack keys (none outside generation; a fallback is a Jinja2 `default` filter); a mandatory variable without a value MUST fail. A backend whose factory takes a `vars` keyword argument gets them; another gets its options with `{{ <name> }}` replaced. The snapshot records each source's variables as resolved (`vars`) and its mandatory ones (`context`).

Credentials MUST come from the environment (`env:<VARIABLE>`) or a file (`file:<path>`, its content with trailing white space dropped; relative to the sources file), such as a Docker secret; a literal value MUST be refused. A file with `requirements` and no `sources` is the single source `reqs` of the built-in `yaml` backend, whose file is `{nodes: {<path>: <label>}, requirements: {<id>: {path, title, ...}}}` (option `file`: another file; `url`: a link template with `{id}`; default `version`: a hash of the content). The built-in `reqif` backend reads a ReqIF file (`file`: `.reqif`, or `.reqifz`: a zip of them): specifications are top nodes, heading objects (type named like heading, chapter, section or folder, or `heading_types`; else a heading attribute and no text) nodes under them, other objects requirements in the node of their headings; id, title, description (XHTML as text), rationale, status, comments and tags come from the attributes the options name, else the usual ones; enumeration values are their names; version: `version_attribute` (`LAST-CHANGE`), else a hash of the content. The built-in `rest` backend reads a JSON REST API: `base`, `auth` (`bearer`, `basic`, `header`, `query`, naming credentials), `headers`, `steps: [{url, extract: {<name>: <jq> | {jq, mandatory}}, cache, method, body}]` (run first, in order; each variable one value at most), `list: {url, items, next, method, body}` (`items`, `next`: jq; pagination by `next`, else `{{ page }}` from `first_page` or `{{ offset }}` until a page has no item, at most 1000 pages), optional `get: {url, items}` (`{{ ids }}`, or `{{ id }}` with 404 meaning unknown); optional `item_vars: {<name>: <jq>}` (on each list item) and `details: [{url, fields, cache, method, body}]` (per requirement, `{{ id }}` and the item variables in the URL; fields by jq on the answer, added to the list's, a later call winning, never `id`, `path`, `version`; a 404 leaves them out; cached by request and the requirement's version; at most 8 at once), made only by `get` (generation, one requirement opened), never by `tree`, `list` or the coverage's version check (`brief(ids)`, default `get`); `{{ <name> }}` in URLs (URL-encoded), headers and bodies, `$<name>` in jq (an expression MAY span several lines), `fields` (jq per field, `id` required; default `.<field>?`, none for `url`), `url`, `timeout`, `verify`; the tree is the paths of the requirements. An error of a call MUST name the call (`step <N>`, `list, page <N>`, `get`), its method and its full URL (query parameters of `auth` included), with every credential value masked (`***`).

- **Links in input files.** Generation MUST scan the text files of `data/*/input/` once generated for `@uid{<id>}` (a test case, until the next `@uid` of the file) and `@tr{<reference>[, ...]}` (references verified by the run verdict, of the test case above, else of the file), record them in the snapshot (`cases: [{uid, file, line}]`, `inputs: [{file, line, uid, reqs}]`, files relative to the datapack root) and resolve them like the others. A test case id defined twice is a problem. Binary files and files over 10 MB are not scanned.
- **Links.** A test definition's `reqs` and any check's `reqs` (a mexploit scenario entry, beside `description`) list references `[<source>:]<id>[@<version>]`. A prefix that names no source is part of the id. `@<version>` pins the version the link was written against.
- **Snapshot.** Generation MUST resolve every link of the test definition and of the checks of the criteria its exploit steps name (§6.8), and write `definition/requirements.yml`: `{format, default_source, sources, testdef: [<key>], checks: [{step, criteria, scenario, file, check, reqs}], requirements: {<key>: {..., pinned, changed}}, problems, warnings}`. A key is the id, prefixed by its source unless the default one. Scenarios are rendered with the datapack keys, unknown keys empty.
- An unknown or inactive requirement, a missing sources file, or a source that cannot be read MUST fail the generation; with `--nocheck` the snapshot and the baseline record them and the datapack is not conform. A pinned version that is not the current one is a warning (`changed: true`).
- **Server down** (a connection refused or timed out, HTTP 502, 503 or 504): a requirement or issue server down MUST NOT be called again for a while (30 s, doubling at each new failure, at most 5 min; its calls then fail at once), except by the Editor's tests. The requirements a remote source answered are kept on the host (`$TSQ_CACHE`, else `$XDG_CACHE_HOME/testsuit`, else `~/.cache/testsuit`); its server down, generation MUST use them when every one it needs was read before: each gets `stale: <when it was read>` in the snapshot, and a warning names the source (not a problem: the datapack stays conform). A requirement never read is a problem as above. Issue lists and issues MUST be shown as last read by the sequencer when their server is down, the list saying so (`errors`); bindings keep their issues as last seen (§11.3).
- **Status of a link** in a run, as reviewed (§11.1; a link whose status a review changed is marked `reviewed`): a check link from its check (`SUCCESS`/`ACCEPTED` → `passed`, `FAILED`/`REJECTED` → `failed`, `ERROR` → `error`), `skipped` when its exploit step was, `error` when the step failed or errored without reporting the check, `not run` otherwise; a test definition link from the verdict (`succeeded` → `passed`, `failed`, `error`).
- **Status of a requirement** over a set of links (one test, or several runs of a campaign): `failed` if one is, else `error` if one is, `passed` if all are, `partial` if one passed, `skipped` if one was skipped, else `not run`; `uncovered` without links. Inactive requirements are never `uncovered`. A link whose tested version differs from the current one is **changed since**.
- **Coverage** (`reqs matrix | uncovered <targets> [--paths P... [-r]]`): targets are test definitions, campaign files (their test definitions), datapack and results zips, and folders of zips (a campaign's results). It MUST give both a requirements × tests matrix, rolled up per path node, and a tests × requirements view (each test, its test definition and each check with the requirements it verifies), as text, YAML, CSV or one self-contained HTML page linking to the requirements (`url`), and with `--sequencer` to the runs and their exploit reports. `--paths` adds the requirements under those nodes (with `-r`, their descendants) as rows, `uncovered` when not linked.

---

## 6. Pipeline

### 6.1 Structure

```yaml
pipeline:
  timeout: <duration>
  reserve: phase | all                 # default phase (§8.4)
  baseline: <expected baseline file>
  include: [<file>...]                 # shared sub-pipeline files, copied into the datapack
  subpipelines: {<name>: {params: {...}, steps: [...], comments: [...]}}
  phases:
    - id: <id>
      needs: [<phase>...]
      stage_slots: [<stage slot>...]   # locked for this phase
      slots: [<ad hoc slot>...]        # locked for this phase
      inputs: [{from: "<phase>:<slot|*>/output/<path>", to: "<slot>/input/<path>"}]
      steps: [<step>...]
      finally: [<step>...]
      timeout: <duration>
      comments: [<card>...]
```

### 6.2 Steps

```yaml
- id: <id>
  # target (at most one)
  slot: <slot>
  for_each: [<slot>...] | slots | <role> | "{{ params.x }}"
  # action (exactly one)
  do: <command> | run: <script> | python: {...} | http: {...} | exploit: {...} | manual: {...}
  | push: ... | pull: ... | delay: <duration> | wait_for: {...} | uses: <sub-pipeline> | <plugin type>: ...
  # options
  needs: [<step>...]
  if: <jinja expression>
  expect: {exit: [<int>...], stdout: <re>, stderr: <re>}   # default exit [0]
  timeout: <duration>
  retries: <int>
  detach: <bool>
  continue_on_error: <bool>
  env: {<name>: <template>}
  with: {<name>: <template>}           # for uses, do (§6.2.1) and exploit (§6.8); refused on other actions
```

- A step targets the components bound to its slot(s), within its phase. `for_each` fans out in parallel. A step without a target runs on the sequencer host, in the run folder.
- `do: <name>` runs the resolved implementation (§4.4) of that command of the slot's role.
- `if` sees `succeeded('<step>')`, `failed(...)`, `errored(...)`, `skipped(...)`, `verdict`. A false `if` makes the step `skipped`.
- Validation at submission (and by `tsq validate`) MUST reject: cycles in `needs` (steps and phases), unknown ids, and steps on slots not held by their phase.

#### 6.2.1 Command parameters

- `with` on a `do` step gives the command's parameters by plain name; a name the role (§4.2) does not declare for the command MUST be rejected at intake. A step MUST NOT override any other key for a command: names are never dotted.
- At run time each parameter takes the step's value, else the default of the bound implementation, each rendered with the step's keys (run-time keys included). A parameter with neither MUST make the step `error`, naming it.
- The command is rendered with `params` = its parameters only (an enclosing sub-pipeline's `params` are not visible). The values MUST be logged and recorded in the step record (`lab_command.params`).

### 6.3 Ordering

- Within a phase, steps form a DAG over `needs`; every step whose `needs` are complete is started. At most one non-detached command runs at a time per component.
- `finally` steps MUST run after the phase's steps end, whatever their outcome, including on abort (§10). `finally` steps without `needs` run in written order, except that a step already awaited (through `needs`) by an earlier one runs first.
- Phases are ordered by `needs`.

### 6.4 `python` action

```yaml
python:
  script: <path on target> | code: <inline source>
  args: [<template>...]
  interpreter: <path>                  # default: tsq_python, else python3; on the sequencer host, tsqd's interpreter
```

- Before execution, every key visible to the step MUST be written as JSON under `<remote_root>/.tsq/<run>/<component>/python/<step>/`, path in `$TSQ_KEYS`, preloaded as `keys`.
- `script`, `args`, `env`, `interpreter` are templates; inline `code` is not.
- An uncaught exception MUST exit with status 70 and the traceback on stderr, giving `error`.
- On the sequencer host the script runs as a separate process, never inside `tsqd`. The target requires only a Python 3 interpreter.

### 6.5 `wait_for`

Kinds: `state` (§4.6), `tcp`, `http` (§4.5.2 `expect`), `file`, `log` (`{file, regex}`), `cmd` (exit 0), plus plugin ready checks. Each takes `timeout` and `interval`; expiry gives `error`.

### 6.6 Sub-pipelines

- `uses: <name>` expands to the named group; `with` sets `params.*` over declared defaults.
- The `uses` step's `needs` apply to the group's entry steps; dependants of the `uses` step wait for all of its steps. Its result is the worst of its steps' results.
- Expanded step ids are namespaced `<uses id>.<step id>`.
- Sub-pipelines MAY nest; cycles and unknown names MUST be rejected. Definitions in `subpipelines` are private to the test definition; `include` files are shared.

### 6.7 Comment cards

`comments` on phases and sub-pipelines: `<text>` or `{text, step, x, y, width, color}`. `color` is one of `yellow` (default), `orange`, `red`, `pink`, `purple`, `blue`, `green`, `grey`, or `#rgb`/`#rrggbb`; anything else MUST be rejected. They MUST NOT be executed or rendered; a card pinned to a missing step is shown free with a warning, never an error. `GET /runs/{id}/pipeline` gives each phase's non-empty cards and those of every sub-pipeline it uses (at any depth, once per `uses` step) as `{text, step, color, group, part}` (`group`: the namespaced `uses` step id, empty for the phase's own); the run graph shows each in its group, a pinned one linked to its step by a line.

### 6.8 `exploit` action

Binds criteria (mexploit scenarios) to the run contents they check.

```yaml
- id: speed_criteria
  needs: [...]
  exploit:
    criteria: [<scenario folder>...]   # relative to the test definition; a folder of scenario folders allowed
    data: ["[<phase>:]<slot|*>/<path>"...]   # files or folders of the run folder, globs allowed
    name: <report title>               # OPTIONAL
    description: <text>                # OPTIONAL
```

- The step has no `slot` nor `for_each`; it runs on the sequencer host. `criteria` and `data` MUST be non-empty. `criteria` MUST NOT be a template, so the binding is known without running: tools read it from the pipeline as written (phases, `finally`, sub-pipelines, included files).
- Validation (§6.2) MUST reject a `data` entry naming an unknown phase or slot. The phase is informative: the entry reads `data/<slot>/<path>` of the run folder, once collected.
- Generation MUST copy each named criteria entry into `definition/<path>` (`definition/criteria/<name>` for a path outside the test definition), and only those; a missing one MUST fail the generation. The Editor reports it as a problem of the test definition.
- `with` gives keys the criteria read: `params.<name>` keys (merged into `params`), or plain keys. A plain key the test's keys define MUST be rejected (at intake, by the Editor, and at run time); any plain key is a *warning*. Values are templates rendered with the step's keys.
- A key the scenarios read that neither the step's keys nor `with` define MUST make the step `error` before exploit_runner runs, naming it; the Editor MUST report it before generation.
- At run time the step writes, under `data/_sequencer/exploit/<step>/`: `keys.yml` (the step's keys with its `with`, §7), `exploit.yml` (one exploit_runner session per criteria entry, on every matched data path), then runs `exploit_runner` in a separate process with tsqd's interpreter; its results go to `results/` there. Scenario templates are rendered with those keys.
- Result: `succeeded` on exit 0 (every check met), `failed` on exit 1 (a check not met), `error` otherwise, or when no data entry matches a file (an entry matching nothing is noted in the log), or when a criteria entry is absent from the datapack. `timeout`, `retries`, `env` apply; `expect` and `detach` do not. Without `continue_on_error`, the steps that need an exploit step MUST run even when it does not succeed, and its result MUST still count in the verdict (§9.4); `continue_on_error: false` makes them skipped, `true` leaves it out of the verdict as for any step.
- The step record (§9.3) carries `exploit: {folder, report, status, checks: [{criteria, scenario, check, status}]}`, read from `exploit_synthesis.yml`. A review MAY accept or reject a check after the run (§11.1); `GET /runs/{id}/exploit/{step}/report` serves the exploit report with the checks as reviewed (a banner, statuses changed, the raw ones kept).

### 6.9 `manual` action

An operation a person does on the bench (plug a cable, press a button) that the run waits for.

```yaml
- id: plug_cable
  needs: [...]
  manual:
    title: <text>                      # OPTIONAL, the step id by default
    text: <Markdown>                   # what to do; templates allowed
    checks: [<text>...]                # OPTIONAL: acknowledged one by one before done
    redo_from: [<step id>...]          # OPTIONAL: earlier steps the operator may run again from
  timeout: <duration>                  # OPTIONAL: no answer by then is an error
```

- The step has no `slot` nor `for_each`; it waits on the sequencer host. `text` or `checks` MUST be given; `title`, `text` and `checks` are rendered with the step's keys.
- A picture of `text` (`![alt](<path>)`) that is not a web address is a path relative to the test definition, inside its folder (an absolute path or `..` MUST be rejected). Generation MUST copy each into `definition/<path>`; a missing one MUST fail the generation. Pictures of steps in included sub-pipeline files are relative to the test definition too.
- At run time the step record (§9.3) carries `manual: {title, text, checks: [{text, done, bypassed, nonconform, comment, by, at}], base, since, note, outcome, by, at}` (`base`: the folder of the run its pictures are in, `definition/`) while the step is `running`, and the run is listed with `awaiting_manual: [{step, title, since}]` (`GET /runs`, `GET /runs/{id}`).
- The operator answers with `POST /runs/{id}/manual/{step}` (`tsq manual`). On one check (`check: <index>`): `done: <bool>` ticks it or not; `comment` sets its comment; `bypass: true` bypasses it and MUST be refused without a comment (its reason), with `nonconform: <bool>` saying whether going on without it makes the run non-conform (the operator's choice, `false` by default); `bypass: false` undoes it. `note` sets the global comment. `outcome: done` ends it `succeeded` and MUST be refused while a check is neither ticked nor bypassed; its reason names the bypassed checks with their comments, and each one bypassed with `nonconform: true` makes the run non-conform (a non-conformity naming the check, who and why) and is recorded as an intervention `bypass` (§9.7). `outcome: failed` ends it `failed` and the global comment is its reason. `outcome: redo` with `from: <step id>` (one of `redo_from`) runs that step and every step that needs it again (§9.7), this operation included, which is asked again; the global comment is the reason. `by` names who answered. Each answer is an event (`step`), so every viewer sees ticks, bypasses and comments. No answer within `timeout`: `error`. Cancel and kill end the wait like any step. The step log records each check (`[x]`, `[ ]` or `[bypassed]`, who, its comment) and the global comment; the campaign view and report list the bypassed checks of each run.
- Each `redo_from` id MUST name a step of the same phase (or of the same sub-pipeline) that this step needs, directly or not; validation (intake, `tsq validate`, the Editor) MUST reject any other.
- `retries` asks again after a failed answer; `expect`, `detach` and `with` do not apply. A run executed by another process (`tsq run --local`) cannot be answered through tsqd.

---

## 7. Template keys

All templates are Jinja2 (the `datapack` tool's environment, `StrictUndefined`). Datapack names (`slot`, `stage_slot`) give the datapack's view; lab names (`component`, `stage`) give the lab's.

| Key | Generation | Steps (run time) | Lab only (probe, self-test, manual) |
|---|---|---|---|
| dictionary keys, `<dataset>.{path,version}`, `testdef.*`, `setup.*`, `datapack.*` | ✓ | ✓ (frozen in `context.yml`) | |
| `slot.{id,name,slot_class,configuration,input_dir}` | ✓ | | |
| `slots.<id>.{id,input_dir}`, `read_input()` | needed slots | | |
| `slot.{id,name,slot_class,role,stage_slot,component,input_dir,output_dir,remote_root}`, `slots.<id>.*` | | ✓ | |
| `stage_slots.<id>.stage` | | ✓ | |
| `component.{id,host,host_alias,label,role,extends,stage,vars,input_dir,output_dir,remote_root,state}` | | bound component | the component |
| `remote_root` | | ✓ | ✓ |
| `stage.{id,type,vars,components.<host>,roles.<role>}` | | ✓ | ✓ |
| `lab.components.<host>`, `lab.stages.<stage>`, `lab.hosts.<name>` | | ✓ | ✓ |
| `via.{id,host,vars}` | | | under `via` |
| `run.{id,dir,priority}`, `phase.id` | | ✓ | |
| `params.<name>` | | in a sub-pipeline; in a lab command, its parameters only (§6.2.1); in exploit criteria, plus the step's `with` | defaults of the command's parameters |
| `succeeded()`, `failed()`, `errored()`, `skipped()`, `verdict` | | in `if` | |

`input_dir` = `<remote_root>/<slot>/input`, `output_dir` = `<remote_root>/<slot>/output`, prefixed by `.tsq/<run>/` for `shared` and `none` lock modes.

---

## 8. Binding, scheduling and locking

### 8.1 Binding

For the phase being activated:

- each stage slot binds to a free stage whose `tsq_stage_type` equals its `type` and that carries all its `tags`;
- each slot of a stage slot binds to a distinct component of that stage whose role equals or extends the slot's role and that carries all its `tags`;
- each ad hoc slot binds to a free ad hoc component under the same rule.

A run of a campaign (§9.6) carries **routing tags**: `stage_tags` are added to the tags of every stage slot (a mapping: of the stage slots of that stage type), `component_tags.<role>` to those of every slot of that role. They apply to intake checks, binding and the resource overview, and MUST NOT change the datapack.

Within a stage, slots with the fewest candidates are assigned first; each slot prefers, in inventory order, an exact-role component over one of a derived role; the assignment backtracks when a choice starves a later slot. A stage binds only if every slot gets a distinct component.

Components of a candidate stage that are in maintenance, on hold, `broken` or `offline` are **left out** (recorded as `left_out`): not locked, not self-tested, not bound. The stage remains a candidate while its other components can take every slot.

Tie-break among candidates: the stage last used by the same test definition, then the least used.

### 8.2 Freeness

A resource is free iff it is not `broken`, `offline`, in maintenance or on hold, and each of its components is free for its lock mode: `exclusive` — no other run holds it; `shared` — no other run holds it exclusively; `none` — always.

### 8.3 Queue

- Priority is an integer in [0, 100], default `queue.default_priority`, mutable while queued. Order: priority descending, then submission time ascending. Optional aging raises priority with waiting time.
- The scheduler walks the queue whenever a resource is released, a run is queued or a component recovers.
- **Backfilling**: a lower-priority run MAY start while a higher-priority run waits only on resources the waiting run cannot use.
- **No preemption**: a running run is never interrupted for priority.

### 8.4 Locking

- Granularity: the stage for staged components, the component for ad hoc ones.
- All locks a phase needs MUST be taken in one database transaction (`BEGIN IMMEDIATE`), all or nothing. Hence no partial holds and no deadlock.
- A component shared by two stages of the same run is locked once.
- Locks are recorded per run with holder and timestamp, and released only by the kernel (or the audited `tsq release`).
- `reserve: phase` (default): at the end of a phase, resources not used by the next phase are released. `reserve: all`: all phases' resources are taken at activation.
- A later phase that cannot bind keeps what the run holds and waits: re-checked every 2s for resources; after a failed self-test, re-checked after 60s, up to `selftest.max_attempts`, then the run ends `error`. `POST /runs/<id>/retry` forces the check.

---

## 9. Run execution

### 9.1 Intake

On submission (`POST /runs` or `tsq submit`), the sequencer MUST verify the BagIt checksums, unpack into `runs/<run>/`, load the pipeline, resources and `context.yml` from the zip only, validate them against the current lab (stage types, roles, commands, the `with` of `do` and `exploit` steps), check the expected baseline if any, and queue the run. Invalid datapacks are rejected; non-conform ones are flagged (§5.3).

### 9.2 Activation

Binding and locking (§8), free-space check (`min_free_space`), self-test (§4.7), then the first phase.

### 9.3 Phase execution

1. Bind and lock what the phase adds (§8.4).
2. Copy `inputs` within the run folder. Nothing is copied host to host.
3. Run `steps` as a DAG (§6.3) under the phase `timeout`.
4. Run `finally` with its own timeout.
5. Release resources the next phase does not use.

Logs: each step to `logs/<phase>/<step>[<component>].log` and the event bus; sequencer actions on the run to `logs/sequencer.log`, written incrementally. A `do` step's log MUST first name the lab command, its component, slot, role and source, then the rendered command. Each step records `lab_command {name, component, source}` in `run.yml`, and `continue_on_error: true` when it has it (so a review can recompute the verdict, §11.1).

### 9.4 Results and verdict

Step result:

| Result | Condition |
|---|---|
| `succeeded` | ran and met `expect` |
| `failed` | ran and did not meet `expect` |
| `error` | crash (signal), timeout, transport failure, unreachable or broken component, internal error, exit 70 of a `python` step, unanswered `http` |
| `skipped` | `if` false, or not reached |

Run verdict, over steps without `continue_on_error`: `error` if any step is `error` or the run was cancelled or killed; else `failed` if any step is `failed`; else `succeeded`. `error` means the test outcome is unknown, not negative.

### 9.5 State machines

```
run:   queued ─► waiting_resources ─► reserved ─► selftest ─► running ─► cleaning ─► done(verdict)
          │                 ▲                        │
          └─► cancelled     └──── self-test failed ──┘   (until max_attempts, then done(error))

step:  pending ─► ready ─► running ─► succeeded | failed | error | skipped
```

The reason of a step `error` (crash, timeout, killed, transport) MUST be kept.

### 9.6 Campaign runs

- `POST /campaigns` (`tsq submit-campaign`) takes a generated campaign (§5.4). Each datapack MUST pass intake (§9.1) with the campaign's routing tags and expected baseline; if one fails, none is queued. Runs get the entry's priority, else the campaign's (a priority given at submission overrides both), and record `campaign_run` and `routing`.
- The campaign run is `queued` while every run is queued, `running` while one has not ended, `done` once all ended. Its verdict is the worst of its runs' (`error` > `failed` > `succeeded`, a cancelled run counting as `error`). It is conform iff the manifest and every run are.
- When it becomes `done`, its report MUST be written to `storage.results/campaigns/<id>/report.html`, and again whenever a review of it or of one of its runs changes (§11.1). A repost of one of its runs reopens it.
- `POST /runs/{id}/rerun` (`tsq rerun`) runs a datapack of a campaign run again, when the bench was not set up right for example: a new run of the same zip, unchanged (intake again, same routing, priority and expected baseline), recording `rerun_of`. It takes the place of the ended run in its entry, which keeps the earlier ones in `attempts`; only the last attempt of an entry can be run again, and only once it ended. The campaign run reopens; its state, verdict and counts are those of each entry's last attempt, and the view and the report list the earlier attempts with their verdicts. Changing the datapack of a re-run is not supported.
- `POST /campaigns/{id}/cancel|kill` aborts each of its runs that has not ended (§10.1).
- Its verdict, counts and report are those of its runs as reviewed (§11.1), the raw verdict kept beside them. The report gives, per run: test definition, verdict (raw verdict and review notes when a review changed it), duration, stages or components bound, exploit checks met and not met, first `failed` or `error` step with its reason, non-conformities, and links to the run, that step's log, the exploit reports and the results zip; and the campaign's git state, conformity, pinned versions and routing tags; its review state (a `DRAFT` banner while a review is open, `Final` once signed off), the counts of accepted and rejected items, and the campaign run's comments and sign-off.

### 9.7 Operator controls

A test with a person in the loop may need a step run again, or the run taken on past a step, while its phase runs. These act on the phase whose steps run now (not on `finally` steps, nor on another phase), and only through the process executing the run (not a `tsq run --local` one).

- **Run again** (`POST /runs/{id}/steps/{step}/run_again`, `tsq step <run> <step> run-again`): the step, ended or running, and every step that needs it, directly or not, run anew. Those still running are stopped first. Their earlier records are kept in `replaced_steps` of `run.yml` (a stopped one as `cancelled`, "stopped by an operator, to run again"), and each new record counts its `passes` (2, 3...). The step log goes on with "run again by an operator: pass N".
- **Continue anyway** (`.../continue`, `tsq step ... continue --reason R`): a reason MUST be given. A running step is stopped and ends `succeeded` ("continued anyway: R"); a `failed` or `error` step becomes `succeeded` with the same reason (its earlier state kept in it), and the steps it held back run. The run is no longer conform: its non-conformities name the step, who and why.
- **Step-by-step mode** (`POST /runs/{id}/control {mode, breakpoints}`, `tsq step-mode <run> off|each|breakpoints [--breakpoint S]...`), on a queued or running run: `each` pauses after every step of the main steps of a phase, `breakpoints` after the steps listed (`<phase>:<step>` or `<step>`), `off` pauses no more. A paused step has ended; the steps that need it wait until the operator answers **Next** (`.../next`), **Run again** or **Continue anyway**. Turning a mode off, or a breakpoint away, lets the steps paused for it go on. Cancel and kill end the pauses. The run lists `paused: [{step, verdict, since}]` and `control: {mode, breakpoints}` (`GET /runs`, `GET /runs/{id}`).
- Every action, Redo from of a manual operation (§6.9) included, is recorded in the run's `interventions: [{at, action, step, by, reason, target?, steps?}]` (`action`: `next`, `run_again`, `continue`, `redo_from`, `bypass`; `steps`: the steps run again), kept in `run.yml`, and sent as an `intervention` event; a pause is a `pause` event. The campaign view and report list the interventions of each run but Next.
- A repost forgets the interventions; the step mode stays.

---

## 10. Abort and recovery

### 10.1 Commands

- `cancel`: a queued run leaves the queue (`cancelled`). A running run: pending steps are cancelled, `stop` is run on every component with a live process of the run, then after a grace period `kill`, then the current phase's `finally`.
- `kill`: `kill` at once on every component with a live process, then `finally`, then lock release.
- An abort MUST answer at once; its `stop` and `kill` commands are time-bounded. A cancel lets running `finally` steps continue; a kill stops them. A second kill of a run that has not ended stops its execution immediately (results are still packed). A run whose execution is lost is ended by cancel or kill with its locks released.
- An aborted run's verdict is `error`.
- `repost`: an ended run is re-queued under the same id and priority, its outputs removed and its datapack re-unpacked and re-validated.

### 10.2 Remote process control

On `linux_system` components every started command MUST run in its own process group (`setsid -w sh -c ...` over SSH, a new session locally), with the group id in `<remote_root>/.tsq/<run>/<component>/<step>.pid`. `kill` sends `SIGKILL` to `-<pgid>` for each pid file; a group is alive while `ps` lists a non-zombie member. Target requirements: a POSIX shell, `setsid`, `ps`. Other classes override `kill`.

### 10.3 Daemon restart

Runs found `running` MUST be marked `error`, then their `kill` and `finally` steps run in the background (bounded by the phase timeout, 10 min by default), then their locks are released. `tsqd` serves requests meanwhile.

---

## 11. Results

The results datapack is the run folder packed with the input layout:

```
data/<slot>/input/      inputs, unchanged
data/<slot>/output/     everything collected
data/<slot>/http/       saved HTTP answers
data/_sequencer/exploit/<step>/   an exploit step's configuration, keys and exploit_runner results (§6.8)
logs/                   step logs, sequencer.log
run.yml                 states, binding, verdict, step results, lab_command, non-conformities, campaign_run, routing,
                        replaced_steps and interventions (§9.7)
selftest.yml            self-test results, bypasses
baseline.yml            completed with the run part (§5.3)
```

- BagIt manifests MUST be recomputed over all contents. Output: `storage.results/<run>/<datapack>_results[_NONCONFORM].zip`.
- `collect` streams over SFTP and honours `tsq_collect: {include, exclude, max_size}`.
- Zip64; files over `large_files.threshold` and already compressed formats are stored uncompressed. With `mode: external`, such files stay in `<datapack>_results.data/` and are listed in BagIt `fetch.txt` with size and checksum.
- Outputs over `max_run_output` end the run in `error` after `finally`.
- Outputs are readable in the run folder while the run is in progress.
- After `retention.runs`, the run folder MUST be removed and the results zip kept. Browsing the files of a run whose folder is gone (`GET /runs/{id}/files/...`, the UI's Files tab, `tsq files`) MUST unpack the zip back into it, kept another `retention.runs` from then (marker `storage.runs/<run>/.restored`); files left out by `mode: external` are linked from `<datapack>_results.data/` and listed with `external: true`. When unpacking would leave less than `min_free_space`, files are read from the zip. `?results=true` reads the zip itself (the bare path downloads it).

### 11.1 Reviews

Results are immutable: `run.yml` and the results zip MUST NOT change after the run. A **review** is an overlay on them, an append-only journal in `storage.results/<run>/review.yml` (a campaign run's own in `storage.results/campaigns/<id>/review.yml`):

```yaml
run: <run id>
entries:
  - {id: 1, action: review, at, by, target: {kind: check | step | run, step, criteria, scenario, check},
     status: accepted | rejected, reason, comment, ref, was, reapplied_from: {run, entry}}
  - {id: 2, action: revoke, at, by, revokes: 1, reason}
  - {id: 3, action: sign_off, at, by, comment}
  - {id: 4, action: reopen, at, by, reason}
```

- A `review` entry has a `status` and a `reason` (mandatory), or a `comment` alone. `accepted` applies only to a `failed` (`FAILED`) target, `rejected` to a `succeeded` or `failed` one; an `error` target MUST NOT be accepted (its outcome is unknown: run it again). `was` records the raw status. A campaign run's entries are comments only (`target: {kind: campaign}`): its verdict comes from its runs.
- Entries are never changed nor removed: `revoke` withdraws one, the latest entry in force on a target wins. Only an ended run can be reviewed. The review is `none` (no entry), `open`, or `signed_off`; after a `sign_off` only `reopen` is accepted. Signing off a campaign run signs off the open reviews of its runs.
- `by`: the user an authenticating reverse proxy names (`X-Remote-User`, `Remote-User`, `X-Forwarded-User`, `X-Auth-Request-User`), else the one the request gives.
- **Effect** (the run *as reviewed*): a check accepted is `ACCEPTED`, rejected `REJECTED` (`raw_status` kept); its exploit step's state is recomputed from its checks (§6.8); a step review overrides the step's state (`raw_state` kept); the verdict is recomputed over the steps that count (§9.4), unless the raw verdict was not the worst of the raw steps (a cancelled run, an error of the run itself): then it stays and a warning says so; a run review overrides the verdict (`raw_verdict` kept). Accepting a failure does not run the steps it held back: a warning lists them. The campaign view, its report, the requirements coverage (§5.5) and the reports of §11.2 MUST use the runs as reviewed.
- **Re-run** (§9.6): the entries in force of the earlier attempt whose target has, in the new run, the same raw status are offered (`reapplicable`); `{action: reapply}` copies them with `reapplied_from`. Nothing is reapplied by itself.
- **Repost** archives the review to `storage.results/reviews-archive/<run>-<stamp>.yml`; the new results start without one.

### 11.2 Reports and exports

- `GET /runs/{id}/report` is the report of a run as reviewed: verdict, steps, exploit checks with their review notes, review journal, operator interventions, non-conformities, requirements, and links to logs and exploit reports.
- An **export** of a campaign run or of a run is built in the background (`POST /campaigns/{id}/export`, `POST /runs/{id}/export`, `{mode: synthetic | complete, include_results}`): a job `{id, state: running | done | error | cancelled, stage, done, total, file, name, size, error}` whose progress is sent as `export_progress` events (a few a second at most, not stored) and given by `GET /exports/{id}`; `POST /exports/{id}/cancel` stops it; `GET /exports/{id}/download` gives the file. An export of the same target, mode, run states and reviews is given again, not built again; a newer one replaces the older ones under `storage.results/exports/`.
- `synthetic`: the campaign (or run) report alone, one self-contained HTML file, its links to the sequencer's absolute URL.
- `complete`: every page, linked from the campaign report down: per run (`runs/<test definition>/attempt-<n>/`) its report, step logs, exploit reports as reviewed, previews of its input files (data files plotted with plotly, at most 12 parameters and 4000 points each; text files, their first 200 kB; files over 200 MB listed only), its requirements coverage and `review.yml`; the campaign's coverage. It is **one HTML file** that needs no unpacking: each page gzip-compressed and base64-encoded inside, opened on demand (`DecompressionStream`) in a frame, plotly embedded once. Over 300 MB, or with the results zips (`include_results`), it is a **zip** of the same pages as files (`README.txt`, `assets/plotly.min.js`), which `tsq view <zip>` serves on localhost without unpacking.
- Reports list the known issues of a run's failures (§11.3), as last seen.
- A log over 2 MB is trimmed: its first and last 2000 lines, and every line between that looks like an error or a warning (a word among `error`, `fail`, `failed`, `failure`, `fatal`, `critical`, `exception`, `traceback`, `panic`, `abort`, `warn`, `warning`, `denied`, `refused`, `timeout`, `timed out`, `not found`, `cannot`, `unable`, any case; at most 20000), the gaps marked.

### 11.3 Issues

**Issues file**: `issues.yml` (formerly `tickets.yml`, still read, as are datapacks and bindings written under that name), the nearest one from the test definition's folder up to the git root of its repository.

```yaml
default: <source>                    # OPTIONAL, default: the first source
classes:                             # OPTIONAL: what several sources share, with variables
  <class>: {backend, label, description, options, credentials, labels, requirements, filter,
            vars: {<name>: {mandatory: true | false, value: <default>, help}}}
sources:
  <source>: {backend: gitlab | github | jira | redmine | <plugin>, label, options: {...},
             credentials: {<name>: env:<VARIABLE> | file:<path>}, labels: [<label>...], requirements: {...},
             filter: {...}, vars: {<name>: <value>}}
  <source>: {class: <class>, label, vars: {<name>: <value>}}        # a source of a class
routes: [{source, testdef | step | criteria | check: <glob>}...]    # where a new issue goes: the first that matches
requirements: {labels: "req:{id}" | false, text: true, jira_links: true, add: true}
```

- Credentials MUST be references (§5.5); a literal value MUST be refused. A source signs in one way: its credentials MUST be those of one sign-in method of its backend (`auth_methods`, e.g. GitLab `token` or `oauth_token`; Jira `email`+`token`, `token`, or `user`+`password`; Redmine `api_key` or `user`+`password`), else the file MUST be refused. Every built-in backend takes `verify`: `false` (also written `"false"`/`no`) does not check the server's certificate, a path is the CA bundle that signs it (relative to the file's folder). Generation MUST copy the file, as written, to `definition/issues.yml` (it is not a source of the baseline); a file that does not read fails it, unless `--nocheck`. The sequencer reads a run's sources from it and resolves the credentials on its host (`file:` relative to `tsq.yml`'s folder).
- Built-in backends: `gitlab` (`url`, `project` or `group`; `token` sent as `PRIVATE-TOKEN`, or `oauth_token` as a bearer), `github` (`url` of the API, `repo` or `org`, `web`; `token`), `jira` (`url`, `project`, `issue_type`, `jql`, `cloud`; Cloud: `email` and `token`, basic; Server and Data Center: `token`, bearer, or `user` and `password`; REST API v2), `redmine` (`url`, `project` and its subprojects, `tracker` of new issues, `markup`: `textile` or `markdown`; `api_key` sent as `X-Redmine-API-Key`, or `user` and `password`, basic; REST API; no labels: an issue's labels are its tracker and category, and a new issue's are not sent; text search needs Redmine 5.0). A `group`/`org` source spans several projects: its ids are `<project>#<n>` and creation names the project.
- **Classes and variables** (as for requirement sources, §5.5): a source of a class gives only `class`, `label` and `vars`; it takes the class's keys. `{{ name }}` (Jinja2, sandboxed, an unknown variable an error) in the label, options, credentials, labels and filter is rendered with the source's variables: a class's, their value given by the source else their `value`; a mandatory one with no value, a key a source of a class MUST NOT set, or a variable the class does not declare, MUST be refused. A direct source MAY declare its own `vars`. Rendered credentials MUST still be references.
- **Filter** (`filter`, of a source or a class): the issues of its tool a source keeps, wherever it is read (listed, searched, matched to requirements, the draft's duplicates); every condition given MUST hold. `state`: `open`, `closed`, `all`; `labels` (all of them), `any_labels`, `not_labels` (case independent); `text`, `not_text` (a regular expression searched in the title and text), `title` (in the title), case independent; `projects` (globs on the project of a group source's issues); `authors`, `assignees`; `created_after`, `created_before`, `updated_after`, `updated_before`: an ISO date or date-time (UTC unless given), or a time ago `<n>d|w|m|y`. `state` and `labels` MUST be asked of the tool; the rest is applied to what it returns, at most 5,000 issues read for one search. An issue read by its reference is not filtered. A filter that does not read MUST refuse the file.
- **References**: `<source>#<id>`, `#<id>` or `<id>` (the default source), a Jira key (the Jira source of its project), or the issue's URL.
- **Target and fingerprint**: what failed in a run (as reviewed, §11.1): a check not met, a step `failed` or `error`, the verdict when not `succeeded`. Its fingerprint is a hash of the test definition and the target (a step's slot left out): the same in every run.
- **Binding**: an issue bound to a target of a test definition, by its fingerprint, in `storage.results/issues/bindings.yml` (`{id, fingerprint, testdef, target, issue: {source, id, key, title, state, url, type?, type_color?, type_icon?}, run, created, note, by, at, removed: {by, at, reason}}`). It is a **known issue** of every run of that test definition that fails there, until unbound (with a reason; the binding stays in the history). Binding the same issue to the same target twice MUST be refused.
- **New issue** (`POST /runs/{id}/issues/draft`, then `{action: create}`): source by the routes, else the default; title `[tsq] <testdef>: <target> <status>`; a body with the run (its verdict, raw verdict, end), datapack, campaign run, resources bound, links to the run, run report, exploit report and log (the sequencer's URL), the requirements the target verifies, the lines of its log that look like errors (else its last ones, at most 40), who reported it, and `tsq:fingerprint=<hex>` (in an HTML comment where Markdown renders it); in the markup of its tool (`markup` of the backend): Markdown, wiki markup for Jira, Textile for Redmine unless `markup: markdown`. Labels: the source's, and one per requirement (`requirements.labels`, when `add`). **Type** (`types: [{name, label?, default?, color?, icon?}]` and `type` in the draft, `type` in the create body), as the backend lists them (`issue_types(project)`): GitLab, the scoped labels `type::<name>` of the project or its groups (or of the group), set as that label (replacing any other `type::`), else GitLab's `issue_type` (`issue`, `incident`, `task`); GitHub, the organization's enabled issue types (none for a user's repository); Jira, the project's issue types (no sub-task); Redmine, the project's trackers. Proposed: for a failure the source's default (Jira `issue_type`, Redmine `tracker`, GitLab `issue`), else a bug-like type, else the first; for a succeeded run an improvement-like type (improvement, enhancement, feature, story, task, request), else the default. An unknown type MUST be refused. Every field MAY be changed before it is sent. The draft lists the issues already bound to the target and the open issues of the source holding its fingerprint. A person MAY give their own token to create it in their name: it MUST NOT be stored nor logged. A Jira issue gets a remote link to the run report.
- **Existing issue** (`{action: bind, issue, note}`): it MUST exist.
- **Long lists**: the sequencer MUST read a source's list in the background, a page at a time (100 issues), showing each page as it comes and the progress (`read`, and `total` when the tool gives it: GitLab `X-Total`, Jira Server `total`, Redmine `total_count`, GitHub an estimate from its last page), at most 10,000 issues a source. Once read, a list older than 5 minutes MUST be refreshed with only the issues updated since its last reading (2 minutes earlier, any state: those closed or out of the filter leave it; backends whose `search` takes `since`), and read whole again every 6 hours or when asked (`refresh`); the list read so far stays shown meanwhile. Lists are kept on the host (as the requirements, §5.5) and shown at once after a restart, then refreshed. A backend reports its pages with `paged(issues, read, total, exact)`.
- Issue states are read from the tools at most every 5 minutes (a cache; `refresh` reads them again) and kept in the bindings as last seen; the campaign view, its report and the run reports use those, with no call.
- **Requirements of an issue**: *derived*: those the links of its bound target verify (a check's links, a step's check links, every link of the test definition for the verdict); *explicit*: a label matching `requirements.labels`, the id of a known requirement as a whole word in its title or text (`text`), a Jira issue link to it (`jira_links`). The Editor's Requirements view gives, per requirement, the open issues about it and why (`via`).

---

## 12. Interfaces

### 12.1 CLI (`tsq`)

```
tsq submit <zip> [--priority N] [--baseline F] [--wait] [--follow]
tsq submit-campaign <folder> [--priority N] [--wait]
tsq campaigns | campaign <id> [--cancel | --kill]
tsq priority <run> <N>
tsq validate <zip | testdef.yml> [--lab F] [--setup-config C]
tsq run <zip> --local [--follow]
tsq queue | runs [--state S] | show <run> | logs <run> [--step S] [-f]
tsq cancel | kill | repost | rerun <run>
tsq manual <run> [--step S] [--tick N]... [--bypass N REASON]... [--nonconform] [--done [--comment C] | --fail WHY | --redo-from S]
tsq step <run> <step> next | run-again | continue [--reason R]
tsq step-mode <run> off | each | breakpoints [--breakpoint S]...
tsq stages | components
tsq hold | release | maintenance [--note T] | resume <stage | component>
tsq selftest [<stage | component>]
tsq files <run | zip> [<path>] [--results] [--get]
tsq review <run> [--accept | --reject <target> --reason R [--ref X] | --comment C [--on <target>] | --revoke N --reason R
               | --sign-off [--comment C] | --reopen --reason R | --reapply] [--by U]
tsq review --campaign <id> [--comment C | --sign-off [--comment C] | --reopen --reason R]
tsq issue <run> [--on <target>] [--link <issue> [--note N] | --create [--source S] [--project P] [--title T] [--label L]... [--yes] [--my-token]] [--refresh]
tsq issue --unbind N --reason R
tsq export <run | --campaign id> [--synthetic] [--with-results] [-o <file | folder>]
tsq view <zip> [--port N] [--no-browser]
tsq lab status | reload [--pull [<rev>]]
tsq config
```

`--wait` exit status: `0` succeeded, `1` failed, `2` error. A unique prefix of a run id identifies it. A review target (§11.1) is `run`, `step:<phase>:<step>` or `check:<step>::[<scenario>/]<check>`; `--by` defaults to `$TSQ_USER`, else `$USER`. `tsq issue --create` shows the issue and asks before creating it (`--yes`: without asking); `--my-token` reads the person's token from `$TSQ_ISSUES_TOKEN`.

### 12.2 REST API

FastAPI, OpenAPI at `/docs`. Core resources:

| Method and path | Purpose |
|---|---|
| `POST /runs` (multipart zip, priority), `GET /runs`, `GET /runs/{id}`, `PATCH /runs/{id}` | submit, list, inspect, change priority |
| `POST /runs/{id}/cancel \| kill \| repost \| retry` | §10, §8.4 |
| `POST /runs/{id}/rerun`, `POST /runs/{id}/manual/{step}` | run a campaign's datapack again (§9.6), answer a manual operation (§6.9) |
| `POST /runs/{id}/control`, `POST /runs/{id}/steps/{step}/{next \| run_again \| continue}` | step-by-step mode and breakpoints, operator actions on a step (§9.7) |
| `POST /campaigns` (multipart manifest, datapacks, priority), `GET /campaigns`, `GET /campaigns/{id}`, `GET /campaigns/{id}/report`, `POST /campaigns/{id}/cancel \| kill` | campaign runs (§9.6) |
| `GET /runs/{id}/pipeline`, `GET /runs/{id}/logs/{step}`, `GET /runs/{id}/files/{path}` | expanded pipeline, logs, files (HTTP range) |
| `GET /stages`, `GET /components`, `POST /{stages\|components}/{id}/{hold\|release\|maintenance\|resume}` | resources |
| `GET\|POST /components/{id}/probe`, `POST /components/{id}/bypass`, `GET\|POST /components/{id}/commands[/{cmd}]` | probe report, bypass, manual commands |
| `POST /selftest`, `GET /lab`, `POST /lab/reload`, `GET /lab/changes`, `GET /lab/view`, `GET\|PUT /lab/layout` | lab |
| `GET\|POST /runs/{id}/review`, `GET\|POST /campaigns/{id}/review`, `GET /runs/{id}/report`, `GET /runs/{id}/exploit/{step}/report` | reviews and reports (§11.1, §11.2); `GET /runs` and `GET /runs/{id}` give the verdict as reviewed beside the raw one |
| `GET /runs/{id}/issues[?refresh=true]`, `POST /runs/{id}/issues/draft`, `POST /runs/{id}/issues` (`{action: create \| bind}`), `GET /runs/{id}/issues/search?q=&source=&state=`, `GET /issues/bindings`, `POST /issues/bindings/{id}/unbind` | issues bound to results (§11.3) |
| `POST /runs/{id}/export`, `POST /campaigns/{id}/export`, `GET /exports/{id}`, `POST /exports/{id}/cancel`, `GET /exports/{id}/download` | exported reports (§11.2) |
| `GET /runs/{id}/requirements[/report]`, `GET /campaigns/{id}/requirements[/report]` | requirements coverage of a run or of a campaign run (§5.5), JSON or one HTML page; `GET /campaigns/{id}/report` includes it |
| `GET /events` | Server-Sent Events: state changes, log lines, `review`, `issue`, `export`, `export_progress` |
| `/edit/...`, `/exploit/...`, `/plot/...` | Editor mode only (§12.4) |

### 12.3 GitLab runner

A gitlab-runner Custom executor (`gitlab/`): `config` declares the builds dir, `prepare` checks `tsqd`, `run` builds the datapack and calls `tsq submit --wait --follow` (trace = run log, job status = verdict), `cleanup` cancels the run if the job was cancelled.

### 12.4 Editor

Enabled by `server.editor`. It edits the files the CLI reads and MUST NOT add semantics the files cannot express.

- **Sessions**: a scratch git clone of one repository on one branch, or in place on the folder (on its checked-out branch; on a detached HEAD, such as a lab on a tag, the push goes to the requested branch, else the upstream default branch (origin/HEAD, else main or master, else the only branch fetched), and the folder stays detached on the pushed commit). Several browsers MAY share a session; each save carries the base version of the file and MUST be refused (409, with the current file) when the file changed.
- **Writes** keep comments, key order and layout (ruamel.yaml); a new string of several lines is written as a `|` block when that keeps it exactly.
- **Push**: commit in the user's name, optional tag, push with credentials supplied in the request; credentials MUST NOT be stored or logged, and tsqd MUST NOT push with its own credentials (agent, credential helper, `insteadOf` rewrites). A rejected push applies nothing.
- **Lab changes** apply only on reload (§4.9).
- **Generate** runs the `datapack` tool with optional keys set by hand and dataset versions set by hand (§5.3).
- **Campaigns**: the Datapacks page lists the campaign files of a repository; for each, its entries, the datasets they import against the versions it pins (one not pinned is a problem that blocks generation), and its routing tags checked against the lab. Its keys: one row per key its entries ask (`user_keys`, shared by name, with the choices they have in common; none in common is a problem) or the campaign pins. It generates a campaign, asking the keys not pinned (remembered per campaign in the browser), and submits it (§9.6). The All runs list groups the runs of a campaign run under it, an earlier attempt greyed under its re-run; its page shows the report and runs an ended datapack again.
- **Manual operations**: the step palette and form edit a `manual` step (§6.9). The Run page opens the dialog of the operation the run waits for (its Markdown text and pictures, its checks, Done, Failed and Redo from); All runs and the campaign page flag the runs that expect one.
- **Operator controls** (§9.7): the Run page has a step mode selector (off, after each step, at breakpoints), a banner per paused step (Next, Run again, Continue anyway), the same actions and a breakpoint toggle on the selected step of the running phase, the pass of a step run again, and the list of operator actions; All runs and the campaign page flag paused runs.
- **Requirements**: the Datapacks page has a Requirements subtab: its sources as cards (`source_cards`: `{name, backend, label?, default?}`), and the path tree of the source picked, or of every source (each a top node of the tree and of the requirement paths) when none is picked; a source picked keeps only its requirements and the links to them. The report covers the same (and the node picked: `node`, `children`) and states it (`scope`: sources, node, campaign) (requirements.yml, kind `requirements`), with per node the coverage of its requirements over the latest ended run of each test definition (else the test definition, linked only), optionally restricted to a campaign's test definitions; the requirements of the node (with or without its sub-nodes), each with its text, status and links; and the matrices of §5.5 (`GET /edit/sessions/{id}/requirements[/report]?source=&campaign=[&node=&children=]`; `POST /edit/sessions/{id}/requirements/test` reads one source, as saved or as given (`spec`), whole or up to a stage (`upto`: `step:<N>`, `list`: the first page, `details:<N>`: that details call for the first requirement of the list; `ids`: the steps and the whole list, the id and title of each requirement; `item:<id>`: the steps, the list up to that requirement, then each field evaluated alone, `fields: [{field, from: list | details:<N>, jq, value | error, default?}]`, with `item_vars` and the list item; with `expr` (Debug), any jq expression on what that stage's jq reads, with the variables known there: a step's answer, the list's first page, a details call's answer for the first requirement, a list item or (`on`: `details:<N>`) a details call's answer for it: `expr: {jq, on, values (at most 50) | error, count}`), and returns each call to the server (`calls: [{call, method, url, headers, body, status, reason, seconds, answer, extracted, items, next, error}]`, at most 30, credentials masked as `***`; the cache of steps not used; the test calls for at most 30 s, after which the next call fails with `stopped: the test takes more than 30 s`); `GET /edit/sessions/{id}/requirements/item?source=&id=` reads one requirement in full, as generation does, for a requirement opened in the tab; `GET /edit/sessions/{id}/requirements/search[?path=]` lists every active requirement of every source by reference, for the quick search of the test definition's `reqs`). The synthesis of a test definition (`POST .../synthesis`) includes `requirements`: `{items: [{key, title, path, status, url, version, found, by}], problems, warnings}`, or null when it lists none. `requirements.yml`, and a file a `yaml` source names, have a form (schema `requirements`): classes (a source written once with class variables, each source of it giving only their values and its label; Detach copies the class into the source; a test of a class asks for test values, kept in the browser; the test API takes `class`, `values` and `classes`, the classes as being edited), sources (backend among those registered, with the `description`, `options_help` and `credentials_help` the backend declares; credentials only as `env:<VARIABLE>` or `file:<path>`), default source, node labels and requirements; their problems are those of reading the sources (a credential not set, or its file not readable, on the host: a warning). The test definition's problems include its unknown or inactive requirements and those of its checks (errors), and pinned versions that changed (warnings) (§5.5).
- **Reviews** (§11.1): the Run page shows the verdict as reviewed (the raw one beside it), Accept, Reject and Comment on each check, step and the verdict (who, why, comment, reference; who is remembered in the browser), and a Review tab: the journal, Revoke, the reviews of the earlier attempt to reapply, Sign off and Reopen. All runs and the campaign page show the verdicts as reviewed and the review state; the campaign page has its review (comments, sign-off). **Exports** (§11.2): an Export button on the Run and campaign pages (synthetic or complete, results zips or not) with a progress bar, Cancel and the download; a topbar badge lists the exports running.
- **Issues** (§11.3): the Datapacks page has an Issues subtab: its sources as cards (`GET /edit/sessions/{id}/issues`: `{file, sources: [{name, backend, project, label, url, default, group, capabilities, class, vars, filter}], default, routes, requirements, backends}`) with Test connection (`POST .../issues/test {source, data?}`, `data` the file as being edited: count, first issues, projects of a group, `types: {list, origin, failure, improvement, seen} | {error, seen}` (the types a new issue can have, where they come from, those proposed for a failure and for a green run, and how many of the issues read have each type), each call with credentials masked, or the error), and their issues (`GET .../issues/list?source=&state=&q=&refresh=&find=&only=reqs|bound&offset=&limit=`: a page, `limit` at most 500, of the lists as read so far (§11.3 Long lists), newest first; `q` is searched by the tools, `find` words of their key, title, labels or assignees; `{issues, total, offset, limit, listed, with_requirements, bound, errors, loading: {<source>: {read, total, exact, mode: first | since | whole}}, read_at}`: a page answers at once with what is read so far, the UI polls while `loading`), each with the requirements it names and the results it is bound to (an issue has `type`, `type_color`, `type_icon` when its tool gives them: a GitLab `type::` label and its colour, else its `issue_type`; a GitHub issue type and its colour; a Jira issue type and its icon; a Redmine tracker. ◉ is drawn in the colour of its type, the tool's, else red for a bug-like type, blue for an improvement-like one, purple for a task, else green when open and grey when closed, faded when closed; the UI shows the Jira icon instead when it loads); without an `issues.yml`, it offers to create one. `GET .../requirements` adds `issues: {<requirement key>: [{issue, via}]}` (open issues; derived and explicit) and `issues_errors`; the Requirements view shows ◉ per requirement, a filter on them, and the issues of a requirement opened. The Run page has ◉ Issue… on each failure (check, step, verdict) and on the verdict of a succeeded run (an improvement: title `[tsq] <testdef>: ` to complete): the draft to edit and create, or an existing issue to search and bind; the known issues of a failure beside it, with Unbind; an Issues tab. The campaign page and report list the known issues of each run.
- **Criteria**: the datapack overview lists the criteria the exploit steps name (§6.8), per step with their data, and the scenarios of the test definition no step uses. The criteria editor marks each scenario with the steps that check it.

---

## 13. Extension points

Plugins register through `testsuit.plugins` (entry points or `TESTSUIT_PLUGINS`) or `tsq.yml` `plugins`. `GET /plugins` (tsqd and `mxp webui`, their Configuration tab) lists each one found: `name`, `source`, `target`, `package`, `version`, `status` (`loaded` | `failed` | `not loaded`), `error`, and `includes`: what it registered while loading, by kind (testsuit's own built-ins excluded).

| Registration | Contract | Built in |
|---|---|---|
| `register_transport(name, cls)` | `run(cmd, env, timeout, detach)`, `put`, `get`, `signal`, `is_alive` | `ssh` (AsyncSSH), `local` |
| `register_role(name, def)`, `register_component(name, def)` | roles and classes in Python | `component`, `linux_system` |
| `register_state_probe(name, fn)` | `status.probe` kinds | `run`, `http` |
| `register_step_type(name, cls)` | step actions | `run`, `python`, `http`, `exploit`, `push`, `pull`, `delay`, `wait_for` |
| `register_ready_check(name, fn)` | `wait_for` kinds | `state`, `tcp`, `http`, `file`, `log`, `cmd` |
| `register_generator(name, fn)` | `method: generate` | none |
| `register_issue_backend(name, factory)` (`testsuit.issues`) | issue sources (§11.3): `factory(options, credentials, base_dir)` → `search(text, state, labels, limit[, since])` (each page reported with `self.paged(issues, read, total, exact)`), `get(ids)`, with capabilities `create`/`comment`/`remote_link`/`group`: `create(title, body, labels, project[, issue_type])` and `issue_types(project)` (`[{name, label?, default?, color?, icon?}]`), `types_origin(types)` (where they come from, in words), `comment(id, body)`, `remote_link(id, url, title)`, `projects()`; `id_of_url(url)`, `with_token(token)`; optional `description`, `options_help`, `credentials_help`, `trace` | `gitlab`, `github`, `jira`, `redmine` |
| `register_req_backend(name, factory)` | requirements sources (§5.5): `factory(options, credentials, base_dir)` → `tree()`, `list(path, children)`, `get(ids)`, optional `brief(ids)` (without the fields that cost more calls, with the version), `revision()`, `create`/`update` with capability `write`; capabilities `children_query`, `write`, `versions`; optional `description`, `options_help`, `credentials_help` (Editor form), `trace` (set to a list by the Editor's test: the calls it makes, described) | `yaml`, `reqif`, `rest` |

---

## 14. Security considerations

- The kernel has no authentication or authorisation. Deployments with the Editor enabled SHOULD sit behind an authenticating reverse proxy.
- SSH uses key-based authentication only. Host keys are checked against `tsq_ssh_host_key` when given, else `known_hosts`; a key is never trusted on first use.
- Secrets in templates come from inventory variables; secret-looking HTTP headers are masked in logs and UI.
- Issues: credentials only by reference; a person's own token for creating an issue is used for that call only, never stored nor logged; tokens are masked in errors and traces. An issue's text is shown, editable, before it is sent, since logs can hold secrets.
- The Editor never stores git credentials, refuses URLs embedding credentials, and pushes only in the user's name.
- Python steps on the sequencer host run as separate processes and cannot block or crash `tsqd`.
- `tsq release` on a lock held by a run is audited.

---

## 15. Known deviations

Where the implementation differs from the full design in [R §18]:

- `collect` does not yet resume interrupted transfers, compute checksums during transfer, or honour `max_size`.
- The `http` transport is not implemented (HTTP commands and probes are).
- `systemd-run` is not used; process control is `setsid` + pid files.
- Generator scripts receive every resolved key, so their key reads are not recorded in `datapack.yml`.
