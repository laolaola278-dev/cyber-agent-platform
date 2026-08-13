"""Deterministic Finding and SecurityEvent to Incident candidate correlation."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.core.enums import FindingConfidence, FindingSeverity
from app.schemas.incident import IncidentCandidate

SEVERITY_RANK = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
CONFIDENCE_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


class IncidentEvent(Protocol):
    id: UUID
    timestamp: datetime
    source: str
    severity: str
    confidence: str
    rule: str | None
    attributes: dict[str, object]


class IncidentFinding(Protocol):
    id: UUID
    title: str
    description: str
    severity: str
    confidence: str
    fingerprint: str


class IncidentCorrelation:
    """Build candidates only; lifecycle creation remains exclusive to IncidentService."""

    def events(
        self,
        events: Iterable[IncidentEvent],
        *,
        window_seconds: int,
        threshold: int,
        asset_ids: dict[UUID, list[UUID]],
    ) -> list[IncidentCandidate]:
        buckets: dict[str, list[IncidentEvent]] = defaultdict(list)
        for event in sorted(events, key=lambda item: item.timestamp):
            keys = {f"source:{event.source.casefold()}"}
            if event.rule:
                keys.add(f"rule:{event.rule.casefold()}")
            keys.update(f"asset:{asset_id}" for asset_id in asset_ids.get(event.id, []))
            for key in keys:
                buckets[key].append(event)
        candidates: list[IncidentCandidate] = []
        window = timedelta(seconds=window_seconds)
        for key, bucket in sorted(buckets.items()):
            start = 0
            for end, event in enumerate(bucket):
                while event.timestamp - bucket[start].timestamp > window:
                    start += 1
                members = bucket[start : end + 1]
                if len(members) < threshold:
                    continue
                candidate_assets = sorted(
                    {asset_id for member in members for asset_id in asset_ids.get(member.id, [])},
                    key=str,
                )
                candidates.append(
                    IncidentCandidate(
                        title=f"Correlated security activity: {key}",
                        description=(
                            f"{len(members)} governed SecurityEvents correlated by {key}."
                        ),
                        severity=FindingSeverity(self._maximum(members, "severity")),
                        confidence=FindingConfidence(self._maximum(members, "confidence")),
                        source="DETECTION",
                        correlation_key=key,
                        event_ids=[member.id for member in members],
                        asset_ids=candidate_assets,
                        attributes={
                            "first_seen": members[0].timestamp.isoformat(),
                            "last_seen": members[-1].timestamp.isoformat(),
                            "event_count": len(members),
                        },
                    )
                )
                start = end + 1
        return candidates

    def findings(self, findings: Iterable[IncidentFinding]) -> list[IncidentCandidate]:
        return [
            IncidentCandidate(
                title=finding.title,
                description=finding.description,
                severity=FindingSeverity(finding.severity),
                confidence=FindingConfidence(finding.confidence),
                source="ASSESSMENT",
                correlation_key=f"finding:{finding.fingerprint}",
                finding_ids=[finding.id],
            )
            for finding in findings
        ]

    @staticmethod
    def _maximum(items: list[object], field: str) -> str:
        rank = SEVERITY_RANK if field == "severity" else CONFIDENCE_RANK
        return max((str(getattr(item, field)) for item in items), key=rank.__getitem__)
