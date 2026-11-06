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

"""Extension points of the sequencer (README section 12), called by plugins from their register().

- ``register_transport(name, cls)``: how to talk to a component (subclass of transports.Transport);
- ``register_role(name, definition)``: a lab role given as a dict, like in the YAML library;
- ``register_component(name, definition)``: a lab component given as a dict, like in the YAML library;
- ``register_state_probe(name, fn)``: ``async fn(transport, spec, keys) -> (output, exit_status)``;
- ``register_step_type(name, fn)``: ``async fn(step_context, spec) -> StepOutcome``;
- ``register_ready_check(name, fn)``: ``async fn(step_context, spec) -> bool`` for ``wait_for``.
"""
from __future__ import annotations

from typing import Any, Callable

transports: dict[str, type] = {}
roles: dict[str, dict] = {}
components: dict[str, dict] = {}
state_probes: dict[str, Callable] = {}
step_types: dict[str, Callable] = {}
ready_checks: dict[str, Callable] = {}


def _key(name: str) -> str:
    if not name or not isinstance(name, str):
        raise ValueError("a registration needs a non-empty name")
    return name.strip().lower()


def register_transport(name: str, cls: type) -> None:
    transports[_key(name)] = cls


def register_role(name: str, definition: dict[str, Any]) -> None:
    roles[name.strip()] = dict(definition)


def register_component(name: str, definition: dict[str, Any]) -> None:
    components[name.strip()] = dict(definition)


def register_state_probe(name: str, fn: Callable) -> None:
    state_probes[_key(name)] = fn


def register_step_type(name: str, fn: Callable) -> None:
    step_types[_key(name)] = fn


def register_ready_check(name: str, fn: Callable) -> None:
    ready_checks[_key(name)] = fn
