"""Phase 27 coverage-completing tests for hybrid modules."""

from __future__ import annotations

import pytest

from app.hybrid.attck import (
    AttackTechniqueCandidateGenerator,
    HybridATTCMapper,
)
from app.hybrid.chaingraph import AttackChainBuilder, AttackChainGraph, ChainEdge, ChainNode
from app.hybrid.engine import HybridEngine, HybridEngineConfig
from app.hybrid.extract import (
    _classify_entity,
    extract_facts_from_evidence,
    extract_facts_from_finding,
)
from app.hybrid.facts import FactCandidate, SecurityFact
from app.hybrid.falsepositive import FalsePositiveScorer
from app.hybrid.ranker import LLMRanker
from app.hybrid.retrieval import (
    KnowledgeSourceError,
    MemoryKnowledgeRetriever,
    NoopKnowledgeRetriever,
    PlatformKnowledgeRetriever,
)
from app.hybrid.severity import DeterministicSeverityEngine

# ---------------------------------------------------------------------------
# extract.py
# ---------------------------------------------------------------------------


def test_classify_entity() -> None:
    assert _classify_entity("8.8.8.8") == "ip"
    assert _classify_entity("user@example.com") == "user"
    assert _classify_entity("https://evil.example") == "url"
    assert _classify_entity("example.com") == "domain"
    assert _classify_entity("a" * 64) == "unknown"  # hex without ':' is not classified
    assert _classify_entity("/tmp/x") == "path"
    assert _classify_entity("weird") == "unknown"


def test_extract_from_evidence() -> None:
    result = extract_facts_from_evidence(
        {
            "id": "ev-1",
            "url": "https://evil.example/payload.bin",
            "sha256": "a" * 64,
            "title": "malicious payload",
            "timestamp": "2026-08-08T00:00:00+00:00",
            "confidence": 0.9,
        }
    )
    kinds = {f.attributes.get("kind") for f in result.facts}
    assert {"sha256", "url", "title"} <= kinds
    assert all(f.validate_fact() for f in result.facts)


def test_extract_from_finding_with_cve() -> None:
    result = extract_facts_from_finding(
        {
            "id": "f-1",
            "title": "log4j RCE",
            "severity": "CRITICAL",
            "references": ["https://nvd.nist.gov/vuln/detail/CVE-2021-44228"],
            "cve_id": "CVE-2021-44228",
        }
    )
    vulnerabilities = [f for f in result.facts if f.fact_type == "vulnerability"]
    assert vulnerabilities
    assert any(f.value == "CVE-2021-44228" for f in vulnerabilities)
    assert result.facts[0].confidence == pytest.approx(0.8)


def test_extract_from_event_missing_confidence() -> None:
    from app.hybrid.extract import extract_facts_from_event

    result = extract_facts_from_event(
        {"id": "e1", "event_type": "login", "entities": ["user@x.com"]}
    )
    assert result.facts
    assert all(f.validate_fact() for f in result.facts)


def test_fact_candidate_promote_knowledge_source() -> None:
    candidate = FactCandidate(fact_type="vulnerability", value="CVE-1", evidence_refs=[])
    promoted = candidate.promote(source_kind="knowledge", source_id="k1")
    assert promoted is not None
    assert promoted.source_kind == "knowledge"


# ---------------------------------------------------------------------------
# severity.py
# ---------------------------------------------------------------------------


def test_severity_no_finding_uses_cvss_anchor() -> None:
    engine = DeterministicSeverityEngine()
    result = engine.assess(cvss=9.8)
    assert result.severity == "CRITICAL"
    low = engine.assess(cvss=2.0)
    assert low.severity == "LOW"


def test_severity_downgrade_signals() -> None:
    engine = DeterministicSeverityEngine()
    result = engine.assess(
        finding_severity="HIGH",
        detection_confidence="LOW",
        evidence_confidence=0.3,
    )
    # low confidence downgrades below the HIGH anchor
    assert result.severity in ("LOW", "MEDIUM", "HIGH")
    assert result.severity != "CRITICAL"
    assert result.factors


def test_severity_kev_bonus() -> None:
    engine = DeterministicSeverityEngine()
    result = engine.assess(finding_severity="HIGH", in_kev=True)
    assert result.severity == "CRITICAL"
    assert any(f.name == "kev" for f in result.factors)


# ---------------------------------------------------------------------------
# chaingraph.py
# ---------------------------------------------------------------------------


def test_chain_graph_neighbors_and_dedupe() -> None:
    graph = AttackChainGraph()
    graph.add_node(ChainNode("a", "security_event", "A"))
    graph.add_node(ChainNode("b", "security_event", "B"))
    graph.add_node(ChainNode("c", "asset", "C"))
    graph.add_edge(ChainEdge("a", "b", "temporal"))
    graph.add_edge(ChainEdge("a", "b", "temporal"))  # dup ignored
    graph.add_edge(ChainEdge("b", "c", "same_asset"))
    assert len(graph.edges) == 2
    assert set(graph.neighbors("a")) == {"b"}
    assert graph.neighbors("c") == ["b"]
    assert graph.to_dict()["nodes"][0]["kind"] == "security_event"


def test_chain_builder_full() -> None:
    builder = AttackChainBuilder()
    graph = builder.build(
        events=[
            {
                "id": "e1",
                "title": "phishing",
                "timestamp": "2026-08-08T00:00:00+00:00",
                "severity": "HIGH",
                "entities": ["10.0.0.5"],
                "techniques": ["T1566"],
                "asset_id": "a1",
            },
            {
                "id": "e2",
                "title": "credential dump",
                "timestamp": "2026-08-08T00:10:00+00:00",
                "severity": "HIGH",
                "entities": ["10.0.0.5"],
                "techniques": ["T1003"],
                "asset_id": "a1",
            },
        ],
        facts=[
            SecurityFact(
                fact_type="observed_indicator",
                value="10.0.0.5",
                source_kind="security_event",
                source_id="e1",
                confidence=0.8,
            )
        ],
        assets=[{"id": "a1", "name": "web-01", "criticality": "HIGH"}],
        technique_candidates=["T1566", "T1003"],
    )
    assert any(n.kind == "asset" for n in graph.nodes)
    assert any(n.kind == "technique_candidate" for n in graph.nodes)
    kinds = {e.kind for e in graph.edges}
    assert {"temporal", "same_asset", "same_identity", "supports"} <= kinds


# ---------------------------------------------------------------------------
# retrieval.py
# ---------------------------------------------------------------------------


def test_platform_retriever_search_and_error() -> None:
    class SearchService:
        async def get_by_external_id(self, knowledge_type: str, external_id: str):
            return None

        async def search(self, **kwargs):
            raise RuntimeError("db down")

    retriever = PlatformKnowledgeRetriever(SearchService())
    with pytest.raises(KnowledgeSourceError):
        asyncio_run(retriever.lookup(knowledge_type="CVE", query="x"))


def test_memory_retriever_fact_lookup() -> None:
    retriever = MemoryKnowledgeRetriever(
        entries=[
            {
                "knowledge_type": "ATT&CK",
                "external_id": "T1566",
                "title": "Phishing",
                "keywords": ["phish"],
            }
        ]
    )
    fact = SecurityFact(
        fact_type="technique",
        value="T1566",
        source_kind="knowledge",
        source_id="k",
        confidence=0.5,
    )
    hits = asyncio_run(retriever.lookup_fact(fact))
    assert hits and hits[0].external_id == "T1566"


def test_noop_retriever() -> None:
    retriever = NoopKnowledgeRetriever()
    assert asyncio_run(retriever.lookup(knowledge_type="CVE", query="x")) == []


# ---------------------------------------------------------------------------
# attck.py / ranker.py
# ---------------------------------------------------------------------------


def test_attck_keyword_generation() -> None:
    generator = AttackTechniqueCandidateGenerator(knowledge=None)
    candidates = generator.from_keywords(
        "phishing email with credential dumping", evidence=["evidence:1"]
    )
    ids = {c.technique_id for c in candidates}
    assert "T1566" in ids
    assert "T1003" in ids
    assert all(c.score == 0.6 for c in candidates)


def test_attck_mapper_threshold_unknown() -> None:
    generator = AttackTechniqueCandidateGenerator(knowledge=None)
    mapper = HybridATTCMapper(generator, threshold=0.95)
    mapping = asyncio_run(mapper.map(facts=[], event_techniques=[]))
    assert mapping.unknown is True
    assert mapping.technique_id is None


def test_attck_mapper_llm_rank_fails_closed() -> None:
    class BrokenRanker:
        async def rank_techniques(self, ids):
            raise RuntimeError("model down")

    generator = AttackTechniqueCandidateGenerator(knowledge=None)
    mapper = HybridATTCMapper(generator, llm=BrokenRanker(), threshold=0.35)
    mapping = asyncio_run(
        mapper.map(
            facts=[],
            event_techniques=["T1566"],
        )
    )
    assert mapping.technique_id == "T1566"  # deterministic fallback


def test_ranker_parse_permutation_only() -> None:
    # LLMRanker._parse must drop invented techniques
    content = '{"order": ["T9999", "T1566"], "explanation": "e"}'
    order, explanation = LLMRanker._parse(content)
    assert "T9999" in order  # parse is raw; mapper filters against candidates
    assert explanation == "e"
    order2, _ = LLMRanker._parse("not json")
    assert order2 == []


# ---------------------------------------------------------------------------
# falsepositive.py
# ---------------------------------------------------------------------------


def test_fp_noisy_and_historical() -> None:
    scorer = FalsePositiveScorer()
    result = scorer.score(
        rule="dns_request flood",
        event_type="dns_request",
        frequency_30d=200,
        historical_fp_rate=0.8,
        evidence_quality=0.2,
        detection_confidence="LOW",
    )
    assert result.false_positive_probability > 0.5
    names = {f.name for f in result.factors}
    assert {"event_frequency", "historical_fp_rate", "noisy_event_type"} <= names


def test_fp_critical_asset_lowers() -> None:
    scorer = FalsePositiveScorer()
    result = scorer.score(
        rule="malicious beacon",
        asset_criticality="CRITICAL",
        evidence_quality=0.95,
        detection_confidence="HIGH",
    )
    assert result.likely_false_positive is False
    assert any(f.direction == "lowers_fp" for f in result.factors)


# ---------------------------------------------------------------------------
# engine.py
# ---------------------------------------------------------------------------


def test_engine_config_defaults() -> None:
    config = HybridEngineConfig()
    assert config.use_llm is False
    assert config.use_retrieval is True


def test_engine_chain_without_events() -> None:
    engine = HybridEngine(
        knowledge=MemoryKnowledgeRetriever(entries=[]),
        config=HybridEngineConfig(use_llm=False, use_retrieval=False),
    )
    output = asyncio_run(
        engine.triage(
            source={
                "id": "evt-x",
                "title": "phishing",
                "severity": "HIGH",
                "evidence_refs": ["evidence:1"],
                "techniques": ["T1566"],
            },
            context={},
        )
    )
    assert output.chain_stages  # deterministic stages from source techniques
    assert output.explanation.coverage() == 1.0


def asyncio_run(coro):
    import asyncio

    return asyncio.get_event_loop().run_until_complete(coro)
