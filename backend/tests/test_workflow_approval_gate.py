"""The workflow engine's approval gate: park, decide, and resume for real.

APPROVAL used to be a Phase 3 placeholder that returned WAITING forever, so any
playbook containing a human gate could be started but never finished -- and
``POST /workflow/run/{id}/resume`` just parked it again. These tests drive the
gate through the service the HTTP route calls, because that is the path an
operator actually takes.
"""

from typing import Any
from uuid import uuid4

import pytest

from app.core.enums import WorkflowStatus
from app.events import InMemoryEventBus
from app.exceptions import WorkflowConflict
from app.middleware.authorization import _permission_for
from app.repositories import WorkflowDefinitionRepository, WorkflowInstanceRepository
from app.schemas import WorkflowDefinitionCreate
from app.workflow.nodes import NodeRegistry
from app.workflow.runtime import WorkflowRuntime
from app.workflow.service import WorkflowService
from tests.conftest import TestSessionFactory

GATED_YAML = """
name: gated-change
version: 1.0.0
nodes:
  - id: start
    type: start
  - id: gate
    type: approval
  - id: finish
    type: end
edges:
  - source: start
    target: gate
  - source: gate
    target: finish
"""

UNGATED_YAML = """
name: ungated-check
version: 1.0.0
nodes:
  - id: start
    type: start
  - id: probe
    type: agent
    capability: crawl.html
  - id: finish
    type: end
edges:
  - source: start
    target: probe
  - source: probe
    target: finish
"""


class RecordingExecutor:
    """Stands in for the agent executor; the gate itself needs no capability."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_capability(
        self,
        capability: str,
        payload: dict[str, Any],
        *,
        trace_id: str,
        asset_id: Any = None,
    ) -> dict[str, Any]:
        self.calls.append(capability)
        return {"success": True, "capability": capability}


async def _service(executor: RecordingExecutor | None = None) -> WorkflowService:
    session = TestSessionFactory()
    executor = executor or RecordingExecutor()
    instances = WorkflowInstanceRepository(session)
    runtime = WorkflowRuntime(
        session, instances, InMemoryEventBus(), executor, NodeRegistry.with_platform_defaults()
    )
    return WorkflowService(
        session,
        WorkflowDefinitionRepository(session),
        instances,
        InMemoryEventBus(),
        runtime,
    )


async def _gated_run(service: WorkflowService, trace_id: str) -> Any:
    definition = await service.create_definition(
        WorkflowDefinitionCreate(yaml=GATED_YAML), trace_id=trace_id
    )
    return await service.create_run(definition.id, {}, trace_id=trace_id)


async def test_the_gate_parks_the_run_and_says_what_it_needs() -> None:
    service = await _service()
    run = await _gated_run(service, "gate-park")

    assert run.status == WorkflowStatus.WAITING.value
    gate = next(step for step in run.steps if step.node_id == "gate")
    assert gate.status == "WAITING"
    # The operator has to be told how to unblock it; a bare WAITING is a dead end.
    assert gate.output["node_id"] == "gate"
    assert "/decision" in gate.output["decide_with"]
    assert "approvals" not in run.context


async def test_resuming_without_a_decision_cannot_pass_the_gate() -> None:
    """The old placeholder looped here forever -- assert that is still true."""
    service = await _service()
    run = await _gated_run(service, "gate-loop")
    after = await service.resume(run.id)
    assert after.status == WorkflowStatus.WAITING.value
    assert next(step for step in after.steps if step.node_id == "gate").status == "WAITING"


async def test_approval_lets_the_run_finish_and_records_the_reviewer() -> None:
    executor = RecordingExecutor()
    service = await _service(executor)
    run = await _gated_run(service, "gate-approve")

    decided = await service.decide(
        run.id, decision="APPROVED", actor="soc-analyst", reason="within window"
    )

    assert decided.status == WorkflowStatus.SUCCESS.value
    gate = next(step for step in decided.steps if step.node_id == "gate")
    assert gate.status == "SUCCESS"
    assert gate.output["approved_by"] == "soc-analyst"
    # The durable trail of who opened the gate lives on the run, not just the step.
    assert decided.context["approvals"]["gate"]["actor"] == "soc-analyst"
    assert decided.context["approvals"]["gate"]["state"] == "APPROVED"


async def test_rejection_terminates_the_run_with_the_reviewers_reason() -> None:
    executor = RecordingExecutor()
    service = await _service(executor)
    run = await _gated_run(service, "gate-reject")

    decided = await service.decide(
        run.id,
        decision="REJECTED",
        actor="soc-analyst",
        node_id="gate",
        reason="change window closed",
    )

    assert decided.status == WorkflowStatus.FAILED.value
    assert "refused by soc-analyst" in decided.error
    assert "change window closed" in decided.error
    gate = next(step for step in decided.steps if step.node_id == "gate")
    assert gate.status == "FAILED"
    # Nothing downstream ran, and a refusal is not retried like a transient error.
    assert "finish" not in {step.node_id for step in decided.steps if step.status == "SUCCESS"}
    assert executor.calls == []


async def test_a_gate_can_only_be_answered_once() -> None:
    service = await _service()
    run = await _gated_run(service, "gate-twice")
    await service.decide(run.id, decision="APPROVED", actor="soc-analyst")

    with pytest.raises(WorkflowConflict):
        await service.decide(run.id, decision="REJECTED", actor="administrator")


async def test_deciding_a_run_that_asked_for_nothing_is_refused() -> None:
    service = await _service()
    definition = await service.create_definition(
        WorkflowDefinitionCreate(yaml=UNGATED_YAML), trace_id="gate-none"
    )
    run = await service.create_run(definition.id, {}, trace_id="gate-none")
    assert run.status == WorkflowStatus.SUCCESS.value

    with pytest.raises(WorkflowConflict):
        await service.decide(run.id, decision="APPROVED", actor="soc-analyst")


async def test_naming_the_wrong_gate_is_refused() -> None:
    service = await _service()
    run = await _gated_run(service, "gate-wrong-node")
    with pytest.raises(WorkflowConflict):
        await service.decide(
            run.id, decision="APPROVED", actor="soc-analyst", node_id="another-gate"
        )


def test_answering_a_gate_needs_the_approval_permission() -> None:
    """The gate is decided with ``approval.decide``, not with platform mastery.

    Without this mapping the route would fall through to ``platform.manage``,
    which would either lock reviewers out of their own approval queue or, if
    someone widened the fallback, let anyone who can start a run approve it.
    """

    run_id = str(uuid4())
    assert (
        _permission_for("POST", f"/workflow/run/{run_id}/decision") == "approval.decide"
    )
    # Starting and resuming a run keeps the pre-existing mapping.
    assert _permission_for("POST", f"/workflow/run/{run_id}/resume") == "platform.manage"
    assert _permission_for("GET", f"/workflow/run/{run_id}") == "platform.manage"
