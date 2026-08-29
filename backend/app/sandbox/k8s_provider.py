"""Phase 28.6 -- Kubernetes sandbox provider (control-plane isolated).

Runs each sandbox execution in a SHORT-LIVED Kubernetes Pod in the dedicated
``cap-sandbox`` namespace, replacing the Phase 28.5 docker-socket-based OCI
driver. The worker NEVER mounts a container runtime socket:

  * worker -> Kubernetes API (ServiceAccount RBAC, namespaced to cap-sandbox)
    -> Sandbox Pod (cap.sandbox.* labels, typed protocol)
  * the Pod reuses the SAME self-contained shim (``sandbox.shim --serve``) and
    the SAME typed JSON protocol (no cloudpickle, no DB/session objects)
  * the shim serves one typed request over HTTP; the provider POSTs the
    request, collects the typed response, then deletes the Pod
  * hard termination / orphan reconciliation via execution+lease identity

The provider declares ``secret=False``: secrets are NEVER injected into the
Pod (they would be visible in the Pod spec via the API). Callers must only
pass secrets to a provider whose capability model supports them.

Sandbox Pod success != run commit: fencing + terminal CAS remain the only
ways a run becomes terminal (worker-side), so a sandbox Pod crashing or being
killed never produces a commit.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any
from uuid import UUID

import httpx

from app.sandbox.oci_protocol import (
    SandboxRequest,
    SandboxResponse,
    validate_request,
)
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import SandboxExecutionError, SandboxProviderCapability

logger = logging.getLogger("cap.sandbox.k8s")

# safe pod labels (no tokens / secrets / URLs -- high-cardinality or sensitive
# values must not be leaked into the API object metadata)
LABEL_PREFIX = "cap.sandbox"
LABEL_MANAGED = "cap.managed"
LABEL_EXECUTION = f"{LABEL_PREFIX}.execution_id"
LABEL_RUN = f"{LABEL_PREFIX}.run_id"
LABEL_WORKER = f"{LABEL_PREFIX}.worker_id"
LABEL_LEASE = f"{LABEL_PREFIX}.lease_id"
LABEL_ATTEMPT = f"{LABEL_PREFIX}.attempt"


class KubernetesSandboxError(SandboxExecutionError):
    """Kubernetes sandbox operation failed."""


def _load_k8s_client() -> Any:
    """Lazy-load the kubernetes API client (in-cluster, else kubeconfig)."""
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config

    try:
        k8s_config.load_incluster_config()
    except Exception:  # noqa: BLE001 -- not running inside a cluster
        k8s_config.load_kube_config()
    return k8s_client


class KubernetesSandboxProvider:
    """Sandbox provider that executes typed operations in Kubernetes Pods."""

    provider_name = "kubernetes-sandbox"
    real_isolation = True
    capabilities = SandboxProviderCapability(
        network=True,
        filesystem=True,
        secret=False,  # fail-closed: never inject secrets into the Pod spec
        timeout=True,
        # v1.0.1: the Pod spec below proves these three, and the production
        # admission policy reads them. They were left at their defaults while
        # only the name was checked; a capability-based policy that trusts an
        # under-declared provider would have rejected the one path Helm ships.
        container=True,  # the sandbox Pod is the container execution domain
        process=True,  # container PID namespace (no pids-limit configured)
        resource=True,  # Pod resources.limits/requests for cpu + memory
    )

    def __init__(
        self,
        *,
        namespace: str | None = None,
        image: str | None = None,
        shim_port: int = 8080,
        egress_proxy: str | None = None,
        pod_ready_timeout: float = 90.0,
        request_timeout: float = 180.0,
        default_memory_mb: int = 512,
        default_cpu_millicores: int = 500,
        metrics: Any | None = None,
    ) -> None:
        self._namespace = namespace or os.environ.get("CAP_SANDBOX_NAMESPACE", "cap-sandbox")
        self._image = image or os.environ.get("CAP_SANDBOX_IMAGE", "cap-sandbox-http:latest")
        self._shim_port = int(os.environ.get("CAP_SANDBOX_SHIM_PORT", str(shim_port)))
        self._egress_proxy = egress_proxy or os.environ.get("CAP_SANDBOX_EGRESS_PROXY", "")
        self._pod_ready_timeout = float(pod_ready_timeout)
        self._request_timeout = float(request_timeout)
        self._default_memory_mb = default_memory_mb
        self._default_cpu_millicores = default_cpu_millicores
        self._metrics = metrics
        self._client: Any | None = None
        self._active: set[str] = set()

    # -- k8s client helpers (blocking client -> asyncio.to_thread) -----------

    def _k8s(self) -> Any:
        if self._client is None:
            self._client = _load_k8s_client().CoreV1Api()
        return self._client

    async def _create_pod(self, body: dict[str, Any]) -> None:
        def _sync() -> None:
            self._k8s().create_namespaced_pod(namespace=self._namespace, body=body)

        await asyncio.to_thread(_sync)

    async def _get_pod(self, name: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            # read_namespaced_pod returns a V1Pod OBJECT; every caller treats
            # the result as a plain dict (pod.get("status")...). Convert like
            # _list_pods does -- GA PRE-GATE E caught the raw object leaking
            # through ("'V1Pod' object has no attribute 'get'"), which the
            # broad retry in _wait_ready then masked as "pod not ready".
            pod = self._k8s().read_namespaced_pod(name=name, namespace=self._namespace)
            # V1Pod -> dict; tolerate inputs that are ALREADY plain dicts
            # (test doubles / pre-sanitized payloads) so conversion is idempotent.
            return pod.to_dict() if hasattr(pod, "to_dict") else pod

        return await asyncio.to_thread(_sync)

    async def _delete_pod(self, name: str, *, grace: int = 0) -> None:
        def _sync() -> None:
            self._k8s().delete_namespaced_pod(
                name=name, namespace=self._namespace, grace_period_seconds=grace
            )

        await asyncio.to_thread(_sync)

    async def _list_pods(self, label_selector: str) -> list[dict[str, Any]]:
        def _sync() -> list[dict[str, Any]]:
            resp = self._k8s().list_namespaced_pod(
                namespace=self._namespace, label_selector=label_selector
            )
            return [p.to_dict() for p in resp.items]

        return await asyncio.to_thread(_sync)

    # -- pod spec -------------------------------------------------------------

    def _pod_name(self, execution_id: UUID) -> str:
        return f"cap-sandbox-{str(execution_id)[:8]}"

    def _pod_spec(
        self,
        execution_id: UUID,
        profile: SandboxProfile,
        *,
        run_id: str,
        worker_id: str,
        lease_id: str | None,
        attempt: int,
    ) -> dict[str, Any]:
        env: list[dict[str, str]] = [
            {"name": "CAP_SHIM_PORT", "value": str(self._shim_port)},
            {"name": "PYTHONUNBUFFERED", "value": "1"},
        ]
        if self._egress_proxy:
            # ALL egress through the proxy; NetworkPolicy is the enforcement
            # (layer 2), proxy env is the transport (layer 1.5)
            env.extend(
                [
                    {"name": "HTTPS_PROXY", "value": self._egress_proxy},
                    {"name": "HTTP_PROXY", "value": self._egress_proxy},
                    {"name": "NO_PROXY", "value": ""},
                ]
            )
        labels = {
            LABEL_MANAGED: "true",
            LABEL_EXECUTION: str(execution_id),
            LABEL_RUN: run_id,
            LABEL_WORKER: worker_id,
            LABEL_LEASE: lease_id or "",
            LABEL_ATTEMPT: str(attempt),
        }
        memory_mb = profile.memory_mb or self._default_memory_mb
        cpu = profile.cpu_millicores or self._default_cpu_millicores
        return {
            "metadata": {
                "name": self._pod_name(execution_id),
                "namespace": self._namespace,
                "labels": labels,
            },
            "spec": {
                "automountServiceAccountToken": False,  # no K8s API token
                "hostNetwork": False,
                "hostPID": False,
                "hostIPC": False,
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "sandbox",
                        "image": self._image,
                        # local-loaded images (kind/CI) must not attempt a
                        # registry pull; 'latest' defaults to pullPolicy
                        # Always which ImagePullBackOffs without a registry.
                        "imagePullPolicy": "IfNotPresent",
                        "command": ["python", "-m", "sandbox.shim", "--serve"],
                        "env": env,
                        "ports": [{"containerPort": self._shim_port, "protocol": "TCP"}],
                        "resources": {
                            "limits": {
                                "memory": f"{memory_mb}Mi",
                                "cpu": f"{max(cpu, 10)}m",
                            },
                            "requests": {
                                "memory": "64Mi",
                                "cpu": "10m",
                            },
                        },
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "runAsNonRoot": True,
                            # explicit numeric uid/gid: runAsNonRoot cannot
                            # verify a symbolic image user ("capuser") and
                            # rejects creation with CreateContainerConfigError
                            # (K8S-GATE17 / GA PRE-GATE E root cause).
                            "runAsUser": 10001,
                            "runAsGroup": 10001,
                            "readOnlyRootFilesystem": True,
                            "seccompProfile": {"type": "RuntimeDefault"},
                            "capabilities": {"drop": ["ALL"]},
                        },
                    }
                ],
            },
        }

    # -- lifecycle ------------------------------------------------------------

    async def _wait_ready(self, name: str, *, timeout: float) -> dict[str, Any]:
        """Wait for the shim to answer /healthz (pod IP reachable over the
        cluster network). The readiness probe keeps the pod alive without
        consuming the single-shot execution."""
        deadline = time.monotonic() + timeout
        last_error = "pod not observed"
        while time.monotonic() < deadline:
            try:
                pod = await self._get_pod(name)
                status = pod.get("status") or {}
                pod_ip = status.get("pod_ip")
                phase = status.get("phase") or ""
                if pod_ip and phase == "Running":
                    async with httpx.AsyncClient(timeout=5.0) as http:
                        resp = await http.get(f"http://{pod_ip}:{self._shim_port}/healthz")
                    if resp.status_code == 200:
                        return pod
                last_error = self._describe_pod_stall(phase, pod_ip, status)
            except Exception as error:  # noqa: BLE001 -- transient
                last_error = str(error)[:200]
            await asyncio.sleep(0.4)
        raise KubernetesSandboxError(f"sandbox pod {name} not ready: {last_error}")

    @staticmethod
    def _describe_pod_stall(phase: str, pod_ip: str | None, status: dict[str, Any]) -> str:
        """Build a deterministic stall description from container statuses so a
        CI timeout pinpoints the real blocker (waiting reason/message or the
        exit code of a terminated container) instead of just phase+ip."""
        detail = f"pod phase={phase} ip={pod_ip or 'none'}"
        reasons: list[str] = []
        for cs in status.get("container_statuses") or []:
            name = cs.get("name") or "container"
            state = cs.get("state") or {}
            waiting = state.get("waiting")
            if waiting and waiting.get("reason"):
                msg = (waiting.get("message") or "")[:200]
                reasons.append(f"{name}: waiting reason={waiting['reason']} msg={msg}")
            terminated = state.get("terminated")
            if terminated:
                reasons.append(
                    f"{name}: terminated exitCode={terminated.get('exit_code')} "
                    f"reason={terminated.get('reason')} "
                    f"msg={(terminated.get('message') or '')[:200]}"
                )
            elif not waiting and (cs.get("ready") is False) and (status.get("phase") == "Running"):
                # running but probe-failing: surface restart count + last state
                last = ((cs.get("last_state") or {}).get("terminated")) or {}
                if last:
                    reasons.append(
                        f"{name}: running-not-ready restarts={cs.get('restart_count')} "
                        f"lastExit={last.get('exit_code')} lastReason={last.get('reason')}"
                    )
                else:
                    reasons.append(f"{name}: running-not-ready restarts={cs.get('restart_count')}")
        if reasons:
            detail += " | " + "; ".join(reasons)
        return detail

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
        if secrets:
            raise KubernetesSandboxError(
                "KubernetesSandboxProvider declares secret=False: secrets are "
                "never injected into a Pod spec"
            )
        validate_request(request)
        execution_id = UUID(request.sandbox_execution_id)
        name = self._pod_name(execution_id)
        self._active.add(str(execution_id))
        body = self._pod_spec(
            execution_id,
            profile,
            run_id=run_id or request.run_id,
            worker_id=worker_id or "",
            lease_id=lease_id,
            attempt=attempt,
        )
        try:
            await self._create_pod(body)
            await self._wait_ready(name, timeout=self._pod_ready_timeout)
            pod = await self._get_pod(name)
            pod_ip = (pod.get("status") or {}).get("pod_ip")
            if not pod_ip:
                raise KubernetesSandboxError(f"sandbox pod {name} has no IP")
            payload = request.model_dump_json().encode("utf-8")
            async with httpx.AsyncClient(timeout=self._request_timeout) as http:
                resp = await http.post(
                    f"http://{pod_ip}:{self._shim_port}/",
                    content=payload,
                    headers={"Content-Type": "application/json"},
                )
            if resp.status_code != 200:
                return SandboxResponse(
                    version=request.version,
                    status="error",
                    error=f"sandbox shim HTTP {resp.status_code}: {resp.text[:300]}",
                    error_type="SandboxServe",
                )
            parsed = SandboxResponse.model_validate_json(resp.content)
            if self._metrics is not None:
                self._metrics.inc(
                    "sandbox_execution_total", labels={"provider": self.provider_name}
                )
            return parsed
        except Exception as error:  # noqa: BLE001 -- report + cleanup
            if isinstance(error, KubernetesSandboxError):
                raise
            return SandboxResponse(
                version=request.version,
                status="error",
                error=str(error)[:500],
                error_type=type(error).__name__,
            )
        finally:
            # lifecycle: delete the Pod on EVERY path (success/failure/timeout)
            try:
                await self._delete_pod(name, grace=0)
            except Exception:  # noqa: BLE001 -- best-effort
                pass
            self._active.discard(str(execution_id))

    async def execute(
        self,
        execution_id: UUID,
        profile: SandboxProfile,
        operation: Any,
        secrets: dict[str, str] | None = None,
    ) -> Any:
        raise SandboxExecutionError(
            "KubernetesSandboxProvider does not accept arbitrary callables: use "
            "execute_request with the typed protocol (no cloudpickle across the "
            "Pod trust boundary)"
        )

    async def terminate(self, execution_id: UUID) -> bool:
        name = self._pod_name(execution_id)
        try:
            await self._delete_pod(name, grace=0)
            self._active.discard(str(execution_id))
            return True
        except Exception:  # noqa: BLE001
            return False

    async def health(self) -> bool:
        try:
            # probe: API reachable? A NotFound (404) for a random name still
            # proves the API + RBAC work; any other ApiException means the
            # worker cannot manage sandbox Pods and must not claim work.
            await self._get_pod(f"cap-health-probe-{os.getpid()}-{time.time_ns()}")
            return True
        except Exception as error:  # noqa: BLE001
            from kubernetes import client as k8s_client

            if isinstance(error, k8s_client.ApiException) and error.status == 404:
                return True
            return False

    async def reconcile_orphans(self, *, owned_executions: set[str]) -> int:
        """Delete cap-managed sandbox Pods whose execution is no longer owned.

        The reaper uses EXECUTION identity + lease identity + current DB
        ownership (never just run_id). Returns the number of pods deleted.
        """
        pods = await self._list_pods(label_selector=f"{LABEL_MANAGED}=true")
        deleted = 0
        for pod in pods:
            labels = pod.get("metadata", {}).get("labels", {})
            execution_id = labels.get(LABEL_EXECUTION, "")
            if execution_id and execution_id not in owned_executions:
                try:
                    await self._delete_pod(pod["metadata"]["name"], grace=0)
                    deleted += 1
                except Exception:  # noqa: BLE001 -- best-effort
                    pass
        return deleted
