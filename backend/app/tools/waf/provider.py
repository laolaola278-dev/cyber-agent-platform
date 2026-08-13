"""Synthetic in-memory WAF provider; it cannot access a real WAF or network."""

from __future__ import annotations

from collections.abc import Mapping

from app.exceptions import ResponseExecutionError, ResponsePolicyViolation
from app.tools.waf.contracts import (
    WAFRollbackAction,
    WAFRule,
    WAFRuleChange,
    WAFRuleStatus,
)


class MockWAFProvider:
    """Deterministic state store used exclusively for Phase 16 lifecycle verification."""

    provider_name = "mock-waf"
    network_access = False
    production_access = False

    def __init__(self) -> None:
        self._rules: dict[str, WAFRule] = {}

    async def apply(self, rule: WAFRule) -> WAFRuleChange:
        """Create or replace an enabled rule without external side effects."""

        self._require_enabled(rule)
        existing = self._rules.get(rule.id)
        if (
            existing
            and existing.checksum != rule.checksum
            and existing.status is WAFRuleStatus.ENABLED
        ):
            raise ResponsePolicyViolation("Enabled WAF rule cannot be replaced without rollback")
        changed = existing != rule
        self._rules[rule.id] = rule
        return self._change("APPLY", rule, changed)

    async def get(self, rule_id: str) -> WAFRule | None:
        return self._rules.get(rule_id)

    async def rollback(
        self,
        *,
        rule_id: str,
        action: WAFRollbackAction,
        original_rule: WAFRule | None,
    ) -> WAFRuleChange:
        """Perform only declared reversible state transitions in the mock store."""

        existing = self._rules.get(rule_id)
        if action is WAFRollbackAction.REMOVE:
            if existing is None:
                raise ResponseExecutionError("WAF rule is unavailable for removal")
            removed = existing.model_copy(update={"status": WAFRuleStatus.REMOVED})
            self._rules[rule_id] = removed
            return self._change("REMOVE", removed, existing.status is not WAFRuleStatus.REMOVED)
        if action is WAFRollbackAction.DISABLE:
            if existing is None:
                raise ResponseExecutionError("WAF rule is unavailable for disable")
            disabled = existing.model_copy(update={"status": WAFRuleStatus.DISABLED})
            self._rules[rule_id] = disabled
            return self._change("DISABLE", disabled, existing.status is not WAFRuleStatus.DISABLED)
        if action is WAFRollbackAction.RESTORE:
            if original_rule is None:
                raise ResponseExecutionError("WAF restore requires the original validated rule")
            restored = original_rule.model_copy(update={"status": WAFRuleStatus.ENABLED})
            changed = existing != restored
            self._rules[rule_id] = restored
            return self._change("RESTORE", restored, changed)
        raise ResponsePolicyViolation("Unsupported WAF rollback action")

    async def snapshot(self) -> Mapping[str, WAFRule]:
        """Return a copy for deterministic evidence assertions."""

        return dict(self._rules)

    def _change(self, operation: str, rule: WAFRule, changed: bool) -> WAFRuleChange:
        return WAFRuleChange(
            operation=operation,
            rule=rule,
            provider_reference=f"mock-waf://rules/{rule.id}/{rule.version}",
            changed=changed,
            metadata={
                "provider": self.provider_name,
                "network_access": self.network_access,
                "production_access": self.production_access,
            },
        )

    @staticmethod
    def _require_enabled(rule: WAFRule) -> None:
        if rule.status is not WAFRuleStatus.ENABLED:
            raise ResponsePolicyViolation("Only enabled WAF rules may be applied")
