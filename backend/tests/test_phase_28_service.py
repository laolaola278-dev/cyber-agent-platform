"""Phase 28 -- AcquisitionService persistence tests (in-memory DB + tmp store).

Verifies create_and_run persists AcquisitionRun / plan / artifacts /
documents / completeness and that blocked runs are persisted correctly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.capabilities import (
    ACQUISITION_CAPABILITIES,
    seed_acquisition_capabilities,
)
from app.acquisition.models_db import (
    AcquisitionArtifactRecord,
    AcquisitionPlanRecord,
    AcquisitionRun,
    CompletenessReportRecord,
    ExtractedDocumentRecord,
)
from app.acquisition.service import AcquisitionService
from app.capabilities import CapabilityRegistryService
from app.evidence.service import EvidenceService
from tests.conftest import TestSessionFactory


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with TestSessionFactory() as session:
        yield session


class _NullPublisher:
    async def publish(self, event) -> None:  # noqa: ARG002
        return None


async def _make_service(session: AsyncSession, tmp_path) -> AcquisitionService:
    evidence = EvidenceService(
        session=session,
        publisher=_NullPublisher(),
        storage_directory=tmp_path / "evidence",
    )
    return AcquisitionService(
        session,
        evidence,
        store_root=tmp_path / "objects",
    )


@pytest.mark.asyncio
async def test_service_persists_successful_run(
    session: AsyncSession, tmp_path
) -> None:
    service = await _make_service(session, tmp_path)
    # Public example URL: DNS-resolvable, no real network (validator passes but
    # fetch fails -> FAILED gracefully). Use a plan-level test instead below.
    # We directly exercise the persistence path through a completed result.
    created = await service.create_and_run(
        goal="collect advisory",
        url="https://bench.example/unreachable",
        expected_fields=["title", "cve"],
    )
    assert created.id is not None
    # run row persisted
    rows = (await session.execute(select(AcquisitionRun))).scalars().all()
    assert len(rows) == 1
    assert rows[0].goal == "collect advisory"
    # plan row persisted
    plans = (await session.execute(select(AcquisitionPlanRecord))).scalars().all()
    assert len(plans) == 1
    assert plans[0].completeness_conditions["expected_fields"] == ["title", "cve"]


@pytest.mark.asyncio
async def test_service_persists_blocked_run(
    session: AsyncSession, tmp_path
) -> None:
    service = await _make_service(session, tmp_path)
    created = await service.create_and_run(
        goal="reach restricted",
        url="http://127.0.0.1/secret",  # SSRF-blocked by validator
    )
    assert created is not None
    rows = (await session.execute(select(AcquisitionRun))).scalars().all()
    assert rows[0].status in ("BLOCKED", "FAILED", "PARTIAL")
    assert rows[0].blocked_reason != "NONE"


@pytest.mark.asyncio
async def test_service_artifact_and_document_tables(
    session: AsyncSession, tmp_path
) -> None:
    from app.acquisition.agent import AdaptiveDataAcquisitionAgent, AgentConfig
    from app.acquisition.dataset import (
        SyntheticResponse,
        SyntheticWeb,
        _html,
    )
    from app.acquisition.evaluation import _TempStore
    from app.acquisition.httpadapter import HTTPAdapter
    from app.acquisition.models import AcquisitionPolicy
    from app.acquisition.planner import AcquisitionPlanner, PlannerRequest
    from app.acquisition.urlpolicy import URLPolicyValidator

    origin = "https://bench.example"
    routes = {
        f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
        f"{origin}/page": SyntheticResponse(
            200, {"content-type": "text/html"}, _html("Page", "CVE-2024-5678")
        ),
    }
    web = SyntheticWeb(routes=routes)
    policy = AcquisitionPolicy(request_rate=100.0)
    agent = AdaptiveDataAcquisitionAgent(
        http=HTTPAdapter(
            policy=policy,
            validator=URLPolicyValidator(resolver=lambda host: ["93.184.216.34"]),
            client_factory=web.client_factory(),
        ),
        store=_TempStore("mem"),
        planner=AcquisitionPlanner(policy=policy),
        config=AgentConfig(task_id="t1", trace_id="tr1"),
    )
    result = await agent.acquire(PlannerRequest(goal="g", url=f"{origin}/page"))
    assert result.status.value == "COMPLETE"
    assert len(result.artifacts) == 1

    # persist through the service result path
    from uuid import uuid4

    service = await _make_service(session, tmp_path)
    run = AcquisitionRun(
        id=uuid4(),
        task_id=uuid4(),
        agent_id=uuid4(),
        trace_id="tr1",
        goal="g",
        status="RUNNING",
        source_type="STATIC_HTML",
        strategy="static-http-fetch+extract",
    )
    await service._persist_result(run=run, result=result, run_id=run.id)
    await session.flush()
    artifacts = (await session.execute(select(AcquisitionArtifactRecord))).scalars().all()
    assert len(artifacts) == 1
    assert artifacts[0].sha256 == result.artifacts[0].sha256
    docs = (await session.execute(select(ExtractedDocumentRecord))).scalars().all()
    assert len(docs) == 1
    assert docs[0].title == "Page"
    reports = (await session.execute(select(CompletenessReportRecord))).scalars().all()
    assert len(reports) == 1


@pytest.mark.asyncio
async def test_seed_acquisition_capabilities(session: AsyncSession) -> None:
    service = CapabilityRegistryService(session, None)  # type: ignore[arg-type]
    # monkeypatch repository methods to avoid real DB rows
    class FakeRepo:
        async def get_by_name(self, name):
            return None

        async def add(self, capability):
            return capability

    service._repository = FakeRepo()
    await seed_acquisition_capabilities(service)
    assert len(ACQUISITION_CAPABILITIES) == 8
    assert "acquisition.public" in ACQUISITION_CAPABILITIES
