"""Suricata EVE JSON to plugin-neutral DetectionResult normalization."""

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.core.enums import FindingConfidence, FindingSeverity
from app.exceptions import DetectionValidationError
from app.schemas.detection import DetectionResult, RawSecurityEvent

_DATETIME = TypeAdapter(datetime)
_IDENTIFIER_PATTERNS = {
    "ATTACK": re.compile(r"^(?:attack\.)?(T\d{4}(?:\.\d{3})?)$", re.IGNORECASE),
    "CAPEC": re.compile(r"^(?:capec[.:-]?)(\d+)$", re.IGNORECASE),
    "CVE": re.compile(r"^(CVE-\d{4}-\d{4,})$", re.IGNORECASE),
}
_URL = re.compile(r"^https?://", re.IGNORECASE)


class SuricataResultNormalizer:
    """Preserve rule identity and bounded telemetry while producing CAP events."""

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
        self, record: dict[str, object], *, asset_id: object, source_id: str
    ) -> RawSecurityEvent:
        event_type = self._text(record.get("event_type")).casefold()
        timestamp = self._timestamp(record.get("timestamp"))
        alert = self._mapping(record.get("alert"))
        signature = self._text(alert.get("signature"))
        sid = self._integer(alert.get("signature_id"))
        gid = self._integer(alert.get("gid"))
        rev = self._integer(alert.get("rev"))
        metadata = self._merge_metadata(record, alert)
        knowledge = self._knowledge(metadata)
        references = self._references(metadata, knowledge)
        src_ip = self._text(record.get("src_ip"))
        dest_ip = self._text(record.get("dest_ip"))
        iocs = list(dict.fromkeys(value for value in (src_ip, dest_ip) if value))
        rule_identity = self._rule_identity(gid, sid, rev, signature, event_type)
        flow_id = self._text(record.get("flow_id"))
        unique_id = "|".join(
            part
            for part in (
                source_id,
                event_type,
                flow_id,
                rule_identity,
                timestamp.isoformat(),
            )
            if part
        )
        attributes: dict[str, Any] = {
            "eve_event_type": event_type,
            "category": self._text(alert.get("category")) or None,
            "signature": signature or None,
            "sid": sid,
            "gid": gid,
            "rev": rev,
            "action": self._text(alert.get("action")) or None,
            "flow_id": flow_id or None,
            "protocol": self._text(record.get("proto")) or None,
            "app_protocol": self._text(record.get("app_proto")) or None,
            "source_ip": src_ip or None,
            "source_port": self._integer(record.get("src_port")),
            "destination_ip": dest_ip or None,
            "destination_port": self._integer(record.get("dest_port")),
            "knowledge_references": [f"{kind}:{external_id}" for kind, external_id in knowledge],
        }
        attributes.update(self._event_details(event_type, record))
        return RawSecurityEvent(
            event_type=f"network.{event_type}",
            source=f"suricata:{source_id}",
            severity=self._severity(alert.get("severity"), event_type),
            confidence=self._confidence(event_type, alert),
            timestamp=timestamp,
            asset_ids=[asset_id],
            references=references,
            tool="suricata",
            rule=rule_identity or None,
            iocs=iocs,
            unique_id_from_tool=unique_id,
            attributes=attributes,
        )

    @staticmethod
    def _event_details(event_type: str, record: dict[str, object]) -> dict[str, object]:
        block = record.get(event_type)
        if not isinstance(block, dict):
            return {}
        allowed: dict[str, tuple[str, ...]] = {
            "flow": ("state", "reason", "alerted", "pkts_toserver", "pkts_toclient"),
            "stats": ("uptime",),
            "dns": ("type", "rrname", "rrtype", "rcode"),
            "http": ("hostname", "url", "http_method", "status", "protocol"),
            "tls": ("subject", "issuerdn", "sni", "version", "fingerprint"),
            "fileinfo": ("filename", "state", "stored", "size", "md5", "sha1", "sha256"),
        }
        return {
            f"{event_type}_{key}": value
            for key in allowed.get(event_type, ())
            if (value := block.get(key)) is not None and isinstance(value, str | int | float | bool)
        }

    @staticmethod
    def _severity(value: object, event_type: str) -> FindingSeverity:
        if event_type != "alert":
            return FindingSeverity.INFO
        mapping = {
            1: FindingSeverity.CRITICAL,
            2: FindingSeverity.HIGH,
            3: FindingSeverity.MEDIUM,
            4: FindingSeverity.LOW,
        }
        try:
            return mapping[int(value)]
        except (KeyError, TypeError, ValueError) as error:
            raise DetectionValidationError(
                "Suricata alert has unsupported severity", details={"severity": value}
            ) from error

    @staticmethod
    def _confidence(event_type: str, alert: dict[str, Any]) -> FindingConfidence:
        if event_type != "alert":
            return FindingConfidence.LOW
        action = str(alert.get("action") or "allowed").casefold()
        return FindingConfidence.HIGH if action in {"blocked", "drop"} else FindingConfidence.MEDIUM

    def _merge_metadata(self, record: dict[str, object], alert: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for candidate in (record.get("metadata"), alert.get("metadata")):
            if isinstance(candidate, dict):
                for key, value in candidate.items():
                    merged.setdefault(str(key).casefold(), value)
        return merged

    def _knowledge(self, metadata: dict[str, Any]) -> list[tuple[str, str]]:
        candidates = self._strings(
            [
                *self._flatten(metadata.values()),
                *self._strings(metadata.get("tag")),
                *self._strings(metadata.get("tags")),
            ]
        )
        output: list[tuple[str, str]] = []
        for candidate in candidates:
            for kind, pattern in _IDENTIFIER_PATTERNS.items():
                match = pattern.match(candidate)
                if match:
                    external_id = match.group(1).upper()
                    if kind == "CAPEC":
                        external_id = f"CAPEC-{external_id}"
                    output.append((kind, external_id))
        return list(dict.fromkeys(output))

    def _references(self, metadata: dict[str, Any], knowledge: list[tuple[str, str]]) -> list[str]:
        references = [
            value
            for value in self._strings(metadata.get("reference"))
            + self._strings(metadata.get("references"))
            if _URL.match(value)
        ]
        for kind, external_id in knowledge:
            if kind == "ATTACK":
                references.append(
                    f"https://attack.mitre.org/techniques/{external_id.replace('.', '/')}/"
                )
            elif kind == "CAPEC":
                references.append(
                    f"https://capec.mitre.org/data/definitions/{external_id.removeprefix('CAPEC-')}.html"
                )
            elif kind == "CVE":
                references.append(f"https://nvd.nist.gov/vuln/detail/{external_id}")
        return list(dict.fromkeys(references))

    @staticmethod
    def _rule_identity(
        gid: int | None, sid: int | None, rev: int | None, signature: str, event_type: str
    ) -> str:
        if sid is not None:
            return f"{gid or 1}:{sid}:{rev or 0}"
        return signature or event_type

    @staticmethod
    def _timestamp(value: object) -> datetime:
        try:
            return _DATETIME.validate_python(value)
        except ValidationError as error:
            raise DetectionValidationError("Suricata EVE timestamp is invalid") from error

    @staticmethod
    def _mapping(value: object) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _text(value: object) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, int):
            return str(value)
        return ""

    @staticmethod
    def _integer(value: object) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _strings(cls, value: object) -> list[str]:
        if isinstance(value, str):
            source: Iterable[object] = re.split(r"[,\s]+", value)
        elif isinstance(value, list | tuple | set):
            source = value
        else:
            return []
        return list(dict.fromkeys(str(item).strip() for item in source if str(item).strip()))

    @classmethod
    def _flatten(cls, values: Iterable[object]) -> list[object]:
        output: list[object] = []
        for value in values:
            if isinstance(value, list | tuple | set):
                output.extend(value)
            else:
                output.append(value)
        return output
