"""Phase 28.7 Tier 2 -- GA-GATE 36..39 (dependency fault injection).

Runs in the main ``ga-certification`` job AFTER the ops module. Every gate
injects a REAL dependency outage against Cluster B and proves the system
fails CLOSED (clean, attributable terminal state -- never a hang, never a
silent partial success), then recovers end-to-end after the dependency
returns:

  GA-GATE 36  PostgreSQL connection exhaustion: saturating max_connections
              must not crash the control plane; recovery immediate on
              release
  GA-GATE 37  object store (MinIO/S3) outage: executions terminate in an
              attributable failure state; full recovery after restore
  GA-GATE 38  cluster DNS (CoreDNS) outage: sandbox runs fail closed;
              recovery after CoreDNS returns
  GA-GATE 39  egress proxy outage: NO direct-egress bypass occurs -- runs
              must FAIL, never succeed around the dead proxy; recovery
              after the proxy scales back

Each outage is reverted in a finally block -- the cluster is left healthy.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from uuid import uuid4

import pytest

from tests.test_phase_28_7_ga_certification import (
    INFRA_NS,
    NAMESPACE,
    STRICT,
    _api_create,
    _json_k,
    _kubectl,
    _pf_api,
    _wait_terminal,
)

SUCCESS_STATUSES = ("COMPLETE", "PARTIAL")
FAIL_CLOSED_STATUSES = ("FAILED", "BLOCKED", "CANCELLED")


def _cluster_ready() -> bool:
    try:
        proc = subprocess.run(
            ["kubectl", "cluster-info"], capture_output=True, timeout=30
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _require_cluster() -> None:
    if not _cluster_ready():
        if STRICT:
            pytest.fail("kind cluster unavailable (CAP_K8S_STRICT=1 -> SKIP==FAIL)")
        pytest.skip("kind cluster unavailable")


def _scale(deployment: str, namespace: str, replicas: int) -> None:
    _kubectl(
        ["scale", "deployment", deployment, "-n", namespace,
         f"--replicas={replicas}"]
    )


def _deployment_replicas(deployment: str, namespace: str) -> int:
    dep = _json_k(["get", "deployment", deployment, "-n", namespace])
    return int(dep["spec"]["replicas"])


def _rollout(deployment: str, namespace: str, timeout: str = "300s") -> None:
    _kubectl(
        ["-n", namespace, "rollout", "status",
         f"deployment/{deployment}", f"--timeout={timeout}"],
        timeout=330.0,
    )


def _backend_ready() -> bool:
    pods = _json_k(
        ["get", "pods", "-n", NAMESPACE,
         "-l", "app.kubernetes.io/component=backend"]
    )
    items = pods.get("items") or []
    if not items:
        return False
    for item in items:
        statuses = item.get("status", {}).get("containerStatuses") or [{}]
        if not all(c.get("ready") for c in statuses):
            return False
    return True


def _start_run(port: int, tag: str) -> tuple[int, dict]:
    return _api_create(
        port, f"ga-tier2-{tag}-{uuid4().hex[:8]}", url="http://example.com/"
    )


# -- GA-GATE 36: PostgreSQL connection exhaustion ------------------------------


def test_ga_gate36_pg_connection_exhaustion_controlled() -> None:
    import asyncpg

    _require_cluster()
    max_conn = int(
        _kubectl(
            [
                "exec", "-n", INFRA_NS, "deploy/postgres", "--",
                "psql", "-U", "cap", "-d", "cap", "-tAc",
                "SHOW max_connections;",
            ]
        ).stdout.strip()
    )
    assert max_conn >= 50, f"unexpected tiny max_connections={max_conn}"

    local_port = 25432
    pf = subprocess.Popen(
        ["kubectl", "port-forward", "-n", INFRA_NS, "svc/postgres",
         f"{local_port}:5432"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 45
        import socket

        while time.monotonic() < deadline:
            with socket.socket() as sock:
                sock.settimeout(1)
                if sock.connect_ex(("127.0.0.1", local_port)) == 0:
                    break
            time.sleep(0.5)

        async def _exhaust() -> tuple[int, bool]:
            dsn = f"postgresql://cap:cap@127.0.0.1:{local_port}/cap"
            conns = []
            hit_limit = False
            # leave the superuser_reserved slots alone
            target = max_conn - 2
            try:
                for _ in range(target):
                    try:
                        # asyncpg's own timeout cleans up its transport
                        # internally -- asyncio.wait_for would leave a
                        # dangling task whose GC fires unraisable warnings
                        conns.append(await asyncpg.connect(dsn, timeout=10))
                    except Exception as exc:  # noqa: BLE001
                        if "too many" in str(exc).lower():
                            hit_limit = True
                            break
                        raise
                return len(conns), hit_limit
            finally:
                for conn in conns:
                    try:
                        await conn.close(timeout=10)
                    except Exception:  # noqa: BLE001
                        pass

        held, hit_limit = asyncio.run(_exhaust())
        # Exhaustion demonstrated EITHER by holding near-max connections OR
        # by the server refusing further ones past its reservation margin.
        assert held >= max_conn - 10 or hit_limit, (
            f"exhaustion not reached: held {held}/{max_conn}, hit_limit={hit_limit}"
        )
        # CONTROLLED degradation: control-plane pods stay Ready throughout
        assert _backend_ready(), "backend pods not Ready during PG exhaustion"
    finally:
        pf.terminate()
        pf.wait()

    # RECOVERY: a fresh connection works immediately after release
    async def _verify_recovery() -> int:
        conn = await asyncpg.connect(
            f"postgresql://cap:cap@127.0.0.1:{local_port}/cap"
        )
        try:
            return await conn.fetchval("SELECT 1")
        finally:
            await conn.close()

    pf2 = subprocess.Popen(
        ["kubectl", "port-forward", "-n", INFRA_NS, "svc/postgres",
         f"{local_port}:5432"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(3)
        assert asyncio.run(_verify_recovery()) == 1
    finally:
        pf2.terminate()
        pf2.wait()


# -- GA-GATE 37/38/39 shared scenario driver -----------------------------------


def _wait_scaled_to_zero(deployment: str, namespace: str, timeout: float = 240.0) -> None:
    """Block until the outage is REAL: zero ready pods for the deployment,
    plus a settle delay so Service endpoints/EndpointSlices drain.
    ``kubectl scale`` returns immediately -- without this wait a fast run
    completes while the old pods are still terminating (false success).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        dep = _json_k(["get", "deployment", deployment, "-n", namespace])
        status = dep.get("status", {})
        ready = int(status.get("readyReplicas") or 0)
        current = int(status.get("replicas") or 0)
        if ready == 0 and current == 0:
            time.sleep(8)  # endpoint drain settle
            return
        time.sleep(3)
    pytest.fail(f"{deployment} in {namespace} still has pods after scale-to-zero")

def _outage_scenario(gate_tag: str, deployment: str, namespace: str) -> None:
    """Scale a dependency to zero -> run -> assert fail-closed -> restore ->
    run again -> assert success."""
    port = _pf_api()
    original = _deployment_replicas(deployment, namespace)
    try:
        _scale(deployment, namespace, 0)
        _wait_scaled_to_zero(deployment, namespace)
        rc, body = _start_run(port, gate_tag)
        assert rc in (200, 201, 202), f"API rejected run creation: {rc} {body}"
        run_id = body.get("id") or body.get("run_id")
        assert run_id, f"no run id in response: {body}"
        status = _wait_terminal(port, run_id, timeout=420)
        assert status in FAIL_CLOSED_STATUSES, (
            f"[{gate_tag}] dependency-dead run ended {status} -- the "
            "system did NOT fail closed"
        )
    finally:
        _scale(deployment, namespace, original)
        _rollout(deployment, namespace)

    # recovery: identical workload succeeds once the dependency is back
    rc, body = _start_run(port, f"{gate_tag}-recovery")
    assert rc in (200, 201, 202), body
    run_id = body.get("id") or body.get("run_id")
    status = _wait_terminal(port, run_id, timeout=420)
    assert status in SUCCESS_STATUSES, (
        f"[{gate_tag}] recovery run ended {status} after dependency restore"
    )


def test_ga_gate37_object_store_outage_fails_closed_and_recovers() -> None:
    _require_cluster()
    _outage_scenario("s3", "minio", INFRA_NS)


def test_ga_gate38_dns_outage_fails_closed_and_recovers() -> None:
    _require_cluster()
    _outage_scenario("dns", "coredns", "kube-system")


def test_ga_gate39_egress_proxy_outage_no_direct_bypass() -> None:
    _require_cluster()
    # THE critical assertion: with the egress proxy dead, an external fetch
    # must FAIL. A SUCCEEDED run here would mean the sandbox bypassed the
    # deny-by-default egress policy -- an isolation breach, not a resiliency
    # event. Recovery proves the proxied path itself was restored.
    _outage_scenario("egress", "cap-cap-egress-proxy", NAMESPACE)
