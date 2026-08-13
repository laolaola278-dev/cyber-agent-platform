"""Pluggable task scheduling strategies."""

from collections.abc import Sequence
from typing import Protocol

from app.models import Agent, Task


class SchedulingStrategy(Protocol):
    """Select one Agent from a repository-provided eligible candidate set."""

    def select(self, task: Task, candidates: Sequence[Agent]) -> Agent | None: ...


class FirstAvailableStrategy:
    """Deterministically select the first eligible Agent."""

    def select(self, task: Task, candidates: Sequence[Agent]) -> Agent | None:
        del task
        return candidates[0] if candidates else None
