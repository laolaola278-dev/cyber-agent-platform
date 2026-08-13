"""Capability-only Playbook node executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.assessment.service import AssessmentService
from app.detection.service import DetectionService
from app.exceptions import PlaybookExecutionError, PlaybookPolicyViolation
from app.notification.service import NotificationService
from app.playbook.contracts import PlaybookApproval, PlaybookNodeType, PlaybookStepDefinition
from app.playbook.planner import SafeConditionEvaluator
from app.response.service import ResponseService
from app.schemas.assessment import AssessmentTaskCreate
from app.schemas.detection import DetectionTaskCreate
from app.schemas.notification import NotificationCreate, TicketCreate
from app.schemas.response import (
    ResponseApprovalCreate,
    ResponseExecutionRequest,
    ResponsePlanCreate,
    ResponseRollbackRequest,
)


@dataclass(frozen=True, slots=True)
class StepOutcome:
    status: str
    output: dict[str, Any]


class PlaybookExecutor:
    """Translate typed nodes into existing domain-service calls only."""

    def __init__(
        self,
        assessment: AssessmentService,
        detection: DetectionService,
        response: ResponseService,
        notification: NotificationService,
        condition_evaluator: SafeConditionEvaluator | None = None,
    ) -> None:
        self._assessment = assessment
        self._detection = detection
        self._response = response
        self._notification = notification
        self._conditions = condition_evaluator or SafeConditionEvaluator()

    async def execute(
        self,
        step: PlaybookStepDefinition,
        *,
        actor: str,
        trace_id: str,
        context: dict[str, Any],
        approvals: dict[str, PlaybookApproval],
    ) -> StepOutcome:
        if step.condition and step.type is not PlaybookNodeType.CONDITION:
            if not self._conditions.evaluate(step.condition, context):
                return StepOutcome(status="SKIPPED", output={"reason": "condition-false"})
        data = self._resolve(step.input, context)
        if step.type is PlaybookNodeType.CONDITION:
            matched = self._conditions.evaluate(step.condition or "False", context)
            return StepOutcome(status="SUCCEEDED", output={"matched": matched})
        if step.type is PlaybookNodeType.APPROVAL:
            approval = approvals.get(step.id)
            if approval is None:
                return StepOutcome(status="WAITING_APPROVAL", output={"step_id": step.id})
            return StepOutcome(
                status="SUCCEEDED",
                output={"approver": approval.approver, "comment": approval.comment},
            )
        if step.capability is None:
            raise PlaybookPolicyViolation("Capability node is missing capability")
        if step.type is PlaybookNodeType.ASSESSMENT:
            payload = AssessmentTaskCreate.model_validate(
                {
                    **data,
                    "capabilities": [step.capability],
                    "execute": True,
                    "name": data.get("name", f"Playbook assessment: {step.id}"),
                }
            )
            result = await self._assessment.create(payload, trace_id=trace_id)
            return StepOutcome(
                status="SUCCEEDED",
                output={"assessment_task_id": str(result.id), "status": result.status},
            )
        if step.type is PlaybookNodeType.DETECTION:
            payload = DetectionTaskCreate.model_validate(
                {
                    **data,
                    "capabilities": [step.capability],
                    "execute": True,
                    "name": data.get("name", f"Playbook detection: {step.id}"),
                }
            )
            result = await self._detection.create(payload, trace_id=trace_id)
            return StepOutcome(
                status="SUCCEEDED",
                output={"detection_task_id": str(result.id), "status": result.status},
            )
        if step.type is PlaybookNodeType.RESPONSE:
            payload = ResponsePlanCreate.model_validate(
                {
                    **data,
                    "target_capability": step.capability,
                    "requested_by": data.get("requested_by", actor),
                }
            )
            plan = await self._response.create(payload, trace_id=trace_id)
            approval = self._latest_approval(context)
            if plan.approval_state == "PENDING_APPROVAL":
                if approval is None:
                    raise PlaybookPolicyViolation(
                        "Response requires a completed Playbook approval step"
                    )
                plan = await self._response.approve(
                    plan.id,
                    ResponseApprovalCreate(
                        approver=approval["approver"],
                        comment=approval.get("comment", ""),
                    ),
                    trace_id=trace_id,
                )
            plan = await self._response.execute(
                plan.id,
                ResponseExecutionRequest(actor=actor),
                trace_id=trace_id,
            )
            return StepOutcome(
                status="SUCCEEDED",
                output={
                    "response_plan_id": str(plan.id),
                    "approval_state": plan.approval_state,
                    "execution_state": plan.execution_state,
                },
            )
        if step.type is PlaybookNodeType.NOTIFICATION:
            payload = NotificationCreate.model_validate(
                {
                    **data,
                    "capability": step.capability,
                    "requested_by": data.get("requested_by", actor),
                }
            )
            plan = await self._notification.create(payload, trace_id=trace_id)
            plan = await self._notification.send(plan.id, actor=actor, trace_id=trace_id)
            return StepOutcome(
                status="SUCCEEDED",
                output={"notification_plan_id": str(plan.id), "status": plan.status},
            )
        if step.type is PlaybookNodeType.TICKET:
            if step.capability != "notification.ticket":
                raise PlaybookPolicyViolation("Ticket node requires notification.ticket capability")
            ticket = await self._notification.create_ticket(
                TicketCreate.model_validate({**data, "created_by": data.get("created_by", actor)}),
                trace_id=trace_id,
            )
            return StepOutcome(
                status="SUCCEEDED",
                output={"ticket_id": str(ticket.id), "status": ticket.status},
            )
        raise PlaybookExecutionError(f"Unsupported Playbook node: {step.type.value}")

    async def compensate(
        self,
        step: PlaybookStepDefinition,
        output: dict[str, Any],
        *,
        actor: str,
        trace_id: str,
    ) -> dict[str, Any]:
        compensation = step.compensation
        if compensation is None:
            return {"status": "NOT_DECLARED"}
        if compensation.type is PlaybookNodeType.RESPONSE:
            plan_id = self._uuid(output, "response_plan_id")
            plan = await self._response.rollback(
                plan_id,
                ResponseRollbackRequest(actor=actor, reason="Playbook compensation"),
                trace_id=trace_id,
            )
            return {"status": "COMPENSATED", "rollback_state": plan.rollback_state}
        if compensation.type is PlaybookNodeType.NOTIFICATION:
            return {"status": "IGNORED", "reason": "notification compensation policy"}
        if compensation.type is PlaybookNodeType.TICKET:
            ticket_id = self._uuid(output, "ticket_id")
            ticket = await self._notification.close_ticket(
                ticket_id, actor=actor, trace_id=trace_id
            )
            return {"status": "COMPENSATED", "ticket_status": ticket.status}
        raise PlaybookExecutionError("Unsupported compensation")

    @classmethod
    def _resolve(cls, value: Any, context: dict[str, Any]) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            current: Any = context
            for segment in value[1:].split("."):
                if not isinstance(current, dict) or segment not in current:
                    raise PlaybookExecutionError(f"Unknown Playbook context reference: {value}")
                current = current[segment]
            return current
        if isinstance(value, dict):
            return {key: cls._resolve(item, context) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._resolve(item, context) for item in value]
        return value

    @staticmethod
    def _latest_approval(context: dict[str, Any]) -> dict[str, Any] | None:
        steps = context.get("steps", {})
        for output in reversed(list(steps.values())):
            if isinstance(output, dict) and "approver" in output:
                return output
        return None

    @staticmethod
    def _uuid(output: dict[str, Any], key: str) -> UUID:
        value = output.get(key)
        if value is None:
            raise PlaybookExecutionError(f"Compensation output is missing {key}")
        return UUID(str(value))
