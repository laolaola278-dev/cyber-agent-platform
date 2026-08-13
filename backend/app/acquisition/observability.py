"""Phase 28 -- Observability (spec 29).

Structured per-run observation records consumed by metrics/tracing/audit.
Kept intentionally small and dependency-free: an AcquisitionRunRecord is a
plain dataclass the API/console layer persists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class StepObservation:
    step_id: str
    kind: str
    status: str
    url: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int = 0
    bytes_received: int = 0
    retries: int = 0
    replanned: bool = False
    detail: str = ""


@dataclass
class AcquisitionRunRecord:
    run_id: str
    trace_id: str
    goal: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "PENDING"
    strategy: str = ""
    source_type: str = "UNKNOWN"
    steps: list[StepObservation] = field(default_factory=list)
    total_requests: int = 0
    total_bytes: int = 0
    total_duration_ms: int = 0
    evidence_hashes: list[str] = field(default_factory=list)
    completeness_score: float = 0.0
    blocked_reason: str = "NONE"
    blocked_detail: str = ""
    replans: int = 0
    retries: int = 0
    urls_visited: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "goal": self.goal,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": self.status,
            "strategy": self.strategy,
            "source_type": self.source_type,
            "steps": [
                {
                    "step_id": s.step_id,
                    "kind": s.kind,
                    "status": s.status,
                    "url": s.url,
                    "duration_ms": s.duration_ms,
                    "bytes_received": s.bytes_received,
                    "retries": s.retries,
                    "replanned": s.replanned,
                    "detail": s.detail,
                }
                for s in self.steps
            ],
            "total_requests": self.total_requests,
            "total_bytes": self.total_bytes,
            "total_duration_ms": self.total_duration_ms,
            "evidence_hashes": self.evidence_hashes,
            "completeness_score": self.completeness_score,
            "blocked_reason": self.blocked_reason,
            "blocked_detail": self.blocked_detail,
            "replans": self.replans,
            "retries": self.retries,
            "urls_visited": self.urls_visited,
        }


class RunTracker:
    """Accumulates observations while an acquisition runs."""

    def __init__(self, *, run_id: str, trace_id: str, goal: str) -> None:
        self.record = AcquisitionRunRecord(
            run_id=run_id,
            trace_id=trace_id,
            goal=goal,
            started_at=datetime.utcnow(),
        )

    def start_step(self, step_id: str, kind: str, url: str) -> None:
        self.record.steps.append(
            StepObservation(
                step_id=step_id,
                kind=kind,
                status="RUNNING",
                url=url,
                started_at=datetime.utcnow(),
            )
        )

    def finish_step(
        self,
        step_id: str,
        *,
        status: str,
        duration_ms: int = 0,
        bytes_received: int = 0,
        retries: int = 0,
        replanned: bool = False,
        detail: str = "",
    ) -> None:
        for step in reversed(self.record.steps):
            if step.step_id == step_id:
                step.status = status
                step.finished_at = datetime.utcnow()
                step.duration_ms = duration_ms
                step.bytes_received = bytes_received
                step.retries = retries
                step.replanned = replanned
                step.detail = detail
                break

    def add_request(self, *, bytes_received: int = 0) -> None:
        self.record.total_requests += 1
        self.record.total_bytes += bytes_received

    def add_evidence(self, sha256: str) -> None:
        self.record.evidence_hashes.append(sha256)

    def mark_visited(self, url: str) -> None:
        if url not in self.record.urls_visited:
            self.record.urls_visited.append(url)

    def finalize(
        self,
        *,
        status: str,
        strategy: str,
        source_type: str,
        completeness_score: float = 0.0,
        blocked_reason: str = "NONE",
        blocked_detail: str = "",
        replans: int = 0,
        retries: int = 0,
    ) -> AcquisitionRunRecord:
        self.record.status = status
        self.record.strategy = strategy
        self.record.source_type = source_type
        self.record.completeness_score = completeness_score
        self.record.blocked_reason = blocked_reason
        self.record.blocked_detail = blocked_detail
        self.record.replans = replans
        self.record.retries = retries
        self.record.finished_at = datetime.utcnow()
        if self.record.started_at:
            self.record.total_duration_ms = int(
                (self.record.finished_at - self.record.started_at).total_seconds() * 1000
            )
        return self.record
