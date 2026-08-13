"""Strict Worker lifecycle state machine."""

from __future__ import annotations

from collections.abc import Mapping

from app.exceptions import InvalidStateTransition
from app.worker.contracts import WorkerStatus

_ALLOWED_TRANSITIONS: Mapping[WorkerStatus, frozenset[WorkerStatus]] = {
    WorkerStatus.REGISTERED: frozenset({WorkerStatus.ONLINE, WorkerStatus.DEAD}),
    WorkerStatus.ONLINE: frozenset(
        {
            WorkerStatus.BUSY,
            WorkerStatus.DRAINING,
            WorkerStatus.OFFLINE,
            WorkerStatus.UNHEALTHY,
            WorkerStatus.DEAD,
        }
    ),
    WorkerStatus.BUSY: frozenset(
        {
            WorkerStatus.ONLINE,
            WorkerStatus.DRAINING,
            WorkerStatus.UNHEALTHY,
            WorkerStatus.DEAD,
        }
    ),
    WorkerStatus.DRAINING: frozenset({WorkerStatus.OFFLINE, WorkerStatus.DEAD}),
    WorkerStatus.OFFLINE: frozenset(
        {WorkerStatus.REGISTERED, WorkerStatus.ONLINE, WorkerStatus.DEAD}
    ),
    WorkerStatus.UNHEALTHY: frozenset(
        {WorkerStatus.ONLINE, WorkerStatus.DRAINING, WorkerStatus.OFFLINE, WorkerStatus.DEAD}
    ),
    WorkerStatus.DEAD: frozenset({WorkerStatus.REGISTERED}),
}


def validate_transition(current: WorkerStatus, target: WorkerStatus) -> None:
    """Reject every state change not explicitly defined by the lifecycle contract."""

    if current is target:
        return
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidStateTransition(
            f"Worker state transition {current.value} -> {target.value} is not allowed",
            details={"current": current.value, "target": target.value},
        )


def allowed_transitions(current: WorkerStatus) -> frozenset[WorkerStatus]:
    """Return a read-only view useful for tests and compliance tooling."""

    return _ALLOWED_TRANSITIONS[current]
