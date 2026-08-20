"""Phase 28.4 -- SubprocessSandboxProvider isolation & termination tests.

Certifies that the production-default provider really executes operations in a
SEPARATE OS process (real process isolation), enforces wall-clock timeouts with
hard termination, kills the whole process tree on terminate (no orphan child
processes), and that a sandbox crash never takes the worker down.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.sandbox.policy import SandboxPolicyEngine
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.subprocess_provider import SubprocessSandboxProvider

pytestmark = pytest.mark.sandbox


def _profile(name: str = "p284", timeout: int = 30, memory_mb: int = 256) -> SandboxProfile:
    return SandboxProfile(
        name=name,
        timeout_seconds=timeout,
        memory_mb=memory_mb,
        cpu_millicores=500,
    )


@pytest.fixture
def runtime() -> SandboxRuntime:
    return SandboxRuntime(SubprocessSandboxProvider(), SandboxPolicyEngine())


def _process_alive(pid: int) -> bool:
    """Cross-platform process existence check."""
    import subprocess
    import sys

    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
        )
        return str(pid) in result.stdout.decode("mbcs", errors="replace")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@pytest.mark.asyncio
async def test_operation_runs_in_separate_os_process(runtime) -> None:
    async def op():
        import os

        return {"child_pid": os.getpid()}

    result = await runtime.execute(_profile(), op, execution_id=uuid4())
    assert result.status == "SUCCEEDED"
    assert result.output["child_pid"] != os.getpid(), "operation ran in the worker process!"


@pytest.mark.asyncio
async def test_provider_reports_honest_capabilities(runtime) -> None:
    provider = runtime.provider
    assert provider.real_isolation is True
    caps = provider.capabilities
    assert caps.timeout is True
    assert caps.process is True
    # honest: this platform cannot do OS-level network/FS isolation, but the
    # provider DOES support in-memory secret injection (Phase 28.4 GATE 13)
    assert caps.network is False
    assert caps.filesystem is False
    assert caps.secret is True


@pytest.mark.asyncio
async def test_policy_fails_closed_when_profile_demands_unsupported(runtime) -> None:
    profile = _profile().model_copy(update={"network_enabled": True})

    async def op():
        return {}

    with pytest.raises(Exception):  # noqa: B017 -- SandboxExecutionError / PolicyViolation
        await runtime.execute(profile, op, execution_id=uuid4())


@pytest.mark.asyncio
async def test_hard_timeout_terminates_sandbox(runtime) -> None:
    async def infinite():
        import asyncio

        await asyncio.sleep(60)
        return {}

    result = await runtime.execute(_profile(timeout=1), infinite, execution_id=uuid4())
    assert result.status == "FAILED"
    assert result.timed_out is True
    assert "timed out" in (result.error or "")


@pytest.mark.asyncio
async def test_terminate_kills_process_and_children(runtime, tmp_path) -> None:
    """Sandbox spawns a child process; terminate must kill the whole tree."""
    execution_id = uuid4()
    pid_file = tmp_path / "child.pid"

    async def spawner():
        import subprocess
        import sys

        # a long-lived child (sleep); its pid is recorded for post-kill checks
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pid_file.write_text(str(proc.pid))
        import asyncio as _asyncio

        await _asyncio.sleep(60)  # keep the sandbox busy
        return {"child_pid": proc.pid}

    task = asyncio.create_task(
        runtime.execute(_profile(timeout=60), spawner, execution_id=execution_id)
    )
    await asyncio.sleep(2.0)  # sandbox running with its child
    assert pid_file.exists(), "sandbox child was not spawned"
    child_pid = int(pid_file.read_text())
    assert _process_alive(child_pid), "child process missing before terminate"

    terminated = await runtime.terminate(execution_id)
    assert terminated is True
    try:
        result = await asyncio.wait_for(task, timeout=5)
        assert result.terminated is True
    except TimeoutError:  # pragma: no cover
        pytest.fail("sandbox did not observe termination")
    await asyncio.sleep(1.5)
    # the child process tree is gone (Job Object kill-on-close)
    assert not _process_alive(child_pid), f"orphan sandbox child {child_pid} survived termination"


@pytest.mark.asyncio
async def test_sandbox_crash_does_not_kill_worker(runtime) -> None:
    async def crash():
        import os

        os._exit(9)  # sandbox process dies hard; worker must survive

    result = await runtime.execute(_profile(), crash, execution_id=uuid4())
    assert result.status == "FAILED"
    assert result.exit_code == 9

    # worker still alive and can run another sandbox
    async def ok():
        return {"alive": True}

    result2 = await runtime.execute(_profile(), ok, execution_id=uuid4())
    assert result2.status == "SUCCEEDED"
    assert result2.output == {"alive": True}


@pytest.mark.asyncio
async def test_unserializable_operation_fails_closed(runtime) -> None:
    # An in-memory SQLite AsyncEngine is not cloudpicklable and needs no
    # external PostgreSQL server (the previous PG_DSN-based engine made this
    # unit test fail with 'connection refused' in the general CI, which has no
    # PostgreSQL service).
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.connect():
        # an operation holding an un-picklable resource must be rejected
        async def op():
            return {"engine": engine}  # AsyncEngine is not cloudpicklable

        with pytest.raises(Exception):  # noqa: B017 -- provider rejects non-cloudpicklable payload
            await runtime.execute(_profile(), op, execution_id=uuid4())
    await engine.dispose()
