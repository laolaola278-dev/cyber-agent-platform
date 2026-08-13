"""Mock-only WAF Response Plugin implementing the existing Response SDK lifecycle."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from time import monotonic

from app.exceptions import ResponseExecutionError, ResponsePolicyViolation
from app.response.contracts import ResponsePluginContext
from app.schemas.response import (
    ResponseEvidenceItem,
    ResponsePlanSpec,
    ResponseResult,
    ResponseVerification,
)
from app.tools.waf import WAFAdapter, WAFRollbackAction, WAFRule, WAFRuleChange


class WAFResponsePlugin:
    """Governed WAF integration backed exclusively by a synthetic provider."""

    name = "waf-response"
    version = "1.0.0"
    description = "Mock-only declarative WAF rule response plugin"
    capabilities = frozenset({"response.waf"})
    permissions = frozenset({"response.execute", "response.verify", "response.rollback"})
    supports_approval = True
    supports_rollback = True
    sandbox_compatible = True
    operational_documentation = "plugins/response/waf/README.md"

    def __init__(self, adapter: WAFAdapter) -> None:
        self._adapter = adapter
        self._context: ResponsePluginContext | None = None
        self._pending_verification: tuple[WAFRuleChange, WAFRollbackAction | None] | None = None

    async def initialize(self, context: ResponsePluginContext) -> None:
        if context.granted_permissions != self.permissions:
            raise ResponsePolicyViolation("WAF plugin requires its certified permission set")
        if context.capability != "response.waf":
            raise ResponsePolicyViolation("WAF plugin accepts only response.waf plans")
        self._context = context
        self._pending_verification = None

    async def plan(
        self, plan: ResponsePlanSpec, context: ResponsePluginContext
    ) -> ResponsePlanSpec:
        self._require_context(context)
        rule = self._adapter.parse_rule(context.parameters)
        return plan.model_copy(
            deep=True,
            update={
                "parameters": {"rule": rule.model_dump(mode="json")},
            },
        )

    async def validate(self, plan: ResponsePlanSpec, context: ResponsePluginContext) -> None:
        self._require_context(context)
        if plan.target_capability != "response.waf":
            raise ResponsePolicyViolation("WAF plan capability is invalid")
        if plan.incident_id != context.incident_id or tuple(plan.asset_ids) != context.asset_ids:
            raise ResponsePolicyViolation("WAF plan scope does not match immutable context")
        rule = self._adapter.parse_rule(plan.parameters)
        self._adapter.parse_rollback_action(context.rollback_parameters)
        if not plan.approval_required:
            raise ResponsePolicyViolation("WAF rule changes require governed approval")
        if rule.id.casefold().startswith(("system-", "provider-")):
            raise ResponsePolicyViolation("Provider-owned WAF rules cannot be modified")

    async def execute(
        self, plan: ResponsePlanSpec, context: ResponsePluginContext
    ) -> ResponseResult:
        self._require_context(context)
        started = monotonic()
        rule = self._adapter.parse_rule(plan.parameters)
        change = await self._adapter.apply(rule, approval_required=plan.approval_required)
        self._pending_verification = (change, None)
        return self._result(
            plan,
            change,
            duration_ms=self._duration_ms(started),
            rollback_token=self._rollback_token(context, rule),
        )

    async def verify(
        self, result: ResponseResult, context: ResponsePluginContext
    ) -> ResponseResult:
        self._require_context(context)
        if self._pending_verification is None:
            raise ResponseExecutionError("WAF verification has no pending provider operation")
        change, rollback_action = self._pending_verification
        if rollback_action is None:
            verified = await self._adapter.verify_applied(change.rule)
        else:
            original = self._adapter.parse_rule(context.parameters)
            verified = await self._adapter.verify_rollback(
                rule_id=change.rule.id,
                action=rollback_action,
                original_rule=original,
            )
        return result.model_copy(
            update={
                "success": result.success and verified,
                "verification": ResponseVerification(
                    verified=verified,
                    status="VERIFIED" if verified else "FAILED",
                    details={
                        "provider_reference": change.provider_reference,
                        "operation": change.operation,
                        "rule_id": change.rule.id,
                        "rule_checksum": change.rule.checksum,
                        "network_access": False,
                        "production_access": False,
                    },
                ),
            }
        )

    async def rollback(
        self, plan: ResponsePlanSpec, context: ResponsePluginContext
    ) -> ResponseResult:
        self._require_context(context)
        if not context.rollback_token or not self._valid_rollback_token(context):
            raise ResponsePolicyViolation("Validated WAF rollback token is required")
        started = monotonic()
        rule = self._adapter.parse_rule(plan.parameters)
        action = self._adapter.parse_rollback_action(context.rollback_parameters)
        change = await self._adapter.rollback(
            rule_id=rule.id,
            action=action,
            original_rule=rule if action is WAFRollbackAction.RESTORE else None,
        )
        self._pending_verification = (change, action)
        return self._result(
            plan, change, duration_ms=self._duration_ms(started), rollback_token=None
        )

    async def shutdown(self) -> None:
        self._context = None
        self._pending_verification = None

    async def health(self) -> bool:
        return (
            not self._adapter.provider.network_access
            and not self._adapter.provider.production_access
        )

    def _result(
        self,
        plan: ResponsePlanSpec,
        change: WAFRuleChange,
        *,
        duration_ms: int,
        rollback_token: str | None,
    ) -> ResponseResult:
        metadata = {
            "operation": change.operation,
            "rule": change.rule.model_dump(mode="json"),
            "provider_reference": change.provider_reference,
            "changed": change.changed,
            **change.metadata,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return ResponseResult(
            success=True,
            plugin_name=self.name,
            plugin_version=self.version,
            capability=plan.target_capability,
            execution_status=(
                "ROLLED_BACK"
                if change.operation in {"REMOVE", "DISABLE", "RESTORE"}
                else "EXECUTED"
            ),
            verification=ResponseVerification(verified=False, status="PENDING"),
            evidence=[
                ResponseEvidenceItem(
                    evidence_type="WAF_RULE_CHANGE",
                    sha256=hashlib.sha256(encoded).hexdigest(),
                    reference=change.provider_reference,
                    metadata=metadata,
                )
            ],
            duration_ms=duration_ms,
            message=f"Mock WAF rule operation {change.operation} completed",
            rollback_supported=True,
            rollback_token=rollback_token,
            metadata={
                "provider": "mock-waf",
                "network_access": False,
                "production_access": False,
                "impact_scope": [str(item) for item in plan.asset_ids],
            },
        )

    def _rollback_token(self, context: ResponsePluginContext, rule: WAFRule) -> str:
        material = (
            f"{context.response_plan_id}:{context.incident_id}:{rule.id}:"
            f"{rule.version}:{rule.checksum}"
        )
        return f"waf-rb:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"

    def _valid_rollback_token(self, context: ResponsePluginContext) -> bool:
        rule = self._adapter.parse_rule(context.parameters)
        return context.rollback_token == self._rollback_token(context, rule)

    def _require_context(self, context: ResponsePluginContext) -> None:
        if self._context != context:
            raise ResponseExecutionError("WAF plugin was not initialized for this context")

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, int((monotonic() - started) * 1_000))
