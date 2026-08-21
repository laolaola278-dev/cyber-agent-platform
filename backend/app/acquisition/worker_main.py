"""Phase 28.3 -- production Acquisition Worker daemon.

A standalone process that consumes the durable acquisition queue:

    API  (POST /acquisitions -> QUEUED row)
      |
      |  shared PostgreSQL (the durable queue is the DB, nothing in memory)
      v
    this daemon: poll -> atomic claim -> execute -> finalize

Run:

    python -m app.acquisition.worker_main

Environment (all optional except DATABASE_URL):

    DATABASE_URL             SQLAlchemy async URL (default from app settings)
    ACQ_WORKER_NAME          unique worker name (default acquisition-worker-<host>)
    ACQ_MAX_CONCURRENCY      registered max_concurrency (default 4)
    ACQ_POLL_INTERVAL        loop poll interval seconds (default 0.2)
    ACQ_BATCH_SIZE           max claims per tick (default 5)
    ACQ_LEASE_TTL_SECONDS    worker lease TTL (default 120)
    ACQ_STORE_ROOT           evidence object-store root (default outputs/acquisition-objects)
    ACQ_RUN_SECONDS          optional: gracefully stop after N seconds (test hook)

The daemon NEVER imports the FastAPI app and never runs inside the API
process. It builds the full execution chain exactly like the Phase 28.1
production path (PluginWorkerRuntime -> WorkerRuntime -> SandboxRuntime)
-- it does NOT use PluginWorkerRuntime.synthetic(), which is reserved for the
API-side cancel plumbing. On SIGINT/SIGTERM it stops claiming, drains
in-flight runs, releases leases, then exits; runs still RUNNING at exit are
recovered by another worker after their lease expires (crash-recovery
semantics).

The daemon assumes the schema is installed: run ``alembic upgrade head``
first. It does NOT call Base.metadata.create_all.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import sys
from typing import Any
from uuid import UUID

from app.acquisition.claim import AcquisitionClaimCoordinator
from app.acquisition.claim_loop import AcquisitionWorkerLoop
from app.acquisition.service import AcquisitionService
from app.acquisition.worker_path import AcquisitionWorkerPath
from app.database import AsyncSessionFactory, engine
from app.evidence.service import EvidenceService
from app.sandbox.policy import SandboxPolicyEngine
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import MemorySandboxProvider, SandboxRuntime
from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
from app.worker.lease import WorkerLeaseManager
from app.worker.plugin_runtime import PluginWorkerRuntime
from app.worker.registry import WorkerRegistry
from app.worker.runtime import WorkerRuntime
from app.worker.scheduler import WorkerScheduler
from app.exceptions import WorkerConflict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [acquisition-worker] %(message)s",
)
logger = logging.getLogger("app.acquisition.worker_main")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid %s=%r, using default %d", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid %s=%r, using default %s", name, raw, default)
        return default


async def _ensure_schema(session_factory: Any) -> None:
    """Fail fast (with a clear message) when migrations were not applied.

    The daemon deliberately refuses to create tables itself: the schema must
    come from ``alembic upgrade head`` (Phase 28.3 production closure).
    """
    from sqlalchemy import text

    async with session_factory() as session:
        try:
            await session.execute(text("SELECT 1 FROM acquisition_runs LIMIT 1"))
        except Exception as error:  # noqa: BLE001
            raise SystemExit(
                "acquisition_runs table is missing -- run `alembic upgrade head` "
                f"against DATABASE_URL first (probe error: {error})"
            ) from error


async def _build_runner(worker_path: AcquisitionWorkerPath, worker_id: UUID):
    """Return the claim-loop runner bound to this worker's fencing identity."""

    async def runner(run_id: UUID, token: UUID) -> Any:
        return await worker_path.run_claimed(run_id, worker_id, token)

    return runner


async def _amain() -> int:
    from app.config.settings import get_settings

    settings = get_settings()
    # Phase 28.4 observability: shared low-cardinality metric registry
    from app.acquisition.metrics import AcquisitionMetrics

    metrics = AcquisitionMetrics()
    worker_name = os.environ.get("ACQ_WORKER_NAME") or f"acquisition-worker-{socket.gethostname()}"
    max_concurrency = _env_int("ACQ_MAX_CONCURRENCY", 4)
    poll_interval = _env_float("ACQ_POLL_INTERVAL", 0.2)
    batch_size = _env_int("ACQ_BATCH_SIZE", 5)
    lease_ttl_seconds = _env_int("ACQ_LEASE_TTL_SECONDS", 120)
    store_root = os.environ.get("ACQ_STORE_ROOT") or "outputs/acquisition-objects"

    # Fail fast when the schema is not migrated (never create_all here).
    await _ensure_schema(AsyncSessionFactory)

    # -- component wiring (each gets its own session; PostgreSQL pool) ------
    loop_session = AsyncSessionFactory()
    work_session = AsyncSessionFactory()
    try:
        registry = WorkerRegistry(loop_session)
        leases = WorkerLeaseManager(loop_session)
        coordinator = AcquisitionClaimCoordinator(
            loop_session,
            leases,
            lease_ttl_seconds=lease_ttl_seconds,
            metrics=metrics,
        )

        worker = await registry.register(
            WorkerRecord(
                name=worker_name,
                runtime_version="28.3",
                capabilities=frozenset({"acquisition.http"}),
                max_concurrency=max_concurrency,
            ),
            actor="acquisition-worker",
        )
        # Phase 28.6: concurrent worker Pods may share the same name (e.g.
        # cap-cap-worker from a Deployment). After register() returns the
        # shared row, another Pod can heartbeat first and bump state_version.
        # Retry the initial heartbeat with a fresh DB read on conflict so
        # startup is eventually consistent instead of crashing.
        for _heartbeat_attempt in range(5):
            try:
                await registry.heartbeat(
                    WorkerHeartbeat(
                        worker_id=worker.id,
                        status=WorkerStatus.ONLINE,
                        active_executions=0,
                    ),
                    actor="acquisition-worker",
                )
                break
            except WorkerConflict:
                worker = await registry.require(worker.id)
        else:
            raise
        logger.info(
            "worker registered id=%s name=%s capabilities=%s max_concurrency=%d "
            "poll_interval=%.2fs batch_size=%d lease_ttl=%ds",
            worker.id,
            worker.name,
            sorted(worker.capabilities),
            worker.max_concurrency,
            poll_interval,
            batch_size,
            lease_ttl_seconds,
        )

        # -- Phase 28.4: durable object store (S3/MinIO) or local fallback ---
        from app.acquisition.store import (
            LocalFilesystemEvidenceStore,
            S3EvidenceStore,
        )

        if settings.object_store_backend.casefold() == "s3":
            store = S3EvidenceStore(
                endpoint=settings.object_store_endpoint,
                access_key=settings.object_store_access_key,
                secret_key=settings.object_store_secret_key,
                bucket=settings.object_store_bucket,
                secure=settings.object_store_secure,
                max_object_bytes=settings.object_store_max_object_bytes,
                metrics=metrics,
            )
            logger.info(
                "object store backend=s3 endpoint=%s bucket=%s",
                settings.object_store_endpoint,
                settings.object_store_bucket,
            )
        else:
            store = LocalFilesystemEvidenceStore(store_root)
            logger.info("object store backend=local root=%s", store_root)

        # -- Phase 28.4/28.5: production sandbox provider --------------------
        # Layer 1 -- network/browser isolation (service): the HTTP fetches and
        # browser sessions run in a REAL separate execution domain. 28.5 adds
        # the OCI container provider (Linux) with full isolation; the
        # subprocess provider remains for Windows/dev hosts.
        if settings.sandbox_provider.casefold() == "oci-sandbox":
            from app.sandbox.oci_provider import OCISandboxProvider

            network_runtime = SandboxRuntime(
                OCISandboxProvider(
                    image=settings.sandbox_image,
                    network=settings.sandbox_network,
                    egress_proxy_url=settings.egress_proxy_url,
                    default_memory_mb=settings.sandbox_memory_mb,
                    default_cpu_millicores=settings.sandbox_cpu_millicores,
                    default_pids_limit=settings.sandbox_pids_limit,
                    metrics=metrics,
                ),
                SandboxPolicyEngine(),
                metrics=metrics,
            )
            logger.info(
                "sandbox provider=oci-sandbox image=%s network=%s egress=%s",
                settings.sandbox_image,
                settings.sandbox_network or "(default)",
                settings.egress_proxy_url or "(direct, egress proxy required in prod)",
            )
        elif settings.sandbox_provider.casefold() == "kubernetes-sandbox":
            from app.sandbox.k8s_provider import KubernetesSandboxProvider

            network_runtime = SandboxRuntime(
                KubernetesSandboxProvider(
                    namespace=settings.sandbox_namespace,
                    image=settings.sandbox_image,
                    shim_port=settings.sandbox_shim_port,
                    egress_proxy=settings.egress_proxy_url,
                    pod_ready_timeout=settings.sandbox_pod_ready_timeout_seconds,
                    default_memory_mb=settings.sandbox_memory_mb,
                    default_cpu_millicores=settings.sandbox_cpu_millicores,
                    metrics=metrics,
                ),
                SandboxPolicyEngine(),
                metrics=metrics,
            )
            logger.info(
                "sandbox provider=kubernetes-sandbox image=%s namespace=%s egress=%s",
                settings.sandbox_image,
                settings.sandbox_namespace,
                settings.egress_proxy_url or "(direct, egress proxy required in prod)",
            )
        elif settings.sandbox_provider.casefold() == "subprocess-sandbox":
            from app.sandbox.subprocess_provider import SubprocessSandboxProvider

            network_runtime = SandboxRuntime(
                SubprocessSandboxProvider(
                    memory_mb=settings.sandbox_memory_mb,
                    max_processes=settings.sandbox_max_processes,
                ),
                SandboxPolicyEngine(),
                metrics=metrics,
            )
        else:
            # explicit opt-in to the non-isolated provider (dev/testing)
            network_runtime = SandboxRuntime(
                MemorySandboxProvider(),
                SandboxPolicyEngine(),
                metrics=metrics,
            )
        sandbox_profile = SandboxProfile(
            name="acquisition-worker",
            timeout_seconds=settings.sandbox_timeout_seconds,
            memory_mb=settings.sandbox_memory_mb,
        )

        # Layer 2 -- orchestration runtime (WorkerRuntime): the run operation
        # itself is DB-bound (it owns the worker session, the durable cancel
        # poll, the fenced commit). It must run IN the worker process -- it can
        # never be serialized into a sandbox subprocess (the closure captures
        # the session/engine). The memory provider here is the orchestration
        # carrier; real isolation is delivered at layer 1.
        orchestration_runtime = SandboxRuntime(
            MemorySandboxProvider(),
            SandboxPolicyEngine(),
            metrics=metrics,
        )

        # service + full execution chain (NOT synthetic)
        evidence = EvidenceService(
            work_session,
            publisher=None,
            storage_directory=store_root,  # type: ignore[arg-type]
        )
        # URLPolicyValidator construction: allow_private is a TEST-ONLY hook
        # (benchmark lab) -- production default keeps private/loopback denied
        validator = None
        if os.environ.get("ACQ_ALLOW_PRIVATE", "0") == "1":
            from app.acquisition.urlpolicy import URLPolicyValidator

            validator = URLPolicyValidator(allow_private=True)
        service = AcquisitionService(
            work_session,
            evidence,
            store_root=store_root,
            store=store,
            validator=validator,
            sandbox_runtime=network_runtime,
            sandbox_profile=sandbox_profile,
        )
        runtime_session = AsyncSessionFactory()
        worker_runtime = WorkerRuntime(
            # Phase 28.3 side-effect fencing: the execution runtime MUST use
            # a session SEPARATE from the service/evidence session. The
            # runtime's commit_result commits the sandbox execution row; if
            # that shared the service session it would also commit the
            # operation's evidence/artifact rows -- bypassing the run-level
            # fencing gate (verify_owner) that guards the final commit.
            runtime_session,
            WorkerRegistry(runtime_session),
            WorkerScheduler(WorkerRegistry(runtime_session)),
            WorkerLeaseManager(runtime_session),
            orchestration_runtime,
            lease_ttl_seconds=lease_ttl_seconds,
        )
        plugin = PluginWorkerRuntime(worker_runtime, SandboxProfile(name="acquisition-worker"))
        worker_path = AcquisitionWorkerPath(
            plugin,
            service,
            lease_ttl_seconds=lease_ttl_seconds,
            metrics=metrics,
        )

        runner = await _build_runner(worker_path, worker.id)

        # -- Phase 28.4: health / readiness (GATE 15) ------------------------
        from app.acquisition.health import WorkerHealth

        health = WorkerHealth(
            # fresh short-lived engine per check: safe from BOTH the claim
            # loop's loop and the metrics server's loop (no shared pool)
            database_url=settings.database_url,
            object_store=store,
            sandbox_runtime=network_runtime,
            worker_id=worker.id,
        )

        async def _readiness() -> bool:
            result = await health.readiness()
            return result.healthy

        loop = AcquisitionWorkerLoop(
            session=loop_session,
            coordinator=coordinator,
            worker_id=worker.id,
            runner=runner,
            poll_interval=poll_interval,
            batch_size=batch_size,
            registry=registry,
            metrics=metrics,
            readiness=_readiness,
        )

        # -- Phase 28.4: metrics + liveness/readiness endpoints ---------------
        from app.acquisition.metrics_server import run_metrics_server

        metrics_task = asyncio.create_task(
            run_metrics_server(
                metrics=metrics,
                health=health,
                host=os.environ.get("ACQ_METRICS_HOST", "127.0.0.1"),
                port=int(os.environ.get("ACQ_METRICS_PORT", "9100")),
            )
        )

        # -- Phase 28.5: orphan container reaper (OCI mode) ------------------
        reaper_task: asyncio.Task | None = None
        if settings.sandbox_provider.casefold() == "oci-sandbox":
            from app.sandbox.oci_reaper import OCIContainerReaper

            reaper = OCIContainerReaper(
                network_runtime.provider._driver,
                AsyncSessionFactory,
                interval_seconds=settings.sandbox_reaper_interval_seconds,
                metrics=metrics,
            )
            reaper_task = asyncio.create_task(reaper.run_forever())
            logger.info(
                "oci sandbox reaper started (interval=%ss)",
                settings.sandbox_reaper_interval_seconds,
            )

        # -- graceful shutdown ----------------------------------------------
        stop_event = asyncio.Event()

        def _request_shutdown() -> None:
            logger.info("shutdown requested; draining in-flight runs")
            loop.request_shutdown()
            stop_event.set()

        for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
            if sig is None:
                continue
            try:
                asyncio.get_running_loop().add_signal_handler(sig, _request_shutdown)
            except (NotImplementedError, RuntimeError):  # pragma: no cover -- platform limits
                logger.warning("signal handler for %s unavailable", sig.name)

        logger.info("acquisition worker started; polling durable queue")
        run_seconds = _env_float("ACQ_RUN_SECONDS", 0.0)
        if run_seconds > 0:
            # Test hook: gracefully stop claiming after N seconds (lets the
            # cross-process test terminate the daemon deterministically on
            # platforms without add_signal_handler support).
            asyncio.get_running_loop().call_later(run_seconds, _request_shutdown)
            logger.info("auto-shutdown scheduled in %.1fs", run_seconds)
        stats = await loop.run_forever()
        await stop_event.wait()
        metrics_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError, Exception):
            await metrics_task
        if reaper_task is not None:
            reaper_task.cancel()
            with __import__("contextlib").suppress(asyncio.CancelledError, Exception):
                await reaper_task
        logger.info(
            "worker stopped claimed=%d reclaimed=%d completed=%d cancelled=%d "
            "skipped_terminal=%d stale_rejected=%d errors=%d",
            stats.claimed,
            stats.reclaimed,
            stats.completed,
            stats.cancelled,
            stats.skipped_terminal,
            stats.stale_rejected,
            len(stats.errors),
        )
        return 0
    finally:
        try:
            await loop_session.close()
        except Exception:  # noqa: BLE001 -- best-effort cleanup
            pass
        try:
            await work_session.close()
        except Exception:  # noqa: BLE001 -- best-effort cleanup
            pass
        await engine.dispose()
        logger.info("database engine disposed")


def main() -> int:
    """Entry point: `python -m app.acquisition.worker_main`."""
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        logger.info("interrupted")
        return 0


if __name__ == "__main__":
    sys.exit(main())
