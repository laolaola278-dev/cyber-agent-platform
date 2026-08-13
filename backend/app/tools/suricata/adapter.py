"""Governed read-only Suricata EVE JSON adapter."""

import json
from pathlib import Path

from app.exceptions import DetectionExecutionError, DetectionPolicyViolation
from app.tools.suricata.contracts import (
    SuricataCollectionResult,
    SuricataDataSource,
    SuricataSandboxProfile,
)


class SuricataAdapter:
    """Resolve allowlisted sources, read bounded JSONL, and validate EVE envelopes."""

    def __init__(
        self,
        sources: dict[str, SuricataDataSource],
        *,
        profile: SuricataSandboxProfile,
    ) -> None:
        self._sources = {key.strip().casefold(): value for key, value in sources.items()}
        self._profile = profile

    def collect(self, source_id: str) -> SuricataCollectionResult:
        source = self.require_source(source_id)
        data = self._read_bounded(source.path)
        records = tuple(self.parse_jsonl(data.decode("utf-8")))
        if len(records) > self._profile.max_records:
            raise DetectionPolicyViolation(
                "Suricata EVE source exceeded the record limit",
                details={"records": len(records), "max_records": self._profile.max_records},
            )
        return SuricataCollectionResult(
            records=records,
            source_id=source.source_id,
            bytes_read=len(data),
            lines_read=len(data.splitlines()),
        )

    def require_source(self, source_id: str) -> SuricataDataSource:
        normalized = source_id.strip().casefold()
        if not normalized:
            raise DetectionPolicyViolation("Suricata data_source_id is required")
        try:
            source = self._sources[normalized]
        except KeyError as error:
            raise DetectionPolicyViolation(
                "Suricata data source is not allowlisted",
                details={"data_source_id": normalized},
            ) from error
        path = source.path.resolve()
        if not path.is_file() or path.suffix.casefold() not in {".json", ".jsonl"}:
            raise DetectionPolicyViolation("Suricata data source must be an existing EVE JSON file")
        return source

    def parse_jsonl(self, output: str) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for line_number, line in enumerate(output.splitlines(), start=1):
            candidate = line.strip()
            if not candidate:
                continue
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError as error:
                raise DetectionExecutionError(
                    "Suricata EVE source contains invalid JSONL",
                    details={"line": line_number},
                ) from error
            if not isinstance(value, dict):
                raise DetectionExecutionError(
                    "Suricata EVE record must be a JSON object",
                    details={"line": line_number},
                )
            self._validate_record(value, line_number)
            records.append(value)
        return records

    def status(self) -> dict[str, object]:
        sources = [
            {
                "source_id": source.source_id,
                "available": source.path.resolve().is_file(),
                "fixture": source.fixture,
            }
            for source in sorted(self._sources.values(), key=lambda item: item.source_id)
        ]
        return {
            "healthy": bool(sources) and all(bool(item["available"]) for item in sources),
            "tool": "suricata",
            "version": "8.0.6",
            "input_format": "eve-jsonl",
            "sources": sources,
            "sandbox": {
                "cpu_limit": self._profile.cpu_limit,
                "memory_limit_mb": self._profile.memory_limit_mb,
                "timeout_seconds": self._profile.timeout_seconds,
                "max_input_bytes": self._profile.max_input_bytes,
                "max_records": self._profile.max_records,
                "filesystem_policy": self._profile.filesystem_policy,
                "network_policy": self._profile.network_policy,
                "permissions": sorted(self._profile.permissions),
            },
        }

    def _read_bounded(self, path: Path) -> bytes:
        try:
            size = path.stat().st_size
        except OSError as error:
            raise DetectionExecutionError("Suricata EVE source cannot be inspected") from error
        if size > self._profile.max_input_bytes:
            raise DetectionPolicyViolation(
                "Suricata EVE source exceeded the input byte limit",
                details={"bytes": size, "max_input_bytes": self._profile.max_input_bytes},
            )
        try:
            data = path.read_bytes()
        except OSError as error:
            raise DetectionExecutionError("Suricata EVE source cannot be read") from error
        if len(data) > self._profile.max_input_bytes:
            raise DetectionPolicyViolation("Suricata EVE source exceeded the input byte limit")
        return data

    def _validate_record(self, record: dict[str, object], line_number: int) -> None:
        event_type = record.get("event_type")
        timestamp = record.get("timestamp")
        if not isinstance(event_type, str) or not event_type.strip():
            raise DetectionExecutionError(
                "Suricata EVE record is missing event_type", details={"line": line_number}
            )
        normalized_type = event_type.strip().casefold()
        if normalized_type not in self._profile.allowed_event_types:
            raise DetectionPolicyViolation(
                "Suricata EVE event_type is not allowed",
                details={"line": line_number, "event_type": normalized_type},
            )
        if not isinstance(timestamp, str) or not timestamp.strip():
            raise DetectionExecutionError(
                "Suricata EVE record is missing timestamp", details={"line": line_number}
            )
        if normalized_type == "alert" and not isinstance(record.get("alert"), dict):
            raise DetectionExecutionError(
                "Suricata alert record is missing alert metadata",
                details={"line": line_number},
            )
