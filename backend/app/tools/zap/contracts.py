"""Typed OWASP ZAP adapter contracts and resource profiles."""

from dataclasses import dataclass, field
from typing import Protocol

from app.schemas.assessment import ZapPolicy


@dataclass(frozen=True, slots=True)
class ZapSandboxProfile:
    """Deployment-enforced limits for the ZAP daemon execution boundary."""

    cpu_limit: float = 1.0
    memory_limit_mb: int = 1024
    timeout_seconds: int = 600
    network_policy: str = "asset-scope-only"


@dataclass(frozen=True, slots=True)
class ZapExecutionRequest:
    target: str
    policy: ZapPolicy
    active_scan_authorized: bool = False


@dataclass(frozen=True, slots=True)
class ZapExecutionResult:
    alerts: tuple[dict[str, object], ...]
    session_name: str
    context_name: str
    tool_version: str
    mode: str
    scan_policy: str
    scan_scope: tuple[str, ...]
    duration_seconds: float
    urls_discovered: int
    requests_made: int
    alert_summary: dict[str, int] = field(default_factory=dict)


class ZapApiClient(Protocol):
    """Anti-corruption port over zap-api-python or a test double."""

    async def version(self) -> str: ...

    async def new_session(self, name: str, *, overwrite: bool) -> None: ...

    async def remove_session(self, name: str) -> None: ...

    async def new_context(self, name: str) -> str: ...

    async def include_in_context(self, name: str, regex: str) -> None: ...

    async def exclude_from_context(self, name: str, regex: str) -> None: ...

    async def access_url(self, url: str) -> None: ...

    async def wait_for_passive_scan(self, timeout_seconds: int) -> None: ...

    async def spider(
        self, url: str, *, context_name: str, max_depth: int, max_urls: int
    ) -> int: ...

    async def active_scan(
        self, url: str, *, context_id: str, scan_policy: str, timeout_seconds: int
    ) -> None: ...

    async def alerts(self, *, base_url: str, limit: int) -> list[dict[str, object]]: ...
