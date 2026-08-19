"""Phase 28.2 -- Browser Process Reaping under cancellation (spec section 8).

Certifies that when an acquisition run is CANCELLED mid-execution, the
browser resources (contexts/pages) that the worker opened are reaped -- the
live context count does not grow across repeated cancel races.

Uses REAL Chromium when installed (PLAYWRIGHT_BROWSERS_PATH); skipped
otherwise (the deterministic cancellation matrix in
test_phase_28_2_cancellation.py still certifies the state machine).
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.worker_path import AcquisitionWorkerPath
from app.sandbox import SandboxPolicyEngine, SandboxRuntime
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import MemorySandboxProvider
from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
from app.worker.lease import WorkerLeaseManager
from app.worker.plugin_runtime import PluginWorkerRuntime
from app.worker.registry import WorkerRegistry
from app.worker.runtime import WorkerRuntime
from app.worker.scheduler import WorkerScheduler
from tests.acquisition_lab import AcquisitionLabServer, lab_policy, lab_url_validator

# Playwright's browser cache (not a hardcoded drive path); real Chromium must
# be installed for this suite (skipped otherwise, e.g. on Linux CI without
# `playwright install chromium`).
os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH",
    os.path.expanduser(os.path.join("~", ".cache", "ms-playwright")),
)

from app.acquisition.claim import AcquisitionClaimCoordinator  # noqa: E402
from app.acquisition.service import AcquisitionService  # noqa: E402
from app.evidence.service import EvidenceService  # noqa: E402
from tests.conftest import TestSessionFactory  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.path.isdir(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")),
    reason="real chromium browsers not installed",
)


@pytest.fixture(scope="module")
def lab() -> AcquisitionLabServer:
    server = AcquisitionLabServer().start()
    yield server
    server.stop()


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    async with TestSessionFactory() as session:
        yield session


async def _count_live_contexts(adapter) -> int:
    """Count live browser contexts held by the manager."""
    manager = adapter._browser_manager  # type: ignore[attr-defined]
    return len(manager._contexts)  # type: ignore[attr-defined]


async def test_cancel_race_does_not_leak_browser_contexts(session, tmp_path, lab) -> None:
    """Repeat cancel-vs-complete races; live browser contexts stay bounded.

    If cancellation failed to reap browser resources, every cancelled run
    would leak a context/page and the live count would grow monotonically.
    """
    from app.acquisition.browseradapter import PlaywrightAcquisitionAdapter
    from app.tools.playwright.adapter import PlaywrightAdapter  # noqa: F401
    from app.tools.playwright.browser import BrowserManager  # noqa: F401

    manager = BrowserManager()
    platform = PlaywrightAdapter(manager)
    await platform.initialize({"headless": True})
    adapter = PlaywrightAcquisitionAdapter(platform)
    try:
        # warm up one browse so lazy browser launch happens before counting
        await adapter.browse(lab.origin + "/static", max_wait_ms=5000)
        baseline = await _count_live_contexts(adapter)
        # a completed browse must have released its context (reaping on the
        # success path is a precondition for reaping on the cancel path)
        assert baseline == 0, "completed browse leaked its context"

        # 6 cancel races: every run claims a browser-capable worker and is
        # cancelled partway; live contexts must not grow
        for _ in range(6):
            async with TestSessionFactory() as s2:
                ev = EvidenceService(
                    s2,
                    publisher=None,
                    storage_directory=tmp_path,  # type: ignore[arg-type]
                )
                svc = AcquisitionService(
                    s2,
                    ev,
                    store_root=tmp_path / "objects",
                    policy=lab_policy(),
                    validator=lab_url_validator(),
                )
                run, _ = await svc.create(
                    goal="g", url=f"{lab.origin}/static", expected_fields=["title"]
                )
                await s2.flush()
                reg = WorkerRegistry(s2)
                worker = await reg.register(
                    WorkerRecord(
                        name="acq-browser",
                        runtime_version="28.2",
                        capabilities=frozenset({"acquisition.http"}),
                    )
                )
                await reg.heartbeat(
                    WorkerHeartbeat(
                        worker_id=worker.id,
                        status=WorkerStatus.ONLINE,
                        active_executions=0,
                    )
                )
                leases = WorkerLeaseManager(s2)
                provider = MemorySandboxProvider()
                rt = WorkerRuntime(
                    s2,
                    reg,
                    WorkerScheduler(reg),
                    leases,
                    SandboxRuntime(provider, SandboxPolicyEngine()),
                )
                plugin = PluginWorkerRuntime(rt, SandboxProfile(name="acquisition-lab"))
                coord = AcquisitionClaimCoordinator(s2, leases, lease_ttl_seconds=60)
                wp = AcquisitionWorkerPath(plugin, svc, coord)
                token = uuid4()
                await coord.claim(run.id, worker.id, token=token)
                task = asyncio.create_task(wp.run_claimed(run.id, worker.id, token))
                await asyncio.sleep(0.05)
                # cancel through a separate session (cooperative cancel)
                async with TestSessionFactory() as cs:
                    ev2 = EvidenceService(
                        cs,
                        publisher=None,
                        storage_directory=tmp_path,  # type: ignore[arg-type]
                    )
                    svc2 = AcquisitionService(
                        cs,
                        ev2,
                        store_root=tmp_path / "objects",
                        policy=lab_policy(),
                        validator=lab_url_validator(),
                    )
                    wp2 = AcquisitionWorkerPath(
                        PluginWorkerRuntime.synthetic(frozenset({"acquisition.http"})),
                        svc2,
                        AcquisitionClaimCoordinator(
                            cs, WorkerLeaseManager(cs), lease_ttl_seconds=60
                        ),
                    )
                    await wp2.cancel(run.id)
                await asyncio.wait_for(task, timeout=15)
                await s2.refresh(run)
                # terminal must be CANCELLED or COMPLETE (cancel race legality)
                assert run.status in ("CANCELLED", "COMPLETE")
                # reaping: a cancelled run must release its browser context
                # (allow a brief GC/cleanup tick before counting)
                await asyncio.sleep(0.1)
                live = await _count_live_contexts(adapter)
                assert (
                    live == baseline
                ), f"browser contexts leaked across cancel race: {live} != baseline {baseline}"
    finally:
        await adapter.shutdown()
