"""Phase 28.4 -- worker metrics + liveness/readiness HTTP endpoints.

Serves on a dedicated loop (``uvicorn``) without interfering with the claim
loop.  Endpoints:
  * ``/healthz`` -- liveness (always 200 when the process runs)
  * ``/readyz``  -- readiness (DB / schema / registration / object store /
                    sandbox provider; 503 when any critical dependency fails)
  * ``/metrics`` -- Prometheus text exposition of the acquisition pipeline

No secrets, tokens or high-cardinality identifiers are ever exported.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("cap.acquisition.metrics_server")


async def run_metrics_server(
    *,
    metrics: Any,
    health: Any,
    host: str = "127.0.0.1",
    port: int = 9100,
) -> None:
    """Run the uvicorn server until cancelled."""
    from fastapi import FastAPI, Response
    from fastapi.responses import JSONResponse

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> Response:
        alive = await health.liveness()
        return Response(status_code=200 if alive else 503, content="ok\n")

    @app.get("/readyz")
    async def readyz() -> Response:
        result = await health.readiness()
        body = "\n".join(
            f"{name}: {'ok' if ok else 'FAIL'}" for name, ok in sorted(result.checks.items())
        )
        return Response(
            status_code=200 if result.healthy else 503,
            media_type="text/plain",
            content=body + "\n",
        )

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(
            media_type="text/plain; version=0.0.4; charset=utf-8",
            content=metrics.render(),
        )

    config = __import__("uvicorn").Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = __import__("uvicorn").Server(config)
    logger.info("metrics server listening on %s:%s", host, port)
    try:
        await server.serve()
    except asyncio.CancelledError:  # pragma: no cover -- shutdown path
        raise
    except Exception as error:  # noqa: BLE001
        logger.warning("metrics server stopped: %s", error)
