"""Mock-only high-privilege EDR Response Plugin using the existing Response lifecycle."""

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
from app.tools.edr import (
    EDRAction,
    EDRAdapter,
    HostAction,
    HostActionReceipt,
    HostObservation,
)


class EDRResponsePlugin:
    """Governed endpoint containment backed exclusively by a synthetic provider."""

    name = "edr-response"
    version = "1.0.0"
    description = "Mock-only provider-neutral endpoint isolation response plugin"
    capabilities = frozenset({"response.edr"})
    permissions = frozenset({"response.execute", "response.verify", "response.rollback"})
    supports_approval = True
    supports_rollback = True
    sandbox_compatible = True
    operational_documentation = "plugins/edr/README.md"

    def __init__(self, adapter: EDRAdapter) -> None:
        self._adapter = adapter
        self._context: ResponsePluginContext | None = None
        self._pending_verification: tuple[HostAction, HostActionReceipt, str] | None = None

    async def initialize(self, context: ResponsePluginContext) -> None:
        if context.granted_permissions != self.permissions:
            raise ResponsePolicyViolation("EDR plugin requires its certified permission set")
        if context.capability != "response.edr":
            raise ResponsePolicyViolation("EDR plugin accepts only response.edr plans")
        self._context = context
        self._pending_verification = None

    async def plan(
        self, plan: ResponsePlanSpec, context: ResponsePluginContext
    ) -> ResponsePlanSpec:
        self._require_context(context)
        action = self._adapter.parse_action(context.parameters)
        return plan.model_copy(
            deep=True,
            update={"parameters": {"host_action": action.model_dump(mode="json")}},
        )

    async def validate(self, plan: ResponsePlanSpec, context: ResponsePluginContext) -> None:
        self._require_context(context)
        if plan.target_capability != "response.edr":
            raise ResponsePolicyViolation("EDR plan capability is invalid")
        if plan.incident_id != context.incident_id or tuple(plan.asset_ids) != context.asset_ids:
            raise ResponsePolicyViolation("EDR plan scope does not match immutable context")
        if not plan.approval_required:
            raise ResponsePolicyViolation("EDR host actions require governed approval")
        action = self._adapter.parse_action(plan.parameters)
        self._adapter.validate_scope(action, context.asset_ids)
        if action.action not in {EDRAction.HOST_ISOLATE, EDRAction.HOST_UNISOLATE}:
            raise ResponsePolicyViolation("EDR action is reserved and not implemented in Phase 19")
        if action.id.casefold().startswith(("provider-", "system-", "builtin-")):
            raise ResponsePolicyViolation("Provider-owned EDR actions cannot be requested")
        if context.rollback_parameters:
            self._adapter.parse_rollback_action(
                context.rollback_parameters,
                original=action,
                actor=context.actor,
            )

    async def execute(
        self, plan: ResponsePlanSpec, context: ResponsePluginContext
    ) -> ResponseResult:
        self._require_context(context)
        started = monotonic()
        action = self._adapter.parse_action(plan.parameters)
        receipt = await self._adapter.execute(
            action,
            approval_required=plan.approval_required,
        )
        self._pending_verification = (action, receipt, "EXECUTE")
        return self._result(
            plan,
            action,
            receipt,
            phase="EXECUTE",
            observation=None,
            drift=False,
            duration_ms=self._duration_ms(started),
            rollback_token=self._rollback_token(context, action),
        )

    async def verify(
        self, result: ResponseResult, context: ResponsePluginContext
    ) -> ResponseResult:
        self._require_context(context)
        if self._pending_verification is None:
            raise ResponseExecutionError("EDR verification has no pending Provider action")
        action, receipt, phase = self._pending_verification
        verified, observed, drift = await self._adapter.verify(action, receipt)
        evidence = self._evidence(action, receipt, phase=phase, observation=observed, drift=drift)
        return result.model_copy(
            update={
                "success": result.success and verified,
                "verification": ResponseVerification(
                    verified=verified,
                    status="VERIFIED" if verified else "FAILED",
                    details={
                        "provider_reference": receipt.provider_reference,
                        "host_id": action.host_id,
                        "action": action.action.value,
                        "desired_state": receipt.observed_state.value,
                        "observed_state": observed.isolation_state.value,
                        "host_present": observed.present,
                        "agent_online": observed.online,
                        "drift_detected": drift,
                        "incident_candidate": drift,
                        "auto_remediation": False,
                        "network_access": False,
                        "production_access": False,
                        "filesystem_write": False,
                        "shell_execute": False,
                    },
                ),
                "evidence": [evidence],
                "metadata": {
                    **result.metadata,
                    "desired_state": receipt.observed_state.value,
                    "observed_state": observed.isolation_state.value,
                    "drift_detected": drift,
                    "incident_candidate": drift,
                    "auto_remediation": False,
                },
            }
        )

    async def rollback(
        self, plan: ResponsePlanSpec, context: ResponsePluginContext
    ) -> ResponseResult:
        self._require_context(context)
        original = self._adapter.parse_action(plan.parameters)
        if not context.rollback_token or not self._valid_rollback_token(context, original):
            raise ResponsePolicyViolation("Validated EDR rollback token is required")
        started = monotonic()
        rollback = self._adapter.parse_rollback_action(
            context.rollback_parameters,
            original=original,
            actor=context.actor,
        )
        receipt = await self._adapter.rollback(
            original,
            rollback,
            approval_required=plan.approval_required,
        )
        self._pending_verification = (rollback, receipt, "ROLLBACK")
        return self._result(
            plan,
            rollback,
            receipt,
            phase="ROLLBACK",
            observation=None,
            drift=False,
            duration_ms=self._duration_ms(started),
            rollback_token=None,
        )

    async def shutdown(self) -> None:
        self._context = None
        self._pending_verification = None

    async def health(self) -> bool:
        provider = self._adapter.provider
        return (
            not provider.network_access
            and not provider.production_access
            and not provider.filesystem_write
            and not provider.shell_execute
        )

    def _result(
        self,
        plan: ResponsePlanSpec,
        action: HostAction,
        receipt: HostActionReceipt,
        *,
        phase: str,
        observation: HostObservation | None,
        drift: bool,
        duration_ms: int,
        rollback_token: str | None,
    ) -> ResponseResult:
        return ResponseResult(
            success=receipt.status.value == "SUCCEEDED",
            plugin_name=self.name,
            plugin_version=self.version,
            capability=plan.target_capability,
            execution_status="ROLLED_BACK" if phase == "ROLLBACK" else "EXECUTED",
            verification=ResponseVerification(verified=False, status="PENDING"),
            evidence=[
                self._evidence(
                    action,
                    receipt,
                    phase=phase,
                    observation=observation,
                    drift=drift,
                )
            ],
            duration_ms=duration_ms,
            message=f"Mock EDR {action.action.value} action completed",
            rollback_supported=True,
            rollback_token=rollback_token,
            metadata={
                "provider": "mock-edr",
                "network_access": False,
                "production_access": False,
                "filesystem_write": False,
                "shell_execute": False,
                "host_id": action.host_id,
                "phase": phase,
            },
        )

    @staticmethod
    def _evidence(
        action: HostAction,
        receipt: HostActionReceipt,
        *,
        phase: str,
        observation: HostObservation | None,
        drift: bool,
    ) -> ResponseEvidenceItem:
        metadata = {
            "phase": phase,
            "action": action.model_dump(mode="json"),
            "provider_reference": receipt.provider_reference,
            "changed": receipt.changed,
            "desired_state": receipt.observed_state.value,
            "observed_state": (
                observation.isolation_state.value if observation else "PENDING_READBACK"
            ),
            "host_present": observation.present if observation else None,
            "agent_online": observation.online if observation else None,
            "drift_detected": drift,
            "incident_candidate": drift,
            "auto_remediation": False,
            **receipt.metadata,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return ResponseEvidenceItem(
            evidence_type=("EDR_HOST_ROLLBACK" if phase == "ROLLBACK" else "EDR_HOST_ACTION"),
            sha256=hashlib.sha256(encoded).hexdigest(),
            reference=receipt.provider_reference,
            metadata=metadata,
        )

    @staticmethod
    def _rollback_token(context: ResponsePluginContext, action: HostAction) -> str:
        material = (
            f"{context.response_plan_id}:{context.incident_id}:{action.id}:"
            f"{action.host_id}:{action.version}:{action.checksum}"
        )
        return f"edr-rb:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"

    def _valid_rollback_token(self, context: ResponsePluginContext, action: HostAction) -> bool:
        return context.rollback_token == self._rollback_token(context, action)

    def _require_context(self, context: ResponsePluginContext) -> None:
        if self._context != context:
            raise ResponseExecutionError("EDR plugin was not initialized for this context")

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, int((monotonic() - started) * 1_000))
