"""Phase 28.3 -- Acquisition status-model consistency.

Phase 28.2 introduced durable queue states (QUEUED / CANCEL_REQUESTED /
CANCELLED) as string literals. Phase 28.3 promotes them to first-class
members of AcquisitionStatus so no status comparison depends on untyped
string constants.
"""

from __future__ import annotations

from app.acquisition.models import AcquisitionStatus
from app.acquisition.worker_path import TERMINAL


def test_all_durable_states_are_enum_members() -> None:
    for status in (
        "PENDING",
        "QUEUED",
        "RUNNING",
        "CANCEL_REQUESTED",
        "COMPLETE",
        "PARTIAL",
        "BLOCKED",
        "FAILED",
        "CANCELLED",
    ):
        assert status in AcquisitionStatus.__members__.values(), status


def test_cancelled_is_an_enum_member() -> None:
    assert AcquisitionStatus.CANCELLED == "CANCELLED"
    assert AcquisitionStatus.CANCELLED.value == "CANCELLED"


def test_terminal_set_matches_enum() -> None:
    for state in TERMINAL:
        assert AcquisitionStatus(state) is not None, state
    # every enum member that is terminal in the worker path is covered
    assert AcquisitionStatus.COMPLETE.value in TERMINAL
    assert AcquisitionStatus.CANCELLED.value in TERMINAL
    assert AcquisitionStatus.BLOCKED.value in TERMINAL
    assert AcquisitionStatus.FAILED.value in TERMINAL


def test_create_emits_queued() -> None:
    """create() durably emits the enum member QUEUED (not a loose string)."""
    import asyncio

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.acquisition.models_db import AcquisitionRun
    from app.acquisition.service import AcquisitionService
    from app.database import Base
    from app.evidence.service import EvidenceService

    async def _check() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
        async with SessionFactory() as session:
            evidence = EvidenceService(session, publisher=None, storage_directory="outputs")  # type: ignore[arg-type]
            service = AcquisitionService(session, evidence)
            run, created = await service.create(goal="g", url="http://example.com/static")
            assert created is True
            assert run.status == AcquisitionStatus.QUEUED.value
            row = (
                await session.execute(select(AcquisitionRun).where(AcquisitionRun.id == run.id))
            ).scalar_one()
            assert row.status == "QUEUED"
        await engine.dispose()

    asyncio.run(_check())
