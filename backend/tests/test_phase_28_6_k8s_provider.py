"""Phase 28.6 -- KubernetesSandboxProvider unit tests (typed protocol transport).

Uses a FAKE k8s API (records create/read/delete) pointed at a REAL local shim
(--serve mode) so the typed protocol transport, readiness probe, pod spec
labels/security, and cleanup lifecycle are all exercised without a cluster.
The real kind-cluster gates (RBAC/NetworkPolicy/Pod kill/DR) run in the
Phase 28.6 certification workflow (cap-k8s-certification.yml).
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.sandbox.k8s_provider import (
    LABEL_ATTEMPT,
    LABEL_EXECUTION,
    LABEL_LEASE,
    LABEL_MANAGED,
    LABEL_RUN,
    LABEL_WORKER,
    KubernetesSandboxProvider,
)
from app.sandbox.oci_protocol import (
    PROTOCOL_VERSION,
    SandboxRequest,
)
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import SandboxExecutionError


class _FakeK8sApi:
    """In-memory k8s CoreV1Api double (methods run inside asyncio.to_thread)."""

    def __init__(self, pod_ip: str = "127.0.0.1") -> None:
        self.pod_ip = pod_ip
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.pods: dict[str, dict] = {}
        self.create_calls = 0

    def create_namespaced_pod(self, namespace: str, body: dict) -> dict:
        self.created.append(body)
        self.create_calls += 1
        name = body["metadata"]["name"]
        self.pods[name] = {
            "metadata": {"name": name, "labels": body["metadata"]["labels"]},
            "status": {"phase": "Running", "pod_ip": self.pod_ip},
        }
        return self.pods[name]

    def read_namespaced_pod(self, name: str, namespace: str) -> dict:
        return self.pods.get(name) or {
            "metadata": {"name": name, "labels": {}},
            "status": {"phase": "Pending", "pod_ip": None},
        }

    def delete_namespaced_pod(self, name: str, namespace: str, grace_period_seconds: int) -> dict:
        self.deleted.append(name)
        self.pods.pop(name, None)
        return {}

    def list_namespaced_pod(self, namespace: str, label_selector: str) -> object:
        class _List:  # noqa: N801
            items = [
                {"metadata": {"name": n, "labels": p["metadata"]["labels"]}}
                for n, p in self.pods.items()
            ]

        return _List()


def _profile() -> SandboxProfile:
    return SandboxProfile(name="p286", timeout_seconds=30, memory_mb=256, cpu_millicores=500)


def _request(operation: str = "http_fetch", url: str = "http://example.com/") -> SandboxRequest:
    return SandboxRequest(
        version=PROTOCOL_VERSION,
        operation=operation,
        run_id=str(uuid4()),
        sandbox_execution_id=str(uuid4()),
        url=url,
        policy={
            "allow_private": False,
            "allowed_schemes": ["http", "https"],
            "timeout_seconds": 15,
        },
    )


@pytest.fixture
async def local_shim() -> int:
    """Start the real shim in --serve mode on a free port (yields the port)."""
    port = 18090
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.sandbox.oci_shim",
        "--serve",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "CAP_SHIM_PORT": str(port)},
    )
    for _ in range(60):
        try:
            async with AsyncClient(timeout=1.0) as http:
                resp = await http.get(f"http://127.0.0.1:{port}/healthz")
            if resp.status_code == 200:
                yield port
                break
        except Exception:  # noqa: BLE001
            await asyncio.sleep(0.1)
    else:
        raise RuntimeError("local shim did not start")
    proc.kill()
    await proc.wait()


async def test_execute_request_transport_and_lifecycle(local_shim: int) -> None:
    fake = _FakeK8sApi(pod_ip="127.0.0.1")
    provider = KubernetesSandboxProvider(shim_port=local_shim, egress_proxy="")
    provider._client = fake  # inject the fake k8s API

    request = _request()
    run_id = request.run_id
    worker_id = str(uuid4())
    lease_id = str(uuid4())
    response = await provider.execute_request(
        _profile(),
        request,
        run_id=run_id,
        worker_id=worker_id,
        lease_id=lease_id,
        attempt=3,
    )

    assert response.status == "ok"
    assert response.result is not None
    assert response.result.get("status") == 200
    assert response.result.get("final_url") == "http://example.com/"

    # pod was created once with the safe identity labels (no tokens/URLs)
    assert fake.create_calls == 1
    labels = fake.created[0]["metadata"]["labels"]
    assert labels[LABEL_MANAGED] == "true"
    assert labels[LABEL_EXECUTION] == request.sandbox_execution_id
    assert labels[LABEL_RUN] == run_id
    assert labels[LABEL_WORKER] == worker_id
    assert labels[LABEL_LEASE] == lease_id
    assert labels[LABEL_ATTEMPT] == "3"
    assert "token" not in labels and "secret" not in labels and "url" not in labels

    # pod spec: no token, no host network, non-root, read-only, drop ALL
    spec = fake.created[0]["spec"]
    assert spec.get("automountServiceAccountToken") is False
    assert spec.get("hostNetwork") is False and spec.get("hostPID") is False
    container = spec["containers"][0]
    assert container["command"] == ["python", "-m", "sandbox.shim", "--serve"]
    assert container["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]

    # pod deleted in the finally (cleanup on success path)
    assert fake.deleted == [fake.created[0]["metadata"]["name"]]
    assert str(request.sandbox_execution_id) not in provider._active


async def test_secrets_fail_closed(local_shim: int) -> None:
    fake = _FakeK8sApi()
    provider = KubernetesSandboxProvider(shim_port=local_shim)
    provider._client = fake
    with pytest.raises(SandboxExecutionError, match="secret=False"):
        await provider.execute_request(
            _profile(), _request(), secrets={"AUTHORIZATION": "Bearer x"}
        )
    assert fake.create_calls == 0


async def test_shim_blocked_private_url_is_typed(local_shim: int) -> None:
    fake = _FakeK8sApi(pod_ip="127.0.0.1")
    provider = KubernetesSandboxProvider(shim_port=local_shim)
    provider._client = fake
    # a private URL must be rejected by the shim's application-layer policy:
    # the sandbox response is ok (shim ran) but the result carries
    # blocked_reason=SSRF_BLOCKED
    request = _request(url="http://10.0.0.1/private")
    response = await provider.execute_request(
        _profile(), request, run_id=request.run_id, worker_id="w", attempt=0
    )
    assert response.status == "ok"
    assert response.result is not None
    assert response.result.get("blocked_reason") == "SSRF_BLOCKED"
    assert fake.deleted, "pod must be cleaned up after a failed execution"


async def test_terminate_deletes_pod(local_shim: int) -> None:
    fake = _FakeK8sApi()
    provider = KubernetesSandboxProvider(shim_port=local_shim)
    provider._client = fake
    execution_id = uuid4()
    provider._active.add(str(execution_id))
    assert await provider.terminate(execution_id) is True
    assert fake.deleted == [provider._pod_name(execution_id)]
    assert str(execution_id) not in provider._active
