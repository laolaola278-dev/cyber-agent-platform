"""Notification Plugin SDK and least-privilege execution context."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

from app.schemas.notification import (
    NotificationPlanSpec,
    NotificationResult,
    RenderedNotification,
)


@dataclass(frozen=True, slots=True)
class NotificationPluginContext:
    """No database, Incident, Response, Report or arbitrary execution services."""

    notification_plan_id: UUID
    incident_id: UUID
    response_plan_id: UUID | None
    trace_id: str
    actor: str
    capability: str
    recipients: tuple[str, ...]
    variables: Mapping[str, Any]
    granted_permissions: frozenset[str]


def readonly_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return readonly_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


class NotificationPlugin(Protocol):
    """Lifecycle every notification integration must implement."""

    name: str
    version: str
    description: str
    capabilities: frozenset[str]
    permissions: frozenset[str]
    supports_verification: bool
    sandbox_compatible: bool
    operational_documentation: str

    async def initialize(self, context: NotificationPluginContext) -> None: ...

    async def render(
        self, plan: NotificationPlanSpec, context: NotificationPluginContext
    ) -> RenderedNotification: ...

    async def validate(
        self,
        plan: NotificationPlanSpec,
        rendered: RenderedNotification,
        context: NotificationPluginContext,
    ) -> None: ...

    async def send(
        self,
        plan: NotificationPlanSpec,
        rendered: RenderedNotification,
        context: NotificationPluginContext,
    ) -> NotificationResult: ...

    async def verify(
        self, result: NotificationResult, context: NotificationPluginContext
    ) -> NotificationResult: ...

    async def shutdown(self) -> None: ...

    async def health(self) -> bool: ...
