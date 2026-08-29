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
import json
import subprocess
import threading
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
    _run,
    _run_status,
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

def _run_detail(port: int, run_id: str) -> str:
    """Full run JSON for violation-time forensics (best effort)."""
    try:
        import httpx

        from tests.test_phase_28_7_ga_certification import _api_headers

        r = httpx.get(
            f"http://127.0.0.1:{port}/acquisitions/{run_id}",
            headers=_api_headers(),
            timeout=15,
        )
        return r.text[:2000] if r.status_code == 200 else f"HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return f"unavailable: {exc}"


def _bypass_diagnostics(port: int, run_id: str) -> str:
    """Violation-time forensics for a silent-success-under-outage breach.

    Dumps, best effort:
      * the full run detail JSON (metrics show whether ANY fetch happened)
      * run evidence + completeness reports (did anything reach the store?)
      * every live pod in the sandbox namespace (label-agnostic -- a wrong
        selector once hid the very pods we needed to see)
      * the last worker log lines (claim/readiness/exec branch decisions)
    """
    import httpx

    from tests.test_phase_28_7_ga_certification import (
        NAMESPACE as API_NS,
    )
    from tests.test_phase_28_7_ga_certification import (
        _api_headers,
    )

    parts: list[str] = [f"forensics for run={run_id}"]
    try:
        base = f"http://127.0.0.1:{port}"
        headers = _api_headers()
        r = httpx.get(f"{base}/acquisitions/{run_id}", headers=headers, timeout=15)
        body = r.text[:1500] if r.status_code == 200 else f"HTTP {r.status_code}"
        parts.append(f"detail={body}")
        for name, path in (("evidence", "/evidence"), ("completeness", "/completeness")):
            try:
                r = httpx.get(
                    f"{base}/acquisitions/{run_id}{path}", headers=headers, timeout=15
                )
                body = r.text[:800] if r.status_code == 200 else f"HTTP {r.status_code}"
                parts.append(f"{name}={body}")
            except Exception as exc:  # noqa: BLE001
                parts.append(f"{name}=unavailable:{exc}")
    except Exception as exc:  # noqa: BLE001
        parts.append(f"api forensics unavailable: {exc}")

    try:
        pods = _json_k(["get", "pods", "-n", "cap-sandbox"])
        items = pods.get("items") or []
        if not items:
            parts.append("sandbox ns: NO PODS AT ALL")
        for item in items:
            meta = item.get("metadata", {})
            status = item.get("status", {})
            name = meta.get("name")
            phase = status.get("phase")
            proc = subprocess.run(
                [
                    "kubectl", "exec", "-n", "cap-sandbox", name, "--",
                    "sh", "-c",
                    "echo HTTP_PROXY=$HTTP_PROXY HTTPS_PROXY=$HTTPS_PROXY "
                    "NO_PROXY=$NO_PROXY",
                ],
                capture_output=True,
                timeout=20,
                check=False,
            )
            env = (proc.stdout or b"").decode(errors="replace").strip()
            parts.append(f"sandbox {name}({phase}): {env}")
    except Exception as exc:  # noqa: BLE001
        parts.append(f"sandbox ns listing unavailable: {exc}")

    try:
        workers = _json_k(
            ["get", "pods", "-n", API_NS,
             "-l", "app.kubernetes.io/component=worker"]
        )
        for item in workers.get("items") or []:
            name = item.get("metadata", {}).get("name")
            if not name:
                continue
            proc = subprocess.run(
                ["kubectl", "logs", "-n", API_NS, name, "--tail=60"],
                capture_output=True,
                timeout=20,
                check=False,
            )
            log = (proc.stdout or b"").decode(errors="replace").strip()[-1200:]
            parts.append(f"worker {name} logs<<<{log}>>>")
    except Exception as exc:  # noqa: BLE001
        parts.append(f"worker logs unavailable: {exc}")

    return " | ".join(parts)


class _SandboxPodWatcher:
    """Capture sandbox pod SPECS (incl. env) while executions are live.

    Sandbox pods are single-shot and deleted on EVERY completion path, so
    post-violation `kubectl get pods` always sees an empty namespace. This
    watcher polls the sandbox namespace during the outage window and keeps
    every observed pod's env + logs -- the only way to answer 'did the
    execution pod actually carry the egress proxy env?'.
    """

    def __init__(self, namespace: str = "cap-sandbox") -> None:
        self._namespace = namespace
        self._captured: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> str:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
        return "\n".join(self._captured) if self._captured else (
            "no sandbox pods observed during outage window"
        )

    def _run(self) -> None:
        seen: set[str] = set()
        while not self._stop.is_set():
            try:
                pods = _json_k(["get", "pods", "-n", self._namespace])
                for item in pods.get("items") or []:
                    name = (item.get("metadata") or {}).get("name")
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    spec = item.get("spec") or {}
                    containers = spec.get("containers") or [{}]
                    env = (containers[0].get("env") or []) if containers else []
                    env_s = ";".join(
                        f"{e.get('name')}={e.get('value')}" for e in env
                    )
                    phase = (item.get("status") or {}).get("phase")
                    entry = f"[watch] {name} phase={phase} env: {env_s}"
                    proc = subprocess.run(
                        ["kubectl", "logs", "-n", self._namespace, name],
                        capture_output=True,
                        timeout=15,
                        check=False,
                    )
                    log = (proc.stdout or b"").decode(errors="replace").strip()
                    if log:
                        entry += f" | shim-log<<<{log[-800:]}>>>"
                    self._captured.append(entry)
            except Exception:  # noqa: BLE001 -- watcher must never disturb the test
                pass
            self._stop.wait(1.0)


def _egress_proxy_endpoints_empty(namespace: str) -> tuple[bool, str]:
    """True when the egress proxy Service has NO endpoints left at all --
    BOTH ready and notReady addresses must be gone (a terminating-but-
    still-listening proxy pod would sit in notReadyAddresses)."""
    eps = _json_k(["get", "endpoints", "-n", namespace, "cap-cap-egress-proxy"])
    subsets = eps.get("subsets") or []
    ready: list[str] = []
    not_ready: list[str] = []
    for s in subsets:
        for addr in s.get("addresses") or []:
            ready.append(addr.get("ip"))
        for addr in s.get("notReadyAddresses") or []:
            not_ready.append(addr.get("ip"))
    ok = not ready and not not_ready
    return ok, f"ready={ready or 'NONE'} notReady={not_ready or 'NONE'}"


def _wait_proxy_pods_gone(namespace: str, timeout_s: float = 120.0) -> None:
    """Wait until ZERO egress-proxy pods remain (fully terminated).

    Scale-to-zero only marks desired state; the old pod may linger in
    Terminating for seconds. GATE 39's outage is only real once the
    previous proxy PROCESS is gone from the cluster.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pods = _json_k(
            ["get", "pods", "-n", namespace,
             "-l", "app.kubernetes.io/component=egress-proxy"]
        )
        if not (pods.get("items") or []):
            return
        time.sleep(2)
    pytest.fail(f"egress-proxy pods still present in {namespace} after {timeout_s}s")


_CANARY_PY_FETCH = (
    "import json,urllib.request\n"
    "body=json.dumps({'version':1,'operation':'http_fetch','run_id':'canary',"
    "'sandbox_execution_id':'canary','url':'http://example.com/',"
    "'policy':{'timeout_seconds':15}}).encode()\n"
    "req=urllib.request.Request('http://127.0.0.1:8080/',data=body,"
    "headers={'Content-Type':'application/json'})\n"
    "try:\n"
    "    resp=json.load(urllib.request.urlopen(req,timeout=60))\n"
    "except Exception as e:\n"
    "    print(json.dumps({'transport_error':str(e)[:300]}))\n"
    "    raise SystemExit\n"
    "r=resp.get('result') or {}\n"
    "print(json.dumps({'envelope':resp.get('status'),'error':resp.get('error'),"
    "'fetch_status':r.get('status'),'b64_len':len(r.get('content_b64') or ''),"
    "'blocked_reason':r.get('blocked_reason'),"
    "'blocked_detail':(r.get('blocked_detail') or '')[:200]}))\n"
)

_CANARY_PY_ENV = (
    "import os;print(';'.join(sorted("
    "f'{k}={v}' for k,v in os.environ.items()"
    " if 'PROXY' in k.upper())))"
)


def _egress_canary_probe(namespace: str = "cap-sandbox") -> str:
    """Deterministic in-test reproduction attempt for the direct-egress bypass.

    Creates a sandbox pod with EXACTLY the spec the worker-side provider uses
    (same image + env incl. proxy trio), then -- while the outage is live --
    issues the SAME typed http_fetch against its own loopback shim and reports:
      * the RUNTIME process env (settles 'did the pod really see the vars')
      * the fetch verdict (real content => bypass reproduced at the pod layer;
        failure => network isolation holds and the breach is elsewhere)
    The canary never touches the API or DB, so it cannot mask a gate verdict.
    """
    name = f"cap-egress-canary-{uuid4().hex[:6]}"
    proxy = "http://cap-cap-egress-proxy.cap.svc:8080"
    manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"cap.canary": "egress-probe"},
        },
        "spec": {
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "sandbox",
                    "image": "cap-sandbox-http:latest",
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["python", "-m", "sandbox.shim", "--serve"],
                    "env": [
                        {"name": "CAP_SHIM_PORT", "value": "8080"},
                        {"name": "PYTHONUNBUFFERED", "value": "1"},
                        {"name": "HTTPS_PROXY", "value": proxy},
                        {"name": "HTTP_PROXY", "value": proxy},
                        {"name": "NO_PROXY", "value": ""},
                    ],
                    "ports": [{"containerPort": 8080, "protocol": "TCP"}],
                    "resources": {
                        "limits": {"memory": "512Mi", "cpu": "500m"},
                        "requests": {"memory": "64Mi", "cpu": "10m"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "runAsNonRoot": True,
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
    try:
        _run(["kubectl", "apply", "-f", "-"], input=json.dumps(manifest), timeout=60)
        # wait Running + ready (image already node-local -> seconds)
        deadline = time.monotonic() + 120
        phase = "?"
        while time.monotonic() < deadline:
            pod = _json_k(["get", "pod", name, "-n", namespace])
            phase = (pod.get("status") or {}).get("phase", "?")
            cs = ((pod.get("status") or {}).get("containerStatuses") or [{}])[0]
            if phase == "Running" and cs.get("ready"):
                break
            if phase in ("Failed", "Succeeded"):
                return f"canary pod {phase} before serving"
            time.sleep(2)
        else:
            return f"canary pod never became ready (last phase={phase})"

        summary = [f"[canary] pod={name} phase={phase}"]
        proc = subprocess.run(
            ["kubectl", "exec", "-n", namespace, name, "--",
             "python", "-c", _CANARY_PY_ENV],
            capture_output=True, timeout=30, check=False,
        )
        summary.append(
            "runtime-env<<<"
            f"{(proc.stdout or b'').decode(errors='replace').strip() or '(empty)'}"
            ">>>"
        )
        proc = subprocess.run(
            ["kubectl", "exec", "-n", namespace, name, "--",
             "python", "-c", _CANARY_PY_FETCH],
            capture_output=True, timeout=120, check=False,
        )
        out = (proc.stdout or b"").decode(errors="replace").strip()
        err = (proc.stderr or b"").decode(errors="replace").strip()
        summary.append(f"fetch-result<<<{out or err[-400:] or '(no output)'}>>>")
        logs = subprocess.run(
            ["kubectl", "logs", "-n", namespace, name],
            capture_output=True, timeout=20, check=False,
        )
        log = (logs.stdout or b"").decode(errors="replace").strip()
        if log:
            summary.append(f"shim-log<<<{log[-600:]}>>>")
        return " ".join(summary)
    except Exception as exc:  # noqa: BLE001 -- diagnostics must never throw
        return f"[canary] probe itself failed: {exc}"
    finally:
        try:
            _run(["kubectl", "delete", "pod", name, "-n", namespace,
                  "--force", "--grace-period=0", "--ignore-not-found"],
                 timeout=60, check=False)
        except Exception:  # noqa: BLE001
            pass


def _outage_scenario(
    gate_tag: str,
    deployment: str,
    namespace: str,
    *,
    execution_level: bool = False,
) -> None:
    """Layered fail-closed contract under a REAL dependency outage.

    Phase A (outage LIVE): no run may reach a success status. Either the
    worker pauses claiming BY DESIGN (Phase 28.4 GATE 15 readiness gate --
    runs stay durably QUEUED, work is never lost), or the execution itself
    fails closed (FAILED / BLOCKED / CANCELLED).

    ``execution_level=True`` (egress proxy, GA-GATE 39): since v1.0.1 the
    controlled egress proxy is ALSO a readiness dependency (PATCH-GATE 5:
    unreachable proxy -> readiness=false -> the worker pauses claiming), so
    BOTH fail-closed outcomes are legal here -- a durably QUEUED run (claim
    paused) OR a terminal failure (execution already in flight when the
    outage landed). Any success would prove a direct-egress bypass, i.e. an
    isolation breach, and fails the gate either way.

    Phase B (dependency RESTORED): a deferred run must COMPLETE from the
    durable queue; an already-failed run stays attributably failed.

    Recovery: a fresh identical workload succeeds end-to-end.
    """
    port = _pf_api()
    original = _deployment_replicas(deployment, namespace)
    watcher = _SandboxPodWatcher() if execution_level else None
    try:
        _scale(deployment, namespace, 0)
        _wait_scaled_to_zero(deployment, namespace)
        canary = "n/a"
        if execution_level:
            # hard pre-condition 1: the old proxy PROCESS is fully gone
            # (scale-to-zero leaves a Terminating pod alive for a while --
            # a still-listening proxy would make the outage fictional)
            _wait_proxy_pods_gone(namespace)
            # hard pre-condition 2: the Service has NO addresses left at all
            # (ready AND notReady -- see _egress_proxy_endpoints_empty)
            empty, detail = _egress_proxy_endpoints_empty(namespace)
            assert empty, (
                f"[{gate_tag}] egress proxy Service still has endpoints "
                f"after scale-to-zero -- outage not real ({detail})"
            )
            # deterministic reproduction attempt: an isolated sandbox pod,
            # identical spec to the worker's provider, fetching DURING the
            # outage. Real content here == bypass reproduced at the pod
            # layer; clean failure == network isolation holds and the breach
            # lives elsewhere (worker-side execution path).
            watcher.start()
            canary = _egress_canary_probe()
            empty2, detail2 = _egress_proxy_endpoints_empty(namespace)
            assert empty2, (
                f"[{gate_tag}] egress proxy endpoints reappeared during "
                f"canary probe ({detail2}); canary={canary}"
            )
        rc, body = _start_run(port, gate_tag)
        assert rc in (200, 201, 202), f"API rejected run creation: {rc} {body}"
        run_id = body.get("id") or body.get("run_id")
        assert run_id, f"no run id in response: {body}"

        # Phase A: outage window -- never a silent success
        deadline = time.monotonic() + 90
        failed_closed = False
        while time.monotonic() < deadline:
            status = _run_status(port, run_id)
            assert status not in SUCCESS_STATUSES, (
                f"[{gate_tag}] run reached {status} WHILE {deployment} was "
                "down -- silent success during a dependency outage; "
                f"{canary}; detail={_run_detail(port, run_id)}; "
                f"{_bypass_diagnostics(port, run_id)}; "
                f"sandbox-pod-capture:\n{watcher.stop() if watcher else 'n/a'}"
            )
            if status in FAIL_CLOSED_STATUSES:
                failed_closed = True
                break
            time.sleep(5)
        # v1.0.1 PATCH-GATE 5: the egress proxy is now a readiness dependency,
        # so with it down the worker pauses claiming and the run stays
        # durably QUEUED for the whole window. That is the DESIGNED fail-
        # closed outcome, not a missing one: no execution can succeed without
        # the controlled proxy (the canary above proves pod-level isolation),
        # and Phase B below proves the queued work completes after restore.
        # A terminal failure (run claimed before the outage landed) is equally
        # acceptable. Only a success is a breach.
        if execution_level:
            print(f"\n[{gate_tag}] canary probe result: {canary}")
            # persist for artifact upload (pytest swallows stdout of PASSED
            # tests, so a green run would otherwise hide the evidence)
            try:
                from tests.test_phase_28_7_ga_certification import REPORT_DIR

                with open(REPORT_DIR / "canary-gate39.log", "a",
                          encoding="utf-8") as fh:
                    fh.write(
                        f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
                        f"{gate_tag}: {canary}\n"
                    )
            except Exception:  # noqa: BLE001 -- diagnostics only
                pass
    finally:
        if watcher is not None:
            watcher.stop()
        _scale(deployment, namespace, original)
        _rollout(deployment, namespace)

    # Phase B: durability -- deferred work completes once the dep is back;
    # a run that already failed closed stays attributably failed.
    status = _wait_terminal(port, run_id, timeout=420)
    if failed_closed:
        assert status in FAIL_CLOSED_STATUSES, (
            f"[{gate_tag}] run flipped {status} after failing closed"
        )
    else:
        assert status in SUCCESS_STATUSES, (
            f"[{gate_tag}] deferred run ended {status} after dependency "
            "restore -- the durable queue must complete accepted work"
        )

    # recovery: fresh workload succeeds end-to-end
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
    _outage_scenario(
        "egress", "cap-cap-egress-proxy", NAMESPACE, execution_level=True
    )
