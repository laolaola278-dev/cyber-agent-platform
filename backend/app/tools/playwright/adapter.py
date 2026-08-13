"""Playwright-only public-web Tool Adapter."""

from typing import Any
from urllib.parse import urlparse

from app.sdk.tool_adapter import BaseToolAdapter
from app.tools.playwright.browser import BrowserManager


class PlaywrightAdapter(BaseToolAdapter):
    """Controlled browser adapter using isolated managed browser contexts."""

    def __init__(
        self,
        browser_manager: BrowserManager | None = None,
        default_config: dict[str, Any] | None = None,
    ) -> None:
        self._browser_manager = browser_manager or BrowserManager()
        self._default_config = default_config or {}
        self._context: Any = None
        self._page: Any = None

    async def initialize(self, config: dict[str, Any]) -> None:
        """Start the platform-managed Browser using explicit configuration."""

        await self._browser_manager.start({**self._default_config, **config})

    async def validate(self, payload: dict[str, Any]) -> None:
        """Reject anything outside public HTTP GET browsing before navigation."""

        url = payload.get("url")
        if not isinstance(url, str):
            raise ValueError("url is required")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("only absolute public HTTP(S) URLs are permitted")
        if payload.get("method", "GET") != "GET":
            raise ValueError("only HTTP GET is permitted")
        if any(key in payload for key in {"cookies", "headers", "credentials", "proxy"}):
            raise ValueError("cookie, header, credential, and proxy injection are forbidden")

    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Navigate after validation and return normalized public-page capture data."""

        await self.validate(payload)
        context = await self._browser_manager.new_context()
        try:
            page = await context.new_page()
            response = await page.goto(payload["url"], wait_until="domcontentloaded")
            return {
                "url": page.url,
                "http_status": response.status if response is not None else None,
                "title": await page.title(),
                "html": await page.content(),
                "screenshot": await page.screenshot(full_page=True),
            }
        finally:
            await self._browser_manager.close_context(context)

    async def open(self) -> None:
        """Open an empty browser page."""

        if self._context is not None:
            await self._browser_manager.close_context(self._context)
        self._context = await self._browser_manager.new_context()
        self._page = await self._context.new_page()

    async def goto(self, url: str) -> int | None:
        """Navigate a current page using permitted GET semantics."""

        await self.validate({"url": url})
        if self._page is None:
            await self.open()
        response = await self._page.goto(url, wait_until="domcontentloaded")
        return response.status if response is not None else None

    async def wait(self, milliseconds: int = 250) -> None:
        """Wait only for browser rendering completion."""

        if self._page is None:
            raise RuntimeError("No active page")
        await self._page.wait_for_timeout(milliseconds)

    async def html(self) -> str:
        """Return current page HTML."""

        if self._page is None:
            raise RuntimeError("No active page")
        return await self._page.content()

    async def title(self) -> str:
        """Return current page title."""

        if self._page is None:
            raise RuntimeError("No active page")
        return await self._page.title()

    async def screenshot(self) -> bytes:
        """Capture a PNG screenshot of the current page."""

        if self._page is None:
            raise RuntimeError("No active page")
        return await self._page.screenshot(full_page=True)

    async def close(self) -> None:
        """Close the current isolated BrowserContext."""

        if self._context is not None:
            await self._browser_manager.close_context(self._context)
            self._context = None
            self._page = None

    async def shutdown(self) -> None:
        """Release all Browser resources owned by this Adapter instance."""

        await self.close()
        await self._browser_manager.stop()
