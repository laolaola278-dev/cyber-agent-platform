"""Phase 28 -- coverage tests for under-covered acquisition modules:
browseradapter (synthetic contract), observability, capabilities seed,
document adapter branches, pagination, candidates, agent pagination path.
"""

from __future__ import annotations

import asyncio

from app.acquisition.browseradapter import PlaywrightAcquisitionAdapter
from app.acquisition.candidates import _is_plausible_ip, extract_candidates
from app.acquisition.capabilities import ACQUISITION_CAPABILITIES
from app.acquisition.dataset import (
    SyntheticResponse,
    SyntheticWeb,
    _html,
    _make_docx_bytes,
    _make_pdf_bytes,
)
from app.acquisition.dedup import DuplicateRegistry
from app.acquisition.documentadapter import DocumentAdapter
from app.acquisition.models import AcquisitionPolicy
from app.acquisition.observability import RunTracker
from app.acquisition.pagination import (
    detect_strategy,
    extract_page_links,
    next_page_url,
)
from app.acquisition.planner import AcquisitionPlanner, PlannerRequest


def run(coro):
    return asyncio.run(coro)


# -- browser adapter --------------------------------------------------------


def test_browser_adapter_unavailable_synthetic() -> None:
    adapter = PlaywrightAcquisitionAdapter.__new__(PlaywrightAcquisitionAdapter)
    adapter._platform = None
    adapter._browser_manager = None
    adapter._available = False
    observation = run(adapter.browse("https://example.com/"))
    assert observation.available is False
    assert "synthetic" in observation.error
    run(adapter.shutdown())  # no-op with no manager


def test_browser_adapter_safe_host() -> None:
    from app.acquisition.browseradapter import _safe_host

    assert _safe_host("https://Example.COM:443/x") == "example.com"
    assert _safe_host("http://[::1]:8080/") == "::1"


# -- observability ----------------------------------------------------------


def test_run_tracker_lifecycle() -> None:
    tracker = RunTracker(run_id="r1", trace_id="t1", goal="g")
    tracker.start_step("fetch", "fetch", "https://example.com/")
    tracker.finish_step(
        "fetch",
        status="SUCCESS",
        duration_ms=10,
        bytes_received=100,
        replanned=False,
        detail="ok",
    )
    tracker.add_request(bytes_received=100)
    tracker.add_evidence("abc123")
    tracker.mark_visited("https://example.com/")
    tracker.mark_visited("https://example.com/")  # dedup
    record = tracker.finalize(
        status="COMPLETE",
        strategy="static",
        source_type="STATIC_HTML",
        completeness_score=0.9,
        blocked_reason="NONE",
        replans=1,
        retries=0,
    )
    assert record.total_requests == 1
    assert record.total_bytes == 100
    assert record.evidence_hashes == ["abc123"]
    assert record.urls_visited == ["https://example.com/"]
    assert record.total_duration_ms >= 0
    data = record.to_dict()
    assert data["status"] == "COMPLETE"
    assert data["steps"][0]["step_id"] == "fetch"


def test_run_tracker_unfinished_step() -> None:
    tracker = RunTracker(run_id="r", trace_id="t", goal="g")
    tracker.start_step("a", "fetch", "https://example.com/")
    # no finish_step -> step stays RUNNING
    assert tracker.record.steps[0].status == "RUNNING"


# -- capabilities -----------------------------------------------------------


def test_acquisition_capabilities_defined() -> None:
    for name in (
        "acquisition.http",
        "acquisition.browser",
        "acquisition.document",
        "acquisition.extract",
        "acquisition.paginate",
        "acquisition.discover",
        "acquisition.verify",
        "acquisition.public",
    ):
        assert name in ACQUISITION_CAPABILITIES


# -- document adapter -------------------------------------------------------


def test_document_detect_type_magic_bytes() -> None:
    adapter = DocumentAdapter()
    assert adapter.detect_type(b"%PDF-1.4", None) == "application/pdf"
    assert adapter.detect_type(_make_docx_bytes(["x"]), None) == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert adapter.detect_type(b"<html><body>x</body></html>", None) == "text/html"
    assert adapter.detect_type(b'{"a": 1}', None) == "application/json"
    assert adapter.detect_type(b"plain text", None) == "text/plain"


def test_document_parse_pdf_real() -> None:
    adapter = DocumentAdapter()
    result = adapter.parse(_make_pdf_bytes(), content_type="application/pdf", source_url="u")
    assert result.ok is True
    assert result.parser_backend == "pypdf"
    assert result.document is not None


def test_document_parse_docx_real() -> None:
    adapter = DocumentAdapter()
    result = adapter.parse(
        _make_docx_bytes(["Hello", "World"]),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        source_url="u",
    )
    assert result.ok is True
    assert result.parser_backend == "python-docx"
    assert "Hello" in result.document.text


def test_document_parse_json_invalid() -> None:
    adapter = DocumentAdapter()
    result = adapter.parse(b"{bad json", content_type="application/json", source_url="u")
    assert result.ok is False
    assert "invalid JSON" in result.error


def test_document_unknown_content_type_falls_back_to_text() -> None:
    adapter = DocumentAdapter()
    assert adapter.detect_type(b"x", "application/x-unknown") == "text/plain"


# -- pagination -------------------------------------------------------------


def test_pagination_extract_page_links() -> None:
    html = '<a href="/a">1</a><a href="/b?page=2" rel="next">next</a>'
    links = extract_page_links(html, "https://example.com/")
    assert links.hrefs == ["/a", "/b?page=2"]
    assert links.next_href == "/b?page=2"


def test_pagination_detect_strategies() -> None:
    # next link
    strategy = detect_strategy(
        page_url="https://example.com/list",
        html='<a href="/list?page=2" rel="next">next</a>',
    )
    assert strategy.kind == "next_link"
    # page param in URL
    strategy = detect_strategy(page_url="https://example.com/list?page=3", html="<a>x</a>")
    assert strategy.kind == "page_param"
    assert strategy.page_param == "page"
    # cursor
    strategy = detect_strategy(page_url="https://example.com/list?cursor=abc", html="<a>x</a>")
    assert strategy.kind == "cursor"
    # load more marker
    strategy = detect_strategy(page_url="https://example.com/f", html="load more")
    assert strategy.kind == "load_more"
    # none
    strategy = detect_strategy(page_url="https://example.com/f", html="plain")
    assert strategy.kind == "none"


def test_pagination_next_page_url_bounded() -> None:
    strategy = detect_strategy(page_url="https://example.com/l?page=1", html="<a>x</a>")
    assert next_page_url(strategy, "https://example.com/l?page=1", 1) is not None
    assert next_page_url(strategy, "https://example.com/l?page=1", 99) is None


def test_pagination_budgets_defaults() -> None:
    strategy = detect_strategy(page_url="https://example.com/l?page=1", html="<a>x</a>")
    assert strategy.max_pages > 0
    assert strategy.max_records > 0
    assert strategy.max_requests > 0
    assert strategy.max_duration > 0


# -- candidates -------------------------------------------------------------


def test_candidates_extraction() -> None:
    text = (
        "CVE-2024-1234 and 192.168.0.5 and 8.8.8.8 and "
        "aaabbbcccdddeeefff000111222333444555666777888999000"
        "aaabbbcccdddeeefff000111222333444555666777888999"
    )
    bundle = extract_candidates(text, evidence_id="e1", source_url="u", title="T")
    assert any(c.external_id == "CVE-2024-1234" for c in bundle.knowledge)
    ips = {e.value for e in bundle.entities if e.entity_type == "ip"}
    assert "8.8.8.8" in ips
    assert "192.168.0.5" in ips  # entities are candidates; validation is downstream
    assert any(f.fact_type == "document_title" for f in bundle.facts)


def test_candidates_plausible_ip() -> None:
    assert _is_plausible_ip("8.8.8.8") is True
    assert _is_plausible_ip("999.1.1.1") is False
    assert _is_plausible_ip("1.2.3") is False
    assert _is_plausible_ip("1.2.3.4.5") is False


def test_candidates_no_duplicate_cves() -> None:
    text = "CVE-2024-1234 ... CVE-2024-1234 again"
    bundle = extract_candidates(text, evidence_id=None, source_url="u")
    assert len([c for c in bundle.knowledge if c.external_id == "CVE-2024-1234"]) == 1


# -- dedup reset ------------------------------------------------------------


def test_dedup_reset() -> None:
    registry = DuplicateRegistry()
    registry.check("https://example.com/a", "h1")
    registry.reset()
    assert registry.seen_urls == {}
    assert registry.duplicates == []


# -- agent pagination path --------------------------------------------------


def test_agent_pagination_next_link() -> None:
    from app.acquisition.agent import AdaptiveDataAcquisitionAgent, AgentConfig
    from app.acquisition.evaluation import _TempStore
    from app.acquisition.httpadapter import HTTPAdapter
    from app.acquisition.urlpolicy import URLPolicyValidator

    origin = "https://bench.example"
    routes = {
        f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
        f"{origin}/list": SyntheticResponse(
            200,
            {"content-type": "text/html"},
            _html("L", "item", next_href=f"{origin}/list?page=2"),
        ),
        f"{origin}/list?page=2": SyntheticResponse(
            200,
            {"content-type": "text/html"},
            _html("L", "item", next_href=f"{origin}/list?page=3"),
        ),
        f"{origin}/list?page=3": SyntheticResponse(
            200, {"content-type": "text/html"}, _html("L", "item")
        ),
    }
    web = SyntheticWeb(routes=routes)
    policy = AcquisitionPolicy(request_rate=100.0, max_pages=5)
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
    result = run(agent.acquire(PlannerRequest(goal="g", url=f"{origin}/list")))
    assert len(result.artifacts) >= 3  # list + page2 + page3
    assert any("page:" in s for s in result.strategy_history)
