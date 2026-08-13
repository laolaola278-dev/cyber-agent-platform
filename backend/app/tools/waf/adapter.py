"""WAF adapter translating plugin parameters to policy-checked mock provider operations."""

from __future__ import annotations

from collections.abc import Mapping

from app.exceptions import ResponseExecutionError, ResponsePolicyViolation
from app.tools.waf.contracts import WAFRollbackAction, WAFRule, WAFRuleChange
from app.tools.waf.policy import WAFPolicyProvider
from app.tools.waf.provider import MockWAFProvider


class WAFAdapter:
    """Apply, verify and roll back only declarative WAF rules through an injected provider."""

    def __init__(self, provider: MockWAFProvider, policy: WAFPolicyProvider) -> None:
        self._provider = provider
        self._policy = policy

    @property
    def provider(self) -> MockWAFProvider:
        return self._provider

    def parse_rule(self, parameters: Mapping[str, object]) -> WAFRule:
        raw = parameters.get("rule")
        if not isinstance(raw, Mapping):
            raise ResponsePolicyViolation("WAF response requires a declarative rule mapping")
        try:
            return WAFRule.model_validate(dict(raw))
        except ValueError as error:
            raise ResponsePolicyViolation("WAF response rule is invalid") from error

    async def apply(self, rule: WAFRule, *, approval_required: bool) -> WAFRuleChange:
        self._policy.validate_rule(rule, approval_required=approval_required)
        return await self._provider.apply(rule)

    async def verify_applied(self, rule: WAFRule) -> bool:
        actual = await self._provider.get(rule.id)
        return actual == rule and actual.status.value == "ENABLED"

    async def rollback(
        self,
        *,
        rule_id: str,
        action: WAFRollbackAction,
        original_rule: WAFRule | None,
    ) -> WAFRuleChange:
        self._policy.validate_rollback(action)
        return await self._provider.rollback(
            rule_id=rule_id,
            action=action,
            original_rule=original_rule,
        )

    async def verify_rollback(
        self,
        *,
        rule_id: str,
        action: WAFRollbackAction,
        original_rule: WAFRule | None,
    ) -> bool:
        actual = await self._provider.get(rule_id)
        if actual is None:
            return False
        if action is WAFRollbackAction.REMOVE:
            return actual.status.value == "REMOVED"
        if action is WAFRollbackAction.DISABLE:
            return actual.status.value == "DISABLED"
        if action is WAFRollbackAction.RESTORE:
            return original_rule is not None and actual == original_rule
        raise ResponseExecutionError("Unsupported WAF rollback verification action")

    def parse_rollback_action(self, parameters: Mapping[str, object]) -> WAFRollbackAction:
        raw = parameters.get("action", WAFRollbackAction.DISABLE.value)
        if not isinstance(raw, str):
            raise ResponsePolicyViolation("WAF rollback action must be a string")
        try:
            return WAFRollbackAction(raw.strip().upper())
        except ValueError as error:
            raise ResponsePolicyViolation("Unsupported WAF rollback action") from error
