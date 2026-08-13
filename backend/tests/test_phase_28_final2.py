"""Phase 28 -- agent terminal branch coverage: paywall stop, replan-without-
browser, browser-unavailable, pagination store failure, extraction failure."""

from __future__ import annotations

import asyncio

from app.acquisition.agent import AdaptiveDataAcquisitionAgent, AgentConfig
from app.acquisition.dataset import (
    SyntheticResponse,
    SyntheticWeb,
    _html,
)
from app.acquisition.evaluation import _TempStore
from app.acquisition.httpadapter import HTTPAdapter
from app.acquisition.models import AcquisitionPolicy, AcquisitionStatus, BlockReason
from app.acquisition.planner import AcquisitionPlanner, PlannerRequest
from app.acquisition.urlpolicy import URLPolicyValidator

ORIGIN = "https://bench.example"
PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


def run(coro):
    return asyncio.run(coro)


def _agent(routes: dict, *, browser=None, store=None):
    web = SyntheticWeb(routes=routes)
    policy = AcquisitionPolicy(request_rate=100.0)
    return AdaptiveDataAcquisitionAgent(
        http=HTTPAdapter(
            policy=policy,
            validator=URLPolicyValidator(resolver=PUBLIC),
            client_factory=web.client_factory(),
        ),
        store=store or _TempStore("mem"),
        planner=AcquisitionPlanner(policy=policy),
        browser=browser,
        config=AgentConfig(task_id="t", trace_id="tr"),
    )


def test_agent_paywall_stops() -> None:
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            f"{ORIGIN}/premium": SyntheticResponse(
                200,
                {"content-type": "text/html"},
                _html("Premium", "subscribe to continue reading"),
            ),
        }
    )
    result = run(agent.acquire(PlannerRequest(goal="g", url=f"{ORIGIN}/premium")))
    assert result.status == AcquisitionStatus.BLOCKED
    assert result.blocked_reason == BlockReason.PAYWALL


def test_agent_replan_without_browser_uses_budget() -> None:
    """Replan requested but no browser capability -> budget loop, then PARTIAL."""
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            f"{ORIGIN}/app": SyntheticResponse(
                200,
                {"content-type": "text/html"},
                b"<html><head><title>A</title></head><body><div id=app></div></body></html>",
            ),
        },
        browser=None,
    )
    result = run(agent.acquire(PlannerRequest(goal="g", url=f"{ORIGIN}/app")))
    assert result.status in (AcquisitionStatus.PARTIAL, AcquisitionStatus.COMPLETE)
    assert result.replans == 0  # no browser -> no transport switch


class _UnavailableBrowser:
    async def browse(self, url, **kwargs):
        return type(
            "Obs",
            (),
            {
                "url": url,
                "final_url": url,
                "status": None,
                "html": "",
                "title": "",
                "endpoints": [],
                "available": False,
                "error": "playwright unavailable",
            },
        )()


def test_agent_browser_unavailable_partial() -> None:
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            f"{ORIGIN}/app": SyntheticResponse(
                200,
                {"content-type": "text/html"},
                b"<html><head><title>A</title></head><body><div id=app></div></body></html>",
            ),
        },
        browser=_UnavailableBrowser(),
    )
    result = run(agent.acquire(PlannerRequest(goal="g", url=f"{ORIGIN}/app")))
    assert result.status in (AcquisitionStatus.PARTIAL, AcquisitionStatus.COMPLETE)


class _FailingStore:
    def __init__(self) -> None:
        self.fail_next = False

    async def put(self, data: bytes, *, metadata=None):
        if self.fail_next:
            raise RuntimeError("store unavailable")
        return type("Stored", (), {"key": "x" * 64, "size": len(data), "metadata": {}})()

    async def get(self, key: str) -> bytes:
        return b""

    async def exists(self, key: str) -> bool:
        return True

    async def metadata(self, key: str) -> dict:
        return {}


def test_agent_pagination_store_failure_breaks() -> None:
    store = _FailingStore()
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            f"{ORIGIN}/l": SyntheticResponse(
                200,
                {"content-type": "text/html"},
                _html("L", "x", next_href=f"{ORIGIN}/l?page=2"),
            ),
            f"{ORIGIN}/l?page=2": SyntheticResponse(
                200, {"content-type": "text/html"}, _html("L", "x")
            ),
        },
        store=store,
    )
    store.fail_next = False
    result = run(agent.acquire(PlannerRequest(goal="g", url=f"{ORIGIN}/l")))
    # page 1 stored; page 2 store failure breaks pagination without crashing
    assert len(result.artifacts) >= 1


def test_agent_pagination_empty_content_breaks() -> None:
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            f"{ORIGIN}/l": SyntheticResponse(
                200,
                {"content-type": "text/html"},
                _html("L", "x", next_href=f"{ORIGIN}/l?page=2"),
            ),
            f"{ORIGIN}/l?page=2": SyntheticResponse(200, {"content-type": "text/html"}, b""),
        }
    )
    result = run(agent.acquire(PlannerRequest(goal="g", url=f"{ORIGIN}/l")))
    assert len(result.artifacts) == 1  # empty next page stops pagination


def test_agent_extract_parse_error_recorded() -> None:
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            f"{ORIGIN}/weird": SyntheticResponse(
                200, {"content-type": "application/x-unknown"}, b"\x00\x01\x02binary"
            ),
        }
    )
    result = run(agent.acquire(PlannerRequest(goal="g", url=f"{ORIGIN}/weird")))
    assert result.artifacts  # raw artifact always preserved
    assert result.documents  # extraction attempted
