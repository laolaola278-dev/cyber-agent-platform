"""Phase 28 -- document adapter content branches + browser adapter observe/
request-handler branches (final coverage push)."""

from __future__ import annotations

import asyncio

from app.acquisition.documentadapter import DocumentAdapter


def run(coro):
    return asyncio.run(coro)


# -- document adapter content branches --------------------------------------


def test_html_parse_links_and_tables() -> None:
    html = (
        b"<html><head><title>Report</title></head><body>"
        b'<a href="https://example.com/a">A</a><a href="/b">B</a>'
        b"<table><tr><td>1</td><td>2</td></tr></table>"
        b"<p>Paragraph one</p>"
        b"</body></html>"
    )
    result = DocumentAdapter().parse(html, content_type="text/html", source_url="u")
    assert result.ok is True
    doc = result.document
    assert "Paragraph one" in doc.text
    assert any("https://example.com/a" in link for link in doc.links)
    assert len(doc.tables) == 1
    assert doc.tables[0][0] == ["1", "2"]
    assert doc.metadata["html_title"] == "Report"


def test_html_parse_skips_script_style() -> None:
    html = (
        b"<html><body><script>var x = 'secret';</script>"
        b"<style>.hidden{}</style><p>visible text</p></body></html>"
    )
    result = DocumentAdapter().parse(html, content_type="text/html", source_url="u")
    assert "visible text" in result.document.text
    assert "secret" not in result.document.text


def test_pdf_parse_no_metadata() -> None:
    from app.acquisition.dataset import _make_pdf_bytes

    result = DocumentAdapter().parse(
        _make_pdf_bytes(), content_type="application/pdf", source_url="u"
    )
    assert result.ok is True
    # metadata may be empty -> pdf_title/author default to None safely
    assert "pdf_pages" in result.document.metadata


def test_docx_parse_empty_paragraphs() -> None:
    from app.acquisition.dataset import _make_docx_bytes

    result = DocumentAdapter().parse(
        _make_docx_bytes([]),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        source_url="u",
    )
    assert result.ok is True
    assert result.document.text == ""


def test_json_parse_metadata_type() -> None:
    result = DocumentAdapter().parse(
        b'{"items": [1, 2, 3]}', content_type="application/json", source_url="u"
    )
    assert result.ok is True
    assert result.document.metadata["json_type"] == "dict"
    assert '"items"' in result.document.text


def test_text_parse_clips_long() -> None:
    result = DocumentAdapter().parse(
        b"x" * 300000, content_type="text/plain", source_url="u"
    )
    assert result.ok is True
    assert len(result.document.text) <= 200000


def test_detect_type_html_doctype() -> None:
    adapter = DocumentAdapter()
    assert adapter.detect_type(b"<!doctype html><html>", None) == "text/html"
    assert adapter.detect_type(b"\xef\xbb\xbf<html>", None) == "text/html"  # BOM


# -- browser adapter observe/request handlers --------------------------------


def test_browser_observe_with_expect_request_attribute() -> None:
    """_observe tolerates a page exposing expect_request (no crash)."""
    from app.acquisition.browseradapter import PlaywrightAcquisitionAdapter

    class PageWithExpect:
        expect_request = []

    adapter = PlaywrightAcquisitionAdapter.__new__(PlaywrightAcquisitionAdapter)
    endpoints = run(adapter._observe(PageWithExpect(), "https://bench.example/"))
    assert endpoints == []


def test_browser_on_request_handlers_direct() -> None:
    """Exercise _on_request logic through the fake page with a real browser
    adapter wiring (hidden-path / cross-origin filtering)."""
    from app.acquisition.browseradapter import PlaywrightAcquisitionAdapter
    from tests.test_phase_28_lastmile import FakePage

    adapter = PlaywrightAcquisitionAdapter.__new__(PlaywrightAcquisitionAdapter)
    adapter._platform = object()
    adapter._available = True

    class Manager:
        async def new_context(self):
            return FakeContext()

        async def close_context(self, context):
            return None

        async def stop(self):
            return None

    class FakeContext:
        async def new_page(self):
            return FakePage(None)

    adapter._browser_manager = Manager()
    observation = run(adapter.browse("https://bench.example/app"))
    assert observation.available is True
    # only same-origin public XHR/Fetch recorded
    urls = {e.url for e in observation.endpoints}
    assert urls == {"https://bench.example/api/v1/items"}
