"""Synthetic in-memory firewall provider with no network or production access."""

from __future__ import annotations

from collections.abc import Mapping

from app.exceptions import ResponseExecutionError, ResponsePolicyViolation
from app.tools.firewall.contracts import (
    FirewallRollbackAction,
    FirewallRule,
    FirewallRuleChange,
    FirewallRuleStatus,
)


class MockFirewallProvider:
    """Deterministic app-local state store used for Phase 17 verification."""

    provider_name = "mock-firewall"
    network_access = False
    production_access = False

    def __init__(self) -> None:
        self._rules: dict[str, FirewallRule] = {}

    async def apply(self, rule: FirewallRule) -> FirewallRuleChange:
        self._require_enabled(rule)
        existing = self._rules.get(rule.id)
        if (
            existing
            and existing.checksum != rule.checksum
            and existing.status is FirewallRuleStatus.ENABLED
        ):
            raise ResponsePolicyViolation(
                "Enabled Firewall rule cannot be replaced without rollback"
            )
        changed = existing != rule
        self._rules[rule.id] = rule
        return self._change("APPLY", rule, changed)

    async def get(self, rule_id: str) -> FirewallRule | None:
        return self._rules.get(rule_id)

    async def rollback(
        self,
        *,
        rule_id: str,
        action: FirewallRollbackAction,
        original_rule: FirewallRule | None,
    ) -> FirewallRuleChange:
        existing = self._rules.get(rule_id)
        if action is FirewallRollbackAction.REMOVE:
            if existing is None:
                raise ResponseExecutionError("Firewall rule is unavailable for removal")
            removed = existing.model_copy(update={"status": FirewallRuleStatus.REMOVED})
            self._rules[rule_id] = removed
            return self._change(
                "REMOVE", removed, existing.status is not FirewallRuleStatus.REMOVED
            )
        if action is FirewallRollbackAction.DISABLE:
            if existing is None:
                raise ResponseExecutionError("Firewall rule is unavailable for disable")
            disabled = existing.model_copy(update={"status": FirewallRuleStatus.DISABLED})
            self._rules[rule_id] = disabled
            return self._change(
                "DISABLE", disabled, existing.status is not FirewallRuleStatus.DISABLED
            )
        if action is FirewallRollbackAction.RESTORE:
            if original_rule is None:
                raise ResponseExecutionError(
                    "Firewall restore requires the original validated rule"
                )
            restored = original_rule.model_copy(update={"status": FirewallRuleStatus.ENABLED})
            changed = existing != restored
            self._rules[rule_id] = restored
            return self._change("RESTORE", restored, changed)
        raise ResponsePolicyViolation("Unsupported Firewall rollback action")

    async def snapshot(self) -> Mapping[str, FirewallRule]:
        return dict(self._rules)

    def _change(self, operation: str, rule: FirewallRule, changed: bool) -> FirewallRuleChange:
        return FirewallRuleChange(
            operation=operation,
            rule=rule,
            provider_reference=(
                f"mock-firewall://tables/{rule.table}/chains/{rule.chain}/"
                f"rules/{rule.id}/{rule.version}"
            ),
            changed=changed,
            metadata={
                "provider": self.provider_name,
                "network_access": self.network_access,
                "production_access": self.production_access,
                "desired_state": rule.status.value,
            },
        )

    @staticmethod
    def _require_enabled(rule: FirewallRule) -> None:
        if rule.status is not FirewallRuleStatus.ENABLED:
            raise ResponsePolicyViolation("Only enabled Firewall rules may be applied")
