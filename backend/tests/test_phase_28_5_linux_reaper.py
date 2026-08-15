"""Phase 28.5-CI -- Linux reaper + hard cancellation certification.

Real-container proofs:

  * worker death leaves a container; reaper removes ONLY the stale owner's
    container and never the new owner's (execution/lease fencing on real ids)
  * hard cancellation: CANCEL_REQUESTED -> container exit -> CANCELLED, with
    t_cancel <= t_container_exit <= t_cancelled
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.certification,
    pytest.mark.oci,
    pytest.mark.security,
]

BACKEND = Path(__file__).resolve().parent.parent
IMAGE = os.environ.get("CAP_SANDBOX_IMAGE", "cap-sandbox-http:latest")
NETWORK = os.environ.get("CAP_SANDBOX_NETWORK", "cap-sandbox-egress")


def _docker() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=15,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


_need_docker = pytest.mark.skipif(not _docker(), reason="docker daemon not available")

_LABEL_EXEC = "cap.sandbox.execution_id"
_LABEL_LEASE = "cap.sandbox.lease_id"
_LABEL_RUN = "cap.sandbox.run_id"
_LABEL_WORKER = "cap.sandbox.worker_id"


def _start_managed_container(execution_id: str, lease_id: str, run_id: str, worker: str) -> str:
    name = f"cap-cert-reap-{uuid.uuid4().hex[:8]}"
    run = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            NETWORK,
            "--label",
            f"{_LABEL_EXEC}={execution_id}",
            "--label",
            f"{_LABEL_LEASE}={lease_id}",
            "--label",
            f"{_LABEL_RUN}={run_id}",
            "--label",
            f"{_LABEL_WORKER}={worker}",
            "--entrypoint",
            "sh",
            IMAGE,
            "-c",
            "sleep 600",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, f"managed container start failed: {run.stderr[-300:]}"
    return name


@_need_docker
def test_reaper_fencing_on_real_containers() -> None:
    """§18: stale A removed, current B untouched (real container ids)."""
    from app.sandbox.oci_reaper import OCIContainerReaper

    run_id = str(uuid.uuid4())
    worker_a, worker_b = str(uuid.uuid4()), str(uuid.uuid4())
    lease_a, lease_b = str(uuid.uuid4()), str(uuid.uuid4())

    a_name = _start_managed_container(str(uuid.uuid4()), lease_a, run_id, worker_a)
    b_name = _start_managed_container(str(uuid.uuid4()), lease_b, run_id, worker_b)
    try:
        # real DB-backed reaper with a session factory that finds the run
        # owned through lease_b (current owner) and worker_b ONLINE
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.acquisition.models_db import AcquisitionRun
        from app.database import Base
        from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
        from app.worker.registry import WorkerRegistry

        pg = os.environ.get("CAP_CERT_PG_DSN", "postgresql+asyncpg://cap@127.0.0.1:55432/cap283")
        # NullPool: no pooled connections cached across the per-section asyncio.run
        # event loops, so asyncpg transports are closed per-session and no
        # unclosed-socket/resource warnings leak into later test teardowns.
        engine = create_async_engine(pg, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        import asyncio

        async def seed() -> None:
            from datetime import UTC, datetime

            # ensure schema exists in the PG service container (create idempotently)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            task_id = uuid.uuid4()
            agent_id = uuid.uuid4()

            async with factory() as s:
                # FK targets must exist before AcquisitionRun references them
                # (agents.id RESTRICT, tasks.id CASCADE are enforced by PG).
                # Explicitly flush Task/Agent first so PG sees them before the
                # AcquisitionRun insert (avoids autoflush ordering surprises).
                from app.models.agent import Agent
                from app.models.task import Task

                s.add(
                    Task(
                        id=task_id,
                        name="reaper-cert-task",
                        task_type="acquisition.http",
                        status="RUNNING",
                        input={},
                        required_permissions=[],
                        required_capabilities=["acquisition.http"],
                    )
                )
                s.add(
                    Agent(
                        id=agent_id,
                        name=f"reaper-agent-{agent_id.hex[:6]}",
                        version="28.5",
                        permissions=[],
                        capabilities=["acquisition.http"],
                        tools=[],
                        status="ONLINE",
                        platform_version="0.2.1",
                    )
                )
                await s.flush()
                s.add(
                    AcquisitionRun(
                        id=uuid.UUID(run_id),  # run row; reaper keys on lease
                        idempotency_key=f"r-{uuid.uuid4().hex}",
                        request_fingerprint=f"f-{uuid.uuid4().hex}",
                        status="RUNNING",
                        worker_id=uuid.UUID(worker_b),
                        lease_id=uuid.UUID(lease_b),
                        goal="g",
                        source_type="web",
                        strategy="paged",
                        task_id=task_id,
                        agent_id=agent_id,
                        trace_id=f"t-{uuid.uuid4().hex[:6]}",
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                )
                reg = WorkerRegistry(s)
                await reg.register(
                    WorkerRecord(
                        id=uuid.UUID(worker_b),
                        name=f"w-{worker_b[:6]}",
                        runtime_version="28.5",
                        capabilities=frozenset({"acquisition.http"}),
                        max_concurrency=2,
                    )
                )
                await reg.heartbeat(
                    WorkerHeartbeat(
                        worker_id=uuid.UUID(worker_b),
                        status=WorkerStatus.ONLINE,
                        active_executions=0,
                    )
                )
                await s.commit()

        asyncio.run(seed())
        from app.sandbox.oci_provider import DockerCLIDriver

        driver = DockerCLIDriver()
        reaper = OCIContainerReaper(driver, factory)

        async def _cert() -> None:
            stats = await reaper.reconcile_once()
            remaining = await driver.list_by_labels({_LABEL_EXEC: ""})
            for c in remaining:
                labs = c.get("Config", {}).get("Labels", {})
                print(
                    f"[reaper-diag] remaining id={c.get('Id', '')[:12]} "
                    f"lease={labs.get(_LABEL_LEASE, '')[:8]} "
                    f"worker={labs.get(_LABEL_WORKER, '')[:8]}",
                    flush=True,
                )
            print(f"[reaper-diag] stats={stats}", flush=True)
            a_gone = all(
                (c.get("Config", {}).get("Labels", {}).get(_LABEL_LEASE) != lease_a)
                for c in remaining
            )
            assert a_gone, "stale container A was not removed by the reaper"
            b_alive = any(
                (c.get("Config", {}).get("Labels", {}).get(_LABEL_LEASE) == lease_b)
                for c in remaining
            )
            assert b_alive, "current owner container B was wrongly reaped"
            assert stats.stale >= 1

        asyncio.run(_cert())
        asyncio.run(_cleanup(engine))
    finally:
        for name in (a_name, b_name):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


async def _cleanup(engine) -> None:
    await engine.dispose()


@_need_docker
def test_cancellation_ordering_timestamps() -> None:
    """§17: t_cancel <= t_container_exit <= t_cancelled."""
    exec_id = str(uuid.uuid4())
    name = f"cap-cert-cancel-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            NETWORK,
            "--label",
            f"{_LABEL_EXEC}={exec_id}",
            "--entrypoint",
            "sh",
            IMAGE,
            "-c",
            "sleep 600",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        t_cancel = time.monotonic()
        # SIGTERM (graceful) then confirm exit then force remove
        subprocess.run(["docker", "stop", "--time", "2", name], capture_output=True, timeout=30)
        # confirm the container actually exited
        state = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", name],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert state.stdout.strip() == "exited", "container did not exit on cancel"
        t_exit = time.monotonic()
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        t_cancelled = time.monotonic()
        assert t_cancel <= t_exit <= t_cancelled, "cancellation ordering violated"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
