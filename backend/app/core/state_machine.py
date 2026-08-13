"""Finite-state transition guards for Agent and Task lifecycles."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol, TypeVar

from app.core.enums import AgentStatus, TaskStatus, WorkflowStatus, WorkflowStepStatus
from app.exceptions import InvalidStateTransition

StateT = TypeVar("StateT", bound=StrEnum)


class StatefulEntity(Protocol):
    status: str


class StateMachine[StateT: StrEnum]:
    """Validate and apply transitions without persistence concerns."""

    def __init__(
        self, transitions: Mapping[StateT, frozenset[StateT]], state_type: type[StateT]
    ) -> None:
        self._transitions = transitions
        self._state_type = state_type

    def can_transition(self, current: StateT | str, target: StateT | str) -> bool:
        current_state = self._state_type(current)
        target_state = self._state_type(target)
        return target_state in self._transitions.get(current_state, frozenset())

    def transition(self, entity: StatefulEntity, target: StateT) -> None:
        current = self._state_type(entity.status)
        if current == target:
            return
        if not self.can_transition(current, target):
            raise InvalidStateTransition(
                f"Illegal {self._state_type.__name__} transition: {current} -> {target}",
                details={"current": current, "target": target},
            )
        entity.status = target.value


TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset({TaskStatus.QUEUED}),
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset({TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.SUCCESS: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

AGENT_TRANSITIONS: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.OFFLINE: frozenset({AgentStatus.STARTING}),
    AgentStatus.STARTING: frozenset({AgentStatus.ONLINE, AgentStatus.ERROR}),
    AgentStatus.ONLINE: frozenset({AgentStatus.STOPPING, AgentStatus.ERROR}),
    AgentStatus.STOPPING: frozenset({AgentStatus.OFFLINE, AgentStatus.ERROR}),
    AgentStatus.ERROR: frozenset({AgentStatus.OFFLINE}),
}

WORKFLOW_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.PENDING: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED}),
    WorkflowStatus.RUNNING: frozenset(
        {
            WorkflowStatus.WAITING,
            WorkflowStatus.FAILED,
            WorkflowStatus.SUCCESS,
            WorkflowStatus.CANCELLED,
        }
    ),
    WorkflowStatus.WAITING: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED}),
    WorkflowStatus.FAILED: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED}),
    WorkflowStatus.SUCCESS: frozenset(),
    WorkflowStatus.CANCELLED: frozenset(),
}

WORKFLOW_STEP_TRANSITIONS: dict[WorkflowStepStatus, frozenset[WorkflowStepStatus]] = {
    WorkflowStepStatus.PENDING: frozenset(
        {
            WorkflowStepStatus.RUNNING,
            WorkflowStepStatus.WAITING,
            WorkflowStepStatus.CANCELLED,
            WorkflowStepStatus.SKIPPED,
        }
    ),
    WorkflowStepStatus.RUNNING: frozenset(
        {
            WorkflowStepStatus.WAITING,
            WorkflowStepStatus.FAILED,
            WorkflowStepStatus.SUCCESS,
            WorkflowStepStatus.CANCELLED,
        }
    ),
    WorkflowStepStatus.WAITING: frozenset(
        {WorkflowStepStatus.RUNNING, WorkflowStepStatus.CANCELLED}
    ),
    WorkflowStepStatus.FAILED: frozenset({WorkflowStepStatus.RUNNING}),
    WorkflowStepStatus.SUCCESS: frozenset(),
    WorkflowStepStatus.CANCELLED: frozenset(),
    WorkflowStepStatus.SKIPPED: frozenset(),
}

TaskStateMachine = StateMachine(TASK_TRANSITIONS, TaskStatus)
AgentStateMachine = StateMachine(AGENT_TRANSITIONS, AgentStatus)
WorkflowStateMachine = StateMachine(WORKFLOW_TRANSITIONS, WorkflowStatus)
WorkflowStepStateMachine = StateMachine(WORKFLOW_STEP_TRANSITIONS, WorkflowStepStatus)
