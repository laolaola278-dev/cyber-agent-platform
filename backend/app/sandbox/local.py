"""Controlled local-process sandbox implementation."""

import asyncio
import os
from pathlib import Path
from time import monotonic

from app.exceptions import AssessmentExecutionError, AssessmentPolicyViolation
from app.sandbox.contracts import SandboxCommand, SandboxResult


class LocalProcessSandbox:
    """Run an allowlisted executable without a shell and with bounded output/time."""

    def __init__(self, allowed_executables: set[str]) -> None:
        self._allowed = {self._identity(item) for item in allowed_executables}

    async def execute(self, command: SandboxCommand) -> SandboxResult:
        if self._identity(command.executable) not in self._allowed:
            raise AssessmentPolicyViolation("Sandbox executable is not allowlisted")
        if command.timeout_seconds < 1 or command.max_output_bytes < 1:
            raise AssessmentPolicyViolation("Sandbox resource limits must be positive")
        cwd = self._validate_working_directory(command.working_directory)
        environment = self._minimal_environment(command.environment)
        started = monotonic()
        process = await asyncio.create_subprocess_exec(
            command.executable,
            *command.arguments,
            cwd=str(cwd) if cwd else None,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=command.timeout_seconds
            )
        except TimeoutError:
            timed_out = True
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
        combined_size = len(stdout_bytes) + len(stderr_bytes)
        budget = command.max_output_bytes
        stdout_bytes = stdout_bytes[:budget]
        stderr_bytes = stderr_bytes[: max(0, budget - len(stdout_bytes))]
        result = SandboxResult(
            exit_code=process.returncode if process.returncode is not None else -1,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            timed_out=timed_out,
            duration_seconds=monotonic() - started,
            output_truncated=combined_size > budget,
        )
        if result.timed_out:
            raise AssessmentExecutionError("Sandbox command timed out")
        return result

    @staticmethod
    def _identity(executable: str) -> str:
        return Path(executable).name.casefold()

    @staticmethod
    def _validate_working_directory(value: Path | None) -> Path | None:
        if value is None:
            return None
        resolved = value.resolve()
        if not resolved.is_dir():
            raise AssessmentPolicyViolation("Sandbox working directory does not exist")
        return resolved

    @staticmethod
    def _minimal_environment(extra: object) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.casefold() in {"path", "systemroot", "windir", "tmp", "temp", "home"}
        }
        environment.update({str(key): str(value) for key, value in dict(extra).items()})
        return environment
