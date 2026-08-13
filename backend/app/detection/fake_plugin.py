"""Non-operational Detection Plugin used for framework verification."""

from datetime import UTC, datetime

from app.detection.contracts import DetectionPluginContext, DetectionRecord
from app.detection.normalizer import DetectionResultNormalizer
from app.schemas.detection import DetectionResult, RawSecurityEvent


class FakeDetectionPlugin:
    """Consume synthetic in-memory records without invoking any real detection tool."""

    name = "fake-detection"
    version = "1.0.0"
    capabilities = frozenset(
        {
            "network.detect",
            "host.detect",
            "log.detect",
            "ids.detect",
            "traffic.detect",
            "event.detect",
            "ioc.detect",
            "rule.detect",
        }
    )
    permissions = frozenset({"detection.execute", "evidence.read"})

    def __init__(self) -> None:
        self.initialized = False

    async def initialize(self, context: DetectionPluginContext) -> None:
        self.initialized = True

    async def collect(self, context: DetectionPluginContext) -> list[DetectionRecord]:
        records = context.input.get("fake_events", [])
        return [dict(item) for item in records if isinstance(item, dict)]

    async def parse(
        self, records: list[DetectionRecord], context: DetectionPluginContext
    ) -> list[DetectionRecord]:
        return records

    async def detect(
        self, records: list[DetectionRecord], context: DetectionPluginContext
    ) -> DetectionResult:
        events = [self._event(record, context) for record in records]
        return DetectionResult(
            success=True,
            plugin_name=self.name,
            plugin_version=self.version,
            events=events,
            records_collected=len(records),
            metadata={"source": "synthetic", "parser": "structured-json"},
        )

    async def normalize(self, result: DetectionResult) -> DetectionResult:
        return DetectionResultNormalizer.normalize_result(result)

    async def shutdown(self) -> None:
        self.initialized = False

    @staticmethod
    def _event(record: DetectionRecord, context: DetectionPluginContext) -> RawSecurityEvent:
        payload = dict(record)
        payload.setdefault("timestamp", datetime.now(UTC))
        payload.setdefault("asset_ids", [context.asset_id])
        return RawSecurityEvent.model_validate(payload)
