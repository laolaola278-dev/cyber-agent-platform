"""Provider-neutral WAF rule contracts for controlled response execution."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WAFRuleAction(StrEnum):
    """Supported fail-closed rule actions."""

    BLOCK = "BLOCK"
    LOG = "LOG"
    ALLOW = "ALLOW"


class WAFRuleStatus(StrEnum):
    """Observable lifecycle state returned by the provider."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    REMOVED = "REMOVED"


class WAFRollbackAction(StrEnum):
    """Only reversible rule lifecycle actions accepted by the mock provider."""

    REMOVE = "REMOVE"
    DISABLE = "DISABLE"
    RESTORE = "RESTORE"


class WAFRule(BaseModel):
    """Versioned declarative WAF rule with deterministic integrity checksum."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    action: WAFRuleAction
    condition: str = Field(min_length=1, max_length=2_000)
    priority: int = Field(ge=1, le=100_000)
    version: str = Field(min_length=1, max_length=64)
    status: WAFRuleStatus = WAFRuleStatus.ENABLED
    source: str = Field(min_length=1, max_length=256)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("id", "name", "version", "source")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, value: str) -> str:
        normalized = " ".join(value.split())
        forbidden = ("\x00", "\n", "\r", "{{", "}}", ";", "`", "$(")
        if any(token in normalized for token in forbidden):
            raise ValueError("WAF rule condition contains unsafe syntax")
        if normalized.casefold().startswith(("exec ", "shell ", "import ")):
            raise ValueError("WAF rule condition must be declarative")
        return normalized

    @model_validator(mode="after")
    def validate_checksum(self) -> WAFRule:
        if self.checksum != self.calculate_checksum():
            raise ValueError("WAF rule checksum does not match canonical rule content")
        return self

    def canonical_content(self) -> dict[str, object]:
        """Return only policy-controlled content, excluding operational status/checksum."""

        return {
            "id": self.id,
            "name": self.name,
            "action": self.action.value,
            "condition": self.condition,
            "priority": self.priority,
            "version": self.version,
            "source": self.source,
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
        action: WAFRuleAction,
        condition: str,
        priority: int,
        version: str,
        source: str,
        status: WAFRuleStatus = WAFRuleStatus.ENABLED,
    ) -> WAFRule:
        draft: dict[str, Any] = {
            "id": id,
            "name": name,
            "action": action,
            "condition": condition,
            "priority": priority,
            "version": version,
            "status": status,
            "source": source,
        }
        canonical = {
            key: value.value if isinstance(value, StrEnum) else value
            for key, value in draft.items()
            if key != "status"
        }
        checksum = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(**draft, checksum=checksum)


class WAFRuleChange(BaseModel):
    """Provider result for an attempted WAF rule lifecycle operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: str = Field(min_length=1, max_length=32)
    rule: WAFRule
    provider_reference: str = Field(min_length=1, max_length=512)
    changed: bool
    metadata: dict[str, Any] = Field(default_factory=dict)
