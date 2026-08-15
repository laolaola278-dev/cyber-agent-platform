"""Phase 28.4 -- sandbox secret injection & redaction (GATE 13).

Secrets must:
  * never be written to the DB;
  * never appear in logs;
  * reach the sandbox ONLY as in-memory stdin payload (not env / filesystem);
  * disappear with the sandbox process;
  * fail closed when the sandbox lacks secret capability;
  * never be echoed in sandbox results/errors.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.sandbox.policy import SandboxPolicyEngine
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import MemorySandboxProvider, SandboxRuntime
from app.sandbox.subprocess_provider import SubprocessSandboxProvider

pytestmark = [pytest.mark.sandbox]

SECRET_VALUE = "hunter2-0f1e2d3c4b5a6e7f8a9b0c1d2e3f4a5b"


@pytest.fixture
def runtime() -> SandboxRuntime:
    return SandboxRuntime(SubprocessSandboxProvider(), SandboxPolicyEngine())


def _profile(**overrides) -> SandboxProfile:
    kwargs = dict(
        name="sec-test",
        timeout_seconds=30,
        memory_mb=256,
        secret_references=("cap-db-pass", "cap-api-key"),
    )
    kwargs.update(overrides)
    return SandboxProfile(**kwargs)


@pytest.mark.asyncio
async def test_secret_reaches_sandbox_in_memory_only(runtime) -> None:
    async def op():
        import os as _os

        from app.sandbox.inject import get_secret

        leaked_env = any(SECRET_VALUE in str(v) for v in _os.environ.values())
        return {
            "pass_len": len(get_secret("cap-db-pass")),
            "key_len": len(get_secret("cap-api-key")),
            "leaked_in_env": leaked_env,
        }

    result = await runtime.execute(
        _profile(),
        op,
        execution_id=uuid4(),
        secrets={"cap-db-pass": SECRET_VALUE, "cap-api-key": "ak-" + SECRET_VALUE[:8]},
    )
    assert result.status == "SUCCEEDED"
    assert result.output["pass_len"] == len(SECRET_VALUE)
    assert result.output["key_len"] == 11
    assert result.output["leaked_in_env"] is False
    # the parent process must never hold the secret either
    from app.sandbox import inject

    assert not inject.has_secret("cap-db-pass")


@pytest.mark.asyncio
async def test_missing_secret_fails_closed(runtime) -> None:
    async def op():
        from app.sandbox.inject import get_secret

        get_secret("never-injected")
        return {}

    result = await runtime.execute(
        _profile(),
        op,
        execution_id=uuid4(),
        secrets={},
    )
    # the operation itself raised; the sandbox reports the failure without
    # echoing any secret value
    assert result.status == "FAILED"
    assert "never-injected" in (result.error or "")


@pytest.mark.asyncio
async def test_memory_provider_rejects_secret_injection() -> None:
    mem = SandboxRuntime(MemorySandboxProvider(), SandboxPolicyEngine())

    async def op():
        return {}

    with pytest.raises(Exception) as excinfo:
        await mem.execute(_profile(), op, execution_id=uuid4(), secrets={"a": SECRET_VALUE})
    assert "secret injection" in str(excinfo.value)


@pytest.mark.asyncio
async def test_sandbox_error_does_not_echo_secret(runtime) -> None:
    """An operation that crashes while holding the secret must not leak it."""

    async def op():
        from app.sandbox.inject import get_secret

        value = get_secret("cap-db-pass")
        raise RuntimeError(f"boom with {value}")

    result = await runtime.execute(
        _profile(),
        op,
        execution_id=uuid4(),
        secrets={"cap-db-pass": SECRET_VALUE},
    )
    assert result.status == "FAILED"
    # error surfaces the exception class/name but never the secret value
    assert SECRET_VALUE not in (result.error or "")
    assert "boom" in (result.error or "")


@pytest.mark.asyncio
async def test_secret_like_environment_rejected_at_profile_boundary() -> None:
    """The profile fails closed if someone tries to smuggle secrets via env."""
    with pytest.raises(ValueError):
        SandboxProfile(
            name="bad",
            environment={"API_KEY": SECRET_VALUE},
        )


@pytest.mark.asyncio
async def test_secret_gone_after_sandbox_exits(runtime) -> None:
    """After the sandbox process terminates, the secret is unreachable."""
    pid_holder: dict[str, int] = {}

    async def op():
        import os as _os

        from app.sandbox.inject import get_secret

        get_secret("cap-db-pass")
        pid_holder["pid"] = _os.getpid()
        return {"pid": _os.getpid()}

    result = await runtime.execute(
        _profile(),
        op,
        execution_id=uuid4(),
        secrets={"cap-db-pass": SECRET_VALUE},
    )
    assert result.status == "SUCCEEDED"
    child_pid = result.output["pid"]
    # the child is gone; its memory (and the secret) is gone with it
    import subprocess
    import sys

    if sys.platform == "win32":
        probe = subprocess.run(
            ["tasklist", "/FI", f"PID eq {child_pid}", "/NH"], capture_output=True
        )
        assert str(child_pid) not in probe.stdout.decode("mbcs", errors="replace")
    else:  # pragma: no cover
        try:
            import os

            os.kill(child_pid, 0)
            raise AssertionError("sandbox process still alive")
        except OSError:
            pass
