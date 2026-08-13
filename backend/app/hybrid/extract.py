"""Phase 27 -- deterministic Fact Extractor.

Turns Evidence / SecurityEvent / Finding / Asset / Knowledge payloads into
SecurityFact records using pure rules. No LLM involvement in this layer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.hybrid.facts import (
    FactCandidate,
    FactExtractionResult,
    SecurityFact,
)

# Indicator-like keys commonly present in event attributes / evidence metadata.
_INDICATOR_KEYS = (
    "ip",
    "domain",
    "host",
    "user",
    "url",
    "hash",
    "md5",
    "sha1",
    "sha256",
    "file",
    "process",
    "command",
    "email",
    "ioc",
)


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


def extract_facts_from_event(event: dict[str, Any]) -> FactExtractionResult:
    """Extract verified facts from a SecurityEvent payload.

    The event dict mirrors the platform SecurityEvent model: id, event_type,
    severity, confidence, timestamp, rule, attributes, entities.
    """
    result = FactExtractionResult()
    event_id = str(event.get("id") or event.get("event_id") or "unknown-event")
    ts = _parse_ts(event.get("timestamp"))
    confidence = _bounded(event.get("confidence") or 0.8)

    # 1. Indicator facts from attributes / explicit entities
    entities = event.get("entities") or event.get("related_entities") or []
    if isinstance(entities, list):
        for entity in entities:
            if isinstance(entity, str):
                value = entity
                kind = _classify_entity(value)
            elif isinstance(entity, dict):
                value = str(entity.get("value") or entity.get("identity") or "")
                kind = str(entity.get("type") or _classify_entity(value))
            else:
                continue
            if not value:
                continue
            result.facts.append(
                SecurityFact(
                    fact_type="entity_identity",
                    value=value,
                    source_kind="security_event",
                    source_id=event_id,
                    evidence_ref=f"evidence:{event_id}",
                    confidence=confidence,
                    timestamp=ts,
                    attributes={"entity_kind": kind},
                )
            )

    # 2. Rule metadata fact
    rule = event.get("rule")
    if rule:
        result.facts.append(
            SecurityFact(
                fact_type="rule_metadata",
                value=str(rule),
                source_kind="security_event",
                source_id=event_id,
                evidence_ref=f"evidence:{event_id}",
                confidence=confidence,
                timestamp=ts,
                attributes={"severity": str(event.get("severity", ""))},
            )
        )

    # 3. Event type fact
    if event.get("event_type"):
        result.facts.append(
            SecurityFact(
                fact_type="observed_indicator",
                value=str(event["event_type"]),
                source_kind="security_event",
                source_id=event_id,
                evidence_ref=f"evidence:{event_id}",
                confidence=confidence,
                timestamp=ts,
                attributes={"category": "event_type"},
            )
        )

    # 4. Attributes scan for IOC-ish values
    attributes = event.get("attributes") or {}
    if isinstance(attributes, dict):
        for key, value in attributes.items():
            if not isinstance(value, (str, int, float)):
                continue
            lowered = str(key).lower()
            if any(token in lowered for token in _INDICATOR_KEYS):
                result.facts.append(
                    SecurityFact(
                        fact_type="observed_indicator",
                        value=str(value),
                        source_kind="security_event",
                        source_id=event_id,
                        evidence_ref=f"evidence:{event_id}",
                        confidence=confidence * 0.95,
                        timestamp=ts,
                        attributes={"attribute_key": key},
                    )
                )
    return result


def extract_facts_from_evidence(evidence: dict[str, Any]) -> FactExtractionResult:
    """Extract facts from an Evidence record (url/title/type/sha256)."""
    result = FactExtractionResult()
    evidence_id = str(evidence.get("id") or "unknown-evidence")
    ts = _parse_ts(evidence.get("timestamp"))
    confidence = _bounded(evidence.get("confidence") or 0.85)

    if evidence.get("sha256"):
        result.facts.append(
            SecurityFact(
                fact_type="observed_indicator",
                value=str(evidence["sha256"]),
                source_kind="evidence",
                source_id=evidence_id,
                evidence_ref=f"evidence:{evidence_id}",
                confidence=confidence,
                timestamp=ts,
                attributes={"kind": "sha256"},
            )
        )
    if evidence.get("url"):
        result.facts.append(
            SecurityFact(
                fact_type="observed_indicator",
                value=str(evidence["url"]),
                source_kind="evidence",
                source_id=evidence_id,
                evidence_ref=f"evidence:{evidence_id}",
                confidence=confidence * 0.9,
                timestamp=ts,
                attributes={"kind": "url"},
            )
        )
    if evidence.get("title"):
        result.facts.append(
            SecurityFact(
                fact_type="observed_indicator",
                value=str(evidence["title"]),
                source_kind="evidence",
                source_id=evidence_id,
                evidence_ref=f"evidence:{evidence_id}",
                confidence=confidence * 0.8,
                timestamp=ts,
                attributes={"kind": "title"},
            )
        )
    return result


def extract_facts_from_finding(finding: dict[str, Any]) -> FactExtractionResult:
    """Extract facts from a Finding (severity, title, CVE references)."""
    result = FactExtractionResult()
    finding_id = str(finding.get("id") or "unknown-finding")
    ts = _parse_ts(finding.get("timestamp"))
    confidence = _bounded(finding.get("confidence") or 0.8)

    title = finding.get("title")
    if title:
        result.facts.append(
            SecurityFact(
                fact_type="observed_indicator",
                value=str(title),
                source_kind="finding",
                source_id=finding_id,
                evidence_ref=f"evidence:{finding_id}",
                confidence=confidence,
                timestamp=ts,
                attributes={"severity": str(finding.get("severity", ""))},
            )
        )

    # CVE references
    for ref in _collect_cves(finding):
        result.facts.append(
            SecurityFact(
                fact_type="vulnerability",
                value=ref,
                source_kind="knowledge",
                source_id=finding_id,
                evidence_ref=f"evidence:{finding_id}",
                confidence=confidence,
                timestamp=ts,
                attributes={"kind": "cve"},
            )
        )
    return result


def _collect_cves(payload: dict[str, Any]) -> list[str]:
    """Extract CVE ids from a payload (direct field, references, knowledge)."""
    cves: list[str] = []
    candidates: list[Any] = []
    direct = payload.get("cve") or payload.get("cve_id")
    if direct:
        candidates.append(direct)
    for key in ("references", "knowledge_refs", "iocs"):
        value = payload.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, str):
            candidates.append(value)
    for item in candidates:
        text = str(item)
        import re

        for match in re.finditer(r"CVE-\d{4}-\d{4,7}", text, re.IGNORECASE):
            cves.append(match.group(0).upper())
    return list(dict.fromkeys(cves))


def _classify_entity(value: str) -> str:
    """Rough deterministic classification of an entity string."""
    import ipaddress

    lowered = value.lower()
    if "@" in lowered and "." in lowered:
        return "user"
    try:
        ipaddress.ip_address(value)
        return "ip"
    except ValueError:
        pass
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return "url"
    if ":" in lowered and len(lowered) == 64:
        return "sha256"
    if "." in lowered:
        return "domain"
    if "/" in lowered:
        return "path"
    return "unknown"


def _bounded(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def validate_candidate(candidate: FactCandidate, *, known_evidence: set[str]) -> bool:
    """Deterministic gate: a candidate is promotable only when at least one
    referenced evidence exists in the platform (no evidence = no fact)."""
    if not candidate.evidence_refs:
        return False
    return any(ref in known_evidence for ref in candidate.evidence_refs)
