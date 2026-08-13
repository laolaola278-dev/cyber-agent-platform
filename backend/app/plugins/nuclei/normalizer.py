"""Nuclei JSONL to plugin-neutral AssessmentResult normalization."""

import re
from typing import Any

from app.core.enums import FindingConfidence, FindingSeverity
from app.exceptions import AssessmentValidationError
from app.schemas.assessment import AssessmentResult, RawFinding

_IDENTIFIER_PATTERNS = {
    "CVE": re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE),
    "CWE": re.compile(r"^CWE-\d+$", re.IGNORECASE),
    "ATTACK_TECHNIQUE": re.compile(r"^T\d{4}(?:\.\d{3})?$", re.IGNORECASE),
}


class NucleiResultNormalizer:
    """Preserve Nuclei identity/evidence while producing CAP RawFinding values."""

    def normalize(self, records: tuple[dict[str, object], ...]) -> list[RawFinding]:
        return [self._record(item) for item in records]

    def assessment_result(
        self,
        records: tuple[dict[str, object], ...],
        *,
        plugin_name: str,
        plugin_version: str,
        requests_made: int,
        metadata: dict[str, object] | None = None,
    ) -> AssessmentResult:
        return AssessmentResult(
            success=True,
            plugin_name=plugin_name,
            plugin_version=plugin_version,
            findings=self.normalize(records),
            requests_made=requests_made,
            metadata=metadata or {},
        )

    def _record(self, record: dict[str, object]) -> RawFinding:
        info = self._mapping(record.get("info"))
        classification = self._mapping(info.get("classification"))
        template_id = self._text(record.get("template-id") or record.get("templateID"))
        if not template_id:
            raise AssessmentValidationError("Nuclei finding is missing template identity")
        matched_at = self._text(record.get("matched-at") or record.get("matched"))
        host = self._text(record.get("host"))
        affected = matched_at or host
        if not affected:
            raise AssessmentValidationError("Nuclei finding is missing affected target")
        severity = self._severity(info.get("severity"))
        references = self._strings(info.get("reference"))
        knowledge_references = self._knowledge_references(classification, info)
        for knowledge_type, external_id in knowledge_references:
            if knowledge_type == "CVE":
                references.append(f"https://nvd.nist.gov/vuln/detail/{external_id}")
            elif knowledge_type == "CWE":
                references.append(f"https://cwe.mitre.org/data/definitions/{external_id[4:]}.html")
        evidence = {
            key: record[key]
            for key in (
                "matcher-name",
                "extractor-name",
                "extracted-results",
                "request",
                "response",
                "curl-command",
                "timestamp",
                "ip",
                "type",
            )
            if key in record
        }
        matcher_name = self._text(record.get("matcher-name"))
        return RawFinding(
            title=self._text(info.get("name")) or template_id,
            severity=severity,
            confidence=self._confidence(record),
            description=self._text(info.get("description")),
            affected_asset=affected,
            references=references,
            tool="nuclei",
            rule=template_id,
            unique_id_from_tool="|".join(
                part for part in (template_id, matcher_name, matched_at or host) if part
            ),
            attributes={
                "template_id": template_id,
                "template_path": self._text(record.get("template-path")),
                "matcher_name": matcher_name or None,
                "protocol": self._text(record.get("type")) or None,
                "tags": self._strings(info.get("tags")),
                "classification": classification,
                "knowledge_references": [
                    {"type": kind, "external_id": external_id}
                    for kind, external_id in knowledge_references
                ],
                "evidence": evidence,
                "raw_metadata": self._mapping(record.get("metadata")),
            },
        )

    def _knowledge_references(
        self, classification: dict[str, Any], info: dict[str, Any]
    ) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        for key, kind in (("cve-id", "CVE"), ("cwe-id", "CWE"), ("cpe", "CPE")):
            for value in self._strings(classification.get(key)):
                candidates.append((kind, value.upper() if kind != "CPE" else value))
        for tag in self._strings(info.get("tags")):
            for kind, pattern in _IDENTIFIER_PATTERNS.items():
                if pattern.match(tag):
                    candidates.append((kind, tag.upper()))
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _mapping(value: object) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _strings(value: object) -> list[str]:
        if isinstance(value, str):
            source = value.split(",")
        elif isinstance(value, list):
            source = value
        else:
            return []
        return list(dict.fromkeys(str(item).strip() for item in source if str(item).strip()))

    @staticmethod
    def _text(value: object) -> str:
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _severity(value: object) -> FindingSeverity:
        normalized = str(value or "info").strip().upper()
        if normalized == "UNKNOWN":
            normalized = "INFO"
        try:
            return FindingSeverity(normalized)
        except ValueError as error:
            raise AssessmentValidationError(
                "Nuclei returned an unsupported severity", details={"severity": normalized}
            ) from error

    @staticmethod
    def _confidence(record: dict[str, object]) -> FindingConfidence:
        if record.get("matcher-status") is False:
            return FindingConfidence.LOW
        return FindingConfidence.HIGH
