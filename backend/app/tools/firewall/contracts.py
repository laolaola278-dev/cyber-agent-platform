"""Provider-neutral firewall rule contracts for controlled response execution."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FirewallAction(StrEnum):
    """Restrictive or observational actions supported by the mock provider."""

    BLOCK = "BLOCK"
    REJECT = "REJECT"
    LOG = "LOG"


class FirewallDirection(StrEnum):
    """Provider-neutral traffic direction."""

    INGRESS = "INGRESS"
    EGRESS = "EGRESS"
    FORWARD = "FORWARD"


class FirewallProtocol(StrEnum):
    """Small, portable protocol allowlist."""

    TCP = "TCP"
    UDP = "UDP"
    ICMP = "ICMP"
    ICMPV6 = "ICMPV6"
    ANY = "ANY"


class FirewallRuleStatus(StrEnum):
    """Observable lifecycle state returned by the provider."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    REMOVED = "REMOVED"


class FirewallRollbackAction(StrEnum):
    """Declared reversible rule lifecycle actions."""

    REMOVE = "REMOVE"
    DISABLE = "DISABLE"
    RESTORE = "RESTORE"


class FirewallRule(BaseModel):
    """A bounded, deterministic firewall rule independent of vendor syntax."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    action: FirewallAction
    direction: FirewallDirection
    source: str = Field(min_length=1, max_length=64)
    destination: str = Field(min_length=1, max_length=64)
    protocol: FirewallProtocol
    source_ports: tuple[int, ...] = Field(default_factory=tuple, max_length=32)
    destination_ports: tuple[int, ...] = Field(default_factory=tuple, max_length=32)
    table: str = Field(min_length=1, max_length=64)
    chain: str = Field(min_length=1, max_length=64)
    priority: int = Field(ge=1, le=100_000)
    version: str = Field(min_length=1, max_length=64)
    status: FirewallRuleStatus = FirewallRuleStatus.ENABLED
    impact_scope: tuple[str, ...] = Field(min_length=1, max_length=20)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("id", "name", "version", "table", "chain")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("source", "destination")
    @classmethod
    def normalize_network(cls, value: str) -> str:
        normalized = value.strip()
        if normalized.casefold() in {"any", "*", "0.0.0.0/0", "::/0"}:
            raise ValueError("Firewall source and destination cannot be any network")
        try:
            network = ipaddress.ip_network(normalized, strict=False)
        except ValueError as error:
            raise ValueError(
                "Firewall source and destination must be valid CIDR networks"
            ) from error
        if network.prefixlen == 0:
            raise ValueError("Firewall default route scope is not allowed")
        minimum_prefix = 8 if network.version == 4 else 32
        if network.prefixlen < minimum_prefix:
            raise ValueError("Firewall network scope is too broad")
        return str(network)

    @field_validator("source_ports", "destination_ports")
    @classmethod
    def validate_ports(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        normalized = tuple(sorted(set(value)))
        if any(port < 1 or port > 65_535 for port in normalized):
            raise ValueError("Firewall ports must be between 1 and 65535")
        return normalized

    @field_validator("impact_scope")
    @classmethod
    def validate_scope(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip() for item in value if item.strip()}))
        if not normalized or any(item in {"*", "ANY", "ALL"} for item in normalized):
            raise ValueError("Firewall impact scope must be explicit")
        return normalized

    @model_validator(mode="after")
    def validate_semantics(self) -> FirewallRule:
        expected_chain = {
            FirewallDirection.INGRESS: "INPUT",
            FirewallDirection.EGRESS: "OUTPUT",
            FirewallDirection.FORWARD: "FORWARD",
        }[self.direction]
        if self.table.casefold() != "filter":
            raise ValueError("Firewall response only supports the filter table")
        if self.chain.casefold() != expected_chain.casefold():
            raise ValueError("Firewall chain does not match the selected direction")
        if self.protocol in {FirewallProtocol.ICMP, FirewallProtocol.ICMPV6} and (
            self.source_ports or self.destination_ports
        ):
            raise ValueError("ICMP firewall rules cannot contain ports")
        if self.protocol is FirewallProtocol.ANY and (self.source_ports or self.destination_ports):
            raise ValueError("ANY protocol firewall rules cannot contain ports")
        if self.checksum != self.calculate_checksum():
            raise ValueError("Firewall rule checksum does not match canonical rule content")
        return self

    def canonical_content(self) -> dict[str, object]:
        """Return policy-controlled content, excluding operational status/checksum."""

        return {
            "id": self.id,
            "name": self.name,
            "action": self.action.value,
            "direction": self.direction.value,
            "source": self.source,
            "destination": self.destination,
            "protocol": self.protocol.value,
            "source_ports": list(self.source_ports),
            "destination_ports": list(self.destination_ports),
            "table": self.table,
            "chain": self.chain,
            "priority": self.priority,
            "version": self.version,
            "impact_scope": list(self.impact_scope),
        }

    def calculate_checksum(self) -> str:
        encoded = json.dumps(
            self.canonical_content(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def create(
        cls,
        *,
        id: str,
        name: str,
        action: FirewallAction,
        direction: FirewallDirection,
        source: str,
        destination: str,
        protocol: FirewallProtocol,
        source_ports: tuple[int, ...] = (),
        destination_ports: tuple[int, ...] = (),
        table: str = "filter",
        chain: str | None = None,
        priority: int,
        version: str,
        impact_scope: tuple[str, ...],
        status: FirewallRuleStatus = FirewallRuleStatus.ENABLED,
    ) -> FirewallRule:
        expected_chain = {
            FirewallDirection.INGRESS: "INPUT",
            FirewallDirection.EGRESS: "OUTPUT",
            FirewallDirection.FORWARD: "FORWARD",
        }[direction]
        normalized_source = str(ipaddress.ip_network(source.strip(), strict=False))
        normalized_destination = str(ipaddress.ip_network(destination.strip(), strict=False))
        normalized_source_ports = tuple(sorted(set(source_ports)))
        normalized_destination_ports = tuple(sorted(set(destination_ports)))
        normalized_scope = tuple(sorted({item.strip() for item in impact_scope if item.strip()}))
        draft: dict[str, Any] = {
            "id": id.strip(),
            "name": name.strip(),
            "action": action,
            "direction": direction,
            "source": normalized_source,
            "destination": normalized_destination,
            "protocol": protocol,
            "source_ports": normalized_source_ports,
            "destination_ports": normalized_destination_ports,
            "table": table.strip(),
            "chain": (chain or expected_chain).strip(),
            "priority": priority,
            "version": version.strip(),
            "status": status,
            "impact_scope": normalized_scope,
        }
        normalized = {
            key: value.value if isinstance(value, StrEnum) else value
            for key, value in draft.items()
            if key != "status"
        }
        normalized["source_ports"] = list(normalized_source_ports)
        normalized["destination_ports"] = list(normalized_destination_ports)
        normalized["impact_scope"] = list(normalized_scope)
        checksum = hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(**draft, checksum=checksum)


class FirewallRuleChange(BaseModel):
    """Provider receipt for an attempted firewall lifecycle operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: str = Field(min_length=1, max_length=32)
    rule: FirewallRule
    provider_reference: str = Field(min_length=1, max_length=512)
    changed: bool
    metadata: dict[str, Any] = Field(default_factory=dict)
