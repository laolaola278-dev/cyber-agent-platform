"""Governed read-only Zeek JSONL adapter."""

import hashlib
import json
from pathlib import Path

from app.exceptions import DetectionExecutionError, DetectionPolicyViolation
from app.tools.zeek.contracts import (
    ZeekCollectionResult,
    ZeekDataSource,
    ZeekRecordEnvelope,
    ZeekSandboxProfile,
)


class ZeekAdapter:
    """Resolve allowlisted sources, read bounded JSONL, and preserve line lineage."""

    def __init__(
        self,
        sources: dict[str, ZeekDataSource],
        *,
        profile: ZeekSandboxProfile,
    ) -> None:
        self._sources = {key.strip().casefold(): value for key, value in sources.items()}
        self._profile = profile

    def collect(self, source_id: str) -> ZeekCollectionResult:
        source = self.require_source(source_id)
        data = self._read_bounded(source.path)
        source_sha256 = hashlib.sha256(data).hexdigest()
        envelopes = self.parse_jsonl(
            data.decode("utf-8"), source_id=source.source_id, source_sha256=source_sha256
        )
        if len(envelopes) > self._profile.max_records:
            raise DetectionPolicyViolation(
                "Zeek source exceeded the record limit",
                details={"records": len(envelopes), "max_records": self._profile.max_records},
            )
        return ZeekCollectionResult(
            records=tuple(
                {
                    "payload": envelope.payload,
                    "metadata": {
                        "source_id": envelope.source_id,
                        "line_number": envelope.line_number,
                        "raw_record_sha256": envelope.raw_record_sha256,
                        "source_sha256": envelope.source_sha256,
                        "schema_fingerprint": envelope.schema_fingerprint,
                    },
                }
                for envelope in envelopes
            ),
            source_id=source.source_id,
            bytes_read=len(data),
            lines_read=len(data.splitlines()),
            source_sha256=source_sha256,
        )

    def require_source(self, source_id: str) -> ZeekDataSource:
        normalized = source_id.strip().casefold()
        if not normalized:
            raise DetectionPolicyViolation("Zeek data_source_id is required")
        try:
            source = self._sources[normalized]
        except KeyError as error:
            raise DetectionPolicyViolation(
                "Zeek data source is not allowlisted",
                details={"data_source_id": normalized},
            ) from error
        path = source.path.resolve()
        if not path.is_file() or path.suffix.casefold() not in {".json", ".jsonl"}:
            raise DetectionPolicyViolation("Zeek data source must be an existing JSONL file")
        return source

    def parse_jsonl(
        self, output: str, *, source_id: str = "unknown", source_sha256: str = ""
    ) -> list[ZeekRecordEnvelope]:
        envelopes: list[ZeekRecordEnvelope] = []
        for line_number, line in enumerate(output.splitlines(), start=1):
            candidate = line.strip()
            if not candidate:
                continue
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError as error:
                raise DetectionExecutionError(
                    "Zeek source contains invalid JSONL", details={"line": line_number}
                ) from error
            if not isinstance(value, dict):
                raise DetectionExecutionError(
                    "Zeek record must be a JSON object", details={"line": line_number}
                )
            self._validate_record(value, line_number)
            envelopes.append(
                ZeekRecordEnvelope(
                    payload=value,
                    source_id=source_id,
                    line_number=line_number,
                    raw_record_sha256=hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
                    source_sha256=source_sha256,
                    schema_fingerprint=self._schema_fingerprint(value),
                )
            )
        return envelopes

    def parse_tsv(self, output: str) -> list[dict[str, object]]:
        """Reserve TSV as an explicit future boundary without accepting it in Phase 13."""

        del output
        raise DetectionPolicyViolation("Zeek TSV parsing is reserved for a future phase")

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
            "tool": "zeek",
            "version": "7.0.0",
            "input_format": "jsonl",
            "supported_logs": sorted(self._profile.allowed_logs),
            "tsv_reserved": True,
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
            raise DetectionExecutionError("Zeek source cannot be inspected") from error
        if size > self._profile.max_input_bytes:
            raise DetectionPolicyViolation(
                "Zeek source exceeded the input byte limit",
                details={"bytes": size, "max_input_bytes": self._profile.max_input_bytes},
            )
        try:
            data = path.read_bytes()
        except OSError as error:
            raise DetectionExecutionError("Zeek source cannot be read") from error
        if len(data) > self._profile.max_input_bytes:
            raise DetectionPolicyViolation("Zeek source exceeded the input byte limit")
        return data

    def _validate_record(self, record: dict[str, object], line_number: int) -> None:
        log_name = self._log_name(record)
        if log_name not in self._profile.allowed_logs:
            raise DetectionPolicyViolation(
                "Zeek log type is not allowed",
                details={"line": line_number, "log": log_name},
            )
        timestamp = record.get("ts")
        if not isinstance(timestamp, int | float | str):
            raise DetectionExecutionError(
                "Zeek record is missing ts", details={"line": line_number}
            )
        if log_name in {"conn", "dns", "http", "ssl"} and not self._text(record.get("uid")):
            raise DetectionExecutionError(
                "Zeek network record is missing uid", details={"line": line_number}
            )
        if log_name == "files" and not (
            self._text(record.get("fuid")) or self._text(record.get("uid"))
        ):
            raise DetectionExecutionError(
                "Zeek files record is missing fuid", details={"line": line_number}
            )
        if log_name == "notice" and not self._text(record.get("note")):
            raise DetectionExecutionError(
                "Zeek notice record is missing note", details={"line": line_number}
            )

    @staticmethod
    def _log_name(record: dict[str, object]) -> str:
        candidate = record.get("_log") or record.get("log") or record.get("log_type")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().casefold().removesuffix(".log")
        if isinstance(record.get("note"), str):
            return "notice"
        if isinstance(record.get("fuid"), str):
            return "files"
        if isinstance(record.get("query"), str):
            return "dns"
        if isinstance(record.get("method"), str) or isinstance(record.get("uri"), str):
            return "http"
        if isinstance(record.get("server_name"), str) or isinstance(record.get("cipher"), str):
            return "ssl"
        return "conn"

    @staticmethod
    def _schema_fingerprint(record: dict[str, object]) -> str:
        fields = "|".join(sorted(str(key) for key in record))
        return hashlib.sha256(fields.encode("utf-8")).hexdigest()

    @staticmethod
    def _text(value: object) -> str:
        return value.strip() if isinstance(value, str) else ""
