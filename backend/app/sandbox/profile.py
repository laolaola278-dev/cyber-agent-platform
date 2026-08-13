"""Provider-neutral resource and access profile for plugin sandbox executions."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReadonlyMount(BaseModel):
    """A host path exposed read-only at a declared sandbox path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1, max_length=1024)
    target: str = Field(min_length=1, max_length=1024)


class TmpMount(BaseModel):
    """An ephemeral writable mount with an explicit size limit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target: str = Field(min_length=1, max_length=1024)
    size_mb: int = Field(default=64, ge=1, le=4096)


class SandboxProfile(BaseModel):
    """Immutable desired sandbox boundary independent of Docker, VM or OCI syntax."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(default="1.0.0", min_length=1, max_length=64)
    cpu_millicores: int = Field(default=500, ge=50, le=16_000)
    memory_mb: int = Field(default=256, ge=32, le=65_536)
    filesystem_writable: bool = False
    network_enabled: bool = False
    allowed_networks: tuple[str, ...] = ()
    environment: dict[str, str] = Field(default_factory=dict)
    secret_references: tuple[str, ...] = ()
    timeout_seconds: int = Field(default=60, ge=1, le=86_400)
    working_directory: str = "/workspace"
    readonly_mounts: tuple[ReadonlyMount, ...] = ()
    tmp_mounts: tuple[TmpMount, ...] = (TmpMount(target="/tmp"),)

    @field_validator("environment")
    @classmethod
    def reject_secret_like_environment(cls, value: dict[str, str]) -> dict[str, str]:
        forbidden = ("secret", "token", "password", "credential", "api_key", "private_key")
        if any(any(marker in key.casefold() for marker in forbidden) for key in value):
            raise ValueError("Sandbox environment cannot carry secret-like values")
        return dict(sorted(value.items()))

    @field_validator("secret_references")
    @classmethod
    def normalize_references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip() for item in value if item.strip()}))
        if any(
            item.casefold().endswith(".env") or ".env/" in item.casefold() for item in normalized
        ):
            raise ValueError("Plugins cannot reference .env files")
        return normalized

    @model_validator(mode="after")
    def validate_network_boundary(self) -> SandboxProfile:
        if not self.network_enabled and self.allowed_networks:
            raise ValueError("Network allowlist requires network access to be enabled")
        if self.network_enabled and not self.allowed_networks:
            raise ValueError("Network-enabled sandboxes require an explicit allowlist")
        if not self.working_directory.startswith("/"):
            raise ValueError("Sandbox working directory must be an absolute sandbox path")
        return self
