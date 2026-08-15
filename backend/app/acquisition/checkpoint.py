"""Acquisition checkpoint -- durable resume state for an AcquisitionRun.

A checkpoint lets a worker resume the SAME AcquisitionRun from where it
stopped instead of restarting from page 1: the current URL / pagination
cursor, accumulated records, used budgets, evidence refs, strategy and
replan count are all persisted between executions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AcquisitionCheckpoint:
    """Serializable resume state captured at the last worker boundary."""

    run_id: str = ""
    current_url: str = ""
    page_number: int = 0  # next page to fetch (1-based); 0 = start of pagination
    records_seen: list[dict[str, str]] = field(default_factory=list)  # url -> sha256
    requests_used: int = 0
    bytes_used: int = 0
    evidence_refs: list[str] = field(default_factory=list)
    strategy: str = ""
    replan_count: int = 0
    visited_urls: list[str] = field(default_factory=list)
    documents_captured: int = 0
    status: str = "QUEUED"
    blocked_reason: str = "NONE"
    blocked_detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> AcquisitionCheckpoint:
        if not payload:
            return cls()
        known = {name: value for name, value in payload.items() if name in cls.__dataclass_fields__}
        return cls(**known)

    def snapshot(self, result: Any) -> None:
        """Capture current result state into this checkpoint."""
        self.records_seen = [
            {"url": record.get("source_url", ""), "sha256": record.get("artifact_sha256", "")}
            for record in getattr(result, "records", [])
            if record.get("source_url")
        ]
        self.page_number = getattr(result, "pagination_page", 0)
        self.requests_used = len(getattr(result, "visited_urls", []))
        self.bytes_used = getattr(result, "total_bytes", 0)
        self.evidence_refs = list(getattr(result, "evidence_ids", []))
        self.replan_count = getattr(result, "replans", 0)
        # only SUCCESSFUL pages survive the checkpoint: a page that was
        # attempted but failed (e.g. timeout) must be retried on resume
        document_urls = {doc.source_url for doc in getattr(result, "documents", [])}
        self.visited_urls = [
            url
            for url in getattr(result, "visited_urls", [])
            if url in document_urls or url == self.current_url
        ]
        self.documents_captured = len(getattr(result, "documents", []))
        self.strategy = ".".join(getattr(result, "strategy_history", []))
        status = getattr(result, "status", "")
        self.status = getattr(status, "value", "RUNNING") if status else "RUNNING"
        reason = getattr(result, "blocked_reason", None)
        self.blocked_reason = getattr(reason, "value", "NONE")
        self.blocked_detail = getattr(result, "blocked_detail", "")
