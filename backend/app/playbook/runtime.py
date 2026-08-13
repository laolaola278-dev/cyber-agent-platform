"""Durable sequential Playbook runtime with retry, timeout, and compensation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.events import EventPublisher, EventType, PlatformEvent
from app.models.playbook import PlaybookExecution, PlaybookStepExecution
from app.playbook.contracts import (
    PlaybookApproval,
    PlaybookExecutionStatus,
    PlaybookStepStatus,
)
from app.playbook.executor import PlaybookExecutor
from app.playbook.planner import PlaybookPlan
from app.playbook.policy import PlaybookPolicy
from app.repositories.playbook import PlaybookExecutionRepository


class _PlaybookStepTimeoutError(TimeoutError):
    """Distinguish an exhausted step timeout from the Playbook deadline."""


class PlaybookRuntime:
    def __init__(
        self,
        session: AsyncSession,
        executions: PlaybookExecutionRepository,
        executor: PlaybookExecutor,
        publisher: EventPublisher,
        policy: PlaybookPolicy,
    ) -> None:
        self._session = session
        self._executions = executions
        self._executor = executor
        self._publisher = publisher
        self._policy = policy

    async def execute(
        self,
        execution: PlaybookExecution,
        plan: PlaybookPlan,
        *,
        approvals: dict[str, PlaybookApproval],
    ) -> PlaybookExecution:
        is_resume = execution.status == PlaybookExecutionStatus.WAITING_APPROVAL.value
        if is_resume:
            context: dict[str, Any] = dict(execution.context or {})
            context.setdefault("input", execution.input)
            context.setdefault("steps", {})
            context.setdefault("execution_id", str(execution.id))
            for persisted in await self._executions.list_steps(execution.id):
                if persisted.output is not None:
                    context["steps"][persisted.step_id] = persisted.output
        else:
            context = {
                **execution.input,
                "input": execution.input,
                "steps": {},
                "execution_id": str(execution.id),
            }
            execution.started_at = datetime.now(UTC)
        execution.status = PlaybookExecutionStatus.RUNNING.value
        execution.context = context
        await self._checkpoint(
            execution,
            EventType.PLAYBOOK_EXECUTION_STARTED,
            {"playbook_id": str(execution.playbook_id)},
        )
        completed: list[tuple[PlaybookStepExecution, object]] = []
        persisted_steps = {
            item.step_id: item for item in await self._executions.list_steps(execution.id)
        }
        timeout_seconds = self._remaining_timeout(execution, plan.document.timeout_seconds)
        try:
            async with asyncio.timeout(timeout_seconds):
                for definition in plan.steps:
                    existing = persisted_steps.get(definition.id)
                    if existing is not None and existing.status in {
                        PlaybookStepStatus.SUCCEEDED.value,
                        PlaybookStepStatus.SKIPPED.value,
                    }:
                        completed.append((existing, definition))
                        continue
                    execution.current_step = definition.id
                    row = existing or PlaybookStepExecution(
                        execution_id=execution.id,
                        step_id=definition.id,
                        node_type=definition.type.value,
                        capability=definition.capability,
                        status=PlaybookStepStatus.PENDING.value,
                        max_attempts=definition.retry.max_attempts,
                        input=definition.input,
                    )
                    if existing is None:
                        await self._executions.add_step(row)
                        persisted_steps[definition.id] = row
                    await self._publish(
                        execution,
                        EventType.PLAYBOOK_STEP_STARTED,
                        {"step_id": definition.id, "node_type": definition.type.value},
                    )
                    if existing is not None:
                        self._require_step_deadline(row, definition.timeout_seconds)
                    outcome = await self._execute_with_retry(
                        row,
                        definition,
                        actor=execution.actor,
                        trace_id=execution.trace_id,
                        context=context,
                        approvals=approvals,
                    )
                    if outcome.status == "WAITING_APPROVAL":
                        row.status = PlaybookStepStatus.PENDING.value
                        row.output = outcome.output
                        row.completed_at = None
                        execution.status = PlaybookExecutionStatus.WAITING_APPROVAL.value
                        execution.context = context
                        await self._session.commit()
                        return await self._require(execution.id)
                    row.status = outcome.status
                    row.output = outcome.output
                    row.completed_at = datetime.now(UTC)
                    context["steps"][definition.id] = outcome.output
                    execution.context = context
                    completed.append((row, definition))
                    await self._publish(
                        execution,
                        EventType.PLAYBOOK_STEP_COMPLETED,
                        {"step_id": definition.id, "status": row.status},
                    )
                    await self._session.commit()
            execution.status = PlaybookExecutionStatus.SUCCEEDED.value
            execution.current_step = None
            execution.completed_at = datetime.now(UTC)
            await self._checkpoint(
                execution,
                EventType.PLAYBOOK_EXECUTION_COMPLETED,
                {"steps": len(completed)},
            )
        except _PlaybookStepTimeoutError as error:
            execution.status = PlaybookExecutionStatus.TIMED_OUT.value
            execution.error = str(error)
            await self._compensate(execution, completed)
            if execution.status != PlaybookExecutionStatus.COMPENSATION_FAILED.value:
                execution.status = PlaybookExecutionStatus.TIMED_OUT.value
            execution.completed_at = datetime.now(UTC)
            await self._checkpoint(
                execution,
                EventType.PLAYBOOK_EXECUTION_FAILED,
                {"status": execution.status},
                error=execution.error,
            )
        except TimeoutError as error:
            execution.status = PlaybookExecutionStatus.TIMED_OUT.value
            execution.error = "Playbook execution timed out"
            if execution.current_step:
                current = persisted_steps.get(execution.current_step)
                if current is not None and current.status == PlaybookStepStatus.RUNNING.value:
                    current.status = PlaybookStepStatus.TIMED_OUT.value
                    current.error = execution.error
                    current.completed_at = datetime.now(UTC)
            await self._compensate(execution, completed)
            if execution.status != PlaybookExecutionStatus.COMPENSATION_FAILED.value:
                execution.status = PlaybookExecutionStatus.TIMED_OUT.value
            execution.completed_at = datetime.now(UTC)
            await self._checkpoint(
                execution,
                EventType.PLAYBOOK_EXECUTION_FAILED,
                {"status": execution.status},
                error=str(error) or execution.error,
            )
        except Exception as error:
            execution.status = PlaybookExecutionStatus.FAILED.value
            execution.error = str(error)
            await self._compensate(execution, completed)
            execution.completed_at = datetime.now(UTC)
            await self._checkpoint(
                execution,
                EventType.PLAYBOOK_EXECUTION_FAILED,
                {"status": execution.status},
                error=str(error),
            )
        return await self._require(execution.id)

    async def _execute_with_retry(
        self,
        row: PlaybookStepExecution,
        definition: object,
        *,
        actor: str,
        trace_id: str,
        context: dict[str, Any],
        approvals: dict[str, PlaybookApproval],
    ) -> object:
        from app.playbook.contracts import PlaybookStepDefinition

        step = PlaybookStepDefinition.model_validate(definition)
        last_error: Exception | None = None
        for attempt in range(1, step.retry.max_attempts + 1):
            row.attempt = attempt
            row.status = PlaybookStepStatus.RUNNING.value
            row.started_at = row.started_at or datetime.now(UTC)
            await self._session.flush()
            try:
                try:
                    async with asyncio.timeout(step.timeout_seconds):
                        return await self._executor.execute(
                            step,
                            actor=actor,
                            trace_id=trace_id,
                            context=context,
                            approvals=approvals,
                        )
                except TimeoutError as error:
                    raise _PlaybookStepTimeoutError from error
            except TimeoutError as error:
                last_error = error
                row.error = "Playbook step timed out"
                if attempt == step.retry.max_attempts:
                    row.status = PlaybookStepStatus.TIMED_OUT.value
            except Exception as error:
                last_error = error
                row.error = str(error)
                if attempt == step.retry.max_attempts:
                    row.status = PlaybookStepStatus.FAILED.value
            if attempt < step.retry.max_attempts and step.retry.delay_seconds:
                await asyncio.sleep(step.retry.delay_seconds)
        row.completed_at = datetime.now(UTC)
        await self._publish(
            await self._require(row.execution_id),
            EventType.PLAYBOOK_STEP_FAILED,
            {"step_id": row.step_id, "status": row.status, "attempt": row.attempt},
            error=row.error,
        )
        if isinstance(last_error, TimeoutError):
            raise _PlaybookStepTimeoutError(
                f"Playbook step timed out: {row.step_id}"
            ) from last_error
        if last_error is not None:
            raise last_error
        raise RuntimeError("Playbook step failed without an error")

    async def _compensate(
        self,
        execution: PlaybookExecution,
        completed: list[tuple[PlaybookStepExecution, object]],
    ) -> None:
        compensable = [
            (row, definition) for row, definition in completed if definition.compensation
        ]
        if not compensable:
            return
        execution.status = PlaybookExecutionStatus.COMPENSATING.value
        await self._publish(
            execution,
            EventType.PLAYBOOK_COMPENSATION_STARTED,
            {"steps": len(compensable)},
        )
        failed = False
        for row, definition in reversed(compensable):
            row.status = PlaybookStepStatus.COMPENSATING.value
            row.compensation_status = PlaybookStepStatus.COMPENSATING.value
            try:
                result = await self._executor.compensate(
                    definition,
                    row.output or {},
                    actor=execution.actor,
                    trace_id=execution.trace_id,
                )
                row.status = PlaybookStepStatus.COMPENSATED.value
                row.compensation_status = PlaybookStepStatus.COMPENSATED.value
                row.compensation_output = result
            except Exception as error:
                failed = True
                row.status = PlaybookStepStatus.COMPENSATION_FAILED.value
                row.compensation_status = PlaybookStepStatus.COMPENSATION_FAILED.value
                row.compensation_output = {"error": str(error)}
            await self._session.commit()
        execution.status = (
            PlaybookExecutionStatus.COMPENSATION_FAILED.value
            if failed
            else PlaybookExecutionStatus.COMPENSATED.value
        )
        await self._publish(
            execution,
            EventType.PLAYBOOK_COMPENSATION_COMPLETED,
            {"status": execution.status},
        )

    async def _checkpoint(
        self,
        execution: PlaybookExecution,
        event_type: EventType,
        payload: dict[str, Any],
        *,
        error: str | None = None,
    ) -> None:
        await self._publish(execution, event_type, payload, error=error)
        await self._session.commit()

    async def _publish(
        self,
        execution: PlaybookExecution,
        event_type: EventType,
        payload: dict[str, Any],
        *,
        error: str | None = None,
    ) -> None:
        await self._publisher.publish(
            PlatformEvent(
                type=event_type,
                aggregate_id=execution.id,
                trace_id=execution.trace_id,
                actor=execution.actor,
                resource="playbook",
                payload=payload,
                error=error,
            )
        )

    @staticmethod
    def _require_step_deadline(row: PlaybookStepExecution, timeout_seconds: int) -> None:
        if row.started_at is None:
            return
        started_at = row.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        if (datetime.now(UTC) - started_at).total_seconds() >= timeout_seconds:
            row.status = PlaybookStepStatus.TIMED_OUT.value
            row.error = "Playbook approval step timed out"
            row.completed_at = datetime.now(UTC)
            raise _PlaybookStepTimeoutError(f"Playbook step timed out: {row.step_id}")

    @staticmethod
    def _remaining_timeout(execution: PlaybookExecution, timeout_seconds: int) -> float:
        if execution.started_at is None:
            return float(timeout_seconds)
        started_at = execution.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        elapsed = (datetime.now(UTC) - started_at).total_seconds()
        return max(0, timeout_seconds - elapsed)

    async def _require(self, execution_id: UUID) -> PlaybookExecution:
        execution = await self._executions.get_with_steps(execution_id)
        if execution is None:
            raise RuntimeError(f"Playbook Execution {execution_id} disappeared")
        return execution
