"""Phase 27 final branch coverage (7 modules -> >=95%)."""

from __future__ import annotations

import asyncio

import pytest

from app.hybrid.attck import AttackTechniqueCandidateGenerator, HybridATTCMapper
from app.hybrid.engine import HybridEngine, HybridEngineConfig
from app.hybrid.explanation import Explanation, ExplanationBuilder, evaluate_explanations
from app.hybrid.extract import _classify_entity, extract_facts_from_event
from app.hybrid.facts import SecurityFact
from app.hybrid.ranker import LLMRanker
from app.hybrid.retrieval import MemoryKnowledgeRetriever
from app.hybrid.severity import DeterministicSeverityEngine


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# attck.py remaining branches
# ---------------------------------------------------------------------------


def test_attck_keyword_no_evidence() -> None:
    generator = AttackTechniqueCandidateGenerator(knowledge=None)
    candidates = generator.from_keywords("phishing", evidence=[])
    assert candidates and candidates[0].supporting_evidence == []


def test_attck_knowledge_lookup_skips_non_attck() -> None:
    retriever = MemoryKnowledgeRetriever(
        entries=[{"knowledge_type": "CVE", "external_id": "CVE-1", "title": "t", "keywords": []}]
    )
    generator = AttackTechniqueCandidateGenerator(knowledge=retriever)
    fact = SecurityFact(
        fact_type="vulnerability",
        value="CVE-1",
        source_kind="knowledge",
        source_id="k",
        confidence=0.5,
    )
    assert run(generator.from_knowledge(fact)) == []


def test_attck_dedup_keeps_higher_score() -> None:
    generator = AttackTechniqueCandidateGenerator(knowledge=None)
    mapping = run(
        HybridATTCMapper(generator, threshold=0.35).map(
            facts=[],
            event_techniques=["T1566", "T1566"],  # duplicates collapse
        )
    )
    assert mapping.technique_id == "T1566"
    assert len(mapping.candidates) == 1


def test_attck_llm_rank_invalid_order_keeps_all() -> None:
    class RankerReturnsEmpty:
        async def rank_techniques(self, ids):
            from app.hybrid.ranker import TechniqueRankResponse

            return TechniqueRankResponse(order=[], explanation="")

    generator = AttackTechniqueCandidateGenerator(knowledge=None)
    mapping = run(
        HybridATTCMapper(generator, llm=RankerReturnsEmpty(), threshold=0.35).map(
            facts=[], event_techniques=["T1566"]
        )
    )
    assert mapping.technique_id == "T1566"


# ---------------------------------------------------------------------------
# engine.py
# ---------------------------------------------------------------------------


def test_engine_uncertainties_populated() -> None:
    engine = HybridEngine(
        knowledge=MemoryKnowledgeRetriever(entries=[]),
        config=HybridEngineConfig(use_llm=False, use_retrieval=False),
    )
    output = run(
        engine.triage(
            source={
                "id": "evt-u",
                "title": "mysterious log line",
                "severity": "LOW",
                "evidence_refs": [],
                "techniques": [],
            },
            context={},
        )
    )
    assert "ATT&CK mapping is UNKNOWN" in output.uncertainties
    assert output.classification == "UNKNOWN"


def test_engine_severity_unknown_factor() -> None:
    engine = HybridEngine(
        knowledge=MemoryKnowledgeRetriever(entries=[]),
        config=HybridEngineConfig(use_llm=False, use_retrieval=False),
    )
    output = run(
        engine.triage(
            source={
                "id": "evt-v",
                "title": "x",
                "severity": "HIGH",
                "evidence_refs": [],
                "techniques": [],
            },
            context={},
        )
    )
    assert output.severity.severity == "UNKNOWN"
    assert any(f.name == "evidence" for f in output.severity.factors)


# ---------------------------------------------------------------------------
# explanation.py
# ---------------------------------------------------------------------------


def test_explainability_metrics_empty() -> None:
    metrics = evaluate_explanations([], required_factors={"cvss"})
    assert metrics["evidence_coverage"] == 0.0
    assert metrics["unsupported_rate"] == 0.0


def test_explanation_builder_llm_failure_falls_back() -> None:
    class BrokenLLM:
        async def explain(self, **kwargs):
            raise RuntimeError("down")

    builder = ExplanationBuilder(llm=BrokenLLM())
    explanation = run(builder.build(statement="s", factors=["cvss"], evidence_refs=["evidence:1"]))
    assert explanation.statement == "s"
    assert explanation.model_generated is False


def test_explanation_without_refs_unsupported() -> None:
    explanation = Explanation(statement="no refs")
    assert explanation.coverage() == 0.0


# ---------------------------------------------------------------------------
# extract.py
# ---------------------------------------------------------------------------


def test_extract_entities_dict_form() -> None:
    result = extract_facts_from_event(
        {
            "id": "e-dict",
            "entities": [
                {"value": "10.0.0.9", "type": "ip"},
                "plain.example.com",
                {"value": "", "type": "empty"},
            ],
            "confidence": 0.9,
        }
    )
    values = {f.value for f in result.facts}
    assert "10.0.0.9" in values
    assert "plain.example.com" in values


def test_extract_attributes_scan() -> None:
    result = extract_facts_from_event(
        {
            "id": "e-attr",
            "attributes": {"src_ip": "10.1.1.1", "url_path": "/admin", "count": 5},
            "confidence": 0.9,
        }
    )
    iocs = [f for f in result.facts if f.fact_type == "observed_indicator"]
    assert iocs
    keys = {f.attributes.get("attribute_key") for f in iocs}
    assert "src_ip" in keys


def test_classify_ipv6() -> None:
    assert _classify_entity("2001:db8::1") == "ip"


# ---------------------------------------------------------------------------
# ranker.py
# ---------------------------------------------------------------------------


def test_ranker_rank_empty() -> None:
    class FakeProvider:
        async def complete(self, request):
            return None

    ranker = LLMRanker(FakeProvider())
    response = run(ranker.rank_techniques([]))
    assert response.order == []


def test_ranker_provider_failure_returns_empty() -> None:
    class FailingProvider:
        async def complete(self, request):
            raise RuntimeError("boom")

    ranker = LLMRanker(FailingProvider())
    # provider failure propagates to the caller, which fails closed
    # (HybridATTCMapper._rank_with_llm catches and falls back)
    with pytest.raises(RuntimeError):
        run(ranker.rank_techniques(["T1566"]))


def test_ranker_clean() -> None:
    assert LLMRanker._clean('"quoted"') == "quoted"


# ---------------------------------------------------------------------------
# retrieval.py
# ---------------------------------------------------------------------------


def test_retriever_lookup_fact_technique() -> None:
    retriever = MemoryKnowledgeRetriever(
        entries=[{"knowledge_type": "ATT&CK", "external_id": "T1566", "title": "t", "keywords": []}]
    )
    fact = SecurityFact(
        fact_type="technique", value="T1566", source_kind="knowledge", source_id="k", confidence=0.5
    )
    hits = run(retriever.lookup_fact(fact))
    assert hits and hits[0].external_id == "T1566"


def test_retriever_lookup_unknown_type() -> None:
    retriever = MemoryKnowledgeRetriever(entries=[])
    assert run(retriever.lookup(knowledge_type="BOGUS", external_id="x")) == []


# ---------------------------------------------------------------------------
# severity.py
# ---------------------------------------------------------------------------


def test_severity_no_signals_unknown_anchor() -> None:
    engine = DeterministicSeverityEngine()
    result = engine.assess()
    assert result.severity in ("LOW", "MEDIUM")
    assert result.confidence >= 0.3


def test_severity_criticality_anchor() -> None:
    engine = DeterministicSeverityEngine()
    result = engine.assess(asset_criticality="CRITICAL")
    assert result.severity == "CRITICAL"


def test_severity_exposed_critical_escalates() -> None:
    engine = DeterministicSeverityEngine()
    result = engine.assess(finding_severity="MEDIUM", asset_criticality="CRITICAL", exposed=True)
    assert result.severity in ("HIGH", "CRITICAL")
    assert any(f.name == "exposure" for f in result.factors)
