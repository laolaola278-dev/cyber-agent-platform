"""Typed Suricata EVE input contracts and sandbox profile."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SuricataSandboxProfile:
    """Deployment and adapter-enforced limits for read-only EVE ingestion."""

    cpu_limit: float = 0.5
    memory_limit_mb: int = 256
    timeout_seconds: int = 30
    max_input_bytes: int = 5_000_000
    max_records: int = 1_000
    allowed_event_types: frozenset[str] = frozenset(
        {"alert", "flow", "stats", "dns", "http", "tls", "fileinfo"}
    )
    filesystem_policy: str = "configured-read-only-sources"
    network_policy: str = "none"
    permissions: frozenset[str] = frozenset({"eve.read"})


@dataclass(frozen=True, slots=True)
class SuricataDataSource:
    """Platform-configured source identity; never supplied as a client path."""

    source_id: str
    path: Path
    fixture: bool = False


@dataclass(frozen=True, slots=True)
class SuricataCollectionResult:
    records: tuple[dict[str, object], ...]
    source_id: str
    bytes_read: int
    lines_read: int
