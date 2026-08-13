"""Phase 3 Workflow Engine and Multi-Agent Orchestrator tests."""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.enums import WorkflowNodeType, WorkflowStatus, WorkflowStepStatus
from app.events import InMemoryEventBus
from app.exceptions import WorkflowConflict, WorkflowExecutionError, WorkflowNotFound
from app.models import WorkflowDefinition, WorkflowExecution
from app.repositories import WorkflowDefinitionRepository, WorkflowInstanceRepository
from app.schemas import WorkflowDefinitionCreate, WorkflowPlanRequest
from app.schemas.workflow import WorkflowNodeDefinition
from app.workflow import CapabilityPlanner, NodeRegistry, WorkflowDefinitionLoader
from app.workflow.nodes import NodeContext, NodeResult
from app.workflow.runtime import WorkflowRuntime
from app.workflow.service import WorkflowService
from tests.conftest import TestSessionFactory

SIMPLE_YAML = """
name: website-assessment
version: 1.0.0
steps:
  - capability: crawl.html
    retry:
      max_attempts: 2
  - capability: evidence.generate
"""


class FakeCapabilityExecutor:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[str] = []

    async def execute_capability(
        self,
        capability: str,
        payload: dict[str, Any],
        *,
        trace_id: str,
    ) -> dict[str, Any]:
        self.calls.append(capability)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("transient failure")
        return {"success": True, "capability": capability, "trace_id": trace_id}


class SlowHandler:
    node_type = WorkflowNodeType.AGENT

    async def execute(self, definition: WorkflowNodeDefinition, context: NodeContext) -> NodeResult:
        await asyncio.sleep(1.1)
        return NodeResult(WorkflowStepStatus.SUCCESS, {"success": True})


def test_workflow_yaml_loader_compiles_concise_steps() -> None:
    document = WorkflowDefinitionLoader().load(SIMPLE_YAML)
    assert document.name == "website-assessment"
    assert [node.type.value for node in document.nodes] == [
        "start",
        "agent",
        "agent",
        "end",
    ]
    assert document.nodes[1].retry.max_attempts == 2
    assert len(document.edges) == 3


@pytest.mark.parametrize(
    "source, message",
    [
        ("- invalid", "mapping"),
        ("name: empty\nsteps: []", "non-empty steps"),
        (
            """
name: cycle
nodes:
  - {id: start, type: start}
  - {id: loop, type: agent, capability: test}
  - {id: end, type: end}
edges:
  - {source: start, target: loop}
  - {source: loop, target: start}
  - {source: loop, target: end}
""",
            "acyclic",
        ),
    ],
)
def test_workflow_loader_rejects_invalid_documents(source: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        WorkflowDefinitionLoader().load(source)


def test_capability_planner_is_deterministic_and_rejects_unknown_goal() -> None:
    planner = CapabilityPlanner()
    plan = planner.plan("采集网站")
    assert plan.capabilities == [
        "crawl.html",
        "browser.render",
        "evidence.generate",
        "report.generate",
    ]
    with pytest.raises(ValueError, match="No deterministic"):
        planner.plan("undefined operation")


async def _service(
    executor: FakeCapabilityExecutor,
    *,
    nodes: NodeRegistry | None = None,
) -> tuple[WorkflowService, WorkflowInstanceRepository]:
    session = TestSessionFactory()
    instances = WorkflowInstanceRepository(session)
    runtime = WorkflowRuntime(session, instances, InMemoryEventBus(), executor, nodes)
    service = WorkflowService(
        session,
        WorkflowDefinitionRepository(session),
        instances,
        InMemoryEventBus(),
        runtime,
    )
    return service, instances


async def test_workflow_runtime_executes_dag_and_persists_history() -> None:
    service, instances = await _service(FakeCapabilityExecutor())
    definition = await service.create_definition(
        WorkflowDefinitionCreate(yaml=SIMPLE_YAML), trace_id="workflow-success"
    )
    run = await service.create_run(
        definition.id, {"url": "https://example.com"}, trace_id="workflow-success"
    )
    assert run.status == WorkflowStatus.SUCCESS.value
    assert [step.status for step in run.steps] == ["SUCCESS"] * 4
    assert run.context["step-1"]["capability"] == "crawl.html"
    count = await instances.session.scalar(select(func.count()).select_from(WorkflowExecution))
    assert count == 4
    await instances.session.close()


async def test_workflow_retry_succeeds_on_second_attempt() -> None:
    executor = FakeCapabilityExecutor(failures=1)
    service, instances = await _service(executor)
    definition = await service.create_definition(
        WorkflowDefinitionCreate(yaml=SIMPLE_YAML), trace_id="workflow-retry"
    )
    run = await service.create_run(definition.id, {}, trace_id="workflow-retry")
    step = next(item for item in run.steps if item.node_id == "step-1")
    assert run.status == WorkflowStatus.SUCCESS.value
    assert step.attempt == 2
    assert executor.calls.count("crawl.html") == 2
    await instances.session.close()


async def test_workflow_timeout_fails_after_retry_exhaustion() -> None:
    source = """
name: timeout-workflow
nodes:
  - {id: start, type: start}
  - id: slow
    type: agent
    capability: slow.test
    timeout_seconds: 1
  - {id: end, type: end}
edges:
  - {source: start, target: slow}
  - {source: slow, target: end}
"""
    registry = NodeRegistry.with_platform_defaults()
    registry.register(SlowHandler())
    service, instances = await _service(FakeCapabilityExecutor(), nodes=registry)
    definition = await service.create_definition(
        WorkflowDefinitionCreate(yaml=source), trace_id="workflow-timeout"
    )
    run = await service.create_run(definition.id, {}, trace_id="workflow-timeout")
    assert run.status == WorkflowStatus.FAILED.value
    assert "timed out" in (run.error or "")
    await instances.session.close()


async def test_workflow_cancel_and_resume_from_checkpoint() -> None:
    service, instances = await _service(FakeCapabilityExecutor())
    definition = await service.create_definition(
        WorkflowDefinitionCreate(yaml=SIMPLE_YAML), trace_id="workflow-resume"
    )
    pending = await service.create_run(definition.id, {}, trace_id="workflow-resume", execute=False)
    cancelled = await service.cancel(pending.id)
    assert cancelled.status == WorkflowStatus.CANCELLED.value

    resumable = await service.create_run(
        definition.id, {}, trace_id="workflow-resume-2", execute=False
    )
    resumed = await service.resume(resumable.id)
    assert resumed.status == WorkflowStatus.SUCCESS.value
    await instances.session.close()


async def test_approval_node_waits_without_approval_implementation() -> None:
    source = """
name: approval-workflow
nodes:
  - {id: start, type: start}
  - {id: approval, type: approval}
  - {id: end, type: end}
edges:
  - {source: start, target: approval}
  - {source: approval, target: end}
"""
    service, instances = await _service(FakeCapabilityExecutor())
    definition = await service.create_definition(
        WorkflowDefinitionCreate(yaml=source), trace_id="workflow-wait"
    )
    run = await service.create_run(definition.id, {}, trace_id="workflow-wait")
    assert run.status == WorkflowStatus.WAITING.value
    assert next(step for step in run.steps if step.node_id == "approval").status == "WAITING"
    await instances.session.close()


async def test_workflow_api_definition_planner_run_query_and_cancel(
    client: AsyncClient,
) -> None:
    create_response = await client.post("/workflow", json={"yaml": SIMPLE_YAML})
    assert create_response.status_code == 201
    workflow_id = create_response.json()["id"]

    assert (await client.get("/workflow")).json()["total"] == 1
    assert (await client.get(f"/workflow/{workflow_id}")).status_code == 200
    plan = await client.post("/workflow/plan", json={"goal": "采集网站"})
    assert plan.status_code == 200
    assert plan.json()["capabilities"][0] == "crawl.html"

    run_response = await client.post(
        "/workflow/run",
        json={"workflow_id": workflow_id, "input": {}, "execute": False},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]
    assert (await client.get(f"/workflow/run/{run_id}")).status_code == 200
    cancelled = await client.post(f"/workflow/cancel/{run_id}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


async def test_workflow_service_rejects_duplicates_and_missing_entities() -> None:
    service, instances = await _service(FakeCapabilityExecutor())
    definition = await service.create_definition(
        WorkflowDefinitionCreate(yaml=SIMPLE_YAML), trace_id="duplicates"
    )
    with pytest.raises(WorkflowConflict, match="already exists"):
        await service.create_definition(
            WorkflowDefinitionCreate(yaml=SIMPLE_YAML), trace_id="duplicates"
        )
    with pytest.raises(WorkflowNotFound, match="not found"):
        await service.get_definition(__import__("uuid").uuid4())
    with pytest.raises(WorkflowNotFound, match="not found"):
        await service.get_run(__import__("uuid").uuid4())
    with pytest.raises(WorkflowExecutionError, match="No deterministic"):
        service.plan(WorkflowPlanRequest(goal="unsupported objective"))
    assert isinstance(definition, WorkflowDefinition)
    await instances.session.close()


def test_workflow_node_contracts_and_registry_errors() -> None:
    with pytest.raises(ValueError, match="requires capability"):
        WorkflowNodeDefinition.model_validate({"id": "agent", "type": "agent"})
    with pytest.raises(ValueError, match="requires condition"):
        WorkflowNodeDefinition.model_validate({"id": "condition", "type": "condition"})
    with pytest.raises(ValueError, match="Only AgentNode"):
        WorkflowNodeDefinition.model_validate({"id": "end", "type": "end", "capability": "invalid"})
    with pytest.raises(LookupError, match="No workflow handler"):
        NodeRegistry().resolve(WorkflowNodeType.END)


@pytest.mark.parametrize(
    "source, message",
    [
        (
            """
name: duplicate
nodes:
  - {id: start, type: start}
  - {id: start, type: end}
edges:
  - {source: start, target: start}
""",
            "unique",
        ),
        (
            """
name: unknown-edge
nodes:
  - {id: start, type: start}
  - {id: end, type: end}
edges:
  - {source: start, target: missing}
""",
            "unknown node",
        ),
        (
            """
name: invalid-condition-edge
nodes:
  - {id: start, type: start}
  - {id: condition, type: condition, condition: start.started == true}
  - {id: end, type: end}
edges:
  - {source: start, target: condition}
  - {source: condition, target: end}
""",
            "require when",
        ),
        (
            """
name: unreachable
nodes:
  - {id: start, type: start}
  - {id: stranded, type: agent, capability: test}
  - {id: end, type: end}
edges:
  - {source: start, target: end}
""",
            "reachable",
        ),
    ],
)
def test_workflow_loader_rejects_graph_contract_violations(source: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        WorkflowDefinitionLoader().load(source)


async def test_condition_node_routes_only_matching_branch() -> None:
    source = """
name: condition-workflow
nodes:
  - {id: start, type: start}
  - id: condition
    type: condition
    condition: start.started == true
  - {id: branch-yes, type: agent, capability: branch.yes}
  - {id: branch-no, type: agent, capability: branch.no}
  - {id: end, type: end}
edges:
  - {source: start, target: condition}
  - {source: condition, target: branch-yes, when: 'true'}
  - {source: condition, target: branch-no, when: 'false'}
  - {source: branch-yes, target: end}
  - {source: branch-no, target: end}
"""
    executor = FakeCapabilityExecutor()
    service, instances = await _service(executor)
    definition = await service.create_definition(
        WorkflowDefinitionCreate(yaml=source), trace_id="condition"
    )
    run = await service.create_run(definition.id, {}, trace_id="condition")
    assert run.status == WorkflowStatus.SUCCESS.value
    assert executor.calls == ["branch.yes"]
    assert next(step for step in run.steps if step.node_id == "branch-no").status == "SKIPPED"
    await instances.session.close()


async def test_failed_workflow_can_resume_from_last_successful_checkpoint() -> None:
    executor = FakeCapabilityExecutor(failures=2)
    service, instances = await _service(executor)
    definition = await service.create_definition(
        WorkflowDefinitionCreate(yaml=SIMPLE_YAML), trace_id="failed-resume"
    )
    failed = await service.create_run(definition.id, {}, trace_id="failed-resume")
    assert failed.status == WorkflowStatus.FAILED.value
    executor.failures = 0
    resumed = await service.resume(failed.id)
    assert resumed.status == WorkflowStatus.SUCCESS.value
    start = next(step for step in resumed.steps if step.node_id == "start")
    assert start.attempt == 1
    await instances.session.close()
