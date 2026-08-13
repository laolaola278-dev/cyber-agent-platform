"""Isolated Phase 22 benchmark runner for CAP.

This runner uses only synthetic providers, an in-memory SQLite database, and the
in-process ASGI application. It never targets a production endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import ctypes
import gc
import json
import math
import os
import platform
import pstats
import statistics
import sys
import time
import tracemalloc
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base, get_db_session
from app.events import InMemoryEventBus
from app.main import create_app
from app.models.asset import Asset
from app.models.playbook import (
    Playbook,
    PlaybookExecution,
    PlaybookVersion,
)
from app.playbook.contracts import PlaybookDSL
from app.playbook.executor import StepOutcome
from app.playbook.planner import PlaybookPlan
from app.playbook.policy import PlaybookPolicy
from app.playbook.runtime import PlaybookRuntime
from app.repositories.playbook import PlaybookExecutionRepository
from app.sandbox import (
    MemorySandboxProvider,
    SandboxPolicyEngine,
    SandboxProfile,
    SandboxRuntime,
)
from app.worker import (
    PluginExecutionRequest,
    WorkerHeartbeat,
    WorkerLeaseManager,
    WorkerRecord,
    WorkerRegistry,
    WorkerRuntime,
    WorkerScheduler,
    WorkerStatus,
)

CONCURRENCY_LEVELS = (1, 10, 50, 100, 500, 1000)
WORKER_LEVELS = (1, 2, 4, 8, 16)
PLAYBOOK_LEVELS = (100, 500, 1000)
HEADERS = {
    "X-CAP-User": "administrator",
    "X-CAP-Proxy-Secret": "change-me-proxy-secret",
}
BUDGET_PATH = Path(__file__).with_name("performance_budget.json")


class BenchmarkSampleFailure(RuntimeError):
    """Failure label retained in benchmark output without aborting the run."""


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(name: str, durations: list[float], errors: int, elapsed: float) -> dict[str, Any]:
    milliseconds = [value * 1000 for value in durations]
    count = len(durations)
    return {
        "name": name,
        "count": count,
        "errors": errors,
        "error_rate": errors / count if count else 0.0,
        "elapsed_seconds": elapsed,
        "tps": count / elapsed if elapsed else 0.0,
        "latency_ms": {
            "p50": percentile(milliseconds, 0.50),
            "p90": percentile(milliseconds, 0.90),
            "p95": percentile(milliseconds, 0.95),
            "p99": percentile(milliseconds, 0.99),
            "max": max(milliseconds, default=0.0),
            "mean": statistics.fmean(milliseconds) if milliseconds else 0.0,
        },
    }


def working_set_bytes() -> int | None:
    if os.name != "nt":
        return None
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    success = psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    )
    return int(counters.WorkingSetSize) if success else None


class ResourceProbe:
    def __enter__(self) -> Self:
        gc.collect()
        self.before_gc = tuple(gc.get_count())
        self.before_objects = len(gc.get_objects())
        self.before_rss = working_set_bytes()
        self.before_cpu = time.process_time()
        self.before_wall = time.perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        self.elapsed = time.perf_counter() - self.before_wall
        self.cpu_seconds = time.process_time() - self.before_cpu
        self.after_rss = working_set_bytes()
        self.after_objects = len(gc.get_objects())
        self.after_gc = tuple(gc.get_count())

    def report(self) -> dict[str, Any]:
        rss_delta = None
        if self.before_rss is not None and self.after_rss is not None:
            rss_delta = self.after_rss - self.before_rss
        return {
            "wall_seconds": self.elapsed,
            "cpu_seconds": self.cpu_seconds,
            "cpu_to_wall_ratio": self.cpu_seconds / self.elapsed if self.elapsed else 0.0,
            "rss_before_bytes": self.before_rss,
            "rss_after_bytes": self.after_rss,
            "rss_delta_bytes": rss_delta,
            "object_delta": self.after_objects - self.before_objects,
            "gc_count_before": self.before_gc,
            "gc_count_after": self.after_gc,
        }


async def measured(operation: Callable[[], Awaitable[bool]]) -> tuple[float, bool, str | None]:
    started = time.perf_counter()
    try:
        ok = await operation()
        failure = None if ok else "operation_returned_false"
    except Exception as exc:  # noqa: BLE001 - exceptions are benchmark samples
        ok = False
        failure = str(exc) if isinstance(exc, BenchmarkSampleFailure) else type(exc).__name__
    return time.perf_counter() - started, ok, failure


async def gather_bounded(
    count: int,
    concurrency: int,
    operation: Callable[[int], Awaitable[bool]],
) -> tuple[list[float], int, float, dict[str, int]]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(index: int) -> tuple[float, bool, str | None]:
        async with semaphore:
            return await measured(lambda: operation(index))

    started = time.perf_counter()
    results = await asyncio.gather(*(one(index) for index in range(count)))
    elapsed = time.perf_counter() - started
    failures = Counter(item[2] for item in results if item[2] is not None)
    return (
        [item[0] for item in results],
        sum(not item[1] for item in results),
        elapsed,
        dict(failures),
    )


async def make_database() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


async def api_benchmark(profile: str) -> dict[str, Any]:
    engine, sessions = await make_database()
    app = create_app()

    async def override_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    app.state.audit_session_factory = sessions
    levels = CONCURRENCY_LEVELS if profile == "full" else CONCURRENCY_LEVELS[:2]
    results: list[dict[str, Any]] = []
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://phase22", headers=HEADERS
    ) as client:
        for level in levels:
            async def health(_: int) -> bool:
                response = await client.get("/health")
                return response.status_code == 200

            durations, errors, elapsed, failures = await gather_bounded(level, level, health)
            result = summarize(f"GET /health concurrency={level}", durations, errors, elapsed)
            result["concurrency"] = level
            result["failure_types"] = failures
            results.append(result)

            asset_ids: list[str | None] = [None] * level

            async def create_asset(
                index: int,
                concurrency: int = level,
                current_asset_ids: list[str | None] = asset_ids,
            ) -> bool:
                suffix = f"{concurrency}-{index}-{uuid4()}"
                response = await client.post(
                    "/assets",
                    json={
                        "asset_type": "HOST",
                        "name": f"phase22-{suffix}",
                        "value": f"phase22-{suffix}.example.test",
                        "environment": "phase22",
                        "tags": ["benchmark"],
                    },
                )
                if response.status_code != 201:
                    raise BenchmarkSampleFailure(
                        f"POST_/assets_status_{response.status_code}"
                    )
                current_asset_ids[index] = response.json()["id"]
                return True

            async def get_asset(
                index: int, current_asset_ids: list[str | None] = asset_ids
            ) -> bool:
                asset_id = current_asset_ids[index]
                if asset_id is None:
                    raise BenchmarkSampleFailure("GET_/assets/id_missing_fixture")
                response = await client.get(f"/assets/{asset_id}")
                if response.status_code != 200:
                    raise BenchmarkSampleFailure(
                        f"GET_/assets/id_status_{response.status_code}"
                    )
                return True

            async def update_asset(
                index: int, current_asset_ids: list[str | None] = asset_ids
            ) -> bool:
                asset_id = current_asset_ids[index]
                if asset_id is None:
                    raise BenchmarkSampleFailure("PUT_/assets/id_missing_fixture")
                response = await client.put(f"/assets/{asset_id}", json={"risk": "LOW"})
                if response.status_code != 200:
                    raise BenchmarkSampleFailure(
                        f"PUT_/assets/id_status_{response.status_code}"
                    )
                return True

            async def delete_asset(
                index: int, current_asset_ids: list[str | None] = asset_ids
            ) -> bool:
                asset_id = current_asset_ids[index]
                if asset_id is None:
                    raise BenchmarkSampleFailure("DELETE_/assets/id_missing_fixture")
                response = await client.delete(f"/assets/{asset_id}")
                if response.status_code != 204:
                    raise BenchmarkSampleFailure(
                        f"DELETE_/assets/id_status_{response.status_code}"
                    )
                return True

            for method, path, operation in (
                ("POST", "/assets", create_asset),
                ("GET", "/assets/{asset_id}", get_asset),
                ("PUT", "/assets/{asset_id}", update_asset),
                ("DELETE", "/assets/{asset_id}", delete_asset),
            ):
                durations, errors, elapsed, failures = await gather_bounded(
                    level, level, operation
                )
                result = summarize(
                    f"{method} {path} concurrency={level}", durations, errors, elapsed
                )
                result["method"] = method
                result["path"] = path
                result["concurrency"] = level
                result["failure_types"] = failures
                results.append(result)
    metrics = app.state.metrics_registry.render()
    async with sessions() as session:
        total_assets = int(await session.scalar(select(func.count()).select_from(Asset)) or 0)
        active_assets = int(
            await session.scalar(
                select(func.count()).select_from(Asset).where(Asset.deleted_at.is_(None))
            )
            or 0
        )
    await engine.dispose()
    return {
        "environment": "in-process ASGI + SQLite StaticPool",
        "results": results,
        "asset_rows": {
            "total_including_soft_deleted": total_assets,
            "active": active_assets,
            "soft_deleted": total_assets - active_assets,
        },
        "observability": {
            "metrics_present": all(
                item in metrics
                for item in (
                    "cap_http_requests_total",
                    "cap_http_request_duration_seconds",
                    "cap_http_requests_in_progress",
                )
            ),
            "route_template_low_cardinality": '/assets/{asset_id}' in metrics,
        },
    }


async def worker_benchmark(profile: str) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    scheduling_iterations = 1000 if profile == "full" else 50
    for worker_count in WORKER_LEVELS:
        engine, sessions = await make_database()
        async with sessions() as session:
            registry = WorkerRegistry(session)
            workers = []
            for index in range(worker_count):
                worker = await registry.register(
                    WorkerRecord(
                        name=f"phase22-worker-{worker_count}-{index}",
                        runtime_version="phase22",
                        capabilities=frozenset({"phase22.synthetic"}),
                        max_concurrency=1024,
                    )
                )
                workers.append(
                    await registry.heartbeat(
                        WorkerHeartbeat(
                            worker_id=worker.id,
                            status=WorkerStatus.ONLINE,
                            active_executions=0,
                        )
                    )
                )
            scheduler = WorkerScheduler(registry)
            durations = []
            started = time.perf_counter()
            for _ in range(scheduling_iterations):
                duration, ok, _ = await measured(
                    lambda current=scheduler: _scheduler_select(
                        current, "phase22.synthetic"
                    )
                )
                durations.append(duration)
                if not ok:
                    raise RuntimeError("Synthetic worker scheduling failed")
            elapsed = time.perf_counter() - started
            result = summarize(
                f"WorkerScheduler workers={worker_count}", durations, 0, elapsed
            )
            result["worker_count"] = worker_count
            results.append(result)
        await engine.dispose()

    engine, sessions = await make_database()
    execution_count = 100 if profile == "full" else 10
    async with sessions() as session:
        registry = WorkerRegistry(session)
        worker = await registry.register(
            WorkerRecord(
                name="phase22-runtime-worker",
                runtime_version="phase22",
                capabilities=frozenset({"phase22.synthetic"}),
                max_concurrency=1024,
            )
        )
        await registry.heartbeat(
            WorkerHeartbeat(
                worker_id=worker.id, status=WorkerStatus.ONLINE, active_executions=0
            )
        )
        leases = WorkerLeaseManager(session)
        runtime = WorkerRuntime(
            session,
            registry,
            WorkerScheduler(registry),
            leases,
            SandboxRuntime(MemorySandboxProvider(), SandboxPolicyEngine()),
        )
        request = PluginExecutionRequest(
            plugin_name="phase22-synthetic",
            plugin_version="1.0.0",
            capability="phase22.synthetic",
            sandbox_profile=SandboxProfile(name="phase22"),
        )

        async def operation() -> dict[str, Any]:
            return {"status": "ok"}

        durations = []
        errors = 0
        started = time.perf_counter()
        for _ in range(execution_count):
            duration, ok, _ = await measured(
                lambda: _worker_execute(runtime, request, operation)
            )
            durations.append(duration)
            errors += not ok
        elapsed = time.perf_counter() - started
        results.append(summarize("WorkerRuntime synthetic execution", durations, errors, elapsed))
    await engine.dispose()
    return {"results": results}


async def _scheduler_select(scheduler: WorkerScheduler, capability: str) -> bool:
    return (await scheduler.select(capability)).status in {
        WorkerStatus.ONLINE,
        WorkerStatus.BUSY,
    }


async def _worker_execute(
    runtime: WorkerRuntime,
    request: PluginExecutionRequest,
    operation: Callable[[], Awaitable[dict[str, Any]]],
) -> bool:
    return (await runtime.execute(request, operation, owner="phase22")).status == "SUCCEEDED"


async def plugin_benchmark(profile: str) -> dict[str, Any]:
    count = 1000 if profile == "full" else 50
    results = []
    for capability in (
        "assessment.synthetic",
        "detection.synthetic",
        "response.synthetic",
        "notification.synthetic",
    ):
        sandbox = SandboxRuntime(MemorySandboxProvider(), SandboxPolicyEngine())
        profile_contract = SandboxProfile(name=f"phase22-{capability}")

        async def operation(
            _: int,
            current_sandbox: SandboxRuntime = sandbox,
            current_profile: SandboxProfile = profile_contract,
            current_capability: str = capability,
        ) -> bool:
            result = await current_sandbox.execute(
                current_profile,
                lambda: _synthetic_plugin_output(current_capability),
            )
            return result.status == "SUCCEEDED"

        durations, errors, elapsed, failures = await gather_bounded(
            count, min(count, 100), operation
        )
        item = summarize(capability, durations, errors, elapsed)
        item["provider"] = "memory-sandbox"
        item["network_access"] = False
        item["failure_types"] = failures
        results.append(item)
    return {"results": results}


async def _synthetic_plugin_output(capability: str) -> dict[str, Any]:
    await asyncio.sleep(0)
    return {"capability": capability, "synthetic": True}


PLAYBOOK_SOURCE = """
dsl_version: v1
name: phase22-runtime
trigger:
  type: manual
steps:
  - id: check
    type: condition
    condition: "True"
timeout_seconds: 60
max_parallel: 1
allowed_plugins: []
allowed_capabilities: []
allowed_runners: [phase22]
"""


class SyntheticPlaybookExecutor:
    async def execute(self, step: Any, **_: Any) -> StepOutcome:
        return StepOutcome(status="SUCCEEDED", output={"step": step.id})

    async def compensate(self, step: Any, output: dict[str, Any], **_: Any) -> dict[str, Any]:
        return {"status": "COMPENSATED", "step": step.id, "output": output}


async def playbook_benchmark(profile: str) -> dict[str, Any]:
    levels = PLAYBOOK_LEVELS if profile == "full" else (10,)
    results = []
    for count in levels:
        engine, sessions = await make_database()
        document = PlaybookDSL.load(PLAYBOOK_SOURCE)
        async with sessions() as session:
            playbook = Playbook(name=f"phase22-{count}", enabled=True)
            session.add(playbook)
            await session.flush()
            version = PlaybookVersion(
                playbook_id=playbook.id,
                version="1.0.0",
                dsl_version="v1",
                source_yaml=PLAYBOOK_SOURCE,
                document=document.model_dump(mode="json"),
                checksum="2" * 64,
            )
            session.add(version)
            await session.commit()
            runtime = PlaybookRuntime(
                session,
                PlaybookExecutionRepository(session),
                SyntheticPlaybookExecutor(),
                InMemoryEventBus(),
                PlaybookPolicy(),
            )
            plan = PlaybookPlan(document=document, steps=tuple(document.steps))
            durations = []
            errors = 0
            started = time.perf_counter()
            for _ in range(count):
                execution = PlaybookExecution(
                    playbook_id=playbook.id,
                    playbook_version_id=version.id,
                    trigger_type="manual",
                    status="PENDING",
                    actor="phase22",
                    input={},
                    context={},
                    trace_id=str(uuid4()),
                )
                session.add(execution)
                await session.commit()
                duration, ok, _ = await measured(
                    lambda current_runtime=runtime, current_execution=execution, current_plan=plan: (
                        _playbook_execute(current_runtime, current_execution, current_plan)
                    )
                )
                durations.append(duration)
                errors += not ok
            elapsed = time.perf_counter() - started
            result = summarize(f"PlaybookRuntime executions={count}", durations, errors, elapsed)
            result["execution_count"] = count
            results.append(result)
        await engine.dispose()
    return {"results": results}


async def _playbook_execute(
    runtime: PlaybookRuntime,
    execution: PlaybookExecution,
    plan: PlaybookPlan,
) -> bool:
    return (await runtime.execute(execution, plan, approvals={})).status == "SUCCEEDED"


async def database_benchmark(profile: str) -> dict[str, Any]:
    engine, sessions = await make_database()
    count = 1000 if profile == "full" else 50
    insert_durations = []
    query_durations = []
    async with sessions() as session:
        for index in range(count):
            started = time.perf_counter()
            session.add(
                Asset(
                    asset_type="HOST",
                    name=f"db-phase22-{index}",
                    value=f"db-phase22-{index}.example.test",
                    canonical_value=f"db-phase22-{index}.example.test",
                    tags=[],
                    capabilities=[],
                    properties={},
                )
            )
            await session.commit()
            insert_durations.append(time.perf_counter() - started)
        for page in range(1, 11):
            started = time.perf_counter()
            rows = await session.scalars(
                select(Asset).order_by(Asset.created_at, Asset.id).limit(100).offset((page - 1) * 100)
            )
            list(rows)
            query_durations.append(time.perf_counter() - started)
        total = int(await session.scalar(select(func.count()).select_from(Asset)) or 0)
    await engine.dispose()
    return {
        "environment": "SQLite in-memory; lock and PostgreSQL pool capacity are not production claims",
        "transaction_insert": summarize(
            "database transaction insert", insert_durations, 0, sum(insert_durations)
        ),
        "pagination_query": summarize(
            "database pagination query", query_durations, 0, sum(query_durations)
        ),
        "row_count": total,
        "lock_validation": "contract-only on SQLite; PostgreSQL online lock test gated",
        "pool_validation": "StaticPool isolation harness; PostgreSQL pool saturation gated",
    }


async def diagnostics_benchmark() -> dict[str, Any]:
    gc.collect()
    tracemalloc.start()
    heap_before = tracemalloc.get_traced_memory()[0]
    await plugin_benchmark("smoke")
    gc.collect()
    heap_after, heap_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    profiler = cProfile.Profile()
    profiler.enable()
    await worker_benchmark("smoke")
    profiler.disable()
    stats = pstats.Stats(profiler)
    top_functions = []
    for (filename, line, function), values in sorted(
        stats.stats.items(), key=lambda item: item[1][3], reverse=True
    )[:20]:
        primitive_calls, total_calls, total_time, cumulative_time, _ = values
        top_functions.append(
            {
                "function": function,
                "file": str(Path(filename).resolve()),
                "line": line,
                "primitive_calls": primitive_calls,
                "total_calls": total_calls,
                "total_time_seconds": total_time,
                "cumulative_time_seconds": cumulative_time,
            }
        )
    return {
        "memory": {
            "workload": "repeat synthetic plugin benchmark after primary measurements",
            "python_heap_before_bytes": heap_before,
            "python_heap_after_gc_bytes": heap_after,
            "python_heap_retained_delta_bytes": heap_after - heap_before,
            "python_heap_peak_bytes": heap_peak,
            "interpretation": "single-process diagnostic; growth alone does not prove a leak",
        },
        "cpu": {
            "workload": "repeat smoke WorkerScheduler and WorkerRuntime benchmark",
            "sort_order": "cumulative_time_seconds descending",
            "top_functions": top_functions,
        },
    }


def evaluate_budgets(result: dict[str, Any]) -> dict[str, Any]:
    budget = json.loads(BUDGET_PATH.read_text(encoding="utf-8"))
    checks: list[dict[str, Any]] = []

    def add(name: str, actual: float | None, limit: float, operator: str) -> None:
        passed = actual is not None and (
            actual <= limit if operator == "<=" else actual >= limit
        )
        checks.append(
            {
                "name": name,
                "actual": actual,
                "limit": limit,
                "operator": operator,
                "status": "PASS" if passed else "FAIL",
            }
        )

    api_results = result["api"]["results"]
    add(
        "API worst P95 latency",
        max(item["latency_ms"]["p95"] for item in api_results),
        budget["api"]["p95_ms"],
        "<=",
    )
    add(
        "API worst P99 latency",
        max(item["latency_ms"]["p99"] for item in api_results),
        budget["api"]["p99_ms"],
        "<=",
    )
    add(
        "API worst error rate",
        max(item["error_rate"] for item in api_results),
        budget["api"]["error_rate_max"],
        "<=",
    )
    worker_results = result["worker"]["results"]
    scheduler_results = [item for item in worker_results if "WorkerScheduler" in item["name"]]
    add(
        "Worker scheduling worst P95 latency",
        max(item["latency_ms"]["p95"] for item in scheduler_results),
        budget["worker"]["scheduling_p95_ms"],
        "<=",
    )
    add(
        "Worker minimum success rate",
        min(1.0 - item["error_rate"] for item in worker_results),
        budget["worker"]["success_rate_min"],
        ">=",
    )
    plugin_results = result["plugin"]["results"]
    add(
        "Plugin worst P95 latency",
        max(item["latency_ms"]["p95"] for item in plugin_results),
        budget["plugin"]["p95_ms"],
        "<=",
    )
    add(
        "Plugin minimum success rate",
        min(1.0 - item["error_rate"] for item in plugin_results),
        budget["plugin"]["success_rate_min"],
        ">=",
    )
    playbook_results = result["playbook"]["results"]
    add(
        "Playbook worst P95 latency",
        max(item["latency_ms"]["p95"] for item in playbook_results),
        budget["playbook"]["p95_ms"],
        "<=",
    )
    add(
        "Playbook minimum success rate",
        min(1.0 - item["error_rate"] for item in playbook_results),
        budget["playbook"]["success_rate_min"],
        ">=",
    )
    rss_delta = result["resources"]["rss_delta_bytes"]
    add(
        "RSS growth",
        rss_delta / (1024 * 1024) if rss_delta is not None else None,
        budget["resources"]["rss_growth_mb_max"],
        "<=",
    )
    add(
        "CPU to wall ratio",
        result["resources"]["cpu_to_wall_ratio"],
        budget["resources"]["cpu_utilization_ratio_max"],
        "<=",
    )
    return {
        "scope": budget["scope"],
        "checks": checks,
        "passed": all(item["status"] == "PASS" for item in checks),
        "notes": budget["notes"],
    }


async def reliability_benchmark() -> dict[str, Any]:
    provider = MemorySandboxProvider()
    sandbox = SandboxRuntime(provider, SandboxPolicyEngine())
    profile = SandboxProfile(name="phase22-chaos", timeout_seconds=1)

    async def plugin_crash() -> dict[str, Any]:
        raise RuntimeError("synthetic plugin crash")

    crash = await sandbox.execute(profile, plugin_crash)

    async def plugin_timeout() -> dict[str, Any]:
        await asyncio.sleep(1.1)
        return {}

    timeout = await sandbox.execute(profile, plugin_timeout)

    engine, sessions = await make_database()
    async with sessions() as session:
        registry = WorkerRegistry(session)
        worker = await registry.register(
            WorkerRecord(
                name="phase22-recovery-worker",
                runtime_version="phase22",
                capabilities=frozenset({"phase22.synthetic"}),
            )
        )
        worker = await registry.heartbeat(
            WorkerHeartbeat(
                worker_id=worker.id, status=WorkerStatus.ONLINE, active_executions=0
            )
        )
        leases = WorkerLeaseManager(session)
        lease = await leases.acquire(
            worker_id=worker.id,
            execution_id=uuid4(),
            owner="phase22",
            ttl_seconds=1,
            now=datetime.now(UTC) - timedelta(seconds=2),
        )
        expired = await leases.expire(now=datetime.now(UTC))
    await engine.dispose()
    return {
        "plugin_crash_fail_closed": crash.status == "FAILED" and crash.exit_code == 1,
        "plugin_timeout_terminated": timeout.timed_out and timeout.terminated,
        "lease_recovery": len(expired) == 1 and expired[0].id == lease.id,
        "worker_crash": "synthetic operation exception validated; OS process kill requires disposable environment",
        "api_crash": "not executed; no disposable server process",
        "database_restart": "not executed; Docker Engine unavailable",
        "redis_restart": "not executed; Docker Engine unavailable",
        "queue_full": "covered by existing bounded queue reliability tests",
        "execution_replay": "covered by existing telemetry and idempotency tests",
    }


async def run(profile: str) -> dict[str, Any]:
    with ResourceProbe() as probe:
        api = await api_benchmark(profile)
        worker = await worker_benchmark(profile)
        plugin = await plugin_benchmark(profile)
        playbook = await playbook_benchmark(profile)
        database = await database_benchmark(profile)
        reliability = await reliability_benchmark()
    diagnostics = await diagnostics_benchmark()
    result = {
        "schema_version": "phase22.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "profile": profile,
        "scope": "isolated local validation; synthetic providers only",
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpu": os.cpu_count(),
        },
        "api": api,
        "worker": worker,
        "plugin": plugin,
        "playbook": playbook,
        "database": database,
        "reliability": reliability,
        "resources": probe.report(),
        "diagnostics": diagnostics,
        "environment_gates": {
            "k6": "not installed",
            "locust": "not installed",
            "vegeta": "not installed",
            "docker_engine": "not running",
            "postgresql_online": "not executed",
            "redis_online": "not executed",
            "distributed_load_generation": "not executed",
            "2c4g_4c8g_8c16g_hardware": "not physically available; recommendations require extrapolation",
        },
    }
    result["performance_budget"] = evaluate_budgets(result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run isolated CAP Phase 22 benchmarks")
    parser.add_argument("--profile", choices=("smoke", "full"), default="smoke")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "phase22-results" / "benchmark.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(run(args.profile))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
