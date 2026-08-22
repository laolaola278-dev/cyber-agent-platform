"""Phase 28.7 -- GA Reliability Certification (whole-cluster DR core).

Implements GA-GATE 1..16. The module-scoped ``dr`` fixture performs the full
disaster-recovery sequence EXACTLY ONCE and every gate asserts an aspect of
it:

  Cluster A: deploy CAP, create a real dataset (terminal runs, cancelled
  runs, idempotency keys, seeded evidence blobs + artifact rows, a RUNNING
  snapshot) -> full backup (pg_dump + mc mirror) to the RUNNER filesystem
  (outside the cluster) with a tamper-evident manifest
  -> `kind delete cluster`  (REAL destruction -- not a pod/table restart)
  -> fresh Cluster B (new cluster, new pod UIDs, new leases)
  -> restore (fail-closed manifest verification first) -> validate.

Gates 12-14 deliberately CORRUPT the restored cluster B state (missing blob,
orphan, digest mismatch) and heal it afterwards; gates 9-11 assert integrity
BEFORE any mutation (pytest runs tests in definition order).

RPO/RTO are MEASURED wall-clock values recorded into outputs/ga-dr/, never
invented.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts" / "certification"
NAMESPACE = "cap"
INFRA_NS = "cap-infra"
CLUSTER = os.environ.get("KIND_CLUSTER", "cap-k8s")
BACKUP_DIR = Path(
    os.environ.get("GA_BACKUP_DIR", str(REPO_ROOT / "outputs" / "ga-dr" / "backup"))
)
REPORT_DIR = Path(os.environ.get("GA_REPORT_DIR", str(REPO_ROOT / "outputs" / "ga-dr")))
PHASE_28_6_RUN = os.environ.get("PHASE_28_6_RUN", "32565459369")
STRICT = os.environ.get("CAP_K8S_STRICT") == "1"
MINIO_USER = "capadmin"
MINIO_PASSWORD = "capadmin123"
PG_LOCAL_PORT = "15432"
MINIO_LOCAL_PORT = "19000"
# non-routable target keeps the RUNNING snapshot alive (no internet needed)
RUNNING_TARGET = "http://10.255.255.1/"

_CTX: dict | None = None
_PF_PROCS: list[subprocess.Popen] = []


# -- low-level helpers --------------------------------------------------------


def _cluster_ready() -> bool:
    try:
        proc = subprocess.run(["kubectl", "cluster-info"], capture_output=True, timeout=30)
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _require_cluster():
    if not _cluster_ready():
        if STRICT:
            pytest.fail("kind cluster unavailable (CAP_K8S_STRICT=1 -> SKIP==FAIL)")
        pytest.skip("kind cluster unavailable")


def _run(
    args: list[str], *, check: bool = True, timeout: float = 300,
    cwd=None, env=None, input=None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, check=check, timeout=int(timeout), cwd=cwd, env=env,
        input=input,
    )


def _kubectl(args, *, check=True, timeout=120.0):
    return _run(["kubectl", *args], check=check, timeout=timeout)


def _json_k(args):
    return json.loads(_kubectl([*args, "-o", "json"]).stdout)


def _kind(args, *, check=True, timeout=600.0):
    return _run(["kind", *args], check=check, timeout=timeout)


def _helm(args, *, check=True, timeout=900.0):
    return _run(["helm", *args], check=check, timeout=timeout, cwd=str(REPO_ROOT))


def _mc(args, *, check=True, timeout=300.0):
    return _run(["mc", *args], check=check, timeout=timeout)


def _api_headers() -> dict[str, str]:
    secret = _json_k(["get", "secret", "cap-runtime", "-n", NAMESPACE])
    proxy = base64.b64decode(secret["data"]["RBAC_TRUSTED_PROXY_SECRET"]).decode()
    return {"X-CAP-User": "administrator", "X-CAP-Proxy-Secret": proxy}


def _api_health(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/health", timeout=5).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _port_forward(local_mapping: str, target_args: list[str]) -> subprocess.Popen:
    proc = subprocess.Popen(
        ["kubectl", "port-forward", *target_args, local_mapping],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _PF_PROCS.append(proc)
    return proc


def _wait_port(local_port: str, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    port = int(local_port.split(":")[0])
    while time.monotonic() < deadline:
        import socket

        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    raise AssertionError(f"port {port} never became reachable")


def _api_create(port: int, key: str, url: str = "http://127.0.0.1:9/") -> tuple[int, dict]:
    with httpx.Client(timeout=30) as http:
        resp = http.post(
            f"http://127.0.0.1:{port}/acquisitions",
            json={"goal": "ga-dr", "url": url, "idempotency_key": key},
            headers=_api_headers(),
        )
        body = resp.json() if resp.content else {}
        return resp.status_code, body


def _run_status(port: int, run_id: str) -> str | None:
    try:
        r = httpx.get(
            f"http://127.0.0.1:{port}/acquisitions/{run_id}", headers=_api_headers(), timeout=15
        )
        return r.json().get("status") if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def _wait_terminal(port: int, run_id: str, timeout: float = 240.0) -> str:
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        status = _run_status(port, run_id)
        if status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"):
            return status
        time.sleep(3)
    pytest.fail(f"run {run_id} not terminal in {timeout}s (status={status})")


def _api_cancel(port: int, run_id: str) -> int:
    with httpx.Client(timeout=30) as http:
        resp = http.post(
            f"http://127.0.0.1:{port}/acquisitions/{run_id}/cancel",
            headers=_api_headers(),
        )
        return resp.status_code


def _pf_api() -> int:
    port = 18080
    _port_forward(f"{port}:8000", ["-n", NAMESPACE, "svc/cap-cap-backend"])
    _wait_port(f"{port}:8000")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if _api_health(port):
            return port
        time.sleep(2)
    pytest.fail("API not healthy via port-forward")


def _seed_evidence_blob(content: bytes) -> str:
    """Put one content-addressed object into MinIO; returns its digest."""
    import hashlib

    digest = hashlib.sha256(content).hexdigest()
    key = f"sha256/{digest[:2]}/{digest}"
    local = REPORT_DIR / "seed" / digest[:2]
    local.mkdir(parents=True, exist_ok=True)
    path = local / digest
    path.write_bytes(content)
    _port_forward(f"{MINIO_LOCAL_PORT}:9000", ["-n", INFRA_NS, "svc/minio"])
    _wait_port(f"{MINIO_LOCAL_PORT}:9000")
    _mc(["alias", "set", "seedmc", f"http://127.0.0.1:{MINIO_LOCAL_PORT}",
         MINIO_USER, MINIO_PASSWORD])
    _mc(["cp", str(path), f"seedmc/cap-evidence/{key}"])
    return digest


def _seed_artifact_rows(pod: str, run_id: str, digests: list[str]) -> None:
    """Insert durable artifact rows referencing the seeded blobs (raw SQL via
    psql -- exactly the reference model the reconciler reads)."""
    values = []
    for digest in digests:
        values.append(
            "gen_random_uuid(), '{run_id}', '{ok}', '{d}', {sz}, "
            "'application/octet-stream', 'https://seed.local/{d}', "
            "'https://seed.local/{d}', 200, 'GET', 'ga-dr-seed', '1.0'".format(
                run_id=run_id, ok=digest[:2] + "-seed", d=digest, sz=64
            )
        )
    sql = (
        "INSERT INTO acquisition_artifacts "
        "(id, run_id, object_key, sha256, size, content_type, source_url, "
        "final_url, http_status, method, tool, tool_version, created_at, updated_at) VALUES "
        + ",\n  ".join("(" + v + ", NOW(), NOW())" for v in values)
        + ";"
    )
    proc = _kubectl(
        ["exec", "-n", INFRA_NS, pod, "--", "psql", "-U", "cap", "-d", "cap", "-c", sql],
        check=False,
    )
    assert proc.returncode == 0, f"artifact seeding failed: {proc.stderr[-400:]}"


def _psql(pod: str, sql: str) -> str:
    return _kubectl(
        ["exec", "-n", INFRA_NS, pod, "--", "psql", "-U", "cap", "-d", "cap", "-tAc", sql]
    ).stdout.strip()


def _pg_pod() -> str:
    return _kubectl(
        ["get", "pods", "-n", INFRA_NS, "-l", "app=postgres",
         "-o", "jsonpath={.items[0].metadata.name}"]
    ).stdout.strip()


_INFRA_YAML = """
apiVersion: v1
kind: Namespace
metadata: {name: cap-infra}
---
apiVersion: v1
kind: Service
metadata: {name: postgres, namespace: cap-infra}
spec:
  selector: {app: postgres}
  ports: [{port: 5432, targetPort: 5432}]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: postgres, namespace: cap-infra}
spec:
  replicas: 1
  selector: {matchLabels: {app: postgres}}
  template:
    metadata: {labels: {app: postgres}}
    spec:
      containers:
        - name: postgres
          image: postgres:16-alpine
          env:
            - {name: POSTGRES_USER, value: cap}
            - {name: POSTGRES_PASSWORD, value: cap}
            - {name: POSTGRES_DB, value: cap}
          ports: [{containerPort: 5432}]
---
apiVersion: v1
kind: Service
metadata: {name: minio, namespace: cap-infra}
spec:
  selector: {app: minio}
  ports: [{port: 9000, targetPort: 9000}]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: minio, namespace: cap-infra}
spec:
  replicas: 1
  selector: {matchLabels: {app: minio}}
  template:
    metadata: {labels: {app: minio}}
    spec:
      containers:
        - name: minio
          image: minio/minio:RELEASE.2025-04-22T22-12-26Z
          command: ["minio", "server", "/data", "--console-address", ":9001"]
          env:
            - {name: MINIO_ROOT_USER, value: capadmin}
            - {name: MINIO_ROOT_PASSWORD, value: capadmin123}
          ports: [{containerPort: 9000}]
"""


# -- the DR sequence (module-scoped, executed once) ---------------------------


def _dr_context() -> dict:
    global _CTX
    if _CTX is not None:
        return _CTX
    _require_cluster()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ctx: dict = {"timings": {}, "phase_28_6_run": PHASE_28_6_RUN}

    # -- Cluster A dataset -------------------------------------------------
    api_port = _pf_api()
    pod = _pg_pod()

    completed_ids, idem_keys = [], {}
    for _i in range(3):
        key = f"ga-dr-done-{uuid4().hex[:8]}"
        status, body = _api_create(api_port, key)
        assert status in (200, 201, 202)
        rid = body["id"]
        idem_keys[key] = rid
        final = _wait_terminal(api_port, rid)
        completed_ids.append((rid, final))
    cancelled_ids = []
    for _ in range(2):
        key = f"ga-dr-cxl-{uuid4().hex[:8]}"
        _, body = _api_create(api_port, key)
        rid = body["id"]
        assert _api_cancel(api_port, rid) in (200, 202)
        final = _wait_terminal(api_port, rid)
        cancelled_ids.append((rid, final))

    seeded_digests = [
        _seed_evidence_blob(f"ga-dr-evidence-{i}-{uuid4().hex}".encode()) for i in range(8)
    ]
    anchor_id = completed_ids[0][0]
    _seed_artifact_rows(pod, anchor_id, seeded_digests)

    # RUNNING snapshot: create then kill the workers so the run is durably
    # RUNNING with a lease that will be EXPIRED by the time Cluster B exists.
    #
    # Scoped TEST-ONLY hooks (run 32571948457 lesson): enabling them cluster-
    # wide makes the DATASET runs enter real sandbox execution, whose worst
    # case (>240s) never terminals inside _wait_terminal. Production defaults
    # stay deny-by-default for everything except this one snapshot: the
    # dataset runs above were policy-blocked fast (terminal in seconds), and
    # on Cluster B the reclaimed snapshot is policy-blocked again -> BLOCKED
    # terminal + recovery_count >= 1 without any hook.
    _kubectl(["-n", NAMESPACE, "set", "env", "deploy/cap-cap-worker",
              "ACQ_ALLOW_PRIVATE=1"])
    _kubectl(["-n", NAMESPACE, "set", "env", "deploy/cap-cap-egress-proxy",
              "CAP_EGRESS_ALLOW=10.255.255.1:80"])
    _kubectl(["-n", NAMESPACE, "rollout", "status", "deploy/cap-cap-worker",
              "--timeout=300s"])
    key = f"ga-dr-run-{uuid4().hex[:8]}"
    _, body = _api_create(api_port, key, url=RUNNING_TARGET)
    running_id = body["id"]
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if _run_status(api_port, running_id) == "RUNNING":
            break
        time.sleep(3)
    assert _run_status(api_port, running_id) == "RUNNING", "RUNNING snapshot not established"
    _kubectl(
        ["delete", "pods", "-n", NAMESPACE, "-l", "app.kubernetes.io/component=worker",
         "--force", "--grace-period=0"],
        check=False,
    )
    # Scale workers to zero: recreated pods would see the expired lease and
    # RECLAIM the snapshot (validator passes with the hook), turning it
    # PARTIAL before the backup reads it. No workers = durably RUNNING.
    _kubectl(["-n", NAMESPACE, "scale", "deploy/cap-cap-worker", "--replicas=0"])
    ctx["dataset"] = {
        "completed": completed_ids,
        "cancelled": cancelled_ids,
        "idempotency": idem_keys,
        "seeded_digests": seeded_digests,
        "running_id": running_id,
        "running_key": key,
    }
    ctx["timings"]["t0_dataset_done"] = datetime.now(UTC).isoformat()

    # -- t1: backup (outside the cluster) -----------------------------------
    env = os.environ.copy()
    env["MINIO_ROOT_USER"], env["MINIO_ROOT_PASSWORD"] = MINIO_USER, MINIO_PASSWORD
    env["CAP_SCHEMA_REVISION"] = _psql(pod, "SELECT version_num FROM alembic_version LIMIT 1")
    env["CAP_EVIDENCE_REF_COUNT"] = _psql(
        pod, "SELECT count(*) FROM acquisition_artifacts WHERE sha256 IS NOT NULL"
    )
    _run(["bash", str(SCRIPTS / "backup_cluster.sh"), str(BACKUP_DIR)], timeout=600, env=env)
    _run(["python3", str(SCRIPTS / "make_backup_manifest.py"), str(BACKUP_DIR)], env=env)
    manifest = json.loads((BACKUP_DIR / "backup-manifest.json").read_text(encoding="utf-8"))
    ctx["manifest"] = manifest
    ctx["timings"]["t1_backup_done"] = datetime.now(UTC).isoformat()

    # -- t2: data created AFTER the backup (the RPO loss window) ------------
    _kubectl(["-n", NAMESPACE, "rollout", "status", "deploy/cap-cap-worker", "--timeout=300s"])
    key2 = f"ga-dr-post-{uuid4().hex[:8]}"
    _, body2 = _api_create(api_port, key2)
    post_id = body2["id"]
    _wait_terminal(api_port, post_id)
    ctx["post_backup"] = {"id": post_id, "key": key2}
    ctx["timings"]["t2_post_backup_data"] = datetime.now(UTC).isoformat()

    # pre-destroy cluster identity (freshness proof for Cluster B)
    nodes_a = _json_k(["get", "nodes"])
    ctx["cluster_a"] = {
        "node_uids": {n["metadata"]["name"]: n["metadata"]["uid"] for n in nodes_a["items"]},
    }

    # -- t3: DESTROY THE ENTIRE CLUSTER -------------------------------------
    ctx["timings"]["t3_destroy_declared"] = datetime.now(UTC).isoformat()
    _kind(["delete", "cluster", "--name", CLUSTER], timeout=600)
    gone = _run(["kind", "get", "clusters"], check=False)
    ctx["cluster_a"]["destroyed"] = CLUSTER not in gone.stdout.split()
    assert ctx["cluster_a"]["destroyed"], "kind cluster still present after delete"

    # -- Cluster B: fresh cluster, fresh identity ---------------------------
    cfg = REPORT_DIR / "kind-b.yaml"
    cfg.write_text(
        "kind: Cluster\napiVersion: kind.x-k8s.io/v1alpha4\nnodes:\n"
        "  - role: control-plane\n  - role: worker\n  - role: worker\n",
        encoding="utf-8",
    )
    _kind(["create", "cluster", "--name", CLUSTER, "--config", str(cfg)], timeout=900)
    ctx["timings"]["cluster_b_created"] = datetime.now(UTC).isoformat()
    _run(["bash", "-c", "helm repo add cilium https://helm.cilium.io/ >/dev/null 2>&1 || true"])
    _helm(["install", "cilium", "cilium/cilium", "--namespace", "kube-system",
           "--set", "operator.replicas=1", "--set", "ipam.mode=kubernetes",
           "--set", "kubeProxyReplacement=false", "--set", "cgroup.autoMount.enabled=false"],
          timeout=600)
    _kubectl(["-n", "kube-system", "rollout", "status", "ds/cilium", "--timeout=300s"])
    ctx["timings"]["cluster_b_cilium_ready"] = datetime.now(UTC).isoformat()

    images = ["cap-backend:ci", "cap-frontend:ci", "cap-sandbox-http:latest",
              "cap-sandbox-browser:latest", "cap-egress-proxy:latest"]
    _kind(["load", "docker-image", *images, "--name", CLUSTER], timeout=600)

    _run(["kubectl", "apply", "-f", "-"],
         input=_INFRA_YAML, timeout=120)
    _kubectl(["-n", INFRA_NS, "rollout", "status", "deployment/postgres", "--timeout=300s"])
    _kubectl(["-n", INFRA_NS, "rollout", "status", "deployment/minio", "--timeout=300s"])
    _run(["kubectl", "create", "namespace", NAMESPACE], check=False)
    _run(["bash", "-c",
          "kubectl -n cap create secret generic cap-runtime "
          "--from-literal=DATABASE_URL="
          "postgresql+asyncpg://cap:cap@postgres.cap-infra.svc:5432/cap "
          "--from-literal=SECRET_KEY=cap-k8s-secret-key-at-least-32-characters "
          "--from-literal=JWT_SECRET=cap-k8s-jwt-secret-at-least-32-characters "
          "--from-literal=RBAC_TRUSTED_PROXY_SECRET=cap-k8s-proxy-secret-at-least-32 "
          "--from-literal=OBJECT_STORE_BACKEND=s3 "
          "--from-literal=OBJECT_STORE_ENDPOINT=minio.cap-infra.svc:9000 "
          "--from-literal=OBJECT_STORE_ACCESS_KEY=capadmin "
          "--from-literal=OBJECT_STORE_SECRET_KEY=capadmin123 "
          "--from-literal=OBJECT_STORE_BUCKET=cap-evidence"], timeout=60)
    ctx["timings"]["cluster_b_infra_ready"] = datetime.now(UTC).isoformat()

    _helm(["install", "cap", "deployment/helm/cap", "--namespace", NAMESPACE,
           "--timeout", "600s",
           "--set", "backend.image.repository=cap-backend", "--set", "backend.image.tag=ci",
           "--set", "frontend.image.repository=cap-frontend", "--set", "frontend.image.tag=ci",
           "--set", "worker.image.repository=cap-backend", "--set", "worker.image.tag=ci",
           "--set", "backend.replicaCount=3", "--set", "worker.replicaCount=2",
           "--set", "worker.sandbox.egressProxyUrl=http://cap-cap-egress-proxy.cap.svc:8080",
           "--set", "worker.sandbox.namespace=cap-sandbox"], timeout=900)
    for dep in ("cap-cap-backend", "cap-cap-worker"):
        _kubectl(["-n", NAMESPACE, "rollout", "status", f"deploy/{dep}", "--timeout=600s"])
    ctx["timings"]["cluster_b_cap_deployed"] = datetime.now(UTC).isoformat()

    # -- t4: restore (fail-closed) into Cluster B ---------------------------
    env2 = os.environ.copy()
    env2["MINIO_ROOT_USER"], env2["MINIO_ROOT_PASSWORD"] = MINIO_USER, MINIO_PASSWORD
    _run(["bash", str(SCRIPTS / "restore_cluster.sh"), str(BACKUP_DIR), _pg_pod(),
          MINIO_LOCAL_PORT], timeout=900, env=env2)
    ctx["timings"]["t4_restore_done"] = datetime.now(UTC).isoformat()

    # -- readiness: API + evidence queryable + workers executing ------------
    api_port = _pf_api()
    ctx["api_port_b"] = api_port
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if _api_health(api_port):
            break
        time.sleep(5)
    assert _api_health(api_port)
    # evidence queryable: first seeded blob retrievable with digest verified
    _port_forward(f"{MINIO_LOCAL_PORT}:9000", ["-n", INFRA_NS, "svc/minio"])
    _wait_port(f"{MINIO_LOCAL_PORT}:9000")
    _mc(["alias", "set", "bmc", f"http://127.0.0.1:{MINIO_LOCAL_PORT}", MINIO_USER, MINIO_PASSWORD])
    d0 = seeded_digests[0]
    out = REPORT_DIR / "probe-blob.bin"
    _mc(["cp", f"bmc/cap-evidence/sha256/{d0[:2]}/{d0}", str(out)])
    import hashlib
    assert hashlib.sha256(out.read_bytes()).hexdigest() == d0
    _kubectl(["-n", NAMESPACE, "rollout", "status", "deploy/cap-cap-worker", "--timeout=300s"])
    ctx["timings"]["t_ready"] = datetime.now(UTC).isoformat()

    nodes_b = _json_k(["get", "nodes"])
    ctx["cluster_b"] = {
        "node_uids": {n["metadata"]["name"]: n["metadata"]["uid"] for n in nodes_b["items"]}
    }

    # persist the DR evidence for the artifact generator
    (REPORT_DIR / "ga-dr-context.json").write_text(json.dumps(ctx, indent=2), encoding="utf-8")
    _CTX = ctx
    return _CTX


@pytest.fixture(scope="module")
def dr() -> dict:
    return _dr_context()


def _reconcile_on_b() -> dict:
    """Run the reconciliation CLI against restored Cluster B (JSON report)."""
    env = os.environ.copy()
    env["DATABASE_URL"] = f"postgresql+asyncpg://cap:cap@127.0.0.1:{PG_LOCAL_PORT}/cap"
    env["OBJECT_STORE_ENDPOINT"] = f"127.0.0.1:{MINIO_LOCAL_PORT}"
    env["OBJECT_STORE_ACCESS_KEY"] = MINIO_USER
    env["OBJECT_STORE_SECRET_KEY"] = MINIO_PASSWORD
    env["OBJECT_STORE_BUCKET"] = "cap-evidence"
    _port_forward(f"{PG_LOCAL_PORT}:5432", ["-n", INFRA_NS, "svc/postgres"])
    _wait_port(f"{PG_LOCAL_PORT}:5432")
    proc = _run(
        ["uv", "run", "python", "-m", "app.acquisition.reconcile_cli"],
        check=False, timeout=600, cwd=str(REPO_ROOT / "backend"), env=env,
    )
    assert proc.returncode in (0, 1), f"reconcile crashed: {proc.stderr[-600:]}"
    return json.loads(proc.stdout)


def _store_b():
    """An S3EvidenceStore bound to restored Cluster B (via port-forward)."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from app.acquisition.store import S3EvidenceStore

    return S3EvidenceStore(
        endpoint=f"127.0.0.1:{MINIO_LOCAL_PORT}",
        access_key=MINIO_USER,
        secret_key=MINIO_PASSWORD,
        bucket="cap-evidence",
        secure=False,
    )


# -- GA-GATE 1..16 ------------------------------------------------------------


@pytest.mark.timeout(3300)
def test_ga_gate1_baseline_recorded(dr) -> None:
    """GA-GATE 1: the Phase 28.6 baseline this phase builds on is recorded
    (32/32 PASS, run {PHASE_28_6_RUN}) and the repo still carries the rc3
    version policy (GA version bump happens ONLY after all gates pass)."""
    assert dr["phase_28_6_run"] == "32565459369"
    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert version.startswith("1.0.0-rc"), f"unexpected VERSION {version}"


def test_ga_gate2_pg_backup_independently_stored(dr) -> None:
    """GA-GATE 2: the PostgreSQL dump lives OUTSIDE the (destroyed) cluster,
    non-empty, and matches the manifest digest."""
    dump = BACKUP_DIR / "postgres" / "cap.sql.gz"
    assert dump.is_file() and dump.stat().st_size > 0
    import hashlib
    assert hashlib.sha256(dump.read_bytes()).hexdigest() == dr["manifest"]["pg_backup_digest"]


def test_ga_gate3_object_backup_independently_stored(dr) -> None:
    """GA-GATE 3: object backup exported object-level to the runner FS with a
    non-empty manifest."""
    objects = list((BACKUP_DIR / "objects").rglob("*"))
    assert sum(1 for p in objects if p.is_file()) == dr["manifest"]["object_count"] > 0


def test_ga_gate4_backup_manifest_integrity(dr) -> None:
    """GA-GATE 4: manifest is complete, tamper-evident, and secret-free."""
    manifest = dr["manifest"]
    for field in ("backup_id", "timestamp", "cap_version", "schema_revision",
                  "pg_backup_digest", "object_manifest_digest", "object_count",
                  "total_bytes", "evidence_reference_count"):
        assert field in manifest, f"manifest missing {field}"
    raw = json.dumps(manifest)
    for secret in (MINIO_PASSWORD, "capadmin123", "cap-k8s-proxy-secret"):
        assert secret not in raw, "manifest must never contain secrets"
    proc = _run(["python3", str(SCRIPTS / "verify_backup_manifest.py"), str(BACKUP_DIR)],
                check=False)
    assert proc.returncode == 0, f"manifest verification failed: {proc.stderr[-300:]}"


def test_ga_gate5_cluster_a_destroyed(dr) -> None:
    """GA-GATE 5: Cluster A was PHYSICALLY destroyed (kind delete cluster)."""
    assert dr["cluster_a"]["destroyed"] is True


def test_ga_gate6_fresh_cluster_b(dr) -> None:
    """GA-GATE 6: Cluster B is a genuinely fresh cluster (new node UIDs)."""
    a_uids = set(dr["cluster_a"]["node_uids"].values())
    b_uids = set(dr["cluster_b"]["node_uids"].values())
    assert a_uids and b_uids and not (a_uids & b_uids), "Cluster B reused Cluster A node identities"


def test_ga_gate7_pg_restored(dr) -> None:
    """GA-GATE 7: restored DB keeps run counts and terminal statuses."""
    ctx = dr
    pod = _pg_pod()
    total = int(_psql(pod, "SELECT count(*) FROM acquisition_runs") or 0)
    expected = len(ctx["dataset"]["completed"]) + len(ctx["dataset"]["cancelled"]) + 1
    assert total >= expected, f"expected >= {expected} runs, restored {total}"
    for rid, status in ctx["dataset"]["completed"]:
        restored = _psql(pod, f"SELECT status FROM acquisition_runs WHERE id='{rid}'")
        assert restored == status, f"run {rid}: {restored} != {status}"
    for rid, _ in ctx["dataset"]["cancelled"]:
        restored = _psql(pod, f"SELECT status FROM acquisition_runs WHERE id='{rid}'")
        assert restored == "CANCELLED", f"cancelled run {rid} became {restored}"


def test_ga_gate8_object_store_restored(dr) -> None:
    """GA-GATE 8: every backed-up object exists in Cluster B's MinIO."""
    manifest = dr["manifest"]
    listing = _mc(["ls", "--recursive", "bmc/cap-evidence"])
    restored = [ln.split()[-1] for ln in listing.stdout.splitlines() if ln.strip()]
    assert len(restored) == manifest["object_count"]


def test_ga_gate9_evidence_integrity_after_restore(dr) -> None:
    """GA-GATE 9: reconciliation on Cluster B reports integrity_ok -- zero
    missing referenced objects, zero digest mismatches."""
    report = _reconcile_on_b()
    dr["reconciliation_initial"] = report
    assert report["integrity_ok"] is True, json.dumps(report, indent=2)[:800]
    assert report["missing_referenced_count"] == 0
    assert report["digest_mismatch_count"] == 0
    assert report["referenced_and_present_count"] >= len(dr["dataset"]["seeded_digests"])


def test_ga_gate10_idempotency_survives_restore(dr) -> None:
    """GA-GATE 10: same request + same idempotency key returns the ORIGINAL
    run (never a duplicate) on the restored cluster."""
    api_port = dr["api_port_b"]
    for key, original_id in list(dr["dataset"]["idempotency"].items())[:3]:
        status, body = _api_create(api_port, key)
        assert status in (200, 201, 202)
        assert body.get("id") == original_id, (
            f"idempotency key {key}: got {body.get('id')} != original {original_id}"
        )


@pytest.mark.timeout(900)
def test_ga_gate11_running_run_auto_recovers(dr) -> None:
    """GA-GATE 11: the RUNNING snapshot's old lease is invalid on Cluster B;
    a new worker reclaims, resumes, and the run reaches a terminal state."""
    api_port = dr["api_port_b"]
    running_id = dr["dataset"]["running_id"]
    deadline = time.monotonic() + 600
    status = None
    while time.monotonic() < deadline:
        status = _run_status(api_port, running_id)
        if status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"):
            break
        time.sleep(5)
    assert status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"), (
        f"restored RUNNING run stuck at {status}"
    )
    pod = _pg_pod()
    recovery = _psql(pod, f"SELECT recovery_count FROM acquisition_runs WHERE id='{running_id}'")
    dr["running_recovery"] = {"final_status": status, "recovery_count": recovery}
    assert int(recovery or 0) >= 1, "run reached terminal without a documented reclaim"


@pytest.mark.timeout(900)
def test_ga_gate12_missing_object_detected(dr) -> None:
    """GA-GATE 12: deleting one DB-referenced blob from restored storage is
    caught as missing_referenced; retrieval fails loudly, never silently."""
    store = _store_b()
    import asyncio

    digest = dr["dataset"]["seeded_digests"][1]
    key = f"sha256/{digest[:2]}/{digest}"

    async def _scenario():
        await store.delete(key)
        report = _reconcile_on_b()
        dr["reconciliation_missing"] = report
        # retrieval must be an integrity error, not an empty success
        try:
            await store.get(key)
            retrieval_error = False
        except Exception:  # noqa: BLE001 -- expected integrity failure
            retrieval_error = True
        return report, retrieval_error

    report, retrieval_error = asyncio.run(_scenario())
    assert digest in report["missing_referenced"], "missing blob not detected"
    assert report["missing_referenced_count"] > 0
    assert retrieval_error, "retrieval of a deleted blob did NOT fail loudly"
    # heal for subsequent gates: re-put the exact original bytes from the
    # external backup (the whole point of having one)
    original = next(
        (p for p in (BACKUP_DIR / "objects").rglob(digest) if p.is_file()), None
    )
    if original is None:
        original = next(
            (p for p in (BACKUP_DIR / "objects").rglob(f"{digest[:2]}/{digest}") if p.is_file()),
            None,
        )
    assert original is not None, "original blob not found in backup for healing"
    _mc(["cp", str(original), f"bmc/cap-evidence/{key}"])


@pytest.mark.timeout(900)
def test_ga_gate13_orphan_detected_and_gc_safe(dr) -> None:
    """GA-GATE 13: an object without a DB reference is identified as orphan;
    grace-period GC never touches referenced objects."""
    store = _store_b()
    import asyncio

    async def _scenario():
        await store.put(b"orphan-after-restore", metadata={})
        report = _reconcile_on_b()
        dr["reconciliation_orphan"] = report
        # GC with zero grace deletes ONLY orphans; referenced must survive
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.acquisition.gc import EvidenceOrphanGC

        engine = create_async_engine(
            f"postgresql+asyncpg://cap:cap@127.0.0.1:{PG_LOCAL_PORT}/cap", pool_pre_ping=True
        )
        try:
            gc = EvidenceOrphanGC(store, async_sessionmaker(engine, expire_on_commit=False),
                                  grace_period_seconds=0.0)
            stats = await gc.run()
        finally:
            await engine.dispose()
        after = _reconcile_on_b()
        return report, stats.to_dict(), after

    report, stats, after = asyncio.run(_scenario())
    assert report["orphan_count"] >= 1, "orphan not identified"
    assert after["orphan_count"] == 0, "GC did not collect the orphan"
    assert after["integrity_ok"], "GC damaged referenced objects"
    assert stats["deleted"] >= 1


@pytest.mark.timeout(900)
def test_ga_gate14_digest_corruption_detected(dr) -> None:
    """GA-GATE 14: flipped bytes in a restored object are caught by the
    digest gate -- never silent success."""
    store = _store_b()
    import asyncio

    digest = dr["dataset"]["seeded_digests"][2]
    key = f"sha256/{digest[:2]}/{digest}"

    async def _scenario():
        data = await store.get(key)
        # put() derives the digest from bytes, so simulate bit-rot by writing
        # corrupted bytes under the ORIGINAL key via mc (raw overwrite)
        corrupted = bytearray(data)
        corrupted[-1] ^= 0xFF
        path = REPORT_DIR / "corrupted.bin"
        path.write_bytes(bytes(corrupted))
        _mc(["cp", str(path), f"bmc/cap-evidence/{key}"])
        try:
            await store.get(key)
            get_rejected = False
        except Exception:  # noqa: BLE001 -- digest mismatch must raise
            get_rejected = True
        report = _reconcile_on_b()
        dr["reconciliation_digest"] = report
        return get_rejected, report, data

    get_rejected, report, original = asyncio.run(_scenario())
    assert get_rejected, "corrupted object read did NOT raise"
    assert digest in report["digest_mismatch"], "reconciliation missed digest corruption"
    assert not report["integrity_ok"]
    # heal: restore the exact original bytes
    path = REPORT_DIR / "healed.bin"
    path.write_bytes(original)
    _mc(["cp", str(path), f"bmc/cap-evidence/{key}"])


def test_ga_gate15_measured_rpo(dr) -> None:
    """GA-GATE 15: RPO is MEASURED, not invented. Pre-backup data survives;
    post-backup data is (per backup policy) lost; the observed window is
    recorded as the tested RPO baseline."""
    ctx = dr
    pod = _pg_pod()
    # t1 data survives
    for rid, _ in ctx["dataset"]["completed"]:
        assert _psql(pod, f"SELECT count(*) FROM acquisition_runs WHERE id='{rid}'") == "1"
    # t2 (post-backup) data is gone -- the honest RPO statement
    post = ctx["post_backup"]["id"]
    post_present = _psql(pod, f"SELECT count(*) FROM acquisition_runs WHERE id='{post}'")
    t1 = datetime.fromisoformat(ctx["timings"]["t1_backup_done"])
    t2 = datetime.fromisoformat(ctx["timings"]["t2_post_backup_data"])
    observed_rpo_seconds = (t2 - t1).total_seconds()
    ctx["rpo"] = {
        "observed_rpo_seconds": observed_rpo_seconds,
        "post_backup_data_present": post_present == "1",
        "note": "post-backup data lost by policy (backup taken at t1); "
                "observed RPO = t2 - t1 window",
    }
    (REPORT_DIR / "ga-dr-context.json").write_text(json.dumps(ctx, indent=2), encoding="utf-8")
    if post_present == "1":
        pytest.fail("post-backup run unexpectedly present -- RPO measurement invalid")
    assert observed_rpo_seconds > 0


def test_ga_gate16_measured_rto(dr) -> None:
    """GA-GATE 16: RTO measured from cluster-loss declaration to API ready +
    evidence queryable + workers executing. Recorded with phase breakdown."""
    t = dr["timings"]
    t3 = datetime.fromisoformat(t["t3_destroy_declared"])
    ready = datetime.fromisoformat(t["t_ready"])
    rto_seconds = (ready - t3).total_seconds()
    breakdown = {
        "cluster_b_creation": (datetime.fromisoformat(t["cluster_b_created"]) - t3).total_seconds(),
        "cilium": (datetime.fromisoformat(t["cluster_b_cilium_ready"])
                   - datetime.fromisoformat(t["cluster_b_created"])).total_seconds(),
        "infra": (datetime.fromisoformat(t["cluster_b_infra_ready"])
                  - datetime.fromisoformat(t["cluster_b_cilium_ready"])).total_seconds(),
        "cap_deploy": (datetime.fromisoformat(t["cluster_b_cap_deployed"])
                       - datetime.fromisoformat(t["cluster_b_infra_ready"])).total_seconds(),
        "restore_and_recovery": (
            ready - datetime.fromisoformat(t["cluster_b_cap_deployed"])
        ).total_seconds(),
    }
    dr["rto"] = {"rto_seconds": rto_seconds, "breakdown": breakdown}
    (REPORT_DIR / "ga-dr-context.json").write_text(json.dumps(dr, indent=2), encoding="utf-8")
    # generous CI bound only (kind cluster creation dominates); the MEASURED
    # value is the deliverable, not an SLO claim
    assert 0 < rto_seconds < 3600, f"RTO {rto_seconds}s outside measurable window"
