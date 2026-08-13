"""Tool-neutral SecurityEvent normalization helpers."""

import hashlib
import json
from typing import Any
from uuid import UUID

from app.schemas.detection import DetectionResult, RawSecurityEvent

_ALLOWED_ATTRIBUTE_TYPES = (str, int, float, bool, type(None))


class DetectionResultNormalizer:
    """Sanitize plugin events and generate stable platform fingerprints."""

    @staticmethod
    def normalize_result(result: DetectionResult) -> DetectionResult:
        events = [DetectionResultNormalizer.normalize_event(item) for item in result.events]
        return result.model_copy(
            update={
                "events": events,
                "metadata": DetectionResultNormalizer.safe_attributes(result.metadata),
            }
        )

    @staticmethod
    def normalize_event(event: RawSecurityEvent) -> RawSecurityEvent:
        return event.model_copy(
            update={
                "event_type": event.event_type.strip().casefold(),
                "source": event.source.strip().casefold(),
                "attributes": DetectionResultNormalizer.safe_attributes(event.attributes),
            }
        )

    @staticmethod
    def safe_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
        """Keep bounded scalar/list metadata and discard tool-native payload trees."""

        output: dict[str, Any] = {}
        for key, value in list(attributes.items())[:100]:
            normalized_key = str(key).strip()[:128]
            if not normalized_key:
                continue
            if isinstance(value, _ALLOWED_ATTRIBUTE_TYPES):
                output[normalized_key] = value
            elif isinstance(value, list):
                output[normalized_key] = [
                    item for item in value[:100] if isinstance(item, _ALLOWED_ATTRIBUTE_TYPES)
                ]
        return output

    @staticmethod
    def fingerprint(event: RawSecurityEvent, plugin: str, primary_asset_id: UUID) -> str:
        payload = {
            "asset": str(primary_asset_id),
            "event_type": event.event_type.strip().casefold(),
            "plugin": plugin.strip().casefold(),
            "rule": (event.rule or "").strip().casefold(),
            "source": event.source.strip().casefold(),
            "tool_id": (event.unique_id_from_tool or "").strip().casefold(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
