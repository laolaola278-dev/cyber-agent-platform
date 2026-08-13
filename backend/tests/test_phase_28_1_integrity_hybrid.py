"""Phase 28.1 -- Evidence integrity, Hybrid E2E and safety regression.

Evidence integrity: the object-store key, Evidence.sha256 and the artifact
SHA-256 must be identical, and tampering with a stored object must be
detected.

Hybrid E2E: a real lab acquisition -> Evidence -> FactCandidate ->
KnowledgeRetriever -> Hybrid Engine explanation that cites the REAL evidence.

Safety regression: the same hard gates hold on the real acquisition path.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.agent import AdaptiveDataAcquisitionAgent, AgentConfig
from app.acquisition.checkpoint import AcquisitionCheckpoint
from app.acquisition.candidates import extract_candidates
from app.acquisition.documentadapter import DocumentAdapter
from app.acquisition.httpadapter import HTTPAdapter
from app.acquisition.planner import AcquisitionPlanner, PlannerRequest
from app.acquisition.service import AcquisitionService
from app.acquisition.store import LocalFilesystemEvidenceStore
from app.evidence.service import EvidenceService
from app.models import Evidence
from tests.acquisition_lab import (
    AcquisitionLabServer,
    lab_policy,
    lab_url_validator,
)
from tests.conftest import TestSessionFactory


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    async with TestSessionFactory() as session:
        yield session


@pytest.fixture(scope="module")
def lab() -> AcquisitionLabServer:
    server = AcquisitionLabServer().start()
    yield server
    server.stop()


async def _agent(tmp_path: Path) -> AdaptiveDataAcquisitionAgent:
    return AdaptiveDataAcquisitionAgent(
        http=HTTPAdapter(policy=lab_policy(), validator=lab_url_validator()),
        store=LocalFilesystemEvidenceStore(tmp_path / "objects"),
        planner=AcquisitionPlanner(policy=lab_policy()),
        document=DocumentAdapter(),
        config=AgentConfig(task_id="t", trace_id="tr"),
    )


# -- 1. evidence integrity: object key == Evidence.sha256 == artifact sha -------

async def test_evidence_integrity_three_way_hash(session: AsyncSession, tmp_path, lab) -> None:
    evidence_svc = EvidenceService(session, publisher=None, storage_directory=tmp_path)  # type: ignore[arg-type]
    service = AcquisitionService(
        session,
        evidence_svc,
        store_root=tmp_path / "objects",
        policy=lab_policy(),
        validator=lab_url_validator(),
    )
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    out = await service.run_agent_operation(run, AcquisitionCheckpoint(run_id=str(run.id)))
    assert out.status == "COMPLETE"
    assert out.evidence_ids

    rows = (await session.execute(select(Evidence))).scalars().all()
    assert rows
    store = LocalFilesystemEvidenceStore(tmp_path / "objects")
    for evidence in rows:
        # object key == Evidence.sha256 (content-addressed)
        assert evidence.object_storage_path == evidence.sha256
        # re-reading the object yields the same hash (integrity check)
        blob = await store.get(evidence.object_storage_path)
        assert hashlib.sha256(blob).hexdigest() == evidence.sha256


async def test_evidence_integrity_tamper_detection(session: AsyncSession, tmp_path, lab) -> None:
    evidence_svc = EvidenceService(session, publisher=None, storage_directory=tmp_path)  # type: ignore[arg-type]
    service = AcquisitionService(
        session,
        evidence_svc,
        store_root=tmp_path / "objects",
        policy=lab_policy(),
        validator=lab_url_validator(),
    )
    run, _ = await service.create(goal="g", url=f"{lab.origin}/static")
    await session.flush()
    await service.run_agent_operation(run, AcquisitionCheckpoint(run_id=str(run.id)))

    rows = (await session.execute(select(Evidence))).scalars().all()
    store = LocalFilesystemEvidenceStore(tmp_path / "objects")
    evidence = rows[0]
    # tamper with the stored object BY OVERWRITING the content-addressed file
    # (a real attacker would not go through the store API)
    target = store._object_path(evidence.object_storage_path)  # type: ignore[attr-defined]
    target.write_bytes(b"attacker modified this artifact")
    blob = await store.get(evidence.object_storage_path)
    # integrity check MUST fail
    assert hashlib.sha256(blob).hexdigest() != evidence.sha256


# -- 2. hybrid E2E: acquisition -> evidence -> candidates -> knowledge -> hybrid -

async def test_hybrid_e2e_explanation_cites_real_evidence(
    session: AsyncSession, tmp_path, lab
) -> None:
    from app.hybrid.attck import HybridATTCMapper
    from app.hybrid.engine import HybridEngine, HybridEngineConfig
    from app.hybrid.facts import SecurityFact
    from app.hybrid.retrieval import MemoryKnowledgeRetriever

    # 1) real acquisition of the dynamic advisory page (with evidence sink)
    from app.acquisition.service import _EvidenceSink

    evidence_svc = EvidenceService(session, publisher=None, storage_directory=tmp_path)  # type: ignore[arg-type]
    agent = AdaptiveDataAcquisitionAgent(
        http=HTTPAdapter(policy=lab_policy(), validator=lab_url_validator()),
        store=LocalFilesystemEvidenceStore(tmp_path / "objects"),
        planner=AcquisitionPlanner(policy=lab_policy()),
        document=DocumentAdapter(),
        evidence_sink=_EvidenceSink(evidence_svc),  # type: ignore[arg-type]
        config=AgentConfig(task_id="t", trace_id="tr"),
    )
    result = await agent.acquire(
        PlannerRequest(
            goal="collect advisory",
            url=f"{lab.origin}/static",
            expected_fields=["title"],
        )
    )
    assert result.status.value == "COMPLETE"
    assert result.evidence_ids, "evidence must exist end-to-end"
    evidence_ref = result.evidence_ids[0]
    assert result.documents and result.documents[0].text

    # 2) FactCandidates from the extracted text (downstream validation only)
    bundle = extract_candidates(
        result.documents[0].text,
        evidence_id=evidence_ref,
        source_url=result.documents[0].source_url,
        title=result.documents[0].title,
    )
    assert bundle.facts, "CVE should be extracted from the advisory"

    # 3) build SecurityFacts for the hybrid engine
    facts = [
        SecurityFact(
            fact_type=candidate.fact_type,
            value=candidate.value,
            source_kind="evidence",
            source_id=evidence_ref,
            evidence_ref=f"evidence:{evidence_ref}",
            confidence=0.9,
        )
        for candidate in bundle.facts
    ]
    assert facts

    # 4) knowledge retriever + hybrid engine
    knowledge = MemoryKnowledgeRetriever(
        entries=[
            {
                "knowledge_type": "ATT&CK",
                "external_id": "T1059",
                "title": "Command and Scripting Interpreter",
                "keywords": ["command", "script", "execution"],
            }
        ]
    )
    engine = HybridEngine(
        knowledge=knowledge,
        llm_ranker=None,
        config=HybridEngineConfig(use_llm=False, use_retrieval=True),
    )
    output = await engine.triage(
        source={
            "id": "src-1",
            "title": result.documents[0].title,
            "severity": "HIGH",
            "evidence_refs": [evidence_ref],
        },
        context={"cvss": 9.0, "in_kev": True, "asset_criticality": "HIGH"},
        events=[{"id": "evt-1", "title": result.documents[0].title, "evidence_refs": [evidence_ref]}],
    )
    # the explanation must reference the REAL evidence id
    assert output.classification in ("CONFIRMED", "LIKELY", "POSSIBLE", "MALICIOUS")
    all_refs = [
        ref
        for claim in output.grounded_claims
        for ref in (claim.evidence_refs + claim.matched_refs)
    ] + [fact.evidence_ref or "" for fact in output.facts]
    assert any(evidence_ref in ref for ref in all_refs), all_refs
    assert output.explanation is not None
    # grounded explanation (cites the evidence we actually collected)
    assert output.grounded_claims


# -- 3. safety regression on the real path ----------------------------------------

async def test_safety_regression_restricted_pages(
    session: AsyncSession, tmp_path, lab
) -> None:
    evidence_svc = EvidenceService(session, publisher=None, storage_directory=tmp_path)  # type: ignore[arg-type]
    service = AcquisitionService(
        session,
        evidence_svc,
        store_root=tmp_path / "objects",
        policy=lab_policy(),
        validator=lab_url_validator(),
    )
    for path, expected_reason in (
        ("/login", "LOGIN_PAGE"),
        ("/captcha", "CAPTCHA"),
        ("/paywall", "PAYWALL"),
    ):
        run, _ = await service.create(goal="g", url=f"{lab.origin}{path}")
        await session.flush()
        out = await service.run_agent_operation(
            run, AcquisitionCheckpoint(run_id=str(run.id))
        )
        assert out.status == "BLOCKED", path
        assert out.blocked_reason == expected_reason, path


async def test_safety_regression_robots_disallowed(
    session: AsyncSession, tmp_path, lab
) -> None:
    evidence_svc = EvidenceService(session, publisher=None, storage_directory=tmp_path)  # type: ignore[arg-type]
    service = AcquisitionService(
        session,
        evidence_svc,
        store_root=tmp_path / "objects",
        policy=lab_policy(),
        validator=lab_url_validator(),
    )
    run, _ = await service.create(goal="g", url=f"{lab.origin}/private")
    await session.flush()
    out = await service.run_agent_operation(run, AcquisitionCheckpoint(run_id=str(run.id)))
    assert out.status == "BLOCKED"
    assert out.blocked_reason == "ROBOTS_DISALLOWED"


async def test_safety_regression_scope_never_expands(session: AsyncSession, tmp_path, lab) -> None:
    agent = await _agent(tmp_path)
    # pagination next links stay same-origin; an off-origin target is blocked
    result = await agent.acquire(
        PlannerRequest(goal="g", url=f"{lab.origin}/pagination?page=1")
    )
    for url in result.visited_urls:
        assert url.startswith(lab.origin), f"scope expanded: {url}"
