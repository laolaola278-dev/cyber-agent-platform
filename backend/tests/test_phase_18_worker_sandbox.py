"""Phase 18 compatibility tests on the Phase 18.1 database execution path."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.database import Base
from app.events import EventType
from app.exceptions import (
    SandboxPolicyViolation,
    SecretNotFound,
    SecretPolicyViolation,
    WorkerLeaseConflict,
    WorkerUnavailable,
)
from app.models.worker import SecretReferenceRecord
from app.runtime.plugin_manifest import load_plugin_manifest
from app.sandbox import (
    MemorySandboxProvider,
    MemorySecretProvider,
    SandboxPolicy,
    SandboxPolicyEngine,
    SandboxProfile,
    SandboxRuntime,
    SecretReference,
)
from app.worker import (
    PluginExecutionRequest,
    WorkerHeartbeat,
    WorkerLeaseManager,
    WorkerManager,
    WorkerRecord,
    WorkerRegistry,
    WorkerRuntime,
    WorkerScheduler,
    WorkerStatus,
)
from tests.conftest import TestSessionFactory


async def _runtime(session, capability: str = "plugin.execute"):
    registry = WorkerRegistry(session)
    worker = await registry.register(
        WorkerRecord(
            name=f"worker-{uuid4()}",
            runtime_version="phase-18.1",
            capabilities=frozenset({capability}),
        )
    )
    worker = await registry.heartbeat(
        WorkerHeartbeat(
            worker_id=worker.id,
            status=WorkerStatus.ONLINE,
            active_executions=0,
        )
    )
    leases = WorkerLeaseManager(session)
    provider = MemorySandboxProvider()
    sandbox = SandboxRuntime(provider, SandboxPolicyEngine())
    runtime = WorkerRuntime(session, registry, WorkerScheduler(registry), leases, sandbox)
    return registry, worker, leases, provider, runtime


async def test_worker_registry_scheduler_heartbeat_and_stale_detection() -> None:
    async with TestSessionFactory() as session:
        registry, worker, _, _, _ = await _runtime(session, "assessment.execute")
        selected = await WorkerScheduler(registry).select("assessment.execute")
        assert selected.id == worker.id
        busy = await registry.heartbeat(
            WorkerHeartbeat(
                worker_id=worker.id,
                status=WorkerStatus.BUSY,
                active_executions=1,
            )
        )
        assert busy.active_executions == 1
        with pytest.raises(WorkerUnavailable):
            await WorkerScheduler(registry).select("missing.capability")
        stale = await registry.mark_stale(
            heartbeat_timeout_seconds=1,
            now=datetime.now(UTC) + timedelta(milliseconds=1500),
        )
        assert stale[0].status is WorkerStatus.UNHEALTHY


async def test_worker_lease_acquire_renew_release_expire_and_owner_guards() -> None:
    async with TestSessionFactory() as session:
        _, worker, leases, _, _ = await _runtime(session)
        now = datetime.now(UTC)
        lease = await leases.acquire(
            worker_id=worker.id,
            execution_id=uuid4(),
            owner="orchestrator",
            ttl_seconds=30,
            now=now,
        )
        renewed = await leases.renew(
            lease.id,
            owner="orchestrator",
            fencing_token=lease.fencing_token,
            expected_version=lease.version,
            ttl_seconds=60,
            now=now + timedelta(seconds=1),
        )
        assert renewed.version == 2
        with pytest.raises(WorkerLeaseConflict, match="fencing"):
            await leases.release(
                lease.id,
                owner="wrong-owner",
                fencing_token=renewed.fencing_token,
                expected_version=renewed.version,
            )
        released = await leases.release(
            lease.id,
            owner="orchestrator",
            fencing_token=renewed.fencing_token,
            expected_version=renewed.version,
        )
        assert released.status == "RELEASED"


async def test_worker_runtime_success_retry_timeout_recovery_and_health() -> None:
    async with TestSessionFactory() as session:
        registry, worker, leases, provider, runtime = await _runtime(session)
        request = PluginExecutionRequest(
            plugin_name="synthetic",
            plugin_version="1.0.0",
            capability="plugin.execute",
            sandbox_profile=SandboxProfile(name="test", timeout_seconds=1),
            retry_limit=1,
        )
        attempts = 0

        async def flaky() -> dict[str, object]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("first attempt")
            return {"ok": True}

        result = await runtime.execute(request, flaky, owner="workflow")
        assert result.status == "SUCCEEDED"
        assert result.attempts == 2
        assert (await registry.require(worker.id)).status is WorkerStatus.ONLINE

        async def hangs() -> dict[str, object]:
            await asyncio.sleep(2)
            return {}

        timed_out = await runtime.execute(
            request.model_copy(update={"retry_limit": 0}), hangs, owner="workflow"
        )
        assert timed_out.timed_out is True
        manager = WorkerManager(registry, leases, runtime)
        assert (await manager.health())["status"] == "ok"
        assert provider.real_isolation is False


def test_sandbox_profile_policy_and_secret_provider_fail_closed() -> None:
    with pytest.raises(ValidationError, match="secret-like"):
        SandboxProfile(name="bad-env", environment={"API_TOKEN": "value"})
    with pytest.raises(ValidationError, match=".env"):
        SandboxProfile(name="bad-secret", secret_references=("config/.env",))
    with pytest.raises(ValidationError, match="explicit allowlist"):
        SandboxProfile(name="bad-network", network_enabled=True)
    profile = SandboxProfile(
        name="networked", network_enabled=True, allowed_networks=("asset-scope",)
    )
    with pytest.raises(SandboxPolicyViolation, match="network"):
        SandboxPolicyEngine().validate(profile, "memory-sandbox")
    SandboxPolicyEngine(SandboxPolicy(allow_network=True)).validate(profile, "memory-sandbox")
    provider = MemorySecretProvider({"zap-api-key": "secret-value"})
    reference = SecretReference(name="zap-api-key", purpose="ZAP auth")
    resolved = asyncio.run(provider.resolve(reference))
    assert resolved.value.get_secret_value() == "secret-value"
    assert "secret-value" not in repr(resolved)
    with pytest.raises(SecretNotFound):
        asyncio.run(provider.resolve(SecretReference(name="missing", purpose="negative test")))
    with pytest.raises(SecretPolicyViolation):
        asyncio.run(
            provider.resolve(
                SecretReference(name="zap-api-key", provider="vault", purpose="provider mismatch")
            )
        )


def test_database_tables_manifest_schema_and_audit_contracts() -> None:
    expected = {
        "workers",
        "worker_leases",
        "sandbox_executions",
        "sandbox_profiles",
        "secret_references",
    }
    assert expected <= set(Base.metadata.tables)
    assert "value" not in SecretReferenceRecord.__table__.columns
    assert EventType.WORKER_STATE_CHANGED.value == "WorkerStateChanged"
    root = Path(__file__).resolve().parents[2] / "plugins"
    manifests = list(root.rglob("manifest.yaml"))
    assert len(manifests) == 9
    for path in manifests:
        manifest = load_plugin_manifest(path.read_text(encoding="utf-8"))
        assert manifest.worker.runtime_version == manifest.runtime_version
        assert manifest.sandbox.environment == {}


async def test_worker_and_sandbox_read_apis(client: AsyncClient) -> None:
    workers = await client.get("/workers")
    assert workers.status_code == 200
    body = workers.json()
    assert len(body) == 1
    assert body[0]["name"] == "memory-worker"
    assert (await client.get(f"/workers/{body[0]['id']}")).status_code == 200
    health = await client.get("/health/workers")
    assert health.status_code == 200
    assert health.json()["sandbox_healthy"] is True
    sandbox = await client.get("/sandbox")
    assert sandbox.status_code == 200
    assert sandbox.json() == []
    assert (await client.get(f"/sandbox/{uuid4()}")).status_code == 422
