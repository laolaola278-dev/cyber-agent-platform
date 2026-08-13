"""Phase 28.5 -- OCI container sandbox provider (real isolation).

Runs each sandbox execution in its own OCI container:

  * process/filesystem/network isolation via the container runtime
  * memory / CPU / PID limits written to the cgroup by the runtime
  * read-only rootfs + tmpfs workdir
  * non-root user from the image (never privileged)
  * controlled egress through a proxy network (layer-2 SSRF defense)
  * typed JSON execution protocol (no cloudpickle across the boundary)
  * unique container identity via safe labels (execution/run/worker/lease ids
    -- never tokens or secrets)
  * hard termination: SIGTERM -> kill -> confirm exit -> remove

Driver abstraction keeps the provider runtime-agnostic (docker / podman /
containerd CLI can plug in). The docker driver shells out to the CLI rather
than binding an SDK, so no docker socket is mounted into the sandbox and the
worker talks to the runtime through the standard CLI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.sandbox.oci_protocol import (
    SandboxRequest,
    SandboxResponse,
    validate_request,
)
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import SandboxExecutionError, SandboxProviderCapability

logger = logging.getLogger("cap.sandbox.oci")

# safe container labels (no tokens / secrets)
LABEL_PREFIX = "cap.sandbox"
LABEL_EXECUTION = f"{LABEL_PREFIX}.execution_id"
LABEL_RUN = f"{LABEL_PREFIX}.run_id"
LABEL_WORKER = f"{LABEL_PREFIX}.worker_id"
LABEL_LEASE = f"{LABEL_PREFIX}.lease_id"
LABEL_ATTEMPT = f"{LABEL_PREFIX}.attempt"
LABEL_IMAGE = f"{LABEL_PREFIX}.image"


class ContainerRuntimeError(SandboxExecutionError):
    """Container runtime operation failed."""


@dataclass
class ContainerSpec:
    name: str
    image: str
    command: list[str]
    env: dict[str, str]
    labels: dict[str, str]
    network: str | None = None
    memory_mb: int | None = None
    cpu_millicores: int | None = None
    pids_limit: int | None = None
    read_only_rootfs: bool = True
    tmpfs: tuple[str, ...] = ("/tmp",)
    user: str | None = None  # image USER by default


class ContainerDriver(Protocol):
    driver_name: str

    async def health(self) -> bool: ...

    async def run_interactive(
        self, spec: ContainerSpec, input_bytes: bytes, timeout: float
    ) -> tuple[int, bytes, bytes]: ...

    async def kill(self, container_id: str) -> None: ...

    async def rm(self, container_id: str, force: bool = True) -> None: ...

    async def inspect(self, container_id: str) -> dict[str, Any]: ...

    async def list_by_labels(self, labels: dict[str, str]) -> list[dict[str, Any]]: ...

    async def exists_image(self, image: str) -> bool: ...

    async def container_stats(self, container_id: str) -> dict[str, Any]: ...


class DockerCLIDriver:
    """Docker driver shelling out to the CLI (runtime-agnostic surface)."""

    driver_name = "docker"

    def __init__(self, binary: str = "docker", *, timeout: float = 180.0) -> None:
        self._binary = binary
        self._timeout = timeout

    async def _run(
        self, args: list[str], *, input_bytes: bytes | None = None
    ) -> subprocess.CompletedProcess:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._binary,
                *args,
                stdin=(
                    asyncio.subprocess.PIPE
                    if input_bytes is not None
                    else asyncio.subprocess.DEVNULL
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input_bytes), timeout=self._timeout
            )
            return subprocess.CompletedProcess(
                args, proc.returncode or 0, stdout, stderr
            )
        except asyncio.TimeoutError as error:
            raise ContainerRuntimeError(
                f"docker {' '.join(args[:2])} timed out after {self._timeout}s"
            ) from error

    async def health(self) -> bool:
        try:
            proc = await self._run(["info", "--format", "{{.ServerVersion}}"])
            return proc.returncode == 0 and bool(proc.stdout.strip())
        except Exception:  # noqa: BLE001
            return False

    async def run_interactive(
        self, spec: ContainerSpec, input_bytes: bytes, timeout: float
    ) -> tuple[int, bytes, bytes]:
        args = ["run", "-i", "--name", spec.name]
        for key, value in spec.labels.items():
            args += ["--label", f"{key}={value}"]
        if spec.network:
            args += ["--network", spec.network]
        if spec.memory_mb:
            args += ["--memory", f"{spec.memory_mb}m", "--memory-swap", f"{spec.memory_mb}m"]
        if spec.cpu_millicores:
            args += ["--cpus", f"{spec.cpu_millicores / 1000:.3f}"]
        if spec.pids_limit:
            args += ["--pids-limit", str(spec.pids_limit)]
        if spec.read_only_rootfs:
            args += ["--read-only"]
        for mount in spec.tmpfs:
            args += ["--tmpfs", mount]
        if spec.user:
            args += ["--user", spec.user]
        # Phase 28.5-L security context: drop ALL capabilities and disable
        # privilege escalation -- the container needs none (verified in the
        # §19 security-context inspect during Linux certification)
        args += ["--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
        args += ["--stop-signal", "SIGTERM", "--stop-timeout", "8"]
        for key, value in spec.env.items():
            args += ["--env", f"{key}={value}"]
        args += [spec.image] + spec.command

        proc = await asyncio.create_subprocess_exec(
            self._binary,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input_bytes), timeout=timeout
            )
            return proc.returncode or 0, stdout, stderr
        except asyncio.TimeoutError:
            # hard termination: SIGTERM -> SIGKILL -> confirm -> remove
            try:
                await self.kill(spec.name)
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:  # pragma: no cover
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            try:
                await self.rm(spec.name, force=True)
            except Exception:  # noqa: BLE001
                pass
            return 124, b"", b"sandbox timed out and was force-terminated"

    async def kill(self, container_id: str) -> None:
        try:
            proc = await self._run(["kill", container_id])
            if proc.returncode != 0:
                logger.warning(
                    "docker kill %s failed: %s",
                    container_id[:16],
                    proc.stderr.decode(errors="replace")[-200:],
                )
        except ContainerRuntimeError:
            pass

    async def rm(self, container_id: str, force: bool = True) -> None:
        args = ["rm"]
        if force:
            args.append("--force")
        args.append(container_id)
        try:
            proc = await self._run(args)
            if proc.returncode != 0:
                logger.warning(
                    "docker rm %s failed: %s",
                    container_id[:16],
                    proc.stderr.decode(errors="replace")[-200:],
                )
        except ContainerRuntimeError:
            pass

    async def inspect(self, container_id: str) -> dict[str, Any]:
        proc = await self._run(["inspect", container_id])
        if proc.returncode != 0:
            raise ContainerRuntimeError(
                f"docker inspect failed: {proc.stderr.decode(errors='replace')[-300:]}"
            )
        try:
            data = json.loads(proc.stdout.decode())
            return data[0] if isinstance(data, list) and data else {}
        except json.JSONDecodeError as error:
            raise ContainerRuntimeError(
                f"docker inspect returned invalid json: {error}"
            ) from error

    async def list_by_labels(self, labels: dict[str, str]) -> list[dict[str, Any]]:
        # empty value = "label key exists" filter (docker syntax)
        filters = ",".join(
            f"label={k}" if v == "" else f"label={k}={v}"
            for k, v in labels.items()
        )
        proc = await self._run(
            ["ps", "-a", "--filter", f"label={filters}", "--format", "{{.ID}}"]
        )
        ids = [
            line.strip()
            for line in proc.stdout.decode().splitlines()
            if line.strip()
        ]
        result = []
        for cid in ids:
            try:
                result.append(await self.inspect(cid))
            except ContainerRuntimeError:
                continue
        return result

    async def exists_image(self, image: str) -> bool:
        proc = await self._run(["image", "inspect", image])
        return proc.returncode == 0

    async def container_stats(self, container_id: str) -> dict[str, Any]:
        try:
            proc = await self._run(
                [
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.MemUsage}}|{{.CPUPerc}}|{{.PIDs}}",
                    container_id,
                ]
            )
        except ContainerRuntimeError:
            return {}
        if proc.returncode != 0:
            return {}
        return {"line": proc.stdout.decode().strip()}


class OCISandboxProvider:
    """Run typed sandbox executions inside OCI containers."""

    provider_name = "oci-sandbox"
    real_isolation = True

    capabilities = SandboxProviderCapability(
        network=True,       # container network namespace + egress proxy
        filesystem=True,    # read-only rootfs + tmpfs
        secret=True,        # ephemeral env injection, never baked into image
        timeout=True,       # hard wall-clock + container stop
        process=True,       # container PID namespace + pids-limit
        resource=True,      # memory/cpu limits via cgroup
        container=True,
        vm=False,
        snapshot=False,
    )

    def __init__(
        self,
        driver: ContainerDriver | None = None,
        *,
        image: str | None = None,
        network: str | None = None,
        egress_proxy_url: str | None = None,
        default_memory_mb: int = 256,
        default_cpu_millicores: int = 500,
        default_pids_limit: int = 256,
        metrics: Any | None = None,
    ) -> None:
        self._driver = driver or DockerCLIDriver()
        self._image = (
            image
            or os.environ.get("CAP_SANDBOX_IMAGE")
            or "cap-sandbox-http:latest"
        )
        self._network = network or os.environ.get("CAP_SANDBOX_NETWORK")
        self._egress_proxy = egress_proxy_url or os.environ.get(
            "CAP_EGRESS_PROXY_URL"
        )
        self._default_memory_mb = default_memory_mb
        self._default_cpu = default_cpu_millicores
        self._default_pids = default_pids_limit
        self._metrics = metrics
        self._active: dict[str, str] = {}  # execution_id -> container name

    # -- identity ------------------------------------------------------------

    @staticmethod
    def container_name(execution_id: UUID) -> str:
        return f"cap-sbx-{execution_id.hex[:16]}"

    def _labels(
        self,
        execution_id: UUID,
        run_id: str,
        worker_id: str,
        lease_id: str | None,
        attempt: int,
    ) -> dict[str, str]:
        return {
            LABEL_EXECUTION: str(execution_id),
            LABEL_RUN: run_id,
            LABEL_WORKER: worker_id,
            LABEL_LEASE: lease_id or "",
            LABEL_ATTEMPT: str(attempt),
            LABEL_IMAGE: self._image,
        }

    # -- lifecycle -----------------------------------------------------------

    async def execute_request(
        self,
        profile: SandboxProfile,
        request: SandboxRequest,
        *,
        run_id: str | None = None,
        worker_id: str | None = None,
        lease_id: str | None = None,
        attempt: int = 0,
        secrets: dict[str, str] | None = None,
    ) -> SandboxResponse:
        validate_request(request)
        execution_id = UUID(request.sandbox_execution_id)
        name = self.container_name(execution_id)
        env: dict[str, str] = {"PYTHONUNBUFFERED": "1"}
        if self._egress_proxy:
            # ALL egress goes through the proxy; never bypass (the proxy is
            # the network-layer SSRF second line)
            env["HTTPS_PROXY"] = self._egress_proxy
            env["HTTP_PROXY"] = self._egress_proxy
            env["NO_PROXY"] = ""
        for key, value in (secrets or {}).items():
            env[f"CAP_SECRET_{key}"] = value

        spec = ContainerSpec(
            name=name,
            image=self._image,
            command=["python", "-m", "sandbox.shim"],
            env=env,
            labels=self._labels(
                execution_id,
                run_id or request.run_id,
                worker_id or "",
                lease_id,
                attempt,
            ),
            network=self._network,
            memory_mb=profile.memory_mb or self._default_memory_mb,
            cpu_millicores=profile.cpu_millicores or self._default_cpu,
            pids_limit=self._default_pids,
            read_only_rootfs=True,
            tmpfs=("/tmp",),
        )

        started = time.monotonic()
        payload = request.model_dump_json().encode("utf-8")
        try:
            exit_code, stdout, stderr = await self._driver.run_interactive(
                spec, payload, timeout=profile.timeout_seconds
            )
            if self._metrics is not None:
                self._metrics.inc("sandbox_execution_total", labels={"provider": self.provider_name})
        finally:
            # lifecycle: execute -> remove on EVERY path (success/failure/
            # timeout/cancel) so no container outlives its execution
            await self._driver.rm(name, force=True)
            self._active.pop(str(execution_id), None)

        if exit_code == 124:
            if self._metrics is not None:
                self._metrics.inc("sandbox_forced_termination_total")
            return SandboxResponse(
                version=request.version,
                status="error",
                error="sandbox timed out",
                error_type="SandboxTimeout",
            )
        if exit_code != 0:
            return SandboxResponse(
                version=request.version,
                status="error",
                error=(stderr.decode(errors="replace") or stdout.decode(errors="replace"))[-500:],
                error_type="SandboxExit",
            )
        try:
            return SandboxResponse.model_validate_json(stdout.decode("utf-8"))
        except Exception as error:  # noqa: BLE001
            return SandboxResponse(
                version=request.version,
                status="error",
                error=f"invalid shim response: {error}",
                error_type="SandboxProtocol",
            )

    # -- runtime contract (callable path fails closed) -----------------------

    async def execute(
        self,
        execution_id: UUID,
        profile: SandboxProfile,
        operation: Any,
        secrets: dict[str, str] | None = None,
    ) -> Any:
        raise SandboxExecutionError(
            "OCISandboxProvider does not accept arbitrary callables: use "
            "execute_request with the typed protocol (no cloudpickle across "
            "the container trust boundary)"
        )

    async def terminate(self, execution_id: UUID) -> bool:
        name = self.container_name(execution_id)
        await self._driver.kill(name)
        await self._driver.rm(name, force=True)
        self._active.pop(str(execution_id), None)
        return True

    async def health(self) -> bool:
        if not await self._driver.health():
            return False
        return await self._driver.exists_image(self._image)
