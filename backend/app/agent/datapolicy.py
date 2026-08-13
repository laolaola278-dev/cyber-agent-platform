"""ModelDataPolicy (v2.0 / Phase 26).

Decides, per data field, whether it may be sent to an external model. Data
classes:

- LOCAL_ONLY:     never leaves the platform (hashed on the model boundary)
- REDACTED:       redacted/truncated copy may leave the platform
- MODEL_ALLOWED:  field is safe to send to the model
- MODEL_FORBIDDEN: field must be rejected if the model asks for it

The policy is configuration-first and fail-closed: anything not explicitly
MODEL_ALLOWED is treated as LOCAL_ONLY.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.agent.injection import analyze_secret_exposure

SECRET_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "password",
        "passwd",
        "token",
        "authorization",
        "cookie",
        "private_key",
        "jwt",
        "session",
        "credential",
        "preshared",
    }
)

SENSITIVE_KEYS: frozenset[str] = frozenset(
    {"email", "phone", "ssn", "national_id", "employee_id", "salary"}
)

# Keys whose values may be sent to the model in the usual investigation flow.
ALLOWED_DOMAIN_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "title",
        "summary",
        "description",
        "severity",
        "confidence",
        "status",
        "source",
        "asset",
        "asset_id",
        "asset_type",
        "finding_id",
        "event_id",
        "incident_id",
        "timestamp",
        "time",
        "technique",
        "tactic",
        "kill_chain",
        "ioc",
        "ip",
        "domain",
        "url",
        "hash",
        "user",
        "host",
        "file",
        "process",
        "command_line",
        "port",
        "protocol",
        "log",
        "category",
        "tags",
        "properties",
        "evidence_refs",
        "knowledge_refs",
        "cve",
        "cwe",
        "capec",
        "kev",
    }
)


class DataClass(StrEnum):
    LOCAL_ONLY = "LOCAL_ONLY"
    REDACTED = "REDACTED"
    MODEL_ALLOWED = "MODEL_ALLOWED"
    MODEL_FORBIDDEN = "MODEL_FORBIDDEN"


@dataclass(frozen=True, slots=True)
class FieldDecision:
    """Decision for one data field."""

    field: str
    data_class: DataClass
    value: Any
    redaction_summary: str = ""


@dataclass(frozen=True, slots=True)
class RedactionReport:
    """What was removed/redacted before reaching the model."""

    local_only_fields: tuple[str, ...] = ()
    redacted_fields: tuple[str, ...] = ()
    forbidden_fields: tuple[str, ...] = ()
    secrets_removed: int = 0
    truncated_characters: int = 0

    @property
    def summary(self) -> str:
        parts = [
            f"local_only={len(self.local_only_fields)}",
            f"redacted={len(self.redacted_fields)}",
            f"forbidden={len(self.forbidden_fields)}",
            f"secrets_removed={self.secrets_removed}",
        ]
        return "; ".join(parts)


class ModelDataPolicy:
    """Decides what model-eligible data may leave the platform."""

    def __init__(
        self,
        *,
        allowed_keys: frozenset[str] | None = None,
        max_field_chars: int = 2048,
        allowed_url_patterns: tuple[str, ...] = (),
    ) -> None:
        self._allowed_keys = allowed_keys or ALLOWED_DOMAIN_KEYS
        self._max_field_chars = max_field_chars
        self._allowed_url_patterns = allowed_url_patterns
        self.last_redaction: str = ""

    def classify_key(self, key: str) -> DataClass:
        normalized = key.casefold().replace("-", "_").strip()
        if normalized in SECRET_KEYS or any(
            part in normalized for part in ("key", "secret", "password", "token", "cookie")
        ):
            return DataClass.MODEL_FORBIDDEN
        if normalized in SENSITIVE_KEYS:
            return DataClass.REDACTED
        if normalized in self._allowed_keys or normalized.startswith("evidence:"):
            return DataClass.MODEL_ALLOWED
        return DataClass.LOCAL_ONLY

    def sanitize_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], RedactionReport]:
        """Return (model-safe payload, redaction report). Fail-closed."""
        sanitized: dict[str, Any] = {}
        local_only: list[str] = []
        redacted_fields: list[str] = []
        forbidden: list[str] = []
        secrets_removed = 0
        truncated = 0

        for key, value in payload.items():
            data_class = self.classify_key(key)
            if data_class == DataClass.MODEL_FORBIDDEN:
                secrets_removed += 1
                forbidden.append(key)
                continue
            if data_class == DataClass.LOCAL_ONLY:
                local_only.append(key)
                sanitized[key] = self._local_marker(value)
                continue
            if data_class == DataClass.REDACTED:
                redacted_fields.append(key)
                sanitized[key] = self._redact_value(value)
                truncated += self._truncated_amount(value)
                continue
            text = str(value)
            if len(text) > self._max_field_chars:
                truncated += len(text) - self._max_field_chars
                text = text[: self._max_field_chars] + "…[truncated]"
            sanitized[key] = text

        report = RedactionReport(
            local_only_fields=tuple(local_only),
            redacted_fields=tuple(redacted_fields),
            forbidden_fields=tuple(forbidden),
            secrets_removed=secrets_removed,
            truncated_characters=truncated,
        )
        self.last_redaction = report.summary
        return sanitized, report

    def validate_outgoing(self, content: str) -> tuple[bool, tuple[str, ...]]:
        """Reject content that still contains secret material (fail-closed)."""
        hits = analyze_secret_exposure(content)
        if hits:
            return False, hits
        for pattern in self._allowed_url_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return False, (f"forbidden-url-pattern:{pattern}",)
        return True, ()

    def _local_marker(self, value: Any) -> str:
        digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
        return f"[local:{digest}]"

    def _redact_value(self, value: Any) -> str:
        text = str(value)
        if len(text) <= 2:
            return "[redacted]"
        return f"{text[:1]}…{text[-1]}"

    @staticmethod
    def _truncated_amount(value: Any) -> int:
        text = str(value)
        return max(0, len(text) - 2)
