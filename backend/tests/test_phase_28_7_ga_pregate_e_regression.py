"""GA PRE-GATE E regression: the typed sandbox fetch path must construct its
PolicySnapshot from a REAL AcquisitionPolicy without attribute errors.

Background: `sandboxed_fetch` accessed `policy.max_response_bytes`, which does
NOT exist on AcquisitionPolicy (the per-response cap is `max_bytes`). Every
K8s certification gate used policy-blocked URLs (127.0.0.1:9), so the real
fetch path never executed until GA PRE-GATE E drove a genuine public fetch
through the deployed worker -- which crashed every run with
AttributeError and triggered a reclaim/stale-commit storm. This test locks
the wiring so the crash can never return silently.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.acquisition.models import AcquisitionPolicy, BlockReason
from app.acquisition.sandboxed_fetch import SandboxedFetchExecutor
from app.acquisition.urlpolicy import URLPolicyValidator
from app.sandbox.profile import SandboxProfile


class _Provider:
    """Marks the runtime as a typed-protocol provider (OCI path)."""

    execute_request = object()


class _CapturingRuntime:
    """Minimal SandboxRuntime double: captures the typed request."""

    def __init__(self) -> None:
        self.provider = _Provider()
        self.last_request: Any = None

    async def execute_typed(self, profile: Any, request: Any, *, run_id: str = "") -> Any:
        self.last_request = request

        class _Resp:
            status = "error"  # force the fail-closed HTTPFetchResult path
            result = None
            error = "captured"

        return _Resp()


def test_sandboxed_fetch_policy_snapshot_matches_acquisition_policy() -> None:
    policy = AcquisitionPolicy()  # production defaults -- no extra attrs
    runtime = _CapturingRuntime()
    executor = SandboxedFetchExecutor(
        runtime,
        profile=SandboxProfile(name="pregate-e"),
        policy=policy,
        validator=URLPolicyValidator(allowed_schemes=policy.allowed_schemes),
        run_id="pregate-e-regression",
    )

    # Must NOT raise AttributeError (the PRE-GATE E production crash).
    result = asyncio.run(executor.fetch("https://example.com/"))

    snap = runtime.last_request.policy
    assert snap is not None, "typed sandbox request was never built"
    # the per-response cap must come from AcquisitionPolicy.max_bytes
    assert snap.max_response_bytes == policy.max_bytes
    assert snap.timeout_seconds == policy.timeout_seconds
    assert tuple(snap.allowed_schemes) == tuple(policy.allowed_schemes)
    assert snap.user_agent == policy.user_agent
    # runtime double returns error status -> fail-closed blocked result
    assert result.status == 0
    assert result.blocked_reason == BlockReason.FAILED
