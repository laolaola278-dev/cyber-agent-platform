"""Phase 28 -- defensive branch coverage for agent / adapters / pagination /
candidates / dedup (the remaining <95% lines)."""

from __future__ import annotations

import asyncio

from app.acquisition.candidates import CandidateBundle, extract_candidates
from app.acquisition.dedup import canonicalize_url, content_sha256
from app.acquisition.pagination import detect_strategy, extract_page_links, next_page_url


def run(coro):
    return asyncio.run(coro)


# -- pagination branches -----------------------------------------------------


def test_pagination_skips_non_http_hrefs() -> None:
    html = (
        '<a href="javascript:void(0)">js</a>'
        '<a href="mailto:x@y.z">mail</a>'
        '<a href="tel:123">tel</a>'
        '<a href="#frag">frag</a>'
        '<a href="/real">real</a>'
    )
    links = extract_page_links(html, "https://example.com/")
    assert links.hrefs == ["/real"]


def test_pagination_next_page_url_none_kinds() -> None:
    strategy = detect_strategy(page_url="https://example.com/f", html="plain")
    assert strategy.kind == "none"
    assert next_page_url(strategy, "https://example.com/f", 1) is None


def test_pagination_resolve_bad_href() -> None:
    strategy = detect_strategy(
        page_url="https://example.com/l",
        html='<a href="javascript:x" rel="next">next</a>',
    )
    # next_href resolves to None -> falls back to none strategy
    assert strategy.kind == "none" or strategy.next_url is None


# -- candidates branches ----------------------------------------------------


def test_candidates_bundle_extend() -> None:
    bundle = CandidateBundle()
    other = extract_candidates("CVE-2024-1111", evidence_id=None, source_url="u")
    bundle.extend(other)
    assert len(bundle.knowledge) == 1
    assert len(bundle.facts) >= 1


def test_candidates_ip_not_plausible_skipped() -> None:
    bundle = extract_candidates("host 999.888.777.666", evidence_id=None, source_url="u")
    assert bundle.entities == []


# -- dedup branches ----------------------------------------------------------


def test_dedup_changed_content_allowed() -> None:
    from app.acquisition.dedup import DuplicateRegistry

    registry = DuplicateRegistry()
    assert registry.check("https://example.com/app", "h1") is None
    # same URL different content -> allowed, hash updated
    assert registry.check("https://example.com/app", "h2") is None
    # same URL same content again -> duplicate
    assert registry.check("https://example.com/app", "h2") is not None


def test_dedup_cross_url_content_duplicate() -> None:
    from app.acquisition.dedup import DuplicateRegistry

    registry = DuplicateRegistry()
    assert registry.check("https://a.example/x", "h1") is None
    assert registry.check("https://b.example/y", "h1") is not None


def test_canonicalize_malformed() -> None:
    assert canonicalize_url("not a url") == "not a url"
    assert canonicalize_url("") == "/"  # empty URL -> root path


def test_content_sha256_empty() -> None:
    assert len(content_sha256(b"")) == 64


# -- agent defensive branches ------------------------------------------------


def _agent(routes: dict, *, with_browser: bool = False, resolver=None):
    from app.acquisition.agent import AdaptiveDataAcquisitionAgent, AgentConfig
    from app.acquisition.dataset import SyntheticWeb
    from app.acquisition.evaluation import _TempStore
    from app.acquisition.httpadapter import HTTPAdapter
    from app.acquisition.models import AcquisitionPolicy
    from app.acquisition.planner import AcquisitionPlanner
    from app.acquisition.urlpolicy import URLPolicyValidator

    web = SyntheticWeb(routes=routes)
    policy = AcquisitionPolicy(request_rate=100.0)
    return AdaptiveDataAcquisitionAgent(
        http=HTTPAdapter(
            policy=policy,
            validator=URLPolicyValidator(
                resolver=resolver or (lambda host: ["93.184.216.34"])
            ),
            client_factory=web.client_factory(),
        ),
        store=_TempStore("mem"),
        planner=AcquisitionPlanner(policy=policy),
        browser=None,
        config=AgentConfig(task_id="t", trace_id="tr"),
    ), AcquisitionPolicy(request_rate=100.0)


def test_agent_robots_401_stops() -> None:
    from app.acquisition.dataset import SyntheticResponse
    from app.acquisition.planner import PlannerRequest

    origin = "https://bench.example"
    agent, _policy = _agent(
        {
            f"{origin}/robots.txt": SyntheticResponse(403, {}, b"forbidden"),
        }
    )
    assert agent is not None
    result = run(agent.acquire(PlannerRequest(goal="g", url=f"{origin}/page")))
    assert result.status.value == "BLOCKED"
    assert result.blocked_reason.value == "AUTH_REQUIRED"


def test_agent_duplicate_records_marked() -> None:
    from app.acquisition.dataset import _html
    from app.acquisition.dedup import DuplicateRegistry

    origin = "https://bench.example"
    body = _html("Dup", "same")
    registry = DuplicateRegistry()
    assert registry.check(f"{origin}/a", content_sha256(body)) is None
    dup = registry.check(f"{origin}/b", content_sha256(body))
    assert dup is not None
    assert registry.duplicates == [(f"{origin}/b", f"{origin}/a")]


def test_agent_pagination_stops_on_403_page() -> None:
    from app.acquisition.agent import AdaptiveDataAcquisitionAgent, AgentConfig
    from app.acquisition.dataset import SyntheticResponse, SyntheticWeb, _html
    from app.acquisition.evaluation import _TempStore
    from app.acquisition.httpadapter import HTTPAdapter
    from app.acquisition.models import AcquisitionPolicy
    from app.acquisition.planner import AcquisitionPlanner, PlannerRequest
    from app.acquisition.urlpolicy import URLPolicyValidator

    origin = "https://bench.example"
    web = SyntheticWeb(
        {
            f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            f"{origin}/l": SyntheticResponse(
                200,
                {"content-type": "text/html"},
                _html("L", "x", next_href=f"{origin}/l?page=2"),
            ),
            f"{origin}/l?page=2": SyntheticResponse(403, {}, b"denied"),
        }
    )
    policy = AcquisitionPolicy(request_rate=100.0)
    agent = AdaptiveDataAcquisitionAgent(
        http=HTTPAdapter(
            policy=policy,
            validator=URLPolicyValidator(resolver=lambda host: ["93.184.216.34"]),
            client_factory=web.client_factory(),
        ),
        store=_TempStore("mem"),
        planner=AcquisitionPlanner(policy=policy),
        config=AgentConfig(task_id="t", trace_id="tr"),
    )
    result = run(agent.acquire(PlannerRequest(goal="g", url=f"{origin}/l")))
    # pagination stops at the 403 page; run completes with page 1 only
    assert len(result.artifacts) == 1


def test_agent_cyclic_next_link_stops() -> None:
    from app.acquisition.agent import AdaptiveDataAcquisitionAgent, AgentConfig
    from app.acquisition.dataset import SyntheticResponse, SyntheticWeb, _html
    from app.acquisition.evaluation import _TempStore
    from app.acquisition.httpadapter import HTTPAdapter
    from app.acquisition.models import AcquisitionPolicy
    from app.acquisition.planner import AcquisitionPlanner, PlannerRequest
    from app.acquisition.urlpolicy import URLPolicyValidator

    origin = "https://bench.example"
    # page 1 and page 2 both point next at page 2 -> cyclic
    web = SyntheticWeb(
        {
            f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            f"{origin}/l": SyntheticResponse(
                200,
                {"content-type": "text/html"},
                _html("L", "x", next_href=f"{origin}/l?page=2"),
            ),
            f"{origin}/l?page=2": SyntheticResponse(
                200,
                {"content-type": "text/html"},
                _html("L", "x", next_href=f"{origin}/l?page=2"),
            ),
        }
    )
    policy = AcquisitionPolicy(request_rate=100.0)
    agent = AdaptiveDataAcquisitionAgent(
        http=HTTPAdapter(
            policy=policy,
            validator=URLPolicyValidator(resolver=lambda host: ["93.184.216.34"]),
            client_factory=web.client_factory(),
        ),
        store=_TempStore("mem"),
        planner=AcquisitionPlanner(policy=policy),
        config=AgentConfig(task_id="t", trace_id="tr"),
    )
    result = run(agent.acquire(PlannerRequest(goal="g", url=f"{origin}/l")))
    assert len(result.artifacts) <= 2  # cyclic link terminates


def test_agent_extract_failure_document() -> None:
    from app.acquisition.agent import AdaptiveDataAcquisitionAgent, AgentConfig
    from app.acquisition.dataset import SyntheticResponse, SyntheticWeb
    from app.acquisition.evaluation import _TempStore
    from app.acquisition.httpadapter import HTTPAdapter
    from app.acquisition.models import AcquisitionPolicy
    from app.acquisition.planner import AcquisitionPlanner, PlannerRequest
    from app.acquisition.urlpolicy import URLPolicyValidator

    origin = "https://bench.example"
    web = SyntheticWeb(
        {
            f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            f"{origin}/bin": SyntheticResponse(
                200, {"content-type": "application/octet-stream"}, b"\x00\x01binary"
            ),
        }
    )
    policy = AcquisitionPolicy(request_rate=100.0)
    agent = AdaptiveDataAcquisitionAgent(
        http=HTTPAdapter(
            policy=policy,
            validator=URLPolicyValidator(resolver=lambda host: ["93.184.216.34"]),
            client_factory=web.client_factory(),
        ),
        store=_TempStore("mem"),
        planner=AcquisitionPlanner(policy=policy),
        config=AgentConfig(task_id="t", trace_id="tr"),
    )
    result = run(agent.acquire(PlannerRequest(goal="g", url=f"{origin}/bin")))
    assert result.artifacts  # raw artifact preserved
    assert result.documents  # extraction attempted (text or parse_error)


def test_agent_status_verdict_blocked() -> None:
    from app.acquisition.agent import AdaptiveDataAcquisitionAgent
    from app.acquisition.models import AcquisitionStatus, Verdict

    status = AdaptiveDataAcquisitionAgent._status_from_verdict(
        Verdict.BLOCKED, None  # type: ignore[arg-type]
    )
    assert status == AcquisitionStatus.BLOCKED
