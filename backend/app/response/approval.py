"""Platform approval service for Response Plans."""

from datetime import UTC, datetime, timedelta

from app.exceptions import ResponseConflict, ResponsePolicyViolation
from app.models.response import ResponseApproval, ResponsePlan
from app.schemas.response import (
    ApprovalState,
    ResponseApprovalCreate,
    ResponsePolicy,
    ResponseRejectionCreate,
)


class ApprovalService:
    """Own Response Plan approval decisions independently of plugins."""

    def approve(
        self,
        plan: ResponsePlan,
        payload: ResponseApprovalCreate,
        policy: ResponsePolicy,
    ) -> ResponseApproval:
        now = datetime.now(UTC)
        self._expire_if_needed(plan, now)
        self._require_pending(plan)
        if policy.require_distinct_approver and payload.approver == plan.requested_by:
            raise ResponsePolicyViolation("Requester cannot approve their own Response Plan")
        if any(
            item.approver == payload.approver and item.decision == "APPROVED"
            for item in plan.approvals
        ):
            raise ResponseConflict("Approver has already approved this Response Plan")
        approval = ResponseApproval(
            plan_id=plan.id,
            approver=payload.approver,
            decision="APPROVED",
            comment=payload.comment,
            approval_level=payload.level,
            decided_at=now,
            expires_at=min(
                self._as_utc(plan.expires_at),
                now + timedelta(seconds=policy.approval_ttl_seconds),
            ),
        )
        approved_levels = {
            item.approval_level for item in plan.approvals if item.decision == "APPROVED"
        } | {payload.level}
        if len(approved_levels) >= policy.required_approval_levels:
            plan.approval_state = ApprovalState.APPROVED.value
            plan.execution_state = "READY"
        return approval

    def reject(self, plan: ResponsePlan, payload: ResponseRejectionCreate) -> ResponseApproval:
        now = datetime.now(UTC)
        self._expire_if_needed(plan, now)
        self._require_pending(plan)
        plan.approval_state = ApprovalState.REJECTED.value
        plan.execution_state = "BLOCKED"
        return ResponseApproval(
            plan_id=plan.id,
            approver=payload.approver,
            decision="REJECTED",
            comment=payload.comment,
            approval_level=1,
            decided_at=now,
            expires_at=plan.expires_at,
        )

    @staticmethod
    def _require_pending(plan: ResponsePlan) -> None:
        if plan.approval_state != ApprovalState.PENDING_APPROVAL.value:
            raise ResponseConflict("Response Plan is not pending approval")

    @staticmethod
    def _expire_if_needed(plan: ResponsePlan, now: datetime) -> None:
        if ApprovalService._as_utc(plan.expires_at) <= now:
            plan.approval_state = ApprovalState.EXPIRED.value
            plan.execution_state = "BLOCKED"
            raise ResponsePolicyViolation("Response Plan approval has expired")

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
