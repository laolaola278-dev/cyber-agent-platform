"""Checkpoint provider interfaces and in-memory implementation."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.exceptions import TelemetryConflict


@dataclass(frozen=True, slots=True)
class Checkpoint:
    provider: str
    stream: str
    partition: str
    consumer: str
    offset: int
    sequence: int
    checksum: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    committed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class CheckpointProvider(Protocol):
    name: str

    async def get(self, stream: str, partition: str, consumer: str) -> Checkpoint | None: ...

    async def commit(self, checkpoint: Checkpoint) -> Checkpoint: ...

    async def list(self) -> list[Checkpoint]: ...


class MemoryCheckpointProvider:
    """Deterministic provider for tests and single-process development."""

    name = "memory"

    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], Checkpoint] = {}

    async def get(self, stream: str, partition: str, consumer: str) -> Checkpoint | None:
        return self._items.get((stream, partition, consumer))

    async def commit(self, checkpoint: Checkpoint) -> Checkpoint:
        key = (checkpoint.stream, checkpoint.partition, checkpoint.consumer)
        previous = self._items.get(key)
        if previous and checkpoint.offset < previous.offset:
            raise TelemetryConflict("Telemetry checkpoint cannot move backwards")
        if previous and checkpoint.sequence < previous.sequence:
            raise TelemetryConflict("Telemetry checkpoint sequence cannot move backwards")
        committed = Checkpoint(
            provider=self.name,
            stream=checkpoint.stream,
            partition=checkpoint.partition,
            consumer=checkpoint.consumer,
            offset=checkpoint.offset,
            sequence=checkpoint.sequence,
            checksum=checkpoint.checksum,
            metadata=dict(checkpoint.metadata),
        )
        self._items[key] = committed
        return committed

    async def list(self) -> list[Checkpoint]:
        return sorted(
            self._items.values(), key=lambda item: (item.stream, item.partition, item.consumer)
        )
