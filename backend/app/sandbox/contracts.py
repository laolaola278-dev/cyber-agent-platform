"""Tool sandbox contracts shared by local and future isolated providers."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SandboxCommand:
    """A shell-free process request with explicit resource boundaries."""

    executable: str
    arguments: tuple[str, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    working_directory: Path | None = None
    timeout_seconds: int = 60
    max_output_bytes: int = 5_000_000


@dataclass(frozen=True, slots=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float
    output_truncated: bool = False


class SandboxProvider(Protocol):
    """Execution port implemented by local, container, VM, or remote workers."""

    async def execute(self, command: SandboxCommand) -> SandboxResult: ...
