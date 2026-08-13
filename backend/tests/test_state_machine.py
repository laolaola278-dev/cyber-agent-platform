"""Agent and Task state machine tests."""

from dataclasses import dataclass

import pytest

from app.core.enums import AgentStatus, TaskStatus
from app.core.state_machine import AgentStateMachine, TaskStateMachine
from app.exceptions import InvalidStateTransition


@dataclass
class Entity:
    status: str


def test_task_state_machine_applies_legal_lifecycle() -> None:
    task = Entity(status=TaskStatus.CREATED.value)
    TaskStateMachine.transition(task, TaskStatus.QUEUED)
    TaskStateMachine.transition(task, TaskStatus.RUNNING)
    TaskStateMachine.transition(task, TaskStatus.SUCCESS)
    assert task.status == TaskStatus.SUCCESS


def test_task_state_machine_rejects_illegal_transition() -> None:
    task = Entity(status=TaskStatus.CREATED.value)
    with pytest.raises(InvalidStateTransition):
        TaskStateMachine.transition(task, TaskStatus.SUCCESS)


def test_agent_state_machine_supports_error_recovery() -> None:
    agent = Entity(status=AgentStatus.STARTING.value)
    AgentStateMachine.transition(agent, AgentStatus.ERROR)
    AgentStateMachine.transition(agent, AgentStatus.OFFLINE)
    assert agent.status == AgentStatus.OFFLINE
