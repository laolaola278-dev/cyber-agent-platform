"""Fail-closed authorization policy for provider-neutral EDR host actions."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.exceptions import ResponsePolicyViolation
from app.tools.edr.contracts import EDRAction, HostAction, HostActionStatus


class EDRPolicy(BaseModel):
    """Phase 19 permits only mock host isolation lifecycle actions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    mock_only: bool = True
    allowed_actions: frozenset[EDRAction] = frozenset(
        {EDRAction.HOST_ISOLATE, EDRAction.HOST_UNISOLATE}
    )
    reserved_actions: frozenset[EDRAction] = frozenset(
        {EDRAction.PROCESS_TERMINATE, EDRAction.COLLECT_PACKAGE}
    )
    require_approval: bool = True
    require_distinct_approver: bool = True

    @field_validator("allowed_actions")
    @classmethod
    def require_allowed_actions(cls, value: frozenset[EDRAction]) -> frozenset[EDRAction]:
        if not value:
            raise ValueError("EDR action allowlist cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_safety_mode(self) -> EDRPolicy:
        if not self.mock_only:
            raise ValueError("Phase 19 EDR policy must remain mock-only")
        if self.allowed_actions & self.reserved_actions:
            raise ValueError("Reserved EDR actions cannot be executable")
        if not self.require_approval or not self.require_distinct_approver:
            raise ValueError("EDR approval safeguards cannot be disabled")
        return self


class EDRPolicyProvider:
    """Authorize structured actions before the Provider connection boundary."""

    def __init__(self, policy: EDRPolicy | None = None) -> None:
        self._policy = policy or EDRPolicy()

    @property
    def policy(self) -> EDRPolicy:
        return self._policy

    def validate_action(self, action: HostAction, *, approval_required: bool) -> None:
        policy = self._policy
        if not policy.enabled:
            raise ResponsePolicyViolation("EDR response policy is disabled")
        if action.action in policy.reserved_actions:
            raise ResponsePolicyViolation("EDR action is reserved and not implemented in Phase 19")
        if action.action not in policy.allowed_actions:
            raise ResponsePolicyViolation("EDR action is not allowlisted")
        if action.status is not HostActionStatus.REQUESTED:
            raise ResponsePolicyViolation("EDR desired action must be in REQUESTED state")
        if policy.require_approval and not approval_required:
            raise ResponsePolicyViolation("EDR host actions require governed approval")
        if action.approved_by is not None:
            raise ResponsePolicyViolation(
                "EDR approved_by is platform-owned and cannot be supplied by the requester"
            )

    def validate_rollback(self, original: HostAction, rollback: HostAction) -> None:
        if original.host_id != rollback.host_id:
            raise ResponsePolicyViolation("EDR rollback cannot change the target host")
        expected = {
            EDRAction.HOST_ISOLATE: EDRAction.HOST_UNISOLATE,
            EDRAction.HOST_UNISOLATE: EDRAction.HOST_ISOLATE,
        }.get(original.action)
        if expected is None or rollback.action is not expected:
            raise ResponsePolicyViolation("EDR rollback must use the inverse host isolation action")
