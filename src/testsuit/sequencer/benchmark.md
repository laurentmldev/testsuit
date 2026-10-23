# Sequencer: benchmark of existing tools

Preliminary study behind the [sequencer architecture](README.md): which well-established open-source tools already cover significant parts of the needs, and why the proposal reuses their designs and libraries rather than one of them as a whole.

Date: 2026-10-21, last checked 2026-10-23 against requirements SQ-1 to SQ-31 (section 6). Nothing here is implemented yet.

## 1. What the needs boil down to

From the sequencer specification, the product combines these capabilities. Each tool below is scored against C1 to C6; C7 to C9 are discussed in section 4.

| # | Capability | Typical owner in industry |
|---|---|---|
| C1 | **Job queue** of datapacks, visible, with priorities and cancel | CI servers, workflow schedulers |
| C2 | **Resource inventory and reservation (locks)**: a datapack waits until all the stages it needs are free, then locks them | lab managers (LAVA, labgrid), Jenkins Lockable Resources |
| C3 | **Pipeline DSL**: phases, commands, parallelism, rendez-vous (`needs`), delays, ready checks, timeouts | CI/CD (GitLab CI, GitHub Actions), workflow engines |
| C4 | **Remote execution**, mostly over SSH on Linux hosts: push/pull files, start/stop processes, commands overridable per component and per stage | Ansible, Fabric, labgrid |
| C5 | **Forced abort**: kill a run, including remote processes (`kill -9`), and always run cleanup | CI (`after_script`, `post { always }`), Ansible `always` |
| C6 | **Headless kernel, optional UI**: CLI and API first | most servers with a REST API |
| C7 | One lab made of **stages** (sets of components) and **ad hoc components**; components may be shared between stages or specific to one; a datapack may lock several stages; several datapacks run at once on different stages | lab managers |
| C8 | **GitLab runner integration** later: a CI job can run a datapack on the lab | GitLab Runner executors |
| C9 | **Heterogeneous components**: computers, switches, clocks; mainly Linux, no Windows | Ansible (network modules), labgrid (drivers) |

Constraints: simple and modular, well-known open-source dependencies, reuse of the testsuit datapack (YAML setup, Jinja2 templates, BagIt zip) and of exploit_runner/mexploit.

## 2. Candidates

### CI/CD servers

**Jenkins** (+ Lockable Resources plugin, Pipeline/Groovy)
- C1 ✓ build queue with "waiting for resource" display. C2 ✓ `lock(resource: 'bench_a')`, labels, quantity; jobs queue until locks are free. C3 ✓ stages, `parallel`, but rendez-vous between parallel branches is awkward (no `needs` DAG; you nest `parallel` blocks). C4 ~ via `sh 'ssh ...'` or agents installed on each component. C5 ✓ abort + `post { always }`, but remote processes started via ssh are not killed unless you code it. C6 ✓ full UI, REST API.
- Why not: heavy JVM server, Groovy DSL instead of YAML, plugin sprawl, and the datapack would have to be translated into a Jenkinsfile. Good model to copy for locks.

**GitLab CI** (`resource_group`, `needs:`, `stages:`)
- C3 ✓✓ the best YAML DSL of the lot: GitLab `stages` ≈ our pipeline phases (not to be confused with our test-mean stages), `needs:` gives a DAG with rendez-vous, `when: always`, `timeout`, `after_script`. C2 ~ `resource_group` serializes jobs on one named resource but cannot lock *a set* of components atomically. C4 ~ gitlab-runner with shell executor on each component, or ssh. C1/C6 ✓ but tied to a git push, not to "post a zip".
- Why not: needs a GitLab instance; pipeline triggered by commits, not by a datapack submission; multi-resource atomic reservation missing. **Its YAML syntax is the one to mimic.**

**GitHub Actions / Gitea Actions / Forgejo**: same DAG model as GitLab (`jobs.<id>.needs`), `concurrency:` groups are a weak lock (one group per job). Self-hosted runners required. Same verdict as GitLab: borrow syntax, do not depend on it.

**Buildbot** (Python, Twisted): Python-native CI, locks (`MasterLock`, `WorkerLock`, counting), workers on each host, UI. Closest CI to our stack, but its config is Python code, Twisted is dated, and it is designed around source changes. Not retained.

**Zuul + Nodepool**: gating CI with real node reservation (Nodepool hands nodes out to jobs, Ansible runs on them). Conceptually very close (resources → lock → run Ansible playbooks), but it is a large OpenStack-born system (ZooKeeper, Gerrit/GitHub integration). Too heavy.

### Workflow orchestrators (data/ML world)

**Apache Airflow**: Python DAGs, scheduler, pools (≈ counting semaphores), SSHOperator/SFTPOperator, UI. Pools are counted slots, not named exclusive components; DAGs are code, designed for scheduled batch; heavyweight (DB + scheduler + webserver + workers). Not retained.

**Prefect 3**: Python flows/tasks, work pools and queues, **global concurrency limits** usable as named locks, cancellation, UI, SQLite by default. Covers C1/C3/C6 well and C2 partially (limits are per tag, atomic multi-lock must be coded). Cancellation kills the *local* worker, not remote ssh processes. Pipelines are Python code, not YAML (YAML only for deployments). Tempting, but it imposes its server, its data model and a fast-moving API; the YAML pipeline would still have to be compiled into Prefect flows.

**Dagster, Luigi, Snakemake, doit**: data-asset or file-target oriented; no notion of exclusive hardware resource. Not retained.

**Temporal / Cadence**: durable workflow engine (Go server, Python SDK), excellent at long-running, resumable, cancellable workflows. Over-sized for one lab; needs a server cluster (Cassandra/Postgres + Temporal services). Not retained, but a valid path if the sequencer ever had to scale to many labs.

**Argo Workflows / Tekton**: YAML DAGs with good semantics (`dependencies`, `onExit`, `retryStrategy`, `synchronization.mutex/semaphore` for locks), but they run steps as Kubernetes pods. Our components are physical/virtual Linux hosts, not pods. Not retained; **Argo's `synchronization` and `onExit` are good references**.

### Hardware-in-the-loop / lab managers (closest domain)

**LAVA** (Linaro Automated Validation Architecture, Python/Django)
- Built for exactly "queue a test job, wait for a free device, reserve it, deploy, boot, run, collect". C1 ✓ queue with UI, C2 ✓ device dictionary, device types, health checks, **multinode jobs with synchronization primitives** (`lava-sync`, `lava-wait` = rendez-vous), C3 ✓ YAML job definitions (`deploy`, `boot`, `test` actions), C5 ✓ cancel, C6 ✓ UI + XML-RPC/REST.
- Why not: centered on embedded boards (flash firmware, boot via serial/u-boot, power-cycle via PDU); deploy/boot actions are fixed types, not free shell commands; heavy deployment (Django, Postgres, dispatcher per worker, often Docker). The "multi-component system that is already installed, we just push inputs and start processes" case fits poorly. **Strong conceptual reference** (job/device/dispatcher split, health checks, multinode sync).

**labgrid** (Python, Pengutronix)
- Library + coordinator for embedded labs. **Places** (named sets of resources) can be **acquired/locked** and **reserved** (reservation queue with priorities and tags, `labgrid-client reserve --wait`), **drivers** for SSH (`SSHDriver` with `run`, `put`, `get`), serial, power, USB; **strategies** = state machines to bring a target to a state (off → shell). Used from pytest via a plugin. Pure Python, pip-installable, well maintained, coordinator now gRPC-based.
- Why not as the kernel: no pipeline DSL, no job queue with datapack semantics, no UI; the coordinator manages places, not pipelines. **Why consider it as a backend**: if the lab includes boards needing power cycling/serial consoles, labgrid's drivers and its reservation coordinator can be plugged as the resource/transport layer later instead of rewriting them.

**OpenTAP** (Keysight, open source core, C#/.NET): test sequencer for instruments with test plans, parallel steps, resources (instruments/DUTs) opened/locked by plan, plugins, CLI + (proprietary) GUI. Domain is right (test sequencing, resources), but .NET-centric, test-plan XML, instruments rather than Linux hosts; the useful GUI is commercial. Not retained. NI TestStand: same domain, proprietary, Windows: excluded.

**Robot Framework** (+ SSHLibrary), **Avocado** (Red Hat test framework with remote runners), **pytest + pytest-xdist/fabric**: test *runners* executing scenarios, not schedulers with queues and resource locks. Robot's keyword syntax could express the run phase, but parallel branches with rendez-vous are not its strength. Not retained as kernel. pytest stays where it already is (inside mexploit).

### Configuration management / remote execution

**Ansible** (+ AWX / Semaphore UI): agentless SSH, inventory (≈ components), `copy`/`synchronize`/`fetch` (≈ deploy inputs/retrieve outputs), `async` + `poll: 0` to start background processes, `wait_for` (port/file/log regex ready-checks), `block/rescue/always` (cleanup), `serial`/`throttle`. AWX/Semaphore add queue, UI, REST.
- Why not as kernel: no multi-resource reservation; rendez-vous between hosts inside one play is clumsy (plays are host-batched; cross-host dependencies require splitting plays); killing an aborted `async` job cleanly is manual; runtime is heavy per command (Python on target, module transfer) and latency matters when starting components in sequence.
- Why keep in mind: a component's custom deploy/start could legitimately be "run this playbook". It fits as **one possible command type**, not as the backbone.

**Fabric / Paramiko / AsyncSSH**: libraries. **AsyncSSH** (asyncio, pure Python, mature, EPL 2.0 / GPL 2.0+ dual license) gives concurrent SSH sessions, SFTP/SCP, process handles with signals, keepalive. **Fabric** (on Paramiko) is simpler but thread-based. Either is the natural transport for our own kernel.

**Rundeck** (Java, open-source core): job scheduler with node inventory, SSH execution, workflows, UI, API. Covers C1/C4/C6, has node filters, but no atomic multi-node lock nor DAG with rendez-vous in the open-source edition. Not retained.

**StackStorm**: event-driven automation (Python), workflows (Orquesta YAML with joins = rendez-vous), SSH runners, UI, API. Functionally broad, but heavy (MongoDB, RabbitMQ, many services), aging community. Not retained.

### Process supervision building blocks
- **systemd** on the components (`systemd-run --unit=tsq-<run>-<cmd>` to start a transient unit, `systemctl kill --signal=KILL` to abort): gives a reliable, standard way to start detached processes and kill the whole process tree on a remote Linux host without custom pid files. Optional but valuable default for start/stop.
- **tmux/screen/nohup + pid file**: fallback when systemd user units are not usable.

## 3. Summary matrix

✓ covers it, ~ partially / with custom work, ✗ not at all.

| Tool | C1 queue | C2 multi-resource lock | C3 YAML DAG + rdv | C4 SSH exec | C5 abort+kill remote | C6 headless + UI | Weight | Verdict |
|---|---|---|---|---|---|---|---|---|
| Jenkins + Lockable Resources | ✓ | ✓ | ~ (Groovy) | ~ | ~ | ✓ | heavy | reference for locks |
| GitLab CI / GH Actions | ~ | ✗/~ | ✓ | ~ | ~ | ✓ | needs forge | **reference for syntax** |
| Buildbot | ✓ | ✓ | ~ (Python) | ~ | ~ | ✓ | medium | no |
| Airflow | ✓ | ~ (pools) | ~ (Python) | ✓ | ~ | ✓ | heavy | no |
| Prefect 3 | ✓ | ~ | ~ (Python) | ~ | ~ | ✓ | medium | possible, not chosen |
| Temporal | ✓ | ~ | ~ (code) | ~ | ✓ | ✓ | heavy | no |
| Argo / Tekton | ✓ | ✓ | ✓ | ✗ (pods) | ✓ | ✓ | k8s | reference |
| LAVA | ✓ | ✓ | ✓ (fixed actions) | ~ | ✓ | ✓ | heavy | **reference for model** |
| labgrid | ~ (reservations) | ✓ | ✗ | ✓ | ~ | ~ (CLI) | light | **optional backend** |
| OpenTAP | ~ | ✓ | ~ (XML) | ~ | ✓ | ~ | .NET | no |
| Ansible (+AWX) | ~/✓ | ✗ | ~ | ✓✓ | ~ | ✓ | medium | **command plugin** |
| Rundeck / StackStorm | ✓ | ✗/~ | ~ | ✓ | ~ | ✓ | heavy | no |
| AsyncSSH / Fabric | - | - | - | ✓✓ | ✓ | - | lib | **transport** |

## 4. Stages, ad hoc components and GitLab

**Stages and shared components (C7).** The lock granularity asked for is the stage, a named set of components, with components that can belong to several stages.
- Jenkins Lockable Resources has labels (a resource can carry several) and `lock(label: ..., quantity: ...)`, but no notion of "this lock implies those other locks".
- LAVA multinode jobs reserve several devices of given types, which is close to "several stages per job", but its devices are boards, not sets of components.
- labgrid **places** are the best match: a place groups resources, a resource can be shared, and the coordinator handles acquire/release and reservations with priorities. It has no pipeline or datapack notion, though.
- The **Ansible inventory** expresses exactly the topology: a group per stage, a host in several groups for a shared component, group variables for stage overrides, ungrouped hosts for ad hoc components. It is a description format, without any locking.

The proposal therefore takes the Ansible inventory format for the topology, and implements stage locks itself as "lock the set of components of the stage", with shared components locked once per datapack and an optional shared (read-only) lock mode for components such as clocks.

**GitLab runner (C8).** Two ways exist:
- implement the GitLab Runner API in the sequencer: fragile (undocumented internals, job tokens, artifacts, traces), not recommended;
- use the official **gitlab-runner** with its **Custom executor**: four scripts (`config`, `prepare`, `run`, `cleanup`) that GitLab calls for each job. The scripts submit the datapack built by the job to the sequencer, stream its log into the job trace, and return its exit code. This needs nothing from the sequencer beyond its CLI/API, and is the recommended path.

**Heterogeneous components (C9).** No tool handles "anything" natively. Ansible has network modules (switches), labgrid has drivers (power, serial, USB), everything else is SSH or HTTP. The proposal makes the transport a plugin per component (`ssh` and `local` built in), so a switch can be driven over SSH, its REST API or SNMP, and a clock over its HTTP or NTP/PTP management interface, without changing the kernel.

## 5. Conclusion

No single tool covers the combination of a datapack queue, atomic reservation of stages made of shared and specific heterogeneous components, a YAML pipeline with rendez-vous, remote kill and per-component or per-stage command overrides, without either:
- heavy infrastructure (Jenkins, LAVA, Zuul, Temporal, Kubernetes), or
- translating our YAML into another tool's code model (Prefect, Airflow), which leaves the hard parts (stage locking, remote kill, overrides) on our side anyway.

The hard parts are small and well understood; the rest is plumbing that well-known libraries provide. So the recommendation is:
- **build a thin sequencer kernel in Python**, inside testsuit: same language, same plugin system, same YAML and Jinja2 stack, direct reuse of datapack and exploit_runner;
- **borrow designs, not servers**: GitLab CI syntax for the pipeline (`needs`, `when`, `timeout`, `after_script`-like `finally`), Jenkins and labgrid semantics for locks and reservations, LAVA's job/device/dispatcher split and health checks, Ansible's inventory format and ready-check vocabulary (`wait_for` port/file/regex);
- **rely on standard libraries** for each layer (AsyncSSH, SQLite/SQLAlchemy, FastAPI, Pydantic, Jinja2), each replaceable;
- **keep doors open**: an `ansible` command type for components already managed by playbooks, a `labgrid` transport for boards with power or serial control, and the gitlab-runner Custom executor for CI.

Fallback if the team prefers adopting a platform rather than owning a kernel: Prefect 3, with each phase compiled to a flow and components mapped to global concurrency limits. It brings the UI and the queue, and costs a server dependency and an API that moves fast.

## 6. Last check (2026-10-23)

Since the first study, requirements SQ-23 to SQ-31 were added:
- maintenance;
- verdicts;
- baseline and conformity;
- the lab folder as a git clone;
- self-test;
- large results;
- sub-pipelines;
- UI and Editor mode;
- the main configuration file;
- the emulated lab.

This section checks again whether a tool now covers the whole, then gives the building block for each requirement.

### 6.1 Tools looked at again

| Tool | What it is (checked 2026-10) | Against our needs | Verdict |
|---|---|---|---|
| **Testflinger** (Canonical) | device test queue: jobs posted to a `job_queue`, served by one agent per device, run in fixed phases (setup, provision, firmware update, test, allocate, reserve, cleanup), with multi-device jobs through `allocate` | close to C1 and to the self-test (setup phase), but one agent per device, fixed phases instead of a pipeline, no stage of shared components, no rendez-vous across devices | reference for phases and agent configuration |
| **Jumpstarter** (Red Hat community) | Python hardware-in-the-loop framework: exporters on the hosts wired to the devices, drivers, leases by label selector with pre- and post-lease hooks, a Kubernetes controller for shared labs, plus local and direct modes without Kubernetes | strong on leases (C2) and drivers (C9); no queue of datapacks, no pipeline DSL; the shared mode needs Kubernetes | possible later as a transport plugin, like labgrid |
| **labgrid** 25.0 / 26.0 | the coordinator is now a standalone gRPC service (25.0); 26.0 (June 2026) fixes reservation locking and supports Python 3.14 | unchanged verdict, and a sounder coordinator | optional backend, as before |
| **Kestra** | Java workflow orchestrator, Apache-2.0, YAML flows edited in a web UI with git integration; needs a JVM and a database (PostgreSQL) | its YAML-plus-UI editing with git is the Editor mode we want, but no hardware resource model, no stage locks, and a heavy server | reference for the Editor's YAML/form round trip |
| GitLab CI, Prefect 3, LAVA, Ansible | no change that affects the first study | | as in section 3 |

None of them covers the combination either, so the conclusion of section 5 stands: a thin Python kernel, with the libraries below.

### 6.2 Building block for each requirement

| Req | Need | Building block | Status in testsuit |
|---|---|---|---|
| DG-1..5 | datapack, baseline, conformity | existing `datapack` tool (Jinja2, Pydantic, BagIt), GitPython for the git state of sources | in place; baseline file to add |
| SQ-1..4 | queue, priorities, backfilling, concurrent runs | own scheduler on SQLite (SQLAlchemy 2), `BEGIN IMMEDIATE` transactions for atomic locking | new |
| SQ-5..9 | stages, ad hoc components, overrides, locks, component types | Ansible YAML inventory format read by PyYAML, own lock model and type library (section 4), transports as plugins | new |
| SQ-10, 11 | state monitoring, visibility | status probes run by the engine on a timer, states in SQLite, `tsq` tables with rich and SSE events for the UI | new |
| SQ-12..14 | pipeline DAG, rendez-vous, delays, ready checks, timeouts | asyncio `TaskGroup` with `graphlib.TopologicalSorter` (standard library) for the DAG, Pydantic for validation | new |
| SQ-15..17 | default commands, sequencer as a component, exploit phase, abort and kill | AsyncSSH and the `local` transport, `systemd-run` transient units with a `setsid` fallback, existing exploit_runner | exploit_runner in place |
| SQ-23 | maintenance | lock state in SQLite, nothing external | new |
| SQ-24 | verdicts and exit codes | own step results; `expect` on exit codes and regex | new |
| SQ-25 | lab folder as a git clone, warnings, reload on demand | GitPython (already a dependency) | new |
| SQ-26 | self-test before each datapack | type commands run by the engine; `pytest-testinfra` possible for richer checks, in a plugin | new |
| SQ-27 | retrieved data, large raw data (pcap) | SFTP streaming (AsyncSSH), `stream-zip` (MIT, zip64, streaming) to write large results zips without loading them, BagIt `fetch.txt` for the external mode; Starlette `FileResponse` with HTTP range requests (since 0.39) to serve large files; captures with `tcpdump`/`dumpcap` as ordinary components | new |
| SQ-28 | sub-pipelines | own expansion before validation, same models | new |
| SQ-18, 19, 22 | CLI first, optional UI, no access control | argparse and rich for the CLI; FastAPI with SSE (`sse-starlette`) for the API; authentication left to a reverse proxy (for example oauth2-proxy) | new |
| SQ-29 | Editor mode | front end: React with TypeScript; **React Flow** (xyflow, MIT) with ELK.js layout for the pipeline graph; **Monaco** with **monaco-yaml** for YAML views validated against the JSON Schemas Pydantic generates; **react-jsonschema-form** (or JSON Forms) for the forms; Monaco's diff view for the diff before commit. Back end: **ruamel.yaml** (already a dependency) to write YAML without losing comments and key order; GitPython for scratch clones, commit and push, with the user's credentials passed through `GIT_ASKPASS` or an SSH agent started for the push, never written to disk | new |
| SQ-20 | GitLab runner | gitlab-runner Custom executor scripts | new |
| SQ-21 | simple, well-known open source | every block above is a common, maintained library; none needs a server besides the sequencer | |
| SQ-30 | main configuration file | Pydantic settings model, loaded from YAML | new |
| SQ-31 | emulated lab on a dev host | the `local` transport and loopback addresses (127.0.0.0/8, multicast included, no root: checked); for platforms that need their real addresses, unprivileged network namespaces (`unshare -rn`) or containers (Podman, Docker Compose) as the next step | example in place (configuration only) |

### 6.3 Points to watch

- **Editor round trip.** Forms and graph edit the YAML through ruamel.yaml, so that comments and layout survive. A Jinja2 expression in a field is edited as text, never evaluated by the UI.
- **Credentials.** Credentials are passed to git for one push only, through an askpass helper, and are never stored, logged or put in a URL.
- **UI build.** The UI is the only part outside Python. It is a separate build served as static files by FastAPI, so the kernel stays installable with pip alone.
- **Network namespaces.** Some distributions restrict unprivileged user namespaces (Ubuntu 24.04 does through AppArmor), which is why the toy-train emulation uses loopback addresses rather than namespaces.
