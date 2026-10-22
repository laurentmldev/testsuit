"""Writes the sample recordings of sample/data: what CALC-TM and the simulated propulsion controller
would record during the max_speed and front_contact tests (nominal behaviour of the train).

    python sample/make_runs.py

The files are committed; this script only documents how they were made.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

START = pd.Timestamp("2026-10-22T10:00:00Z")
RATE_HZ = 20
SPEED_LIMIT = 0.5
ACCEL, BRAKING = 0.5, 2.0        # m/s2 of the simulated propulsion
# pilot commands and states, as recorded by CALC-TM
KEEP, UP, DOWN, STOP = 0, 1, 2, 3
IDLE, RUNNING, STOPPED = 0, 1, 2


def run(duration_s: float, contact_after_full_speed_s: float | None) -> pd.DataFrame:
    dt = 1.0 / RATE_HZ
    rows, speed, target, state = [], 0.0, 0.0, IDLE
    contact_at = full_speed_at = None
    for i in range(int(duration_s * RATE_HZ) + 1):
        t = i * dt
        cmd = KEEP
        # pilot: starts at 1 s, 0.25 m/s for 5 s, then +0.1 m/s every 2 s up to the limit
        if state == IDLE and t >= 1.0:
            state, target, cmd = RUNNING, 0.25, UP
        elif state == RUNNING and t >= 6.0 and target < SPEED_LIMIT and abs((t - 6.0) % 2.0) < dt / 2:
            target, cmd = min(round(target + 0.1, 3), SPEED_LIMIT), UP
        if full_speed_at is None and speed >= SPEED_LIMIT - 1e-9:
            full_speed_at = t
        contact = int(contact_after_full_speed_s is not None and full_speed_at is not None
                      and t >= full_speed_at + contact_after_full_speed_s)
        if contact and contact_at is None:
            contact_at = t
        # the pilot reacts one sample after the AU reports the contact
        if contact_at is not None and state == RUNNING and t >= contact_at + dt:
            state, target, cmd = STOPPED, 0.0, STOP
        # propulsion: goes to the target at its acceleration / braking rate
        rate = ACCEL if target > speed else BRAKING
        speed = float(np.clip(target, speed - rate * dt, speed + rate * dt))
        rows.append((t, round(speed, 4), cmd, state, contact, 0))
    return pd.DataFrame(rows, columns=["t", "speed_mps", "pilot_cmd", "pilot_state", "contact_front", "contact_back"])


def write(name: str, df: pd.DataFrame) -> None:
    folder = Path(__file__).parent / "data" / name
    folder.mkdir(parents=True, exist_ok=True)
    dates = (START + pd.to_timedelta(df["t"], unit="s")).dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str[:-3] + "Z"
    with open(folder / "telemetry.csv", "w", newline="") as f:
        f.write("#TOYTRAIN-TM v1;unit=CALC-TM;train=TT-01\n")
        df.drop(columns="t").assign(Time=dates)[["Time", "speed_mps", "pilot_cmd", "pilot_state", "contact_front", "contact_back"]] \
          .to_csv(f, sep=";", index=False)
    record = {"device": "propulsion-sim", "start": START.isoformat().replace("+00:00", "Z"), "rate_hz": RATE_HZ,
              "channels": {"cmd_received": df["pilot_cmd"].tolist(), "speed_sent_mps": df["speed_mps"].tolist()}}
    (folder / "propulsion.propj").write_text(json.dumps(record) + "\n")


if __name__ == "__main__":
    write("max_speed", run(30, None))
    write("front_contact", run(25, 3.0))
