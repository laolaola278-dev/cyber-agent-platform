"""Broker-neutral stream semantics: batch, window, ack and replay."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.schemas.telemetry import TelemetryRecord
from app.telemetry.checkpoint import Checkpoint, CheckpointProvider


@dataclass(frozen=True, slots=True)
class StreamBatch:
    records: tuple[TelemetryRecord, ...]
    first_offset: int
    last_offset: int
    opened_at: datetime


class TelemetryJournal(Protocol):
    def append(self, stream: str, partition: str, records: list[TelemetryRecord]) -> None: ...

    def read(self, stream: str, partition: str) -> list[TelemetryRecord]: ...


class MemoryTelemetryJournal:
    """Process-local replay journal behind an explicit replaceable boundary."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], list[TelemetryRecord]] = {}

    def append(self, stream: str, partition: str, records: list[TelemetryRecord]) -> None:
        existing = self._records.setdefault((stream, partition), [])
        existing.extend(records)
        existing.sort(key=lambda item: (item.offset, item.sequence))

    def read(self, stream: str, partition: str) -> list[TelemetryRecord]:
        return list(self._records.get((stream, partition), []))


class StreamRuntime:
    """Pure stream coordinator; no Kafka, Redis or broker client dependency."""

    def __init__(self, checkpoint_provider: CheckpointProvider) -> None:
        self._checkpoints = checkpoint_provider

    def batch(self, records: Iterable[TelemetryRecord], *, batch_size: int) -> list[StreamBatch]:
        materialized = list(records)
        return [
            StreamBatch(
                records=tuple(materialized[index : index + batch_size]),
                first_offset=materialized[index].offset,
                last_offset=materialized[min(index + batch_size, len(materialized)) - 1].offset,
                opened_at=datetime.now(UTC),
            )
            for index in range(0, len(materialized), batch_size)
            if materialized[index : index + batch_size]
        ]

    def window(
        self, records: Iterable[TelemetryRecord], *, seconds: int
    ) -> list[tuple[TelemetryRecord, ...]]:
        materialized = sorted(records, key=lambda item: item.timestamp)
        windows: list[tuple[TelemetryRecord, ...]] = []
        current: list[TelemetryRecord] = []
        start: datetime | None = None
        for record in materialized:
            if start is None:
                start = record.timestamp
            if record.timestamp - start >= timedelta(seconds=seconds) and current:
                windows.append(tuple(current))
                current = []
                start = record.timestamp
            current.append(record)
        if current:
            windows.append(tuple(current))
        return windows

    async def ack(self, record: TelemetryRecord, *, partition: str, consumer: str) -> Checkpoint:
        return await self._checkpoints.commit(
            Checkpoint(
                provider=self._checkpoints.name,
                stream=record.stream,
                partition=partition,
                consumer=consumer,
                offset=record.offset,
                sequence=record.sequence,
                checksum=record.checksum,
            )
        )

    async def replay(
        self,
        records: Iterable[TelemetryRecord],
        *,
        from_offset: int | None = None,
        to_offset: int | None = None,
        window_seconds: int | None = None,
    ) -> list[TelemetryRecord]:
        selected = [
            record
            for record in records
            if (from_offset is None or record.offset >= from_offset)
            and (to_offset is None or record.offset <= to_offset)
        ]
        if window_seconds is None or not selected:
            return selected
        cutoff = max(record.timestamp for record in selected) - timedelta(seconds=window_seconds)
        return [record for record in selected if record.timestamp >= cutoff]
