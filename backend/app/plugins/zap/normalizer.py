"""OWASP ZAP Alert to plugin-neutral AssessmentResult normalization."""

import re

from app.core.enums import FindingConfidence, FindingSeverity
from app.exceptions import AssessmentValidationError
from app.schemas.assessment import AssessmentResult, RawFinding

_IDENTIFIER = re.compile(r"\b(CVE-\d{4}-\d{4,}|CWE-\d+|CAPEC-\d+)\b", re.IGNORECASE)
_OWASP = re.compile(r"\bA(0?[1-9]|10):?20(?:17|21)\b", re.IGNORECASE)


class ZapResultNormalizer:
    """Convert mutable ZAP Alert dictionaries into stable CAP findings."""

    def assessment_result(
        self,
        alerts: tuple[dict[str, object], ...],
        *,
        plugin_name: str,
        plugin_version: str,
        requests_made: int,
        metadata: dict[str, object],
    ) -> AssessmentResult:
        return AssessmentResult(
            success=True,
            plugin_name=plugin_name,
            plugin_version=plugin_version,
            findings=[self._alert(alert) for alert in alerts],
            requests_made=requests_made,
            metadata=metadata,
        )

    def _alert(self, alert: dict[str, object]) -> RawFinding:
        title = self._text(alert.get("alert") or alert.get("name"))
        url = self._text(alert.get("url"))
        plugin_id = self._text(alert.get("pluginId") or alert.get("pluginid"))
        if not title or not url or not plugin_id:
            raise AssessmentValidationError("ZAP Alert is missing alert/url/pluginId")
        cwe_id = self._numeric_identifier("CWE", alert.get("cweid"))
        wasc_id = self._numeric_identifier("WASC", alert.get("wascid"))
        references = self._references(alert.get("reference"))
        knowledge = self._knowledge_references(alert, cwe_id, references)
        if cwe_id:
            references.append(
                f"https://cwe.mitre.org/data/definitions/{cwe_id.split('-', 1)[1]}.html"
            )
        if wasc_id:
            references.append(f"http://projects.webappsec.org/w/page/{wasc_id}")
        evidence = {
            key: alert[key]
            for key in ("method", "param", "attack", "evidence", "other", "messageId")
            if alert.get(key) not in {None, ""}
        }
        owasp_categories = [
            item["external_id"] for item in knowledge if item["type"] == "OWASP_CATEGORY"
        ]
        return RawFinding(
            title=title,
            severity=self._severity(alert.get("risk") or alert.get("riskdesc")),
            confidence=self._confidence(alert.get("confidence")),
            description=self._description(alert),
            affected_asset=url,
            references=list(dict.fromkeys(references)),
            tool="owasp-zap",
            rule=plugin_id,
            unique_id_from_tool="|".join(
                filter(None, (plugin_id, url, self._text(alert.get("param"))))
            ),
            attributes={
                "zap_alert_id": self._text(alert.get("id")) or None,
                "plugin_id": plugin_id,
                "cwe": cwe_id,
                "wasc": wasc_id,
                "owasp_categories": owasp_categories,
                "knowledge_references": knowledge,
                "evidence": evidence,
                "solution": self._text(alert.get("solution")),
                "raw_metadata": {
                    key: value for key, value in alert.items() if key not in {"request", "response"}
                },
            },
        )

    def _knowledge_references(
        self, alert: dict[str, object], cwe_id: str, references: list[str]
    ) -> list[dict[str, str]]:
        values = " ".join(
            [
                self._text(alert.get("alert")),
                self._text(alert.get("description")),
                self._text(alert.get("reference")),
                *references,
            ]
        )
        pairs: list[tuple[str, str]] = []
        if cwe_id:
            pairs.append(("CWE", cwe_id))
        for match in _IDENTIFIER.findall(values):
            external_id = match.upper()
            pairs.append((external_id.split("-", 1)[0], external_id))
        for match in _OWASP.finditer(values):
            index = int(match.group(1))
            pairs.append(("OWASP_CATEGORY", f"A{index:02d}:2021"))
        return [
            {"type": kind, "external_id": external_id} for kind, external_id in dict.fromkeys(pairs)
        ]

    @staticmethod
    def _description(alert: dict[str, object]) -> str:
        parts = [
            ZapResultNormalizer._text(alert.get("description")),
            ZapResultNormalizer._text(alert.get("solution")),
        ]
        return "\n\n".join(part for part in parts if part)

    @staticmethod
    def _references(value: object) -> list[str]:
        if not isinstance(value, str):
            return []
        return [
            item.strip()
            for item in re.split(r"[\r\n]+", value)
            if item.strip().startswith(("http://", "https://"))
        ]

    @staticmethod
    def _numeric_identifier(kind: str, value: object) -> str:
        text = str(value or "").strip()
        return f"{kind}-{text}" if text.isdigit() and int(text) > 0 else ""

    @staticmethod
    def _text(value: object) -> str:
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _severity(value: object) -> FindingSeverity:
        normalized = str(value or "Informational").split()[0].strip().upper()
        aliases = {"INFORMATIONAL": "INFO", "FALSE": "INFO"}
        normalized = aliases.get(normalized, normalized)
        try:
            return FindingSeverity(normalized)
        except ValueError as error:
            raise AssessmentValidationError(
                "ZAP returned unsupported risk", details={"risk": normalized}
            ) from error

    @staticmethod
    def _confidence(value: object) -> FindingConfidence:
        normalized = str(value or "Medium").split()[0].strip().upper()
        aliases = {"FALSE": "LOW", "USER": "HIGH", "CONFIRMED": "HIGH"}
        normalized = aliases.get(normalized, normalized)
        try:
            return FindingConfidence(normalized)
        except ValueError:
            return FindingConfidence.MEDIUM
