"""Validated importer framework with an initial JSON implementation."""

from typing import Any

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from app.exceptions import KnowledgeValidationError
from app.schemas.knowledge import KnowledgeRecord


class JSONKnowledgeImporter:
    format_name = "json"

    def parse(self, payload: Any, *, source: str) -> list[KnowledgeRecord]:
        """Parse one object, an array, or an object containing a records array."""

        raw_records: Any = payload
        if isinstance(payload, dict) and "records" in payload:
            raw_records = payload["records"]
        if isinstance(raw_records, dict):
            raw_records = [raw_records]
        if not isinstance(raw_records, list):
            raise KnowledgeValidationError("JSON import payload must contain an object or array")
        prepared: list[dict[str, Any]] = []
        for raw in raw_records:
            if not isinstance(raw, dict):
                raise KnowledgeValidationError("Every JSON knowledge record must be an object")
            item = dict(raw)
            item.setdefault("source", source)
            if item["source"].casefold() != source.casefold():
                raise KnowledgeValidationError("Record source must match the import source")
            prepared.append(item)
        try:
            return TypeAdapter(list[KnowledgeRecord]).validate_python(prepared)
        except PydanticValidationError as error:
            raise KnowledgeValidationError(
                "Invalid JSON knowledge record",
                details={"errors": error.errors(include_context=False)},
            ) from error
