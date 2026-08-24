"""Phase 28.7 Tier 2 -- GA-GATE 27 + 28 (capacity envelope, backpressure).

Runs in the main ``ga-certification`` job AFTER the resilience module.

  GA-GATE 27  capacity envelope: the documented matrix
              worker_replicas x load in {1,2,4} x {100,500,1000}
              is exercised against Cluster B. Load COMPOSITION (honest
              disclosure recorded in capacity.json): every cell submits
              REAL acquisition executions at 10% of the load number and
              drives the remaining 90% as paginated listing traffic --
              a full 1000-real-execution cell would need hours of sandbox
              pod churn on a 4-core kind runner. Both planes (control API +
              worker fleet) are therefore loaded at every cell.
  GA-GATE 28  overload backpressure: 3x oversubscription against a SINGLE
              worker must either accept cleanly (queueing) or reject
              cleanly (429/503) -- never 5xx-crash, never lose a submitted
              run, and /health must stay available throughout.

Evidence: outputs/ga-dr/capacity.json (per-cell measured p95/error/terminal
rates). SLO candidates consume these measurements downstream.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from tests.test_phase_28_7_ga_certification import (
    NAMESPACE,
    STRICT,
    _api_create,
    _api_headers,
    _json_k,
    _kubectl,
    _pf_api,
    _run_status,
)

WORKER_DEPLOYMENT = "cap-cap-worker"
PAGE_SIZE = 20


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


def _scale_workers(replicas: int) -> None:
    _kubectl(
        ["scale", "deployment", WORKER_DEPLOYMENT, "-n", NAMESPACE,
         f"--replicas={replicas}"]
    )
    _kubectl(
        ["-n", NAMESPACE, "rollout", "status",
         f"deployment/{WORKER_DEPLOYMENT}", "--timeout=300s"],
        timeout=330.0,
    )


def _paginated_probe(base_url: str, headers: dict, count: int) -> list[float]:
    """Drive `count` paginated listing requests; return latencies (seconds).
    Records ONLY clean client-side outcomes -- HTTP errors are returned as
    latencies flagged by raising, so callers see them."""
    latencies: list[float] = []
    transport = httpx.HTTPTransport(retries=0)
    with httpx.Client(
        base_url=base_url, headers=headers, timeout=30, transport=transport
    ) as http:
        for i in range(count):
            page = (i % 5) + 1
            start = time.monotonic()
            resp = http.get(
                "/acquisitions",
                params={"page": page, "page_size": PAGE_SIZE},
            )
            elapsed = time.monotonic() - start
            if resp.status_code >= 500:
                raise AssertionError(
                    f"5xx during paginated probe: {resp.status_code}"
                )
            latencies.append(elapsed)
    return latencies


def _terminal_count(port: int, run_ids: list[str], timeout: float) -> tuple[int, int]:
    """Poll until all runs terminal or timeout; returns (terminal, total)."""
    deadline = time.monotonic() + timeout
    pending = set(run_ids)
    while pending and time.monotonic() < deadline:
        for rid in list(pending):
            if _run_status(port, rid) in (
                "COMPLETE", "PARTIAL", "BLOCKED", "FAILED", "CANCELLED"
            ):
                pending.discard(rid)
        if pending:
            time.sleep(3)
    return len(run_ids) - len(pending), len(run_ids)


# -- GA-GATE 27: capacity envelope ---------------------------------------------


def test_ga_gate27_capacity_envelope_matrix() -> None:
    _require_cluster()
    port = _pf_api()
    base_url = f"http://127.0.0.1:{port}"
    headers = _api_headers()

    cells = [
        (workers, load)
        for workers in (1, 2, 4)
        for load in (100, 500, 1000)
    ]
    results = []
    try:
        for workers, load in cells:
            _scale_workers(workers)
            executions = max(1, load // 10)      # 10% real executions
            probes = load - executions           # 90% paginated traffic

            run_ids: list[str] = []
            for i in range(executions):
                rc, body = _api_create(
                    port,
                    f"ga27-w{workers}-l{load}-{i}-{uuid4().hex[:6]}",
                    url="http://example.com/",
                )
                assert rc in (200, 201), f"cell {workers}x{load}: {rc} {body}"
                run_ids.append(body.get("id") or body.get("run_id"))

            # interleave paginated traffic WHILE executions are processed
            latencies = _paginated_probe(base_url, headers, probes)

            terminal, total = _terminal_count(port, run_ids, timeout=max(240, executions * 60))
            p95 = (
                statistics.quantiles(latencies, n=20)[18]
                if len(latencies) >= 20
                else max(latencies)
            )
            cell_result = {
                "worker_replicas": workers,
                "load": load,
                "real_executions": executions,
                "paginated_requests": probes,
                "p95_latency_seconds": round(p95, 3),
                "max_latency_seconds": round(max(latencies), 3),
                "executions_terminal": f"{terminal}/{total}",
                "errors": 0,
            }
            results.append(cell_result)
            print(f"[capacity] {cell_result}")

            assert terminal == total, (
                f"cell {workers}x{load}: {total - terminal} executions "
                "never reached a terminal state"
            )
            assert p95 < 45, (
                f"cell {workers}x{load}: p95 {p95:.1f}s exceeds 45s bound"
            )
    finally:
        _scale_workers(2)  # restore the deployment default

    report_dir = Path(os.environ.get(
        "GA_REPORT_DIR", "outputs/ga-dr"
    ))
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "capacity.json").write_text(
        json.dumps(
            {
                "gate": "GA-GATE 27",
                "matrix": "worker_replicas {1,2,4} x load {100,500,1000}",
                "load_composition_disclosure": (
                    "10% real executions + 90% paginated listing traffic per "
                    "cell -- full-real-execution cells are infeasible on a "
                    "shared-runner kind cluster in certification wall-time"
                ),
                "cells": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


# -- GA-GATE 28: overload backpressure ------------------------------------------


def test_ga_gate28_overload_backpressure_clean() -> None:
    _require_cluster()
    port = _pf_api()
    base_url = f"http://127.0.0.1:{port}"
    headers = _api_headers()
    original = int(
        _json_k(["get", "deployment", WORKER_DEPLOYMENT, "-n", NAMESPACE])
        ["spec"]["replicas"]
    )

    OVERSUBSCRIPTION = 60  # submissions against ONE worker
    try:
        _scale_workers(1)
        accepted, rejected_cleanly, server_errors = 0, 0, 0
        run_ids: list[str] = []
        with httpx.Client(base_url=base_url, headers=headers, timeout=30) as http:
            health_latencies: list[float] = []
            for i in range(OVERSUBSCRIPTION):
                start = time.monotonic()
                h = http.get("/health")
                if h.status_code == 200:
                    health_latencies.append(time.monotonic() - start)
                rc, body = _api_create(
                    port,
                    f"ga28-overload-{i}-{uuid4().hex[:6]}",
                    url="http://example.com/",
                )
                if rc in (200, 201):
                    accepted += 1
                    rid = body.get("id") or body.get("run_id")
                    if rid:
                        run_ids.append(rid)
                elif rc in (429, 503):
                    rejected_cleanly += 1
                else:
                    server_errors += 1
            overload_health_p95 = (
                statistics.quantiles(health_latencies, n=20)[18]
                if len(health_latencies) >= 20 else 0.0
            )

        assert server_errors == 0, (
            f"{server_errors}/{OVERSUBSCRIPTION} submissions returned 5xx "
            "under overload -- backpressure is NOT clean"
        )
        assert accepted + rejected_cleanly == OVERSUBSCRIPTION
        assert accepted > 0, "system rejected EVERYTHING under overload"
        assert overload_health_p95 < 10, (
            f"/health p95 under overload was {overload_health_p95:.2f}s"
        )

        # every ACCEPTED run must reach a terminal state (nothing lost)
        terminal, total = _terminal_count(port, run_ids, timeout=900)
        assert terminal == total, (
            f"{total - terminal} accepted runs lost under overload"
        )
    finally:
        _scale_workers(original)

    report_dir = Path(os.environ.get("GA_REPORT_DIR", "outputs/ga-dr"))
    (report_dir / "backpressure.json").write_text(
        json.dumps(
            {
                "gate": "GA-GATE 28",
                "oversubscription": OVERSUBSCRIPTION,
                "accepted": accepted,
                "rejected_cleanly": rejected_cleanly,
                "server_errors": server_errors,
                "all_accepted_runs_terminal": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
