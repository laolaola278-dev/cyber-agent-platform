"""Workflow application service coordinating definitions, plans, and durable runs."""

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.events import EventPublisher, EventType, PlatformEvent
from app.exceptions import (
    AssetNotFound,
    WorkflowConflict,
    WorkflowExecutionError,
    WorkflowNotFound,
)
from app.models import Asset, WorkflowDefinition, WorkflowInstance
from app.repositories import (
    PageResult,
    WorkflowDefinitionRepository,
    WorkflowInstanceRepository,
)
from app.schemas.workflow import (
    CapabilityPlan,
    WorkflowDefinitionCreate,
    WorkflowPlanRequest,
)
from app.workflow.definition import WorkflowDefinitionLoader
from app.workflow.planner import CapabilityPlanner
from app.workflow.runtime import WorkflowRuntime


class WorkflowService:
    """Persist versioned DAGs and expose controlled execution operations."""

    def __init__(
        self,
        session: AsyncSession,
        definitions: WorkflowDefinitionRepository,
        instances: WorkflowInstanceRepository,
        publisher: EventPublisher,
        runtime: WorkflowRuntime,
        loader: WorkflowDefinitionLoader | None = None,
        planner: CapabilityPlanner | None = None,
    ) -> None:
        self._session = session
        self._definitions = definitions
        self._instances = instances
        self._publisher = publisher
        self._runtime = runtime
        self._loader = loader or WorkflowDefinitionLoader()
        self._planner = planner or CapabilityPlanner()

    async def create_definition(
        self, payload: WorkflowDefinitionCreate, *, trace_id: str
    ) -> WorkflowDefinition:
        try:
            document = self._loader.load(payload.yaml)
        except (ValueError, TypeError) as error:
            raise WorkflowExecutionError(str(error)) from error
        if await self._definitions.get_by_name_version(document.name, document.version):
            raise WorkflowConflict(f"Workflow {document.name}:{document.version} already exists")
        definition = await self._definitions.add(
            WorkflowDefinition(
                name=document.name,
                version=document.version,
                description=document.description,
                source_yaml=payload.yaml,
                definition=document.model_dump(mode="json"),
            )
        )
        await self._publisher.publish(
            PlatformEvent(
                type=EventType.WORKFLOW_CREATED,
                trace_id=trace_id,
                aggregate_id=definition.id,
                actor="api-user",
                resource=f"workflow-definition:{definition.id}",
                payload={"name": definition.name, "version": definition.version},
            )
        )
        await self._session.commit()
        await self._session.refresh(definition)
        return definition

    async def list_definitions(
        self, *, page: int = 1, page_size: int = 100
    ) -> PageResult[WorkflowDefinition]:
        return await self._definitions.list_page(page=page, page_size=page_size)

    async def get_definition(self, definition_id: UUID) -> WorkflowDefinition:
        definition = await self._definitions.get(definition_id)
        if definition is None:
            raise WorkflowNotFound(f"Workflow definition {definition_id} not found")
        return definition

    def plan(self, payload: WorkflowPlanRequest) -> CapabilityPlan:
        try:
            return self._planner.plan(payload.goal)
        except ValueError as error:
            raise WorkflowExecutionError(str(error)) from error

    async def create_run(
        self,
        definition_id: UUID,
        payload: dict[str, object],
        *,
        asset_id: UUID | None = None,
        trace_id: str | None = None,
        execute: bool = True,
    ) -> WorkflowInstance:
        definition = await self.get_definition(definition_id)
        if asset_id is not None:
            asset = await self._session.get(Asset, asset_id)
            if asset is None or asset.deleted_at is not None:
                raise AssetNotFound(f"Asset {asset_id} not found")
        instance = await self._instances.add(
            WorkflowInstance(
                definition=definition,
                asset_id=asset_id,
                input=payload,
                context={},
                trace_id=trace_id or str(uuid4()),
            )
        )
        await self._session.commit()
        persisted = await self._instances.get_with_steps(instance.id)
        if persisted is None:
            raise WorkflowNotFound("Workflow instance disappeared after creation")
        if execute:
            await self._runtime.execute(persisted)
            return await self.get_run(persisted.id)
        return persisted

    async def get_run(self, instance_id: UUID) -> WorkflowInstance:
        instance = await self._instances.get_with_steps(instance_id)
        if instance is None:
            raise WorkflowNotFound(f"Workflow instance {instance_id} not found")
        return instance

    async def resume(self, instance_id: UUID) -> WorkflowInstance:
        instance = await self.get_run(instance_id)
        await self._runtime.execute(instance)
        return await self.get_run(instance_id)

    async def cancel(self, instance_id: UUID) -> WorkflowInstance:
        instance = await self.get_run(instance_id)
        await self._runtime.cancel(instance)
        return await self.get_run(instance_id)
