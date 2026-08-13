"""Provider-neutral contracts for governed endpoint response actions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EDRAction(StrEnum):
    """Portable EDR actions; reserved actions remain non-executable in Phase 19."""

    HOST_ISOLATE = "host.isolate"
    HOST_UNISOLATE = "host.unisolate"
    PROCESS_TERMINATE = "process.terminate"
    COLLECT_PACKAGE = "collect.package"


class HostActionStatus(StrEnum):
    """Provider-neutral asynchronous action status."""

    REQUESTED = "REQUESTED"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class HostIsolationState(StrEnum):
    """Observed host containment state returned by an EDR provider."""

    ISOLATED = "ISOLATED"
    UNISOLATED = "UNISOLATED"
    UNKNOWN = "UNKNOWN"


class HostAction(BaseModel):
    """Typed JSON action embedded in a Response Plan, never an EDR database row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    host_id: str = Field(min_length=1, max_length=128)
    action: EDRAction
    status: HostActionStatus
    version: str = Field(min_length=1, max_length=64)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_by: str = Field(min_length=1, max_length=256)
    approved_by: str | None = Field(default=None, max_length=256)
    reason: str = Field(min_length=1, max_length=4_000)
    created_at: datetime

    @field_validator("id", "host_id", "version", "requested_by", "reason")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("HostAction text fields cannot be blank")
        return normalized

    @field_validator("approved_by")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("host_id")
    @classmethod
    def normalize_host_id(cls, value: str) -> str:
        try:
            return str(UUID(value.strip()))
        except ValueError as error:
            raise ValueError("HostAction host_id must be a canonical Asset UUID") from error

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        if normalized > datetime.now(UTC):
            raise ValueError("HostAction created_at cannot be in the future")
        return normalized

    @model_validator(mode="after")
    def validate_checksum(self) -> HostAction:
        if self.checksum != self.calculate_checksum():
            raise ValueError("HostAction checksum does not match canonical action content")
        return self

    def canonical_content(self) -> dict[str, object]:
        """Return immutable desired action content, excluding provider-owned status."""

        return {
            "id": self.id,
            "host_id": self.host_id,
            "action": self.action.value,
            "version": self.version,
            "requested_by": self.requested_by,
            "approved_by": self.approved_by,
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
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
        host_id: str,
        action: EDRAction,
        version: str,
        requested_by: str,
        reason: str,
        created_at: datetime,
        approved_by: str | None = None,
    ) -> HostAction:
        normalized_time = (
            created_at.replace(tzinfo=UTC)
            if created_at.tzinfo is None
            else created_at.astimezone(UTC)
        )
        try:
            normalized_host_id = str(UUID(host_id.strip()))
        except ValueError as error:
            raise ValueError("HostAction host_id must be a canonical Asset UUID") from error
        draft: dict[str, Any] = {
            "id": id.strip(),
            "host_id": normalized_host_id,
            "action": action,
            "status": HostActionStatus.REQUESTED,
            "version": version.strip(),
            "requested_by": requested_by.strip(),
            "approved_by": approved_by.strip() if approved_by and approved_by.strip() else None,
            "reason": reason.strip(),
            "created_at": normalized_time,
        }
        canonical = {
            "id": draft["id"],
            "host_id": draft["host_id"],
            "action": action.value,
            "version": draft["version"],
            "requested_by": draft["requested_by"],
            "approved_by": draft["approved_by"],
            "reason": draft["reason"],
            "created_at": normalized_time.isoformat(),
        }
        checksum = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(**draft, checksum=checksum)


class HostObservation(BaseModel):
    """Observed provider state used exclusively for read-back verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    host_id: str
    isolation_state: HostIsolationState
    online: bool
    present: bool
    version: str | None = None
    last_action_id: str | None = None
    observed_at: datetime


class HostActionReceipt(BaseModel):
    """Provider receipt for an idempotent endpoint action attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: HostAction
    status: HostActionStatus
    provider_reference: str = Field(min_length=1, max_length=512)
    changed: bool
    observed_state: HostIsolationState
    metadata: dict[str, Any] = Field(default_factory=dict)
