"""Synthetic EDR provider with no path to a real endpoint or operating system."""

from __future__ import annotations

from datetime import UTC, datetime

from app.exceptions import ResponseExecutionError, ResponsePolicyViolation
from app.tools.edr.contracts import (
    EDRAction,
    HostAction,
    HostActionReceipt,
    HostActionStatus,
    HostIsolationState,
    HostObservation,
)


class MockEDRProvider:
    """Deterministic in-memory observed state for Phase 19 safety verification."""

    provider_name = "mock-edr"
    network_access = False
    production_access = False
    filesystem_write = False
    shell_execute = False

    def __init__(self) -> None:
        self._hosts: dict[str, HostObservation] = {}
        self._receipts: dict[str, HostActionReceipt] = {}

    def seed_host(
        self,
        host_id: str,
        *,
        isolation_state: HostIsolationState = HostIsolationState.UNISOLATED,
        online: bool = True,
    ) -> None:
        """Create synthetic inventory state; no external discovery is performed."""

        self._hosts[host_id] = HostObservation(
            host_id=host_id,
            isolation_state=isolation_state,
            online=online,
            present=True,
            observed_at=datetime.now(UTC),
        )

    async def execute(self, action: HostAction) -> HostActionReceipt:
        """Apply an idempotent action to synthetic state only."""

        prior = self._receipts.get(action.id)
        if prior is not None:
            if prior.action.checksum != action.checksum:
                raise ResponsePolicyViolation(
                    "EDR idempotency key cannot be reused with different action content"
                )
            return prior.model_copy(
                update={
                    "changed": False,
                    "metadata": {**prior.metadata, "idempotent_replay": True},
                }
            )
        observed = self._hosts.get(action.host_id)
        if observed is None or not observed.present:
            raise ResponseExecutionError("EDR target host is missing from provider inventory")
        if not observed.online:
            raise ResponseExecutionError("EDR target agent is offline")
        desired = self._desired_state(action.action)
        changed = observed.isolation_state is not desired
        completed = action.model_copy(update={"status": HostActionStatus.SUCCEEDED})
        receipt = HostActionReceipt(
            action=completed,
            status=HostActionStatus.SUCCEEDED,
            provider_reference=f"mock-edr://hosts/{action.host_id}/actions/{action.id}",
            changed=changed,
            observed_state=desired,
            metadata={
                "provider": self.provider_name,
                "network_access": self.network_access,
                "production_access": self.production_access,
                "filesystem_write": self.filesystem_write,
                "shell_execute": self.shell_execute,
                "desired_state": desired.value,
                "idempotent_replay": False,
            },
        )
        self._hosts[action.host_id] = HostObservation(
            host_id=action.host_id,
            isolation_state=desired,
            online=True,
            present=True,
            version=action.version,
            last_action_id=action.id,
            observed_at=datetime.now(UTC),
        )
        self._receipts[action.id] = receipt
        return receipt

    async def read_host(self, host_id: str) -> HostObservation:
        observed = self._hosts.get(host_id)
        if observed is None:
            return HostObservation(
                host_id=host_id,
                isolation_state=HostIsolationState.UNKNOWN,
                online=False,
                present=False,
                observed_at=datetime.now(UTC),
            )
        return observed.model_copy(update={"observed_at": datetime.now(UTC)})

    def set_online(self, host_id: str, online: bool) -> None:
        observed = self._hosts.get(host_id)
        if observed is None:
            raise KeyError(host_id)
        self._hosts[host_id] = observed.model_copy(
            update={"online": online, "observed_at": datetime.now(UTC)}
        )

    def remove_host(self, host_id: str) -> None:
        self._hosts.pop(host_id, None)

    def inject_observed_state(self, host_id: str, state: HostIsolationState) -> None:
        """Test-only drift injection used to prove detection without auto-remediation."""

        observed = self._hosts.get(host_id)
        if observed is None:
            raise KeyError(host_id)
        self._hosts[host_id] = observed.model_copy(
            update={"isolation_state": state, "observed_at": datetime.now(UTC)}
        )

    @staticmethod
    def _desired_state(action: EDRAction) -> HostIsolationState:
        if action is EDRAction.HOST_ISOLATE:
            return HostIsolationState.ISOLATED
        if action is EDRAction.HOST_UNISOLATE:
            return HostIsolationState.UNISOLATED
        raise ResponsePolicyViolation("Mock EDR Provider received an unsupported action")
