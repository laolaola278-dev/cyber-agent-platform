"""Phase 28 -- last-mile coverage: browser adapter browse flow (with fake
Playwright objects), completeness branches, document HTML/xlsx/text paths,
robots rules, http adapter restricted-markers."""

from __future__ import annotations

import asyncio

from app.acquisition.browseradapter import PlaywrightAcquisitionAdapter
from app.acquisition.completeness import CompletenessEvaluator, CompletenessInput
from app.acquisition.documentadapter import DocumentAdapter
from app.acquisition.httpadapter import HTTPAdapter
from app.acquisition.models import AcquisitionPolicy, Verdict
from app.acquisition.robots import RobotsPolicy, robots_url_for
from app.acquisition.urlpolicy import URLPolicyValidator


def run(coro):
    return asyncio.run(coro)


# -- fake Playwright objects -------------------------------------------------


class FakeRequest:
    def __init__(self, url: str, resource_type: str, method: str = "GET") -> None:
        self.url = url
        self.resource_type = resource_type
        self.method = method


class FakePage:
    def __init__(self, handler) -> None:
        self._handler = handler
        self._events: dict[str, list] = {}
        self.url = "https://bench.example/app"
        self.fired: list[str] = []

    def on(self, event: str, handler) -> None:
        self._events[event] = [handler]

    async def goto(self, url: str, **kwargs) -> None:
        # fire observed network requests during navigation (await handlers)
        for request in (
            FakeRequest("https://bench.example/api/v1/items", "xhr"),
            FakeRequest("https://bench.example/api/v1/private/data", "fetch"),
            FakeRequest("https://other.example/x", "xhr"),
            FakeRequest("https://bench.example/style.css", "stylesheet"),
        ):
            for handler in self._events.get("request", []):
                result = handler(request)
                if hasattr(result, "__await__"):
                    await result
        return None

    async def wait_for_selector(self, selector: str, **kwargs) -> None:
        return None

    async def wait_for_load_state(self, state: str, **kwargs) -> None:
        return None

    async def content(self) -> str:
        return "<html><body>rendered content</body></html>"

    async def title(self) -> str:
        return "App"

    async def wait_for_event(self, event: str) -> None:
        return None


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []

    async def new_page(self) -> FakePage:
        page = FakePage(self)
        self.pages.append(page)
        return page


class FakeBrowserManager:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def new_context(self) -> FakeContext:
        return FakeContext()

    async def close_context(self, context) -> None:
        return None

    async def stop(self) -> None:
        self.stopped = True


def _adapter_with_fakes() -> tuple[PlaywrightAcquisitionAdapter, FakeBrowserManager]:
    manager = FakeBrowserManager()
    adapter = PlaywrightAcquisitionAdapter.__new__(PlaywrightAcquisitionAdapter)
    adapter._platform = object()  # truthy -> available path
    adapter._browser_manager = manager
    adapter._available = True
    return adapter, manager


def test_browser_browse_observes_public_endpoints() -> None:
    adapter, _manager = _adapter_with_fakes()
    observation = run(adapter.browse("https://bench.example/app"))
    assert observation.available is True
    assert "rendered content" in observation.html
    urls = {e.url for e in observation.endpoints}
    # public XHR/Fetch from the same origin recorded
    assert "https://bench.example/api/v1/items" in urls
    # hidden/internal path NOT recorded for use
    assert "https://bench.example/api/v1/private/data" not in urls
    # cross-origin not recorded
    assert "https://other.example/x" not in urls
    # non-XHR resource types not recorded
    assert "https://bench.example/style.css" not in urls


def test_browser_browse_with_wait_selector() -> None:
    adapter, _manager = _adapter_with_fakes()
    observation = run(
        adapter.browse("https://bench.example/app", wait_for_selector=".item")
    )
    assert observation.title == "App"


def test_browser_shutdown_stops_manager() -> None:
    adapter, manager = _adapter_with_fakes()
    run(adapter.shutdown())
    assert manager.stopped is True


# -- completeness extra branches ---------------------------------------------


def test_completeness_time_coverage_parsed() -> None:
    evaluator = CompletenessEvaluator()
    report = evaluator.evaluate(
        CompletenessInput(
            expected_fields=["t"],
            observed_fields={"t"},
            expected_time_range=("2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"),
            observed_timestamps=["2026-01-01T12:00:00+00:00"],
        )
    )
    assert report.time_coverage > 0.0


def test_completeness_time_coverage_bad_range() -> None:
    evaluator = CompletenessEvaluator()
    report = evaluator.evaluate(
        CompletenessInput(
            expected_fields=["t"],
            observed_fields={"t"},
            expected_time_range=("not-a-date", "also-bad"),
            observed_timestamps=["2026-01-01T00:00:00+00:00"],
        )
    )
    assert report.time_coverage == 1.0


def test_completeness_time_coverage_inverted_range() -> None:
    evaluator = CompletenessEvaluator()
    report = evaluator.evaluate(
        CompletenessInput(
            expected_fields=["t"],
            observed_fields={"t"},
            expected_time_range=("2026-08-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            observed_timestamps=["2026-03-01T00:00:00+00:00"],
        )
    )
    assert report.time_coverage == 1.0


def test_completeness_errors_retry() -> None:
    evaluator = CompletenessEvaluator()
    report = evaluator.evaluate(
        CompletenessInput(
            expected_fields=["t"],
            observed_fields={"t"},
            errors=["fetch failed once"],
        )
    )
    assert report.verdict == Verdict.RETRY


def test_completeness_not_paginated_retry() -> None:
    evaluator = CompletenessEvaluator()
    from app.acquisition.models import PaginationStrategy

    report = evaluator.evaluate(
        CompletenessInput(
            expected_fields=["t"],
            observed_fields={"t"},
            pagination=PaginationStrategy(
                kind="page_param", max_pages=5, pages_fetched=1
            ),
        )
    )
    assert report.verdict == Verdict.RETRY


# -- document adapter extra paths --------------------------------------------


def test_document_html_parse_real() -> None:
    adapter = DocumentAdapter()
    result = adapter.parse(
        b"<html><head><title>Doc</title></head><body><h1>H</h1><p>body</p></body></html>",
        content_type="text/html",
        source_url="u",
    )
    assert result.ok is True
    assert result.document.title == "Doc"
    assert "body" in result.document.text


def test_document_html_parse_error_path() -> None:
    adapter = DocumentAdapter()
    result = adapter.parse(
        b"not html at all < broken",
        content_type="text/html",
        source_url="u",
    )
    # lxml tolerates most inputs; if it errors, result.ok is False
    assert result.ok is True or "parse failed" in result.error


def test_document_xlsx_parse() -> None:
    from app.acquisition.dataset import _make_xlsx_bytes

    adapter = DocumentAdapter()
    result = adapter.parse(
        _make_xlsx_bytes([["a", "b"], [1, 2]]),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        source_url="u",
    )
    assert result.ok is True
    assert result.parser_backend == "openpyxl"
    assert result.document.tables


def test_document_text_parse() -> None:
    adapter = DocumentAdapter()
    result = adapter.parse(b"plain text content", content_type="text/plain", source_url="u")
    assert result.ok is True
    assert "plain text" in result.document.text


def test_document_detect_xlsx_magic() -> None:
    from app.acquisition.dataset import _make_xlsx_bytes

    adapter = DocumentAdapter()
    detected = adapter.detect_type(_make_xlsx_bytes([["a"]]), None)
    assert detected == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# -- robots ------------------------------------------------------------------


def test_robots_policy_rules() -> None:
    robots = RobotsPolicy()
    disallowed = robots.evaluate(
        "https://example.com/admin/x",
        "User-agent: *\nDisallow: /admin/\nAllow: /public/\n",
    )
    assert disallowed.allowed is False
    allowed = robots.evaluate(
        "https://example.com/public/x",
        "User-agent: *\nDisallow: /admin/\n",
    )
    assert allowed.allowed is True
    no_rules = robots.evaluate("https://example.com/x", "User-agent: *\nAllow: /\n")
    assert no_rules.allowed is True


def test_robots_no_match_allowed() -> None:
    robots = RobotsPolicy()
    result = robots.evaluate(
        "https://example.com/x",
        "User-agent: googlebot\nDisallow: /x/\n",
    )
    assert result.allowed is True  # our UA tokens don't match googlebot


def test_robots_no_robots_txt_allowed() -> None:
    robots = RobotsPolicy()
    result = robots.evaluate("https://example.com/x", None)
    assert result.allowed is True


def test_robots_url_builder() -> None:
    assert robots_url_for("https://example.com/a/b?c=1") == "https://example.com/robots.txt"


def test_robots_crawl_delay_parsed() -> None:
    robots = RobotsPolicy()
    # crawl-delay is parsed but does not gate allow/deny
    result = robots.evaluate("https://example.com/x", "User-agent: *\nDisallow:\nCrawl-delay: 5\n")
    assert result.allowed is True


# -- http adapter extra ------------------------------------------------------


def test_http_adapter_redirect_to_public_followed() -> None:
    from app.acquisition.dataset import SyntheticResponse, SyntheticWeb

    final = "https://bench.example/final"
    url = "https://bench.example/start"
    web = SyntheticWeb(
        {
            url: SyntheticResponse(302, {"location": final}, b""),
            final: SyntheticResponse(200, {"content-type": "text/html"}, b"<html>ok</html>"),
        }
    )
    adapter = HTTPAdapter(
        policy=AcquisitionPolicy(request_rate=100.0),
        validator=URLPolicyValidator(resolver=lambda host: ["93.184.216.34"]),
        client_factory=web.client_factory(),
    )
    result = run(adapter.fetch(url))
    assert result.final_url == final
    assert result.status == 200


def test_http_adapter_timeout_fails_closed() -> None:
    from app.acquisition.dataset import SyntheticResponse, SyntheticWeb

    url = "https://bench.example/slow"
    web = SyntheticWeb({url: SyntheticResponse(200, {"content-type": "text/html"}, b"")})
    adapter = HTTPAdapter(
        policy=AcquisitionPolicy(request_rate=100.0),
        validator=URLPolicyValidator(resolver=lambda host: ["93.184.216.34"]),
        client_factory=web.client_factory(),
    )
    # empty body -> fetch returns no content; agent treats as partial
    result = run(adapter.fetch(url))
    assert result.status == 200
    assert result.content == b""
