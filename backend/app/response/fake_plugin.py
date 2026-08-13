"""Non-destructive Response plugin used to certify the framework lifecycle."""

import hashlib
import json
from datetime import UTC, datetime

from app.exceptions import ResponseExecutionError, ResponsePolicyViolation
from app.response.contracts import ResponsePluginContext
from app.schemas.response import (
    ResponseEvidenceItem,
    ResponsePlanSpec,
    ResponseResult,
    ResponseVerification,
)


class FakeResponsePlugin:
    """Deterministic plugin that simulates notify/ticket/block/isolate response."""

    name = "fake-response"
    version = "1.0.0"
    description = "Synthetic non-destructive Response Framework certification plugin"
    capabilities = frozenset(
        {
            "response.notify",
            "response.ticket",
            "response.block",
            "response.isolate",
            "response.rollback",
            "response.custom",
        }
    )
    permissions = frozenset({"response.execute", "response.verify", "response.rollback"})
    supports_approval = True
    supports_rollback = True
    sandbox_compatible = True
    operational_documentation = "plugins/response/synthetic/README.md"

    def __init__(self) -> None:
        self._context: ResponsePluginContext | None = None

    async def initialize(self, context: ResponsePluginContext) -> None:
        if "response.execute" not in context.granted_permissions:
            raise ResponsePolicyViolation("Response execution permission is required")
        self._context = context

    async def plan(
        self, plan: ResponsePlanSpec, context: ResponsePluginContext
    ) -> ResponsePlanSpec:
        self._require_context(context)
        return plan.model_copy(deep=True)

    async def validate(self, plan: ResponsePlanSpec, context: ResponsePluginContext) -> None:
        self._require_context(context)
        if plan.target_capability != context.capability:
            raise ResponsePolicyViolation("Plugin context capability does not match plan")
        if plan.incident_id != context.incident_id or tuple(plan.asset_ids) != context.asset_ids:
            raise ResponsePolicyViolation("Plugin context scope does not match plan")
        if context.parameters.get("force_validation_failure"):
            raise ResponsePolicyViolation("Synthetic validation failure requested")

    async def execute(
        self, plan: ResponsePlanSpec, context: ResponsePluginContext
    ) -> ResponseResult:
        self._require_context(context)
        if context.parameters.get("force_execution_failure"):
            raise ResponseExecutionError("Synthetic execution failure requested")
        return self._result(
            plan,
            context,
            action="execute",
            rollback_token=f"rb:{plan.incident_id}",
        )

    async def verify(
        self, result: ResponseResult, context: ResponsePluginContext
    ) -> ResponseResult:
        self._require_context(context)
        verified = not bool(context.parameters.get("force_verification_failure"))
        return result.model_copy(
            update={
                "success": result.success and verified,
                "verification": ResponseVerification(
                    verified=verified,
                    status="VERIFIED" if verified else "FAILED",
                    details={"synthetic": True},
                ),
            }
        )

    async def rollback(
        self, plan: ResponsePlanSpec, context: ResponsePluginContext
    ) -> ResponseResult:
        self._require_context(context)
        if "response.rollback" not in context.granted_permissions or not context.rollback_token:
            raise ResponsePolicyViolation("Validated rollback token is required")
        return self._result(plan, context, action="rollback", rollback_token=None)

    async def shutdown(self) -> None:
        self._context = None

    async def health(self) -> bool:
        return True

    def _result(
        self,
        plan: ResponsePlanSpec,
        context: ResponsePluginContext,
        *,
        action: str,
        rollback_token: str | None,
    ) -> ResponseResult:
        payload = {
            "action": action,
            "plan_id": str(context.response_plan_id),
            "incident_id": str(context.incident_id),
            "asset_ids": [str(item) for item in context.asset_ids],
            "capability": context.capability,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return ResponseResult(
            success=True,
            plugin_name=self.name,
            plugin_version=self.version,
            capability=plan.target_capability,
            execution_status="ROLLED_BACK" if action == "rollback" else "EXECUTED",
            verification=ResponseVerification(verified=False, status="PENDING"),
            evidence=[
                ResponseEvidenceItem(
                    evidence_type="RESPONSE_RECEIPT",
                    sha256=hashlib.sha256(encoded).hexdigest(),
                    reference=f"synthetic://{context.response_plan_id}/{action}",
                    metadata=payload,
                )
            ],
            duration_ms=1,
            message=f"Synthetic response {action} completed",
            rollback_supported=True,
            rollback_token=rollback_token,
            metadata={"destructive": False, "network_access": False},
        )

    def _require_context(self, context: ResponsePluginContext) -> None:
        if self._context != context:
            raise ResponseExecutionError("Response plugin was not initialized for this context")
