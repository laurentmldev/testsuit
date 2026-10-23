"""Extension points of the sequencer (README section 12), called by plugins from their register().

- ``register_transport(name, cls)``: how to talk to a component (subclass of transports.Transport);
- ``register_component_type(name, definition)``: a component type given as a dict, like in the YAML library;
- ``register_state_probe(name, fn)``: ``async fn(transport, spec, keys) -> (output, exit_status)``;
- ``register_step_type(name, fn)``: ``async fn(step_context, spec) -> StepOutcome``;
- ``register_ready_check(name, fn)``: ``async fn(step_context, spec) -> bool`` for ``wait_for``.
"""
from __future__ import annotations

from typing import Any, Callable

transports: dict[str, type] = {}
component_types: dict[str, dict] = {}
state_probes: dict[str, Callable] = {}
step_types: dict[str, Callable] = {}
ready_checks: dict[str, Callable] = {}


def _key(name: str) -> str:
    if not name or not isinstance(name, str):
        raise ValueError("a registration needs a non-empty name")
    return name.strip().lower()


def register_transport(name: str, cls: type) -> None:
    transports[_key(name)] = cls


def register_component_type(name: str, definition: dict[str, Any]) -> None:
    component_types[name.strip()] = dict(definition)


def register_state_probe(name: str, fn: Callable) -> None:
    state_probes[_key(name)] = fn


def register_step_type(name: str, fn: Callable) -> None:
    step_types[_key(name)] = fn


def register_ready_check(name: str, fn: Callable) -> None:
    ready_checks[_key(name)] = fn
