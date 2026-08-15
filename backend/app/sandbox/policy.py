"""Fail-closed policy for platform-level sandbox profiles."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.exceptions import SandboxPolicyViolation
from app.sandbox.profile import SandboxProfile


class SandboxPolicy(BaseModel):
    """Global limits that a plugin manifest cannot weaken."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    allowed_providers: frozenset[str] = frozenset({"memory-sandbox", "subprocess-sandbox"})
    maximum_cpu_millicores: int = Field(default=2000, ge=50, le=16_000)
    maximum_memory_mb: int = Field(default=2048, ge=32, le=65_536)
    maximum_timeout_seconds: int = Field(default=3600, ge=1, le=86_400)
    maximum_readonly_mounts: int = Field(default=16, ge=0, le=64)
    maximum_tmp_mounts: int = Field(default=4, ge=0, le=16)
    allow_network: bool = False
    allow_host_filesystem_write: bool = False


class SandboxPolicyEngine:
    """Validate desired profiles before a provider can create an execution."""

    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self._policy = policy or SandboxPolicy()

    @property
    def policy(self) -> SandboxPolicy:
        return self._policy

    def validate(self, profile: SandboxProfile, provider_name: str) -> None:
        policy = self._policy
        if not policy.enabled:
            raise SandboxPolicyViolation("Sandbox execution is disabled")
        if provider_name not in policy.allowed_providers:
            raise SandboxPolicyViolation("Sandbox provider is not allowlisted")
        if profile.cpu_millicores > policy.maximum_cpu_millicores:
            raise SandboxPolicyViolation("Sandbox CPU limit exceeds platform policy")
        if profile.memory_mb > policy.maximum_memory_mb:
            raise SandboxPolicyViolation("Sandbox memory limit exceeds platform policy")
        if profile.timeout_seconds > policy.maximum_timeout_seconds:
            raise SandboxPolicyViolation("Sandbox timeout exceeds platform policy")
        if len(profile.readonly_mounts) > policy.maximum_readonly_mounts:
            raise SandboxPolicyViolation("Sandbox readonly mount count exceeds platform policy")
        if len(profile.tmp_mounts) > policy.maximum_tmp_mounts:
            raise SandboxPolicyViolation("Sandbox tmp mount count exceeds platform policy")
        if profile.network_enabled and not policy.allow_network:
            raise SandboxPolicyViolation("Sandbox network access is denied by platform policy")
        if profile.filesystem_writable and not policy.allow_host_filesystem_write:
            raise SandboxPolicyViolation("Sandbox host filesystem write access is prohibited")
