"""Zeek Detection Plugin consuming Telemetry-delivered records only."""

from app.detection.contracts import DetectionPluginContext, DetectionRecord
from app.exceptions import DetectionExecutionError, DetectionValidationError
from app.plugins.zeek.normalizer import ZeekResultNormalizer
from app.schemas.detection import DetectionResult


class ZeekDetectionPlugin:
    """Detection lifecycle with Adapter-only source access and no framework calls."""

    name = "zeek-detection"
    version = "1.0.0"
    capabilities = frozenset(
        {
            "network.detect",
            "log.detect",
            "traffic.detect",
            "event.detect",
            "ioc.detect",
            "rule.detect",
        }
    )
    permissions = frozenset({"detection.execute", "evidence.read"})

    def __init__(self, normalizer: ZeekResultNormalizer | None = None) -> None:
        self._normalizer = normalizer or ZeekResultNormalizer()
        self._initialized = False
        self._source_id = ""
        self._collection_metadata: dict[str, object] = {}

    async def initialize(self, context: DetectionPluginContext) -> None:
        if context.granted_permissions != self.permissions:
            raise DetectionValidationError("Zeek Plugin permissions are invalid")
        source_id = context.input.get("data_source_id")
        if not isinstance(source_id, str):
            raise DetectionValidationError(
                "Zeek Plugin requires a platform-configured data_source_id"
            )
        telemetry_records = context.input.get("telemetry_records")
        if not isinstance(telemetry_records, list):
            raise DetectionValidationError("Zeek Plugin requires Telemetry-delivered records")
        self._source_id = source_id.strip().casefold()
        self._collection_metadata = {"telemetry_required": True}
        self._initialized = True

    async def collect(self, context: DetectionPluginContext) -> list[DetectionRecord]:
        self._require_initialized()
        telemetry_records = context.input.get("telemetry_records")
        if not isinstance(telemetry_records, list):
            raise DetectionValidationError("Zeek Plugin requires Telemetry-delivered records")
        records: list[DetectionRecord] = []
        for item in telemetry_records:
            if not isinstance(item, dict) or not isinstance(item.get("payload"), dict):
                raise DetectionValidationError("Zeek Telemetry record envelope is invalid")
            records.append(item)
        self._collection_metadata.update(
            {
                "data_source_id": self._source_id,
                "records_from_telemetry": len(records),
                "input_format": "jsonl",
            }
        )
        return records

    async def parse(
        self, records: list[DetectionRecord], context: DetectionPluginContext
    ) -> list[DetectionRecord]:
        self._require_initialized()
        del context
        if any(not isinstance(record, dict) for record in records):
            raise DetectionValidationError("Zeek Telemetry record must be an object")
        return records

    async def detect(
        self, records: list[DetectionRecord], context: DetectionPluginContext
    ) -> DetectionResult:
        self._require_initialized()
        return self._normalizer.detection_result(
            records,
            plugin_name=self.name,
            plugin_version=self.version,
            asset_id=context.asset_id,
            source_id=self._source_id,
            collection_metadata=self._collection_metadata,
        )

    async def normalize(self, result: DetectionResult) -> DetectionResult:
        self._require_initialized()
        if result.plugin_name != self.name or result.plugin_version != self.version:
            raise DetectionValidationError("Zeek result identity is invalid")
        if any(event.tool != "zeek" for event in result.events):
            raise DetectionValidationError("Zeek result contains a foreign tool event")
        return result

    async def shutdown(self) -> None:
        self._initialized = False
        self._source_id = ""
        self._collection_metadata = {}

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise DetectionExecutionError("Zeek Plugin is not initialized")
