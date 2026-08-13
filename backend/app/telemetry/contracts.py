"""Broker-neutral Telemetry Plugin SDK contracts."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.schemas.telemetry import TelemetryExecutionResult, TelemetryPolicy, TelemetryRecord

TelemetryEnvelope = dict[str, Any]


@dataclass(frozen=True, slots=True)
class TelemetryPluginContext:
    """Minimal context; no database, DetectionService or IncidentService access."""

    telemetry_task_id: UUID
    task_id: UUID
    trace_id: str
    stream: str
    partition: str
    consumer: str
    policy: TelemetryPolicy
    input: tuple[TelemetryEnvelope, ...]
    granted_permissions: frozenset[str]


class TelemetryPlugin(Protocol):
    """Six-stage lifecycle for every future telemetry source plugin."""

    name: str
    version: str
    permissions: frozenset[str]
    capabilities: frozenset[str]

    async def initialize(self, context: TelemetryPluginContext) -> None: ...

    async def receive(self, context: TelemetryPluginContext) -> list[TelemetryEnvelope]: ...

    async def parse(
        self, envelopes: list[TelemetryEnvelope], context: TelemetryPluginContext
    ) -> list[TelemetryEnvelope]: ...

    async def transform(
        self, envelopes: list[TelemetryEnvelope], context: TelemetryPluginContext
    ) -> list[TelemetryRecord]: ...

    async def publish(
        self, records: list[TelemetryRecord], context: TelemetryPluginContext
    ) -> TelemetryExecutionResult: ...

    async def shutdown(self) -> None: ...
