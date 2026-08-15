"""Phase 28.4 -- browser acquisition executed inside the isolation sandbox.

The Playwright/Chromium process tree belongs to the sandbox execution
lifecycle, NOT to the worker daemon. A ``SandboxedBrowserExecutor`` runs the
whole browser session inside the subprocess sandbox: when the sandbox is
terminated (cancel / timeout / forced kill) the provider's process-tree kill
(Job Object + ``taskkill /F /T``) terminates the Chromium children with it, so
no orphan browser process can outlive the execution.

Capability honesty: the executor does not change the sandbox's network
policy -- browser egress is restricted only by the URLPolicyValidator at the
application layer (the subprocess provider does not implement a kernel-level
egress allowlist). Network isolation therefore remains PARTIAL on this
platform; see the Phase 28.4 certification report.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.acquisition.browseradapter import BrowserObservation
from app.acquisition.models import AcquisitionPolicy
from app.acquisition.urlpolicy import URLPolicyValidator
from app.sandbox.profile import SandboxProfile


class SandboxedBrowserExecutor:
    """Execute ``browse()`` inside the isolated execution domain."""

    def __init__(
        self,
        runtime: Any,
        *,
        profile: SandboxProfile | None = None,
        policy: AcquisitionPolicy | None = None,
        validator: URLPolicyValidator | None = None,
        run_id: str | None = None,
    ) -> None:
        self._runtime = runtime
        self._profile = profile or SandboxProfile(
            name="acquisition-browser",
            timeout_seconds=60,
            memory_mb=1024,
        )
        self._policy = policy or AcquisitionPolicy()
        self._validator = validator or URLPolicyValidator(
            allowed_schemes=self._policy.allowed_schemes
        )
        self._run_id = run_id

    async def browse(
        self,
        url: str,
        *,
        wait_for_selector: str | None = None,
        wait_network_idle_ms: int = 1500,
        max_wait_ms: int = 15_000,
    ) -> BrowserObservation:
        # application-layer SSRF gate runs first (defense in depth layer 1);
        # the browser itself then operates inside the sandbox (layer 2).
        result = self._validator.validate_url(url)
        if not result.allowed:
            return BrowserObservation(
                url=url,
                final_url=url,
                status=None,
                html="",
                title="",
                available=False,
                error=f"blocked by URL policy: {result.reason}",
            )

        # Phase 28.5: OCI providers use the typed JSON protocol
        if getattr(self._runtime.provider, "execute_request", None) is not None:
            return await self._browse_typed(
                url,
                wait_for_selector=wait_for_selector,
                wait_network_idle_ms=wait_network_idle_ms,
                max_wait_ms=max_wait_ms,
            )

        validator = self._validator

        async def operation() -> dict[str, Any]:
            from app.acquisition.browseradapter import (
                BrowserObservation as _Obs,
            )
            from app.acquisition.browseradapter import (
                PlaywrightAcquisitionAdapter,
            )
            from app.tools.playwright.adapter import PlaywrightAdapter
            from app.tools.playwright.browser import BrowserManager

            manager = BrowserManager()
            platform = PlaywrightAdapter(manager)
            await platform.initialize({"headless": True})
            adapter = PlaywrightAcquisitionAdapter(platform)
            try:
                observation = await adapter.browse(
                    url,
                    wait_for_selector=wait_for_selector,
                    wait_network_idle_ms=wait_network_idle_ms,
                    max_wait_ms=max_wait_ms,
                )
                # re-run the validator on the FINAL url too: a page may
                # redirect a sandboxed browser to a forbidden destination even
                # when the sandbox itself cannot enforce egress (no kernel
                # network policy on this platform). Defense in depth.
                final = validator.validate_url(observation.final_url)
                if observation.available and not final.allowed:
                    return _Obs(
                        url=url,
                        final_url=observation.final_url,
                        status=observation.status,
                        html="",
                        title="",
                        endpoints=[],
                        available=False,
                        error=f"final URL blocked by policy: {final.reason}",
                    ).__dict__
                return observation.__dict__
            finally:
                try:
                    await adapter.shutdown()
                except Exception:  # noqa: BLE001 -- best-effort browser close
                    pass
                # double-net: even if the graceful path aborted, the manager's
                # failure-tolerant stop closes every context/browser/driver so
                # no Chromium orphan outlives the sandbox execution
                try:
                    await manager.stop()
                except Exception:  # noqa: BLE001
                    pass

        sandbox_result = await self._runtime.execute(self._profile, operation, execution_id=uuid4())
        if sandbox_result.status == "SUCCEEDED" and sandbox_result.output:
            try:
                return BrowserObservation(**sandbox_result.output)
            except Exception:  # noqa: BLE001 -- contract drift is a failure
                pass
        if sandbox_result.status in ("CANCELLED", "TIMEOUT"):
            return BrowserObservation(
                url=url,
                final_url=url,
                status=None,
                html="",
                title="",
                available=False,
                error=f"sandbox browser terminated: {sandbox_result.status}",
            )
        return BrowserObservation(
            url=url,
            final_url=url,
            status=None,
            html="",
            title="",
            available=False,
            error=sandbox_result.error or f"sandbox status: {sandbox_result.status}",
        )

    async def _browse_typed(
        self,
        url: str,
        *,
        wait_for_selector: str | None,
        wait_network_idle_ms: int,
        max_wait_ms: int,
    ) -> BrowserObservation:
        """Phase 28.5 typed path: JSON request -> OCI container browser image."""
        from uuid import uuid4

        from app.sandbox.oci_protocol import (
            PolicySnapshot,
            SandboxRequest,
            browser_observation_from_dict,
        )

        request = SandboxRequest(
            operation="browser_browse",
            run_id=self._run_id or "",
            sandbox_execution_id=str(uuid4()),
            url=url,
            policy=PolicySnapshot(
                allowed_schemes=tuple(self._policy.allowed_schemes),
                user_agent=self._policy.user_agent,
                timeout_seconds=self._profile.timeout_seconds,
                allow_private=getattr(self._validator, "_allow_private", False),
            ),
            wait_for_selector=wait_for_selector,
            wait_network_idle_ms=wait_network_idle_ms,
            max_wait_ms=max_wait_ms,
        )
        response = await self._runtime.execute_typed(
            self._profile, request, run_id=self._run_id or ""
        )
        if response.status == "ok" and response.result is not None:
            try:
                return browser_observation_from_dict(response.result)
            except Exception:  # noqa: BLE001 -- protocol drift is a failure
                pass
        return BrowserObservation(
            url=url,
            final_url=url,
            status=None,
            html="",
            title="",
            available=False,
            error=response.error or "sandbox browser failed",
        )
