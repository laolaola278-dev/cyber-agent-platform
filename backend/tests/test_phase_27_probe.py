"""Probe remaining uncovered lines in attck/ranker/retrieval."""

from __future__ import annotations

import asyncio

import pytest

from app.hybrid.attck import AttackTechniqueCandidateGenerator
from app.hybrid.facts import SecurityFact
from app.hybrid.ranker import LLMRanker
from app.hybrid.retrieval import MemoryKnowledgeRetriever


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_attck_invalid_technique_format_skipped() -> None:
    generator = AttackTechniqueCandidateGenerator(knowledge=None)
    candidates = generator.from_event_techniques(["not-a-technique", "T9000"])
    assert candidates == []  # invalid format + unknown catalog id both skipped


def test_attck_knowledge_dedup_over_event() -> None:
    # knowledge candidate with score 1.0 (via hit.score 1.0 * 0.9 = 0.9) must
    # not override an event candidate (1.0); this covers the dedup branch.
    retriever = MemoryKnowledgeRetriever(
        entries=[{"knowledge_type": "ATT&CK", "external_id": "T1566", "title": "t", "keywords": []}]
    )
    generator = AttackTechniqueCandidateGenerator(knowledge=retriever)
    fact = SecurityFact(
        fact_type="observed_indicator",
        value="phishing",
        source_kind="security_event",
        source_id="e",
        confidence=0.8,
        evidence_ref="evidence:1",
    )
    candidates = run(generator.generate(facts=[fact], event_techniques=["T1566"]))
    assert candidates[0].technique_id == "T1566"
    assert candidates[0].score == 1.0  # event declared wins over knowledge


def test_ranker_throttle_positive() -> None:
    class FakeProvider:
        def __init__(self):
            self.calls = 0

        async def complete(self, request):
            self.calls += 1
            from app.agent.contracts import ModelResponse, TokenUsage

            return ModelResponse(
                content='{"order": ["T1566"], "explanation": "e"}',
                structured={"order": ["T1566"]},
                usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                latency_ms=1,
            )

    provider = FakeProvider()
    ranker = LLMRanker(provider, throttle_seconds=0.01)
    response = run(ranker.rank_techniques(["T1566"]))
    assert response.order == ["T1566"]
    assert provider.calls == 1


def test_ranker_parse_malformed_json_with_braces() -> None:
    # JSON with braces but invalid structure -> except branch
    order, explanation = LLMRanker._parse('{"order": ["T1566", ')
    assert order == []
    assert explanation == ""


def test_retriever_platform_external_id_miss() -> None:
    from app.hybrid.retrieval import PlatformKnowledgeRetriever

    class MissService:
        async def get_by_external_id(self, knowledge_type, external_id):
            return None

        async def search(self, **kwargs):
            return []

    retriever = PlatformKnowledgeRetriever(MissService())
    assert run(retriever.lookup(knowledge_type="CVE", external_id="CVE-0")) == []


def test_retriever_platform_search_page_empty() -> None:
    from app.hybrid.retrieval import PlatformKnowledgeRetriever

    class EmptyPageService:
        async def get_by_external_id(self, knowledge_type, external_id):
            return None

        class _Page:
            items = []

        async def search(self, **kwargs):
            return self._Page()

    retriever = PlatformKnowledgeRetriever(EmptyPageService())
    assert run(retriever.lookup(knowledge_type="CVE", query="x")) == []


# -- Platform retriever full-branch coverage --------------------------------


def test_platform_retriever_external_id_miss() -> None:
    from app.hybrid.retrieval import PlatformKnowledgeRetriever

    class Service:
        async def get_by_external_id(self, knowledge_type, external_id):
            return None

        async def search(self, **kwargs):
            return []

    retriever = PlatformKnowledgeRetriever(Service())
    # external_id miss -> line 77 return []
    assert run(retriever.lookup(knowledge_type="CVE", external_id="CVE-MISS")) == []


def test_platform_retriever_external_id_hit() -> None:
    from app.hybrid.retrieval import PlatformKnowledgeRetriever

    class Service:
        async def get_by_external_id(self, knowledge_type, external_id):
            return {
                "knowledge_type": "CVE",
                "external_id": "CVE-HIT",
                "title": "t",
                "description": "d",
                "attributes": {},
            }

        async def search(self, **kwargs):
            return []

    retriever = PlatformKnowledgeRetriever(Service())
    hits = run(retriever.lookup(knowledge_type="CVE", external_id="CVE-HIT"))
    assert hits and hits[0].score == 1.0


def test_platform_retriever_exception_paths() -> None:
    from app.hybrid.retrieval import KnowledgeSourceError, PlatformKnowledgeRetriever

    class Raising:
        async def get_by_external_id(self, knowledge_type, external_id):
            raise KnowledgeSourceError("domain error")

        async def search(self, **kwargs):
            raise RuntimeError("db down")

    retriever = PlatformKnowledgeRetriever(Raising())
    # KnowledgeSourceError propagates unchanged
    with pytest.raises(KnowledgeSourceError):
        run(retriever.lookup(knowledge_type="CVE", external_id="CVE-X"))
    # generic Exception wrapped as KnowledgeSourceError
    with pytest.raises(KnowledgeSourceError):
        run(retriever.lookup(knowledge_type="CVE", query="q"))


def test_platform_retriever_bogus_type_and_noargs() -> None:
    from app.hybrid.retrieval import PlatformKnowledgeRetriever

    class Service:
        async def get_by_external_id(self, knowledge_type, external_id):
            return None

        async def search(self, **kwargs):
            return []

    retriever = PlatformKnowledgeRetriever(Service())
    assert run(retriever.lookup(knowledge_type="BOGUS")) == []  # 77
    assert run(retriever.lookup(knowledge_type="CVE")) == []  # 97


def test_platform_retriever_lookup_fact_technique_ioc() -> None:
    from app.hybrid.retrieval import PlatformKnowledgeRetriever

    class Service:
        async def get_by_external_id(self, knowledge_type, external_id):
            if knowledge_type == "ATT&CK" and external_id == "T1566":
                return {
                    "knowledge_type": "ATT&CK",
                    "external_id": "T1566",
                    "title": "t",
                    "attributes": {},
                }
            return None

        async def search(self, **kwargs):
            return []

    retriever = PlatformKnowledgeRetriever(Service())
    technique_fact = SecurityFact(
        fact_type="technique", value="T1566", source_kind="knowledge", source_id="k", confidence=0.5
    )
    hits = run(retriever.lookup_fact(technique_fact))
    assert hits and hits[0].external_id == "T1566"
    # IOC branch (not vulnerability/technique) -> lookup with query
    ioc_fact = SecurityFact(
        fact_type="observed_indicator", value="8.8.8.8", source_kind="security_event",
        source_id="e", confidence=0.5,
    )
    assert run(retriever.lookup_fact(ioc_fact)) == []
