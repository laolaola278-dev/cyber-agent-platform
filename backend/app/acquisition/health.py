"""Phase 28.4 -- worker health / readiness (GATE 15).

Liveness: the process is alive.
Readiness: the worker may claim new work. Checks:
  * DB connectivity (SELECT 1)
  * schema compatible (alembic version row reachable)
  * worker registration (registry row present)
  * object store reachable (when configured)
  * sandbox provider available
  * egress enforcement reachable (production only; PATCH-GATE 5)

A single FAILED acquisition never flips readiness. Dependency failures do.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("cap.acquisition.health")


@dataclass(slots=True)
class HealthCheckResult:
    healthy: bool
    checks: dict[str, bool] = field(default_factory=dict)
    detail: dict[str, str] = field(default_factory=dict)


class WorkerHealth:
    """Readiness/liveness gate for the production acquisition worker.

    Every DB-backed check uses a FRESH short-lived engine so the checks are
    safe to run from ANY event loop (the claim loop's loop and the metrics
    server's own loop). Sharing a pool across loops is what triggers
    asyncpg MissingGreenlet ping failures.
    """

    def __init__(
        self,
        *,
        session_factory: Any | None = None,
        database_url: str | None = None,
        object_store: Any | None = None,
        sandbox_runtime: Any | None = None,
        worker_id: Any | None = None,
        egress_proxy_url: str = "",
        require_egress_enforcement: bool = False,
        egress_probe_timeout_seconds: float = 2.0,
    ) -> None:
        self._session_factory = session_factory
        self._database_url = database_url
        self._object_store = object_store
        self._sandbox_runtime = sandbox_runtime
        self._worker_id = worker_id
        self._egress_proxy_url = egress_proxy_url
        self._require_egress_enforcement = require_egress_enforcement
        self._egress_probe_timeout_seconds = egress_probe_timeout_seconds

    def _new_session_factory(self):
        if self._session_factory is not None:
            return self._session_factory, None
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(self._database_url or "", pool_pre_ping=False)
        return async_sessionmaker(engine, expire_on_commit=False), engine

    async def _check_db(self) -> bool:
        factory, engine = self._new_session_factory()
        try:
            async with factory() as session:
                await session.execute(__import__("sqlalchemy").text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            if engine is not None:
                await engine.dispose()

    async def _check_schema(self) -> bool:
        factory, engine = self._new_session_factory()
        try:
            async with factory() as session:
                from sqlalchemy import text

                row = await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
                row = row.scalar_one_or_none()
            return row is not None
        except Exception:  # noqa: BLE001
            return False
        finally:
            if engine is not None:
                await engine.dispose()

    async def _check_registration(self) -> bool:
        if self._worker_id is None:
            return True  # not enforced by this deployment
        factory, engine = self._new_session_factory()
        try:
            from app.worker.contracts import WorkerStatus
            from app.worker.registry import WorkerRegistry

            async with factory() as session:
                worker = await WorkerRegistry(session).require(self._worker_id)
            return worker.status == WorkerStatus.ONLINE
        except Exception:  # noqa: BLE001
            return False
        finally:
            if engine is not None:
                await engine.dispose()

    async def _check_object_store(self) -> bool:
        if self._object_store is None:
            return True
        try:
            if hasattr(self._object_store, "health"):
                return bool(await self._object_store.health())
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _check_sandbox(self) -> bool:
        if self._sandbox_runtime is None:
            return True
        try:
            if hasattr(self._sandbox_runtime, "health"):
                return bool(await self._sandbox_runtime.health())
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _check_egress_enforcement(self) -> bool:
        """v1.0.1 PATCH-GATE 5 -- enforced egress must be reachable.

        Layer 1 (``URLPolicyValidator``) runs inside the sandbox shim and is
        unaffected by this check. Layer 2 is the controlled egress proxy, and
        the invariant carried from Phase 28.5/28.6 is that the sandbox's only
        route out is that proxy: with Kubernetes the NetworkPolicy denies
        everything else, with OCI the container sits on an isolated bridge
        network.

        So when the proxy is unreachable the correct behaviour is
        readiness=false and failed network jobs -- **never** a silent switch to
        direct egress. A TCP connect probe is used deliberately: it proves the
        proxy is listening without emitting traffic to a public target on
        every readiness poll.
        """

        if not self._require_egress_enforcement:
            return True
        url = (self._egress_proxy_url or "").strip()
        if not url:
            # Startup validation already refuses this in production; a worker
            # that somehow got here is not allowed to claim network work.
            logger.error("egress enforcement required but EGRESS_PROXY_URL is empty")
            return False
        try:
            parsed = urlparse(url)
            host = parsed.hostname
            if not host:
                logger.error("egress proxy URL %r has no host", url)
                return False
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=self._egress_probe_timeout_seconds,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, TimeoutError, ValueError) as error:
            logger.error(
                "egress proxy %s unreachable (%s): readiness=false, "
                "no direct-egress fallback",
                url,
                error,
            )
            return False

    async def readiness(self) -> HealthCheckResult:
        db = await self._check_db()
        schema = await self._check_schema()
        reg = await self._check_registration()
        store = await self._check_object_store()
        sandbox = await self._check_sandbox()
        egress = await self._check_egress_enforcement()
        checks = {
            "db_connectivity": db,
            "schema_compatible": schema,
            "worker_registration": reg,
            "object_store": store,
            "sandbox_provider": sandbox,
            "egress_enforcement": egress,
        }
        return HealthCheckResult(
            healthy=all(checks.values()),
            checks=checks,
            detail={k: "ok" if v else "failed" for k, v in checks.items()},
        )

    async def liveness(self) -> bool:
        return True
