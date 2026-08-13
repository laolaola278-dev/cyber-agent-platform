"""Managed Playwright Browser and BrowserContext lifecycle."""

from typing import Any


class BrowserManager:
    """Own one Browser process and isolated BrowserContext instances."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._contexts: set[Any] = set()

    async def start(self, config: dict[str, Any] | None = None) -> None:
        if self._browser is not None:
            return
        from playwright.async_api import async_playwright

        settings = config or {}
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=bool(settings.get("headless", True))
        )

    async def new_context(self) -> Any:
        if self._browser is None:
            raise RuntimeError("BrowserManager is not started")
        context = await self._browser.new_context()
        self._contexts.add(context)
        return context

    async def close_context(self, context: Any) -> None:
        if context in self._contexts:
            await context.close()
            self._contexts.remove(context)

    async def stop(self) -> None:
        # Phase 28.4 (GATE 12): every step is failure-tolerant so that a
        # single dead context can never leave the browser process running
        # (orphan Chromium). Closing is best-effort per resource.
        for context in list(self._contexts):
            try:
                await context.close()
            except Exception:  # noqa: BLE001 -- dead context, keep closing
                pass
        self._contexts.clear()
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None
