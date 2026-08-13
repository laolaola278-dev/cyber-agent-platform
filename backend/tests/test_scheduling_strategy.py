"""Scheduling strategy and configurable eligibility tests."""

from app.models import Agent, Task
from app.orchestrator import FirstAvailableStrategy


def test_first_available_strategy_is_deterministic() -> None:
    first = Agent(name="first", version="1", author="test")
    second = Agent(name="second", version="1", author="test")
    task = Task(name="task", task_type="test")

    strategy = FirstAvailableStrategy()
    assert strategy.select(task, [first, second]) is first
    assert strategy.select(task, []) is None
