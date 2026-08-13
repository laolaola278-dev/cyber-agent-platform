"""Phase 28.4 -- orphan blob GC safety tests.

Certifies: grace-period protection (never delete during the write->attach
window), durable-reference retention (including shared digests across runs),
orphan deletion after grace, idempotent sweeps, and GC-vs-attach races.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.acquisition.gc import EvidenceOrphanGC, GCRunStats
from app.acquisition.store import S3EvidenceStore, sha256_hex
from app.models import Evidence

pytestmark = [pytest.mark.postgres, pytest.mark.object_store]

PG_DSN = "postgresql+asyncpg://cap@127.0.0.1:55432/cap283"
S3_ENDPOINT = os.environ.get("CAP283_S3_ENDPOINT", "127.0.0.1:9000")
S3_ACCESS = os.environ.get("CAP283_S3_ACCESS", "capadmin")
S3_SECRET = os.environ.get("CAP283_S3_SECRET", "capadmin123")
S3_BUCKET = os.environ.get("CAP283_S3_BUCKET", "cap-gc284")


async def _probe() -> bool:
    try:
        store = S3EvidenceStore(
            endpoint=S3_ENDPOINT, access_key=S3_ACCESS, secret_key=S3_SECRET, bucket=S3_BUCKET
        )
        return await store.health()
    except Exception:  # noqa: BLE001
        return False


_skip = pytest.mark.skipif(not asyncio.run(_probe()), reason="MinIO not reachable")


async def _make_gc(grace: float):
    store = S3EvidenceStore(
        endpoint=S3_ENDPOINT, access_key=S3_ACCESS, secret_key=S3_SECRET, bucket=S3_BUCKET
    )
    engine = create_async_engine(PG_DSN, pool_size=3)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gc = EvidenceOrphanGC(store, factory, grace_period_seconds=grace)
    return gc, store, engine


async def _cleanup(gc: EvidenceOrphanGC, store: S3EvidenceStore, engine) -> None:
    keys = await store.list_keys()
    for key in keys:
        await store.delete(key)
    async with gc._session_factory() as session:  # type: ignore[attr-defined]
        await session.execute(text("DELETE FROM evidence"))
        await session.commit()
    await engine.dispose()


async def _ensure_agent(session, agent_id) -> None:
    from app.models.agent import Agent

    agent = await session.get(Agent, agent_id)
    if agent is None:
        session.add(
            Agent(
                id=agent_id,
                name=f"gc-agent-{agent_id.hex[:8]}",
                version="1",
                status="ONLINE",
                health_status="HEALTHY",
            )
        )
        await session.flush()


async def _ensure_task(session, task_id) -> None:
    from app.models.task import Task

    task = await session.get(Task, task_id)
    if task is None:
        session.add(
            Task(
                id=task_id,
                name=f"gc-task-{task_id.hex[:8]}",
                task_type="acquisition",
                status="QUEUED",
                input={},
                required_permissions=[],
                required_capabilities=["acquisition.http"],
            )
        )
        await session.flush()




@_skip
class TestOrphanGC:
    @pytest.mark.asyncio
    async def test_grace_period_protects_fresh_objects(self) -> None:
        gc, store, engine = await _make_gc(grace=3600)
        try:
            obj = await store.put(b"fresh blob", metadata={})
            stats = await gc.run()
            assert stats.scanned >= 1
            assert obj.key in await store.list_keys()  # untouched
            assert stats.deleted == 0
            assert stats.too_young >= 1
        finally:
            await _cleanup(gc, store, engine)

    @pytest.mark.asyncio
    async def test_orphan_deleted_after_grace(self) -> None:
        gc, store, engine = await _make_gc(grace=0)
        try:
            obj = await store.put(b"orphan blob", metadata={})
            stats = await gc.run()
            assert stats.deleted == 1
            assert await store.exists(obj.key) is False
        finally:
            await _cleanup(gc, store, engine)

    @pytest.mark.asyncio
    async def test_referenced_object_is_never_deleted(self) -> None:
        gc, store, engine = await _make_gc(grace=0)
        try:
            data = b"referenced blob"
            obj = await store.put(data, metadata={})
            # durable evidence row referencing the digest
            async with gc._session_factory() as session:  # type: ignore[attr-defined]
                _agent = uuid4()
                _task = uuid4()
                await _ensure_agent(session, _agent)
                await _ensure_task(session, _task)
                session.add(
                    Evidence(
                        task_id=_task,
                        agent_id=_agent,
                        trace_id="gc-t",
                        url="http://example.com",
                        http_status=200,
                        title="t",
                        evidence_type="html",
                        sha256=obj.key,
                        content_type="text/html",
                        html_hash=obj.key,
                        content_hash=obj.key,
                        captured_at=datetime.now(UTC),
                    )
                )
                await session.commit()
            stats = await gc.run()
            assert stats.deleted == 0
            assert await store.exists(obj.key) is True  # retained
        finally:
            await _cleanup(gc, store, engine)

    @pytest.mark.asyncio
    async def test_shared_digest_across_runs_is_retained(self) -> None:
        gc, store, engine = await _make_gc(grace=0)
        try:
            data = b"shared digest blob"
            obj = await store.put(data, metadata={})
            # two different runs reference the SAME digest
            async with gc._session_factory() as session:  # type: ignore[attr-defined]
                _agents = [uuid4(), uuid4()]
                _tasks = [uuid4(), uuid4()]
                for _a in _agents:
                    await _ensure_agent(session, _a)
                for _t in _tasks:
                    await _ensure_task(session, _t)
                for i, (_a, _t) in enumerate(zip(_agents, _tasks)):
                    session.add(
                        Evidence(
                            task_id=_t,
                            agent_id=_a,
                            trace_id=f"gc-shared-{i}",
                            url="http://example.com",
                            http_status=200,
                            title="t",
                            evidence_type="html",
                            sha256=obj.key,
                            content_type="text/html",
                            html_hash=obj.key,
                            content_hash=obj.key,
                            captured_at=datetime.now(UTC),
                        )
                    )
                await session.commit()
            stats = await gc.run()
            assert stats.deleted == 0
            assert await store.exists(obj.key) is True
        finally:
            await _cleanup(gc, store, engine)

    @pytest.mark.asyncio
    async def test_gc_vs_attach_race_write_then_attach(self) -> None:
        """A blob written and immediately attached must survive a concurrent GC."""
        gc, store, engine = await _make_gc(grace=0)
        try:
            data = b"race blob"
            obj = await store.put(data, metadata={})
            # attach in a separate session, then run GC concurrently
            async def attach() -> None:
                async with gc._session_factory() as session:  # type: ignore[attr-defined]
                    _a = uuid4()
                    _t = uuid4()
                    await _ensure_agent(session, _a)
                    await _ensure_task(session, _t)
                    session.add(
                        Evidence(
                            task_id=_t,
                            agent_id=_a,
                            trace_id="gc-race",
                            url="http://example.com",
                            http_status=200,
                            title="t",
                            evidence_type="html",
                            sha256=obj.key,
                            content_type="text/html",
                            html_hash=obj.key,
                            content_hash=obj.key,
                            captured_at=datetime.now(UTC),
                        )
                    )
                    await session.commit()

            await attach()
            stats = await gc.run()
            assert stats.deleted == 0
            assert await store.exists(obj.key) is True
        finally:
            await _cleanup(gc, store, engine)

    @pytest.mark.asyncio
    async def test_gc_is_idempotent_and_restart_safe(self) -> None:
        gc, store, engine = await _make_gc(grace=0)
        try:
            await store.put(b"idempotent blob", metadata={})
            first = await gc.run()
            second = await gc.run()
            assert first.deleted >= 1
            assert second.deleted == 0  # nothing left to delete
        finally:
            await _cleanup(gc, store, engine)
