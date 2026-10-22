# sequencer: architecture overview

The sequencer runs real tests on a lab made of components (computers, switches, clocks...), grouped in **stages**. It takes a **datapack**, the zip built by the `datapack` tool, and queues it by priority. When the stages and components the datapack needs are free, it locks them and runs the datapack's **pipeline**:
1. deploy the component inputs;
2. start the components, in parallel or waiting for each other;
3. feed the system;
4. stop the components and collect their outputs;
5. analyse the results.

Several datapacks run at the same time when they use different stages.

One sequencer manages one lab: the whole set of components and the stages they belong to, however complex. Another lab, for example on another site, gets its own sequencer instance.

Status: architecture proposal, nothing is implemented yet. The tools that were considered, and why the design reuses their ideas and libraries rather than one of them as a whole, are in [benchmark.md](benchmark.md).

A sample configuration, a toy train on a one-stage lab with a simulated environment, is in [examples/toytrain](../../../examples/toytrain/README.md).

Contents:
1. [Functional requirements](#1-functional-requirements)
2. [Vocabulary](#2-vocabulary)
3. [Principles](#3-principles)
4. [Overview](#4-overview)
5. [Datapack generation refinements](#5-datapack-generation-refinements)
6. [Lab inventory: stages and components](#6-lab-inventory-stages-and-components)
7. [Component types and state monitoring](#7-component-types-and-state-monitoring)
8. [Datapack side: resources needed and pipeline](#8-datapack-side-resources-needed-and-pipeline)
9. [Queue, scheduling and locking](#9-queue-scheduling-and-locking)
10. [Running a datapack](#10-running-a-datapack)
11. [Abort and kill](#11-abort-and-kill)
12. [Extension points](#12-extension-points)
13. [Interfaces: CLI, REST API, UI, GitLab runner](#13-interfaces-cli-rest-api-ui-gitlab-runner)
14. [Building blocks and dependencies](#14-building-blocks-and-dependencies)
15. [Package layout](#15-package-layout)
16. [Implementation plan](#16-implementation-plan)
17. [Open points](#17-open-points)

## 1. Functional requirements

What the product shall do, gathered from the specifications discussed so far. The last column points to the section of this document that answers each requirement.

### 1.1 Datapack generation

The sequencer reuses the testsuit `datapack` tool (YAML dictionaries, Jinja2 templates, self-sufficient BagIt zip), with these refinements:

| Id | Requirement | Section |
|---|---|---|
| DG-1 | The definition of each component (its input files) comes from the YAML setup files the datapack tool already reads. These files can be extended to describe the test run (commands, pipeline, stages needed) and still be usable for a plain datapack. | 5, 8 |
| DG-2 | Generating a component's inputs can involve processing specific to that component, beyond rendering templates and copying files. | 5.1 |
| DG-3 | A component can need other components' inputs to generate its own. | 5.2 |
| DG-4 | A datapack is self-sufficient: it can be run from its zip contents only (inputs, resolved keys, test definition with its pipeline). | 10 |
| DG-5 | A datapack carries a **baseline**: where each of its contents comes from (git path, commit and tag of the original inputs, the processing tools and their versions, and, for results, the sequencer and the lab), so that the identity of the produced data can be controlled and checked against an expected baseline. A non-conform datapack is accepted but flagged as such, up to its results. | 5.3, 10 |

### 1.2 Queue and resources

| Id | Requirement | Section |
|---|---|---|
| SQ-1 | Accept and queue a datapack containing everything about the test to execute: component inputs and the commands pipeline. | 10 |
| SQ-2 | Each datapack in the queue has a priority. Higher priorities are activated first, FIFO within a priority, and the priority of a queued datapack can be changed. | 9 |
| SQ-3 | When the resources a datapack needs are available, dequeue it and activate it on them. | 9 |
| SQ-4 | Several datapacks can run at the same time on different stages, for example two datapacks on two stages of the same type. | 9 |
| SQ-5 | The lab is made of components. A component belongs to one or more stages, or is ad hoc (outside any stage). A component in several stages is shared by them; one in a single stage is specific to it. An ad hoc component is for example a server running the exploitation tools, shared by all datapacks when it is not the sequencer host itself. | 6 |
| SQ-6 | The definition of a stage includes the list of its components and possible overrides of their commands, for example a specific configuration deployment. | 6, 7.3 |
| SQ-7 | When a datapack is activated, the resources it actually needs are reserved (locked). The granularity is the stage for components that belong to a stage, and the component itself for ad hoc components. | 9 |
| SQ-8 | A single datapack can lock several stages and ad hoc components. | 8.1, 9 |
| SQ-9 | Components can be of any type (computer, switch, clock...): mainly Linux based, no Windows. | 7, 12 |
| SQ-10 | The state of each component is monitored (for example idle, configured, running, broken), and broken components are not used. | 7.4 |
| SQ-23 | A stage or a component can be declared temporarily disabled, for example for maintenance: no new datapack is activated on it until it is enabled again. | 7.4, 9, 13 |
| SQ-11 | Visibility of the queued datapacks (with their priority and what they wait for), and of the state of stages and components. | 9, 13 |

### 1.3 Pipeline execution

| Id | Requirement | Section |
|---|---|---|
| SQ-12 | Run a commands pipeline that deploys configuration data, runs startup commands, feeds the system and retrieves the results into the outputs of the datapack. | 8.3, 10 |
| SQ-13 | Commands can run in parallel or wait for each other (rendez-vous points), depending on their configuration. They can be grouped in named phases to make the pipeline clearer. | 8.3 |
| SQ-14 | Readiness of a component is either checked objectively (port, log, file, command) or approximated with a plain delay. | 8.3 |
| SQ-15 | Default commands are given by a base component type, for example "Linux system": scp to a standard target folder, with SSH keys configured so that no password is asked. A new component type can extend another one and override some of its commands, and each component instance is assigned a type. | 7 |
| SQ-16 | The sequencer itself is a component like the others. The exploit phase (exploit_runner/mexploit analysis of the retrieved data) is a phase of the pipeline like any other, with the outputs of earlier phases becoming its inputs. | 2, 8.3 |
| SQ-17 | A test run can be forced to stop, killing the remote processes (`kill -9` or similar), while the cleanup and result retrieval still run. | 11 |
| SQ-24 | Each run ends with a verdict: `succeeded`; `failed` when a command did not give its expected result, typically an unexpected return status; or `error` when a command crashed or could not complete (signal, timeout, unreachable component). | 8.3, 10 |

### 1.4 Interfaces and integration

| Id | Requirement | Section |
|---|---|---|
| SQ-18 | The product works from the command line, without any UI. | 13 |
| SQ-19 | An optional UI can be added on top of the kernel later, to post new datapacks and to navigate and control the queue and the pipelines. | 13 |
| SQ-20 | The product can act as a GitLab runner, for a future CI integration. | 13 |
| SQ-21 | Simple and modular, built on well-known open-source languages and libraries. | 3, 14 |
| SQ-22 | A single-team lab is enough for a first version: no access control in the kernel. | 13 |

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **lab** | everything one sequencer manages: all the components and the stages they belong to. One sequencer instance per lab. |
| **component** | one managed element: a computer, a switch, a clock... Mainly Linux, never Windows. It has a host, a **type**, a transport (how to reach it), commands (how to deploy, start, stop, collect, kill...), and a monitored **state** (idle, configured, running, broken...). |
| **component type** | the commands and state monitoring of a kind of component. Types form a hierarchy: `linux_system` is the built-in base; a new type `extends` one and overrides some of its commands. |
| **stage** | a named set of components; the unit of reservation for the components it holds. A component can be **specific** to one stage or **shared** by several. |
| **ad hoc component** | a component that belongs to no stage, reserved on its own (for example a server running exploitation tools). |
| **lab inventory** | the file describing the stages and components of the lab, owned by the lab operators. |
| **datapack** | the self-sufficient zip built by `datapack`: component inputs, every resolved key, and the test definition and setup (pipeline included). It says *what* to run; the lab inventory says *where*. |
| **logical stage / component** | what a datapack needs ("a toy-train stage with its pilot, telemetry and acquisition units", "the simulated propulsion"); bound to a concrete stage or ad hoc component at activation. |
| **run** | one activation of a datapack. |
| **phase / step** | the pipeline of a run: named phases (`test_run`, `exploit`...) made of steps (commands), run in parallel or waiting for each other (`needs`). |
| **sequencer component** | the sequencer host itself: an ad hoc component like the others (`transport: local`), never locked. |

## 3. Principles

1. **Kernel first, headless.** Everything is available from a CLI and a REST API; a UI is only another API client.
2. **One YAML family, one template language.** The datapack test definition and setup (already on YAML and Jinja2) gain optional sections; a datapack without them is still a plain datapack. The lab inventory uses the standard Ansible YAML inventory format.
3. **Declarative pipeline, imperative escape hatches.** Pipelines and component types are YAML. Any command can be overridden by a derived type, per stage, per component, or inline. New transports, step types, state probes and generators come through the existing `testsuit.plugins` entry points.
4. **Safe by construction.** Locks are taken all at once and released by the kernel only. Every phase has a `finally` that always runs. Every remote process is started so that it can be killed with its whole process tree.
5. **Few, boring dependencies**, each replaceable.

## 4. Overview

```
            user / CI job (gitlab-runner) / future UI (posts datapacks too)
                     │ CLI (tsq)              │ REST + SSE
                     ▼                        ▼
   ┌───────────────────────────── sequencer daemon (tsqd) ─────────────────────────────┐
   │                                                                                     │
   │  Intake ──► Queue ──────► Scheduler ─────────► Run engine ──► Executors ──► Transports│
   │  (zip,      (priority,    (bind logical       (pipeline DAG,  (run, push,  (ssh,     │
   │  verify,    FIFO, DB)     stages and ad hoc   phases, steps,  pull, wait,  local,    │
   │  validate)                components, lock)   finally, kill)  delay, ...)  plugins)  │
   │                              ▲                       │                               │
   │       Lab inventory ─────────┤                  Event bus ──► State store (SQLite)   │
   │       (stages, components,   │                  (states, logs)  + run folders on disk│
   │       types)                 │                                                       │
   │                         State monitor (polls each component's status command)        │
   └─────────────────────────────────────────────────────────────────────────────────────┘
          │ ssh / scp / systemd-run / kill / status ...               │ local
          ▼                                                           ▼
   stage and ad hoc components (computers, switches, clocks)  sequencer component
```

One process (`tsqd`) runs an asyncio loop. There is no broker and no worker fleet: steps and state probes are mostly remote calls, so the work is I/O bound and one loop drives dozens of components. A dev mode (`tsq run --local`) runs one datapack in the foreground without a daemon.

## 5. Datapack generation refinements

The `datapack` tool (`testsuit.datatools.datapack`, YAML dictionaries + Jinja2 templates + BagIt zip) stays the way to prepare a datapack. Two refinements cover the generation needs.

### 5.1 Component-specific processing to generate inputs

Today an input is `method: template` (Jinja2) or `method: copy`, optionally followed by `postprocess` processors. A third method, **`generate`**, runs a registered **generator** that produces the target from the source and the keys. Examples: compile the pilot's speed profile into the table its microcontroller reads, compute a calibration table, build a network configuration from a topology file.

```yaml
component_types:
  pilot_app:
    configurations:
      standard:
        inputs:
          - source: templates/profile.yml
            target: profile.bin
            method: generate
            generator: toytrain-profile-compiler   # registered by a plugin
            options: {step_mps: "{{ train.pilot_profile.step_mps }}"}
```

A generator is a Python callable registered like processors (`register_generator(name, fn)`), called as `fn(source, target, keys, options, component)`. When the name is not registered, it is run as a script relative to the setup folder, with the source, the target and a JSON file of the resolved keys. The keys a generator reads are recorded in `datapack.yml` like those of templates.

### 5.2 Components needing other components' inputs

A component can declare `needs: [other_component, ...]`. Components are then generated in dependency order (topological sort, cycles reported), and a dependent component sees the inputs already generated:
- `components.<id>.input_dir`: path of the other component's generated inputs, usable in templates and generators;
- `read_input('<id>/<file>')`, a Jinja2 function returning the text of a generated file (`| from_yaml` / `| from_json` filters to read structured data);
- generators receive the same paths.

```yaml
configurations:
  sim_bench:
    components:
      pilot:       {type: pilot_app, configuration: standard}
      acquisition: {type: acquisition_app, configuration: standard}
      telemetry:
        type: telemetry_app
        configuration: standard
        needs: [pilot, acquisition]   # records what they publish, read from their configurations
```

Both refinements keep the datapack self-sufficient: what a component needed from another one is already in the zip.

### 5.3 Baseline

Each datapack carries a `baseline.yml`, the synthesis of where its contents come from. Since it is covered by the BagIt tag manifest, it cannot be changed without `datapack --verify` noticing.

The datapack tool already records part of it in `datapack.yml` (git commit, tag and cleanliness of the test definition and setup folders; source and version of each dataset). The baseline completes it and puts it in one place:

```yaml
# baseline.yml
datapack: {name: toytrain_max_speed_toytrain_lab_setup_20261022T1012, format: 2, conform: true}
sources:                          # every original input, by git origin
  - {role: testdef, path: datapack/testdef/max_speed/testdef.yml, repo: git@forge:toytrain/toytrain-tests.git, commit: 3f2a9c1, tag: v1.4.0, clean: true}
  - {role: setup, path: datapack/setup/toytrain_setup.yml, repo: git@forge:toytrain/toytrain-tests.git, commit: 3f2a9c1, tag: v1.4.0, clean: true}
  - {role: dictionary, path: datapack/setup/globals.yml, repo: git@forge:toytrain/toytrain-tests.git, commit: 3f2a9c1, tag: v1.4.0, clean: true}
  - {role: dataset, id: toytrain_sw, importer: toytrain-release, source: releases/toytrain-sw, version: v0.3.0}
tools:                            # what processed them
  - {name: testsuit, version: 3.2.0}
  - {name: toytrain-testenv, kind: plugin, version: 0.1.0}        # generators, processors, importers
  - {name: jinja2, version: 3.1.6}
files:                            # each generated file and what produced it
  data/pilot/input/pilot.yml: {from: [datapack/setup/templates/pilot.yml], method: template, sha256: 9e1f...}
  data/pilot/input/profile.bin: {from: [datapack/setup/templates/profile.yml], method: generate, generator: toytrain-profile-compiler, sha256: 44ab...}
```

When the sequencer produces the results zip (section 10), it extends the baseline with a `run` part:
- the sequencer version and its plugins;
- the git commit and tag of the lab inventory and of the component type library;
- the concrete stages and components the run was bound to;
- the software version of each component, when its type defines a `version` command (for example `toytrain-install --version` on CALC-Pilot, the firmware id of the propulsion controller), read before the first phase.

**Conformity.** A baseline is **conform** when every source is a clean, tagged git state and every dataset version was confirmed. This is what `--nocheck` relaxes today, and a `--nocheck` datapack is marked `conform: false`. An **expected baseline** is a YAML file with the same structure, holding only what must match: tags, versions, tool versions, component versions, possibly with patterns (`tag: "v1.4.*"`). It is checked:
- by `datapack --check-baseline expected.yml pack.zip`, and when building with `datapack ... --baseline expected.yml`;
- by the sequencer at submission (`tsq submit --baseline expected.yml`, or `pipeline.baseline` in the test definition);
- before the first phase for the `run` part (component versions).

A non-conform datapack is **accepted but flagged**, never rejected. This covers both kinds: a datapack that is not conform in itself (`conform: false`), and one that does not match its expected baseline. The run proceeds normally, and its verdict is unchanged, but:
- the run is flagged `non-conform` in the queue, in `tsq runs` and `tsq show`, in the REST API and the UI, and in the GitLab job trace;
- the differences are listed, one line per mismatch, kept in the run record and written to the results baseline (`run.conform: false` with the mismatches);
- the results zip is named with a `_NONCONFORM` suffix, like the `_DRAFT` suffix the datapack tool already gives to `--nocheck` datapacks.

So the identity of produced data can always be checked afterwards, and non-conform results can't be mistaken for baseline results.

## 6. Lab inventory: stages and components

The lab inventory follows the [Ansible YAML inventory](https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html) format. It is a well-known standard, and the same file can be used with `ansible` for ad hoc operations, but Ansible is not a dependency: the sequencer reads it with its own loader. The format maps naturally onto the lab:
- a **group** per stage, its hosts being the stage's components;
- a host listed in **several stage groups** is a shared component;
- a host in **no group** (directly under `all`) is an **ad hoc component**;
- **group variables** are stage-level settings and command overrides.

Sequencer settings use the `tsq_` prefix, the convention for tool-specific inventory variables.

The examples of this document use the toy train of [examples/toytrain](../../../examples/toytrain/README.md), whose lab has one stage. Here it has a second, identical one, to show concurrent runs and a shared component:

```yaml
# lab/toytrain_lab.yml (Ansible YAML inventory format)
all:
  vars:
    ansible_user: tsq                              # standard names, reused by the sequencer
    ansible_ssh_private_key_file: ~/.ssh/id_toytrain_lab   # key-based auth only, no password prompt
    tsq_type: linux_system                         # default component type
    tsq_remote_root: /opt/testrun                  # default target: <root>/<component>/{input,output}
    tsq_types: [types/]                            # component type library, relative to this file (section 7.2)

  hosts:                                           # ad hoc components: in no stage
    lab_srv:        {ansible_host: localhost, tsq_transport: local, tsq_role: sequencer, tsq_lock: none}
    sim_propulsion: {ansible_host: localhost, tsq_transport: local, tsq_type: toytrain_sim, tsq_role: propulsion_sim}
    sim_piezo:      {ansible_host: localhost, tsq_transport: local, tsq_type: toytrain_sim, tsq_role: piezo_sim}

  children:
    train_1:                                       # a stage
      vars:
        tsq_stage_type: toytrain                   # what datapacks ask for
      hosts:
        calc_pilot_1: {ansible_host: 10.42.1.10, tsq_type: arduino_unoq, tsq_role: pilot}
        calc_tm_1:    {ansible_host: 10.42.1.11, tsq_type: rpi5, tsq_role: telemetry}
        au_1:         {ansible_host: 10.42.1.12, tsq_type: rpi5, tsq_role: acquisition}
        switch_1:     {ansible_host: 10.42.1.2, tsq_type: auvidea_m82, tsq_role: switch}
        lab_switch:   {ansible_host: 10.42.0.2, tsq_type: auvidea_m82, tsq_role: lab_network, tsq_lock: shared}
    train_2:                                       # a second stage of the same type
      vars:
        tsq_stage_type: toytrain
        tsq_commands:                              # stage override: this train's pilot has no flashing helper
          install: {run: "arduino-cli upload --input-dir {{ component.input_dir }}/app"}
      hosts:
        calc_pilot_2: {ansible_host: 10.42.2.10, tsq_type: arduino_unoq, tsq_role: pilot}
        calc_tm_2:    {ansible_host: 10.42.2.11, tsq_type: rpi5, tsq_role: telemetry}
        au_2:         {ansible_host: 10.42.2.12, tsq_type: rpi5, tsq_role: acquisition}
        switch_2:     {ansible_host: 10.42.2.2, tsq_type: auvidea_m82, tsq_role: switch}
        lab_switch: {}                             # shared with train_1: joins both trains to lab_srv
```

- A stage is defined by its components (the hosts of its group) and its overrides (`tsq_commands` and the other variables of its group).
- Variables follow Ansible precedence: host over stage group over `all`.
- `tsq_type` assigns a component type to each component (section 7).
- `tsq_lock` is the lock mode of a component:
  - `exclusive` (default): one datapack at a time;
  - `shared`: several datapacks at once, for passive components such as a network switch nobody configures. In the example, `train_1` and `train_2` can run two datapacks at the same time although they share `lab_switch`;
  - `none`: never locked, for the sequencer component.
- Components are taken out of scheduling by state, not by editing the file: a `broken` state (section 7.4), or `tsq maintenance <component>`.

## 7. Component types and state monitoring

A component type gives a kind of component its commands and the way to monitor its state. Types form a hierarchy: a type `extends` another one and overrides only what differs.

### 7.1 Built-in base types

| Type | Commands | State |
|---|---|---|
| `component` | none: the abstract root, for components that are not Linux systems (a switch or a clock driven through their own API) | `unknown` unless a `status` probe is defined |
| `linux_system` (extends `component`) | `deploy`: push `data/<component>/input/` to `<remote_root>/<component>/input/` (scp/sftp over SSH, key-based); `collect`: pull `<remote_root>/<component>/output/` back to `data/<component>/output/`; `kill`: kill the process trees started by the run (section 11); `start`, `stop`: none (component-specific) | `offline` when SSH fails, `running` while a unit started by the sequencer is active, `idle` otherwise |

### 7.2 Defining types

Types are YAML, in the lab's **type library** (the `tsq_types` folders of the inventory). Plugins can also register them in Python (`register_component_type`).

```yaml
# lab/types/toytrain.yml
component_types:
  rpi5:
    extends: linux_system
    commands:
      start:   {run: "sudo systemctl start toytrain-{{ component.role }}", timeout: 30s}
      stop:    {run: "sudo systemctl stop toytrain-{{ component.role }}", timeout: 30s}
      ready:   {wait_for: {log: {file: "{{ component.output_dir }}/app.log", regex: "READY"}, timeout: 30s}}
      version: {run: "toytrain-{{ component.role }} --version"}
    status:
      probe: {run: "systemctl is-active toytrain-{{ component.role }}"}
      states:                              # first match wins, on the probe output
        running: {regex: "^active"}
        broken:  {regex: "^failed"}
        idle:    {regex: "^inactive"}

  arduino_unoq:
    extends: linux_system                  # Debian on the MPU, plus a microcontroller to flash
    commands:
      install: {run: "sudo /usr/local/bin/toytrain-install --input {{ component.input_dir }}", timeout: 2m}
      start:   {run: "sudo systemctl start toytrain-{{ component.role }}"}
      stop:    {run: "sudo systemctl stop toytrain-{{ component.role }}"}

  auvidea_m82:
    extends: component                     # an unmanaged switch: only monitored
    transport: local                       # probed from the sequencer host
    status:
      probe: {run: "ping -c 1 -W 1 {{ component.host }}"}
      states: {running: {exit: 0}, offline: {exit: nonzero}}

  propulsion_controller:                   # the real one, on the real train
    extends: component                     # not a Linux system: its own protocol
    transport: toytrain-udp                # plugin (section 12)
    commands:
      stop:  {run: "stop"}
      kill:  {run: "stop"}
    status:
      probe: {run: "speed"}
      states: {running: {regex: "^[1-9]"}, idle: {regex: "^0"}}
```

### 7.3 Where commands come from

A datapack component type (setup file, section 8.2) can extend a lab type to add test-specific commands. The command used for a component is resolved in this order, last wins:
1. the chain of its lab type, from the base type down (`component` → `linux_system` → `arduino_unoq`);
2. the datapack component type (setup file `component_types.<type>.commands`);
3. the datapack component instance (setup configuration);
4. the stage `tsq_commands` (lab inventory);
5. the host `tsq_commands` (lab inventory);
6. an inline `run:` in a pipeline step.

The lab has the last word over the datapack on purpose: a stage with a special way of deploying overrides the generic command for every datapack.

### 7.4 State monitoring

Each component has a monitored **state**:

| State | Meaning |
|---|---|
| `offline` | unreachable |
| `idle` | reachable, nothing running |
| `configured` | inputs deployed, not started |
| `running` | started |
| `broken` | reachable but faulty |
| `unknown` | no probe can tell |

- The **state monitor** runs each component's `status` probe periodically: every `status.interval` (default 30 s), more often for the components of active runs. Results go to the event bus and the state store.
- The sequencer also knows the **expected** state: `configured` after `deploy`, `running` after `start`, `idle` after `stop`. When the probed state differs from the expected one (expected `running`, probed `idle` or `broken`), an event is raised. The step fails or only warns, as set by `on_state_mismatch: fail | warn` (default `warn`).
- A state can be waited for in a pipeline: `wait_for: {state: running}`.
- A `broken` or `offline` component is not used for new runs. A stage is unavailable while one of its components is. A component that recovers becomes available again, unless an operator put it in `maintenance`.

This is distinct from the **lock state** (free or held by a run), shown next to it.

## 8. Datapack side: resources needed and pipeline

### 8.1 Resources a datapack needs

The setup configuration (datapack side) names the **logical stages** it needs, by stage type, and puts each logical component in one of them with a **role**. A logical component with no stage asks for an **ad hoc component** by role. At activation:
- each logical stage is bound to a free concrete stage of that type;
- each logical component in a stage is bound to the host with the same role in that stage;
- each ad hoc logical component is bound to a free ad hoc component with that role.

```yaml
# setup file (datapack)
configurations:
  sim_bench:
    stages:
      train: {type: toytrain}            # bound to train_1 or train_2, whichever is free
    components:
      pilot:       {type: pilot_app, configuration: standard, stage: train, role: pilot}
      telemetry:   {type: telemetry_app, configuration: standard, stage: train, role: telemetry}
      acquisition: {type: acquisition_app, configuration: standard, stage: train, role: acquisition}
      propulsion:  {type: propulsion_sim, configuration: standard, role: propulsion_sim}   # ad hoc
      piezo:       {type: piezo_sim, configuration: standard, role: piezo_sim}             # ad hoc
      analysis:    {type: exploit, configuration: standard, role: sequencer}               # ad hoc: lab_srv
```

A datapack can therefore lock several stages and ad hoc components. Here the analysis runs on the sequencer host (`role: sequencer`); a separate exploitation server would be an ad hoc component with its own role.

### 8.2 Datapack component types

Datapack component types (already in the setup file) declare the inputs. They can also `extends` a lab type, which makes binding check that the concrete host's type is that type or derives from it, and add or override commands for this test:

```yaml
component_types:
  pilot_app:
    extends: arduino_unoq               # lab type: binds only to an Arduino UNO Q
    configurations:
      standard:
        inputs:
          - {source: "{{ toytrain_sw.path }}/pilot/", target: app/, method: copy}
          - {source: templates/pilot.yml, target: pilot.yml}
  propulsion_sim:
    extends: toytrain_sim
    configurations: {standard: {inputs: [{source: templates/propulsion_sim.yml, target: propulsion_sim.yml}]}}
    commands:
      start: {run: "toytrain-sim propulsion --config {{ component.input_dir }}/propulsion_sim.yml", detach: true}
  exploit:
    extends: linux_system
    configurations:
      standard:
        inputs:
          - {source: templates/exploit.yml, target: exploit.yml}
          - {source: "{{ testdef.path }}/criteria/", target: scenario/toytrain/, method: copy}
    commands:
      start: {run: "exploit_runner {{ component.input_dir }}/exploit.yml", timeout: 15m}
```

Commands are kept as written in the datapack and rendered by the sequencer at run time.

### 8.3 Pipeline (test definition)

The pipeline lives in the test definition (already kept as is in the datapack). Its syntax is close to GitLab CI so that it reads familiar. Every phase follows the same rules, the exploit phase included: it lists the logical stages and components it needs, can take earlier phases' outputs as inputs, and runs steps.

```yaml
pipeline:
  timeout: 15m
  phases:
    - id: test_run
      stages: [train]                      # logical stage locked for this phase
      components: [propulsion, piezo]      # ad hoc logical components locked for this phase
      steps:
        - {id: deploy, for_each: components, do: deploy}                    # fan-out, in parallel
        - {id: flash_pilot, component: pilot, do: install, needs: [deploy]}
        - {id: start_sims, for_each: [propulsion, piezo], do: start, needs: [deploy]}
        - {id: sims_ready, for_each: [propulsion, piezo], do: ready, needs: [start_sims]}
        - {id: start_tm, component: telemetry, do: start, needs: [deploy]}
        - {id: tm_ready, component: telemetry, do: ready, needs: [start_tm]}
        - {id: start_au, component: acquisition, do: start, needs: [deploy, sims_ready]}
        - {id: au_ready, component: acquisition, do: ready, needs: [start_au]}
        - id: start_pilot                                                   # rendez-vous
          component: pilot
          do: start
          needs: [flash_pilot, sims_ready, tm_ready, au_ready]
        - {id: pilot_running, wait_for: {state: running, component: pilot, timeout: 20s}, needs: [start_pilot]}
        - {id: run, delay: "{{ scenario.duration_s }}s", needs: [pilot_running]}   # no objective end
        - {id: stop_pilot, component: pilot, do: stop, needs: [run]}
        - {id: stop_all, for_each: [acquisition, telemetry, propulsion, piezo], do: stop, needs: [stop_pilot]}
      finally:                                                              # always runs, even on abort
        - {id: kill_leftovers, for_each: components, do: kill, if: "not succeeded('stop_all')"}
        - {id: collect, for_each: components, do: collect}

    - id: exploit
      needs: [test_run]
      components: [analysis]               # an ad hoc component (the sequencer host)
      inputs:                              # outputs of earlier phases become inputs
        - {from: "test_run:telemetry/output/telemetry.csv", to: "analysis/input/data/"}
        - {from: "test_run:propulsion/output/propulsion.propj", to: "analysis/input/data/"}
      steps:
        - {id: deploy, component: analysis, do: deploy}
        - {id: analyse, component: analysis, do: start, needs: [deploy]}   # exit 0: criteria ok, else failed
      finally:
        - {id: collect, component: analysis, do: collect}
```

Phase fields: `id`, `needs`, `stages` (logical stages), `components` (ad hoc logical components), `inputs`, `steps`, `finally`, `timeout`.

Step fields:
- target: `component`, or `for_each` (a list of components, `components` for all the components of the phase, or a role);
- action, one of: `do` (a named command), `run` (inline shell), `push` / `pull`, `delay`, `wait_for`, or a plugin step type;
- options: `needs`, `if`, `expect`, `timeout`, `retries`, `detach`, `continue_on_error`, `env`.

`expect` gives the expected result of a step: `exit` (exit codes, default `[0]`), and optionally `stdout` or `stderr` (regex). A step that ran but did not give the expected result is `failed`. A step that could not run or did not finish normally is `error`: a crash (killed by a signal), a timeout, a transport failure, an unreachable or broken component, or an internal sequencer error.

`wait_for` covers ready checks done from outside: `state` (section 7.4), `tcp`, `http` (status or regex), `file`, `log` (regex), `cmd` (exit 0), each with `timeout` and `interval`.

`inputs` copies files inside the run folder, from `<phase>:<component or *>/output/...` to `<component>/input/...`, before the phase starts. Nothing is copied from host to host.

Validation (Pydantic) rejects cycles, unknown ids and steps on components outside the phase, at submission time (`tsq validate` before that).

Templates in commands are rendered with Jinja2, the same environment as the datapack tool, in two layers:
- the datapack's frozen keys from `context.yml`;
- run-time keys: `run.{id, dir, priority}`, `phase.id`, `component.{id, type, host, stage, role, state, input_dir, ...}`, `components.<id>.*`, `stages.<id>.*`, `remote_root`.

## 9. Queue, scheduling and locking

**Priority.** Each run has a priority, an integer from 0 to 100 (default 50), given at submission (`tsq submit --priority 80`) and changeable while queued (`tsq priority <run> 90`). The queue is ordered by priority, then by submission time. Optional aging (`priority_aging: +1 per 30 min`, off by default) keeps low-priority runs from waiting forever.

**Scheduling.** Whenever resources are released, a run is queued, or a component recovers, the scheduler walks the queue in order. For each run it tries to bind the logical stages and ad hoc components of its first phase to free concrete ones (section 8.1). A resource is free when it is not `broken`, `offline` or in maintenance, and each of its components is free for its lock mode:
- an `exclusive` component is free when no other run holds it;
- a `shared` component is free unless another run holds it exclusively.

A lower-priority run may start while a higher-priority one waits, but only on resources the waiting run cannot use (backfilling), so priority is respected without leaving the lab idle. Among several free candidates, the stage used last by the same test definition is preferred, then the least used one.

**Concurrency.** Runs that bind to different stages and ad hoc components run at the same time. In the example inventory, a toy-train datapack runs on `train_1` and holds the two simulations of `lab_srv`. A second one can only bind `train_2` once simulations are free too: with a second pair (two more ad hoc components with the same roles), both run together, sharing `lab_switch`, while a third one waits for a stage.

**Locking.**
- The granularity is the **stage** for components that belong to a stage, and the **component** for ad hoc components.
- Everything a run needs for a phase is locked in **one database transaction**, all or nothing, so there are no partial holds and no deadlocks.
- A component shared by two stages of the same run is locked once. A component shared with a stage held by another run makes this run wait, unless the component is `shared`.
- Locks are recorded per run, stage and ad hoc component, with who holds them and since when.

**Per phase.** At the end of a phase, the resources the next phase doesn't use are released, and those it does use stay held. Optionally, a run can pre-reserve the resources of all its phases at activation (`reserve: all`), so it isn't stuck between phases.

**Visibility.**
- `tsq queue` shows each waiting run with its priority and what it waits for (no free `toytrain` stage: `train_1` held by run A, `train_2` broken).
- `tsq stages` and `tsq components` show stages and components with their monitored state, lock state and holder.

## 10. Running a datapack

1. **Submit**: `tsq submit pack.zip [--priority N]`, or a datapack posted through the REST API (UI, CI).
   - Intake verifies the BagIt checksums (`datapack --verify`) and unzips into `runs/<run_id>/`.
   - It reads the pipeline, commands, resources needed and `context.yml` from the zip only.
   - It validates them against the lab inventory: stage types, roles, component types, commands.
   - The run state is `queued`.
2. **Activate**: binding and locking (section 9), then a fresh state probe of every locked component. The run state goes `waiting_resources → reserved → running`.
3. **Phases**:
   - Before a phase, the kernel copies its `inputs` and binds and locks its resources if they aren't held yet.
   - The engine builds the step DAG and starts every step whose `needs` are met, as asyncio tasks, one command at a time per component unless `detach`.
   - Each step's output goes to `runs/<id>/logs/<phase>/<step>[<component>].log` and to the event bus.
4. **Finally**: the `finally` steps of each phase always run, with their own timeout. The phase's resources are released afterwards, unless the next phase uses them.
5. **Verdict**, one of three, computed from the steps (those with `continue_on_error` excepted):
   - `succeeded`: every step gave its expected result;
   - `failed`: at least one step ran but did not give its expected result, typically an unexpected exit status, and no step is in `error`;
   - `error`: at least one step crashed or could not complete (section 8.3), or the run was cancelled or killed. The test outcome is then unknown, not negative.

   The status of each phase and step is kept.
6. **Results**: the run folder is zipped as `<datapack>_results.zip`, a valid datapack whose manifest now covers the outputs too, and whose baseline gets the `run` part (section 5.3). The run record stays in the database as history.

State machines:
- run: `queued → waiting_resources → reserved → running → cleaning → done`, or `cancelled` while still queued; a done run carries its verdict, and an aborted run (`tsq cancel`, `tsq kill`) gets the verdict `error`;
- step: `pending → ready → running → succeeded | failed | error | skipped`, with the reason of an `error` kept (crash, timeout, killed, transport).

## 11. Abort and kill

- **`tsq cancel <run>`**: a queued run leaves the queue. On a running one, pending steps are cancelled and running steps get their `stop` command; after a grace period, the kill below follows.
- **`tsq kill <run>`**: no grace. The `kill` command of every running step, then the `finally` steps (at least `collect`, to keep the logs), then the locks are released.
- **Remote processes** on `linux_system` components start as transient systemd units:
  - `systemd-run --user --unit=tsq-<run>-<step>-<component> --collect ...`;
  - `systemctl --user kill --signal=KILL tsq-<run>-*` kills whole process trees, even when the ssh session is gone;
  - fallback without systemd: `setsid` + pid file under `<remote_root>/.tsq/<run>/` and `kill -9 -<pgid>`.
- **Other component types** override `kill`: stop the propulsion controller through its own protocol, `virsh destroy` a VM, reload a switch configuration.
- **Daemon restart**: runs found `running` are marked `error`, then their `kill` and `finally` steps run, then their locks are released. `tsq release <stage | component>` is the audited manual escape hatch.

## 12. Extension points

Through `testsuit.plugins` (entry points or `TESTSUIT_PLUGINS`), next to the existing registrations:

| Registration | Purpose | Built in |
|---|---|---|
| `register_transport(name, cls)` | how to talk to a component: `run(cmd, env, timeout, detach)`, `put`, `get`, `signal`, `is_alive` | `ssh` (AsyncSSH), `local`, `http` |
| `register_component_type(name, definition)` | component types defined in Python rather than in the YAML type library | `component`, `linux_system` |
| `register_state_probe(name, fn)` | new kinds of `status` probes | `run`, `http` |
| `register_step_type(name, cls)` | new step kinds | `run`, `push`, `pull`, `delay`, `wait_for` |
| `register_ready_check(name, fn)` | new `wait_for` kinds | `state`, `tcp`, `http`, `file`, `log`, `cmd` |
| `register_generator(name, fn)` | datapack `method: generate` (section 5.1) | none |

Components of any type are handled this way. A managed switch is driven through an SSH, REST or SNMP transport and its own type, the propulsion controller through its UDP protocol, a board through a `labgrid` transport, and a component managed by playbooks through an `ansible` step type. A package such as `examples/toytrain` ships, in its `plugin.py`, its file formats, criteria and importers, and would add its transports and component types the same way.

## 13. Interfaces: CLI, REST API, UI, GitLab runner

**CLI** (`tsq`), working against a local or remote daemon:

```
tsq submit <datapack.zip> [--priority N] [--wait] [--follow]
tsq priority <run> <N>                                      # change the priority of a queued run
tsq validate <datapack.zip | testdef.yml> [--lab lab.yml]   # resources, pipeline, commands, fully expanded
tsq run <datapack.zip> --local                              # no daemon: dev mode, foreground
tsq queue                                                   # waiting runs, priorities, what they wait for
tsq runs [--state running] | tsq show <run> | tsq logs <run> [--step S] [-f]
tsq cancel <run> | tsq kill <run>
tsq stages | tsq components                                 # monitored state, lock state, holder
tsq hold | release | maintenance | resume <stage | component>
```

`--wait` returns the run verdict as exit code (0 succeeded, 1 failed, 2 error), and `--follow` streams the logs: what scripts and CI need.

**REST API** (FastAPI, OpenAPI generated):
- `POST /runs` (multipart zip, priority), `GET /runs`, `GET /runs/{id}`, `PATCH /runs/{id}` (priority), `POST /runs/{id}/cancel|kill`, `GET /runs/{id}/logs/{step}`;
- `GET /stages`, `GET /components`, `POST /stages/{id}|/components/{id}/hold|release|maintenance|resume`;
- `GET /events` (Server-Sent Events: run and component state changes, log lines).

**UI (optional, later).** Any web front end on the REST API. It can post a new datapack (upload, priority) and show:
- the queue, with priorities that can be changed;
- the stages and components, with their monitored and lock states;
- each run, with its step DAG, live logs and cancel/kill buttons.

The kernel never depends on it.

**GitLab runner (future).** gitlab-runner's **Custom executor** turns the sequencer into a GitLab runner without implementing the runner protocol:
- `config` declares the builds dir;
- `prepare` checks that `tsqd` is reachable;
- `run` executes the job script, which builds the datapack (`datapack testdef.yml`) and calls `tsq submit --wait --follow`, so the run log becomes the job trace and the verdict becomes the job status;
- `cleanup` cancels the run if the job was cancelled.

Results zips become job artifacts. Runner tags can name stage types, and CI variables can set the priority.

**Auth.** None in the kernel for the first version (single-team lab, behind the lab network or a reverse proxy). OIDC can come later.

## 14. Building blocks and dependencies

| Layer | Choice | Why | Alternative |
|---|---|---|---|
| Language | Python ≥ 3.10 | same as testsuit; reuse of datapack, exploit_runner, plugins | - |
| Schemas | Pydantic v2 | validated YAML, JSON Schema for editors and the UI | jsonschema |
| Templates | Jinja2 3.1 (`testsuit.datatools.datapack.templating`) | one syntax for datapack, dictionaries, types and pipeline | - |
| Lab inventory | Ansible YAML inventory format, own loader | standard topology format, Ansible optional | own schema |
| Concurrency | asyncio | DAG execution, state probes, timeouts, cancellation | anyio |
| SSH, SCP, SFTP | AsyncSSH | async, pure Python, signals, keepalive, `~/.ssh` keys and agent | Fabric / Paramiko |
| Remote process control | `systemd-run --user`, `setsid` fallback | kill whole trees, standard Linux | tmux |
| State store | SQLite via SQLAlchemy 2 | queue, locks, states, history; zero administration; Postgres later if needed | JSON files |
| API | FastAPI + uvicorn, SSE | REST + OpenAPI for UI and CI | Flask |
| CLI | argparse (like the other testsuit tools), rich for tables and live views | consistent with existing CLIs | Typer |
| CI integration | gitlab-runner Custom executor | official runner, no protocol to implement | - |
| Optional backends | ansible-runner, labgrid | as plugins only | - |

New dependencies go to an optional extra, `testsuit[sequencer]` (`asyncssh`, `sqlalchemy`, `fastapi`, `uvicorn`, `rich`), so datapack and exploit users are not affected. Jinja2 and Pydantic are already dependencies.

## 15. Package layout

```
src/testsuit/sequencer/
  README.md, benchmark.md
  model/          # pydantic: lab inventory, component types, resources needed, pipeline, run state
  inventory.py    # Ansible-format loader, variable precedence, stages, ad hoc components, roles
  types.py        # component type library, 'extends' chains, command resolution
  monitor.py      # state probes, expected vs probed state, availability
  intake.py       # verify zip, unzip, load embedded definition and context
  store.py        # SQLAlchemy tables: runs, stages, components, locks, states, steps, events
  scheduler.py    # priority queue, binding, backfilling, atomic locking, release
  engine.py       # phases, step DAG, timeouts, cancel and kill, finally
  steps/          # run, push/pull, delay, wait_for
  transports/     # local, ssh (AsyncSSH), http, remote process control
  api.py          # FastAPI app
  daemon.py       # tsqd entry point
  gitlab/         # Custom executor scripts (config, prepare, run, cleanup)
src/testsuit/cli/tsq.py
src/testsuit/datatools/datapack/   # + generators (5.1) and component needs (5.2)
```

Tests:
- engine, scheduler and monitor against a fake transport (pure asyncio, no network);
- the ssh transport against a local `sshd` container in CI;
- an end-to-end example in `examples/toytrain` with its stage and its simulations as ad hoc components on `localhost`.

## 16. Implementation plan

1. **Datapack refinements**: `method: generate` with registered generators, component `needs`, and the baseline with its check against an expected baseline. Small, independent, testable on their own.
2. **Models and validation**:
   - Pydantic models for the lab inventory, component types, resources needed and pipeline;
   - `tsq validate` printing the binding candidates and every command fully expanded (the main debugging tool for YAML authors).
3. **Local runner**: `tsq run --local` with the engine, the `linux_system` type and the `local` and `ssh` transports, one run at a time, file locks per component. Already usable on one stage.
4. **Daemon**: SQLite store, priority queue, binding, backfilling, atomic locking, REST API, CLI as a client.
5. **State monitoring**: status probes, expected vs probed states, `wait_for: state`, availability.
6. **Robust abort**: systemd-run/setsid process control, kill, restart recovery.
7. **Results**: phase `inputs`, results zip, exploit component type for exploit_runner.
8. **Plugins**: transports, component types, state probes, step types, ready checks; ansible and labgrid adapters if needed.
9. **GitLab Custom executor** scripts.
10. **UI** (separate, later).

## 17. Open points

Defaults are proposed for each; none blocks the start.

1. **Shared lock mode.** Default: `exclusive`; `shared` only for components declared passive (clock, monitoring). Should a switch be `shared` (traffic of several stages) or `exclusive` (configuration pushed per run)? Proposed: `exclusive` when a pipeline deploys its configuration, `shared` otherwise.
2. **Preemption.** Default: none, a running datapack is never interrupted by a higher-priority one; priority only orders the queue.
3. **Reservation of later phases.** Default: resources are locked phase by phase; `reserve: all` holds all of them from activation.
4. **Recovery of broken components.** Default: a component becomes available again as soon as its probe reports a healthy state; `tsq maintenance` keeps it out until `tsq resume`.
