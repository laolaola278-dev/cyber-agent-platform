"""EDR adapter for parsing, capability checks, provider calls and verification."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from app.exceptions import ResponsePolicyViolation
from app.tools.edr.contracts import (
    EDRAction,
    HostAction,
    HostActionReceipt,
    HostIsolationState,
    HostObservation,
)
from app.tools.edr.policy import EDRPolicyProvider
from app.tools.edr.provider import MockEDRProvider


class EDRAdapter:
    """The exclusive provider connection boundary for endpoint actions."""

    def __init__(self, provider: MockEDRProvider, policy: EDRPolicyProvider) -> None:
        self._provider = provider
        self._policy = policy

    @property
    def provider(self) -> MockEDRProvider:
        return self._provider

    def parse_action(self, parameters: Mapping[str, object]) -> HostAction:
        raw = parameters.get("host_action")
        if not isinstance(raw, Mapping):
            raise ResponsePolicyViolation("EDR response requires a HostAction mapping")
        try:
            return HostAction.model_validate(dict(raw))
        except ValueError as error:
            raise ResponsePolicyViolation("EDR HostAction is invalid") from error

    def parse_rollback_action(
        self,
        parameters: Mapping[str, object],
        *,
        original: HostAction,
        actor: str,
        created_at: datetime | None = None,
    ) -> HostAction:
        default_action = (
            EDRAction.HOST_UNISOLATE
            if original.action is EDRAction.HOST_ISOLATE
            else EDRAction.HOST_ISOLATE
        )
        raw = parameters.get("action", default_action.value)
        if not isinstance(raw, str):
            raise ResponsePolicyViolation("EDR rollback action must be a string")
        try:
            action = EDRAction(raw.strip().casefold())
        except ValueError as error:
            raise ResponsePolicyViolation("Unsupported EDR rollback action") from error
        rollback_id = parameters.get("id", f"{original.id}:rollback")
        version = parameters.get("version", original.version)
        reason = parameters.get("reason", f"Rollback {original.id}")
        if not all(isinstance(item, str) for item in (rollback_id, version, reason)):
            raise ResponsePolicyViolation("EDR rollback identity fields must be strings")
        rollback = HostAction.create(
            id=str(rollback_id),
            host_id=original.host_id,
            action=action,
            version=str(version),
            requested_by=actor,
            approved_by=None,
            reason=str(reason),
            created_at=created_at or datetime.now(UTC),
        )
        self._policy.validate_rollback(original, rollback)
        return rollback

    def validate_scope(self, action: HostAction, asset_ids: tuple[UUID, ...]) -> None:
        if len(asset_ids) != 1:
            raise ResponsePolicyViolation(
                "EDR HostAction requires exactly one immutable Host Asset"
            )
        if action.host_id != str(asset_ids[0]):
            raise ResponsePolicyViolation(
                "EDR host_id must match immutable Response Plan Asset scope"
            )

    async def execute(
        self,
        action: HostAction,
        *,
        approval_required: bool,
    ) -> HostActionReceipt:
        self._policy.validate_action(action, approval_required=approval_required)
        return await self._provider.execute(action)

    async def verify(
        self,
        action: HostAction,
        receipt: HostActionReceipt,
    ) -> tuple[bool, HostObservation, bool]:
        observed = await self._provider.read_host(action.host_id)
        expected = self._expected_state(action.action)
        verified = (
            receipt.status.value == "SUCCEEDED"
            and observed.present
            and observed.online
            and observed.isolation_state is expected
            and observed.last_action_id == action.id
        )
        drift = observed.isolation_state is not expected
        return verified, observed, drift

    async def rollback(
        self,
        original: HostAction,
        rollback: HostAction,
        *,
        approval_required: bool,
    ) -> HostActionReceipt:
        self._policy.validate_rollback(original, rollback)
        self._policy.validate_action(rollback, approval_required=approval_required)
        return await self._provider.execute(rollback)

    @staticmethod
    def _expected_state(action: EDRAction) -> HostIsolationState:
        if action is EDRAction.HOST_ISOLATE:
            return HostIsolationState.ISOLATED
        if action is EDRAction.HOST_UNISOLATE:
            return HostIsolationState.UNISOLATED
        raise ResponsePolicyViolation("EDR action has no Phase 19 verification mapping")
