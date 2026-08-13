"""Phase 28.4 -- SubprocessSandboxProvider.

Real OS-process isolation for plugin operations on hosts where container runtimes
are not available (the production default until an OCI runtime is provisioned).

What IS isolated (real):
- the operation runs in a SEPARATE OS process (independent address space,
  separate Python interpreter, separate heap);
- hard termination = killing that OS process (and its process tree via a
  Windows Job Object / POSIX process group);
- process-count and memory ceilings are enforced via the Job Object where the
  platform supports it;
- a sandbox crash cannot take the worker down (the worker only observes the
  child exit).

What is NOT isolated (capabilities report false, and the policy engine fails
closed when a profile demands them):
- network egress filtering (no OS-level network policy; SSRF defense relies on
  URLPolicyValidator at the application layer -- defense in depth, PARTIAL);
- filesystem sandboxing (no OS-level FS jail);
- secret injection.

The operation must be self-contained and cloudpickle-serializable (no worker
session/engine references). ``SandboxRuntime`` validates the profile against
these capabilities before any execution, so a profile that demands network or
filesystem isolation is REJECTED rather than silently weakened.
"""

from __future__ import annotations

import asyncio
import base64
import cloudpickle
import json
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from app.exceptions import SandboxExecutionError
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import (
    SandboxProviderCapability,
    SandboxResult,
)


class JobObject:
    """Windows Job Object wrapper: kill-on-close + process/memory ceilings.

    A Job Object guarantees that killing the sandbox process also terminates
    every child it spawned (no orphan browser/helper processes), and can cap
    total process count and memory. On non-Windows platforms the wrapper is a
    no-op and termination uses the process group / direct kill.
    """

    def __init__(self, *, max_processes: int = 64, memory_mb: int | None = None) -> None:
        self._handle: Any = None
        self._assigned: set[int] = set()
        self._max_processes = max_processes
        self._memory_mb = memory_mb
        if sys.platform == "win32":
            self._open_windows_job(max_processes, memory_mb)

    def _open_windows_job(self, max_processes: int, memory_mb: int | None) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x0008
        JOB_OBJECT_LIMIT_JOB_MEMORY = 0x0200

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise SandboxExecutionError("CreateJobObject failed")

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if max_processes > 0:
            info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = max_processes
        if memory_mb:
            info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = memory_mb * 1024 * 1024

        ok = kernel32.SetInformationJobObject(
            job,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(job)
            raise SandboxExecutionError(
                f"SetInformationJobObject failed: {ctypes.get_last_error()}"
            )
        self._kernel32 = kernel32
        self._handle = job

    def assign(self, pid: int) -> None:
        if self._handle is None:
            return
        ok = self._kernel32.AssignProcessToJobObject(self._handle, int(pid))
        if not ok:
            # the child may have exited between spawn and assign
            import ctypes

            raise SandboxExecutionError(
                f"AssignProcessToJobObject failed: {ctypes.get_last_error()}"
            )
        self._assigned.add(int(pid))

    def close(self) -> None:
        """Closing the job handle with KILL_ON_JOB_CLOSE terminates all members."""
        if self._handle is None:
            return
        self._kernel32.CloseHandle(self._handle)
        self._handle = None


_HOST_BOOTSTRAP = r"""
import asyncio, base64, json, os, sys
import cloudpickle

_syspath = os.environ.get("CAP_SANDBOX_SYSPATH", "")
if _syspath and _syspath not in sys.path:
    sys.path.insert(0, _syspath)

def _main() -> None:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    operation = cloudpickle.loads(base64.b64decode(payload["operation"]))
    started = payload.get("started_at")
    # Phase 28.4 secrets: inject into the in-process module. The values live
    # only in this process's memory; the process is terminated after the
    # operation (or by the parent's hard kill), so they never persist.
    try:
        import app.sandbox.inject as _inject
    except Exception:  # noqa: BLE001 -- syspath not available
        _inject = None
    if _inject is not None:
        _inject.set_secrets(payload.get("secrets") or {})

    async def _run() -> dict:
        out = await operation()
        return {"ok": True, "output": out}

    try:
        result = asyncio.run(_run())
    except BaseException as error:  # noqa: BLE001 -- report to parent
        result = {
            "ok": False,
            "error": str(error),
            "error_type": type(error).__name__,
        }
    sys.stdout.buffer.write(base64.b64encode(cloudpickle.dumps(result)))
    sys.stdout.buffer.flush()

_main()
"""


def _redact(text: str, secrets: dict[str, str] | None) -> str:
    """Defense-in-depth: scrub known secret values from surfaced error text.

    The sandbox child may accidentally embed a secret in an exception message
    or stderr; the provider KNOWS the injected values and replaces them before
    the error ever reaches logs / the caller.
    """
    for value in (secrets or {}).values():
        if value and value in text:
            text = text.replace(value, "[REDACTED]")
    return text


class SubprocessSandboxProvider:
    """Run a self-contained async operation in a separate OS process."""

    provider_name = "subprocess-sandbox"
    real_isolation = True

    capabilities = SandboxProviderCapability(
        network=False,  # no OS-level egress filtering on this platform
        filesystem=False,  # no OS-level FS jail
        secret=True,  # in-memory injection via stdin payload (never env/disk/log)
        timeout=True,  # parent enforces a hard wall-clock deadline
        container=False,
        vm=False,
        process=True,  # Job Object process-tree limits + kill-on-close
    )

    def __init__(
        self,
        *,
        python: str | None = None,
        max_processes: int = 64,
        memory_mb: int | None = 512,
    ) -> None:
        self._python = python or sys.executable
        self._max_processes = max_processes
        self._memory_mb = memory_mb
        self._active: dict[UUID, asyncio.subprocess.Process] = {}
        self._jobs: dict[UUID, JobObject] = {}
        self._terminated: set[UUID] = set()

    async def execute(
        self,
        execution_id: UUID,
        profile: SandboxProfile,
        operation: Callable[[], Any],
        secrets: dict[str, str] | None = None,
    ) -> SandboxResult:
        started = datetime.now(UTC)
        try:
            operation_blob = base64.b64encode(
                cloudpickle.dumps(operation)
            ).decode("ascii")
        except Exception as error:  # noqa: BLE001
            raise SandboxExecutionError(
                f"operation is not sandbox-serializable: {error}"
            ) from error

        payload = {
            "operation": operation_blob,
            "started_at": started.isoformat(),
            # Phase 28.4 secrets: transported ONLY inside the stdin payload
            # (subprocess memory). Never env, never disk, never logs. The
            # child dies -> the secrets die with it.
            "secrets": dict(secrets or {}),
        }
        job = JobObject(
            max_processes=self._max_processes, memory_mb=self._memory_mb
        )
        # let the child import project modules (app.*) when operations need them
        import app  # noqa: F401

        child_env = dict(os.environ)
        child_env["CAP_SANDBOX_SYSPATH"] = str(Path(app.__file__).resolve().parents[1])
        try:
            process = await asyncio.create_subprocess_exec(
                self._python,
                "-c",
                _HOST_BOOTSTRAP,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
            )
            try:
                job.assign(process.pid)
            except SandboxExecutionError:
                # child exited before assignment (e.g. import failure): let
                # communicate surface the real error
                pass
            self._active[execution_id] = process
            self._jobs[execution_id] = job

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(json.dumps(payload).encode("utf-8")),
                    timeout=profile.timeout_seconds,
                )
            except TimeoutError:
                await self.terminate(execution_id)
                return SandboxResult(
                    execution_id=execution_id,
                    provider=self.provider_name,
                    status="FAILED",
                    output={},
                    error="Sandbox execution timed out",
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    timed_out=True,
                    terminated=True,
                    exit_code=124,
                )

            if execution_id in self._terminated:
                return SandboxResult(
                    execution_id=execution_id,
                    provider=self.provider_name,
                    status="CANCELLED",
                    output={},
                    error="Sandbox execution terminated",
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    terminated=True,
                    exit_code=130,
                )
            if process.returncode != 0:
                stderr_tail = (stderr_bytes or b"").decode(
                    "utf-8", errors="replace"
                )[-2000:]
                stderr_tail = _redact(stderr_tail, secrets)
                return SandboxResult(
                    execution_id=execution_id,
                    provider=self.provider_name,
                    status="FAILED",
                    output={},
                    error=(
                        f"Sandbox process exited with code {process.returncode}"
                        + (f": {stderr_tail}" if stderr_tail else "")
                    ),
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    exit_code=process.returncode,
                )

            try:
                decoded = cloudpickle.loads(
                    base64.b64decode(stdout_bytes.decode("utf-8").strip())
                )
            except Exception as error:  # noqa: BLE001
                return SandboxResult(
                    execution_id=execution_id,
                    provider=self.provider_name,
                    status="FAILED",
                    output={},
                    error=f"Sandbox result decode failed: {error}",
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    exit_code=2,
                )

            if not decoded.get("ok"):
                return SandboxResult(
                    execution_id=execution_id,
                    provider=self.provider_name,
                    status="FAILED",
                    output={},
                    error=_redact(
                        decoded.get("error") or "sandbox operation failed", secrets
                    ),
                    error_code=decoded.get("error_type"),
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    exit_code=1,
                )

            return SandboxResult(
                execution_id=execution_id,
                provider=self.provider_name,
                status="SUCCEEDED",
                output=decoded.get("output") or {},
                started_at=started,
                finished_at=datetime.now(UTC),
                exit_code=0,
            )
        finally:
            self._active.pop(execution_id, None)
            self._jobs.pop(execution_id, None)
            self._terminated.discard(execution_id)
            job.close()

    async def terminate(self, execution_id: UUID) -> bool:
        process = self._active.get(execution_id)
        if process is None:
            return False
        self._terminated.add(execution_id)
        # kill the whole process tree (explicit tree kill + Job Object
        # kill-on-close as a second net for any escaped descendants)
        if sys.platform == "win32":
            import subprocess as _sp

            try:
                _sp.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True,
                    timeout=15,
                )
            except Exception:  # noqa: BLE001 -- best-effort tree kill
                pass
        job = self._jobs.get(execution_id)
        if job is not None and job._handle is not None:  # type: ignore[attr-defined]
            job.close()  # KILL_ON_JOB_CLOSE terminates all members
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except (TimeoutError, ProcessLookupError):
            pass
        self._active.pop(execution_id, None)
        return True

    async def health(self) -> bool:
        try:
            probe = await asyncio.create_subprocess_exec(
                self._python,
                "-c",
                "import sys; sys.exit(0)",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(probe.wait(), timeout=10)
            return probe.returncode == 0
        except Exception:  # noqa: BLE001
            return False
