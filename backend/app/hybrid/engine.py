"""Phase 27 -- Hybrid Security Intelligence Engine.

Orchestrates the full deterministic pipeline:

  Facts -> Retrieval -> Candidates -> Deterministic Scoring -> LLM Rank/Explain
  -> Hypothesis -> Evidence Validation -> Conclusion

The LLM (when enabled) only ranks and explains. Every judgment (severity,
technique mapping, FP probability, chain stages) is first computed
deterministically; the model can never override it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.hybrid.attck import (
    AttackTechniqueCandidateGenerator,
    HybridATTCMapper,
    TechniqueMapping,
)
from app.hybrid.chaingraph import (
    AttackChainBuilder,
    AttackChainGraph,
    order_stages_deterministically,
)
from app.hybrid.confidence import CalibratedConfidence, ConfidenceCalibrator, ConfidenceInputs
from app.hybrid.explanation import Explanation, ExplanationBuilder
from app.hybrid.extract import extract_facts_from_event
from app.hybrid.facts import FactExtractionResult, SecurityFact
from app.hybrid.falsepositive import FalsePositiveAssessment, FalsePositiveScorer
from app.hybrid.grounding import EvidenceGroundingEngine, GroundedClaim
from app.hybrid.retrieval import KnowledgeRetriever, NoopKnowledgeRetriever, RetrievedKnowledge
from app.hybrid.severity import (
    DeterministicSeverityEngine,
    SeverityAssessment,
    SeverityFactor,
)


@dataclass
class HybridEngineConfig:
    use_llm: bool = False
    use_retrieval: bool = True
    severity_threshold_critical: float = 0.75
    attck_threshold: float = 0.35
    fp_threshold: float = 0.7


@dataclass
class HybridTriageOutput:
    classification: str
    severity: SeverityAssessment
    false_positive: FalsePositiveAssessment
    technique_mapping: TechniqueMapping
    facts: list[SecurityFact]
    knowledge_hits: list[RetrievedKnowledge]
    grounded_claims: list[GroundedClaim]
    calibrated_confidence: CalibratedConfidence
    explanation: Explanation
    chain_graph: AttackChainGraph | None = None
    chain_stages: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "severity": self.severity.to_dict(),
            "false_positive": self.false_positive.to_dict(),
            "technique_mapping": self.technique_mapping.to_dict(),
            "fact_count": len(self.facts),
            "knowledge_hits": [
                {"type": k.knowledge_type, "id": k.external_id, "score": k.score}
                for k in self.knowledge_hits
            ],
            "grounding": {
                "claims": [g.to_dict() for g in self.grounded_claims],
                "aggregate": EvidenceGroundingEngine.aggregate(self.grounded_claims),
            },
            "confidence": self.calibrated_confidence.to_dict(),
            "explanation": self.explanation.to_dict(),
            "chain_stages": self.chain_stages,
            "uncertainties": self.uncertainties,
        }


class HybridEngine:
    """Composable hybrid engine.

    Ablation knobs (use_llm / use_retrieval) let the evaluation harness
    measure the contribution of each layer.
    """

    def __init__(
        self,
        *,
        knowledge: KnowledgeRetriever | None = None,
        llm_ranker: Any | None = None,
        config: HybridEngineConfig | None = None,
        severity_engine: DeterministicSeverityEngine | None = None,
        fp_scorer: FalsePositiveScorer | None = None,
        calibrator: ConfidenceCalibrator | None = None,
        grounding: EvidenceGroundingEngine | None = None,
        chain_builder: AttackChainBuilder | None = None,
    ) -> None:
        self._config = config or HybridEngineConfig()
        self._knowledge = knowledge if self._config.use_retrieval else NoopKnowledgeRetriever()
        self._ranker = llm_ranker if self._config.use_llm else None
        self._severity = severity_engine or DeterministicSeverityEngine()
        self._fp = fp_scorer or FalsePositiveScorer(threshold=self._config.fp_threshold)
        self._calibrator = calibrator or ConfidenceCalibrator()
        self._grounding = grounding or EvidenceGroundingEngine()
        self._chains = chain_builder or AttackChainBuilder()
        generator = AttackTechniqueCandidateGenerator(knowledge=self._knowledge)
        self._attck = HybridATTCMapper(
            generator, llm=self._ranker, threshold=self._config.attck_threshold
        )
        self._explainer = ExplanationBuilder(llm=self._ranker)

    # -- public API --------------------------------------------------------

    async def triage(
        self,
        *,
        source: dict[str, Any],
        context: dict[str, Any] | None = None,
        events: list[dict[str, Any]] | None = None,
        data_blocks: list[dict[str, Any]] | None = None,
    ) -> HybridTriageOutput:
        ctx = context or {}
        events = events or []
        evidence_refs: set[str] = set(source.get("evidence_refs", [])) or {
            f"evidence:{source.get('id', 'evt')}"
        }
        for event in events:
            evidence_refs.update(event.get("evidence_refs", []))
        self._grounding.set_known_evidence(evidence_refs)

        # 0. prompt injection boundary: untrusted data blocks fail closed.
        self._assert_no_injection(data_blocks)

        # 1. deterministic facts
        extraction = self._extract(source, events)

        # 2. knowledge retrieval over facts -- must mirror the ATT&CK
        # generator's lookup semantics (behavior facts query the ATT&CK
        # catalog directly) so knowledge_hits is populated for citations.
        hits: list[RetrievedKnowledge] = []
        for fact in extraction.facts:
            if fact.fact_type in ("technique", "vulnerability"):
                hits.extend(await self._knowledge.lookup_fact(fact, limit=3))
            else:
                hits.extend(
                    await self._knowledge.lookup(knowledge_type="ATT&CK", query=fact.value, limit=3)
                )

        # 3. severity (deterministic)
        severity = self._severity.assess(
            finding_severity=source.get("severity"),
            cvss=_to_float(ctx.get("cvss")),
            epss=_to_float(ctx.get("epss")),
            in_kev=bool(ctx.get("in_kev") or ctx.get("kev")),
            asset_criticality=source.get("criticality") or ctx.get("asset_criticality"),
            exposed=bool(ctx.get("exposed", True)),
            evidence_confidence=0.9 if extraction.facts else 0.4,
            detection_confidence=source.get("detection_confidence") or source.get("confidence"),
        )

        # 4. ATT&CK mapping (deterministic + optional LLM rank)
        mapping = await self._attck.map(
            facts=extraction.facts,
            event_techniques=source.get("techniques") or ctx.get("expected_techniques"),
        )

        # 5. false positive scoring (deterministic)
        fp = self._fp.score(
            rule=source.get("rule") or source.get("title"),
            event_type=source.get("event_type"),
            frequency_30d=_to_int(ctx.get("frequency_30d")),
            asset_criticality=source.get("criticality") or ctx.get("asset_criticality"),
            historical_fp_rate=_to_float(ctx.get("historical_fp_rate")),
            evidence_quality=0.9 if extraction.facts else 0.4,
            detection_confidence=source.get("confidence"),
            known_benign_match=bool(
                ctx.get("known_benign_match")
                or ctx.get("false_positive_hint")
                or ctx.get("known_benign_context")
            ),
        )

        # 6. classification (deterministic, derived from severity + FP)
        # No platform evidence references -> UNKNOWN (never assert without a
        # source). Extracted entities are indirect signals, not evidence.
        evidence_present = bool(source.get("evidence_refs"))
        if not evidence_present:
            severity = SeverityAssessment(
                severity="UNKNOWN",
                score=0.0,
                confidence=0.3,
                factors=[SeverityFactor("evidence", "missing", 0.0)],
            )
        classification = "UNKNOWN" if not evidence_present else self._classify(severity, fp, ctx)

        # 7. confidence calibration (never model self-report)
        calibrated = self._calibrator.calibrate(
            ConfidenceInputs(
                evidence_quality=0.9 if extraction.facts else 0.4,
                deterministic_score=severity.score,
                knowledge_match=max([k.score for k in hits], default=0.0),
                model_agreement=None,
            )
        )

        # 8. grounding of LLM-agnostic claims (evidence refs from source)
        claims: list[tuple[str, list[str]]] = [
            (
                f"classification={classification}",
                source.get("evidence_refs") or [f"evidence:{source.get('id', 'evt')}"],
            ),
            (
                f"severity={severity.severity}",
                source.get("evidence_refs") or [f"evidence:{source.get('id', 'evt')}"],
            ),
        ]
        if mapping.technique_id:
            claims.append(
                (
                    f"technique={mapping.technique_id}",
                    source.get("evidence_refs") or [f"evidence:{source.get('id', 'evt')}"],
                )
            )
        grounded = self._grounding.ground_claims(claims)

        # 9. explanation (deterministic factors, LLM rewording optional)
        explanation = await self._explainer.build(
            statement=(
                f"Severity {severity.severity} because {_factor_summary(severity)}; "
                f"FP probability {fp.false_positive_probability:.0%}; "
                f"technique {mapping.technique_id or 'UNKNOWN'}."
            ),
            factors=[f.name for f in severity.factors] + [f.name for f in fp.factors],
            evidence_refs=source.get("evidence_refs") or [],
            knowledge_refs=[k.external_id for k in hits],
        )

        # 10. attack chain graph (deterministic; LLM analyses only if present)
        chain_graph, chain_stages = await self._build_chain(
            source=source, facts=extraction.facts, events=events
        )

        # 11. uncertainties
        uncertainties: list[str] = []
        if not extraction.facts:
            uncertainties.append("no platform facts extracted")
        if not mapping.technique_id:
            uncertainties.append("ATT&CK mapping is UNKNOWN")
        if fp.confidence < 0.5:
            uncertainties.append("FP estimate low confidence")

        return HybridTriageOutput(
            classification=classification,
            severity=severity,
            false_positive=fp,
            technique_mapping=mapping,
            facts=extraction.facts,
            knowledge_hits=hits,
            grounded_claims=grounded,
            calibrated_confidence=calibrated,
            explanation=explanation,
            chain_graph=chain_graph,
            chain_stages=chain_stages,
            uncertainties=uncertainties,
        )

    # -- internals ---------------------------------------------------------

    def _extract(
        self, source: dict[str, Any], events: list[dict[str, Any]]
    ) -> FactExtractionResult:
        event_payloads = events or [
            {
                "id": source.get("id", "evt"),
                "event_type": source.get("event_type") or source.get("title"),
                "severity": source.get("severity"),
                "confidence": source.get("confidence"),
                "timestamp": source.get("timestamp"),
                "rule": source.get("rule"),
                "entities": source.get("entities"),
                "attributes": source.get("attributes") or {},
            }
        ]
        result = FactExtractionResult()
        for payload in event_payloads:
            extracted = extract_facts_from_event(payload)
            result.facts.extend(extracted.facts)
            result.notes.extend(extracted.notes)
        return result

    @staticmethod
    def _assert_no_injection(data_blocks: list[dict[str, Any]] | None) -> None:
        """Fail closed on untrusted data containing prompt injection.

        Phase 27.1: untrusted text is FIRST normalized (NFKC / zero-width /
        HTML-entity / bounded Base64) so obfuscated injections are caught,
        then the Phase 26 prompt-injection isolation boundary runs on the
        normalized payloads. Raising here means the engine never proceeds
        with induced content.
        """
        if not data_blocks:
            return
        from app.agent.failures import ProviderUnavailableError
        from app.hybrid.normalize import injection_resistant

        resisted, _report, _note = injection_resistant(data_blocks, isolation_func=None)
        if not resisted:
            raise ProviderUnavailableError(
                "Hybrid engine rejected untrusted data (prompt injection); fail closed."
            )

    async def _build_chain(
        self,
        *,
        source: dict[str, Any],
        facts: list[SecurityFact],
        events: list[dict[str, Any]],
    ) -> tuple[AttackChainGraph, list[str]]:
        event_payloads = events or [
            {
                "id": source.get("id", "evt"),
                "title": source.get("title"),
                "timestamp": source.get("timestamp"),
                "severity": source.get("severity"),
                "entities": source.get("entities"),
                "techniques": source.get("techniques"),
            }
        ]
        technique_ids = [
            c.technique_id
            for c in await AttackTechniqueCandidateGenerator(knowledge=self._knowledge).generate(
                facts=facts, event_techniques=source.get("techniques")
            )
        ]
        graph = self._chains.build(
            events=event_payloads,
            facts=facts,
            technique_candidates=technique_ids,
        )
        stages = order_stages_deterministically(graph)
        if not stages:
            stages = technique_ids[:3]
        return graph, stages

    @staticmethod
    def _classify(
        severity: SeverityAssessment,
        fp: FalsePositiveAssessment,
        ctx: dict[str, Any],
    ) -> str:
        # deterministic classification: FP -> BENIGN, severity drives the rest
        if fp.likely_false_positive:
            return "BENIGN"
        if severity.severity in ("HIGH", "CRITICAL"):
            return "MALICIOUS"
        if severity.severity == "MEDIUM":
            return "SUSPICIOUS"
        return "UNKNOWN"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _factor_summary(severity: SeverityAssessment) -> str:
    names = [f.name for f in severity.factors]
    return ", ".join(names) if names else "no factors"
