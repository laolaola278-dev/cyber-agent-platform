"""Firewall adapter translating plugin parameters to governed provider operations."""

from __future__ import annotations

from collections.abc import Mapping

from app.exceptions import ResponseExecutionError, ResponsePolicyViolation
from app.tools.firewall.contracts import (
    FirewallRollbackAction,
    FirewallRule,
    FirewallRuleChange,
)
from app.tools.firewall.policy import FirewallPolicyProvider
from app.tools.firewall.provider import MockFirewallProvider


class FirewallAdapter:
    """Apply, verify and roll back only policy-approved declarative rules."""

    def __init__(self, provider: MockFirewallProvider, policy: FirewallPolicyProvider) -> None:
        self._provider = provider
        self._policy = policy

    @property
    def provider(self) -> MockFirewallProvider:
        return self._provider

    def parse_rule(self, parameters: Mapping[str, object]) -> FirewallRule:
        raw = parameters.get("rule")
        if not isinstance(raw, Mapping):
            raise ResponsePolicyViolation("Firewall response requires a declarative rule mapping")
        try:
            return FirewallRule.model_validate(dict(raw))
        except ValueError as error:
            raise ResponsePolicyViolation("Firewall response rule is invalid") from error

    async def apply(self, rule: FirewallRule, *, approval_required: bool) -> FirewallRuleChange:
        self._policy.validate_rule(rule, approval_required=approval_required)
        return await self._provider.apply(rule)

    async def verify_applied(self, rule: FirewallRule) -> bool:
        actual = await self._provider.get(rule.id)
        return actual == rule and actual.status.value == "ENABLED"

    async def rollback(
        self,
        *,
        rule_id: str,
        action: FirewallRollbackAction,
        original_rule: FirewallRule | None,
    ) -> FirewallRuleChange:
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
        action: FirewallRollbackAction,
        original_rule: FirewallRule | None,
    ) -> bool:
        actual = await self._provider.get(rule_id)
        if actual is None:
            return False
        if action is FirewallRollbackAction.REMOVE:
            return actual.status.value == "REMOVED"
        if action is FirewallRollbackAction.DISABLE:
            return actual.status.value == "DISABLED"
        if action is FirewallRollbackAction.RESTORE:
            return original_rule is not None and actual == original_rule
        raise ResponseExecutionError("Unsupported Firewall rollback verification action")

    def parse_rollback_action(self, parameters: Mapping[str, object]) -> FirewallRollbackAction:
        raw = parameters.get("action", FirewallRollbackAction.DISABLE.value)
        if not isinstance(raw, str):
            raise ResponsePolicyViolation("Firewall rollback action must be a string")
        try:
            return FirewallRollbackAction(raw.strip().upper())
        except ValueError as error:
            raise ResponsePolicyViolation("Unsupported Firewall rollback action") from error
