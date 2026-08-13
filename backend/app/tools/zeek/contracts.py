"""Typed Zeek input contracts and sandbox profile."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ZeekSandboxProfile:
    """Deployment and adapter-enforced limits for read-only Zeek ingestion."""

    cpu_limit: float = 0.5
    memory_limit_mb: int = 256
    timeout_seconds: int = 30
    max_input_bytes: int = 5_000_000
    max_records: int = 1_000
    allowed_logs: frozenset[str] = frozenset({"conn", "dns", "http", "ssl", "files", "notice"})
    filesystem_policy: str = "configured-read-only-sources"
    network_policy: str = "none"
    permissions: frozenset[str] = frozenset({"zeek.read"})


@dataclass(frozen=True, slots=True)
class ZeekDataSource:
    """Platform-configured source identity; clients never supply a file path."""

    source_id: str
    path: Path
    fixture: bool = False


@dataclass(frozen=True, slots=True)
class ZeekCollectionResult:
    records: tuple[dict[str, object], ...]
    source_id: str
    bytes_read: int
    lines_read: int
    source_sha256: str
    input_format: str = "jsonl"


@dataclass(frozen=True, slots=True)
class ZeekRecordEnvelope:
    """A parsed Zeek row plus immutable evidence lineage metadata."""

    payload: dict[str, object]
    source_id: str
    line_number: int
    raw_record_sha256: str
    source_sha256: str
    schema_fingerprint: str
