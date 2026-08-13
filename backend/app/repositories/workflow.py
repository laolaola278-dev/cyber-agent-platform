"""Workflow definition, checkpoint, and execution history repositories."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import (
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowInstance,
    WorkflowStep,
)
from app.repositories.base import SQLAlchemyRepository


class WorkflowDefinitionRepository(SQLAlchemyRepository[WorkflowDefinition]):
    model = WorkflowDefinition

    async def get_by_name_version(self, name: str, version: str) -> WorkflowDefinition | None:
        return await self.session.scalar(
            select(WorkflowDefinition).where(
                WorkflowDefinition.name == name,
                WorkflowDefinition.version == version,
            )
        )


class WorkflowInstanceRepository(SQLAlchemyRepository[WorkflowInstance]):
    model = WorkflowInstance

    async def get_with_steps(self, instance_id: UUID) -> WorkflowInstance | None:
        return await self.session.scalar(
            select(WorkflowInstance)
            .where(WorkflowInstance.id == instance_id)
            .options(
                selectinload(WorkflowInstance.steps),
                selectinload(WorkflowInstance.definition),
            )
        )

    async def list_steps(self, instance_id: UUID) -> list[WorkflowStep]:
        return list(
            await self.session.scalars(
                select(WorkflowStep)
                .where(WorkflowStep.instance_id == instance_id)
                .order_by(WorkflowStep.created_at, WorkflowStep.node_id)
            )
        )

    async def add_step(self, step: WorkflowStep) -> WorkflowStep:
        self.session.add(step)
        await self.session.flush()
        await self.session.refresh(step)
        return step

    async def get_step(self, instance_id: UUID, node_id: str) -> WorkflowStep | None:
        return await self.session.scalar(
            select(WorkflowStep).where(
                WorkflowStep.instance_id == instance_id,
                WorkflowStep.node_id == node_id,
            )
        )

    async def add_execution(self, execution: WorkflowExecution) -> WorkflowExecution:
        self.session.add(execution)
        await self.session.flush()
        await self.session.refresh(execution)
        return execution
