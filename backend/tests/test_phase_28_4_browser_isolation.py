"""Phase 28.4 -- browser lifecycle isolation (GATE 12).

The Playwright/Chromium process tree must belong to the sandbox execution
lifecycle: when the sandbox is terminated (cancel / timeout / forced kill) the
browser children die with it, and repeated runs leave NO orphan Chromium.

Requires real Chromium (PLAYWRIGHT_BROWSERS_PATH) -- skipped when absent, and
the certification gate is reported accordingly (no fake PASS).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from app.acquisition.sandboxed_browser import SandboxedBrowserExecutor
from app.sandbox.policy import SandboxPolicyEngine
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.subprocess_provider import SubprocessSandboxProvider
from tests.acquisition_lab import AcquisitionLabServer
from tests.acquisition_lab import lab_policy, lab_url_validator

# Windows-only: the subprocess browser executor + real-Chromium process-tree
# reaping in this suite uses Windows PowerShell to enumerate Chromium PIDs
# (the OCI container browser path is certified separately in Phase 28.5).
# PLAYWRIGHT_BROWSERS_PATH defaults to the Playwright cache dir, not a
# hardcoded drive path.
os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH",
    os.path.expanduser(os.path.join("~", ".cache", "ms-playwright")),
)

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.skipif(
        sys.platform != "win32",
        reason="subprocess browser isolation is Windows-only (OCI browser path is 28.5)",
    ),
    pytest.mark.skipif(
        not os.path.isdir(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")),
        reason="real chromium browsers not installed",
    ),
]


def _chromium_pids() -> set[int]:
    """PIDs of browser processes owned by the PLAYWRIGHT browser tree.

    Filters by command line containing the playwright browsers path so the
    user's own Google Chrome is never counted as a sandbox orphan.
    """
    if sys.platform != "win32":
        return set()
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { ($_.Name -match 'chrome|chromium|headless') -and "
                    "($_.CommandLine -like '*playwright-browsers*') } | "
                    "ForEach-Object { $_.ProcessId }"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:  # noqa: BLE001
        return set()
    pids: set[int] = set()
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.add(int(line))
    return pids


@pytest.fixture(scope="module")
def lab() -> AcquisitionLabServer:
    server = AcquisitionLabServer().start()
    yield server
    server.stop()


@pytest.fixture
def executor(lab: AcquisitionLabServer) -> SandboxedBrowserExecutor:
    runtime = SandboxRuntime(SubprocessSandboxProvider(), SandboxPolicyEngine())
    profile = SandboxProfile(
        name="browser-test",
        timeout_seconds=90,
        memory_mb=1024,
    )
    return SandboxedBrowserExecutor(
        runtime,
        profile=profile,
        policy=lab_policy(),
        validator=lab_url_validator(),
    )


@pytest.mark.asyncio
async def test_browser_runs_inside_sandbox_and_renders(executor, lab) -> None:
    observation = await executor.browse(f"{lab.origin}/dynamic")
    assert observation.available is True
    # real JS rendering happened inside the sandbox subprocess
    assert "Rendered advisory" in observation.html
    assert "CVE-2026-1002" in observation.html


@pytest.mark.asyncio
async def test_terminate_kills_browser_process_tree(executor, lab) -> None:
    """A browser session that never returns must be hard-killed with its
    Chromium children; nothing may survive termination."""
    runtime = executor._runtime
    execution_id = uuid4()

    async def hang_browser() -> dict:
        # start a REAL chromium session then block forever: the sandbox
        # process tree (python + chromium) must die on terminate()
        from app.tools.playwright.adapter import PlaywrightAdapter
        from app.tools.playwright.browser import BrowserManager

        manager = BrowserManager()
        platform = PlaywrightAdapter(manager)
        await platform.initialize({"headless": True})
        # chromium is running now; never return until killed
        await asyncio.sleep(300)
        return {}

    task = asyncio.create_task(
        runtime.execute(
            executor._profile, hang_browser, execution_id=execution_id
        )
    )
    await asyncio.sleep(8.0)  # let chromium spawn inside the sandbox
    spawned = _chromium_pids()
    assert spawned, "no chromium process observed during sandbox browser run"

    terminated = await runtime.terminate(execution_id)
    assert terminated is True
    try:
        result = await asyncio.wait_for(task, timeout=10)
        assert result.status in ("CANCELLED", "FAILED")
        assert result.terminated is True
    except asyncio.TimeoutError:  # pragma: no cover
        pytest.fail("sandbox browser did not terminate")

    # give the OS a moment to reap the tree, then assert NO chromium remains
    await asyncio.sleep(2.0)
    remaining = _chromium_pids()
    # allowed to differ only by removing the spawned set entirely
    assert not remaining & spawned, (
        f"orphan chromium survived termination: {remaining & spawned}"
    )


@pytest.mark.asyncio
async def test_repeated_browser_runs_leave_no_orphans(executor, lab) -> None:
    for _ in range(3):
        observation = await executor.browse(f"{lab.origin}/static")
        assert observation.available is True
    # graceful chromium shutdown may take a few seconds; poll until gone
    import time as _t

    deadline = _t.monotonic() + 20.0
    while True:
        remaining = _chromium_pids()
        if not remaining:
            break
        if _t.monotonic() > deadline:  # pragma: no cover
            pytest.fail(f"orphan chromium after repeated runs: {remaining}")
        await asyncio.sleep(0.5)
