"""Configuration-first safety policy for declarative WAF response rules."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.exceptions import ResponsePolicyViolation
from app.tools.waf.contracts import WAFRollbackAction, WAFRule, WAFRuleAction


class WAFPolicy(BaseModel):
    """Allowlist-based policy applied before the adapter touches a provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    mock_only: bool = True
    allowed_actions: frozenset[WAFRuleAction] = frozenset({WAFRuleAction.BLOCK, WAFRuleAction.LOG})
    allowed_condition_fields: frozenset[str] = frozenset(
        {"client_ip", "http_method", "path_prefix", "header", "query_parameter"}
    )
    allowed_sources: frozenset[str] = frozenset({"cap", "incident", "assessment"})
    allowed_rollback_actions: frozenset[WAFRollbackAction] = frozenset(WAFRollbackAction)
    maximum_priority: int = Field(default=10_000, ge=1, le=100_000)
    require_block_approval: bool = True

    @field_validator(
        "allowed_actions", "allowed_condition_fields", "allowed_sources", "allowed_rollback_actions"
    )
    @classmethod
    def require_nonempty_allowlist(cls, value: frozenset[object]) -> frozenset[object]:
        if not value:
            raise ValueError("WAF policy allowlists cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_safety_mode(self) -> WAFPolicy:
        if not self.mock_only:
            raise ValueError("Phase 16 WAF policy must remain mock-only")
        if WAFRuleAction.ALLOW in self.allowed_actions:
            raise ValueError("Broad allow rules are not permitted in Phase 16")
        return self


class WAFPolicyProvider:
    """Validate rules and rollback requests against an immutable policy snapshot."""

    def __init__(self, policy: WAFPolicy | None = None) -> None:
        self._policy = policy or WAFPolicy()

    @property
    def policy(self) -> WAFPolicy:
        return self._policy

    def validate_rule(self, rule: WAFRule, *, approval_required: bool) -> None:
        if not self._policy.enabled:
            raise ResponsePolicyViolation("WAF response policy is disabled")
        if rule.action not in self._policy.allowed_actions:
            raise ResponsePolicyViolation("WAF rule action is not allowlisted")
        if rule.source.casefold() not in self._policy.allowed_sources:
            raise ResponsePolicyViolation("WAF rule source is not allowlisted")
        if rule.priority > self._policy.maximum_priority:
            raise ResponsePolicyViolation("WAF rule priority exceeds the policy limit")
        field, separator, operand = rule.condition.partition(":")
        if not separator or not operand.strip():
            raise ResponsePolicyViolation("WAF rule condition must use field:value syntax")
        if field.strip().casefold() not in self._policy.allowed_condition_fields:
            raise ResponsePolicyViolation("WAF rule condition field is not allowlisted")
        if rule.action is WAFRuleAction.BLOCK and self._policy.require_block_approval:
            if not approval_required:
                raise ResponsePolicyViolation("Blocking WAF rules require governed approval")

    def validate_rollback(self, action: WAFRollbackAction) -> None:
        if action not in self._policy.allowed_rollback_actions:
            raise ResponsePolicyViolation("WAF rollback action is not allowlisted")
