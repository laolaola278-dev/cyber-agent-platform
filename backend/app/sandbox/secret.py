"""Secret reference contracts and fail-closed audited resolution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.events import EventType, PlatformEvent
from app.events.transactional import publish_audit
from app.exceptions import SecretNotFound, SecretPolicyViolation


class SecretReference(BaseModel):
    """Opaque secret identity stored by the control plane; never a secret value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(default="current", min_length=1, max_length=64)
    provider: str = Field(default="memory", min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=256)


class ResolvedSecret(BaseModel):
    """Short-lived resolved value intended only for worker-side injection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reference: SecretReference
    value: SecretStr
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SecretProvider(Protocol):
    provider_name: str

    async def resolve(self, reference: SecretReference) -> ResolvedSecret: ...

    async def health(self) -> bool: ...


class MemorySecretProvider:
    """App-local provider whose optional audit session records success and failure."""

    provider_name = "memory"

    def __init__(
        self,
        values: dict[str, str] | None = None,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        self._values = dict(values or {})
        self._session = session

    def with_session(self, session: AsyncSession) -> MemorySecretProvider:
        """Return an audited view without copying or exposing secret values."""

        provider = MemorySecretProvider(session=session)
        provider._values = self._values
        return provider

    def put(self, name: str, value: str) -> None:
        if not name or not value:
            raise SecretPolicyViolation("Secret name and value must be non-empty")
        if name.casefold().endswith(".env") or ".env/" in name.casefold():
            raise SecretPolicyViolation("Secret Provider cannot expose .env files")
        self._values[name] = value

    async def resolve(self, reference: SecretReference) -> ResolvedSecret:
        try:
            if reference.provider != self.provider_name:
                raise SecretPolicyViolation("Secret reference provider does not match")
            try:
                value = self._values[reference.name]
            except KeyError as error:
                raise SecretNotFound("Secret reference was not found") from error
        except (SecretNotFound, SecretPolicyViolation) as error:
            await self._audit(reference, succeeded=False, error=str(error))
            raise
        resolved = ResolvedSecret(reference=reference, value=SecretStr(value))
        await self._audit(reference, succeeded=True)
        return resolved

    async def health(self) -> bool:
        return True

    async def _audit(
        self,
        reference: SecretReference,
        *,
        succeeded: bool,
        error: str | None = None,
    ) -> None:
        if self._session is None:
            return
        await publish_audit(
            self._session,
            PlatformEvent(
                type=(
                    EventType.SECRET_REFERENCE_RESOLVED
                    if succeeded
                    else EventType.SECRET_REFERENCE_RESOLVE_FAILED
                ),
                trace_id=f"secret:{reference.name}",
                actor="secret-provider",
                resource=f"secret-reference:{reference.name}",
                payload={
                    "provider": reference.provider,
                    "purpose": reference.purpose,
                    "version": reference.version,
                },
                error=error,
            ),
        )
        await self._session.commit()
