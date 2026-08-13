"""Telemetry control-plane service."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import TaskStatus
from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import TelemetryPolicyViolation
from app.models import Task, TelemetryPipeline, TelemetryTask
from app.repositories.pagination import PageResult
from app.repositories.telemetry import TelemetryRepository
from app.schemas.telemetry import (
    TelemetryPolicy,
    TelemetryReplayRead,
    TelemetryReplayRequest,
    TelemetryRuntimeRead,
    TelemetryTaskCreate,
)
from app.telemetry.backpressure import BoundedTelemetryQueue
from app.telemetry.checkpoint import Checkpoint, CheckpointProvider, MemoryCheckpointProvider
from app.telemetry.planner import TelemetryPlanner
from app.telemetry.runtime import TelemetryRuntime
from app.telemetry.stream import MemoryTelemetryJournal, StreamRuntime, TelemetryJournal


class TelemetryService:
    """Own telemetry task persistence, audit and stream coordination."""

    def __init__(
        self,
        session: AsyncSession,
        repository: TelemetryRepository,
        planner: TelemetryPlanner,
        runtime: TelemetryRuntime,
        publisher: EventPublisher,
        default_policy: TelemetryPolicy,
        checkpoint_provider: CheckpointProvider | None = None,
        journal: TelemetryJournal | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._planner = planner
        self._runtime = runtime
        self._publisher = publisher
        self._default_policy = default_policy
        self._checkpoints = checkpoint_provider or MemoryCheckpointProvider()
        self._stream = StreamRuntime(self._checkpoints)
        self._journal = journal or MemoryTelemetryJournal()

    async def create(self, payload: TelemetryTaskCreate, *, trace_id: str) -> TelemetryTask:
        policy = payload.policy or self._default_policy
        if len(payload.records) > policy.max_records:
            raise TelemetryPolicyViolation(
                "Telemetry task exceeded maximum record count before execution"
            )
        pipeline = TelemetryPipeline(
            name=f"{payload.plugin_name}:{payload.stream}",
            version="1.0.0",
            receivers=[payload.plugin_name],
            processors=["parse", "transform", "batch", "window"],
            exporters=["telemetry-record"],
            configuration={"broker": None, "source": "synthetic"},
        )
        self._session.add(pipeline)
        await self._session.flush()
        task = Task(
            name=payload.name,
            task_type="telemetry",
            status=TaskStatus.CREATED.value,
            input={"records": payload.records},
            required_permissions=["telemetry.receive", "telemetry.publish"],
            required_capabilities=["telemetry.receive"],
        )
        self._session.add(task)
        await self._session.flush()
        telemetry = TelemetryTask(
            task_id=task.id,
            pipeline_id=pipeline.id,
            plugin_name=payload.plugin_name,
            status="PLANNED",
            stream=payload.stream,
            partition=payload.partition,
            consumer=payload.consumer,
            policy=policy.model_dump(mode="json"),
        )
        self._session.add(telemetry)
        await self._session.flush()
        plan, _ = self._planner.plan(
            telemetry_task_id=telemetry.id,
            task_id=task.id,
            trace_id=trace_id,
            plugin_name=payload.plugin_name,
            stream=payload.stream,
            partition=payload.partition,
            consumer=payload.consumer,
            policy=policy,
            input_data=tuple(payload.records),
        )
        telemetry.plan = plan.model_dump(mode="json")
        await self._publish(
            EventType.TELEMETRY_TASK_CREATED,
            telemetry.id,
            task.id,
            trace_id,
            {"stream": payload.stream},
        )
        if payload.execute:
            await self.execute(telemetry, task, payload, policy=policy, trace_id=trace_id)
        await self._session.commit()
        await self._session.refresh(telemetry)
        return telemetry

    async def execute(
        self,
        telemetry: TelemetryTask,
        task: Task,
        payload: TelemetryTaskCreate,
        *,
        policy: TelemetryPolicy,
        trace_id: str,
    ) -> None:
        telemetry.status = "RUNNING"
        telemetry.started_at = datetime.now(UTC)
        await self._repository.upsert_runtime_state(
            worker_id=f"telemetry-{payload.consumer}-{payload.partition}",
            pipeline_id=telemetry.pipeline_id,
            status="RUNNING",
            stream=payload.stream,
            partition=payload.partition,
            consumer=payload.consumer,
            current_offset=None,
            queue_depth=len(payload.records),
            backpressure_action=None,
            metadata={"plugin": payload.plugin_name},
        )
        await self._publish(
            EventType.TELEMETRY_EXECUTION_STARTED, telemetry.id, task.id, trace_id, {}
        )
        try:
            bounded_records = await self.apply_backpressure(
                payload.records,
                policy,
                telemetry_task_id=telemetry.id,
                task_id=task.id,
                trace_id=trace_id,
            )
            plan, context = self._planner.plan(
                telemetry_task_id=telemetry.id,
                task_id=task.id,
                trace_id=trace_id,
                plugin_name=payload.plugin_name,
                stream=payload.stream,
                partition=payload.partition,
                consumer=payload.consumer,
                policy=policy,
                input_data=tuple(bounded_records),
            )
            result = await self._runtime.execute(plan, context)
            self._journal.append(payload.stream, payload.partition, list(result.records))
            if result.records:
                await self._stream.ack(
                    result.records[-1],
                    partition=payload.partition,
                    consumer=payload.consumer,
                )
                await self._publish(
                    EventType.TELEMETRY_CHECKPOINT_COMMITTED,
                    telemetry.id,
                    task.id,
                    trace_id,
                    {"offset": result.records[-1].offset},
                )
            telemetry.status = "SUCCESS"
            task.status = TaskStatus.SUCCESS.value
            await self._repository.upsert_runtime_state(
                worker_id=f"telemetry-{payload.consumer}-{payload.partition}",
                pipeline_id=telemetry.pipeline_id,
                status="IDLE",
                stream=payload.stream,
                partition=payload.partition,
                consumer=payload.consumer,
                current_offset=result.records[-1].offset if result.records else None,
                queue_depth=0,
                backpressure_action=None,
                metadata={"plugin": payload.plugin_name, "last_result": "SUCCESS"},
            )
            telemetry.result_summary = {
                "received": result.received_count,
                "published": result.published_count,
                "records": len(result.records),
                "security_events_created": 0,
            }
            await self._publish(
                EventType.TELEMETRY_EXECUTION_COMPLETED,
                telemetry.id,
                task.id,
                trace_id,
                telemetry.result_summary,
            )
        except Exception as error:
            telemetry.status = "FAILED"
            task.status = TaskStatus.FAILED.value
            telemetry.error = str(error)
            await self._repository.upsert_runtime_state(
                worker_id=f"telemetry-{payload.consumer}-{payload.partition}",
                pipeline_id=telemetry.pipeline_id,
                status="FAILED",
                stream=payload.stream,
                partition=payload.partition,
                consumer=payload.consumer,
                current_offset=None,
                queue_depth=0,
                backpressure_action=None,
                metadata={"plugin": payload.plugin_name, "error": str(error)},
            )
            await self._publish(
                EventType.TELEMETRY_EXECUTION_FAILED,
                telemetry.id,
                task.id,
                trace_id,
                {},
                error=str(error),
            )
            raise
        finally:
            telemetry.finished_at = datetime.now(UTC)

    async def apply_backpressure(
        self,
        items: list[Any],
        policy: TelemetryPolicy,
        *,
        telemetry_task_id: UUID | None,
        task_id: UUID | None,
        trace_id: str,
    ) -> list[Any]:
        """Apply bounded queue policy and audit every non-accept decision."""

        queue = BoundedTelemetryQueue[Any](policy)
        accepted: list[Any] = []
        for item in items:
            result = await queue.put(item)
            if result.decision.value != "ACCEPT":
                await self._publish(
                    EventType.TELEMETRY_BACKPRESSURE_APPLIED,
                    telemetry_task_id,
                    task_id,
                    trace_id,
                    {
                        "decision": result.decision.value,
                        "queue_depth": queue.depth,
                        "queue_capacity": policy.queue_capacity,
                        "attempts": result.attempts,
                    },
                )
            if result.decision.value == "DROP":
                continue
            if result.decision.value == "RETRY":
                for attempt in range(policy.retry_attempts + 1):
                    if queue.depth < policy.queue_capacity:
                        await queue.put(item)
                        break
                    if attempt >= policy.retry_attempts:
                        raise TelemetryPolicyViolation(
                            "Telemetry queue remained full after bounded retries"
                        )
                    if policy.pause_seconds:
                        await asyncio.sleep(policy.pause_seconds)
            accepted.append(item)
        return accepted

    async def list_tasks(self, *, page: int, page_size: int) -> PageResult[TelemetryTask]:
        return await self._repository.list_tasks(page=page, page_size=page_size)

    async def list_checkpoints(self) -> list[Checkpoint]:
        return await self._checkpoints.list()

    async def runtime_status(self) -> TelemetryRuntimeRead:
        workers = await self._repository.list_runtime_states()
        return TelemetryRuntimeRead(
            workers=workers,
            queue_capacity=self._default_policy.queue_capacity,
            checkpoint_provider=self._checkpoints.name,
            plugin_count=len(self._planner.registry.plugins),
            capabilities=sorted(
                {
                    capability
                    for plugin in self._planner.registry.plugins
                    for capability in plugin.capabilities
                }
            ),
        )

    async def replay(
        self, payload: TelemetryReplayRequest, *, trace_id: str
    ) -> TelemetryReplayRead:
        records = await self._stream.replay(
            self._journal.read(payload.stream, payload.partition),
            from_offset=payload.from_offset,
            to_offset=payload.to_offset,
            window_seconds=payload.window_seconds,
        )
        checkpoint = await self._checkpoints.get(
            payload.stream, payload.partition, payload.consumer
        )
        start = (
            payload.from_offset
            if payload.from_offset is not None
            else (checkpoint.offset if checkpoint else 0)
        )
        await self._publish(
            EventType.TELEMETRY_REPLAY_REQUESTED,
            None,
            None,
            trace_id,
            {"stream": payload.stream, "records": len(records)},
        )
        return TelemetryReplayRead(
            stream=payload.stream,
            partition=payload.partition,
            consumer=payload.consumer,
            from_offset=start,
            to_offset=payload.to_offset,
            window_seconds=payload.window_seconds,
            records=records,
        )

    async def _publish(
        self,
        event_type: EventType,
        aggregate_id: UUID | None,
        task_id: UUID | None,
        trace_id: str,
        payload: dict[str, object],
        *,
        error: str | None = None,
    ) -> None:
        await self._publisher.publish(
            PlatformEvent(
                type=event_type,
                aggregate_id=aggregate_id,
                task_id=task_id,
                trace_id=trace_id,
                resource="telemetry",
                payload=payload,
                error=error,
            )
        )
