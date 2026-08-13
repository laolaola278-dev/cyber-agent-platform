"""AssessmentResult to persistent Finding normalization."""

from uuid import UUID

from app.assessment.fingerprint import FingerprintProvider, SHA256FingerprintProvider
from app.assessment.risk import RiskEngine
from app.exceptions import AssessmentValidationError
from app.models import (
    Asset,
    Evidence,
    Finding,
    FindingAsset,
    FindingEvidence,
    FindingKnowledge,
    FindingReference,
    Knowledge,
    KnowledgeVersion,
)
from app.schemas.assessment import AssessmentResult, RawFinding


class ResultNormalizer:
    """Validate cross-domain references and create normalized Finding aggregates."""

    def __init__(
        self,
        risk_engine: RiskEngine,
        fingerprint_provider: FingerprintProvider | None = None,
    ) -> None:
        self._risk_engine = risk_engine
        self._fingerprints = fingerprint_provider or SHA256FingerprintProvider()

    def normalize(
        self,
        *,
        assessment_task_id: UUID,
        asset: Asset,
        result: AssessmentResult,
        evidence: dict[UUID, Evidence],
        knowledge: dict[UUID, tuple[Knowledge, KnowledgeVersion]],
    ) -> list[Finding]:
        normalized: list[Finding] = []
        for raw in result.findings:
            self._validate_links(raw, evidence, knowledge)
            knowledge_rows = [knowledge[item][0] for item in raw.knowledge_ids]
            risk = self._risk_engine.assess(raw, knowledge_rows, asset)
            finding = Finding(
                assessment_task_id=assessment_task_id,
                fingerprint=self._fingerprints.fingerprint(raw, result.plugin_name, asset.id),
                title=raw.title,
                severity=raw.severity.value,
                confidence=raw.confidence.value,
                description=raw.description,
                affected_asset=raw.affected_asset,
                plugin=result.plugin_name,
                tool=raw.tool,
                rule=raw.rule,
                risk_level=risk.level.value,
                risk_score=risk.score,
                status="NEW",
                attributes={**raw.attributes, "risk_reasons": list(risk.reasons)},
            )
            finding.references = [FindingReference(url=url) for url in raw.references]
            finding.evidence_links = [
                FindingEvidence(evidence_id=evidence_id) for evidence_id in raw.evidence_ids
            ]
            finding.knowledge_links = [
                FindingKnowledge(
                    knowledge_id=knowledge_id,
                    knowledge_version_id=knowledge[knowledge_id][1].id,
                )
                for knowledge_id in raw.knowledge_ids
            ]
            finding.asset_links = [FindingAsset(asset_id=asset.id)]
            normalized.append(finding)
        return normalized

    @staticmethod
    def fingerprint(raw: RawFinding, plugin_name: str, asset_id: UUID) -> str:
        """Compatibility facade for callers introduced in Phase 6."""

        return SHA256FingerprintProvider().fingerprint(raw, plugin_name, asset_id)

    @staticmethod
    def _validate_links(
        raw: RawFinding,
        evidence: dict[UUID, Evidence],
        knowledge: dict[UUID, tuple[Knowledge, KnowledgeVersion]],
    ) -> None:
        missing_evidence = set(raw.evidence_ids) - set(evidence)
        missing_knowledge = set(raw.knowledge_ids) - set(knowledge)
        if missing_evidence or missing_knowledge:
            raise AssessmentValidationError(
                "Assessment result references unknown platform entities",
                details={
                    "evidence": sorted(str(item) for item in missing_evidence),
                    "knowledge": sorted(str(item) for item in missing_knowledge),
                },
            )
