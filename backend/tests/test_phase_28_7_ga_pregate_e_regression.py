"""GA PRE-GATE E regression: the typed sandbox fetch path must construct its
PolicySnapshot from a REAL AcquisitionPolicy without attribute errors.

Background: two real wiring bugs crashed EVERY genuine public fetch in the
deployed K8s worker (all earlier gates used policy-blocked URLs, so this
path never executed until GA PRE-GATE E drove a real fetch):

1. sandboxed_fetch accessed `policy.max_response_bytes`, which does NOT
   exist on AcquisitionPolicy (the per-response cap is `max_bytes`).
2. The executor was built without run_id -> SandboxRequest.run_id="" is
   rejected by pydantic (min_length=1) mid-flight, causing crash loops,
   false reclaims and stale-commit storms.

These tests lock both wirings so neither can return silently.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

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


def test_sandboxed_fetch_executor_requires_run_id() -> None:
    """Construction without run_id must FAIL FAST, not crash mid-flight."""
    policy = AcquisitionPolicy()
    with pytest.raises(ValueError, match="run_id"):
        SandboxedFetchExecutor(
            _CapturingRuntime(),
            profile=SandboxProfile(name="pregate-e"),
            policy=policy,
            validator=URLPolicyValidator(allowed_schemes=policy.allowed_schemes),
        )


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

    # Must NOT raise AttributeError (PRE-GATE E production crash #1).
    result = asyncio.run(executor.fetch("https://example.com/"))

    snap = runtime.last_request.policy
    assert snap is not None, "typed sandbox request was never built"
    # the per-response cap must come from AcquisitionPolicy.max_bytes
    assert snap.max_response_bytes == policy.max_bytes
    assert snap.timeout_seconds == policy.timeout_seconds
    assert tuple(snap.allowed_schemes) == tuple(policy.allowed_schemes)
    assert snap.user_agent == policy.user_agent
    # the captured request carries the executor's run_id (crash #2)
    assert runtime.last_request.run_id == "pregate-e-regression"
    # runtime double returns error status -> fail-closed blocked result
    assert result.status == 0
    assert result.blocked_reason == BlockReason.FAILED
