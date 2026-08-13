"""Validated Agent manifest loading and registry registration boundary."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, HttpUrl

from app.schemas.registry import AgentRegister
from app.services.registry import AgentRegistryService


class RuntimeSpec(BaseModel):
    """Runtime implementation and configuration declared by an Agent."""

    entrypoint: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)


class NetworkPolicy(BaseModel):
    """Deliberately narrow network policy for the Phase 2 acquisition Agent."""

    allowed_methods: set[str] = Field(default_factory=lambda: {"GET"})
    public_web_only: bool = True
    allowed_origins: list[HttpUrl] = Field(default_factory=list)


class AgentManifest(BaseModel):
    """Portable, validated definition used by Registry and Runtime Manager."""

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    minimum_runtime_version: str = Field(default="1.0.0", min_length=1, max_length=64)
    platform_version: str = Field(default="0.2.1", min_length=1, max_length=64)
    sdk_version: str = Field(default="1.0.0", min_length=1, max_length=64)
    description: str | None = None
    author: str = "system"
    permissions: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    runtime: RuntimeSpec
    network_policy: NetworkPolicy = Field(default_factory=NetworkPolicy)
    resource_limit: dict[str, Any] = Field(default_factory=dict)
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    healthcheck: dict[str, Any] = Field(default_factory=dict)

    def as_registration(self) -> AgentRegister:
        """Translate the stable manifest into the existing Registry contract."""

        return AgentRegister(
            name=self.name,
            version=self.version,
            description=self.description,
            author=self.author,
            permissions=self.permissions,
            capabilities=self.capabilities,
            tools=self.tools,
            minimum_runtime_version=self.minimum_runtime_version,
            platform_version=self.platform_version,
            sdk_version=self.sdk_version,
            runtime={"entrypoint": self.runtime.entrypoint, **self.runtime.config},
            network_policy=self.network_policy.model_dump(mode="json"),
            resource_limit=self.resource_limit,
            approval_policy=self.approval_policy,
        )


class ManifestLoader:
    """Parse, validate and register Agent manifests without executing them."""

    def load(self, path: Path) -> AgentManifest:
        """Load a single UTF-8 YAML manifest from a trusted platform directory."""

        if path.name != "manifest.yaml":
            raise ValueError("Agent manifest must be named manifest.yaml")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Agent manifest must contain a YAML mapping")
        return AgentManifest.model_validate(raw)

    async def register(
        self, manifest: AgentManifest, service: AgentRegistryService, *, trace_id: str
    ) -> "Agent":
        """Create or update the Registry definition from the validated manifest."""

        return await service.register(manifest.as_registration(), trace_id=trace_id)


from app.models import Agent  # noqa: E402
