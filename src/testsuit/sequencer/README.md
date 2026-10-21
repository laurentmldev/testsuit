# sequencer: architecture overview

The sequencer runs real tests on test means made of several components (computers, switches, clocks...). It takes a **datapack**, the zip built by the `datapack` tool, queues it until the **stages** it needs are free on one of the **test means**, locks them, then runs the datapack's **pipeline**:
1. deploy the component inputs;
2. start the components, in parallel or waiting for each other;
3. feed the system;
4. stop the components and collect their outputs;
5. analyse the results (exploit phase).

Status: architecture proposal, nothing is implemented yet. The tools that were considered, and why the design reuses their ideas and libraries rather than one of them as a whole, are in [benchmark.md](benchmark.md).

Contents:
1. [Functional requirements](#1-functional-requirements)
2. [Vocabulary](#2-vocabulary)
3. [Principles](#3-principles)
4. [Overview](#4-overview)
5. [Datapack generation refinements](#5-datapack-generation-refinements)
6. [Lab inventory: test means, stages, components](#6-lab-inventory-test-means-stages-components)
7. [Datapack side: stages needed, commands, pipeline](#7-datapack-side-stages-needed-commands-pipeline)
8. [Scheduling and locking](#8-scheduling-and-locking)
9. [Running a datapack](#9-running-a-datapack)
10. [Abort and kill](#10-abort-and-kill)
11. [Extension points](#11-extension-points)
12. [Interfaces: CLI, REST API, UI, GitLab runner](#12-interfaces-cli-rest-api-ui-gitlab-runner)
13. [Building blocks and dependencies](#13-building-blocks-and-dependencies)
14. [Package layout](#14-package-layout)
15. [Implementation plan](#15-implementation-plan)
16. [Open points](#16-open-points)

## 1. Functional requirements

What the product shall do, gathered from the specifications discussed so far. The last column points to the section of this document that answers each requirement.

### 1.1 Datapack generation

The sequencer reuses the testsuit `datapack` tool (YAML dictionaries, Jinja2 templates, self-sufficient BagIt zip), with these refinements:

| Id | Requirement | Section |
|---|---|---|
| DG-1 | The definition of each component (its input files) comes from the YAML setup files the datapack tool already reads; they can be enlarged to describe the test run (commands, pipeline, stages) and still be usable for a plain datapack. | 5, 7 |
| DG-2 | Generating a component's inputs can involve processing specific to that component, beyond rendering templates and copying files. | 5.1 |
| DG-3 | A component can need other components' inputs to generate its own. | 5.2 |
| DG-4 | A datapack is self-sufficient: it can be run from its zip contents only (inputs, resolved keys, test definition with its pipeline). | 9 |

### 1.2 Queue and resources

| Id | Requirement | Section |
|---|---|---|
| SQ-1 | Accept and queue a datapack containing everything about the test to execute: component inputs and the commands pipeline. | 9 |
| SQ-2 | When the resources a datapack needs are available, dequeue it and activate it on the chosen resources. | 8 |
| SQ-3 | Manage several instances of test means, and activate datapacks on the available ones. | 6, 8 |
| SQ-4 | A test mean is made of stages; a stage is made of components. Some components are shared by several stages, others are specific to one stage. | 6 |
| SQ-5 | The definition of a stage includes the list of its components and possible overrides of the commands, for example a specific configuration deployment. | 6, 7.2 |
| SQ-6 | When a datapack is activated, the components it actually needs are reserved (locked); the granularity of reservation is the stage. | 8 |
| SQ-7 | A single datapack can lock several stages. | 7.1, 8 |
| SQ-8 | Components can be of any type (computer, switch, clock...): mainly Linux based, no Windows. | 11 |
| SQ-9 | Visibility of the queued datapacks (and what they wait for) and of the state of test means, stages and components. | 8, 12 |

### 1.3 Pipeline execution

| Id | Requirement | Section |
|---|---|---|
| SQ-10 | Run a commands pipeline that deploys configuration data, runs startup commands, feeds the system and retrieves the results into the outputs of the datapack. | 7.3, 9 |
| SQ-11 | Commands can run in parallel or wait for each other (rendez-vous points), depending on their configuration; they can be grouped in named phases to make the pipeline clearer. | 7.3 |
| SQ-12 | Readiness of a component is either checked objectively (port, log, file, command) or approximated with a plain delay. | 7.3 |
| SQ-13 | Default commands exist (for example scp to a standard target folder, with SSH keys configured so that no password is asked); they can be overridden for specific components (non standard push/pull or run commands). | 7.2 |
| SQ-14 | The sequencer itself is a component like the others; the exploit phase (exploit_runner/mexploit analysis of the retrieved data) is a phase of the pipeline on its own component, the outputs of the test run becoming its inputs. | 2, 7.3 |
| SQ-15 | A test run can be forced to stop, killing the remote processes (`kill -9` or similar), while the cleanup and result retrieval still run. | 10 |

### 1.4 Interfaces and integration

| Id | Requirement | Section |
|---|---|---|
| SQ-16 | The product works from the command line, without any UI. | 12 |
| SQ-17 | An optional UI to navigate and control pipelines can be added on top of the kernel later. | 12 |
| SQ-18 | The product can act as a GitLab runner, for a future CI integration. | 12 |
| SQ-19 | Simple and modular, built on well-known open-source languages and libraries. | 3, 13 |
| SQ-20 | A single-team lab is enough for a first version: no access control in the kernel. | 12 |

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **component** | one managed element: a computer, a switch, a clock... Mainly Linux, never Windows. It has a host, a transport (how to reach it) and commands (how to deploy, start, stop, collect...). |
| **stage** | a named set of components of a test mean; the unit of reservation. A component can be **specific** to one stage or **shared** by several. |
| **test mean** | one physical (or virtual) bench: a set of stages. The lab can hold several test means, possibly identical. |
| **lab inventory** | the file describing test means, stages and components (owned by the lab operators). |
| **datapack** | the self-sufficient zip built by `datapack`: component inputs, every resolved key, test definition and setup (pipeline included). It says *what* to run; the lab inventory says *where*. |
| **logical stage / component** | what a datapack needs ("a HIL stage with a controller and a feeder"); bound to a concrete stage and concrete components at activation. |
| **run** | one activation of a datapack on a test mean. |
| **phase / step** | the pipeline of a run: named phases (`test_run`, `exploit`) made of steps (commands), run in parallel or waiting for each other (`needs`). |
| **sequencer component** | the sequencer host itself, a component like the others (`transport: local`), for steps that run there (exploit analysis, archiving). |

## 3. Principles

1. **Kernel first, headless.** Everything is available from a CLI and a REST API; a UI is only another API client.
2. **One YAML family, one template language.** The datapack test definition and setup (already on YAML + Jinja2) gain optional sections; a datapack without them is still a plain datapack. The lab inventory uses the standard Ansible YAML inventory format.
3. **Declarative pipeline, imperative escape hatches.** Pipelines are YAML. Any command can be overridden per component type, per stage, per component, or inline. New transports, step types and generators come through the existing `testsuit.plugins` entry points.
4. **Safe by construction.** Locks are taken all at once and released by the kernel only. Every phase has a `finally` that always runs. Every remote process is started so that it can be killed with its whole process tree.
5. **Few, boring dependencies**, each replaceable.

## 4. Overview

```
              user / CI job (gitlab-runner) / future UI
                     │ CLI (tsq)              │ REST + SSE
                     ▼                        ▼
   ┌───────────────────────────── sequencer daemon (tsqd) ─────────────────────────────┐
   │                                                                                     │
   │  Intake ──► Queue ──► Scheduler ──────────► Run engine ──► Executors ──► Transports │
   │  (zip,      (DB)      (bind logical stages  (pipeline DAG,  (run, push,   (ssh,     │
   │  verify,              to a test mean,       phases, steps,  pull, wait,   local,    │
   │  validate)            lock stages)          finally, kill)  delay, ...)   plugins)  │
   │                          ▲                        │                                  │
   │                    Lab inventory             Event bus ──► State store (SQLite)      │
   │                    (test means, stages,      (states, logs)   + run folders on disk  │
   │                    components, health)                                               │
   └─────────────────────────────────────────────────────────────────────────────────────┘
          │ ssh / scp / systemd-run / kill ...                       │ local
          ▼                                                          ▼
   test mean components (computers, switches, clocks)       sequencer component
                                                            (exploit_runner, archive)
```

One process (`tsqd`) runs an asyncio loop. There is no broker and no worker fleet: steps are mostly remote calls, so the work is I/O bound and one loop drives dozens of components. A dev mode (`tsq run --local`) runs one datapack in the foreground without a daemon.

## 5. Datapack generation refinements

The `datapack` tool (`testsuit.datatools.datapack`, YAML dictionaries + Jinja2 templates + BagIt zip) stays the way to prepare a datapack. Two refinements cover the generation needs.

### 5.1 Component-specific processing to generate inputs

Today an input is `method: template` (Jinja2) or `method: copy`, optionally followed by `postprocess` processors. A third method, **`generate`**, runs a registered **generator** that produces the target from the source and the keys. Examples: compile a scenario to a binary format, compute a calibration table, build a network configuration from a topology file.

```yaml
component_types:
  data_feeder:
    configurations:
      standard:
        inputs:
          - source: scenarios/nominal.yml
            target: scenario.bin
            method: generate
            generator: acme-scenario-compiler      # registered by a plugin
            options: {rate_hz: "{{ feeder.rate_hz }}"}
```

A generator is a Python callable registered like processors (`register_generator(name, fn)`), called as `fn(source, target, keys, options, component)`. When the name is not registered, it is run as a script relative to the setup folder, with the source, the target and a JSON file of the resolved keys. The keys a generator reads are recorded in `datapack.yml` like those of templates.

### 5.2 Components needing other components' inputs

A component can declare `needs: [other_component, ...]`. Components are then generated in dependency order (topological sort, cycles reported), and a dependent component sees the inputs already generated:
- `components.<id>.input_dir`: path of the other component's generated inputs, usable in templates and generators;
- `read_input('<id>/<file>')`, a Jinja2 function returning the text of a generated file (`| from_yaml` / `| from_json` filters to read structured data);
- generators receive the same paths.

```yaml
configurations:
  nominal:
    components:
      switch_1: {type: network_switch, configuration: standard}
      bench_a:
        type: bench_controller
        configuration: standard
        needs: [switch_1]          # bench_a's network config is built from the switch's port map
```

Both refinements keep the datapack self-sufficient: what a component needed from another one is already in the zip.

## 6. Lab inventory: test means, stages, components

The lab inventory follows the [Ansible YAML inventory](https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html) format. It is a well-known standard, and the same file can be used with `ansible` for ad-hoc operations, but Ansible is not a dependency: the sequencer reads it with its own loader. The format maps naturally onto the topology:
- a **group** per test mean, whose child groups are its stages;
- a host listed in **several stage groups** is a shared component;
- **group variables** are stage-level settings and command overrides.

Sequencer settings use the `tsq_` prefix, the convention for tool-specific inventory variables.

```yaml
# lab/acme_lab.yml (Ansible YAML inventory format)
all:
  vars:
    ansible_user: testops                          # standard names, reused by the sequencer
    ansible_ssh_private_key_file: ~/.ssh/id_acme   # key-based auth only, no password prompt
    tsq_transport: ssh
    tsq_remote_root: /opt/testrun                  # default target: <root>/<component>/{input,output}
  children:

    bench1:                                        # a test mean
      vars: {tsq_test_mean: true}
      children:
        bench1_hil:                                # a stage of bench1
          vars:
            tsq_stage_type: hil                    # what datapacks ask for
            tsq_commands:                          # stage override: deploy differently on this stage
              deploy: {run: "/opt/bench1/bin/push_config {{ component.input_dir }}"}
          hosts:
            b1_ctrl:  {ansible_host: 10.1.0.11, tsq_role: controller}
            b1_plc:   {ansible_host: 10.1.0.31, tsq_role: plc, tsq_transport: acme-ftp}
            b1_clock: {ansible_host: 10.1.0.2,  tsq_role: clock, tsq_lock: shared}
        bench1_feed:
          vars: {tsq_stage_type: feeder}
          hosts:
            b1_feeder: {ansible_host: 10.1.0.21, tsq_role: feeder}
            b1_clock: {}                           # shared with bench1_hil
            b1_switch: {ansible_host: 10.1.0.1, tsq_role: switch, tsq_transport: switch-rest}

    bench2:                                        # a second test mean, same structure as bench1
      vars: {tsq_test_mean: true}
      children:
        bench2_hil:
          vars: {tsq_stage_type: hil}
          hosts:
            b2_ctrl: {ansible_host: 10.2.0.11, tsq_role: controller}
            # ...

    sequencer:                                     # the sequencer host itself: a stage always available
      vars: {tsq_stage_type: sequencer}
      hosts:
        tsq_local: {ansible_host: localhost, tsq_transport: local, tsq_role: sequencer, tsq_lock: none}
```

- A stage is defined by its components (the hosts of its group) and its overrides (`tsq_commands` and other variables of its group).
- Variables follow Ansible precedence: host over stage group over test mean group over `all`.
- `tsq_lock` is the lock mode of a component:
  - `exclusive` (default): one datapack at a time;
  - `shared`: several datapacks at once, for passive references such as a clock;
  - `none`: never locked, for the sequencer component.
- Components are kept out of scheduling by state, not by editing the file: `tsq maintenance <component>`, or a failed health check.

## 7. Datapack side: stages needed, commands, pipeline

### 7.1 Stages a datapack needs

The setup configuration (datapack side) names the **logical stages** it needs and puts each logical component in one of them with a **role**. At activation, each logical stage is bound to a concrete stage of the requested type on one test mean, and each logical component to the host with the same role in that stage.

```yaml
# setup file (datapack)
configurations:
  nominal:
    stages:
      hil:  {type: hil}                  # bound to bench1_hil or bench2_hil...
      feed: {type: feeder}               # ...and the feeder stage of the same test mean
    components:
      bench_a:  {type: bench_controller, configuration: standard, stage: hil, role: controller}
      plc_1:    {type: legacy_plc, configuration: standard, stage: hil, role: plc}
      feeder:   {type: data_feeder, configuration: standard, stage: feed, role: feeder}
      exploit:  {type: exploit, configuration: standard, stage: sequencer}   # the sequencer component
```

One datapack can therefore lock several stages. All of them are taken on the same test mean by default; `same_test_mean: false` allows spreading them over several test means.

### 7.2 Default commands per component type

Component types (already in the datapack setup) declare default `commands`, kept as written in the datapack and rendered by the sequencer at run time:

```yaml
component_types:
  bench_controller:
    configurations: {standard: {inputs: [...]}}
    commands:
      deploy:  {push: {from: input/, to: "{{ remote_root }}/{{ component.id }}/input/"}}
      start:   {run: "{{ remote_root }}/bin/bench_ctl start --config input/bench_config.yml", detach: true}
      ready:   {wait_for: {log: "{{ remote_root }}/{{ component.id }}/bench.log", regex: READY, timeout: 60s}}
      stop:    {run: "{{ remote_root }}/bin/bench_ctl stop", timeout: 30s}
      kill:    {signal: KILL}
      collect: {pull: {from: "{{ remote_root }}/{{ component.id }}/output/", to: output/}}
      health:  {run: "test -x {{ remote_root }}/bin/bench_ctl"}
  exploit:
    configurations: {standard: {inputs: [{source: exploit/exploit.yml, target: exploit.yml}]}}
    commands:
      start: {run: "exploit_runner input/exploit.yml", timeout: 1h}
```

Built-in defaults when nothing is declared:
- `deploy` pushes `data/<component>/input/` to `<remote_root>/<component>/input/` (scp/sftp);
- `collect` pulls `<remote_root>/<component>/output/` back to `data/<component>/output/`;
- `kill` kills the process trees the run started.

`start`, `stop` and `ready` have no default.

Override order, last wins:
1. built-in default;
2. transport or type plugin;
3. component type `commands` (setup);
4. component instance `commands` (setup configuration);
5. test mean, then stage `tsq_commands` (lab inventory);
6. host `tsq_commands` (lab inventory);
7. inline `run:` in a pipeline step.

The lab has the last word over the datapack on purpose: a stage with a special way of deploying overrides the generic command for every datapack.

### 7.3 Pipeline (test definition)

The pipeline lives in the test definition (already kept as is in the datapack). Its syntax is close to GitLab CI so that it reads familiar.

```yaml
pipeline:
  timeout: 2h
  phases:
    - id: test_run
      stages: [hil, feed]                  # logical stages locked for this phase
      steps:
        - {id: deploy, for_each: components, do: deploy}            # fan-out, in parallel
        - {id: start_plc, component: plc_1, do: start, needs: [deploy]}
        - {id: start_bench, component: bench_a, do: start, needs: [deploy]}
        - {id: bench_ready, component: bench_a, do: ready, needs: [start_bench]}
        - {id: plc_settle, delay: 20s, needs: [start_plc]}          # no objective ready state
        - id: feed                                                  # rendez-vous of both branches
          component: feeder
          run: "feeder --scenario input/scenario.bin --target {{ components.bench_a.host }}"
          needs: [bench_ready, plc_settle]
          timeout: 45m
        - {id: stop_all, for_each: [bench_a, plc_1], do: stop, needs: [feed]}
      finally:                                                      # always runs, even on abort
        - {id: kill_leftovers, for_each: components, do: kill, if: "not succeeded('stop_all')"}
        - {id: collect, for_each: components, do: collect}

    - id: exploit
      needs: [test_run]
      stages: []                           # only the sequencer component: nothing to lock
      inputs:                              # outputs of the previous phase become inputs
        - {from: "test_run:*/output/", to: "exploit/input/data/"}
      steps:
        - {id: analyse, component: exploit, do: start}
```

Step fields:
- target: `component`, or `for_each` (a list of components, `components` for all the components of the phase's stages, or a role);
- action, one of: `do` (a named command), `run` (inline shell), `push` / `pull`, `delay`, `wait_for`, or a plugin step type;
- options: `needs`, `if`, `timeout`, `retries`, `detach`, `continue_on_error`, `env`.

`wait_for` covers ready checks done from outside: `tcp`, `http` (status or regex), `file`, `log` (regex), `cmd` (exit 0), each with `timeout` and `interval`.

Validation (Pydantic) rejects cycles, unknown ids and steps on components outside the phase's stages, at submission time (`tsq validate` before that).

Templates in commands are rendered with Jinja2, the same environment as the datapack tool, in two layers:
- the datapack's frozen keys from `context.yml`;
- run-time keys: `run.{id, dir}`, `phase.id`, `component.{id, type, host, stage, role, input_dir, ...}`, `components.<id>.*`, `stage.<id>.*`, `test_mean.id`, `remote_root`.

## 8. Scheduling and locking

**Queue.** Submitted runs are ordered by priority, then FIFO, with aging so that a run needing many stages is not starved by small ones.

**Binding.** For the first phase of a queued run, the scheduler looks for a test mean on which every logical stage of the run can be bound to a free concrete stage of the right type. A stage is free when each of its components is free for its lock mode:
- an `exclusive` component is free when no other run holds it;
- a `shared` component is free unless another run holds it exclusively.

Candidates are tried in a stable order, preferring the test mean used last by the same test definition (warm caches), then the least used one.

**Locking.** All the components of all the bound stages are locked in **one database transaction** (all or nothing), so there are no partial holds and no deadlocks:
- a component shared by two stages of the same run is locked once;
- a component shared with a stage held by another run makes this run wait, unless the component is `shared`.

Locks are recorded per run and per stage, with who holds them and since when.

**Per phase.** A phase lists the logical stages it needs. Stages the next phase doesn't use are released at the end of a phase; stages it does use stay held. Optionally, a run can pre-reserve its later phases' stages at activation (`reserve: all`), so it isn't stuck between phases.

**Health.** Before a run is activated, the `health` command of each locked component runs. A failing component goes into `maintenance` with the reason, and the run goes back to the queue (or fails, configurable).

**Visibility.**
- `tsq queue` shows each waiting run and what it waits for (stage type X busy on all test means, held by runs A and B).
- `tsq means` shows test means, stages and components, with their states and holders.

## 9. Running a datapack

1. **Submit**: `tsq submit pack.zip [--priority N] [--test-mean bench1]`.
   - Intake verifies the BagIt checksums (`datapack --verify`) and unzips into `runs/<run_id>/`.
   - It reads the pipeline, commands, stages and `context.yml` from the zip only.
   - It validates them against the lab inventory: stage types, roles, commands.
   - The run state is `queued`.
2. **Activate**: binding, locking and health checks (section 8). The state goes `waiting_resources → reserved → running`.
3. **Phases**:
   - The engine builds the step DAG and starts every step whose `needs` are met, as asyncio tasks, one command at a time per component unless `detach`.
   - Each step's output goes to `runs/<id>/logs/<phase>/<step>[<component>].log` and to the event bus.
   - Between phases the kernel copies the declared `inputs` inside the run folder; nothing is copied from host to host.
4. **Finally**: the `finally` steps of each phase always run, with their own timeout. The phase's locks are released afterwards.
5. **Verdict**: the result of the last phase, typically the exploit component's step.
6. **Results**: the run folder is zipped as `<datapack>_results.zip`, a valid datapack whose manifest now covers the outputs too. The run record stays in the database as history.

State machines:
- run: `queued → waiting_resources → reserved → running → cleaning → succeeded | failed | aborted | error`;
- step: `pending → ready → running → ok | failed | skipped | timeout | killed`.

## 10. Abort and kill

- **`tsq cancel <run>`**: a queued run leaves the queue. On a running one, pending steps are cancelled and running steps get their `stop` command; after a grace period, the kill below follows.
- **`tsq kill <run>`**: no grace. The `kill` command of every running step, then the `finally` steps (at least `collect`, to keep the logs), then the locks are released.
- **Remote processes** start as transient systemd units:
  - `systemd-run --user --unit=tsq-<run>-<step>-<component> --collect ...`;
  - `systemctl --user kill --signal=KILL tsq-<run>-*` kills whole process trees, even when the ssh session is gone;
  - fallback without systemd: `setsid` + pid file under `<remote_root>/.tsq/<run>/` and `kill -9 -<pgid>`.
- **Special components** override `kill`: reset a PLC through its protocol, `virsh destroy` a VM, reload a switch configuration.
- **Daemon restart**: runs found `running` are marked `error`, then their `kill` and `finally` steps run, then their locks are released. `tsq release <component>` is the audited manual escape hatch.

## 11. Extension points

Through `testsuit.plugins` (entry points or `TESTSUIT_PLUGINS`), next to the existing registrations:

| Registration | Purpose | Built in |
|---|---|---|
| `register_transport(name, cls)` | how to talk to a component: `run(cmd, env, timeout, detach)`, `put`, `get`, `signal`, `is_alive` | `ssh` (AsyncSSH), `local` |
| `register_step_type(name, cls)` | new step kinds | `run`, `push`, `pull`, `delay`, `wait_for` |
| `register_component_type(name, defaults)` | Python-provided default commands for a type | none |
| `register_ready_check(name, fn)` | new `wait_for` kinds | `tcp`, `http`, `file`, `log`, `cmd` |
| `register_generator(name, fn)` | datapack `method: generate` (section 5.1) | none |

This is how components of any type are handled: a switch through a `switch-rest` or SNMP transport, a clock through its HTTP management API, a board through a `labgrid` transport, a component managed by playbooks through an `ansible` step type. A package such as `examples/acme_testbench` ships its file formats, criteria, importers and its bench's transports and commands in the same `plugin.py`.

## 12. Interfaces: CLI, REST API, UI, GitLab runner

**CLI** (`tsq`), working against a local or remote daemon:

```
tsq submit <datapack.zip> [--priority N] [--test-mean M] [--wait] [--follow]
tsq validate <datapack.zip | testdef.yml> [--lab lab.yml]   # pipeline, stages, commands, fully expanded
tsq run <datapack.zip> --local                              # no daemon: dev mode, foreground
tsq queue                                                   # waiting runs and what they wait for
tsq runs [--state running] | tsq show <run> | tsq logs <run> [--step S] [-f]
tsq cancel <run> | tsq kill <run>
tsq means                                                   # test means, stages, components, holders
tsq hold | release | maintenance | resume <component | stage>
```

`--wait` returns the run verdict as exit code (0 success, 1 failed, 2 aborted or error), and `--follow` streams the logs: what scripts and CI need.

**REST API** (FastAPI, OpenAPI generated):
- `POST /runs` (multipart zip), `GET /runs`, `GET /runs/{id}`, `POST /runs/{id}/cancel|kill`, `GET /runs/{id}/logs/{step}`;
- `GET /means`, `GET /components`, `POST /components/{id}/hold|release|maintenance`;
- `GET /events` (Server-Sent Events: state changes, log lines).

**UI (optional, later).** Any web front end on the REST API: queue board, test means board (stages, components, holders), run view with the step DAG and live logs, cancel/kill buttons. The kernel never depends on it.

**GitLab runner (future).** gitlab-runner's **Custom executor** turns the sequencer into a GitLab runner without implementing the runner protocol:
- `config` declares the builds dir;
- `prepare` checks that `tsqd` is reachable;
- `run` executes the job script, which builds the datapack (`datapack testdef.yml`) and calls `tsq submit --wait --follow`, so the run log becomes the job trace and the verdict becomes the job status;
- `cleanup` cancels the run if the job was cancelled.

Results zips become job artifacts. Runner tags can name test means or stage types.

**Auth.** None in the kernel for the first version (single-team lab, behind the lab network or a reverse proxy). OIDC can come later.

## 13. Building blocks and dependencies

| Layer | Choice | Why | Alternative |
|---|---|---|---|
| Language | Python ≥ 3.10 | same as testsuit; reuse of datapack, exploit_runner, plugins | - |
| Schemas | Pydantic v2 | validated YAML, JSON Schema for editors and the UI | jsonschema |
| Templates | Jinja2 3.1 (`testsuit.datatools.datapack.templating`) | one syntax for datapack, dictionaries and pipeline | - |
| Lab inventory | Ansible YAML inventory format, own loader | standard topology format, Ansible optional | own schema |
| Concurrency | asyncio | DAG execution, timeouts, cancellation | anyio |
| SSH, SCP, SFTP | AsyncSSH | async, pure Python, signals, keepalive, `~/.ssh` keys and agent | Fabric / Paramiko |
| Remote process control | `systemd-run --user`, `setsid` fallback | kill whole trees, standard Linux | tmux |
| State store | SQLite via SQLAlchemy 2 | queue, locks, history; zero administration; Postgres later if needed | JSON files |
| API | FastAPI + uvicorn, SSE | REST + OpenAPI for UI and CI | Flask |
| CLI | argparse (like the other testsuit tools), rich for tables and live views | consistent with existing CLIs | Typer |
| CI integration | gitlab-runner Custom executor | official runner, no protocol to implement | - |
| Optional backends | ansible-runner, labgrid | as plugins only | - |

New dependencies go to an optional extra, `testsuit[sequencer]` (`asyncssh`, `sqlalchemy`, `fastapi`, `uvicorn`, `rich`), so datapack and exploit users are not affected. Jinja2 and Pydantic are already dependencies.

## 14. Package layout

```
src/testsuit/sequencer/
  README.md, benchmark.md
  model/          # pydantic: lab inventory, stages, commands, pipeline, run state
  inventory.py    # Ansible-format loader, variable precedence, stage/role resolution
  intake.py       # verify zip, unzip, load embedded definition and context
  store.py        # SQLAlchemy tables: runs, components, stages, locks, steps, events
  scheduler.py    # queue ordering, binding of logical stages, atomic locking, release
  engine.py       # phases, step DAG, timeouts, cancel and kill, finally
  steps/          # run, push/pull, delay, wait_for
  transports/     # local, ssh (AsyncSSH), remote process control
  api.py          # FastAPI app
  daemon.py       # tsqd entry point
  gitlab/         # Custom executor scripts (config, prepare, run, cleanup)
src/testsuit/cli/tsq.py
src/testsuit/datatools/datapack/   # + generators (5.1) and component needs (5.2)
```

Tests:
- engine and scheduler against a fake transport (pure asyncio, no network);
- the ssh transport against a local `sshd` container in CI;
- an end-to-end example in `examples/acme_testbench` with two stages on `localhost`.

## 15. Implementation plan

1. **Datapack refinements**: `method: generate` with registered generators, and component `needs`. Small, independent, testable on their own.
2. **Models and validation**:
   - Pydantic models for the lab inventory, stages and pipeline;
   - `tsq validate` printing the bound stages and every command fully expanded (the main debugging tool for YAML authors).
3. **Local runner**: `tsq run --local` with the engine and the `local` and `ssh` transports, one run at a time, file locks per component. Already usable on one bench.
4. **Daemon**: SQLite store, queue, binding, atomic stage locking, REST API, CLI as a client.
5. **Robust abort**: systemd-run/setsid process control, kill, restart recovery, health checks.
6. **Exploit component** for exploit_runner, phase `inputs`, results zip.
7. **Plugins**: transports, step types, ready checks; ansible and labgrid adapters if needed.
8. **GitLab Custom executor** scripts.
9. **UI** (separate, later).

## 16. Open points

Defaults are proposed for each; none blocks the start.

1. **Stages on several test means.** Default: all the stages of a run on the same test mean; `same_test_mean: false` to allow otherwise.
2. **Shared lock mode.** Default: `exclusive`; `shared` only for components declared passive (clock, monitoring). Should a switch be `shared` (traffic of several stages) or `exclusive` (configuration pushed per run)? Proposed: `exclusive` when the pipeline deploys its configuration, `shared` otherwise.
3. **Reservation of later phases.** Default: stages are locked phase by phase; `reserve: all` to hold all of them from activation.
4. **Where the exploit runs.** Default: the sequencer component (`local`); a dedicated analysis host is just another component with the `exploit` role.
