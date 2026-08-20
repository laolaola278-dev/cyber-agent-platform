"""Phase 18.1 production-consistency acceptance tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import select

from app.events import EventType
from app.exceptions import (
    InvalidStateTransition,
    SandboxExecutionError,
    SandboxPolicyViolation,
    SecretNotFound,
    SecretPolicyViolation,
    WorkerConflict,
    WorkerLeaseConflict,
    WorkerLeaseNotFound,
    WorkerNotFound,
)
from app.models import AuditLog
from app.models.worker import SandboxExecution
from app.repositories.worker import SandboxExecutionRepository, WorkerRepository
from app.runtime.plugin_manifest import (
    PluginManifestV1,
    PluginManifestV2,
    load_plugin_manifest,
)
from app.sandbox import (
    MemorySandboxProvider,
    MemorySecretProvider,
    SandboxPolicy,
    SandboxPolicyEngine,
    SandboxProfile,
    SandboxProviderCapability,
    SandboxRuntime,
    SecretReference,
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
from app.worker.state_machine import validate_transition
from tests.conftest import TestSessionFactory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_MANIFESTS = (
    PROJECT_ROOT / "plugins/nuclei/manifest.yaml",
    PROJECT_ROOT / "plugins/zap/manifest.yaml",
    PROJECT_ROOT / "plugins/suricata/manifest.yaml",
    PROJECT_ROOT / "plugins/zeek/manifest.yaml",
    PROJECT_ROOT / "plugins/response/synthetic/manifest.yaml",
    PROJECT_ROOT / "plugins/response/waf/manifest.yaml",
    PROJECT_ROOT / "plugins/response/firewall/manifest.yaml",
    PROJECT_ROOT / "plugins/notification/synthetic/manifest.yaml",
)


async def _stack(session):
    registry = WorkerRegistry(session)
    worker = await registry.register(
        WorkerRecord(
            name=f"consistency-{uuid4()}",
            runtime_version="phase-18.1",
            capabilities=frozenset({"plugin.execute"}),
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
    runtime = WorkerRuntime(
        session,
        registry,
        WorkerScheduler(registry),
        leases,
        SandboxRuntime(MemorySandboxProvider(), SandboxPolicyEngine()),
    )
    return registry, worker, leases, runtime


def test_strict_worker_state_machine_rejects_illegal_jump() -> None:
    validate_transition(WorkerStatus.REGISTERED, WorkerStatus.ONLINE)
    validate_transition(WorkerStatus.ONLINE, WorkerStatus.BUSY)
    validate_transition(WorkerStatus.BUSY, WorkerStatus.DRAINING)
    validate_transition(WorkerStatus.DRAINING, WorkerStatus.OFFLINE)
    validate_transition(WorkerStatus.UNHEALTHY, WorkerStatus.DEAD)
    with pytest.raises(InvalidStateTransition):
        validate_transition(WorkerStatus.REGISTERED, WorkerStatus.BUSY)
    with pytest.raises(InvalidStateTransition):
        validate_transition(WorkerStatus.DEAD, WorkerStatus.BUSY)


async def test_worker_repository_is_source_of_truth_not_cache() -> None:
    async with TestSessionFactory() as session:
        registry, worker, _, _ = await _stack(session)
        registry._cache.clear()
        reread = await registry.require(worker.id)
        assert reread.id == worker.id
        assert reread.status is WorkerStatus.ONLINE
        stale_version = reread.state_version
        updated = await registry.heartbeat(
            WorkerHeartbeat(
                worker_id=worker.id,
                status=WorkerStatus.BUSY,
                active_executions=1,
            )
        )
        assert updated.state_version == stale_version + 1


async def test_lease_fencing_rejects_stale_token_and_version() -> None:
    async with TestSessionFactory() as session:
        _, worker, leases, _ = await _stack(session)
        lease = await leases.acquire(
            worker_id=worker.id,
            execution_id=uuid4(),
            owner="owner-a",
            ttl_seconds=60,
        )
        renewed = await leases.renew(
            lease.id,
            owner="owner-a",
            fencing_token=lease.fencing_token,
            expected_version=lease.version,
            ttl_seconds=60,
        )
        with pytest.raises(WorkerLeaseConflict, match="fencing"):
            await leases.release(
                lease.id,
                owner="owner-a",
                fencing_token=lease.fencing_token,
                expected_version=lease.version,
            )
        assert renewed.version == lease.version + 1


async def test_result_commit_rejects_expired_fencing_token() -> None:
    async with TestSessionFactory() as session:
        _, worker, leases, _ = await _stack(session)
        lease = await leases.acquire(
            worker_id=worker.id,
            execution_id=uuid4(),
            owner="owner",
            ttl_seconds=1,
            now=datetime.now(UTC) - timedelta(seconds=2),
        )
        execution_id = uuid4()
        repository = SandboxExecutionRepository(session)
        await repository.add(
            SandboxExecution(
                execution_id=execution_id,
                worker_id=worker.id,
                profile_id=None,
                plugin_name="synthetic",
                plugin_version="1",
                operation="execute",
                provider="memory-sandbox",
                status="RUNNING",
                result_metadata={},
                error=None,
                started_at=datetime.now(UTC),
                finished_at=None,
                timed_out=False,
                terminated=False,
                lease_id=lease.id,
                lease_version=lease.version,
                attempt=1,
                recovery_of_execution_id=None,
            )
        )
        committed = await repository.commit_result(
            execution_id=execution_id,
            lease_id=lease.id,
            owner="owner",
            fencing_token=lease.fencing_token,
            expected_lease_version=lease.version,
            now=datetime.now(UTC),
            values={"status": "SUCCEEDED", "finished_at": datetime.now(UTC)},
        )
        assert committed is None


async def test_execution_persistence_records_failure_and_recovery() -> None:
    async with TestSessionFactory() as session:
        _, _, _, runtime = await _stack(session)
        attempts = 0

        async def operation() -> dict[str, bool]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("retry")
            return {"ok": True}

        result = await runtime.execute(
            PluginExecutionRequest(
                plugin_name="synthetic",
                plugin_version="1.0.0",
                capability="plugin.execute",
                sandbox_profile=SandboxProfile(name="default"),
                retry_limit=1,
            ),
            operation,
            owner="workflow",
        )
        rows = list(await SandboxExecutionRepository(session).list())
        assert result.status == "SUCCEEDED"
        assert [row.status for row in reversed(rows)] == ["FAILED", "RECOVERED"]
        assert rows[0].recovery_of_execution_id == rows[1].execution_id


async def test_secret_failure_is_fail_closed_and_audited() -> None:
    async with TestSessionFactory() as session:
        provider = MemorySecretProvider(session=session)
        with pytest.raises(SecretNotFound):
            await provider.resolve(SecretReference(name="missing", purpose="startup"))
        with pytest.raises(SecretPolicyViolation):
            await provider.resolve(
                SecretReference(name="missing", provider="vault", purpose="startup")
            )
        with pytest.raises(SecretPolicyViolation):
            provider.put("", "value")
        with pytest.raises(SecretPolicyViolation):
            provider.put("runtime.env", "value")
        provider.put("available", "secret-value")
        audited = provider.with_session(session)
        resolved = await audited.resolve(
            SecretReference(name="available", purpose="sandbox injection")
        )
        assert resolved.value.get_secret_value() == "secret-value"
        assert await provider.health() is True
        actions = list(await session.scalars(select(AuditLog.action)))
        assert EventType.SECRET_REFERENCE_RESOLVE_FAILED.value in actions
        assert EventType.SECRET_REFERENCE_RESOLVED.value in actions


def test_sandbox_policy_and_provider_capabilities_fail_closed() -> None:
    checks = (
        (SandboxPolicy(enabled=False), SandboxProfile(name="disabled")),
        (SandboxPolicy(), SandboxProfile(name="provider"), "unknown-provider"),
        (SandboxPolicy(maximum_cpu_millicores=50), SandboxProfile(name="cpu")),
        (SandboxPolicy(maximum_memory_mb=32), SandboxProfile(name="memory")),
        (SandboxPolicy(maximum_timeout_seconds=1), SandboxProfile(name="timeout")),
        (
            SandboxPolicy(maximum_readonly_mounts=0),
            SandboxProfile(
                name="readonly",
                readonly_mounts=({"source": "/input", "target": "/data"},),
            ),
        ),
        (
            SandboxPolicy(maximum_tmp_mounts=0),
            SandboxProfile(name="tmp"),
        ),
        (
            SandboxPolicy(allow_network=False),
            SandboxProfile(name="network", network_enabled=True, allowed_networks=("asset",)),
        ),
        (
            SandboxPolicy(allow_host_filesystem_write=False),
            SandboxProfile(name="filesystem", filesystem_writable=True),
        ),
    )
    for check in checks:
        policy, profile, *provider = check
        engine = SandboxPolicyEngine(policy)
        assert engine.policy is policy
        with pytest.raises(SandboxPolicyViolation):
            engine.validate(profile, provider[0] if provider else "memory-sandbox")


async def test_sandbox_runtime_rejects_unsupported_capabilities_and_bad_identity() -> None:
    class RestrictedProvider(MemorySandboxProvider):
        capabilities = SandboxProviderCapability(timeout=False)

    runtime = SandboxRuntime(
        RestrictedProvider(),
        SandboxPolicyEngine(SandboxPolicy(allow_network=True, allow_host_filesystem_write=True)),
    )

    async def operation() -> dict[str, bool]:
        return {"ok": True}

    profiles = (
        SandboxProfile(name="network", network_enabled=True, allowed_networks=("asset",)),
        SandboxProfile(name="filesystem", filesystem_writable=True),
        SandboxProfile(name="secret", secret_references=("ref",)),
        SandboxProfile(name="timeout"),
    )
    for profile in profiles:
        with pytest.raises(SandboxExecutionError):
            await runtime.execute(profile, operation)

    class BadIdentityProvider(MemorySandboxProvider):
        async def execute(self, execution_id, profile, callback, secrets=None):
            result = await super().execute(execution_id, profile, callback, secrets=secrets)
            return result.model_copy(update={"execution_id": uuid4()})

    bad_runtime = SandboxRuntime(BadIdentityProvider(), SandboxPolicyEngine())
    with pytest.raises(SandboxExecutionError, match="identity"):
        await bad_runtime.execute(SandboxProfile(name="bad-identity"), operation)

    provider = MemorySandboxProvider()
    active_id = uuid4()
    provider._active.add(active_id)
    assert await provider.terminate(active_id) is True
    assert await provider.terminate(uuid4()) is False
    assert await runtime.health() is True


async def test_worker_and_lease_negative_repository_paths() -> None:
    async with TestSessionFactory() as session:
        registry, worker, leases, _ = await _stack(session)
        existing = await registry.register(
            WorkerRecord(
                name=worker.name,
                runtime_version="ignored",
                capabilities=frozenset({"ignored"}),
            )
        )
        assert existing.id == worker.id
        with pytest.raises(WorkerNotFound):
            await registry.require(uuid4())
        with pytest.raises(WorkerConflict):
            await registry.heartbeat(
                WorkerHeartbeat(
                    worker_id=worker.id,
                    status=WorkerStatus.BUSY,
                    active_executions=worker.max_concurrency + 1,
                )
            )
        assert (
            await WorkerRepository(session).update_state(
                worker_id=worker.id,
                expected_version=999,
                status=WorkerStatus.ONLINE.value,
                active_executions=0,
                observed_at=datetime.now(UTC),
            )
            is None
        )
        with pytest.raises(WorkerLeaseConflict, match="positive"):
            await leases.acquire(
                worker_id=worker.id,
                execution_id=uuid4(),
                owner="owner",
                ttl_seconds=0,
            )
        with pytest.raises(WorkerLeaseNotFound):
            await leases.require(uuid4())
        execution_id = uuid4()
        lease = await leases.acquire(
            worker_id=worker.id,
            execution_id=execution_id,
            owner="owner",
            ttl_seconds=60,
        )
        with pytest.raises(WorkerLeaseConflict, match="active"):
            await leases.acquire(
                worker_id=worker.id,
                execution_id=execution_id,
                owner="other",
                ttl_seconds=60,
            )
        await leases.release(
            lease.id,
            owner="owner",
            fencing_token=lease.fencing_token,
            expected_version=lease.version,
        )
        with pytest.raises(WorkerLeaseConflict, match="reused"):
            await leases.acquire(
                worker_id=worker.id,
                execution_id=execution_id,
                owner="owner",
                ttl_seconds=60,
            )
        expiring = await leases.acquire(
            worker_id=worker.id,
            execution_id=uuid4(),
            owner="owner",
            ttl_seconds=1,
            now=datetime.now(UTC) - timedelta(seconds=2),
        )
        expired = await leases.expire(now=datetime.now(UTC))
        assert [item.id for item in expired] == [expiring.id]
        assert await leases.expire(now=datetime.now(UTC)) == ()


def test_all_eight_phase_manifests_remain_v1_compatible() -> None:
    assert len(PLUGIN_MANIFESTS) == 8
    for manifest_path in PLUGIN_MANIFESTS:
        manifest = load_plugin_manifest(manifest_path.read_text(encoding="utf-8"))
        assert isinstance(manifest, PluginManifestV1), manifest_path
        assert manifest.schema_version == "v1"


def test_manifest_v1_compatible_and_v2_forbids_unknown_fields() -> None:
    base = {
        "name": "synthetic",
        "version": "1.0.0",
        "entrypoint": "plugin:main",
        "runtime_version": "phase-18.1",
        "capabilities": ["plugin.execute"],
        "sandbox": {"name": "default"},
        "worker": {"runtime_version": "phase-18.1"},
    }
    v1 = PluginManifestV1.model_validate({**base, "legacy_field": True})
    assert v1.schema_version == "v1"
    v2 = PluginManifestV2.model_validate({**base, "schema_version": "v2"})
    assert v2.provider_requirements.timeout is True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PluginManifestV2.model_validate({**base, "schema_version": "v2", "unknown_field": True})


def test_phase_18_1_migration_is_single_head_and_worker_scoped() -> None:
    alembic_config = Config(str(PROJECT_ROOT / "backend/alembic.ini"))
    script = ScriptDirectory.from_config(alembic_config)
    # Phase 28.3/28.5 appends the Acquisition durable-runtime migration; the
    # chain stays single-head.
    assert script.get_heads() == ["20260812_0021"]

    revision = script.get_revision("20260802_0017")
    assert revision is not None
    assert revision.down_revision == "20260802_0016"

    migration_path = PROJECT_ROOT / "backend/alembic/versions/20260802_0017_worker_consistency.py"
    migration = migration_path.read_text(encoding="utf-8")
    for table in ("workers", "worker_leases", "sandbox_executions"):
        assert f'"{table}"' in migration
    for forbidden_table in (
        "assessment_tasks",
        "findings",
        "detection",
        "responses",
        "incidents",
    ):
        assert forbidden_table not in migration.lower()


async def test_worker_lease_sandbox_and_secret_audit_coverage() -> None:
    async with TestSessionFactory() as session:
        _, _, _, runtime = await _stack(session)

        async def operation() -> dict[str, bool]:
            return {"ok": True}

        await runtime.execute(
            PluginExecutionRequest(
                plugin_name="synthetic",
                plugin_version="1.0.0",
                capability="plugin.execute",
                sandbox_profile=SandboxProfile(name="default"),
            ),
            operation,
            owner="workflow",
        )
        actions = set(await session.scalars(select(AuditLog.action)))
        assert {
            EventType.WORKER_REGISTERED.value,
            EventType.WORKER_STATE_CHANGED.value,
            EventType.WORKER_LEASE_ACQUIRED.value,
            EventType.WORKER_LEASE_RELEASED.value,
            EventType.SANDBOX_EXECUTION_STARTED.value,
            EventType.SANDBOX_EXECUTION_COMPLETED.value,
        } <= actions
