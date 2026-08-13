"""Zeek Telemetry Plugin: Adapter source read through the Telemetry lifecycle."""

import hashlib
import json
from datetime import UTC, datetime

from app.exceptions import TelemetryValidationError
from app.schemas.telemetry import TelemetryExecutionResult, TelemetryRecord
from app.telemetry.contracts import TelemetryEnvelope, TelemetryPluginContext
from app.tools.zeek import ZeekAdapter


class ZeekTelemetryPlugin:
    """Read Zeek through the source-neutral Telemetry Plugin contract."""

    name = "zeek-telemetry"
    version = "1.0.0"
    permissions = frozenset({"telemetry.receive", "telemetry.publish"})
    capabilities = frozenset({"telemetry.receive", "telemetry.transform", "telemetry.publish"})

    def __init__(self, adapter: ZeekAdapter) -> None:
        self._adapter = adapter
        self._initialized = False

    async def initialize(self, context: TelemetryPluginContext) -> None:
        if context.granted_permissions != self.permissions:
            raise TelemetryValidationError("Zeek telemetry permissions do not match")
        source_id = self._source_id(context)
        self._adapter.require_source(source_id)
        self._initialized = True

    async def receive(self, context: TelemetryPluginContext) -> list[TelemetryEnvelope]:
        self._require_initialized()
        source_id = self._source_id(context)
        result = self._adapter.collect(source_id)
        return [dict(item) for item in result.records]

    async def parse(
        self, envelopes: list[TelemetryEnvelope], context: TelemetryPluginContext
    ) -> list[TelemetryEnvelope]:
        self._require_initialized()
        if any(not isinstance(item, dict) for item in envelopes):
            raise TelemetryValidationError("Zeek telemetry envelope must be an object")
        return envelopes

    async def transform(
        self, envelopes: list[TelemetryEnvelope], context: TelemetryPluginContext
    ) -> list[TelemetryRecord]:
        self._require_initialized()
        records: list[TelemetryRecord] = []
        for sequence, envelope in enumerate(envelopes):
            payload = envelope.get("payload")
            metadata = envelope.get("metadata")
            if not isinstance(payload, dict) or not isinstance(metadata, dict):
                raise TelemetryValidationError("Zeek telemetry envelope is malformed")
            timestamp = self._timestamp(payload.get("ts"))
            checksum = hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            records.append(
                TelemetryRecord(
                    source=f"zeek:{metadata.get('source_id', 'unknown')}",
                    timestamp=timestamp,
                    stream=context.stream,
                    offset=sequence,
                    sequence=sequence,
                    payload=dict(payload),
                    metadata={**metadata, "telemetry_plugin": self.name},
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
            received_count=len(records),
            published_count=len(records),
        )

    async def shutdown(self) -> None:
        self._initialized = False

    @staticmethod
    def _source_id(context: TelemetryPluginContext) -> str:
        candidate = context.input[0].get("data_source_id") if context.input else None
        if not isinstance(candidate, str) or not candidate.strip():
            raise TelemetryValidationError("Zeek telemetry requires data_source_id")
        return candidate.strip().casefold()

    @staticmethod
    def _timestamp(value: object) -> datetime:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value, tz=UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return (
                    parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
                )
            except ValueError as error:
                raise TelemetryValidationError("Zeek telemetry timestamp is invalid") from error
        raise TelemetryValidationError("Zeek telemetry timestamp is missing")

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise TelemetryValidationError("Zeek telemetry plugin is not initialized")
