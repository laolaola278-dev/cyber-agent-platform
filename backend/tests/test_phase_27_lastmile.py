"""Phase 27 last-mile coverage (attck/ranker/retrieval/severity -> >=95%)."""

from __future__ import annotations

import asyncio

import pytest

from app.hybrid.attck import AttackTechniqueCandidateGenerator, HybridATTCMapper
from app.hybrid.facts import SecurityFact
from app.hybrid.retrieval import MemoryKnowledgeRetriever, PlatformKnowledgeRetriever
from app.hybrid.severity import DeterministicSeverityEngine, _cvss_severity, _severity_score


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# severity helpers
# ---------------------------------------------------------------------------


def test_severity_score_helper() -> None:
    assert _severity_score("CRITICAL") == 1.0
    assert _severity_score("NONE") == 0.0
    assert _severity_score("bogus") == 0.0


def test_cvss_severity_bands() -> None:
    assert _cvss_severity(9.0) == "CRITICAL"
    assert _cvss_severity(7.5) == "HIGH"
    assert _cvss_severity(5.0) == "MEDIUM"
    assert _cvss_severity(2.0) == "LOW"
    assert _cvss_severity(0.0) == "NONE"


def test_severity_epss_escalation_components() -> None:
    engine = DeterministicSeverityEngine()
    result = engine.assess(finding_severity="MEDIUM", epss=0.8)
    assert result.severity in ("MEDIUM", "HIGH", "CRITICAL")
    epss_factors = [f for f in result.factors if f.name == "epss"]
    assert epss_factors and epss_factors[0].value == 0.8


# ---------------------------------------------------------------------------
# retrieval Platform search branches
# ---------------------------------------------------------------------------


def test_platform_retriever_search_items() -> None:
    class SearchService:
        async def get_by_external_id(self, knowledge_type, external_id):
            return None

        class _Page:
            items = [
                {
                    "knowledge_type": "CVE",
                    "external_id": "CVE-1",
                    "title": "t",
                    "description": "d",
                    "attributes": {},
                }
            ]

        async def search(self, **kwargs):
            return self._Page()

    retriever = PlatformKnowledgeRetriever(SearchService())
    hits = run(retriever.lookup(knowledge_type="CVE", query="x"))
    assert hits and hits[0].external_id == "CVE-1"


def test_platform_retriever_search_raw_list() -> None:
    class SearchService:
        async def get_by_external_id(self, knowledge_type, external_id):
            return None

        async def search(self, **kwargs):
            return [
                {"knowledge_type": "CVE", "external_id": "CVE-2", "title": "t", "attributes": {}}
            ]

    retriever = PlatformKnowledgeRetriever(SearchService())
    hits = run(retriever.lookup(knowledge_type="CVE", query="x"))
    assert hits and hits[0].external_id == "CVE-2"


def test_platform_retriever_search_exception() -> None:
    class BrokenService:
        async def get_by_external_id(self, knowledge_type, external_id):
            return None

        async def search(self, **kwargs):
            raise RuntimeError("down")

    retriever = PlatformKnowledgeRetriever(BrokenService())
    from app.hybrid.retrieval import KnowledgeSourceError

    with pytest.raises(KnowledgeSourceError):
        run(retriever.lookup(knowledge_type="CVE", query="x"))


def test_platform_retriever_lookup_fact() -> None:
    class LookupService:
        async def get_by_external_id(self, knowledge_type, external_id):
            if external_id == "CVE-9":
                return {"knowledge_type": "CVE", "external_id": "CVE-9", "title": "t", "attributes": {}}
            return None

        async def search(self, **kwargs):
            return []

    retriever = PlatformKnowledgeRetriever(LookupService())
    fact = SecurityFact(
        fact_type="vulnerability", value="CVE-9", source_kind="knowledge", source_id="k", confidence=0.5
    )
    hits = run(retriever.lookup_fact(fact))
    assert hits and hits[0].external_id == "CVE-9"


# ---------------------------------------------------------------------------
# attck remaining: non-ATT&CK knowledge skip / invalid id / dedup branch
# ---------------------------------------------------------------------------


def test_attck_knowledge_non_attck_skipped() -> None:
    retriever = MemoryKnowledgeRetriever(
        entries=[{"knowledge_type": "CVE", "external_id": "CVE-1", "title": "t", "keywords": []}]
    )
    generator = AttackTechniqueCandidateGenerator(knowledge=retriever)
    fact = SecurityFact(
        fact_type="vulnerability", value="CVE-1", source_kind="knowledge", source_id="k", confidence=0.5
    )
    hits = run(generator.from_knowledge(fact))
    assert hits == []  # CVE hit is not an ATT&CK technique -> skipped


def test_attck_rank_below_threshold() -> None:
    generator = AttackTechniqueCandidateGenerator(knowledge=None)
    mapper = HybridATTCMapper(generator, threshold=0.7)
    # keyword candidates score 0.6 -> below threshold -> UNKNOWN
    mapping = run(
        mapper.map(
            facts=[
                SecurityFact(
                    fact_type="observed_indicator",
                    value="phishing activity",
                    source_kind="security_event",
                    source_id="e",
                    confidence=0.8,
                    evidence_ref="evidence:1",
                )
            ],
            event_techniques=[],
        )
    )
    assert mapping.unknown is True
