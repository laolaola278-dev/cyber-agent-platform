"""Deterministic rule-based SecurityEvent correlation."""

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.schemas.detection import CorrelationGroup


class CorrelatableEvent(Protocol):
    id: UUID
    timestamp: datetime
    source: str
    rule: str | None
    attributes: dict[str, object]


class RuleBasedCorrelationEngine:
    """Group events by bounded deterministic keys; no AI or incident creation."""

    def correlate(
        self,
        events: Iterable[CorrelatableEvent],
        *,
        window_seconds: int,
        asset_ids: dict[UUID, list[UUID]],
    ) -> list[CorrelationGroup]:
        ordered = sorted(events, key=lambda item: item.timestamp)
        groups: list[CorrelationGroup] = []
        extractors: tuple[tuple[str, Callable[[CorrelatableEvent], list[str]]], ...] = (
            ("asset", lambda item: [str(value) for value in asset_ids.get(item.id, [])]),
            ("source", lambda item: [item.source]),
            ("ioc", self._iocs),
            ("rule", lambda item: [item.rule] if item.rule else []),
        )
        for key_type, extractor in extractors:
            groups.extend(self._groups_for_key(ordered, key_type, extractor, window_seconds))
        return sorted(groups, key=lambda item: (item.key_type, item.key_value, item.first_seen))

    @staticmethod
    def _iocs(event: CorrelatableEvent) -> list[str]:
        value = event.attributes.get("iocs", [])
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    @staticmethod
    def _groups_for_key(
        events: list[CorrelatableEvent],
        key_type: str,
        extractor: Callable[[CorrelatableEvent], list[str]],
        window_seconds: int,
    ) -> list[CorrelationGroup]:
        buckets: dict[str, list[CorrelatableEvent]] = defaultdict(list)
        for event in events:
            for key in extractor(event):
                buckets[key].append(event)
        output: list[CorrelationGroup] = []
        window = timedelta(seconds=window_seconds)
        for key, bucket in buckets.items():
            start = 0
            for end, event in enumerate(bucket):
                while event.timestamp - bucket[start].timestamp > window:
                    start += 1
                members = bucket[start : end + 1]
                if len(members) >= 2:
                    output.append(
                        CorrelationGroup(
                            key_type=key_type,
                            key_value=key,
                            event_ids=[item.id for item in members],
                            first_seen=members[0].timestamp,
                            last_seen=members[-1].timestamp,
                            count=len(members),
                        )
                    )
                    start = end + 1
        return output
