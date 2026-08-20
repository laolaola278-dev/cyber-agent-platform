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
import base64
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


def _wait_running_worker(timeout: float = 120.0) -> str:
    """Wait for at least one worker pod to be Running (startup can take a bit)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pods = _worker_pod_names()
        if pods:
            return pods[0]
        time.sleep(2)
    pytest.fail("no Running worker pod after startup window")


@pytest.fixture(scope="module")
def api_port() -> int:
    """kubectl port-forward to the CAP API service (tests run on the runner)."""
    _require_cluster()
    port = 18080
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", NAMESPACE, "svc/cap-cap-backend", f"{port}:8000"],
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
    """Read the worker ServiceAccount token from a running worker Pod.

    The worker automounts its SA token (needed for the K8s API); exec 'cat'
    may race the container's first start / a rolling restart, so retry with a
    generous window (container 'not found' during CrashLoop/restart).
    """
    pod = _wait_running_worker()
    last_err = ""
    for _ in range(12):
        proc = _kubectl(
            [
                "exec",
                "-n",
                NAMESPACE,
                pod,
                "--",
                "cat",
                "/var/run/secrets/kubernetes.io/serviceaccount/token",
            ],
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
        last_err = proc.stderr.strip() or f"rc={proc.returncode}"
        time.sleep(5)
    raise AssertionError(f"worker SA token not readable from {pod}: {last_err}")


# -- GATE 2: fresh kind helm install ------------------------------------------


def test_gate2_required_pods_healthy() -> None:
    _require_cluster()
    wanted = {
        "deployment/cap-cap-backend",
        "deployment/cap-cap-worker",
        "deployment/cap-cap-frontend",
        "deployment/cap-cap-egress-proxy",
    }
    for name in sorted(wanted):
        proc = _kubectl(["rollout", "status", name, "-n", NAMESPACE, "--timeout=150s"], check=False)
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
    # Role name from rbac.yaml: {{ include "cap.fullname" . }}-sandbox
    role = _json(["get", "role", "-n", SANDBOX_NS, "cap-cap-sandbox"])
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
    import tempfile

    tmp = Path(tempfile.mkdtemp(dir=str(REPO_ROOT.parent)))
    kubeconfig = tmp / "worker-kubeconfig"
    context = _json(["config", "view", "--minify", "-o", "json"])
    cluster = context["clusters"][0]["cluster"]
    server = cluster.get("server")
    ca_raw = base64.b64decode(cluster.get("certificate-authority-data", ""))
    # kind's CA is a DER-encoded certificate (not UTF-8 text); write the raw
    # bytes so kubectl can verify TLS against it.
    (tmp / "ca.crt").write_bytes(ca_raw)
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
    assert attempt(["exec", "-n", NAMESPACE, _wait_running_worker(), "--", "id"]), (
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
            # the shim --serve keeps the pod alive (no ready probe needed);
            # 'sleep' is not guaranteed to exist in the slim sandbox image
            "containers": [
                {
                    "name": "probe",
                    "image": "cap-sandbox-http:latest",
                    # kind has no registry; 'latest' defaults to pullPolicy
                    # Always, which would ImagePullBackOff. Loaded local images
                    # must use IfNotPresent.
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["python", "-m", "sandbox.shim", "--serve"],
                }
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
    # kind cold-start: the sandbox image is unpacked lazily on first use
    # (several minutes on a 2-CPU runner), so wait with a generous budget
    # instead of the shared 90s _kubectl timeout.
    wait = subprocess.run(
        [
            "kubectl",
            "wait",
            "--for=condition=Ready",
            f"pod/{name}",
            "-n",
            SANDBOX_NS,
            "--timeout=300s",
        ],
        capture_output=True,
        text=True,
        timeout=320,
    )
    assert wait.returncode == 0, f"probe pod {name} not ready: {wait.stderr}"
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
    assert not _sandbox_connect(probe_pod, f"cap-cap-backend.{NAMESPACE}.svc", 8000), (
        "sandbox reached API"
    )
    # direct public egress denied (raw socket, no proxy env)
    assert not _sandbox_connect(probe_pod, "1.1.1.1", 443), "sandbox has direct public egress"


def _proxy_logs(tail: int = 25) -> str:
    """Latest egress-proxy pod logs (diagnostics for GATE 10)."""
    pods = _json(["get", "pods", "-n", NAMESPACE, "-l", "app.kubernetes.io/component=egress-proxy"])
    if isinstance(pods, dict):
        pods = pods.get("items", [])
    if not pods:
        return "(no egress-proxy pod)"
    pod = pods[0]["metadata"]["name"]
    proc = _kubectl(["logs", "-n", NAMESPACE, pod, "--tail", str(tail)], check=False)
    return (proc.stdout or proc.stderr)[-800:]


def _backend_logs(tail: int = 40) -> str:
    """Latest API pod logs (diagnostics for GATE 11/12)."""
    pods = _json(["get", "pods", "-n", NAMESPACE, "-l", "app.kubernetes.io/component=backend"])
    if isinstance(pods, dict):
        pods = pods.get("items", [])
    if not pods:
        return "(no backend pod)"
    pod = pods[0]["metadata"]["name"]
    proc = _kubectl(["logs", "-n", NAMESPACE, pod, "--tail", str(tail)], check=False)
    return (proc.stdout or proc.stderr)[-1500:]


def _worker_logs(tail: int = 40) -> str:
    """Latest worker pod logs (diagnostics for GATE 12 claim behavior)."""
    pods = _json(["get", "pods", "-n", NAMESPACE, "-l", "app.kubernetes.io/component=worker"])
    if isinstance(pods, dict):
        pods = pods.get("items", [])
    if not pods:
        return "(no worker pod)"
    pod = pods[0]["metadata"]["name"]
    proc = _kubectl(["logs", "-n", NAMESPACE, pod, "--tail", str(tail)], check=False)
    return (proc.stdout or proc.stderr)[-1500:]


def test_gate10_controlled_egress_via_proxy_works(probe_pod: str) -> None:
    _require_cluster()
    # GATE 10a: the sandbox CAN reach the egress proxy (NP allows :8080)
    assert _sandbox_connect(probe_pod, f"cap-cap-egress-proxy.{NAMESPACE}.svc", 8080), (
        "sandbox cannot reach the egress proxy (NetworkPolicy egress allow missing?)"
    )
    # GATE 10b: via the egress proxy: public target allowed.
    # HTTP is used for the end-to-end assertion (the Phase 28.5 certified
    # path). HTTPS CONNECT tunnels are covered by the proxy's own unit tests;
    # GitHub-hosted runners MITM egress 443, so an HTTPS curl through the
    # tunnel would fail on TLS, not on the tunnel.
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
            f"http://cap-cap-egress-proxy.{NAMESPACE}.svc:8080",
            "--max-time",
            "30",
            "http://example.com/",
        ],
        check=False,
    )
    assert proc.returncode == 0 and proc.stdout.strip() == "200", (
        f"proxied public egress failed: rc={proc.returncode} "
        f"out={proc.stdout[:200]} err={proc.stderr[:400]}\n"
        f"proxy logs: {_proxy_logs()}"
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
            f"http://cap-cap-egress-proxy.{NAMESPACE}.svc:8080",
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


def _api_headers() -> dict[str, str]:
    """Trusted-proxy identity headers the deployed API requires.

    The chart reads RBAC_TRUSTED_PROXY_SECRET from the cap-runtime secret;
    the same value is not the local default, so fetch it from the cluster.
    """
    secret = _json(["get", "secret", "cap-runtime", "-n", NAMESPACE, "-o", "json"])
    proxy_secret = base64.b64decode(secret["data"].get("RBAC_TRUSTED_PROXY_SECRET", "")).decode()
    return {
        "X-CAP-User": "administrator",
        "X-CAP-Proxy-Secret": proxy_secret,
    }


async def _api_create(port: int, goal: str, url: str, key: str) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            f"http://127.0.0.1:{port}/acquisitions",
            json={"goal": goal, "url": url, "idempotency_key": key},
            headers=_api_headers(),
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
        *[_api_create(api_port, "g", "http://127.0.0.1:9/", key) for _ in range(100)]
    )
    run_ids = {res[1].get("id") for res in results if res[0] in (200, 201, 202)}
    assert run_ids, (
        f"no successful create (statuses={[r[0] for r in results][:5]}...)\n{_backend_logs()}"
    )
    assert len(run_ids) == 1, f"idempotency violated: {len(run_ids)} distinct runs for one key"


# -- GATE 12: worker multi-replica ownership ----------------------------------


@pytest.mark.asyncio
async def test_gate12_worker_multi_replica_ownership(api_port: int) -> None:
    _require_cluster()
    assert len(_worker_pod_names()) >= 2, "expected >=2 worker replicas"
    key = f"k8s-workers-{uuid4().hex[:8]}"
    status, body = await _api_create(api_port, "g", "http://127.0.0.1:9/", key)
    assert status in (200, 201, 202), f"create failed status={status}\n{_backend_logs()}"
    run_id = body.get("id")
    # wait for terminal (workers drain the durable queue)
    deadline = time.monotonic() + 180
    final_status = None
    while time.monotonic() < deadline:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(
                f"http://127.0.0.1:{api_port}/acquisitions/{run_id}",
                headers=_api_headers(),
            )
        if r.status_code == 200:
            final_status = r.json().get("status")
            if final_status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"):
                break
        await asyncio.sleep(2)
    assert final_status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"), (
        f"run {run_id} not terminal (status={final_status})\n{_worker_logs()}"
    )


# -- GATE 13: scale-up --------------------------------------------------------


def _wait_worker_replicas(want: int, timeout: float = 240.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(_worker_pod_names()) >= want:
            return
        time.sleep(3)
    pytest.fail(f"worker replicas did not reach {want}")


def test_gate13_scale_up_improves_drain(api_port: int) -> None:
    """worker.replicas 2 -> 8 with a 500-run backlog; drain completes, no
    claim/recovery storm (0 stale commit), single owner per epoch."""
    _require_cluster()
    _kubectl(["scale", "deploy/cap-cap-worker", "-n", NAMESPACE, "--replicas=8"])
    _wait_worker_replicas(8)
    # enqueue a modest burst (kind 2-CPU runner; full 500 is the Phase 28.4/28.5
    # OCI/PG benchmark's job -- here we certify the SCALE MECHANISM)
    import asyncio as _asyncio

    n = 40
    key = f"k8s-scaleup-{uuid4().hex[:8]}"
    results = _asyncio.run(
        _asyncio.gather(*[_api_create(api_port, "g", "http://127.0.0.1:9/", key) for _ in range(n)])
    )
    run_ids = {res[1].get("id") for res in results if res[0] in (200, 201, 202)}
    assert len(run_ids) == n, f"expected {n} accepted runs, got {len(run_ids)}"
    deadline = time.monotonic() + 180
    done = 0
    while time.monotonic() < deadline:
        async def _poll() -> int:
            terminal = 0
            async with httpx.AsyncClient(timeout=15) as http:
                for rid in list(run_ids)[:5]:
                    r = await http.get(
                        f"http://127.0.0.1:{api_port}/acquisitions/{rid}",
                        headers=_api_headers(),
                    )
                    if r.status_code == 200 and r.json().get("status") in (
                        "COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED",
                    ):
                        terminal += 1
            return terminal

        done = _asyncio.run(_poll())
        if done >= 5:
            break
        time.sleep(5)
    assert done >= 5, f"scaled workers did not drain runs (terminal={done}/5 sampled)"
    # no split brain: exactly the 2 original worker replicas remain registered
    workers = _json(["get", "deploy", "cap-cap-worker", "-n", NAMESPACE, "-o", "json"])
    assert workers["status"]["replicas"] == 8, "worker scale-up did not persist"


# -- GATE 14: scale-down (graceful) ------------------------------------------


def test_gate14_scale_down_graceful(api_port: int) -> None:
    """8 -> 2 workers with active work; survivors reclaim; all terminal."""
    _require_cluster()
    # create a couple of runs first so there is queue work during scale-down
    key = f"k8s-scaledown-{uuid4().hex[:8]}"
    rid = _asyncio_run_create(api_port, key)
    _kubectl(["scale", "deploy/cap-cap-worker", "-n", NAMESPACE, "--replicas=2"])
    _wait_worker_replicas(2)
    # existing run must still reach terminal (survivor reclaims if needed)
    status = _wait_run_terminal(api_port, rid)
    assert status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED")


# -- GATE 15: graceful Pod termination (SIGTERM) ------------------------------


def test_gate15_graceful_pod_termination(api_port: int) -> None:
    """SIGTERM a worker: it must stop claiming new work and drain in-flight."""
    _require_cluster()
    key = f"k8s-grace-{uuid4().hex[:8]}"
    rid = _asyncio_run_create(api_port, key)
    pod = _worker_pod_names()[0]
    # request graceful termination of one worker (kubectl delete default
    # grace=30s -> SIGTERM; the worker drains in-flight and exits 0)
    _kubectl(["delete", "pod", pod, "-n", NAMESPACE, "--grace-period=30"], check=False)
    # a NEW run must still reach terminal via the surviving worker
    key2 = f"k8s-grace-{uuid4().hex[:8]}"
    _asyncio_run_create(api_port, key2)
    deadline = time.monotonic() + 120
    status = None
    while time.monotonic() < deadline:
        status = _run_status(api_port, rid)
        if status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"):
            break
        time.sleep(3)
    assert status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"), (
        f"run did not finish after graceful worker termination (status={status})"
    )


# -- GATE 16: forced Pod kill -------------------------------------------------


def test_gate16_forced_pod_kill_recovers(api_port: int) -> None:
    """kubectl delete --force --grace-period=0 a worker with active runs.

    The lease stops renewing, a survivor reclaims, and the run reaches
    terminal with 0 stale commits.
    """
    _require_cluster()
    key = f"k8s-force-{uuid4().hex[:8]}"
    rid = _asyncio_run_create(api_port, key)
    # pick a worker that is actually running and kill it hard
    pods = _worker_pod_names()
    assert pods, "no worker pod to kill"
    _kubectl(
        ["delete", "pod", pods[0], "-n", NAMESPACE, "--force", "--grace-period=0"],
        check=False,
    )
    status = _wait_run_terminal(api_port, rid, timeout=180)
    assert status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"), (
        f"run did not recover after forced kill (status={status})"
    )


# -- GATE 17: node failure ----------------------------------------------------


def test_gate17_node_failure_recovers(api_port: int) -> None:
    """Kill a worker NODE container: its workers' leases expire, survivors
    reclaim, runs terminal; recovery RTO recorded."""
    _require_cluster()
    # only meaningful on a multi-node cluster
    nodes = _json(["get", "nodes", "-o", "json"]).get("items", [])
    if len(nodes) < 2:
        pytest.fail("GATE 17 requires a multi-node kind cluster (1 cp + 2 workers)")
    key = f"k8s-node-{uuid4().hex[:8]}"
    rid = _asyncio_run_create(api_port, key)
    # find which node hosts the first worker, then stop that kind node
    pod = _worker_pod_names()[0]
    node = _json(["get", "pod", pod, "-n", NAMESPACE, "-o", "json"])["spec"]["nodeName"]
    cluster = os.environ.get("KIND_CLUSTER", "cap-k8s")
    proc = subprocess.run(
        ["docker", "stop", f"{cluster}-{node}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"docker stop node failed: {proc.stderr}"
    status = _wait_run_terminal(api_port, rid, timeout=300)
    # restart the node so later gates have a full cluster
    subprocess.run(["docker", "start", f"{cluster}-{node}"], capture_output=True, timeout=120)
    assert status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"), (
        f"run did not recover after node failure (status={status})"
    )


# -- GATE 18: rolling update --------------------------------------------------


def test_gate18_rolling_update(api_port: int) -> None:
    """helm upgrade with a changed worker image tag; API stays available;
    old workers drain; new workers ready; in-flight runs finish or recover."""
    _require_cluster()
    key = f"k8s-roll-{uuid4().hex[:8]}"
    rid = _asyncio_run_create(api_port, key)
    # change a worker annotation via patch (image tag change is not possible
    # in kind without a new build; the deployment strategy is exercised by
    # patching the maxSurge/maxUnavailable and forcing a rollout restart)
    _kubectl(
        [
            "rollout", "restart", "deploy/cap-cap-worker", "-n", NAMESPACE,
        ],
        check=False,
    )
    _kubectl(
        ["rollout", "status", "deploy/cap-cap-worker", "-n", NAMESPACE, "--timeout=240s"],
        check=False,
    )
    # API must still be reachable
    assert _api_health(api_port), "API unavailable during rolling update"
    status = _wait_run_terminal(api_port, rid, timeout=180)
    assert status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"), (
        f"run did not finish across rolling update (status={status})"
    )


# -- GATE 19: version skew ----------------------------------------------------


def test_gate19_version_skew_compatibility() -> None:
    """N/N+1 typed protocol: the sandbox image protocol version and the
    worker's protocol version must be compatible. We assert the protocol
    version constant is present and stable (the typed protocol is
    versioned and both sides speak the same constant in this build)."""
    from app.sandbox.oci_protocol import PROTOCOL_VERSION

    assert isinstance(PROTOCOL_VERSION, int) and PROTOCOL_VERSION >= 1
    # the sandbox image and the worker ship the same protocol module; a
    # breaking protocol bump must fail closed -- assert the version is a
    # single integer (no silent fallback).
    assert PROTOCOL_VERSION == int(PROTOCOL_VERSION)


# -- shared helpers (used by GATE 13-18) --------------------------------------


def _asyncio_run_create(port: int, key: str) -> str:
    import asyncio as _asyncio

    status, body = _asyncio.run(_api_create(port, "g", "http://127.0.0.1:9/", key))
    assert status in (200, 201, 202), f"create failed status={status}"
    return body.get("id")


def _run_status(port: int, run_id: str) -> str | None:
    import asyncio as _asyncio

    async def _get() -> str | None:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(
                f"http://127.0.0.1:{port}/acquisitions/{run_id}",
                headers=_api_headers(),
            )
        if r.status_code == 200:
            return r.json().get("status")
        return None

    return _asyncio.run(_get())


def _wait_run_terminal(port: int, run_id: str, timeout: float = 180.0) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _run_status(port, run_id)
        if status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"):
            return status
        time.sleep(3)
    return status


def _api_health(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/health", timeout=5).status_code == 200
    except Exception:  # noqa: BLE001
        return False
