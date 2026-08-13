"""Suricata Detection Plugin implementation."""

from app.detection.contracts import DetectionPluginContext, DetectionRecord
from app.exceptions import DetectionExecutionError, DetectionValidationError
from app.plugins.suricata.normalizer import SuricataResultNormalizer
from app.schemas.detection import DetectionResult
from app.tools.suricata import SuricataAdapter


class SuricataDetectionPlugin:
    """First real Detection Plugin; all EVE file and JSON access stays in Adapter."""

    name = "suricata-detection"
    version = "1.0.0"
    capabilities = frozenset(
        {
            "network.detect",
            "ids.detect",
            "traffic.detect",
            "event.detect",
            "ioc.detect",
            "rule.detect",
        }
    )
    permissions = frozenset({"detection.execute", "evidence.read"})

    def __init__(
        self,
        adapter: SuricataAdapter,
        normalizer: SuricataResultNormalizer | None = None,
    ) -> None:
        self._adapter = adapter
        self._normalizer = normalizer or SuricataResultNormalizer()
        self._initialized = False
        self._source_id = ""
        self._collection_metadata: dict[str, object] = {}

    async def initialize(self, context: DetectionPluginContext) -> None:
        if context.granted_permissions != self.permissions:
            raise DetectionValidationError("Suricata Plugin permissions are invalid")
        source_id = context.input.get("data_source_id")
        if not isinstance(source_id, str):
            raise DetectionValidationError(
                "Suricata Plugin requires a platform-configured data_source_id"
            )
        self._adapter.require_source(source_id)
        self._source_id = source_id.strip().casefold()
        self._collection_metadata = {}
        self._initialized = True

    async def collect(self, context: DetectionPluginContext) -> list[DetectionRecord]:
        self._require_initialized()
        result = self._adapter.collect(self._source_id)
        self._collection_metadata = {
            "data_source_id": result.source_id,
            "bytes_read": result.bytes_read,
            "lines_read": result.lines_read,
            "sandboxed": True,
            "input_format": "eve-jsonl",
        }
        return list(result.records)

    async def parse(
        self, records: list[DetectionRecord], context: DetectionPluginContext
    ) -> list[DetectionRecord]:
        self._require_initialized()
        if any(not isinstance(record, dict) for record in records):
            raise DetectionValidationError("Suricata Adapter returned a non-object EVE record")
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
            raise DetectionValidationError("Suricata result identity is invalid")
        if any(event.tool != "suricata" for event in result.events):
            raise DetectionValidationError("Suricata result contains a foreign tool event")
        return result

    async def shutdown(self) -> None:
        self._initialized = False
        self._source_id = ""
        self._collection_metadata = {}

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise DetectionExecutionError("Suricata Plugin is not initialized")
