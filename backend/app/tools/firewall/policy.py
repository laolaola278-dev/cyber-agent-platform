"""Configuration-first safety policy for declarative firewall response rules."""

from __future__ import annotations

import ipaddress

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.exceptions import ResponsePolicyViolation
from app.tools.firewall.contracts import (
    FirewallAction,
    FirewallDirection,
    FirewallProtocol,
    FirewallRollbackAction,
    FirewallRule,
)


class FirewallPolicy(BaseModel):
    """Fail-closed rule and rollback policy evaluated before provider access."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    mock_only: bool = True
    allowed_actions: frozenset[FirewallAction] = frozenset(
        {FirewallAction.BLOCK, FirewallAction.REJECT, FirewallAction.LOG}
    )
    allowed_directions: frozenset[FirewallDirection] = frozenset(FirewallDirection)
    allowed_protocols: frozenset[FirewallProtocol] = frozenset(FirewallProtocol)
    allowed_tables: frozenset[str] = frozenset({"filter"})
    allowed_chains: frozenset[str] = frozenset({"INPUT", "OUTPUT", "FORWARD"})
    allowed_rollback_actions: frozenset[FirewallRollbackAction] = frozenset(FirewallRollbackAction)
    management_networks: tuple[str, ...] = (
        "10.255.0.0/16",
        "192.0.2.0/24",
        "2001:db8:ffff::/48",
    )
    protected_networks: tuple[str, ...] = (
        "127.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "::1/128",
        "fe80::/10",
        "ff00::/8",
    )
    protected_management_ports: frozenset[int] = frozenset({22, 3389, 443, 8443})
    maximum_ports_per_rule: int = Field(default=16, ge=1, le=32)
    maximum_priority: int = Field(default=10_000, ge=1, le=100_000)
    require_change_approval: bool = True

    @field_validator(
        "allowed_actions",
        "allowed_directions",
        "allowed_protocols",
        "allowed_tables",
        "allowed_chains",
        "allowed_rollback_actions",
    )
    @classmethod
    def require_nonempty_allowlist(cls, value: frozenset[object]) -> frozenset[object]:
        if not value:
            raise ValueError("Firewall policy allowlists cannot be empty")
        return value

    @field_validator("management_networks", "protected_networks")
    @classmethod
    def validate_network_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("Firewall protected network sets cannot be empty")
        try:
            return tuple(str(ipaddress.ip_network(item, strict=False)) for item in value)
        except ValueError as error:
            raise ValueError("Firewall policy contains an invalid protected network") from error

    @model_validator(mode="after")
    def validate_safety_mode(self) -> FirewallPolicy:
        if not self.mock_only:
            raise ValueError("Phase 17 Firewall policy must remain mock-only")
        if not self.protected_management_ports:
            raise ValueError("Firewall management port protection cannot be disabled")
        return self


class FirewallPolicyProvider:
    """Apply management-plane and blast-radius controls to validated rules."""

    def __init__(self, policy: FirewallPolicy | None = None) -> None:
        self._policy = policy or FirewallPolicy()

    @property
    def policy(self) -> FirewallPolicy:
        return self._policy

    def validate_rule(self, rule: FirewallRule, *, approval_required: bool) -> None:
        policy = self._policy
        if not policy.enabled:
            raise ResponsePolicyViolation("Firewall response policy is disabled")
        if rule.action not in policy.allowed_actions:
            raise ResponsePolicyViolation("Firewall rule action is not allowlisted")
        if rule.direction not in policy.allowed_directions:
            raise ResponsePolicyViolation("Firewall rule direction is not allowlisted")
        if rule.protocol not in policy.allowed_protocols:
            raise ResponsePolicyViolation("Firewall rule protocol is not allowlisted")
        if rule.table.casefold() not in {item.casefold() for item in policy.allowed_tables}:
            raise ResponsePolicyViolation("Firewall rule table is not allowlisted")
        if rule.chain.upper() not in {item.upper() for item in policy.allowed_chains}:
            raise ResponsePolicyViolation("Firewall rule chain is not allowlisted")
        if rule.priority > policy.maximum_priority:
            raise ResponsePolicyViolation("Firewall rule priority exceeds the policy limit")
        if (
            len(rule.source_ports) > policy.maximum_ports_per_rule
            or len(rule.destination_ports) > policy.maximum_ports_per_rule
        ):
            raise ResponsePolicyViolation("Firewall rule exceeds the port scope limit")
        if policy.require_change_approval and not approval_required:
            raise ResponsePolicyViolation("Firewall rule changes require governed approval")
        self._protect_control_plane(rule)

    def validate_rollback(self, action: FirewallRollbackAction) -> None:
        if action not in self._policy.allowed_rollback_actions:
            raise ResponsePolicyViolation("Firewall rollback action is not allowlisted")

    def _protect_control_plane(self, rule: FirewallRule) -> None:
        source = ipaddress.ip_network(rule.source)
        destination = ipaddress.ip_network(rule.destination)
        networks = (*self._policy.management_networks, *self._policy.protected_networks)
        protected = [ipaddress.ip_network(item) for item in networks]
        if any(
            candidate.version == network.version and candidate.overlaps(network)
            for candidate in (source, destination)
            for network in protected
        ):
            raise ResponsePolicyViolation(
                "Firewall rule overlaps a protected management or control-plane network"
            )
        if rule.action in {FirewallAction.BLOCK, FirewallAction.REJECT}:
            ports = set(rule.source_ports) | set(rule.destination_ports)
            if ports & self._policy.protected_management_ports:
                raise ResponsePolicyViolation(
                    "Firewall rule would block a protected management port"
                )
            if rule.protocol is FirewallProtocol.ANY and not ports:
                raise ResponsePolicyViolation("Firewall any-protocol deny rules are prohibited")
