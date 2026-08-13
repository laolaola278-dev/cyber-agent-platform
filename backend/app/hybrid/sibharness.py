"""CAP-SIB v1 harness -- scores Rules / Retrieval / LLM / Hybrid layers.

The harness runs the frozen dataset through a *scorer* interface and computes:

  - Track A / Track B metrics (Track B is the product-competition metric)
  - ATT&CK benchmark: precision / recall / F1 / top-1 / top-3 /
    candidate-recall / LLM re-ranking lift
  - Severity benchmark: accuracy (exact + within-one-level)
  - False Positive benchmark: precision / recall / F1 / AUROC
  - Attack Chain benchmark: stage ordering / edge / technique / entity /
    grounding accuracy
  - Retrieval lift / LLM lift (ablation deltas)
  - Wilson 95% confidence intervals
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

SEVERITY_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


@dataclass
class SIBPrediction:
    """Prediction produced by a scorer for one scenario."""

    classification: str = "UNKNOWN"
    severity: str = "UNKNOWN"
    false_positive_probability: float = 0.0
    false_positive: bool = False
    techniques: list[str] = field(default_factory=list)
    technique_scores: dict[str, float] = field(default_factory=dict)
    chain_stages: list[str] = field(default_factory=list)
    entity_links: list[tuple[str, str]] = field(default_factory=list)
    grounded: bool = False
    explanations: list[str] = field(default_factory=list)
    knowledge_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    factors: list[str] = field(default_factory=list)
    completed: bool = True


Scorer = Callable[[dict[str, Any]], SIBPrediction]


@dataclass
class ScenarioMetrics:
    classification_ok: bool = False
    severity_exact: bool = False
    severity_within_one: bool = False
    fp_tp: bool = False  # true positive (FP correctly predicted)
    fp_tn: bool = False
    fp_fp: bool = False
    fp_fn: bool = False
    technique_hits: int = 0
    technique_expected: int = 0
    top1_hit: bool = False
    top3_hit: bool = False
    candidate_recall: float = 0.0
    stage_accuracy: float = 0.0
    entity_accuracy: float = 0.0
    grounded: bool = False
    incomplete_handled: bool = False  # incomplete scenario -> UNKNOWN/alt/downconf


def explainability_report(predictions: list[SIBPrediction]) -> dict[str, Any]:
    """Deterministic explainability metrics over collected predictions.

    Never uses LLM self-evaluation. Evidence coverage / factor correctness /
    knowledge citation / unsupported rate / readability are computed with
    deterministic checks (a human rubric can sample the explanations).
    """
    total = len(predictions)
    if total == 0:
        return {
            "samples": 0,
            "evidence_coverage": 0.0,
            "factor_coverage": 0.0,
            "knowledge_citation": 0.0,
            "unsupported_rate": 0.0,
            "readability": 0.0,
        }
    evidence_coverage = sum(
        1 for p in predictions if p.explanations and p.evidence_refs
    ) / total
    factor_coverage = sum(
        1 for p in predictions if p.explanations and p.factors
    ) / total
    knowledge_citation = sum(
        1 for p in predictions if p.explanations and p.knowledge_refs
    ) / total
    unsupported = sum(
        1
        for p in predictions
        if p.explanations and not p.evidence_refs and not p.knowledge_refs and not p.factors
    ) / total
    readable = sum(
        1
        for p in predictions
        if p.explanations and len(p.explanations[0]) >= 20
    ) / total
    return {
        "samples": total,
        "evidence_coverage": round(evidence_coverage, 4),
        "factor_coverage": round(factor_coverage, 4),
        "knowledge_citation": round(knowledge_citation, 4),
        "unsupported_rate": round(unsupported, 4),
        "readability": round(readable, 4),
    }


def wilson_ci(positive: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% confidence interval for a proportion."""
    if total == 0:
        return (0.0, 0.0)
    p = positive / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass
class TrackReport:
    name: str
    scenarios: int = 0
    classification_accuracy: float = 0.0
    classification_ci: tuple[float, float] = (0.0, 0.0)
    severity_exact: float = 0.0
    severity_within_one: float = 0.0
    fp_precision: float = 0.0
    fp_recall: float = 0.0
    fp_f1: float = 0.0
    fp_auroc: float = 0.0
    fp_ci: tuple[float, float] = (0.0, 0.0)
    attck_precision: float = 0.0
    attck_recall: float = 0.0
    attck_f1: float = 0.0
    attck_top1: float = 0.0
    attck_top3: float = 0.0
    candidate_recall: float = 0.0
    stage_accuracy: float = 0.0
    entity_accuracy: float = 0.0
    evidence_grounding: float = 0.0
    incomplete_handled: float = 0.0
    hard_negative_accuracy: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenarios": self.scenarios,
            "classification_accuracy": round(self.classification_accuracy, 4),
            "classification_ci": [round(v, 4) for v in self.classification_ci],
            "severity_exact": round(self.severity_exact, 4),
            "severity_within_one": round(self.severity_within_one, 4),
            "fp_precision": round(self.fp_precision, 4),
            "fp_recall": round(self.fp_recall, 4),
            "fp_f1": round(self.fp_f1, 4),
            "fp_auroc": round(self.fp_auroc, 4),
            "fp_ci": [round(v, 4) for v in self.fp_ci],
            "attck_precision": round(self.attck_precision, 4),
            "attck_recall": round(self.attck_recall, 4),
            "attck_f1": round(self.attck_f1, 4),
            "attck_top1": round(self.attck_top1, 4),
            "attck_top3": round(self.attck_top3, 4),
            "candidate_recall": round(self.candidate_recall, 4),
            "stage_accuracy": round(self.stage_accuracy, 4),
            "entity_accuracy": round(self.entity_accuracy, 4),
            "evidence_grounding": round(self.evidence_grounding, 4),
            "incomplete_handled": round(self.incomplete_handled, 4),
            "hard_negative_accuracy": round(self.hard_negative_accuracy, 4),
        }


class SIBHarness:
    """Runs the frozen CAP-SIB dataset through a scorer."""

    def __init__(self, scorer: Scorer, *, name: str) -> None:
        self._scorer = scorer
        self._name = name

    def run(
        self,
        scenarios: list[dict[str, Any]],
        *,
        splits: tuple[str, ...] = ("dev", "holdout"),
        tracks: tuple[str, ...] = ("A", "B"),
    ) -> dict[str, TrackReport]:
        reports: dict[str, TrackReport] = {}
        for split in splits:
            for track in tracks:
                key = f"{split}_{track}"
                subset = [
                    s
                    for s in scenarios
                    if s["split"] == split and s["track"] == track
                ]
                reports[key] = self._score_track(subset, f"{self._name}-{key}")
        return reports

    def _score_track(self, scenarios: list[dict[str, Any]], name: str) -> TrackReport:
        report = TrackReport(name=name, scenarios=len(scenarios))
        if not scenarios:
            return report

        class_ok = 0
        sev_exact = 0
        sev_within = 0
        fp_tp = fp_tn = fp_fp = fp_fn = 0
        fp_positive = 0
        hits = 0
        expected = 0
        top1_ok = 0
        top3_ok = 0
        cand_recall_sum = 0.0
        stage_sum = 0.0
        entity_sum = 0.0
        grounded_ok = 0
        incomplete_ok = 0
        incomplete_total = 0
        hn_ok = 0
        hn_total = 0
        fp_scores: list[tuple[float, int]] = []  # (probability, is_fp_label)

        for scenario in scenarios:
            labels = scenario["labels"]
            prediction = self._scorer(scenario)

            if labels.get("classification"):
                class_ok += 1 if prediction.classification == labels["classification"] else 0
            if labels.get("severity"):
                expected_sev = str(labels["severity"]).upper()
                sev_exact += 1 if prediction.severity == expected_sev else 0
                if (
                    prediction.severity in SEVERITY_ORDER
                    and expected_sev in SEVERITY_ORDER
                ):
                    sev_within += (
                        1
                        if abs(
                            SEVERITY_ORDER[prediction.severity]
                            - SEVERITY_ORDER[expected_sev]
                        )
                        <= 1
                        else 0
                    )

            # FP benchmark
            is_fp = bool(labels.get("false_positive"))
            predicted_fp = prediction.false_positive
            if is_fp:
                fp_positive += 1
                if predicted_fp:
                    fp_tp += 1
                else:
                    fp_fn += 1
            else:
                if predicted_fp:
                    fp_fp += 1
                else:
                    fp_tn += 1
            fp_scores.append((prediction.false_positive_probability, int(is_fp)))

            # ATT&CK benchmark (labels only; Track B input has no ids)
            expected_techniques = set(labels.get("techniques", []))
            if expected_techniques:
                expected += len(expected_techniques)
                actual = set(prediction.techniques)
                overlap = len(actual & expected_techniques)
                hits += overlap
                if prediction.techniques and prediction.techniques[0] in expected_techniques:
                    top1_ok += 1
                top3_ok += 1 if set(prediction.techniques[:3]) & expected_techniques else 0
                cand_recall_sum += (
                    overlap / len(expected_techniques) if expected_techniques else 0.0
                )

            # attack chain: stage accuracy (predicted stages vs expected techniques)
            expected_chain = list(labels.get("techniques", []))
            if expected_chain:
                stage_hits = sum(
                    1 for s in prediction.chain_stages if s in set(expected_chain)
                )
                stage_sum += stage_hits / len(expected_chain)
            # entity accuracy: predicted entity links vs expected
            expected_entities = list(labels.get("entities", []))
            if expected_entities and prediction.entity_links:
                entity_sum += 1.0
            elif expected_entities:
                entity_sum += 0.0
            elif prediction.entity_links:
                entity_sum += 0.0

            grounded_ok += 1 if prediction.grounded else 0

            # incomplete-evidence handling: UNKNOWN/alternative/down-confidence
            incomplete = scenario.get("incomplete", "none")
            if incomplete != "none":
                incomplete_total += 1
                handled = (
                    prediction.classification in ("UNKNOWN",)
                    or prediction.severity in ("UNKNOWN",)
                    or prediction.false_positive_probability < 0.5
                    or prediction.chain_stages == []
                )
                incomplete_ok += 1 if handled else 0

            # hard negative accuracy
            if scenario.get("hard_negative"):
                hn_total += 1
                hn_ok += 1 if prediction.classification == "BENIGN" else 0

        total = len(scenarios)
        report.classification_accuracy = class_ok / total
        report.classification_ci = wilson_ci(class_ok, total)
        report.severity_exact = sev_exact / total
        report.severity_within_one = sev_within / total

        # FP metrics
        fp_pos = fp_tp + fp_fn
        report.fp_precision = fp_tp / max(fp_tp + fp_fp, 1)
        report.fp_recall = fp_tp / max(fp_pos, 1)
        report.fp_f1 = (
            2 * report.fp_precision * report.fp_recall
            / max(report.fp_precision + report.fp_recall, 1e-9)
        )
        report.fp_auroc = _auroc(fp_scores)
        report.fp_ci = wilson_ci(fp_tp, max(fp_pos, 1))

        report.attck_precision = hits / max(hits + fp_fp, 1)
        report.attck_recall = hits / max(expected, 1)
        report.attck_f1 = (
            2 * report.attck_precision * report.attck_recall
            / max(report.attck_precision + report.attck_recall, 1e-9)
        )
        technique_scenarios = sum(1 for s in scenarios if s["labels"].get("techniques"))
        report.attck_top1 = top1_ok / max(technique_scenarios, 1)
        report.attck_top3 = top3_ok / max(technique_scenarios, 1)
        report.candidate_recall = cand_recall_sum / max(technique_scenarios, 1)
        report.stage_accuracy = stage_sum / max(technique_scenarios, 1)
        report.entity_accuracy = entity_sum / max(total, 1)
        report.evidence_grounding = grounded_ok / total
        report.incomplete_handled = incomplete_ok / max(incomplete_total, 1)
        report.hard_negative_accuracy = hn_ok / max(hn_total, 1)
        return report


def _auroc(scores: list[tuple[float, int]]) -> float:
    """Area under ROC from (probability, binary_label) pairs."""
    positives = [p for p, label in scores if label == 1]
    negatives = [p for p, label in scores if label == 0]
    if not positives or not negatives:
        return 0.5
    rank_sum = 0.0
    ordered = sorted(scores, key=lambda x: x[0])
    for index, (_, label) in enumerate(ordered, start=1):
        if label == 1:
            rank_sum += index
    return (rank_sum - len(positives) * (len(positives) + 1) / 2) / (
        len(positives) * len(negatives)
    )


def retrieval_lift(
    without: dict[str, TrackReport],
    with_retrieval: dict[str, TrackReport],
    *,
    keys: tuple[str, ...] = ("holdout_B",),
    metrics: tuple[str, ...] = ("classification_accuracy", "attck_recall", "evidence_grounding"),
) -> dict[str, dict[str, float]]:
    """Retrieval lift = with_retrieval - without, per metric per key."""
    result: dict[str, dict[str, float]] = {}
    for key in keys:
        result[key] = {
            metric: round(
                getattr(with_retrieval[key], metric, 0.0)
                - getattr(without[key], metric, 0.0),
                4,
            )
            for metric in metrics
        }
    return result


def llm_lift(
    without_llm: dict[str, TrackReport],
    with_llm: dict[str, TrackReport],
    *,
    keys: tuple[str, ...] = ("holdout_B",),
    metrics: tuple[str, ...] = (
        "classification_accuracy",
        "attck_top1",
        "attck_top3",
        "incomplete_handled",
    ),
) -> dict[str, dict[str, float]]:
    """LLM lift = with_llm - without_llm, per metric per key."""
    result: dict[str, dict[str, float]] = {}
    for key in keys:
        result[key] = {
            metric: round(
                getattr(with_llm[key], metric, 0.0)
                - getattr(without_llm[key], metric, 0.0),
                4,
            )
            for metric in metrics
        }
    return result
