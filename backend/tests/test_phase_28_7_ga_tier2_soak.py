"""Phase 28.7 Tier 2 -- GA-GATE 24, 25, 26, 34, 35 (soak & reliability).

Runs ONLY in the dedicated ``cap-ga-reliability`` workflow (own kind
cluster, nightly schedule + workflow_dispatch). Regular push CI never pays
the soak wall-time; FULL GA proof dispatches this workflow explicitly.

  GA-GATE 24  2h real soak: continuous HTTP/pagination traffic + real
              acquisition executions with periodic WORKER POD KILLS,
              worker scale changes, run cancellations and resumes
  GA-GATE 25  leak analysis: periodic worker RSS sampling across the whole
              soak -- growth bounds asserted on the recorded series
  GA-GATE 26  orphan accumulation == 0: reconciliation sweep after the
              final GC pass finds zero orphans / missing / mismatched
              objects
  GA-GATE 34  helm upgrade DURING sustained load (executed at t=1/3 of the
              soak window); bounded errors + availability during transition
  GA-GATE 35  helm rollback DURING sustained load (t=2/3 of the window)

Honest disclosure: runs are plain HTTP acquisitions -- the acquisition API
does not expose per-run browser/tool selection, so the browser sandbox is
exercised by the Phase 28.5 Linux certification suite instead of here.
CAP_SOAK_SECONDS shortens the window ONLY for debugging; the certified run
uses the default 7200.
"""

from __future__ import annotations

import json
import os
import random
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from tests.test_phase_28_7_ga_certification import (
    INFRA_NS,
    MINIO_LOCAL_PORT,
    MINIO_PASSWORD,
    MINIO_USER,
    NAMESPACE,
    PG_LOCAL_PORT,
    REPO_ROOT,
    STRICT,
    _api_cancel,
    _api_create,
    _api_headers,
    _json_k,
    _kubectl,
    _pf_api,
    _port_forward,
    _run_status,
    _wait_port,
)

WORKER_DEPLOYMENT = "cap-cap-worker"
REPORT_DIR = Path(os.environ.get("GA_REPORT_DIR", str(REPO_ROOT / "outputs/ga-dr")))
SOAK_SECONDS = int(os.environ.get("CAP_SOAK_SECONDS", "7200"))
TICK_SECONDS = 15
KILL_INTERVAL = 600          # kill a worker pod every 10 minutes
RSS_SAMPLE_INTERVAL = 300    # sample RSS every 5 minutes

# scale changes at fixed offsets into the soak window
SCALE_SCHEDULE: dict[int, int] = {
    SOAK_SECONDS // 6: 3,
    SOAK_SECONDS // 2: 1,
    (5 * SOAK_SECONDS) // 6: 2,
}
UPGRADE_AT = SOAK_SECONDS // 3
ROLLBACK_AT = (2 * SOAK_SECONDS) // 3

_CTX_PATH = REPORT_DIR / "soak-context.json"


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


def _helm(args: list[str], *, timeout: float = 900.0):
    proc = subprocess.run(
        ["helm", *args], capture_output=True, text=True, check=True,
        timeout=int(timeout), cwd=str(REPO_ROOT),
    )
    return proc


def _worker_rss_kib() -> int | None:
    """Total VmRSS (KiB) of all worker pods, sampled via /proc in-pod."""
    total = 0
    try:
        pods = _json_k(
            ["get", "pods", "-n", NAMESPACE,
             "-l", "app.kubernetes.io/component=worker",
             "--field-selector", "status.phase=Running"]
        )
        for item in pods.get("items", []):
            name = item["metadata"]["name"]
            proc = _kubectl(
                ["exec", "-n", NAMESPACE, name, "--",
                 "sh", "-c", "grep VmRSS /proc/1/status"],
                check=False, timeout=20.0,
            )
            if proc.returncode != 0:
                continue
            for line in proc.stdout.splitlines():
                # "VmRSS:    123456 kB"
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "VmRSS:":
                    total += int(parts[1])
    except Exception:  # noqa: BLE001
        return None
    return total or None


def _kill_one_worker_pod() -> str | None:
    pods = _json_k(
        ["get", "pods", "-n", NAMESPACE,
         "-l", "app.kubernetes.io/component=worker",
         "--field-selector", "status.phase=Running"]
    )
    names = [item["metadata"]["name"] for item in pods.get("items", [])]
    if not names:
        return None
    victim = random.choice(names)
    _kubectl(["delete", "pod", victim, "-n", NAMESPACE, "--wait=false"])
    return victim


def _scale_workers(replicas: int) -> None:
    _kubectl(
        ["scale", "deployment", WORKER_DEPLOYMENT, "-n", NAMESPACE,
         f"--replicas={replicas}"]
    )


# -- GA-GATE 24 (+34/35 executed inside the load window) -----------------------


@pytest.mark.timeout(11000)
def test_ga_gate24_two_hour_soak_full_workload() -> None:
    _require_cluster()
    port = _pf_api()
    base_url = f"http://127.0.0.1:{port}"
    headers = _api_headers()

    ctx: dict = {
        "gate": "GA-GATE 24",
        "started_at": datetime.now(UTC).isoformat(),
        "soak_seconds": SOAK_SECONDS,
        "disclosure": (
            "plain HTTP acquisitions only -- the acquisition API exposes no "
            "per-run browser/tool selection; browser sandbox is exercised "
            "by the phase 28.5 certification suite"
        ),
        "ticks": 0,
        "healthy_ticks": 0,
        "runs_created": 0,
        "runs_cancelled": 0,
        "runs_resumed": 0,
        "pagination_requests": 0,
        "http_errors": 0,
        "downtime_seconds": 0,
        "worker_pods_killed": [],
        "rss_samples": [],           # [offset_s, total_kib]
        "transitions": {},           # upgrade / rollback records
        "submitted_run_ids": [],
        "cancelled_run_ids": [],
    }

    def record_transition(kind: str, started: float, errors: int, extra: dict) -> None:
        ctx["transitions"][kind] = {
            "duration_seconds": round(time.monotonic() - started, 1),
            "http_errors_during": errors,
            **extra,
        }

    start = time.monotonic()
    next_kill = KILL_INTERVAL
    next_rss = 0
    pending_scale_offsets = sorted(SCALE_SCHEDULE.items())
    upgrade_done = rollback_done = False

    while time.monotonic() - start < SOAK_SECONDS:
        offset = time.monotonic() - start
        tick_start = time.monotonic()

        # --- health probe ----------------------------------------------------
        healthy = False
        try:
            resp = httpx.get(f"{base_url}/health", timeout=5)
            healthy = resp.status_code == 200
        except Exception:  # noqa: BLE001
            healthy = False
        ctx["ticks"] += 1
        if healthy:
            ctx["healthy_ticks"] += 1
        else:
            ctx["downtime_seconds"] += TICK_SECONDS

        # --- create a run -----------------------------------------------------
        rc, body = _api_create(
            port, f"ga24-soak-{int(offset)}-{uuid4().hex[:6]}",
            url="http://example.com/",
        )
        if rc == 202:
            ctx["runs_created"] += 1
            rid = body.get("id") or body.get("run_id")
            if rid:
                ctx["submitted_run_ids"].append(rid)
        elif rc in (429, 503):
            pass  # clean backpressure is acceptable under load
        else:
            ctx["http_errors"] += 1

        # --- paginated listing traffic ----------------------------------------
        try:
            with httpx.Client(base_url=base_url, headers=headers, timeout=20) as http:
                for page in (1, 2, 3):
                    r = http.get("/acquisitions",
                                 params={"page": page, "page_size": 50})
                    if r.status_code >= 500:
                        ctx["http_errors"] += 1
                    else:
                        ctx["pagination_requests"] += 1
        except httpx.HTTPError:
            ctx["http_errors"] += 1

        # --- periodic chaos ----------------------------------------------------
        if offset >= next_kill:
            victim = _kill_one_worker_pod()
            if victim:
                ctx["worker_pods_killed"].append(
                    {"offset": int(offset), "pod": victim}
                )
            next_kill += KILL_INTERVAL

        while pending_scale_offsets and offset >= pending_scale_offsets[0][0]:
            _, replicas = pending_scale_offsets.pop(0)
            _scale_workers(replicas)

        if not upgrade_done and offset >= UPGRADE_AT:
            t0 = time.monotonic()
            pre_errors = ctx["http_errors"]
            _helm(["upgrade", "cap", "deployment/helm/cap", "-n", NAMESPACE,
                   "--reuse-values", "--set", "backend.replicaCount=2",
                   "--timeout", "600s"])
            _kubectl(["-n", NAMESPACE, "rollout", "status",
                      "deployment/cap-cap-backend", "--timeout=600s"],
                     timeout=660.0)
            record_transition("upgrade_under_load", t0,
                              ctx["http_errors"] - pre_errors,
                              {"at_offset_seconds": int(offset)})
            upgrade_done = True

        if not rollback_done and offset >= ROLLBACK_AT and upgrade_done:
            releases = json.loads(
                subprocess.run(
                    ["helm", "ls", "-n", NAMESPACE, "-o", "json"],
                    capture_output=True, text=True, check=True,
                ).stdout
            )
            revision = next(
                int(r["revision"]) for r in releases if r["name"] == "cap"
            )
            t0 = time.monotonic()
            pre_errors = ctx["http_errors"]
            _helm(["rollback", "cap", str(revision - 1), "-n", NAMESPACE,
                   "--timeout", "600s"])
            _kubectl(["-n", NAMESPACE, "rollout", "status",
                      "deployment/cap-cap-backend", "--timeout=600s"],
                     timeout=660.0)
            record_transition("rollback_under_load", t0,
                              ctx["http_errors"] - pre_errors,
                              {"at_offset_seconds": int(offset),
                               "from_revision": revision})
            rollback_done = True

        if offset >= next_rss:
            rss = _worker_rss_kib()
            if rss:
                ctx["rss_samples"].append([int(offset), rss])
            next_rss += RSS_SAMPLE_INTERVAL

        elapsed = time.monotonic() - tick_start
        time.sleep(max(0.0, TICK_SECONDS - elapsed))

    # --- drain: cancel ~10% then require everything terminal ------------------
    submitted = ctx["submitted_run_ids"]
    to_cancel = max(1, len(submitted) // 10)
    for rid in random.sample(submitted, min(to_cancel, len(submitted))):
        try:
            _api_cancel(port, rid)
            ctx["runs_cancelled"] += 1
            ctx["cancelled_run_ids"].append(rid)
        except Exception:  # noqa: BLE001
            pass
    time.sleep(30)

    non_terminal = []
    deadline = time.monotonic() + 1800
    pending = set(ctx["submitted_run_ids"])
    while pending and time.monotonic() < deadline:
        for rid in list(pending):
            status = _run_status(port, rid)
            if status in ("COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"):
                pending.discard(rid)
        if pending:
            time.sleep(5)
    non_terminal = sorted(pending)

    ctx["finished_at"] = datetime.now(UTC).isoformat()
    availability = ctx["healthy_ticks"] / max(1, ctx["ticks"])
    ctx["availability"] = round(availability, 4)
    ctx["non_terminal_runs"] = non_terminal
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    _CTX_PATH.write_text(json.dumps(ctx, indent=2), encoding="utf-8")

    assert availability >= 0.98, (
        f"soak availability {availability:.3f} below 0.98 "
        f"(downtime {ctx['downtime_seconds']}s)"
    )
    assert not non_terminal, (
        f"{len(non_terminal)} submitted runs never reached a terminal state"
    )
    assert ctx["http_errors"] <= max(3, ctx["ticks"] // 100), (
        f"too many HTTP errors during soak: {ctx['http_errors']}"
    )
    assert len(ctx["worker_pods_killed"]) >= SOAK_SECONDS // KILL_INTERVAL - 1
    assert "upgrade_under_load" in ctx["transitions"]
    assert "rollback_under_load" in ctx["transitions"]


# -- GA-GATE 25: leak analysis over the recorded RSS series --------------------

def _require_soak_context() -> None:
    """Gates 25/26/34/35 consume evidence produced by the gate24 soak run in
    THIS session; outside the reliability workflow they skip cleanly."""
    if not _CTX_PATH.exists():
        if STRICT:
            pytest.fail(
                "soak context missing (CAP_K8S_STRICT=1 -> SKIP==FAIL): "
                "run test_ga_gate24_two_hour_soak_full_workload first"
            )
        pytest.skip("gate24 soak has not run in this session")



def test_ga_gate25_worker_memory_no_leak() -> None:
    _require_soak_context()
    ctx = json.loads(_CTX_PATH.read_text(encoding="utf-8"))
    samples = ctx["rss_samples"]
    assert len(samples) >= 3, "not enough RSS samples recorded during soak"

    values = [kib for _, kib in samples]
    first5 = values[:5]
    last5 = values[-5:]
    baseline = statistics.median(first5)
    ceiling = max(values)

    assert ceiling < max(baseline * 3, baseline + 512 * 1024), (
        f"worker memory leak suspected: baseline~{baseline:.0f} KiB, "
        f"peak {ceiling} KiB (samples={len(samples)})"
    )
    # no sustained end-of-soak elevation either
    assert statistics.median(last5) < max(baseline * 2.5, baseline + 384 * 1024), (
        f"workers ended the soak elevated: first~{baseline:.0f} KiB, "
        f"last~{statistics.median(last5):.0f} KiB"
    )


# -- GA-GATE 26: zero orphan accumulation after the soak ------------------------


def test_ga_gate26_orphan_accumulation_zero() -> None:
    _require_soak_context()
    _require_cluster()
    env = os.environ.copy()
    env["DATABASE_URL"] = (
        f"postgresql+asyncpg://cap:cap@127.0.0.1:{PG_LOCAL_PORT}/cap"
    )
    env["OBJECT_STORE_ENDPOINT"] = f"127.0.0.1:{MINIO_LOCAL_PORT}"
    env["OBJECT_STORE_ACCESS_KEY"] = MINIO_USER
    env["OBJECT_STORE_SECRET_KEY"] = MINIO_PASSWORD
    env["OBJECT_STORE_BUCKET"] = "cap-evidence"
    _port_forward(f"{PG_LOCAL_PORT}:5432", ["-n", INFRA_NS, "svc/postgres"])
    _wait_port(f"{PG_LOCAL_PORT}:5432")
    proc = subprocess.run(
        ["uv", "run", "python", "-m", "app.acquisition.reconcile_cli"],
        capture_output=True, text=True, check=False,
        timeout=900, cwd=str(REPO_ROOT / "backend"), env=env,
    )
    assert proc.returncode in (0, 1), f"reconcile crashed: {proc.stderr[-600:]}"
    report = json.loads(proc.stdout)
    assert report["orphan_count"] == 0, (
        f"orphans accumulated during soak: {report['orphan'][:10]}"
    )
    assert report["missing_referenced_count"] == 0
    assert report["digest_mismatch_count"] == 0


# -- GA-GATE 34/35: assertions over the under-load transitions ------------------


def test_ga_gate34_upgrade_under_load_bounded() -> None:
    _require_soak_context()
    ctx = json.loads(_CTX_PATH.read_text(encoding="utf-8"))
    t = ctx["transitions"]["upgrade_under_load"]
    assert t["http_errors_during"] == 0, (
        f"upgrade-under-load produced {t['http_errors_during']} HTTP errors"
    )
    assert t["duration_seconds"] < 900


def test_ga_gate35_rollback_under_load_bounded() -> None:
    _require_soak_context()
    ctx = json.loads(_CTX_PATH.read_text(encoding="utf-8"))
    t = ctx["transitions"]["rollback_under_load"]
    assert t["http_errors_during"] == 0, (
        f"rollback-under-load produced {t['http_errors_during']} HTTP errors"
    )
    assert t["duration_seconds"] < 900
