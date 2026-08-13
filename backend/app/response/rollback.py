"""Independent rollback orchestration service."""

from dataclasses import replace
from datetime import UTC, datetime

from app.exceptions import ResponseConflict, ResponsePolicyViolation
from app.models.response import ResponseExecution, ResponsePlan, ResponseRollback
from app.response.contracts import ResponsePluginContext
from app.response.runtime import ResponseRuntime
from app.schemas.response import (
    ApprovalState,
    ResponsePlanSpec,
    ResponsePolicy,
    ResponseRollbackRequest,
    RollbackState,
)


class RollbackService:
    """Own rollback authorization, execution record and verification state."""

    async def rollback(
        self,
        *,
        plan: ResponsePlan,
        execution: ResponseExecution,
        specification: ResponsePlanSpec,
        context: ResponsePluginContext,
        policy: ResponsePolicy,
        payload: ResponseRollbackRequest,
        runtime: ResponseRuntime,
    ) -> tuple[ResponseRollback, object]:
        if not plan.supports_rollback:
            raise ResponsePolicyViolation("Response Plan does not support rollback")
        if plan.rollback_state not in {RollbackState.AVAILABLE.value, RollbackState.FAILED.value}:
            raise ResponseConflict("Response Plan is not rollback eligible")
        if not execution.rollback_token:
            raise ResponsePolicyViolation("Response execution has no rollback token")
        started = datetime.now(UTC)
        rollback = ResponseRollback(
            plan_id=plan.id,
            execution_id=execution.id,
            actor=payload.actor,
            reason=payload.reason,
            status=RollbackState.RUNNING.value,
            verification_status="PENDING",
            result={},
            started_at=started,
        )
        plan.rollback_state = RollbackState.RUNNING.value
        rollback_context = replace(
            context,
            actor=payload.actor,
            rollback_token=execution.rollback_token,
        )
        result = await runtime.rollback(specification, rollback_context, policy)
        rollback.status = (
            RollbackState.SUCCEEDED.value if result.success else RollbackState.FAILED.value
        )
        rollback.verification_status = result.verification.status
        rollback.result = result.model_dump(mode="json", exclude={"rollback_token"})
        rollback.finished_at = datetime.now(UTC)
        if result.success and result.verification.verified:
            plan.rollback_state = RollbackState.VERIFIED.value
            plan.approval_state = ApprovalState.ROLLED_BACK.value
        else:
            plan.rollback_state = RollbackState.FAILED.value
        return rollback, result
