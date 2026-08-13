"""Detection Plugin SDK contracts and least-privilege context."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.schemas.detection import DetectionPolicy, DetectionResult

DetectionRecord = dict[str, Any]


@dataclass(frozen=True, slots=True)
class DetectionPluginContext:
    """Narrow context excluding workflow, database, assessment and report services."""

    detection_task_id: UUID
    task_id: UUID
    asset_id: UUID
    trace_id: str
    capabilities: tuple[str, ...]
    policy: DetectionPolicy
    input: dict[str, Any]
    granted_permissions: frozenset[str]


class DetectionPlugin(Protocol):
    """Lifecycle every detection integration must implement."""

    name: str
    version: str
    capabilities: frozenset[str]
    permissions: frozenset[str]

    async def initialize(self, context: DetectionPluginContext) -> None: ...

    async def collect(self, context: DetectionPluginContext) -> list[DetectionRecord]: ...

    async def parse(
        self, records: list[DetectionRecord], context: DetectionPluginContext
    ) -> list[DetectionRecord]: ...

    async def detect(
        self, records: list[DetectionRecord], context: DetectionPluginContext
    ) -> DetectionResult: ...

    async def normalize(self, result: DetectionResult) -> DetectionResult: ...

    async def shutdown(self) -> None: ...
