"""Phase 28.1 -- REAL Playwright certification (task: real-browser).

Runs a real Chromium (PLAYWRIGHT_BROWSERS_PATH must point to a browser
cache) against the LOCAL synthetic lab only. Certifies:

- real JavaScript rendering (content that only exists after JS runs)
- network observation (XHR/Fetch -> PublicEndpointCandidate, same-origin)
- DOM snapshot
- browser context cleanup
- timeout cleanup
- resource leak: 100 consecutive browser acquisitions -> 0 leaked contexts
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "F:/playwright-browsers")

from app.acquisition.browseradapter import PlaywrightAcquisitionAdapter  # noqa: E402
from app.tools.playwright.adapter import PlaywrightAdapter  # noqa: E402
from app.tools.playwright.browser import BrowserManager  # noqa: E402
from tests.acquisition_lab import AcquisitionLabServer  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.path.isdir(os.environ["PLAYWRIGHT_BROWSERS_PATH"]),
    reason="real chromium browsers not installed",
)


@pytest.fixture(scope="module")
def lab() -> AcquisitionLabServer:
    server = AcquisitionLabServer().start()
    yield server
    server.stop()


@pytest.fixture
async def adapter(lab: AcquisitionLabServer) -> PlaywrightAcquisitionAdapter:
    manager = BrowserManager()
    platform = PlaywrightAdapter(manager)
    await platform.initialize({"headless": True})
    adapter = PlaywrightAcquisitionAdapter(platform)
    yield adapter
    await adapter.shutdown()


# -- 1. real JavaScript rendering -------------------------------------------------

async def test_real_js_rendering(adapter: PlaywrightAcquisitionAdapter, lab) -> None:
    observation = await adapter.browse(f"{lab.origin}/dynamic")
    assert observation.available is True
    # the content ONLY exists after real JS execution
    assert "Rendered advisory" in observation.html
    assert "CVE-2026-1002" in observation.html


# -- 2. network observation: XHR/Fetch -> PublicEndpointCandidate ------------------

async def test_network_observation_xhr(adapter: PlaywrightAcquisitionAdapter, lab) -> None:
    observation = await adapter.browse(f"{lab.origin}/xhr")
    urls = [endpoint.url for endpoint in observation.endpoints]
    assert any("/api/records" in url for url in urls), urls
    states = {endpoint.state.value for endpoint in observation.endpoints}
    assert "OBSERVED" in states


# -- 3. DOM snapshot ------------------------------------------------------------------

async def test_dom_snapshot_and_title(adapter: PlaywrightAcquisitionAdapter, lab) -> None:
    observation = await adapter.browse(f"{lab.origin}/static")
    assert observation.available is True
    assert "Public advisory" in observation.html
    assert "CVE-2026-1001" in observation.html
    assert observation.title  # real <title> extracted from DOM


# -- 4. browser context cleanup after browse -------------------------------------------

async def test_browser_context_cleanup(adapter: PlaywrightAcquisitionAdapter, lab) -> None:
    manager = adapter._browser_manager  # type: ignore[attr-defined]
    for _ in range(5):
        await adapter.browse(f"{lab.origin}/static")
    # every context created by browse() must be closed afterwards
    assert len(manager._contexts) == 0  # type: ignore[attr-defined]


# -- 5. timeout cleanup (slow page must not leak a context) ------------------------------

async def test_timeout_cleanup(adapter: PlaywrightAcquisitionAdapter, lab) -> None:
    manager = adapter._browser_manager  # type: ignore[attr-defined]
    lab.set_fail_page2(True)
    try:
        # the lab stalls page 2; a short navigation timeout still cleans up
        observation = await adapter.browse(
            f"{lab.origin}/pagination?page=2", max_wait_ms=2000
        )
        assert observation.available is True or observation.error
    finally:
        lab.set_fail_page2(False)
    assert len(manager._contexts) == 0  # type: ignore[attr-defined]


# -- 6. resource leak: 100 consecutive browser acquisitions -> 0 leaked contexts --------

async def test_no_context_leak_across_100_acquisitions(
    adapter: PlaywrightAcquisitionAdapter, lab
) -> None:
    manager = adapter._browser_manager  # type: ignore[attr-defined]
    for index in range(100):
        # short wait keeps the 100-run leak loop fast; still exercises the
        # full navigate -> observe -> snapshot -> close lifecycle
        await adapter.browse(
            f"{lab.origin}/static", wait_network_idle_ms=200
        )
        if index % 25 == 0:
            assert len(manager._contexts) == 0  # type: ignore[attr-defined]
    assert len(manager._contexts) == 0  # type: ignore[attr-defined]
    assert adapter._browser_manager._browser is not None  # type: ignore[attr-defined]
