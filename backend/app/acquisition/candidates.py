"""Phase 28 -- Fact / Entity / Knowledge candidates (spec 22).

ExtractedDocument produces *candidates* only. They MUST pass the platform's
existing validation systems before becoming SecurityFacts / AssetRelations /
Knowledge. The acquisition agent never writes verified facts directly.

Candidates are carried on the AcquisitionResult so the Hybrid layer /
validation pipeline can consume them downstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# conservative CVE pattern (not a proof of validity)
_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_SHA_RE = re.compile(r"\b[0-9a-fA-F]{40}\b|\b[0-9a-fA-F]{64}\b")


@dataclass
class FactCandidate:
    fact_type: str
    value: str
    evidence_id: str | None
    confidence: float = 0.5
    source_url: str = ""


@dataclass
class EntityLinkCandidate:
    entity_type: str
    value: str
    asset_hint: str | None = None
    evidence_id: str | None = None
    source_url: str = ""


@dataclass
class KnowledgeCandidate:
    knowledge_type: str  # CVE | IOC | ATT&CK | THREAT
    external_id: str
    title: str
    evidence_id: str | None
    source_url: str = ""


@dataclass
class CandidateBundle:
    facts: list[FactCandidate] = field(default_factory=list)
    entities: list[EntityLinkCandidate] = field(default_factory=list)
    knowledge: list[KnowledgeCandidate] = field(default_factory=list)

    def extend(self, other: CandidateBundle) -> None:
        self.facts.extend(other.facts)
        self.entities.extend(other.entities)
        self.knowledge.extend(other.knowledge)


def extract_candidates(
    text: str,
    *,
    evidence_id: str | None,
    source_url: str,
    title: str = "",
) -> CandidateBundle:
    """Deterministic candidate extraction from an ExtractedDocument's text.

    Candidates only -- downstream validation decides what becomes real.
    """
    bundle = CandidateBundle()

    # CVEs -> KnowledgeCandidate + FactCandidate
    seen_cves: set[str] = set()
    for match in _CVE_RE.finditer(text):
        cve = match.group(0).upper()
        if cve in seen_cves:
            continue
        seen_cves.add(cve)
        bundle.knowledge.append(
            KnowledgeCandidate(
                knowledge_type="CVE",
                external_id=cve,
                title=cve,
                evidence_id=evidence_id,
                source_url=source_url,
            )
        )
        bundle.facts.append(
            FactCandidate(
                fact_type="vulnerability",
                value=cve,
                evidence_id=evidence_id,
                confidence=0.6,
                source_url=source_url,
            )
        )

    # IPs -> EntityLinkCandidate (asset hint left for validation)
    seen_ips: set[str] = set()
    for match in _IP_RE.finditer(text):
        ip = match.group(0)
        if _is_plausible_ip(ip) and ip not in seen_ips:
            seen_ips.add(ip)
            bundle.entities.append(
                EntityLinkCandidate(
                    entity_type="ip",
                    value=ip,
                    evidence_id=evidence_id,
                    source_url=source_url,
                )
            )

    # hashes (sha1/sha256) -> FactCandidate observed_indicator
    for match in _SHA_RE.finditer(text):
        bundle.facts.append(
            FactCandidate(
                fact_type="observed_indicator",
                value=match.group(0),
                evidence_id=evidence_id,
                confidence=0.5,
                source_url=source_url,
            )
        )

    if title:
        bundle.facts.append(
            FactCandidate(
                fact_type="document_title",
                value=title[:500],
                evidence_id=evidence_id,
                confidence=0.8,
                source_url=source_url,
            )
        )
    return bundle


def _is_plausible_ip(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)
