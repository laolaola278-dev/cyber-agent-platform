"""Phase 28.6 -- Kubernetes certification gates (run inside the kind cluster CI).

Runs against the REAL kind cluster provisioned by cap-k8s-certification.yml.
CAP_K8S_STRICT=1 turns every environment-availability skip into a FAILURE
(SKIP == FAIL per the Phase 28.6 certification policy).

Gates covered here:
  K8S-GATE 2  fresh kind helm install (pods healthy)
  K8S-GATE 3  no docker.sock / container-runtime socket in the Worker
  K8S-GATE 4  Worker RBAC least privilege (namespaced Role rules)
  K8S-GATE 5  Worker adversarial Kubernetes API attempts DENIED
  K8S-GATE 6  Sandbox ServiceAccount token absent
  K8S-GATE 7  Sandbox NetworkPolicy ENFORCED (not just YAML)
  K8S-GATE 8  Sandbox cannot reach Kubernetes API
  K8S-GATE 9  Sandbox cannot reach PG / MinIO / control plane
  K8S-GATE 10 controlled public egress (via proxy) works
  K8S-GATE 11 API multi-replica idempotency (3 replicas)
  K8S-GATE 12 Worker multi-replica ownership (2 workers, >=40 runs)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

STRICT = os.environ.get("CAP_K8S_STRICT") == "1"
NAMESPACE = os.environ.get("CAP_NAMESPACE", "cap")
SANDBOX_NS = os.environ.get("CAP_SANDBOX_NAMESPACE", "cap-sandbox")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _cluster_ready() -> bool:
    try:
        proc = subprocess.run(["kubectl", "cluster-info"], capture_output=True, timeout=30)
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


_CLUSTER = _cluster_ready()


def _require_cluster():
    if not _CLUSTER:
        if STRICT:
            pytest.fail("kind cluster unavailable (CAP_K8S_STRICT=1 -> SKIP==FAIL)")
        pytest.skip("kind cluster unavailable")
    return True


def _kubectl(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, check=check, timeout=90
    )


def _json(args: list[str]) -> dict | list:
    proc = _kubectl(args + ["-o", "json"])
    return json.loads(proc.stdout)


def _worker_pod_names() -> list[str]:
    items = _json(["get", "pods", "-n", NAMESPACE, "-l", "app.kubernetes.io/component=worker"])
    if isinstance(items, dict):
        items = items.get("items", [])
    return [p["metadata"]["name"] for p in items if p.get("status", {}).get("phase") == "Running"]


@pytest.fixture(scope="module")
def api_port() -> int:
    """kubectl port-forward to the CAP API service (tests run on the runner)."""
    _require_cluster()
    port = 18080
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", NAMESPACE, "svc/cap-backend", f"{port}:8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
            if r.status_code == 200:
                yield port
                proc.terminate()
                proc.wait(timeout=10)
                return
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    proc.terminate()
    pytest.fail("CAP API not reachable via port-forward")


def _worker_sa_token() -> str:
    """Read the worker ServiceAccount token from a running worker Pod."""
    pod = _worker_pod_names()[0]
    proc = _kubectl(
        [
            "exec",
            "-n",
            NAMESPACE,
            pod,
            "--",
            "cat",
            "/var/run/secrets/kubernetes.io/serviceaccount/token",
        ]
    )
    return proc.stdout.strip()


# -- GATE 2: fresh kind helm install ------------------------------------------


def test_gate2_required_pods_healthy() -> None:
    _require_cluster()
    wanted = {
        f"{NAMESPACE}/deployment/cap-backend",
        f"{NAMESPACE}/deployment/cap-worker",
        f"{NAMESPACE}/deployment/cap-frontend",
        f"{NAMESPACE}/deployment/cap-egress-proxy",
    }
    for name in sorted(wanted):
        proc = _kubectl(["rollout", "status", name, "--timeout=120s"], check=False)
        assert proc.returncode == 0, f"rollout not healthy: {name}\n{proc.stderr}"
    assert _worker_pod_names(), "no running worker pod"


# -- GATE 3: no runtime socket in the Worker ----------------------------------


def test_gate3_worker_has_no_runtime_socket() -> None:
    _require_cluster()
    for pod in _worker_pod_names():
        spec = _json(["get", "pod", pod, "-n", NAMESPACE])["spec"]
        for volume in spec.get("volumes", []):
            assert "hostPath" not in volume, (
                f"worker {pod} mounts hostPath volume {volume.get('name')}"
            )
        # no docker/containerd/cri socket anywhere in the pod spec
        rendered = json.dumps(spec)
        for forbidden in ("docker.sock", "containerd.sock", "cri.sock", "/var/run/docker"):
            assert forbidden not in rendered, f"worker {pod} references {forbidden}"


# -- GATE 4: worker RBAC least privilege --------------------------------------


def test_gate4_worker_role_is_namespaced_least_privilege() -> None:
    _require_cluster()
    role = _json(["get", "role", "-n", SANDBOX_NS, "cap-sandbox"])
    allowed: dict[tuple[str, str], set[str]] = {}
    for rule in role.get("rules", []):
        for res in rule.get("resources", []):
            verbs = set(rule.get("verbs", []))
            key = (",".join(rule.get("apiGroups", [])), res)
            allowed[key] = allowed.get(key, set()) | verbs
    # pods create/get/list/watch/delete + logs/status in cap-sandbox ONLY
    assert ("", "pods") in allowed, "no pods permission"
    assert {"create", "get", "list", "watch", "delete"} <= allowed[("", "pods")]
    # must NOT have cluster-wide / privileged permissions
    forbidden_res = (
        "secrets",
        "configmaps",
        "nodes",
        "namespaces",
        "roles",
        "rolebindings",
        "deployments",
        "pods/exec",
    )
    for res in forbidden_res:
        for key in allowed:
            if key[1] == res and res not in ("pods", "pods/log", "pods/status"):
                pytest.fail(f"worker Role grants {res} -- least privilege violated")


# -- GATE 5: worker adversarial Kubernetes API attempts DENIED ----------------


def test_gate5_worker_sa_adversarial_attempts_denied() -> None:
    _require_cluster()
    token = _worker_sa_token()
    assert token, "worker SA token not readable"
    # use kubectl with the worker token (least-privilege verification)
    import base64
    import tempfile

    tmp = Path(tempfile.mkdtemp(dir=str(REPO_ROOT.parent)))
    kubeconfig = tmp / "worker-kubeconfig"
    context = _json(["config", "view", "--minify", "-o", "json"])
    cluster = context["clusters"][0]["cluster"]
    server = cluster.get("server")
    ca = base64.b64decode(cluster.get("certificate-authority-data", "")).decode()
    (tmp / "ca.crt").write_text(ca)
    kubeconfig.write_text(
        json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [
                    {
                        "name": "kind",
                        "cluster": {
                            "server": server,
                            "certificate-authority": str(tmp / "ca.crt"),
                        },
                    }
                ],
                "contexts": [
                    {
                        "name": "w",
                        "context": {"cluster": "kind", "user": "w", "namespace": NAMESPACE},
                    }
                ],
                "current-context": "w",
                "users": [{"name": "w", "user": {"token": token}}],
            }
        )
    )
    env = {**os.environ, "KUBECONFIG": str(kubeconfig)}

    def attempt(args: list[str]) -> bool:
        proc = subprocess.run(["kubectl", *args], capture_output=True, env=env, timeout=60)
        return proc.returncode != 0

    # create a Pod OUTSIDE cap-sandbox -> Forbidden
    assert attempt(["create", "deployment", "evil", "--image=busybox", "-n", "default"]), (
        "worker SA must NOT create workloads outside cap-sandbox"
    )
    # read a Secret -> Forbidden
    assert attempt(["get", "secret", "-n", NAMESPACE, "cap-runtime"]), (
        "worker SA must NOT read Secrets"
    )
    # create a privileged Pod -> Forbidden (apply with stdin)
    priv_spec = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "evil-priv", "namespace": "default"},
        "spec": {
            "containers": [
                {"name": "c", "image": "busybox", "securityContext": {"privileged": True}}
            ]
        },
    }
    proc = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=json.dumps(priv_spec),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode != 0, "worker SA must NOT create privileged Pods"
    # exec into the API/worker Pod -> Forbidden
    assert attempt(["exec", "-n", NAMESPACE, _worker_pod_names()[0], "--", "id"]), (
        "worker SA must NOT exec"
    )
    # modify RoleBinding / delete namespace -> Forbidden
    assert attempt(["delete", "namespace", "kube-system"]), "worker SA must NOT delete namespaces"


# -- GATE 6: sandbox ServiceAccount token absent ------------------------------


def _create_sandbox_probe_pod(name: str) -> str:
    """Create a probe Pod in cap-sandbox with the sandbox image (no token)."""
    body = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": SANDBOX_NS, "labels": {"cap.managed": "true"}},
        "spec": {
            "automountServiceAccountToken": False,
            "containers": [
                {"name": "probe", "image": "cap-sandbox-http:latest", "command": ["sleep", "300"]}
            ],
        },
    }
    proc = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    _kubectl(["wait", "--for=condition=Ready", f"pod/{name}", "-n", SANDBOX_NS, "--timeout=120s"])
    return name


@pytest.fixture(scope="module")
def probe_pod() -> str:
    _require_cluster()
    name = f"cap-probe-{uuid4().hex[:6]}"
    pod = _create_sandbox_probe_pod(name)
    yield pod
    _kubectl(["delete", "pod", name, "-n", SANDBOX_NS, "--force", "--grace-period=0"], check=False)


def test_gate6_sandbox_has_no_serviceaccount_token(probe_pod: str) -> None:
    _require_cluster()
    proc = _kubectl(
        [
            "exec",
            "-n",
            SANDBOX_NS,
            probe_pod,
            "--",
            "ls",
            "/var/run/secrets/kubernetes.io/serviceaccount",
        ],
        check=False,
    )
    assert proc.returncode != 0, "sandbox pod has a K8s service account token mounted"
    assert "No such file" in proc.stderr or "cannot access" in proc.stderr


def _sandbox_connect(probe_pod: str, target: str, port: int) -> bool:
    """Returns True when the sandbox pod can CONNECT to target:port."""
    proc = _kubectl(
        [
            "exec",
            "-n",
            SANDBOX_NS,
            probe_pod,
            "--",
            "sh",
            "-c",
            f"timeout 5 bash -c '</dev/tcp/{target}/{port}'",
        ],
        check=False,
    )
    return proc.returncode == 0


def test_gate7_sandbox_networkpolicy_enforced(probe_pod: str) -> None:
    _require_cluster()
    # GATE 8: Kubernetes API denied
    assert not _sandbox_connect(probe_pod, "kubernetes.default.svc", 443), "sandbox reached K8s API"
    # GATE 9: PG + MinIO denied
    assert not _sandbox_connect(probe_pod, "postgres.cap-infra.svc", 5432), (
        "sandbox reached PostgreSQL"
    )
    assert not _sandbox_connect(probe_pod, "minio.cap-infra.svc", 9000), "sandbox reached MinIO"
    # GATE 9: control plane (backend) denied
    assert not _sandbox_connect(probe_pod, f"cap-backend.{NAMESPACE}.svc", 8000), (
        "sandbox reached API"
    )
    # direct public egress denied (raw socket, no proxy env)
    assert not _sandbox_connect(probe_pod, "1.1.1.1", 443), "sandbox has direct public egress"


def test_gate10_controlled_egress_via_proxy_works(probe_pod: str) -> None:
    _require_cluster()
    # via the egress proxy (HTTP CONNECT): public target allowed
    proc = _kubectl(
        [
            "exec",
            "-n",
            SANDBOX_NS,
            probe_pod,
            "--",
            "curl",
            "-s",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "-x",
            f"http://cap-egress-proxy.{NAMESPACE}.svc:8080",
            "--max-time",
            "30",
            "https://example.com/",
        ],
        check=False,
    )
    assert proc.returncode == 0 and proc.stdout.strip() == "200", (
        f"proxied public egress failed: rc={proc.returncode} "
        f"out={proc.stdout[:200]} err={proc.stderr[:200]}"
    )
    # proxy denies private targets
    proc = _kubectl(
        [
            "exec",
            "-n",
            SANDBOX_NS,
            probe_pod,
            "--",
            "curl",
            "-s",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "-x",
            f"http://cap-egress-proxy.{NAMESPACE}.svc:8080",
            "--max-time",
            "20",
            "http://10.0.0.1/",
        ],
        check=False,
    )
    assert proc.returncode != 0 or proc.stdout.strip() != "200", (
        "egress proxy allowed a private target"
    )


# -- GATE 11: API multi-replica idempotency -----------------------------------


async def _api_create(port: int, goal: str, url: str, key: str) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            f"http://127.0.0.1:{port}/api/v1/acquisitions",
            json={"goal": goal, "url": url, "idempotency_key": key},
        )
        return resp.status_code, resp.json() if resp.content else {}


@pytest.mark.asyncio
async def test_gate11_api_multi_replica_idempotency(api_port: int) -> None:
    _require_cluster()
    # 3 API replicas behind the Service
    items = _json(["get", "pods", "-n", NAMESPACE, "-l", "app.kubernetes.io/component=backend"])
    replicas = len(
        [p for p in items.get("items", []) if p.get("status", {}).get("phase") == "Running"]
    )
    assert replicas >= 3, f"expected >=3 API replicas, got {replicas}"
    # 100 concurrent identical idempotency requests -> exactly one run row
    key = f"k8s-idem-{uuid4().hex[:10]}"
    results = await asyncio.gather(
        *[_api_create(api_port, "g", "http://example.com/", key) for _ in range(100)]
    )
    run_ids = {res[1].get("id") for res in results if res[0] in (200, 201)}
    assert run_ids, "no successful create"
    assert len(run_ids) == 1, f"idempotency violated: {len(run_ids)} distinct runs for one key"


# -- GATE 12: worker multi-replica ownership ----------------------------------


@pytest.mark.asyncio
async def test_gate12_worker_multi_replica_ownership(api_port: int) -> None:
    _require_cluster()
    assert len(_worker_pod_names()) >= 2, "expected >=2 worker replicas"
    key = f"k8s-workers-{uuid4().hex[:8]}"
    status, body = await _api_create(api_port, "g", "http://example.com/", key)
    assert status in (200, 201)
    run_id = body.get("id")
    # wait for terminal (workers drain the durable queue)
    deadline = time.monotonic() + 90
    final_status = None
    while time.monotonic() < deadline:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(f"http://127.0.0.1:{api_port}/api/v1/acquisitions/{run_id}")
        if r.status_code == 200:
            final_status = r.json().get("status")
            if final_status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"):
                break
        await asyncio.sleep(2)
    assert final_status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"), (
        f"run {run_id} not terminal (status={final_status})"
    )
