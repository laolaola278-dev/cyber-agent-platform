"""Phase 12 Telemetry and Stream Framework acceptance tests."""

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.events import InMemoryEventBus
from app.events.contracts import EventType
from app.exceptions import (
    AssetNotFound,
    TaskNotFound,
    TelemetryConflict,
    TelemetryExecutionError,
    TelemetryNotFound,
    TelemetryPolicyViolation,
    TelemetryValidationError,
)
from app.models import (
    Asset,
    AuditLog,
    SecurityEvent,
    Task,
    TelemetryCheckpoint,
    TelemetryPipeline,
    TelemetryRuntimeState,
    TelemetryTask,
)
from app.repositories import TaskRepository
from app.repositories.telemetry import SQLAlchemyCheckpointProvider, TelemetryRepository
from app.schemas import TaskCreate
from app.schemas.telemetry import (
    BackpressureAction,
    TelemetryPolicy,
    TelemetryRecord,
    TelemetryReplayRequest,
    TelemetryTaskCreate,
)
from app.services.task import TaskService
from app.telemetry import (
    BoundedTelemetryQueue,
    Checkpoint,
    FakeTelemetryPlugin,
    MemoryCheckpointProvider,
    StreamRuntime,
    TelemetryPlanner,
    TelemetryRegistry,
    TelemetryRuntime,
    execute_with_backpressure,
)
from app.telemetry.service import TelemetryService
from tests.conftest import TestSessionFactory


def record(offset: int, *, seconds: int = 0) -> TelemetryRecord:
    payload = {"message": f"event-{offset}"}
    return TelemetryRecord(
        source="synthetic",
        timestamp=datetime.now(UTC) + timedelta(seconds=seconds),
        stream="synthetic",
        offset=offset,
        sequence=offset,
        payload=payload,
        metadata={},
        checksum=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
    )


@pytest.mark.asyncio
async def test_registry_planner_lifecycle_and_record_contract() -> None:
    registry = TelemetryRegistry()
    plugin = FakeTelemetryPlugin()
    registry.register(plugin)
    policy = TelemetryPolicy(max_records=10)
    planner = TelemetryPlanner(registry)

    plan, context = planner.plan(
        telemetry_task_id=uuid4(),
        task_id=uuid4(),
        trace_id="phase12",
        plugin_name=plugin.name,
        stream="synthetic",
        partition="0",
        consumer="tests",
        policy=policy,
        input_data=({"message": "one", "offset": 7},),
    )
    result = await TelemetryRuntime(registry).execute(plan, context)
    assert plan.steps == ["receiver", "parser", "transformer", "telemetry-record", "publisher"]
    assert result.published_count == 1
    assert result.records[0].offset == 7
    assert (
        result.records[0].checksum
        == hashlib.sha256(json.dumps({"message": "one"}, sort_keys=True).encode()).hexdigest()
    )
    with pytest.raises(TelemetryValidationError):
        await plugin.receive(context)

    class ForbiddenPlugin(FakeTelemetryPlugin):
        name = "forbidden"
        permissions = frozenset({"database.access"})

    with pytest.raises(TelemetryPolicyViolation):
        registry.register(ForbiddenPlugin())
    with pytest.raises(TelemetryConflict):
        registry.register(FakeTelemetryPlugin())
    with pytest.raises(TelemetryPolicyViolation, match="not allowlisted"):
        planner.plan(
            telemetry_task_id=uuid4(),
            task_id=uuid4(),
            trace_id="phase12-plugin-policy",
            plugin_name=plugin.name,
            stream="synthetic",
            partition="0",
            consumer="tests",
            policy=TelemetryPolicy(allowed_plugins=["other"]),
            input_data=(),
        )
    with pytest.raises(TelemetryPolicyViolation, match="stream is not allowlisted"):
        planner.plan(
            telemetry_task_id=uuid4(),
            task_id=uuid4(),
            trace_id="phase12-stream-policy",
            plugin_name=plugin.name,
            stream="forbidden",
            partition="0",
            consumer="tests",
            policy=TelemetryPolicy(),
            input_data=(),
        )
    await plugin.initialize(context)
    with pytest.raises(TelemetryValidationError, match="envelope must be an object"):
        await plugin.parse(["invalid"], context)
    await plugin.shutdown()
    with pytest.raises(TelemetryValidationError, match="permissions do not match"):
        await plugin.initialize(replace(context, granted_permissions=frozenset()))

    class NoCapabilityPlugin(FakeTelemetryPlugin):
        name = "no-capability"
        capabilities = frozenset()

    with pytest.raises(TelemetryPolicyViolation, match="declare capabilities"):
        registry.register(NoCapabilityPlugin())
    with pytest.raises(TelemetryNotFound):
        registry.require("missing")


@pytest.mark.asyncio
async def test_stream_batch_window_ack_replay_and_checkpoint_monotonicity() -> None:
    provider = MemoryCheckpointProvider()
    runtime = StreamRuntime(provider)
    records = [record(0), record(1, seconds=10), record(2, seconds=70)]
    batches = runtime.batch(records, batch_size=2)
    windows = runtime.window(records, seconds=60)
    assert [(item.first_offset, item.last_offset) for item in batches] == [(0, 1), (2, 2)]
    assert [len(item) for item in windows] == [2, 1]
    committed = await runtime.ack(records[1], partition="a", consumer="tests")
    replayed = await runtime.replay(records, from_offset=1, to_offset=2)
    assert committed.offset == 1
    assert [item.offset for item in replayed] == [1, 2]
    assert (await provider.get("synthetic", "a", "tests")).offset == 1
    with pytest.raises(TelemetryConflict):
        await provider.commit(Checkpoint("memory", "synthetic", "a", "tests", 0, 0))


@pytest.mark.asyncio
async def test_backpressure_drop_reject_pause_and_retry_decisions() -> None:
    drop = BoundedTelemetryQueue[int](
        TelemetryPolicy(queue_capacity=1, backpressure_action=BackpressureAction.DROP)
    )
    await drop.put(1)
    assert (await drop.put(2)).decision.value == "DROP"

    reject = BoundedTelemetryQueue[int](
        TelemetryPolicy(queue_capacity=1, backpressure_action=BackpressureAction.REJECT)
    )
    await reject.put(1)
    with pytest.raises(TelemetryPolicyViolation):
        await reject.put(2)

    pause = BoundedTelemetryQueue[int](
        TelemetryPolicy(
            queue_capacity=1,
            backpressure_action=BackpressureAction.PAUSE,
            pause_seconds=0,
        )
    )
    await pause.put(1)
    with pytest.raises(TelemetryPolicyViolation):
        await pause.put(2)

    retry = BoundedTelemetryQueue[int](
        TelemetryPolicy(queue_capacity=1, backpressure_action=BackpressureAction.RETRY)
    )
    await retry.put(1)
    assert (await retry.put(2)).decision.value == "RETRY"

    pause_success = BoundedTelemetryQueue[int](
        TelemetryPolicy(
            queue_capacity=1,
            backpressure_action=BackpressureAction.PAUSE,
            pause_seconds=0.01,
        )
    )
    await pause_success.put(1)

    async def release_queue() -> None:
        await asyncio.sleep(0)
        assert await pause_success.get() == 1
        pause_success.task_done()

    release_task = asyncio.create_task(release_queue())
    assert (await pause_success.put(2)).decision.value == "PAUSE"
    assert await pause_success.get() == 2
    pause_success.task_done()
    await pause_success.join()
    await release_task

    async def successful_operation() -> int:
        return 42

    value, decision = await execute_with_backpressure(
        successful_operation,
        TelemetryPolicy(backpressure_action=BackpressureAction.DROP),
    )
    assert value == 42
    assert decision.decision.value == "ACCEPT"


@pytest.mark.asyncio
async def test_bounded_retry_exhaustion_and_runtime_integrity_fail_closed() -> None:
    attempts = 0

    async def failing_operation() -> int:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("synthetic failure")

    policy = TelemetryPolicy(
        backpressure_action=BackpressureAction.RETRY,
        retry_attempts=2,
        pause_seconds=0,
    )
    with pytest.raises(TelemetryExecutionError, match="retries exhausted"):
        await execute_with_backpressure(failing_operation, policy)
    assert attempts == 3

    class CorruptingPlugin(FakeTelemetryPlugin):
        name = "corrupting-telemetry"

        async def transform(self, envelopes, context):
            records = await super().transform(envelopes, context)
            return [records[0].model_copy(update={"checksum": "0" * 64})]

    registry = TelemetryRegistry()
    plugin = CorruptingPlugin()
    registry.register(plugin)
    planner = TelemetryPlanner(registry)

    plan, context = planner.plan(
        telemetry_task_id=uuid4(),
        task_id=uuid4(),
        trace_id="phase12-corrupt",
        plugin_name=plugin.name,
        stream="synthetic",
        partition="0",
        consumer="tests",
        policy=TelemetryPolicy(allowed_plugins=[plugin.name]),
        input_data=({"message": "tampered"},),
    )
    with pytest.raises(TelemetryExecutionError, match="checksum mismatch"):
        await TelemetryRuntime(registry).execute(plan, context)
    assert plugin._initialized is False

    with pytest.raises(TelemetryPolicyViolation, match="permissions do not match"):
        await TelemetryRuntime(registry).execute(
            plan,
            replace(context, granted_permissions=frozenset()),
        )

    class TimeoutPlugin(FakeTelemetryPlugin):
        name = "timeout-telemetry"

        async def receive(self, context):
            await asyncio.sleep(0.01)
            return await super().receive(context)

    timeout_registry = TelemetryRegistry()
    timeout_plugin = TimeoutPlugin()
    timeout_registry.register(timeout_plugin)
    timeout_plan, timeout_context = TelemetryPlanner(timeout_registry).plan(
        telemetry_task_id=uuid4(),
        task_id=uuid4(),
        trace_id="phase12-timeout",
        plugin_name=timeout_plugin.name,
        stream="synthetic",
        partition="0",
        consumer="tests",
        policy=TelemetryPolicy(allowed_plugins=[timeout_plugin.name], timeout_seconds=1),
        input_data=({"message": "slow"},),
    )
    timeout_context.policy.timeout_seconds = 0.001
    with pytest.raises(TelemetryExecutionError, match="timed out"):
        await TelemetryRuntime(timeout_registry).execute(timeout_plan, timeout_context)

    class WrongIdentityPlugin(FakeTelemetryPlugin):
        name = "wrong-identity-telemetry"

        async def publish(self, records, context):
            result = await super().publish(records, context)
            return result.model_copy(update={"plugin_name": "unregistered-identity"})

    identity_registry = TelemetryRegistry()
    identity_plugin = WrongIdentityPlugin()
    identity_registry.register(identity_plugin)
    identity_plan, identity_context = TelemetryPlanner(identity_registry).plan(
        telemetry_task_id=uuid4(),
        task_id=uuid4(),
        trace_id="phase12-identity",
        plugin_name=identity_plugin.name,
        stream="synthetic",
        partition="0",
        consumer="tests",
        policy=TelemetryPolicy(allowed_plugins=[identity_plugin.name]),
        input_data=({"message": "identity"},),
    )
    with pytest.raises(TelemetryExecutionError, match="identity does not match"):
        await TelemetryRuntime(identity_registry).execute(identity_plan, identity_context)

    class WrongCountPlugin(FakeTelemetryPlugin):
        name = "wrong-count-telemetry"

        async def publish(self, records, context):
            result = await super().publish(records, context)
            return result.model_copy(update={"published_count": len(records) + 1})

    count_registry = TelemetryRegistry()
    count_plugin = WrongCountPlugin()
    count_registry.register(count_plugin)
    count_plan, count_context = TelemetryPlanner(count_registry).plan(
        telemetry_task_id=uuid4(),
        task_id=uuid4(),
        trace_id="phase12-count",
        plugin_name=count_plugin.name,
        stream="synthetic",
        partition="0",
        consumer="tests",
        policy=TelemetryPolicy(allowed_plugins=[count_plugin.name]),
        input_data=({"message": "count"},),
    )
    with pytest.raises(TelemetryExecutionError, match="publish count is inconsistent"):
        await TelemetryRuntime(count_registry).execute(count_plan, count_context)


@pytest.mark.asyncio
async def test_runtime_rejects_oversized_records_and_invalid_replay_range() -> None:
    registry = TelemetryRegistry()
    plugin = FakeTelemetryPlugin()
    registry.register(plugin)
    planner = TelemetryPlanner(registry)

    plan, context = planner.plan(
        telemetry_task_id=uuid4(),
        task_id=uuid4(),
        trace_id="phase12-size",
        plugin_name=plugin.name,
        stream="synthetic",
        partition="0",
        consumer="tests",
        policy=TelemetryPolicy(max_record_size_bytes=32),
        input_data=({"message": "x" * 128},),
    )
    with pytest.raises(TelemetryPolicyViolation, match="oversized"):
        await TelemetryRuntime(registry).execute(plan, context)
    assert plugin._initialized is False

    response = TelemetryReplayRequest(
        stream="synthetic",
        from_offset=4,
        to_offset=5,
        window_seconds=30,
    )
    assert response.from_offset == 4
    with pytest.raises(ValueError, match="greater than or equal"):
        TelemetryReplayRequest(stream="synthetic", from_offset=5, to_offset=4)


@pytest.mark.asyncio
async def test_service_backpressure_audit_and_record_limit() -> None:
    async with TestSessionFactory() as session:
        registry = TelemetryRegistry()
        registry.register(FakeTelemetryPlugin())
        policy = TelemetryPolicy(
            queue_capacity=1,
            backpressure_action=BackpressureAction.DROP,
            max_records=1,
        )
        bus = InMemoryEventBus()
        events = []

        async def capture(event):
            events.append(event)

        bus.subscribe(EventType.TELEMETRY_BACKPRESSURE_APPLIED, capture)
        service = TelemetryService(
            session,
            TelemetryRepository(session),
            TelemetryPlanner(registry),
            TelemetryRuntime(registry),
            bus,
            policy,
        )
        accepted = await service.apply_backpressure(
            [{"offset": 0}, {"offset": 1}],
            policy,
            telemetry_task_id=None,
            task_id=None,
            trace_id="phase12-backpressure",
        )
        assert accepted == [{"offset": 0}]
        assert events[0].payload["decision"] == "DROP"
        with pytest.raises(TelemetryPolicyViolation, match="maximum record count"):
            await service.create(
                TelemetryTaskCreate(name="too many", records=[{}, {}]),
                trace_id="phase12-limit",
            )


@pytest.mark.asyncio
async def test_service_failure_state_and_bounded_retry_audit() -> None:
    async with TestSessionFactory() as session:
        registry = TelemetryRegistry()
        registry.register(FakeTelemetryPlugin())
        bus = InMemoryEventBus()
        service = TelemetryService(
            session,
            TelemetryRepository(session),
            TelemetryPlanner(registry),
            TelemetryRuntime(registry),
            bus,
            TelemetryPolicy(),
        )
        payload = TelemetryTaskCreate(name="failing telemetry", records=[])
        pipeline = TelemetryPipeline(
            name="failure:test",
            version="1.0.0",
            receivers=[],
            processors=[],
            exporters=[],
            configuration={},
        )
        task = Task(
            name="failing telemetry",
            task_type="telemetry",
            status="CREATED",
            input={},
            required_permissions=[],
            required_capabilities=[],
        )
        session.add_all([pipeline, task])
        await session.flush()
        telemetry = TelemetryTask(
            task_id=task.id,
            pipeline_id=pipeline.id,
            plugin_name="missing-plugin",
            status="PLANNED",
            stream="synthetic",
            partition="0",
            consumer="tests",
            policy={},
            plan={},
            result_summary={},
        )
        session.add(telemetry)
        await session.flush()
        payload.plugin_name = "missing-plugin"
        with pytest.raises(TelemetryNotFound):
            await service.execute(
                telemetry,
                task,
                payload,
                policy=TelemetryPolicy(allowed_plugins=["missing-plugin"]),
                trace_id="phase12-failure",
            )
        assert telemetry.status == "FAILED"
        assert telemetry.finished_at is not None
        state = (await TelemetryRepository(session).list_runtime_states())[0]
        assert state.status == "FAILED"

        retry_policy = TelemetryPolicy(
            queue_capacity=1,
            backpressure_action=BackpressureAction.RETRY,
            retry_attempts=0,
            pause_seconds=0,
        )
        with pytest.raises(TelemetryPolicyViolation, match="bounded retries"):
            await service.apply_backpressure(
                [1, 2],
                retry_policy,
                telemetry_task_id=None,
                task_id=None,
                trace_id="phase12-retry-audit",
            )


@pytest.mark.asyncio
async def test_sqlalchemy_checkpoint_provider_and_models() -> None:
    async with TestSessionFactory() as session:
        provider = SQLAlchemyCheckpointProvider(session)
        current = await provider.commit(Checkpoint("database", "synthetic", "0", "tests", 3, 3))
        assert current.offset == 3
        assert len(await provider.list()) == 1
        updated = await provider.commit(Checkpoint("database", "synthetic", "0", "tests", 4, 4))
        assert updated.offset == 4
        persisted = await provider.get("synthetic", "0", "tests")
        assert persisted is not None
        assert persisted.offset == 4
        assert await provider.get("missing", "0", "tests") is None
        with pytest.raises(TelemetryConflict):
            await provider.commit(Checkpoint("database", "synthetic", "0", "tests", 2, 2))
        assert {
            TelemetryTask.__tablename__,
            TelemetryCheckpoint.__tablename__,
            TelemetryPipeline.__tablename__,
            TelemetryRuntimeState.__tablename__,
        } == {
            "telemetry_tasks",
            "telemetry_checkpoints",
            "telemetry_pipelines",
            "telemetry_runtime_states",
        }


@pytest.mark.asyncio
async def test_telemetry_api_audit_checkpoint_replay_and_domain_boundary(client) -> None:
    response = await client.post(
        "/telemetry/tasks",
        json={
            "name": "Synthetic framework validation",
            "records": [
                {"message": "first", "offset": 0},
                {"message": "second", "offset": 1},
            ],
            "execute": True,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["result_summary"] == {
        "received": 2,
        "published": 2,
        "records": 2,
        "security_events_created": 0,
    }
    tasks = await client.get("/telemetry/tasks")
    runtime = await client.get("/telemetry/runtime")
    checkpoints = await client.get("/telemetry/checkpoints")
    replay = await client.post(
        "/telemetry/replay",
        json={"stream": "synthetic", "from_offset": 1},
    )
    assert tasks.json()["total"] == 1
    assert runtime.json()["plugin_count"] == 2
    assert checkpoints.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["checkpoint_unchanged"] is True

    rejected = await client.post(
        "/telemetry/tasks",
        json={"name": "bad", "path": "C:/Windows/System32", "records": []},
    )
    assert rejected.status_code == 422
    async with TestSessionFactory() as session:
        assert await session.scalar(select(func.count()).select_from(SecurityEvent)) == 0
        actions = set(await session.scalars(select(AuditLog.action)))
        assert EventType.TELEMETRY_TASK_CREATED.value in actions
        assert EventType.TELEMETRY_EXECUTION_COMPLETED.value in actions
        assert EventType.TELEMETRY_CHECKPOINT_COMMITTED.value in actions


@pytest.mark.asyncio
async def test_task_service_existing_coverage_paths_and_asset_boundary() -> None:
    class StubPublisher:
        def __init__(self) -> None:
            self.events = []

        async def publish(self, event) -> None:
            self.events.append(event)

    class StubDispatcher:
        async def dispatch(self, task, *, trace_id):
            return type("Execution", (), {"result": None, "status": "SUCCESS"})()

        async def execute(self, execution, task, *, trace_id):
            execution.result = {"capability": task.required_capabilities[0]}
            return execution

    async with TestSessionFactory() as session:
        publisher = StubPublisher()
        service = TaskService(
            session,
            TaskRepository(session),
            publisher,
            StubDispatcher(),
        )
        missing_id = uuid4()
        with pytest.raises(TaskNotFound, match="not found"):
            await service.get_task(missing_id)
        with pytest.raises(RuntimeError, match="RuntimeService is required"):
            await service.create_data_acquisition_task("https://example.com")
        with pytest.raises(AssetNotFound, match="not found"):
            await service.execute_capability(
                "telemetry.receive",
                {"message": "test"},
                trace_id="phase12-task-asset",
                asset_id=missing_id,
            )

        asset = Asset(
            asset_type="HOST",
            name="authorized telemetry host",
            value="telemetry.example",
            canonical_value="telemetry.example",
        )
        session.add(asset)
        await session.flush()
        result = await service.execute_capability(
            "telemetry.receive",
            {"message": "test"},
            trace_id="phase12-task-capability",
            asset_id=asset.id,
        )
        assert result == {"capability": "telemetry.receive"}
        assert publisher.events[-1].payload["capability"] == "telemetry.receive"

        created = await service.create_task(
            TaskCreate(name="non acquisition", task_type="telemetry-validation"),
            trace_id="phase12-task-create",
        )
        assert created.status == "CREATED"
        assert (await service.list_tasks()).total >= 2
