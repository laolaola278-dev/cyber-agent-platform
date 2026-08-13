"""Phase 27 final branch completion (5 modules -> >=95%)."""

from __future__ import annotations

import asyncio

from app.hybrid.attck import AttackTechniqueCandidateGenerator, HybridATTCMapper
from app.hybrid.engine import HybridEngine, HybridEngineConfig
from app.hybrid.extract import _classify_entity, _parse_ts, extract_facts_from_event
from app.hybrid.facts import SecurityFact
from app.hybrid.retrieval import MemoryKnowledgeRetriever
from app.hybrid.severity import DeterministicSeverityEngine


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# attck.py: 114/116 (non-matching keyword), 146 (non-T knowledge), 152-155
# (invalid technique id from knowledge), 187-191 (dedup), 249 (below threshold),
# 280-284 (LLM rank partial order)
# ---------------------------------------------------------------------------


def test_attck_keyword_no_match() -> None:
    generator = AttackTechniqueCandidateGenerator(knowledge=None)
    assert generator.from_keywords("completely unrelated text", evidence=[]) == []


def test_attck_knowledge_invalid_technique_id() -> None:
    retriever = MemoryKnowledgeRetriever(
        entries=[{"knowledge_type": "ATT&CK", "external_id": "BOGUS", "title": "t", "keywords": []}]
    )
    generator = AttackTechniqueCandidateGenerator(knowledge=retriever)
    fact = SecurityFact(
        fact_type="technique", value="BOGUS", source_kind="knowledge", source_id="k", confidence=0.5
    )
    assert run(generator.from_knowledge(fact)) == []


def test_attck_dedup_event_over_knowledge() -> None:
    retriever = MemoryKnowledgeRetriever(
        entries=[{"knowledge_type": "ATT&CK", "external_id": "T1566", "title": "t", "keywords": []}]
    )
    generator = AttackTechniqueCandidateGenerator(knowledge=retriever)
    fact = SecurityFact(
        fact_type="observed_indicator", value="phishing", source_kind="security_event",
        source_id="e", confidence=0.8, evidence_ref="evidence:1",
    )
    candidates = run(
        generator.generate(facts=[fact], event_techniques=["T1566"])
    )
    # event-declared T1566 (1.0) beats knowledge/rule candidates
    assert candidates[0].score == 1.0


def test_attck_llm_rank_partial_valid_order() -> None:
    class PartialRanker:
        async def rank_techniques(self, ids):
            from app.hybrid.ranker import TechniqueRankResponse

            # only one of the two ids valid; the other dropped
            return TechniqueRankResponse(order=[ids[0]], explanation="ranked")

    generator = AttackTechniqueCandidateGenerator(knowledge=None)
    mapping = run(
        HybridATTCMapper(generator, llm=PartialRanker(), threshold=0.35).map(
            facts=[], event_techniques=["T1566", "T1059"]
        )
    )
    assert mapping.technique_id in ("T1566", "T1059")
    assert mapping.explanation == "ranked"


# ---------------------------------------------------------------------------
# engine.py: 252 (no platform facts uncertainty), 370-371/377-380
# ---------------------------------------------------------------------------


def test_engine_no_facts_uncertainty() -> None:
    engine = HybridEngine(
        knowledge=MemoryKnowledgeRetriever(entries=[]),
        config=HybridEngineConfig(use_llm=False, use_retrieval=False),
    )
    output = run(
        engine.triage(
            source={"id": "e-nf", "title": "", "severity": "LOW", "evidence_refs": ["evidence:1"]},
            context={},
        )
    )
    # title empty -> no facts extracted
    assert "no platform facts extracted" in output.uncertainties


# ---------------------------------------------------------------------------
# extract.py: 39 (entity dict with dict value), 43-44 (dict entity), 70 (rule),
# 122 (sha256 classify), 240 (references list), 265 (CVE regex), 276-277
# ---------------------------------------------------------------------------


def test_extract_entity_dict_with_unknown_type() -> None:
    result = extract_facts_from_event(
        {"id": "e1", "entities": [{"value": "10.0.0.1"}], "confidence": 0.9}
    )
    assert any(f.value == "10.0.0.1" for f in result.facts)


def test_extract_rule_and_event_type_facts() -> None:
    result = extract_facts_from_event(
        {
            "id": "e2",
            "event_type": "malware_detected",
            "rule": "malware-rule-1",
            "confidence": 0.9,
        }
    )
    types = {f.fact_type for f in result.facts}
    assert "rule_metadata" in types
    assert "observed_indicator" in types


def test_extract_sha256_colon_classify() -> None:
    # 64-char string containing ":" hits the sha256 branch
    assert _classify_entity(("ab:" * 21) + "a") == "sha256"


def test_parse_ts_invalid() -> None:
    from datetime import datetime

    parsed = _parse_ts("not-a-date")
    assert isinstance(parsed, datetime)


# ---------------------------------------------------------------------------
# retrieval.py: 77 (CVE fact lookup), 88-89 (search), 94 (search result None),
# 97 (search page items), 100-108 (search error)
# ---------------------------------------------------------------------------


def test_retriever_cve_fact_lookup() -> None:
    retriever = MemoryKnowledgeRetriever(
        entries=[{"knowledge_type": "CVE", "external_id": "CVE-1", "title": "t", "keywords": []}]
    )
    fact = SecurityFact(
        fact_type="vulnerability", value="CVE-1", source_kind="knowledge", source_id="k", confidence=0.5
    )
    hits = run(retriever.lookup_fact(fact))
    assert hits and hits[0].external_id == "CVE-1"


def test_retriever_query_path() -> None:
    retriever = MemoryKnowledgeRetriever(
        entries=[{"knowledge_type": "IOC", "external_id": "i1", "title": "x", "keywords": ["phish"]}]
    )
    hits = run(retriever.lookup(knowledge_type="IOC", query="phish"))
    assert hits and hits[0].external_id == "i1"


# ---------------------------------------------------------------------------
# severity.py: 49 (level score), 54 (epss none), 59-61 (epss low), 120-121
# (criticality none), 157-163 (confidence), 177/179/182 (level thresholds)
# ---------------------------------------------------------------------------


def test_severity_epss_below_threshold() -> None:
    engine = DeterministicSeverityEngine()
    result = engine.assess(finding_severity="HIGH", epss=0.1)
    assert result.severity == "HIGH"  # epss below 0.5 no escalation
    assert any(f.name == "finding_severity" for f in result.factors)


def test_severity_level_thresholds() -> None:
    assert DeterministicSeverityEngine._level(0.8) == "CRITICAL"
    assert DeterministicSeverityEngine._level(0.6) == "HIGH"
    assert DeterministicSeverityEngine._level(0.3) == "MEDIUM"
    assert DeterministicSeverityEngine._level(0.1) == "LOW"
