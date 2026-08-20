"""Phase 28 -- exhaustive defensive-branch coverage: http adapter failure
modes (timeout, compressed bodies, artifact builder), agent evidence sink /
store failure / verdict mapping, browser adapter shutdown with no manager."""

from __future__ import annotations

import asyncio

import httpx

from app.acquisition.agent import AdaptiveDataAcquisitionAgent, AgentConfig
from app.acquisition.dataset import SyntheticResponse, SyntheticWeb, _html
from app.acquisition.evaluation import _TempStore
from app.acquisition.httpadapter import HTTPAdapter
from app.acquisition.models import (
    AcquisitionPolicy,
    AcquisitionStatus,
    BlockReason,
    RawArtifact,
    Verdict,
)
from app.acquisition.planner import AcquisitionPlanner, PlannerRequest
from app.acquisition.urlpolicy import URLPolicyValidator


def run(coro):
    return asyncio.run(coro)


# -- http adapter failure modes ----------------------------------------------


def _adapter(transport=None, policy: AcquisitionPolicy | None = None):
    policy = policy or AcquisitionPolicy(request_rate=100.0)
    if transport is not None:
        client_factory = lambda: httpx.AsyncClient(  # noqa: E731
            transport=transport, timeout=httpx.Timeout(5.0), follow_redirects=False
        )
    else:
        client_factory = None
    return HTTPAdapter(
        policy=policy,
        validator=URLPolicyValidator(resolver=lambda host: ["93.184.216.34"]),
        client_factory=client_factory,
    )


class _RaisingClient:
    """Client whose get() raises a typed httpx error."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def get(self, url: str):
        raise self._error

    async def aclose(self) -> None:
        return None


def test_http_adapter_timeout_exception() -> None:
    adapter = _adapter(
        transport=None,
        policy=AcquisitionPolicy(request_rate=100.0),
    )
    adapter._client_factory = lambda: _RaisingClient(  # noqa: E731
        httpx.TimeoutException("simulated timeout")
    )
    result = run(adapter.fetch("https://bench.example/slow"))
    assert result.blocked_reason == BlockReason.TIMEOUT
    assert "timeout" in result.blocked_detail


def test_http_adapter_http_error() -> None:
    adapter = _adapter(
        transport=None,
        policy=AcquisitionPolicy(request_rate=100.0),
    )
    adapter._client_factory = lambda: _RaisingClient(  # noqa: E731
        httpx.ConnectError("connection refused")
    )
    result = run(adapter.fetch("https://bench.example/down"))
    assert result.blocked_reason == BlockReason.FAILED
    assert "http error" in result.blocked_detail


def test_http_adapter_compressed_body_rejected() -> None:
    web = SyntheticWeb(
        {
            "https://bench.example/robots.txt": SyntheticResponse(200, {}, b"x"),
            "https://bench.example/gz": SyntheticResponse(
                200, {"content-type": "text/html", "content-encoding": "gzip"}, b"garbage"
            ),
        }
    )
    adapter = HTTPAdapter(
        policy=AcquisitionPolicy(request_rate=100.0),
        validator=URLPolicyValidator(resolver=lambda host: ["93.184.216.34"]),
        client_factory=web.client_factory(),
    )
    result = run(adapter.fetch("https://bench.example/gz"))
    # compressed bodies are rejected outright (decompression-bomb defence)
    assert result.blocked_reason == BlockReason.SIZE_LIMIT or result.status == 0


def test_http_adapter_redirect_no_location_breaks() -> None:
    web = SyntheticWeb(
        {
            "https://bench.example/r": SyntheticResponse(302, {}, b""),
        }
    )
    adapter = HTTPAdapter(
        policy=AcquisitionPolicy(request_rate=100.0),
        validator=URLPolicyValidator(resolver=lambda host: ["93.184.216.34"]),
        client_factory=web.client_factory(),
    )
    result = run(adapter.fetch("https://bench.example/r"))
    # no location header -> treated as final response (redirect stops)
    assert result.final_url == "https://bench.example/r"


def test_http_adapter_build_artifact() -> None:
    adapter = _adapter()
    fetch_result = type(
        "R",
        (),
        {
            "status": 200,
            "final_url": "https://bench.example/final",
            "content": b"data",
            "content_type": "text/html",
            "etag": '"abc"',
            "last_modified": "Wed, 21 Oct 2026 07:28:00 GMT",
            "redirects": [],
        },
    )()
    artifact = adapter.build_artifact(
        fetch_result, task_id="t1", trace_id="tr1", tool_version="0.1"
    )
    assert artifact.tool == "acquisition.http"
    assert artifact.etag == '"abc"'
    assert artifact.trace_id == "tr1"


# -- agent: evidence sink + store failure + verdict mapping ------------------


class _RecordingSink:
    def __init__(self) -> None:
        self.saved: list[str] = []

    async def save_evidence(
        self, artifact: RawArtifact, object_key: str, content: bytes = b""
    ) -> str:
        self.saved.append(object_key)
        return f"evidence-{object_key[:8]}"

    async def commit(self) -> None:
        return None


def _agent_with_sink(sink, store=None):
    web = SyntheticWeb(
        {
            "https://bench.example/robots.txt": SyntheticResponse(
                200, {}, b"User-agent: *\nAllow: /\n"
            ),
            "https://bench.example/page": SyntheticResponse(
                200, {"content-type": "text/html"}, _html("P", "body")
            ),
        }
    )
    policy = AcquisitionPolicy(request_rate=100.0)
    return AdaptiveDataAcquisitionAgent(
        http=HTTPAdapter(
            policy=policy,
            validator=URLPolicyValidator(resolver=lambda host: ["93.184.216.34"]),
            client_factory=web.client_factory(),
        ),
        store=store or _TempStore("mem"),
        planner=AcquisitionPlanner(policy=policy),
        evidence_sink=sink,  # type: ignore[arg-type]
        config=AgentConfig(task_id="t1", trace_id="tr1"),
    )


def test_agent_evidence_sink_called() -> None:
    sink = _RecordingSink()
    agent = _agent_with_sink(sink)
    result = run(agent.acquire(PlannerRequest(goal="g", url="https://bench.example/page")))
    assert len(sink.saved) >= 1
    assert len(result.evidence_ids) >= 1


class _ExplodingStore(_TempStore):
    async def put(self, data: bytes, *, metadata=None) -> None:
        raise RuntimeError("disk full")


def test_agent_store_failure_partial() -> None:
    agent = _agent_with_sink(_RecordingSink(), store=_ExplodingStore("mem"))
    result = run(agent.acquire(PlannerRequest(goal="g", url="https://bench.example/page")))
    assert result.status in (AcquisitionStatus.PARTIAL, AcquisitionStatus.COMPLETE)
    assert result.artifacts == []  # nothing persisted


def test_agent_verdict_retry_maps_partial() -> None:
    status = AdaptiveDataAcquisitionAgent._status_from_verdict(
        Verdict.RETRY,
        None,  # type: ignore[arg-type]
    )
    assert status == AcquisitionStatus.PARTIAL
    status = AdaptiveDataAcquisitionAgent._status_from_verdict(
        Verdict.REPLAN,
        None,  # type: ignore[arg-type]
    )
    assert status == AcquisitionStatus.PARTIAL


def test_agent_verdict_finish_complete() -> None:
    status = AdaptiveDataAcquisitionAgent._status_from_verdict(
        Verdict.FINISH,
        None,  # type: ignore[arg-type]
    )
    assert status == AcquisitionStatus.COMPLETE


# -- browser adapter shutdown without manager --------------------------------


def test_browser_adapter_shutdown_no_manager() -> None:
    from app.acquisition.browseradapter import PlaywrightAcquisitionAdapter

    adapter = PlaywrightAcquisitionAdapter.__new__(PlaywrightAcquisitionAdapter)
    adapter._browser_manager = None
    run(adapter.shutdown())  # no-op, no crash
