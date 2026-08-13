"""Phase 28.4 -- sandboxed HTTP fetch executor.

Performs the ENTIRE acquisition HTTP fetch loop (client GET, redirect
re-validation, size guard, restricted-marker detection) inside the isolated
subprocess sandbox instead of the worker process. The operation is fully
self-contained (policy + validator + url are value objects, no worker
session/engine references), so it is cloudpickle-serializable and runs in a
separate OS process with a hard wall-clock timeout and killable process tree.

The application-layer URLPolicyValidator still runs in the worker FIRST; the
sandbox is a second layer of containment for the network phase, not a
replacement for SSRF validation.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.acquisition.httpadapter import HTTPFetchResult
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import SandboxRuntime


class SandboxedFetchExecutor:
    """Runs HTTPAdapter.fetch inside the isolated sandbox provider."""

    def __init__(
        self,
        runtime: SandboxRuntime,
        *,
        profile: SandboxProfile,
        policy: Any,
        validator: Any,
        run_id: str | None = None,
    ) -> None:
        self._runtime = runtime
        self._profile = profile
        self._policy = policy
        self._validator = validator
        self._run_id = run_id

    async def fetch(self, url: str) -> HTTPFetchResult:
        # Phase 28.5: OCI providers use the typed JSON protocol (no cloudpickle
        # across the container trust boundary); subprocess providers keep the
        # in-process callable path.
        if getattr(self._runtime.provider, "execute_request", None) is not None:
            return await self._fetch_typed(url)

        # self-contained: every referenced object is a value object
        policy = self._policy
        validator = self._validator

        async def operation():
            import base64

            import cloudpickle

            from app.acquisition.httpadapter import HTTPAdapter

            adapter = HTTPAdapter(policy=policy, validator=validator)
            result = await adapter.fetch(url)
            # SandboxResult.output is a dict; encode the arbitrary result via
            # cloudpickle (content includes bytes)
            return {
                "payload": base64.b64encode(cloudpickle.dumps(result)).decode("ascii")
            }

        result = await self._runtime.execute(
            self._profile, operation, execution_id=uuid4()
        )
        if result.status == "SUCCEEDED":
            import base64

            import cloudpickle

            try:
                decoded = cloudpickle.loads(
                    base64.b64decode(result.output["payload"])
                )
                if isinstance(decoded, HTTPFetchResult):
                    return decoded
            except Exception:  # noqa: BLE001 -- fall through to failure mapping
                pass
        # sandbox-level failure (timeout / process death / policy) maps to a
        # FAILED fetch result, never to a successful acquisition
        from app.acquisition.models import BlockReason

        return HTTPFetchResult(
            status=0,
            final_url=url,
            content=b"",
            content_type="",
            etag=None,
            last_modified=None,
            blocked_reason=BlockReason.FAILED,
            blocked_detail=result.error or f"sandbox status: {result.status}",
            duration_ms=0,
        )

    async def _fetch_typed(self, url: str) -> HTTPFetchResult:
        """Phase 28.5 typed path: JSON request -> OCI container -> JSON result.

        Only the URL + policy snapshot cross the boundary. The container shim
        re-applies the policy (layer 1) and the egress proxy enforces layer 2.
        """
        from uuid import uuid4

        from app.acquisition.models import BlockReason
        from app.sandbox.oci_protocol import (
            PolicySnapshot,
            SandboxRequest,
            http_fetch_result_from_dict,
        )

        request = SandboxRequest(
            operation="http_fetch",
            run_id=self._run_id or "",
            sandbox_execution_id=str(uuid4()),
            url=url,
            policy=PolicySnapshot(
                allowed_schemes=tuple(self._policy.allowed_schemes),
                user_agent=self._policy.user_agent,
                timeout_seconds=self._policy.timeout_seconds,
                max_response_bytes=self._policy.max_response_bytes,
                allow_private=getattr(self._validator, "_allow_private", False),
            ),
        )
        response = await self._runtime.execute_typed(
            self._profile,
            request,
            run_id=self._run_id or "",
        )
        if response.status == "ok" and response.result is not None:
            try:
                return http_fetch_result_from_dict(response.result)
            except Exception:  # noqa: BLE001 -- protocol drift is a failure
                pass
        return HTTPFetchResult(
            status=0,
            final_url=url,
            content=b"",
            content_type="",
            etag=None,
            last_modified=None,
            blocked_reason=BlockReason.FAILED,
            blocked_detail=response.error or "sandbox execution failed",
            duration_ms=0,
        )
