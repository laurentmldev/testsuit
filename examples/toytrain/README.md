# Toy train: sample test environment for the sequencer

A sample configuration of the [sequencer](../../src/testsuit/sequencer/README.md) around a small system. Only the test environment is shown, and nothing here runs yet: the sequencer is not implemented, and the toy-train software and its simulation are assumed to exist in their own git repositories.

## The system under test

An autonomous toy train that only goes forward or backward. It has a speed limit of 0.5 m/s, although its propulsion can reach 1 m/s. Two piezo sensors, front and back, detect contacts with obstacles.

The pilot runs at 0.25 m/s for 5 s, then goes up by steps of +0.1 m/s until it reaches the speed limit. When it detects a contact, it stops the train.

| Equipment | Hardware | Role |
|---|---|---|
| CALC-Pilot | Arduino UNO Q, Ethernet through a W5500 | pilot: sends speed commands, reacts to contacts |
| CALC-TM | Raspberry Pi 5 | telemetry: records the system data into a local file |
| AU | Raspberry Pi 5 | acquisition unit: reads the piezo sensors on its GPIO |
| Switch | Auvidea M82 | Ethernet network of the train |
| Propulsion controller | home made, Ethernet | accepts `up`, `down`, `stop`, `keep`, and sends back the current speed. Its battery powers the other equipment |

## The lab

```
             Linux server lab-srv (ad hoc components)
       ┌──────────────────────────────────────────────────────────────────────┐
       │ sequencer   simulated propulsion   simulated piezo sensors   exploit_runner
       └───────┬─────────────────┬──────────────────────┬───────────────────┘
          10.42.0.1       alias 10.42.0.20         USB GPIO adapter
                          (address of the real     wired to AU GPIO 17 / 27
                          propulsion controller)        │
  stage train_1│                                        │
  ┌────────────┴──────── switch Auvidea M82 ────────────┼────────────┐
  │ CALC-Pilot 10.42.0.10   CALC-TM 10.42.0.11   AU 10.42.0.12 ◄─────┘
  └──────────────────────────────────────────────────────────────────┘
```

- **One stage**, `train_1`, holds CALC-Pilot, CALC-TM, AU and the switch. They are locked together.
- **The Linux server** hosts three ad hoc components: the sequencer itself, never locked, which also runs the exploitation, and the two simulations.
- **The simulated propulsion controller** answers on the real controller's address, so the calculators run with exactly the configuration they use on the real train.
- **The simulated piezo sensors** pulse the AU GPIO inputs through a USB GPIO adapter. A contact can be triggered on a condition on the speed feedback, for example 3 s after full speed is reached.

## Test scenarios

| Test | Scenario | Checked by the exploitation |
|---|---|---|
| [max_speed](datapack/testdef/max_speed/testdef.yml) | the train accelerates for 30 s, with no obstacle | the speed never exceeds 0.5 m/s (5 mm/s tolerance); the speed is 0.25 m/s during the first 5 s; the limit is reached; no contact |
| [front_contact](datapack/testdef/front_contact/testdef.yml) | a front contact 3 s after full speed is reached | full speed during the 3 s before the contact; `stop` commanded within 0.2 s; standstill within 1 s; the train does not move again; the speed limit is never exceeded |

Both tests share the setup and the pipeline. Only their scenario dictionary and their criteria differ.

## Files

```
lab/                                   owned by the lab operators
  toytrain_lab.yml                     inventory: stage train_1, ad hoc components on lab-srv
  types/toytrain.yml                   lab types: arduino_unoq, rpi5, auvidea_m82, toytrain_sim
datapack/                              owned by the test authors
  setup/toytrain_setup.yml             test component types (extending the lab types), inputs, resources
  setup/globals.yml                    train characteristics, network plan, versions
  setup/templates/                     configuration of each component, rendered by the datapack tool
  testdef/<test>/testdef.yml           test definition and pipeline
  testdef/<test>/scenario.yml          scenario keys
  testdef/<test>/criteria/<test>/      exploitation criteria (mexploit)
baseline_expected.yml                  expected baseline of the campaign
```

## A run

```
datapack datapack/testdef/front_contact/testdef.yml        # build the zip (inputs, keys, pipeline, baseline)
tsq submit .work/datapacks/toytrain_front_contact_*.zip --baseline baseline_expected.yml --wait --follow
```

The pipeline has two phases.

**test_run** locks the stage `train_1` and the two simulations, then:
1. deploys every input in parallel, and flashes the CALC-Pilot microcontroller;
2. starts the simulations, then CALC-TM and AU, each waiting for its `READY` log line;
3. starts the pilot once everything listens (rendez-vous), and waits for its monitored state to be `running`;
4. lets the scenario run for its duration;
5. stops the pilot first, then the rest;
6. finally collects every output (`telemetry.csv`, `propulsion.csv`, `piezo.csv`, logs), even after a kill.

**exploit** runs on the lab server. It receives these outputs as inputs and runs `exploit_runner` on the criteria. Exit code 0 means the verdict is `succeeded`; any other exit code means `failed`.

The results zip carries the baseline with the git tags of the software and simulation, the lab inventory version, and the software version read on each calculator. A mismatch with `baseline_expected.yml` flags the run as non-conform.

## Assumptions

Interfaces assumed for this example, all belonging to the toy-train software:
- each calculator runs a systemd service `toytrain-<role>`, configured from `<remote_root>/<component>/input/`, that logs `READY` when listening;
- CALC-Pilot has an `install` helper that flashes its microcontroller;
- messages are UDP. Commands go to the propulsion controller on port 5000. Speed feedback (20 Hz), contact events and pilot state are multicast on 239.42.0.1, ports 5001 to 5003;
- the telemetry file is a CSV with columns `Time, speed_mps, pilot_cmd, pilot_state, contact_front, contact_back`, read by testsuit's generic CSV reader;
- the switch is unmanaged: it is only monitored (ping), and locked with its stage.

This sample also uses a few details that the sequencer README leaves open:
- a probe state can be matched on an exit code (`exit: 0`);
- host variables are reachable as `component.vars.*`, with `component.output_dir` next to `component.input_dir`;
- `install` and `version` are ordinary named commands;
- `wait_for: {state: ...}` names its component.

Today's `datapack` tool cannot build these datapacks yet: its setup schema doesn't accept the sequencer fields (`extends`, `stages`, `stage`, `role`). Those fields are part of the sequencer work (README, section 16, step 2).
