"""Investigation timeline reasoning (v2.0 / Phase 26).

Maps SecurityEvent / Finding / Evidence / IncidentTimeline into a unified
``InvestigationTimelineEntry``. Agents may sort, correlate and summarize;
they never modify the original timestamps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TimelineEntryKind(StrEnum):
    SECURITY_EVENT = "SECURITY_EVENT"
    FINDING = "FINDING"
    EVIDENCE = "EVIDENCE"
    INCIDENT = "INCIDENT"
    OBSERVATION = "OBSERVATION"


class InvestigationTimelineEntry(BaseModel):
    """A normalized, read-only view of one timeline item."""

    model_config = ConfigDict(frozen=True)

    entry_id: str
    kind: TimelineEntryKind
    source_id: str
    timestamp: datetime
    title: str
    detail: str = ""
    entities: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class TimelineBuilder:
    """Builds and summarizes unified investigation timelines."""

    def build(self, items: list[dict[str, Any]]) -> list[InvestigationTimelineEntry]:
        entries: list[InvestigationTimelineEntry] = []
        for item in items:
            entry = self._normalize(item)
            if entry is not None:
                entries.append(entry)
        entries.sort(key=lambda entry: entry.timestamp)
        return entries

    def _normalize(self, item: dict[str, Any]) -> InvestigationTimelineEntry | None:
        kind = str(item.get("kind", "")).upper()
        if kind == "SECURITY_EVENT":
            return InvestigationTimelineEntry(
                entry_id=f"event:{item.get('id')}",
                kind=TimelineEntryKind.SECURITY_EVENT,
                source_id=str(item.get("id", "")),
                timestamp=self._as_utc(item.get("timestamp") or item.get("detected_at")),
                title=str(item.get("title", "") or item.get("name", "")),
                detail=str(item.get("summary", "")),
                entities=list(item.get("entities", [])),
                evidence_refs=list(item.get("evidence_refs", [])),
                tags=["event"],
            )
        if kind == "FINDING":
            return InvestigationTimelineEntry(
                entry_id=f"finding:{item.get('id')}",
                kind=TimelineEntryKind.FINDING,
                source_id=str(item.get("id", "")),
                timestamp=self._as_utc(item.get("timestamp") or item.get("detected_at")),
                title=str(item.get("title", "") or item.get("name", "")),
                detail=str(item.get("description", "")),
                entities=list(item.get("entities", [])),
                evidence_refs=list(item.get("evidence_refs", [])),
                tags=["finding"],
            )
        if kind == "EVIDENCE":
            return InvestigationTimelineEntry(
                entry_id=f"evidence:{item.get('id')}",
                kind=TimelineEntryKind.EVIDENCE,
                source_id=str(item.get("id", "")),
                timestamp=self._as_utc(item.get("timestamp") or item.get("collected_at")),
                title=f"Evidence {item.get('id')}",
                detail=str(item.get("summary", "")),
                entities=list(item.get("entities", [])),
                evidence_refs=[f"evidence:{item.get('id')}"],
                tags=["evidence"],
            )
        if kind == "INCIDENT":
            return InvestigationTimelineEntry(
                entry_id=f"incident:{item.get('id')}",
                kind=TimelineEntryKind.INCIDENT,
                source_id=str(item.get("id", "")),
                timestamp=self._as_utc(item.get("timestamp") or item.get("created_at")),
                title=str(item.get("title", "")),
                detail=str(item.get("description", "")),
                entities=list(item.get("entities", [])),
                evidence_refs=list(item.get("evidence_refs", [])),
                tags=["incident"],
            )
        return None

    @staticmethod
    def summarize(entries: list[InvestigationTimelineEntry], *, max_items: int = 10) -> str:
        if not entries:
            return "Timeline is empty"
        ordered = sorted(entries, key=lambda entry: entry.timestamp)
        lines = [
            f"{entry.timestamp.isoformat()} [{entry.kind.value}] {entry.title}"
            for entry in ordered[:max_items]
        ]
        return "\n".join(lines)

    @staticmethod
    def correlate(entries: list[InvestigationTimelineEntry]) -> dict[str, list[str]]:
        """Cluster entry ids by shared entity (read-only correlation)."""
        clusters: dict[str, list[str]] = {}
        for entry in entries:
            for entity in entry.entities:
                clusters.setdefault(entity, []).append(entry.entry_id)
        return clusters

    @staticmethod
    def _as_utc(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            parsed = datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
