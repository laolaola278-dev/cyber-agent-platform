"""Phase 20 SOAR Playbook Engine acceptance tests."""

import asyncio
import importlib.util
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import select

from app.events import EventType, InMemoryEventBus, PlatformEvent
from app.exceptions import (
    PlaybookExecutionError,
    PlaybookPolicyViolation,
    PlaybookValidationError,
)
from app.models.playbook import (
    Playbook,
    PlaybookExecution,
    PlaybookStepExecution,
    PlaybookVersion,
)
from app.playbook.contracts import (
    PlaybookApproval,
    PlaybookCreate,
    PlaybookDSL,
    PlaybookNodeType,
    PlaybookRunRequest,
    PlaybookStepDefinition,
    PlaybookTriggerType,
)
from app.playbook.executor import PlaybookExecutor, StepOutcome
from app.playbook.planner import PlaybookPlan, PlaybookPlanner, SafeConditionEvaluator
from app.playbook.policy import PlaybookPolicy
from app.playbook.registry import PlaybookRegistry
from app.playbook.runtime import PlaybookRuntime
from app.playbook.service import PlaybookService
from app.repositories.playbook import (
    PlaybookExecutionRepository,
    PlaybookRepository,
    PlaybookTriggerRepository,
    PlaybookVersionRepository,
)
from tests.conftest import TestSessionFactory

VALID_DSL = """
dsl_version: v1
name: safe-manual-playbook
description: synthetic test
trigger:
  type: manual
steps:
  - id: check
    type: condition
    condition: "severity == 'HIGH'"
timeout_seconds: 60
max_parallel: 1
allowed_plugins: []
allowed_capabilities: []
allowed_runners: [api-user]
"""

INCIDENT_DSL = """
dsl_version: v1
name: incident-created-playbook
description: synthetic incident trigger test
trigger:
  type: incident.created
  filters:
    source: MANUAL
steps:
  - id: check-priority
    type: condition
    condition: "priority == 'P2'"
timeout_seconds: 60
max_parallel: 1
allowed_plugins: []
allowed_capabilities: []
allowed_runners: [playbook-event]
"""

APPROVAL_DSL = """
dsl_version: v1
name: approval-resume-playbook
description: approval persistence test
trigger:
  type: manual
steps:
  - id: approve
    type: approval
  - id: after-approval
    type: condition
    condition: "severity == 'HIGH'"
timeout_seconds: 60
max_parallel: 1
allowed_plugins: []
allowed_capabilities: []
allowed_runners: [api-user]
"""

TICKET_COMPENSATION_DSL = """
dsl_version: v1
name: ticket-compensation-playbook
trigger:
  type: manual
steps:
  - id: create-ticket
    type: ticket
    capability: notification.ticket
    input:
      title: Synthetic containment ticket
      description: Created only by the Phase 20 test
      priority: HIGH
    compensation:
      type: ticket
      capability: notification.ticket
  - id: fail-closed
    type: condition
    condition: "missing_variable == True"
timeout_seconds: 60
max_parallel: 1
allowed_plugins: []
allowed_capabilities: [notification.ticket]
allowed_runners: [api-user]
"""

ASSESSMENT_DETECTION_DSL = """
dsl_version: v1
name: assessment-detection-playbook
trigger:
  type: manual
steps:
  - id: assess
    type: assessment
    capability: header.scan
    input:
      asset_id: $input.asset_id
      input: {}
  - id: detect
    type: detection
    capability: host.detect
    input:
      asset_id: $input.asset_id
      log_source: synthetic
      parser: structured-json
      input: {}
timeout_seconds: 60
max_parallel: 1
allowed_plugins: []
allowed_capabilities: [header.scan, host.detect]
allowed_runners: [api-user]
"""

RESPONSE_COMPENSATION_DSL = """
dsl_version: v1
name: response-compensation-playbook
trigger:
  type: manual
steps:
  - id: approve
    type: approval
  - id: contain
    type: response
    capability: response.block
    input:
      incident_id: $input.incident_id
      asset_ids: [$input.asset_id]
      reason: Synthetic Phase 20 containment
      risk_level: HIGH
      parameters: {}
      rollback_parameters:
        restore: true
    compensation:
      type: response
      capability: response.rollback
  - id: fail-closed
    type: condition
    condition: "missing_variable == True"
timeout_seconds: 60
max_parallel: 1
allowed_plugins: []
allowed_capabilities: [response.block]
allowed_runners: [api-user]
"""

NOTIFICATION_COMPENSATION_DSL = """
dsl_version: v1
name: notification-compensation-playbook
trigger:
  type: manual
steps:
  - id: notify
    type: notification
    capability: notification.custom
    input:
      incident_id: $input.incident_id
      severity: HIGH
      priority: HIGH
      variables:
        incident_title: Synthetic Phase 20 notification
        incident_id: $input.incident_id
        severity: HIGH
    compensation:
      type: notification
      capability: notification.custom
  - id: fail-closed
    type: condition
    condition: "missing_variable == True"
timeout_seconds: 60
max_parallel: 1
allowed_plugins: []
allowed_capabilities: [notification.custom]
allowed_runners: [api-user]
"""


def test_dsl_is_strict_and_reserved_features_fail_closed() -> None:
    document = PlaybookDSL.load(VALID_DSL)
    assert document.name == "safe-manual-playbook"
    with pytest.raises(ValidationError):
        PlaybookDSL.load(VALID_DSL.replace("type: manual", "type: schedule"))
    with pytest.raises(ValidationError):
        PlaybookDSL.load(VALID_DSL.replace("type: condition", "type: parallel"))
    with pytest.raises(ValidationError):
        PlaybookDSL.load(VALID_DSL.replace("description: synthetic test", "unknown: forbidden"))


def test_condition_evaluator_has_no_code_execution() -> None:
    evaluator = SafeConditionEvaluator()
    assert evaluator.evaluate("severity == 'HIGH' and count >= 2", {"severity": "HIGH", "count": 2})
    with pytest.raises(PlaybookValidationError):
        evaluator.evaluate("__import__('os').system('whoami')", {})
    with pytest.raises(PlaybookValidationError):
        evaluator.evaluate("object.value", {"object": {"value": True}})


def test_phase_20_migration_is_reversible() -> None:
    backend = Path(__file__).parents[1]
    path = backend / "alembic/versions/20260803_0018_playbook_engine.py"
    spec = importlib.util.spec_from_file_location("phase_20_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    config = Config(str(backend / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    # Phase 28.3/28.5 adds the Acquisition durable-runtime migration on top of
    # the playbook chain; the chain must remain a single head.
    assert script.get_heads() == ["20260812_0021"]
    revision = script.get_revision("20260808_0019")
    assert revision is not None
    assert revision.down_revision == "20260803_0018"

    upgrade_buffer = io.StringIO()
    upgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": upgrade_buffer},
    )
    with Operations.context(upgrade_context):
        migration.upgrade()
    upgrade_sql = upgrade_buffer.getvalue()

    tables = (
        "playbooks",
        "playbook_versions",
        "playbook_triggers",
        "playbook_executions",
        "playbook_step_executions",
    )
    for table in tables:
        assert f"CREATE TABLE {table}" in upgrade_sql
    for constraint in (
        "fk_playbook_versions_playbook_id_playbooks",
        "fk_playbook_triggers_playbook_version_id_playbook_versions",
        "fk_playbook_executions_playbook_id_playbooks",
        "fk_playbook_executions_playbook_version_id_playbook_versions",
        "fk_playbook_executions_trigger_id_playbook_triggers",
        "fk_playbook_step_executions_execution_id_playbook_executions",
        "playbook_execution_status",
        "playbook_step_execution_status",
        "uq_playbook_executions_idempotency_key",
        "uq_playbook_step_execution_step",
    ):
        assert f"CONSTRAINT {constraint}" in upgrade_sql

    downgrade_buffer = io.StringIO()
    downgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": downgrade_buffer},
    )
    with Operations.context(downgrade_context):
        migration.downgrade()
    downgrade_sql = downgrade_buffer.getvalue()
    for table in reversed(tables):
        assert f"DROP TABLE {table}" in downgrade_sql
    assert downgrade_sql.index("DROP TABLE playbook_step_executions") < downgrade_sql.index(
        "DROP TABLE playbook_executions"
    )
    assert downgrade_sql.index("DROP TABLE playbook_executions") < downgrade_sql.index(
        "DROP TABLE playbook_triggers"
    )
    assert downgrade_sql.index("DROP TABLE playbook_triggers") < downgrade_sql.index(
        "DROP TABLE playbook_versions"
    )
    assert downgrade_sql.index("DROP TABLE playbook_versions") < downgrade_sql.index(
        "DROP TABLE playbooks"
    )


@pytest.mark.asyncio
async def test_manual_playbook_create_and_run(client) -> None:
    created = await client.post("/playbooks", json={"yaml": VALID_DSL})
    assert created.status_code == 201, created.text
    playbook = created.json()
    assert playbook["name"] == "safe-manual-playbook"
    executed = await client.post(
        f"/playbooks/{playbook['id']}/run",
        json={"actor": "api-user", "input": {"severity": "HIGH"}, "idempotency_key": "phase20-1"},
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "SUCCEEDED"
    replay = await client.post(
        f"/playbooks/{playbook['id']}/run",
        json={"actor": "api-user", "input": {"severity": "HIGH"}, "idempotency_key": "phase20-1"},
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == executed.json()["id"]


@pytest.mark.asyncio
async def test_waiting_approval_resumes_the_same_execution(client) -> None:
    created = await client.post("/playbooks", json={"yaml": APPROVAL_DSL})
    assert created.status_code == 201, created.text
    playbook_id = created.json()["id"]
    waiting = await client.post(
        f"/playbooks/{playbook_id}/run",
        json={"actor": "api-user", "input": {"severity": "HIGH"}},
    )
    assert waiting.status_code == 200, waiting.text
    waiting_body = waiting.json()
    assert waiting_body["status"] == "WAITING_APPROVAL"
    assert len(waiting_body["steps"]) == 1
    execution_id = waiting_body["id"]

    same_actor = await client.post(
        f"/playbooks/executions/{execution_id}/resume",
        json={
            "actor": "api-user",
            "approvals": {"approve": {"approver": "api-user"}},
        },
    )
    assert same_actor.status_code == 422, same_actor.text
    assert same_actor.json()["error"]["code"] == "PLAYBOOK_VALIDATION_ERROR"

    resumed = await client.post(
        f"/playbooks/executions/{execution_id}/resume",
        json={
            "actor": "api-user",
            "approvals": {"approve": {"approver": "soc-approver", "comment": "Approved"}},
        },
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["id"] == execution_id
    assert body["status"] == "SUCCEEDED"
    assert [step["step_id"] for step in body["steps"]] == ["approve", "after-approval"]
    assert body["steps"][0]["attempt"] == 1
    assert body["steps"][0]["output"]["approver"] == "soc-approver"

    duplicate = await client.post(
        f"/playbooks/executions/{execution_id}/resume",
        json={
            "actor": "api-user",
            "approvals": {"approve": {"approver": "soc-approver"}},
        },
    )
    assert duplicate.status_code == 409, duplicate.text


@pytest.mark.asyncio
async def test_waiting_approval_resume_fails_closed_after_step_deadline(client) -> None:
    source = APPROVAL_DSL.replace("name: approval-resume-playbook", "name: expired-approval")
    source = source.replace("    type: approval", "    type: approval\n    timeout_seconds: 1")
    created = await client.post("/playbooks", json={"yaml": source})
    assert created.status_code == 201, created.text
    waiting = await client.post(
        f"/playbooks/{created.json()['id']}/run",
        json={"actor": "api-user", "input": {"severity": "HIGH"}},
    )
    assert waiting.status_code == 200, waiting.text
    execution_id = waiting.json()["id"]
    async with TestSessionFactory() as session:
        step = await session.scalar(
            select(PlaybookStepExecution).where(
                PlaybookStepExecution.execution_id == UUID(execution_id)
            )
        )
        assert step is not None
        step.started_at = datetime.now(UTC) - timedelta(seconds=2)
        await session.commit()
    resumed = await client.post(
        f"/playbooks/executions/{execution_id}/resume",
        json={
            "actor": "api-user",
            "approvals": {"approve": {"approver": "soc-approver"}},
        },
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["status"] == "TIMED_OUT"
    assert body["steps"][0]["status"] == "TIMED_OUT"
    assert body["steps"][0]["error"] == "Playbook approval step timed out"
    assert len(body["steps"]) == 1


@pytest.mark.asyncio
async def test_incident_created_trigger_runs_on_the_request_event_bus(client) -> None:
    created = await client.post("/playbooks", json={"yaml": INCIDENT_DSL})
    assert created.status_code == 201, created.text
    incident = await client.post(
        "/incidents",
        json={
            "title": "Phase 20 incident trigger",
            "description": "Synthetic event-driven Playbook execution",
            "severity": "HIGH",
            "confidence": "HIGH",
            "source": "MANUAL",
            "attributes": {"correlation_key": "phase20:incident-trigger"},
            "create_case": False,
        },
    )
    assert incident.status_code == 201, incident.text
    executions = await client.get("/playbooks/executions")
    assert executions.status_code == 200, executions.text
    body = executions.json()
    assert body["total"] == 1
    execution = body["items"][0]
    assert execution["trigger_type"] == "incident.created"
    assert execution["status"] == "SUCCEEDED"
    assert execution["actor"] == "playbook-event"
    assert execution["input"]["incident_id"] == incident.json()["id"]
    assert execution["steps"][0]["step_id"] == "check-priority"
    assert execution["steps"][0]["status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_assessment_and_detection_nodes_execute_through_domain_services(client) -> None:
    asset = await client.post(
        "/assets",
        json={
            "asset_type": "HOST",
            "name": "Phase 20 assessment detection host",
            "value": "phase20-assessment-detection.example.test",
            "criticality": "HIGH",
        },
    )
    assert asset.status_code == 201, asset.text
    created = await client.post("/playbooks", json={"yaml": ASSESSMENT_DETECTION_DSL})
    assert created.status_code == 201, created.text
    executed = await client.post(
        f"/playbooks/{created.json()['id']}/run",
        json={"actor": "api-user", "input": {"asset_id": asset.json()["id"]}},
    )
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "SUCCEEDED"
    by_id = {step["step_id"]: step for step in body["steps"]}
    assert by_id["assess"]["status"] == "SUCCEEDED"
    assert by_id["assess"]["output"]["status"] == "SUCCESS"
    assert by_id["detect"]["status"] == "SUCCEEDED"
    assert by_id["detect"]["output"]["status"] == "SUCCESS"
    assessments = await client.get("/assessment/tasks")
    detections = await client.get("/detection/tasks")
    assert assessments.status_code == 200 and assessments.json()["total"] == 1
    assert detections.status_code == 200 and detections.json()["total"] == 1


@pytest.mark.asyncio
async def test_notification_compensation_is_explicitly_ignored(client) -> None:
    incident = await client.post(
        "/incidents",
        json={
            "title": "Phase 20 notification incident",
            "severity": "HIGH",
            "confidence": "HIGH",
            "source": "MANUAL",
            "attributes": {"correlation_key": "phase20:notification-compensation"},
            "create_case": False,
        },
    )
    assert incident.status_code == 201, incident.text
    created = await client.post("/playbooks", json={"yaml": NOTIFICATION_COMPENSATION_DSL})
    assert created.status_code == 201, created.text
    executed = await client.post(
        f"/playbooks/{created.json()['id']}/run",
        json={"actor": "api-user", "input": {"incident_id": incident.json()["id"]}},
    )
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "COMPENSATED", {
        "error": body["error"],
        "steps": body["steps"],
    }
    notify = next(step for step in body["steps"] if step["step_id"] == "notify")
    assert notify["status"] == "COMPENSATED"
    assert notify["compensation_status"] == "COMPENSATED"
    assert notify["output"]["status"] == "VERIFIED"


@pytest.mark.asyncio
async def test_response_node_compensates_through_response_service(client) -> None:
    asset = await client.post(
        "/assets",
        json={
            "asset_type": "HOST",
            "name": "Phase 20 response host",
            "value": "phase20-response-host.example.test",
            "criticality": "HIGH",
        },
    )
    assert asset.status_code == 201, asset.text
    incident = await client.post(
        "/incidents",
        json={
            "title": "Phase 20 response incident",
            "severity": "HIGH",
            "confidence": "HIGH",
            "source": "MANUAL",
            "attributes": {"correlation_key": "phase20:response-compensation"},
            "create_case": False,
        },
    )
    assert incident.status_code == 201, incident.text
    created = await client.post("/playbooks", json={"yaml": RESPONSE_COMPENSATION_DSL})
    assert created.status_code == 201, created.text
    executed = await client.post(
        f"/playbooks/{created.json()['id']}/run",
        json={
            "actor": "api-user",
            "input": {
                "incident_id": incident.json()["id"],
                "asset_id": asset.json()["id"],
            },
            "approvals": {"approve": {"approver": "soc-approver", "comment": "Scope reviewed"}},
        },
    )
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "COMPENSATED"
    by_id = {step["step_id"]: step for step in body["steps"]}
    assert by_id["contain"]["status"] == "COMPENSATED"
    plans = await client.get("/response/plans")
    assert plans.status_code == 200, plans.text
    assert plans.json()["total"] == 1
    plan = plans.json()["items"][0]
    assert plan["approval_state"] == "ROLLED_BACK"
    assert plan["rollback_state"] == "VERIFIED"
    assert plan["approvals"][0]["approver"] == "soc-approver"


@pytest.mark.asyncio
async def test_ticket_node_compensates_through_notification_service(client) -> None:
    created = await client.post("/playbooks", json={"yaml": TICKET_COMPENSATION_DSL})
    assert created.status_code == 201, created.text
    executed = await client.post(
        f"/playbooks/{created.json()['id']}/run",
        json={"actor": "api-user"},
    )
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "COMPENSATED"
    by_id = {step["step_id"]: step for step in body["steps"]}
    assert by_id["create-ticket"]["status"] == "COMPENSATED"
    assert by_id["create-ticket"]["compensation_status"] == "COMPENSATED"
    assert by_id["fail-closed"]["status"] == "FAILED"
    tickets = await client.get("/tickets")
    assert tickets.status_code == 200, tickets.text
    assert tickets.json()["total"] == 1
    assert tickets.json()["items"][0]["status"] == "CLOSED"


@pytest.mark.asyncio
async def test_runtime_retry_skip_failure_and_timeout_are_persisted() -> None:
    class SyntheticExecutor:
        def __init__(self) -> None:
            self.attempts = 0

        async def execute(self, step, **kwargs) -> StepOutcome:
            self.attempts += 1
            if step.id == "retry" and self.attempts == 1:
                raise RuntimeError("retry once")
            if step.id == "skip":
                return StepOutcome(status="SKIPPED", output={"reason": "condition-false"})
            if step.id == "timeout":
                await asyncio.sleep(0.02)
            return StepOutcome(status="SUCCEEDED", output={"step": step.id})

        async def compensate(self, step, output, **kwargs) -> dict[str, str]:
            return {"status": "COMPENSATED"}

    document = PlaybookDSL.load(
        VALID_DSL.replace(
            "  - id: check\n    type: condition\n    condition: \"severity == 'HIGH'\"",
            '  - id: retry\n    type: condition\n    condition: "True"\n'
            "    retry:\n      max_attempts: 2\n"
            '  - id: skip\n    type: condition\n    condition: "True"',
        )
    )
    async with TestSessionFactory() as session:
        playbook = Playbook(name="runtime-semantics", enabled=True)
        session.add(playbook)
        await session.flush()
        version = PlaybookVersion(
            playbook_id=playbook.id,
            version="1.0.0",
            dsl_version="v1",
            source_yaml=VALID_DSL,
            document=document.model_dump(mode="json"),
            checksum="0" * 64,
        )
        session.add(version)
        await session.flush()
        execution = PlaybookExecution(
            playbook_id=playbook.id,
            playbook_version_id=version.id,
            trigger_type="manual",
            status="PENDING",
            actor="api-user",
            input={},
            context={},
            trace_id=str(uuid4()),
        )
        session.add(execution)
        await session.commit()
        executor = SyntheticExecutor()
        runtime = PlaybookRuntime(
            session,
            PlaybookExecutionRepository(session),
            executor,
            InMemoryEventBus(),
            PlaybookPolicy(),
        )
        result = await runtime.execute(
            execution,
            PlaybookPlan(document=document, steps=tuple(document.steps)),
            approvals={},
        )
        assert result.status == "SUCCEEDED"
        assert [step.status for step in result.steps] == ["SUCCEEDED", "SKIPPED"]
        assert result.steps[0].attempt == 2

        timeout_document = PlaybookDSL.load(
            VALID_DSL.replace("id: check", "id: timeout").replace(
                "    condition: \"severity == 'HIGH'\"",
                "    condition: \"severity == 'HIGH'\"\n    timeout_seconds: 1",
            )
        )
        timeout_execution = PlaybookExecution(
            playbook_id=playbook.id,
            playbook_version_id=version.id,
            trigger_type="manual",
            status="PENDING",
            actor="api-user",
            input={},
            context={},
            trace_id=str(uuid4()),
        )
        session.add(timeout_execution)
        await session.commit()
        timeout_runtime = PlaybookRuntime(
            session,
            PlaybookExecutionRepository(session),
            executor,
            InMemoryEventBus(),
            PlaybookPolicy(),
        )
        original_execute = executor.execute

        async def hangs(step, **kwargs) -> StepOutcome:
            await asyncio.sleep(1.1)
            return await original_execute(step, **kwargs)

        executor.execute = hangs
        timed_out = await timeout_runtime.execute(
            timeout_execution,
            PlaybookPlan(document=timeout_document, steps=tuple(timeout_document.steps)),
            approvals={},
        )
        assert timed_out.status == "TIMED_OUT"
        assert timed_out.error == "Playbook step timed out: timeout"
        assert timed_out.steps[0].status == "TIMED_OUT"
        assert timed_out.steps[0].error == "Playbook step timed out"


@pytest.mark.asyncio
async def test_runtime_compensates_completed_steps_in_reverse_order() -> None:
    compensated: list[str] = []

    class CompensationExecutor:
        async def execute(self, step, **kwargs) -> StepOutcome:
            if step.id == "fail":
                raise RuntimeError("synthetic terminal failure")
            return StepOutcome(status="SUCCEEDED", output={"step": step.id})

        async def compensate(self, step, output, **kwargs) -> dict[str, str]:
            compensated.append(step.id)
            return {"status": "COMPENSATED", "step": step.id}

    source = """
dsl_version: v1
name: compensation-order
trigger:
  type: manual
steps:
  - id: first
    type: notification
    capability: notification.email
    compensation:
      type: notification
      capability: notification.email
  - id: second
    type: ticket
    capability: notification.ticket
    compensation:
      type: ticket
      capability: notification.ticket
  - id: fail
    type: condition
    condition: "True"
timeout_seconds: 60
max_parallel: 1
allowed_plugins: []
allowed_capabilities: [notification.email, notification.ticket]
allowed_runners: [api-user]
"""
    document = PlaybookDSL.load(source)
    async with TestSessionFactory() as session:
        playbook = Playbook(name="compensation-order", enabled=True)
        session.add(playbook)
        await session.flush()
        version = PlaybookVersion(
            playbook_id=playbook.id,
            version="1.0.0",
            dsl_version="v1",
            source_yaml=source,
            document=document.model_dump(mode="json"),
            checksum="1" * 64,
        )
        session.add(version)
        await session.flush()
        execution = PlaybookExecution(
            playbook_id=playbook.id,
            playbook_version_id=version.id,
            trigger_type="manual",
            status="PENDING",
            actor="api-user",
            input={},
            context={},
            trace_id=str(uuid4()),
        )
        session.add(execution)
        await session.commit()
        runtime = PlaybookRuntime(
            session,
            PlaybookExecutionRepository(session),
            CompensationExecutor(),
            InMemoryEventBus(),
            PlaybookPolicy(),
        )
        result = await runtime.execute(
            execution,
            PlaybookPlan(document=document, steps=tuple(document.steps)),
            approvals={},
        )
        assert result.status == "COMPENSATED"
        assert result.error == "synthetic terminal failure"
        assert compensated == ["second", "first"]
        by_id = {step.step_id: step for step in result.steps}
        assert by_id["first"].status == "COMPENSATED"
        assert by_id["second"].status == "COMPENSATED"
        assert by_id["fail"].status == "FAILED"


@pytest.mark.asyncio
async def test_playbook_service_fail_closed_paths(client) -> None:
    created = await client.post("/playbooks", json={"yaml": VALID_DSL})
    assert created.status_code == 201, created.text
    playbook_id = created.json()["id"]
    duplicate = await client.post("/playbooks", json={"yaml": VALID_DSL})
    assert duplicate.status_code == 409
    disabled_source = VALID_DSL.replace("name: safe-manual-playbook", "name: disabled-playbook")
    disabled = await client.post("/playbooks", json={"yaml": disabled_source, "enabled": False})
    assert disabled.status_code == 201
    disabled_run = await client.post(
        f"/playbooks/{disabled.json()['id']}/run", json={"actor": "api-user"}
    )
    assert disabled_run.status_code == 409
    missing = await client.get(f"/playbooks/{uuid4()}")
    assert missing.status_code == 404
    mismatch = await client.post(
        f"/playbooks/{playbook_id}/run",
        json={"actor": "api-user", "idempotency_key": "phase20-conflict"},
    )
    assert mismatch.status_code == 200
    second = await client.post("/playbooks", json={"yaml": disabled_source, "enabled": True})
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_playbook_list_detail_and_execution_detail_routes(client) -> None:
    created = await client.post("/playbooks", json={"yaml": VALID_DSL})
    assert created.status_code == 201, created.text
    playbook_id = created.json()["id"]
    listing = await client.get("/playbooks")
    assert listing.status_code == 200 and listing.json()["total"] == 1
    detail = await client.get(f"/playbooks/{playbook_id}")
    assert detail.status_code == 200 and detail.json()["id"] == playbook_id
    executed = await client.post(
        f"/playbooks/{playbook_id}/run",
        json={"actor": "api-user", "input": {"severity": "HIGH"}},
    )
    execution_id = executed.json()["id"]
    execution_detail = await client.get(f"/playbooks/executions/{execution_id}")
    assert execution_detail.status_code == 200
    assert execution_detail.json()["id"] == execution_id


@pytest.mark.asyncio
async def test_playbook_routes_are_registered(client) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/playbooks" in paths
    assert "/playbooks/{playbook_id}/run" in paths
    assert "/playbooks/executions" in paths
    assert "/playbooks/executions/{execution_id}" in paths
    assert "/playbooks/executions/{execution_id}/resume" in paths


def test_safe_condition_evaluator_covers_typed_values_and_fail_closed_edges() -> None:
    evaluator = SafeConditionEvaluator()
    context = {"data": {"items": ["one", "two"]}, "flag": False, "value": 3}
    assert evaluator.evaluate("data['items'][1] in ['one', 'two']", context)
    assert evaluator.evaluate("not flag or value < 4", context)
    assert evaluator.evaluate("{'key': value}['key'] == 3", context)
    assert evaluator.evaluate("value != 4 and value <= 3 and value > 2", context)
    assert evaluator.evaluate("value not in [1, 2]", context)
    with pytest.raises(PlaybookValidationError, match="Invalid condition syntax"):
        evaluator.evaluate("value ==", context)
    with pytest.raises(PlaybookValidationError, match="Unknown condition variable"):
        evaluator.evaluate("missing == True", context)
    with pytest.raises(PlaybookValidationError, match="Condition subscript is not available"):
        evaluator.evaluate("data['missing'] == 1", context)
    with pytest.raises(PlaybookValidationError, match="Condition operation is not allowed"):
        evaluator.evaluate("value + 1", context)


def test_playbook_policy_and_planner_fail_closed_boundaries() -> None:
    policy = PlaybookPolicy(
        allowed_capabilities=frozenset({"web.scan"}),
        allowed_plugins=frozenset({"approved-plugin"}),
        max_timeout_seconds=10,
        max_retry_attempts=2,
    )
    with pytest.raises(PlaybookValidationError, match="timeout"):
        policy.validate_document(PlaybookDSL.load(VALID_DSL))
    with pytest.raises(PlaybookValidationError, match="max_parallel"):
        policy.validate_document(SimpleNamespace(max_parallel=2, timeout_seconds=1, steps=[]))
    with pytest.raises(PlaybookPolicyViolation, match="Runner"):
        policy.authorize_runner("intruder")
    with pytest.raises(PlaybookPolicyViolation, match="Approver"):
        PlaybookPolicy(allowed_approvers=frozenset({"soc"})).authorize_approver("intruder")
    assert PlaybookPolicy.matches_filters({"source": "MANUAL"}, {"source": "MANUAL"})
    assert not PlaybookPolicy.matches_filters({"source": "MANUAL"}, {"source": "API"})
    document = PlaybookDSL.load(
        VALID_DSL.replace("allowed_runners: [api-user]", "allowed_runners: []")
    )
    assert PlaybookPlanner(PlaybookPolicy()).plan(document, actor="api-user").steps
    restricted = PlaybookDSL.load(
        VALID_DSL.replace("allowed_runners: [api-user]", "allowed_runners: [playbook-event]")
    )
    with pytest.raises(PlaybookValidationError, match="Runner is not allowed"):
        PlaybookPlanner(PlaybookPolicy()).plan(restricted, actor="api-user")


@pytest.mark.asyncio
async def test_playbook_executor_executes_all_capability_nodes_and_conditions() -> None:
    asset_id = uuid4()
    incident_id = uuid4()
    response_id = uuid4()
    ticket_id = uuid4()

    class AssessmentStub:
        async def create(self, payload, *, trace_id):
            assert payload.execute is True and payload.asset_id == asset_id
            return SimpleNamespace(id=uuid4(), status="SUCCESS")

    class DetectionStub:
        async def create(self, payload, *, trace_id):
            assert payload.execute is True and payload.asset_id == asset_id
            return SimpleNamespace(id=uuid4(), status="SUCCESS")

    class ResponseStub:
        def __init__(self):
            self.approved = False
            self.rolled_back = False

        async def create(self, payload, *, trace_id):
            assert payload.incident_id == incident_id
            return SimpleNamespace(
                id=response_id,
                approval_state="PENDING_APPROVAL",
                execution_state="READY",
                rollback_state="AVAILABLE",
            )

        async def approve(self, plan_id, payload, *, trace_id):
            self.approved = True
            return SimpleNamespace(
                id=response_id,
                approval_state="APPROVED",
                execution_state="READY",
                rollback_state="AVAILABLE",
            )

        async def execute(self, plan_id, payload, *, trace_id):
            assert self.approved and payload.actor == "api-user"
            return SimpleNamespace(
                id=response_id,
                approval_state="EXECUTED",
                execution_state="VERIFIED",
                rollback_state="AVAILABLE",
            )

        async def rollback(self, plan_id, payload, *, trace_id):
            self.rolled_back = True
            return SimpleNamespace(rollback_state="VERIFIED")

    class NotificationStub:
        async def create(self, payload, *, trace_id):
            assert payload.incident_id == incident_id
            return SimpleNamespace(id=uuid4(), status="PLANNED")

        async def send(self, plan_id, *, actor, trace_id):
            return SimpleNamespace(id=plan_id, status="VERIFIED")

        async def create_ticket(self, payload, *, trace_id):
            return SimpleNamespace(id=ticket_id, status="OPEN")

        async def close_ticket(self, ticket_id_value, *, actor, trace_id):
            assert ticket_id_value == ticket_id
            return SimpleNamespace(status="CLOSED")

    executor = PlaybookExecutor(
        AssessmentStub(), DetectionStub(), ResponseStub(), NotificationStub()
    )
    context = {
        "asset_id": asset_id,
        "incident_id": incident_id,
        "steps": {"approval": {"approver": "soc-approver", "comment": "approved"}},
    }
    condition = PlaybookStepDefinition(id="condition", type="condition", condition="True")
    condition_result = await executor.execute(
        condition, actor="api-user", trace_id="trace", context=context, approvals={}
    )
    assert condition_result.output["matched"]
    gated = PlaybookStepDefinition(
        id="gated", type="notification", capability="notification.custom", condition="False"
    )
    gated_result = await executor.execute(
        gated, actor="api-user", trace_id="trace", context=context, approvals={}
    )
    assert gated_result.status == "SKIPPED"
    approval = PlaybookStepDefinition(id="approval", type="approval")
    waiting_result = await executor.execute(
        approval, actor="api-user", trace_id="trace", context=context, approvals={}
    )
    assert waiting_result.status == "WAITING_APPROVAL"
    assert (
        await executor.execute(
            approval,
            actor="api-user",
            trace_id="trace",
            context=context,
            approvals={"approval": PlaybookApproval(approver="soc-approver")},
        )
    ).status == "SUCCEEDED"
    assessment = PlaybookStepDefinition(
        id="assessment", type="assessment", capability="web.scan", input={"asset_id": "$asset_id"}
    )
    detection = PlaybookStepDefinition(
        id="detection",
        type="detection",
        capability="host.detect",
        input={"asset_id": "$asset_id", "log_source": "synthetic", "parser": "structured-json"},
    )
    response = PlaybookStepDefinition(
        id="response",
        type="response",
        capability="response.block",
        input={
            "incident_id": "$incident_id",
            "asset_ids": ["$asset_id"],
            "reason": "synthetic",
            "risk_level": "HIGH",
        },
    )
    notification = PlaybookStepDefinition(
        id="notification",
        type="notification",
        capability="notification.custom",
        input={
            "incident_id": "$incident_id",
            "severity": "HIGH",
            "priority": "HIGH",
            "variables": {},
        },
    )
    ticket = PlaybookStepDefinition(
        id="ticket",
        type="ticket",
        capability="notification.ticket",
        input={"title": "Synthetic ticket", "description": "Test", "priority": "HIGH"},
    )
    for step in (assessment, detection, response, notification, ticket):
        outcome = await executor.execute(
            step, actor="api-user", trace_id="trace", context=context, approvals={}
        )
        assert outcome.status == "SUCCEEDED"
    with pytest.raises(PlaybookPolicyViolation, match="missing capability"):
        await executor.execute(
            SimpleNamespace(
                id="missing",
                type=PlaybookNodeType.RESPONSE,
                capability=None,
                input={},
                condition=None,
            ),
            actor="api-user",
            trace_id="trace",
            context=context,
            approvals={},
        )
    wrong_ticket = ticket.model_copy(update={"capability": "notification.custom"})
    with pytest.raises(PlaybookPolicyViolation, match="Ticket node"):
        await executor.execute(
            wrong_ticket,
            actor="api-user",
            trace_id="trace",
            context=context,
            approvals={},
        )


@pytest.mark.asyncio
async def test_playbook_executor_compensation_and_context_errors() -> None:
    class ResponseStub:
        async def rollback(self, plan_id, payload, *, trace_id):
            assert plan_id == response_id
            return SimpleNamespace(rollback_state="VERIFIED")

    response_id = uuid4()
    ticket_id = uuid4()

    class NotificationStub:
        async def close_ticket(self, ticket_id_value, *, actor, trace_id):
            assert ticket_id_value == ticket_id
            return SimpleNamespace(status="CLOSED")

    executor = PlaybookExecutor(None, None, ResponseStub(), NotificationStub())
    response_step = PlaybookStepDefinition(
        id="response",
        type="response",
        capability="response.block",
        compensation={"type": "response", "capability": "response.rollback"},
    )
    ticket_step = PlaybookStepDefinition(
        id="ticket",
        type="ticket",
        capability="notification.ticket",
        input={"title": "t", "description": "d", "priority": "HIGH"},
        compensation={"type": "ticket", "capability": "notification.ticket"},
    )
    notify_step = PlaybookStepDefinition(
        id="notify",
        type="notification",
        capability="notification.custom",
        compensation={"type": "notification", "capability": "notification.custom"},
    )
    response_compensation = await executor.compensate(
        response_step,
        {"response_plan_id": str(response_id)},
        actor="api-user",
        trace_id="trace",
    )
    assert response_compensation["rollback_state"] == "VERIFIED"
    ticket_compensation = await executor.compensate(
        ticket_step,
        {"ticket_id": str(ticket_id)},
        actor="api-user",
        trace_id="trace",
    )
    assert ticket_compensation["ticket_status"] == "CLOSED"
    notification_compensation = await executor.compensate(
        notify_step, {}, actor="api-user", trace_id="trace"
    )
    assert notification_compensation["status"] == "IGNORED"
    undeclared = await executor.compensate(
        PlaybookStepDefinition(id="none", type="condition", condition="True"),
        {},
        actor="api-user",
        trace_id="trace",
    )
    assert undeclared["status"] == "NOT_DECLARED"
    with pytest.raises(PlaybookExecutionError, match="missing response_plan_id"):
        await executor.compensate(response_step, {}, actor="api-user", trace_id="trace")
    with pytest.raises(PlaybookExecutionError, match="Unknown Playbook context"):
        await executor.execute(
            ticket_step.model_copy(update={"input": {"title": "$missing"}}),
            actor="api-user",
            trace_id="trace",
            context={},
            approvals={},
        )
    unsupported = SimpleNamespace(
        id="bad", type=SimpleNamespace(value="delay"), capability="x", input={}, condition=None
    )
    with pytest.raises(PlaybookExecutionError, match="Unsupported Playbook node"):
        await executor.execute(
            unsupported, actor="api-user", trace_id="trace", context={}, approvals={}
        )
    unsupported_compensation = SimpleNamespace(
        id="bad-compensation",
        compensation=SimpleNamespace(type=PlaybookNodeType.ASSESSMENT),
    )
    with pytest.raises(PlaybookExecutionError, match="Unsupported compensation"):
        await executor.compensate(
            unsupported_compensation,
            {"response_plan_id": str(response_id)},
            actor="api-user",
            trace_id="trace",
        )


@pytest.mark.asyncio
async def test_playbook_registry_and_repositories_cover_persisted_resolution() -> None:
    document = PlaybookDSL.load(VALID_DSL)
    async with TestSessionFactory() as session:
        playbooks = PlaybookRepository(session)
        versions = PlaybookVersionRepository(session)
        executions = PlaybookExecutionRepository(session)
        triggers = PlaybookTriggerRepository(session)
        registry = PlaybookRegistry(versions)
        playbook = await playbooks.add(Playbook(name="registry-test", enabled=True))
        version = await versions.add(
            PlaybookVersion(
                playbook_id=playbook.id,
                version="1.0.0",
                dsl_version="v1",
                source_yaml=VALID_DSL,
                document=document.model_dump(mode="json"),
                checksum="2" * 64,
            )
        )
        assert await registry.resolve(version.id) == document
        assert await registry.resolve(version.id) == document
        latest, latest_document = await registry.latest(playbook.id)
        assert latest.id == version.id and latest_document == document
        execution = await executions.add(
            PlaybookExecution(
                playbook_id=playbook.id,
                playbook_version_id=version.id,
                trigger_type="manual",
                status="PENDING",
                actor="api-user",
                input={},
                context={},
                trace_id=str(uuid4()),
                idempotency_key="repository-idempotency",
            )
        )
        await session.commit()
        assert (await playbooks.get_by_name("registry-test")).id == playbook.id
        assert (await playbooks.list_with_versions(page=1, page_size=10)).total == 1
        assert (
            await executions.get_by_idempotency_key("repository-idempotency")
        ).id == execution.id
        assert (await executions.list_with_steps(page=1, page_size=10)).total == 1
        assert await triggers.active_for_type("manual") == []

    memory_registry = PlaybookRegistry()
    with pytest.raises(Exception, match="not found"):
        await memory_registry.resolve(uuid4())
    with pytest.raises(Exception, match="has no version"):
        await memory_registry.latest(uuid4())
    invalid = PlaybookVersion(
        playbook_id=uuid4(),
        version="1.0.0",
        dsl_version="v1",
        source_yaml=VALID_DSL,
        document={},
        checksum="3" * 64,
    )
    with pytest.raises(Exception, match="does not match"):
        await memory_registry.register(invalid)


@pytest.mark.asyncio
async def test_playbook_registry_policy_and_evaluator_remaining_edges() -> None:
    evaluator = SafeConditionEvaluator()
    assert evaluator.evaluate("('a', 'b')[0] == 'a'", {})
    assert not evaluator.evaluate("3 < 2", {})
    with pytest.raises(PlaybookValidationError, match="mapping or sequence"):
        evaluator.evaluate("value[0]", {"value": 7})

    capability_step = SimpleNamespace(
        type=PlaybookNodeType.RESPONSE,
        capability="response.block",
        retry=SimpleNamespace(max_attempts=3),
    )
    with pytest.raises(PlaybookValidationError, match="retry"):
        PlaybookPolicy(max_retry_attempts=2).validate_document(
            SimpleNamespace(max_parallel=1, timeout_seconds=10, steps=[capability_step])
        )
    with pytest.raises(PlaybookPolicyViolation, match="Capability"):
        PlaybookPolicy(allowed_capabilities=frozenset({"response.notify"})).validate_document(
            SimpleNamespace(max_parallel=1, timeout_seconds=10, steps=[capability_step])
        )
    with pytest.raises(PlaybookPolicyViolation, match="plugin"):
        PlaybookPolicy(allowed_plugins=frozenset({"approved"})).validate_document(
            SimpleNamespace(
                max_parallel=1,
                timeout_seconds=10,
                steps=[
                    SimpleNamespace(
                        type=PlaybookNodeType.CONDITION,
                        capability=None,
                        retry=SimpleNamespace(max_attempts=1),
                    )
                ],
                allowed_plugins=["forbidden"],
            )
        )

    document = PlaybookDSL.load(VALID_DSL)
    memory_registry = PlaybookRegistry()
    playbook_id = uuid4()
    version = PlaybookVersion(
        playbook_id=playbook_id,
        version="1.0.0",
        dsl_version="v1",
        source_yaml=VALID_DSL,
        document=document.model_dump(mode="json"),
        checksum="4" * 64,
    )
    await memory_registry.register(version)
    latest, resolved = await memory_registry.latest(playbook_id)
    assert latest is version and resolved == document

    class MissingVersions:
        async def get(self, version_id):
            return None

        async def latest(self, playbook_id):
            return None

    persisted_registry = PlaybookRegistry(MissingVersions())
    with pytest.raises(Exception, match="not found"):
        await persisted_registry.resolve(uuid4())
    with pytest.raises(Exception, match="has no version"):
        await persisted_registry.latest(uuid4())
    assert PlaybookExecutor._latest_approval({"steps": {"check": {"matched": True}}}) is None


@pytest.mark.asyncio
async def test_playbook_service_direct_fail_closed_and_event_paths() -> None:
    class ExecutorStub:
        async def execute(self, step, **kwargs) -> StepOutcome:
            if step.type is PlaybookNodeType.APPROVAL:
                approval = kwargs["approvals"].get(step.id)
                if approval is None:
                    return StepOutcome(status="WAITING_APPROVAL", output={"step_id": step.id})
                return StepOutcome(status="SUCCEEDED", output={"approver": approval.approver})
            return StepOutcome(status="SUCCEEDED", output={"step": step.id})

        async def compensate(self, step, output, **kwargs) -> dict[str, str]:
            return {"status": "COMPENSATED"}

    async with TestSessionFactory() as session:
        publisher = InMemoryEventBus()
        policy = PlaybookPolicy()
        executions = PlaybookExecutionRepository(session)
        registry = PlaybookRegistry(PlaybookVersionRepository(session))
        runtime = PlaybookRuntime(session, executions, ExecutorStub(), publisher, policy)
        service = PlaybookService(
            session,
            PlaybookRepository(session),
            PlaybookVersionRepository(session),
            executions,
            PlaybookTriggerRepository(session),
            registry,
            PlaybookPlanner(policy),
            runtime,
            policy,
            publisher,
        )
        with pytest.raises(PlaybookValidationError):
            await service.create(PlaybookCreate(yaml="[]"), trace_id="trace", actor="api-user")
        playbook = await service.create(
            PlaybookCreate(yaml=VALID_DSL), trace_id="trace", actor="api-user"
        )
        with pytest.raises(Exception, match="already exists"):
            await service.create(PlaybookCreate(yaml=VALID_DSL), trace_id="trace", actor="api-user")
        first = await service.run(
            playbook.id,
            PlaybookRunRequest(
                actor="api-user",
                input={"severity": "HIGH"},
                idempotency_key="direct-idempotency",
            ),
            trace_id="trace",
        )
        replay = await service.run(
            playbook.id,
            PlaybookRunRequest(
                actor="api-user",
                input={"severity": "HIGH"},
                idempotency_key="direct-idempotency",
            ),
            trace_id="trace",
        )
        assert replay.id == first.id
        another = await service.create(
            PlaybookCreate(yaml=VALID_DSL.replace("safe-manual-playbook", "another-playbook")),
            trace_id="trace",
            actor="api-user",
        )
        with pytest.raises(Exception, match="another Playbook"):
            await service.run(
                another.id,
                PlaybookRunRequest(actor="api-user", idempotency_key="direct-idempotency"),
                trace_id="trace",
            )
        with pytest.raises(PlaybookValidationError, match="trigger does not match"):
            await service.run(
                playbook.id,
                PlaybookRunRequest(actor="api-user", input={"severity": "HIGH"}),
                trace_id="trace",
                trigger_type=PlaybookTriggerType.INCIDENT_CREATED,
            )
        disabled = await service.create(
            PlaybookCreate(
                yaml=VALID_DSL.replace("safe-manual-playbook", "direct-disabled"),
                enabled=False,
            ),
            trace_id="trace",
            actor="api-user",
        )
        with pytest.raises(Exception, match="Disabled Playbooks"):
            await service.run(
                disabled.id,
                PlaybookRunRequest(actor="api-user", input={"severity": "HIGH"}),
                trace_id="trace",
            )
        with pytest.raises(PlaybookValidationError, match="distinct"):
            await service.run(
                playbook.id,
                PlaybookRunRequest(
                    actor="api-user",
                    input={"severity": "HIGH"},
                    approvals={"check": PlaybookApproval(approver="api-user")},
                ),
                trace_id="trace",
            )
        with pytest.raises(Exception, match="not found"):
            await service.get(uuid4())
        with pytest.raises(Exception, match="not found"):
            await service.get_execution(uuid4())
        waiting_playbook = await service.create(
            PlaybookCreate(
                yaml=APPROVAL_DSL.replace("approval-resume-playbook", "direct-resume-playbook")
            ),
            trace_id="trace",
            actor="api-user",
        )
        waiting = await service.run(
            waiting_playbook.id,
            PlaybookRunRequest(actor="api-user", input={"severity": "HIGH"}),
            trace_id="trace",
        )
        with pytest.raises(PlaybookValidationError, match="cannot replace"):
            await service.resume(
                waiting.id,
                PlaybookRunRequest(actor="api-user", input={"replacement": True}),
                trace_id="resume",
            )
        with pytest.raises(PlaybookValidationError, match="original runner"):
            await service.resume(
                waiting.id,
                PlaybookRunRequest(actor="different-runner"),
                trace_id="resume",
            )
        resumed = await service.resume(
            waiting.id,
            PlaybookRunRequest(
                actor="api-user",
                approvals={"approve": PlaybookApproval(approver="soc-approver")},
            ),
            trace_id="resume",
        )
        assert resumed.status == "SUCCEEDED"
        ignored = await service.handle_incident_created(
            PlatformEvent(
                type=EventType.ASSET_CREATED,
                trace_id="trace",
                aggregate_id=uuid4(),
                resource="asset",
            )
        )
        assert ignored == []
        empty = await service.handle_incident_created(
            PlatformEvent(
                type=EventType.INCIDENT_CREATED,
                trace_id="trace",
                aggregate_id=uuid4(),
                resource="incident",
                payload={"source": "API"},
            )
        )
        assert empty == []
