"""W3C Trace Context helpers used by the dependency-free request span boundary."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

_TRACEPARENT = re.compile(
    r"^00-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    sampled: bool

    @property
    def traceparent(self) -> str:
        flags = "01" if self.sampled else "00"
        return f"00-{self.trace_id}-{self.span_id}-{flags}"


def build_tracer(service_name: str, endpoint: str | None = None) -> Tracer:
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    return provider.get_tracer("cap.control-plane")


def start_server_span(traceparent: str | None) -> TraceContext:
    parent_span_id = None
    trace_id = secrets.token_hex(16)
    sampled = True
    if traceparent:
        match = _TRACEPARENT.fullmatch(traceparent.strip().lower())
        if match and match.group("trace_id") != "0" * 32 and match.group("span_id") != "0" * 16:
            trace_id = match.group("trace_id")
            parent_span_id = match.group("span_id")
            sampled = bool(int(match.group("flags"), 16) & 1)
    return TraceContext(
        trace_id=trace_id,
        span_id=secrets.token_hex(8),
        parent_span_id=parent_span_id,
        sampled=sampled,
    )
