"""Synthetic framework-validation Telemetry Plugin; no real source access."""

import hashlib
import json
from datetime import UTC, datetime

from app.exceptions import TelemetryValidationError
from app.schemas.telemetry import TelemetryExecutionResult, TelemetryRecord
from app.telemetry.contracts import TelemetryEnvelope, TelemetryPluginContext


class FakeTelemetryPlugin:
    name = "synthetic-telemetry"
    version = "1.0.0"
    permissions = frozenset({"telemetry.receive", "telemetry.publish"})
    capabilities = frozenset({"telemetry.receive", "telemetry.transform", "telemetry.publish"})

    def __init__(self) -> None:
        self._initialized = False

    async def initialize(self, context: TelemetryPluginContext) -> None:
        if context.granted_permissions != self.permissions:
            raise TelemetryValidationError("Synthetic telemetry permissions do not match")
        self._initialized = True

    async def receive(self, context: TelemetryPluginContext) -> list[TelemetryEnvelope]:
        self._require_initialized()
        return [dict(item) for item in context.input]

    async def parse(
        self, envelopes: list[TelemetryEnvelope], context: TelemetryPluginContext
    ) -> list[TelemetryEnvelope]:
        self._require_initialized()
        if any(not isinstance(item, dict) for item in envelopes):
            raise TelemetryValidationError("Telemetry envelope must be an object")
        return envelopes

    async def transform(
        self, envelopes: list[TelemetryEnvelope], context: TelemetryPluginContext
    ) -> list[TelemetryRecord]:
        self._require_initialized()
        records: list[TelemetryRecord] = []
        for sequence, envelope in enumerate(envelopes):
            payload = dict(envelope)
            timestamp_raw = payload.pop("timestamp", None)
            timestamp = (
                datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
                if isinstance(timestamp_raw, str)
                else datetime.now(UTC)
            )
            offset_raw = payload.pop("offset", sequence)
            offset = offset_raw if isinstance(offset_raw, int) and offset_raw >= 0 else sequence
            checksum = hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            records.append(
                TelemetryRecord(
                    source="synthetic",
                    timestamp=timestamp,
                    stream=context.stream,
                    offset=offset,
                    sequence=sequence,
                    payload=payload,
                    metadata={"fixture": True, "partition": context.partition},
                    checksum=checksum,
                )
            )
        return records

    async def publish(
        self, records: list[TelemetryRecord], context: TelemetryPluginContext
    ) -> TelemetryExecutionResult:
        self._require_initialized()
        return TelemetryExecutionResult(
            plugin_name=self.name,
            plugin_version=self.version,
            records=records,
            received_count=len(context.input),
            published_count=len(records),
        )

    async def shutdown(self) -> None:
        self._initialized = False

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise TelemetryValidationError("Synthetic telemetry plugin is not initialized")
