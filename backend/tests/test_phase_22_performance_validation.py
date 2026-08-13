"""Phase 22 performance-validation asset and scope-boundary tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmarks" / "phase22"


def _load_runner():
    path = BENCHMARK_ROOT / "run_benchmarks.py"
    spec = importlib.util.spec_from_file_location("phase22_benchmarks", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_phase_22_matrix_and_percentile_contract() -> None:
    runner = _load_runner()
    assert runner.CONCURRENCY_LEVELS == (1, 10, 50, 100, 500, 1000)
    assert runner.WORKER_LEVELS == (1, 2, 4, 8, 16)
    assert runner.PLAYBOOK_LEVELS == (100, 500, 1000)
    assert runner.percentile([0.001, 0.002, 0.003, 0.004], 0.50) == 0.0025
    summary = runner.summarize("sample", [0.001, 0.002], 0, 0.01)
    assert summary["latency_ms"]["p50"] == 1.5
    assert summary["tps"] == 200


def test_performance_budget_is_bounded_and_explicit() -> None:
    budget = json.loads((BENCHMARK_ROOT / "performance_budget.json").read_text("utf-8"))
    assert budget["schema_version"] == "phase22.v1"
    assert budget["api"]["p95_ms"] == 500
    assert budget["api"]["p99_ms"] == 1000
    assert budget["api"]["error_rate_max"] == 0.01
    assert budget["resources"]["rss_growth_mb_max"] == 256
    assert any("not production SLOs" in note for note in budget["notes"])


def test_k6_locust_and_vegeta_reference_contracts() -> None:
    k6 = (BENCHMARK_ROOT / "k6-api.js").read_text("utf-8")
    assert "[1, 10, 50, 100, 500, 1000]" in k6
    assert 'executor: "constant-vus"' in k6
    assert 'http_req_failed: ["rate<0.01"]' in k6
    assert 'http_req_duration: ["p(95)<500", "p(99)<1000"]' in k6
    for method in ("http.get", "http.post", "http.put", "http.del"):
        assert method in k6

    locust = (BENCHMARK_ROOT / "locustfile.py").read_text("utf-8")
    assert "class CAPUser(HttpUser)" in locust
    assert "@task" in locust
    assert "POST /assets" in locust
    assert "DELETE /assets/{id}" in locust

    vegeta = (BENCHMARK_ROOT / "run_vegeta.ps1").read_text("utf-8")
    assert 'vegeta attack -rate "$Rate/s"' in vegeta
    assert "vegeta report -type json" in vegeta


def test_benchmark_assets_are_local_synthetic_and_fail_closed() -> None:
    runner = (BENCHMARK_ROOT / "run_benchmarks.py").read_text("utf-8")
    assert "sqlite+aiosqlite://" in runner
    assert "MemorySandboxProvider" in runner
    assert ".example.test" in runner
    method_contracts = (
        '("POST", "/assets"',
        '("GET", "/assets/{asset_id}"',
        '("PUT", "/assets/{asset_id}"',
        '("DELETE", "/assets/{asset_id}"',
    )
    for method in method_contracts:
        assert method in runner
    assert "production endpoint" in runner
    assert "database_restart" in runner and "not executed" in runner
    assert "redis_restart" in runner and "not executed" in runner
    assert "os.kill" not in runner
    assert "subprocess" not in runner


def test_resource_probe_and_budget_evaluation_contract() -> None:
    runner = _load_runner()
    if runner.os.name == "nt":
        assert isinstance(runner.working_set_bytes(), int)
    result = {
        "api": {"results": [{"latency_ms": {"p95": 100.0, "p99": 200.0}, "error_rate": 0.0}]},
        "worker": {
            "results": [
                {
                    "name": "WorkerScheduler workers=1",
                    "latency_ms": {"p95": 10.0},
                    "error_rate": 0.0,
                }
            ]
        },
        "plugin": {"results": [{"latency_ms": {"p95": 10.0}, "error_rate": 0.0}]},
        "playbook": {"results": [{"latency_ms": {"p95": 10.0}, "error_rate": 0.0}]},
        "resources": {"rss_delta_bytes": 1024, "cpu_to_wall_ratio": 0.1},
    }
    evaluation = runner.evaluate_budgets(result)
    assert evaluation["passed"] is True
    assert all(check["status"] == "PASS" for check in evaluation["checks"])


def test_phase_22_does_not_add_business_plane_artifacts() -> None:
    assert not list((PROJECT_ROOT / "backend" / "alembic" / "versions").glob("*phase_22*"))
    assert not list((PROJECT_ROOT / "backend" / "app" / "api" / "routes").glob("*phase_22*"))
    assert not list((PROJECT_ROOT / "backend" / "app" / "models").glob("*phase_22*"))
    assert not list((PROJECT_ROOT / "backend" / "app" / "plugins").glob("*phase_22*"))
