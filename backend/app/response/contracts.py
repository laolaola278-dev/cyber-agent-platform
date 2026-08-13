"""Response Plugin SDK contracts and least-privilege execution context."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

from app.schemas.response import ResponsePlanSpec, ResponseResult


@dataclass(frozen=True, slots=True)
class ResponsePluginContext:
    """Narrow execution context without database, Incident, Asset or Report services."""

    response_plan_id: UUID
    incident_id: UUID
    asset_ids: tuple[UUID, ...]
    trace_id: str
    actor: str
    capability: str
    parameters: Mapping[str, Any]
    rollback_parameters: Mapping[str, Any]
    rollback_token: str | None
    granted_permissions: frozenset[str]


def readonly_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Recursively freeze configuration passed across the plugin trust boundary."""

    return MappingProxyType({key: _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return readonly_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


class ResponsePlugin(Protocol):
    """Lifecycle every response integration must implement."""

    name: str
    version: str
    description: str
    capabilities: frozenset[str]
    permissions: frozenset[str]
    supports_approval: bool
    supports_rollback: bool
    sandbox_compatible: bool
    operational_documentation: str

    async def initialize(self, context: ResponsePluginContext) -> None: ...

    async def plan(
        self, plan: ResponsePlanSpec, context: ResponsePluginContext
    ) -> ResponsePlanSpec: ...

    async def validate(self, plan: ResponsePlanSpec, context: ResponsePluginContext) -> None: ...

    async def execute(
        self, plan: ResponsePlanSpec, context: ResponsePluginContext
    ) -> ResponseResult: ...

    async def verify(
        self, result: ResponseResult, context: ResponsePluginContext
    ) -> ResponseResult: ...

    async def rollback(
        self, plan: ResponsePlanSpec, context: ResponsePluginContext
    ) -> ResponseResult: ...

    async def shutdown(self) -> None: ...

    async def health(self) -> bool: ...
