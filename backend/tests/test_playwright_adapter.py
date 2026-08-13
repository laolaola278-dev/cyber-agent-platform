"""Playwright Adapter behavior with a fake browser backend."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.tools.playwright.adapter import PlaywrightAdapter


class FakePage:
    url = "https://example.com/"

    async def goto(self, url: str, *, wait_until: str) -> object:
        self.url = url
        assert wait_until == "domcontentloaded"
        return SimpleNamespace(status=200)

    async def title(self) -> str:
        return "Example"

    async def content(self) -> str:
        return "<html>example</html>"

    async def screenshot(self, *, full_page: bool) -> bytes:
        assert full_page is True
        return b"png"

    async def wait_for_timeout(self, milliseconds: int) -> None:
        assert milliseconds == 5

    async def close(self) -> None:
        return None


class FakeContext:
    def __init__(self) -> None:
        self.closed = False

    async def new_page(self) -> FakePage:
        return FakePage()

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[FakeContext] = []

    async def new_context(self) -> FakeContext:
        context = FakeContext()
        self.contexts.append(context)
        return context

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_playwright_adapter_supports_capture_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser = FakeBrowser()

    class FakeChromium:
        async def launch(self, *, headless: bool) -> FakeBrowser:
            assert headless is True
            return browser

    class FakePlaywright:
        chromium = FakeChromium()

        async def stop(self) -> None:
            return None

    class FakeStarter:
        async def start(self) -> FakePlaywright:
            return FakePlaywright()

    async_api = ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: FakeStarter()  # type: ignore[attr-defined]
    playwright = ModuleType("playwright")
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)

    adapter = PlaywrightAdapter()
    await adapter.initialize({"headless": True})
    await adapter.open()
    assert await adapter.goto("https://example.com/") == 200
    await adapter.wait(5)
    assert await adapter.title() == "Example"
    assert await adapter.html() == "<html>example</html>"
    assert await adapter.screenshot() == b"png"
    result = await adapter.execute({"url": "https://example.com/", "method": "GET"})
    assert result["http_status"] == 200
    assert browser.contexts[-1].closed is True
    await adapter.close()
    assert browser.contexts[0].closed is True
    await adapter.shutdown()
