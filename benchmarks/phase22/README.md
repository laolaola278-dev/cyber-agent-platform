# Phase 22 Benchmark Assets

These assets validate CAP without adding business features, APIs, models, or plugins.

## Safety boundary

- Target only an isolated local CAP instance.
- Never point the scripts at production or an unknown external endpoint.
- Synthetic asset values use the reserved `.test` domain.
- Process-kill, database-restart, and Redis-restart scenarios require a disposable container environment.

## Python isolated benchmark

From the repository root:

```powershell
backend\.venv\Scripts\python.exe benchmarks\phase22\run_benchmarks.py --profile smoke
backend\.venv\Scripts\python.exe benchmarks\phase22\run_benchmarks.py --profile full
```

The runner writes JSON to `outputs/phase22-results/` and records P50/P90/P95/P99/Max, TPS, errors, CPU time, RSS, Python heap, object count, and GC deltas. API results are emitted separately for GET, POST, PUT, and DELETE at each concurrency level. The JSON also contains per-sample failure types, validation-budget decisions, retained-heap diagnostics, and `cProfile` CPU hot paths.

The in-process ASGI runner keeps structured logging enabled. High-concurrency latency therefore includes application logging overhead and local load-generator contention. SQLite `StaticPool` results validate scale behavior and failure handling only; they are not PostgreSQL or production HTTP capacity claims.

## k6

`k6-api.js` defines six sequential constant-VU scenarios: 1, 10, 50, 100, 500, and 1000. Thresholds fail the run when error rate is at least 1%, p95 is at least 500 ms, or p99 is at least 1000 ms.

```powershell
$env:CAP_BASE_URL = "http://127.0.0.1:8000"
k6 run --summary-export outputs\phase22-results\k6-summary.json benchmarks\phase22\k6-api.js
```

## Locust

`locustfile.py` models one user performing Asset CRUD. Distributed mode uses one master and one or more workers. On Windows, start the processes explicitly because fork-based `--processes` is unavailable.

```powershell
locust -f benchmarks\phase22\locustfile.py --master --headless --expect-workers 2 -u 1000 -r 100 --run-time 2m --host http://127.0.0.1:8000 --csv outputs\phase22-results\locust
locust -f benchmarks\phase22\locustfile.py --worker --master-host 127.0.0.1
```

## Vegeta

The PowerShell runner applies a constant request rate to public health/readiness/metrics endpoints and emits a JSON report.

```powershell
benchmarks\phase22\run_vegeta.ps1 -Rate 100 -Duration 30s
```

Tool-generated load must be compared with server-side Prometheus metrics and OpenTelemetry spans to detect load-generator saturation and hot paths.
