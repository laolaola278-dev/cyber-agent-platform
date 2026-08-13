"""Request metrics, W3C trace context, and structured correlation middleware."""

from __future__ import annotations

import logging
from time import perf_counter

from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.logging.context import trace_id_context
from app.observability import MetricsRegistry, start_server_span

logger = logging.getLogger("cap.http")


class ObservabilityMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        metrics: MetricsRegistry,
        tracer: Tracer,
        tracing_enabled: bool = True,
    ) -> None:
        super().__init__(app)
        self.metrics = metrics
        self.tracer = tracer
        self.tracing_enabled = tracing_enabled

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = perf_counter()
        self.metrics.begin()
        fallback_context = start_server_span(request.headers.get("traceparent"))
        context = fallback_context
        token = trace_id_context.set(fallback_context.trace_id)
        response: Response | None = None
        status_code = 500
        try:
            with self.tracer.start_as_current_span(
                f"{request.method} {request.url.path}",
                context=extract(request.headers),
                kind=SpanKind.SERVER,
                attributes={
                    "http.request.method": request.method,
                    "url.path": request.url.path,
                },
            ) as span:
                span_context = span.get_span_context()
                if span_context.is_valid:
                    context = type(fallback_context)(
                        trace_id=f"{span_context.trace_id:032x}",
                        span_id=f"{span_context.span_id:016x}",
                        parent_span_id=fallback_context.parent_span_id,
                        sampled=span_context.trace_flags.sampled,
                    )
                request.state.trace_id = context.trace_id
                request.state.span_id = context.span_id
                trace_id_context.set(context.trace_id)
                response = await call_next(request)
                status_code = response.status_code
                span.set_attribute("http.response.status_code", status_code)
                if status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                return response
        finally:
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            duration = perf_counter() - started
            self.metrics.observe(request.method, route_path, status_code, duration)
            if response is not None:
                response.headers["traceparent"] = context.traceparent
                response.headers["X-Trace-ID"] = context.trace_id
                response.headers["X-Span-ID"] = context.span_id
            logger.info(
                "http_request_completed",
                extra={
                    "method": request.method,
                    "route": route_path,
                    "status_code": status_code,
                    "duration_ms": round(duration * 1000, 3),
                    "trace_id": context.trace_id,
                    "span_id": context.span_id,
                },
            )
            trace_id_context.reset(token)
