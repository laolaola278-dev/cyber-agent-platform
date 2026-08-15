"""Phase 28 -- agent integration tests (robots, blocked, replan, dedup,
evidence lineage, extraction) using the SyntheticWeb harness."""

from __future__ import annotations

import pytest

from app.acquisition.agent import AdaptiveDataAcquisitionAgent, AgentConfig
from app.acquisition.dataset import (
    SyntheticResponse,
    SyntheticWeb,
    _html,
)
from app.acquisition.documentadapter import DocumentAdapter
from app.acquisition.evaluation import SyntheticBrowser, _TempStore
from app.acquisition.httpadapter import HTTPAdapter
from app.acquisition.models import AcquisitionPolicy, AcquisitionStatus, BlockReason
from app.acquisition.planner import AcquisitionPlanner, PlannerRequest
from app.acquisition.urlpolicy import URLPolicyValidator

ORIGIN = "https://bench.example"
PUBLIC = ["93.184.216.34"]


def _policy() -> AcquisitionPolicy:
    return AcquisitionPolicy(request_rate=100.0)


def _agent(
    routes: dict,
    *,
    with_browser: bool = False,
    resolver=None,
) -> AdaptiveDataAcquisitionAgent:
    web = SyntheticWeb(routes=routes)
    validator = URLPolicyValidator(resolver=resolver or (lambda host: PUBLIC))
    http = HTTPAdapter(policy=_policy(), validator=validator, client_factory=web.client_factory())
    agent = AdaptiveDataAcquisitionAgent(
        http=http,
        store=_TempStore("mem"),
        planner=AcquisitionPlanner(policy=_policy()),
        browser=SyntheticBrowser(web) if with_browser else None,
        document=DocumentAdapter(),
        config=AgentConfig(task_id="t1", trace_id="tr1"),
    )
    return agent


async def _run(agent: AdaptiveDataAcquisitionAgent, url: str, **kwargs):
    request = PlannerRequest(goal="g", url=url, **kwargs)
    return await agent.acquire(request)


@pytest.mark.asyncio
async def test_agent_static_html_success() -> None:
    url = f"{ORIGIN}/page"
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            url: SyntheticResponse(200, {"content-type": "text/html"}, _html("Page", "body text")),
        }
    )
    result = await _run(agent, url)
    assert result.status == AcquisitionStatus.COMPLETE
    assert len(result.artifacts) == 1
    assert result.artifacts[0].sha256
    assert result.documents and result.documents[0].title == "Page"
    assert result.artifacts[0].trace_id == "tr1"
    assert result.artifacts[0].tool == "acquisition.http"


@pytest.mark.asyncio
async def test_agent_401_stops_blocked() -> None:
    url = f"{ORIGIN}/restricted"
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            url: SyntheticResponse(401, {}, b"unauthorized"),
        }
    )
    result = await _run(agent, url)
    assert result.status == AcquisitionStatus.BLOCKED
    assert result.blocked_reason == BlockReason.AUTH_REQUIRED


@pytest.mark.asyncio
async def test_agent_captcha_stops_blocked() -> None:
    url = f"{ORIGIN}/captcha"
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            url: SyntheticResponse(
                200,
                {"content-type": "text/html"},
                _html("Verify", "recaptcha verify you are human"),
            ),
        }
    )
    result = await _run(agent, url)
    assert result.status == AcquisitionStatus.BLOCKED
    assert result.blocked_reason == BlockReason.CAPTCHA


@pytest.mark.asyncio
async def test_agent_robots_disallow_blocked() -> None:
    url = f"{ORIGIN}/disallowed/x"
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(
                200, {}, b"User-agent: *\nDisallow: /disallowed/\n"
            ),
            url: SyntheticResponse(200, {"content-type": "text/html"}, _html("X", "y")),
        }
    )
    result = await _run(agent, url)
    assert result.status == AcquisitionStatus.BLOCKED
    assert result.blocked_reason == BlockReason.ROBOTS_DISALLOWED
    assert result.artifacts == []  # nothing was fetched


@pytest.mark.asyncio
async def test_agent_redirect_to_private_ip_blocked() -> None:
    url = f"{ORIGIN}/jump"
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            url: SyntheticResponse(302, {"location": "http://192.168.0.5/secret"}, b""),
        }
    )
    result = await _run(agent, url)
    assert result.status == AcquisitionStatus.BLOCKED
    assert result.blocked_reason == BlockReason.SSRF_BLOCKED


@pytest.mark.asyncio
async def test_agent_dns_rebinding_blocked() -> None:
    url = f"{ORIGIN}/rebind"
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            url: SyntheticResponse(200, {"content-type": "text/html"}, _html("R", "x")),
        },
        resolver=lambda host: ["169.254.169.254"],
    )
    result = await _run(agent, url)
    assert result.status == AcquisitionStatus.BLOCKED
    assert result.blocked_reason == BlockReason.SSRF_BLOCKED


@pytest.mark.asyncio
async def test_agent_replan_http_to_browser() -> None:
    url = f"{ORIGIN}/app"
    # HTTP returns an empty JS shell; the browser capability renders content
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            url: SyntheticResponse(
                200,
                {"content-type": "text/html"},
                b"<!doctype html><html><head><title>App</title></head>"
                b"<body><div id=app></div></body></html>",
            ),
        },
        with_browser=True,
    )
    result = await _run(agent, url)
    assert result.status == AcquisitionStatus.COMPLETE
    assert result.replans >= 1
    assert "DYNAMIC_HTML" in result.strategy_history


@pytest.mark.asyncio
async def test_agent_deduplication() -> None:
    url = f"{ORIGIN}/dup"
    body = _html("Dup", "identical content")
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            url: SyntheticResponse(200, {"content-type": "text/html"}, body),
            f"{url}?page=2": SyntheticResponse(200, {"content-type": "text/html"}, body),
        }
    )
    result = await _run(agent, url)
    # pagination detected via next link -> page 2 fetched -> content dedup
    assert result.completeness is not None
    assert len(result.artifacts) >= 1
    sha_set = {a.sha256 for a in result.artifacts}
    assert len(sha_set) == 1  # identical content stored once (content-addressed)


@pytest.mark.asyncio
async def test_agent_evidence_lineage() -> None:
    url = f"{ORIGIN}/page"
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            url: SyntheticResponse(
                200, {"content-type": "text/html"}, _html("Page", "CVE-2024-1234")
            ),
        }
    )
    result = await _run(agent, url)
    assert result.artifacts and result.artifacts[0].sha256 == result.artifacts[0].object_key
    doc = result.documents[0]
    assert doc.artifact_sha256 == result.artifacts[0].sha256
    assert doc.source_url == url
    # candidates extracted from document text
    assert result.candidate_bundles
    knowledge_cves = [
        c for b in result.candidate_bundles for c in b.knowledge if c.knowledge_type == "CVE"
    ]
    assert any(c.external_id == "CVE-2024-1234" for c in knowledge_cves)


@pytest.mark.asyncio
async def test_agent_never_visits_out_of_scope() -> None:
    url = f"{ORIGIN}/page"
    agent = _agent(
        {
            f"{ORIGIN}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            url: SyntheticResponse(
                200,
                {"content-type": "text/html"},
                _html("Page", "body", next_href="https://evil.example/track"),
            ),
        }
    )
    result = await _run(agent, url)
    visited_hosts = {u.split("//")[1].split("/")[0] for u in result.visited_urls}
    assert "evil.example" not in visited_hosts
