"""Zeek log rows to plugin-neutral DetectionResult normalization."""

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.core.enums import FindingConfidence, FindingSeverity
from app.exceptions import DetectionValidationError
from app.schemas.detection import DetectionResult, RawSecurityEvent

_DATETIME = TypeAdapter(datetime)
_IDENTIFIER = {
    "ATTACK": re.compile(r"^(?:attack\.)?(T\d{4}(?:\.\d{3})?)$", re.IGNORECASE),
    "CAPEC": re.compile(r"^(?:capec[.:-]?)(\d+)$", re.IGNORECASE),
    "CVE": re.compile(r"^(CVE-\d{4}-\d{4,})$", re.IGNORECASE),
}
_URL = re.compile(r"^https?://", re.IGNORECASE)


class ZeekResultNormalizer:
    """Project Zeek's extensible records into bounded CAP events and lineage."""

    def detection_result(
        self,
        records: list[dict[str, object]],
        *,
        plugin_name: str,
        plugin_version: str,
        asset_id: object,
        source_id: str,
        collection_metadata: dict[str, object],
    ) -> DetectionResult:
        events = [self._record(item, asset_id=asset_id, source_id=source_id) for item in records]
        return DetectionResult(
            success=True,
            plugin_name=plugin_name,
            plugin_version=plugin_version,
            events=events,
            records_collected=len(records),
            metadata=collection_metadata,
        )

    def _record(
        self, item: dict[str, object], *, asset_id: object, source_id: str
    ) -> RawSecurityEvent:
        payload = self._mapping(item.get("payload"))
        metadata = self._mapping(item.get("metadata"))
        log_name = self._log_name(payload)
        timestamp = self._timestamp(payload.get("ts"))
        uid = self._text(payload.get("uid")) or self._text(payload.get("fuid"))
        src = self._text(payload.get("id.orig_h"))
        dst = self._text(payload.get("id.resp_h"))
        protocol = self._text(payload.get("proto")) or self._text(payload.get("service"))
        iocs = list(
            dict.fromkeys(
                value
                for value in (
                    src,
                    dst,
                    self._text(payload.get("query")),
                    self._text(payload.get("host")),
                    self._text(payload.get("server_name")),
                )
                if value
            )
        )
        knowledge = self._knowledge(payload)
        references = self._references(payload, knowledge)
        unique_id = "|".join(
            value for value in (source_id, log_name, uid, timestamp.isoformat()) if value
        )
        attributes: dict[str, Any] = {
            "zeek_log": log_name,
            "uid": uid or None,
            "source_ip": src or None,
            "source_port": self._integer(payload.get("id.orig_p")),
            "destination_ip": dst or None,
            "destination_port": self._integer(payload.get("id.resp_p")),
            "protocol": protocol or None,
            "direction": self._direction(payload),
            "asset": {"primary_asset_id": str(asset_id)},
            "evidence_lineage": {
                "source_id": metadata.get("source_id", source_id),
                "line_number": metadata.get("line_number"),
                "raw_record_sha256": metadata.get("raw_record_sha256"),
                "source_sha256": metadata.get("source_sha256"),
                "schema_fingerprint": metadata.get("schema_fingerprint"),
            },
            "zeek_fields": self._allowlisted_fields(log_name, payload),
        }
        return RawSecurityEvent(
            event_type=f"network.zeek.{log_name}",
            source=f"zeek:{source_id}",
            severity=self._severity(log_name, payload),
            confidence=FindingConfidence.HIGH if log_name == "notice" else FindingConfidence.MEDIUM,
            timestamp=timestamp,
            asset_ids=[asset_id],
            references=references,
            tool="zeek",
            rule=self._text(payload.get("note")) or log_name,
            iocs=iocs,
            unique_id_from_tool=unique_id,
            attributes=attributes,
        )

    @staticmethod
    def _allowlisted_fields(log_name: str, payload: dict[str, object]) -> dict[str, object]:
        fields = {
            "conn": (
                "uid",
                "id.orig_h",
                "id.orig_p",
                "id.resp_h",
                "id.resp_p",
                "proto",
                "service",
                "duration",
                "orig_bytes",
                "resp_bytes",
                "conn_state",
            ),
            "dns": ("uid", "query", "qtype_name", "qtype", "rcode_name", "answers"),
            "http": (
                "uid",
                "method",
                "host",
                "uri",
                "user_agent",
                "status_code",
                "request_body_len",
                "response_body_len",
            ),
            "ssl": (
                "uid",
                "version",
                "cipher",
                "server_name",
                "subject",
                "issuer",
                "validation_status",
            ),
            "files": (
                "fuid",
                "uid",
                "mime_type",
                "filename",
                "total_bytes",
                "md5",
                "sha1",
                "sha256",
            ),
            "notice": ("uid", "note", "msg", "src", "dst", "p", "sub", "actions"),
        }
        return {
            key: value
            for key in fields.get(log_name, ())
            if (value := payload.get(key)) is not None
            and isinstance(value, str | int | float | bool | list)
        }

    @staticmethod
    def _severity(log_name: str, payload: dict[str, object]) -> FindingSeverity:
        if log_name == "notice":
            note = str(payload.get("note", "")).casefold()
            if any(token in note for token in ("scan", "attack", "malware", "exploit")):
                return FindingSeverity.HIGH
            return FindingSeverity.MEDIUM
        return FindingSeverity.INFO

    @staticmethod
    def _direction(payload: dict[str, object]) -> str:
        if payload.get("orig_bytes") is not None or payload.get("id.orig_h") is not None:
            return "originator_to_responder"
        return "unknown"

    @staticmethod
    def _log_name(payload: dict[str, object]) -> str:
        value = payload.get("_log") or payload.get("log") or payload.get("log_type")
        if isinstance(value, str) and value.strip():
            return value.strip().casefold().removesuffix(".log")
        if isinstance(payload.get("note"), str):
            return "notice"
        if isinstance(payload.get("fuid"), str):
            return "files"
        if isinstance(payload.get("query"), str):
            return "dns"
        if isinstance(payload.get("method"), str) or isinstance(payload.get("uri"), str):
            return "http"
        if isinstance(payload.get("server_name"), str) or isinstance(payload.get("cipher"), str):
            return "ssl"
        return "conn"

    @staticmethod
    def _timestamp(value: object) -> datetime:
        try:
            if isinstance(value, int | float):
                return datetime.fromtimestamp(value, tz=UTC)
            result = _DATETIME.validate_python(value)
            return result.replace(tzinfo=UTC) if result.tzinfo is None else result
        except (ValidationError, TypeError, ValueError) as error:
            raise DetectionValidationError("Zeek timestamp is invalid") from error

    def _knowledge(self, payload: dict[str, object]) -> list[tuple[str, str]]:
        values = [str(value).strip() for value in payload.values() if isinstance(value, str)]
        output: list[tuple[str, str]] = []
        for value in values:
            for kind, pattern in _IDENTIFIER.items():
                match = pattern.match(value)
                if match:
                    identifier = match.group(1).upper()
                    if kind == "CAPEC":
                        identifier = f"CAPEC-{identifier}"
                    output.append((kind, identifier))
        return list(dict.fromkeys(output))

    def _references(
        self, payload: dict[str, object], knowledge: list[tuple[str, str]]
    ) -> list[str]:
        refs = [value for value in payload.values() if isinstance(value, str) and _URL.match(value)]
        for kind, identifier in knowledge:
            if kind == "ATTACK":
                refs.append(f"https://attack.mitre.org/techniques/{identifier.replace('.', '/')}/")
            elif kind == "CAPEC":
                refs.append(
                    f"https://capec.mitre.org/data/definitions/{identifier.removeprefix('CAPEC-')}.html"
                )
            elif kind == "CVE":
                refs.append(f"https://nvd.nist.gov/vuln/detail/{identifier}")
        return list(dict.fromkeys(refs))

    @staticmethod
    def _mapping(value: object) -> dict[str, object]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _text(value: object) -> str:
        return (
            value.strip()
            if isinstance(value, str)
            else str(value) if isinstance(value, int) else ""
        )

    @staticmethod
    def _integer(value: object) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
