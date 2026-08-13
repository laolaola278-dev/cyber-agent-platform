"""Phase 28 -- Dynamic Page Acquisition via the platform Playwright tool (spec 9/10).

Wraps the existing platform ``app.tools.playwright`` adapter (which already
restricts to public HTTP(S) GET without cookie/header/credential/proxy
injection). Phase 28 adds:

  * bounded wait conditions (wait_for_selector, domcontentloaded)
  * DOM snapshot after rendering
  * network observation: only requests the page's own front-end issues
    (XHR/Fetch) are recorded as PublicEndpointCandidates (OBSERVED)
  * never touches hidden endpoints; never modifies requests to bypass
    permissions

If Playwright is unavailable at runtime the adapter reports
``available=False`` (synthetic contract) instead of faking success.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.acquisition.models import PublicEndpointCandidate

_JSON_CT_RE = re.compile(r"application/json|text/json|\+json", re.I)
_HIDDEN_PATH_MARKERS = (
    "/admin",
    "/internal",
    "/debug",
    "/api/v1/private",
    "/manage",
    "/console",
)


@dataclass
class BrowserObservation:
    url: str
    final_url: str
    status: int | None
    html: str
    title: str
    endpoints: list[PublicEndpointCandidate] = field(default_factory=list)
    available: bool = True
    error: str = ""


class PlaywrightAcquisitionAdapter:
    """Controlled browser acquisition (wraps platform PlaywrightAdapter)."""

    def __init__(self, base_adapter: Any | None = None) -> None:
        try:
            from app.tools.playwright.adapter import PlaywrightAdapter as _PlatformAdapter
            from app.tools.playwright.browser import BrowserManager

            self._platform = base_adapter or _PlatformAdapter()
            self._browser_manager = (
                base_adapter._browser_manager if base_adapter else BrowserManager()
            )
            self._available = True
        except Exception:  # noqa: BLE001 -- synthetic contract, no fake success
            self._platform = None
            self._browser_manager = None
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def _observe(self, page: Any, origin: str) -> list[PublicEndpointCandidate]:
        """Record XHR/Fetch requests issued by the page's own front-end."""
        observed: dict[str, PublicEndpointCandidate] = {}
        try:
            for _request in page.expect_request or []:
                pass
        except Exception:  # noqa: BLE001
            pass
        # Use a one-shot listener attached during navigation (see browse()).
        return list(observed.values())

    async def browse(
        self,
        url: str,
        *,
        wait_for_selector: str | None = None,
        wait_network_idle_ms: int = 1500,
        max_wait_ms: int = 15_000,
    ) -> BrowserObservation:
        """Navigate a public page and capture rendered DOM + observed endpoints."""
        if not self._available or self._platform is None:
            return BrowserObservation(
                url=url,
                final_url=url,
                status=None,
                html="",
                title="",
                available=False,
                error="playwright unavailable (synthetic contract)",
            )

        endpoints: list[PublicEndpointCandidate] = []

        async def _on_request(request: Any) -> None:
            req_url = request.url
            if not req_url.startswith("http"):
                return
            # only XHR/Fetch resource types issued by the page itself
            if request.resource_type not in ("xhr", "fetch"):
                return
            parsed = _safe_host(req_url)
            origin = _safe_host(url)
            if parsed != origin:
                return  # cross-origin observed requests are noted, not reused
            for marker in _HIDDEN_PATH_MARKERS:
                if marker in req_url.split("?")[0]:
                    return  # hidden/internal endpoints are never recorded for use
            endpoints.append(
                PublicEndpointCandidate(
                    url=req_url,
                    method=request.method or "GET",
                    state=PublicEndpointCandidate.state.OBSERVED,
                    observed_from=url,
                )
            )

        context = await self._browser_manager.new_context()
        try:
            page = await context.new_page()
            page.on("request", _on_request)
            await page.goto(url, wait_until="domcontentloaded")
            if wait_for_selector:
                try:
                    await page.wait_for_selector(wait_for_selector, timeout=max_wait_ms)
                except Exception:  # noqa: BLE001
                    pass
            elif wait_network_idle_ms:
                try:
                    await page.wait_for_load_state("networkidle", timeout=wait_network_idle_ms)
                except Exception:  # noqa: BLE001
                    pass
            html = await page.content()
            title = await page.title()
            final_url = page.url
            status = None
            try:
                # the primary document response usually arrives during goto();
                # waiting for it is best-effort and MUST be time-bounded
                response = await page.wait_for_event("response", timeout=2000)
                status = response.status if response else None
            except Exception:  # noqa: BLE001
                pass
            return BrowserObservation(
                url=url,
                final_url=final_url,
                status=status,
                html=html,
                title=title,
                endpoints=endpoints,
                available=True,
            )
        finally:
            await self._browser_manager.close_context(context)

    async def shutdown(self) -> None:
        if self._browser_manager is not None:
            await self._browser_manager.stop()


def _safe_host(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return (parsed.hostname or "").lower()
