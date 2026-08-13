"""Versioned portable Plugin Manifest contracts."""

from __future__ import annotations

from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.sandbox.profile import SandboxProfile


class WorkerManifestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime_version: str = Field(min_length=1, max_length=64)
    max_concurrency: int = Field(default=1, ge=1, le=1024)
    heartbeat_seconds: int = Field(default=30, ge=1, le=3600)
    lease_ttl_seconds: int = Field(default=120, ge=1, le=86_400)


class SecretManifestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    references: tuple[str, ...] = ()
    provider: str = Field(default="memory", min_length=1, max_length=64)


class NetworkManifestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    allowlist: tuple[str, ...] = ()


class FilesystemManifestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    writable: bool = False
    working_directory: str = "/workspace"
    readonly_mounts: tuple[dict[str, str], ...] = ()
    tmp_mounts: tuple[dict[str, Any], ...] = ({"target": "/tmp", "size_mb": 64},)


class SandboxProviderRequirements(BaseModel):
    """Provider-neutral capability requirements used during Worker placement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    network: bool = False
    filesystem: bool = False
    secret: bool = False
    timeout: bool = True
    container: bool = False
    vm: bool = False
    snapshot: bool = False


class _ManifestBoundaryMixin(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    entrypoint: str = Field(min_length=1, max_length=512)
    runtime_version: str = Field(min_length=1, max_length=64)
    capabilities: tuple[str, ...] = Field(min_length=1)
    permissions: tuple[str, ...] = ()
    sandbox: SandboxProfile
    worker: WorkerManifestSpec
    secret: SecretManifestSpec = Field(default_factory=SecretManifestSpec)
    network: NetworkManifestSpec = Field(default_factory=NetworkManifestSpec)
    filesystem: FilesystemManifestSpec = Field(default_factory=FilesystemManifestSpec)
    healthcheck: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_consistent_boundaries(self) -> _ManifestBoundaryMixin:
        if self.runtime_version != self.worker.runtime_version:
            raise ValueError("Plugin and Worker runtime versions must match")
        if self.secret.references != self.sandbox.secret_references:
            raise ValueError("Secret references must match the Sandbox Profile")
        if self.network.enabled != self.sandbox.network_enabled:
            raise ValueError("Network policy must match the Sandbox Profile")
        if self.network.allowlist != self.sandbox.allowed_networks:
            raise ValueError("Network allowlists must match")
        if self.filesystem.writable != self.sandbox.filesystem_writable:
            raise ValueError("Filesystem policy must match the Sandbox Profile")
        if self.filesystem.working_directory != self.sandbox.working_directory:
            raise ValueError("Working directories must match")
        return self


class PluginManifestV1(_ManifestBoundaryMixin):
    """Backward-compatible Phase 18 schema; unknown legacy metadata is preserved."""

    model_config = ConfigDict(extra="allow", frozen=True)

    schema_version: Literal["v1"] = "v1"

    @classmethod
    def from_yaml(cls, content: str) -> PluginManifestV1 | PluginManifestV2:
        return load_plugin_manifest(content)


class PluginManifestV2(_ManifestBoundaryMixin):
    """Strict schema: every undeclared field is rejected fail closed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v2"]
    provider_requirements: SandboxProviderRequirements = Field(
        default_factory=SandboxProviderRequirements
    )


PluginManifest = PluginManifestV1
VersionedPluginManifest = PluginManifestV1 | PluginManifestV2


def load_plugin_manifest(content: str) -> VersionedPluginManifest:
    """Dispatch by explicit schema version while treating legacy files as V1."""

    raw = yaml.safe_load(content)
    if not isinstance(raw, dict):
        raise ValueError("Plugin manifest must contain a YAML mapping")
    version = raw.get("schema_version", "v1")
    if version == "v1":
        return PluginManifestV1.model_validate(raw)
    if version == "v2":
        return PluginManifestV2.model_validate(raw)
    raise ValueError(f"Unsupported plugin manifest schema version: {version}")
