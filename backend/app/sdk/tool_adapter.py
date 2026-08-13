"""Abstract Tool Adapter interface."""

from abc import ABC, abstractmethod
from typing import Any


class BaseToolAdapter(ABC):
    """Stable adapter boundary for mature external tools."""

    @abstractmethod
    async def initialize(self, config: dict[str, Any]) -> None:
        """Initialize an adapter from validated configuration."""

    @abstractmethod
    async def validate(self, payload: dict[str, Any]) -> None:
        """Validate one execution payload without side effects."""

    @abstractmethod
    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute a validated payload and return normalized output."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Release adapter resources."""
