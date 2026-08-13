"""CAP-AQB v2 metrics layer (Phase 28.1, spec section 15).

Redesigned benchmark reporting that separates *quality* from *safety*:

  * Quality metrics are computed ONLY over scenarios the benchmark intended
    to succeed / partially succeed. Expected-BLOCKED scenarios (login,
    captcha, paywall, robots, SSRF, rate-limit, ...) are NEVER counted as
    quality failures -- blocking them is the desired outcome.
  * Safety gates (SSRF Block Rate etc.) are computed over the restricted
    scenario categories and must remain at 100%.

v2 adds the production-path probes that v1 could not observe:
  * Resume Accuracy      -- checkpoint resume continues the SAME run
  * Integrity Verify Rate-- evidence triple sha256 verification passes

Both probes are fed from real worker-path / evidence-storage executions
(see tests/test_phase_28_1_aqb_v2.py) so the report reflects the actual
Worker/Sandbox chain, not a synthetic model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.acquisition.evaluation import ScenarioResult

# Categories whose expected outcome is BLOCKED (hard gates).
_EXPECTED_BLOCKED_CATEGORIES = {
    "redirect_private_ip",
    "dns_rebinding",
    "http_401",
    "http_403",
    "login_page",
    "captcha",
    "paywall",
    "robots_disallow",
    "oversized",
}

# Category -> expected source-type strategy (mirrors evaluation helper).
_EXPECTED_STRATEGY: dict[str, str] = {
    "pdf": "DOCUMENT",
    "docx": "DOCUMENT",
    "xlsx": "DOCUMENT",
    "json_api": "PUBLIC_JSON_API",
    "pagination": "STATIC_HTML",
    "infinite_scroll": "STATIC_HTML",
    "static_html": "STATIC_HTML",
    "dynamic_html": "STATIC_HTML",
}

# Expected outcome class -> terminal status.
_EXPECTED_STATUS = {
    "success": "COMPLETE",
    "blocked": "BLOCKED",
    "partial": "PARTIAL",
}

_SSRF_CATEGORIES = {"redirect_private_ip", "dns_rebinding"}


@dataclass
class Probe:
    """A real execution probe (resume / integrity) collected from the chain."""

    ok: int = 0
    total: int = 0

    @property
    def rate(self) -> float:
        return self.ok / max(self.total, 1)


@dataclass
class AQBV2Metrics:
    """CAP-AQB v2 report (Phase 28.1)."""

    total: int = 0
    expected_success: int = 0
    expected_blocked: int = 0
    expected_partial: int = 0

    # -- quality (expected-success / expected-partial only) -----------------
    outcome_classification_accuracy: float = 0.0  # status == expected status
    successful_acquisition_rate: float = 0.0  # success-class -> COMPLETE
    correct_block_rate: float = 0.0  # blocked-class -> BLOCKED (safety)
    correct_partial_rate: float = 0.0  # partial-class -> PARTIAL
    strategy_accuracy: float = 0.0  # planner chose right source type
    pagination_accuracy: float = 0.0  # pagination reached expected pages
    evidence_lineage_rate: float = 0.0  # success-case evidence chain intact
    completeness_accuracy: float = 0.0  # COMPLETE runs with high coverage

    # -- production-path probes (real worker/sandbox/evidence executions) ---
    resume_accuracy: float = 0.0
    integrity_verification_rate: float = 0.0

    # -- safety --------------------------------------------------------------
    ssrf_block_rate: float = 0.0

    # quality failures ONLY from expected-success / expected-partial runs
    quality_failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "expected_success": self.expected_success,
            "expected_blocked": self.expected_blocked,
            "expected_partial": self.expected_partial,
            "outcome_classification_accuracy": round(self.outcome_classification_accuracy, 4),
            "successful_acquisition_rate": round(self.successful_acquisition_rate, 4),
            "correct_block_rate": round(self.correct_block_rate, 4),
            "correct_partial_rate": round(self.correct_partial_rate, 4),
            "strategy_accuracy": round(self.strategy_accuracy, 4),
            "pagination_accuracy": round(self.pagination_accuracy, 4),
            "evidence_lineage_rate": round(self.evidence_lineage_rate, 4),
            "completeness_accuracy": round(self.completeness_accuracy, 4),
            "resume_accuracy": round(self.resume_accuracy, 4),
            "integrity_verification_rate": round(self.integrity_verification_rate, 4),
            "ssrf_block_rate": round(self.ssrf_block_rate, 4),
            "quality_failures": self.quality_failures[:20],
        }


def _expected_status_for(scenario: ScenarioResult) -> str:
    if scenario.outcome_class in _EXPECTED_STATUS:
        return _EXPECTED_STATUS[scenario.outcome_class]
    # fallback: explicit expected_status carried by the harness
    return scenario.expected_status or "COMPLETE"


def compute_aqb_v2(
    results: list[ScenarioResult],
    *,
    resume: Probe | None = None,
    integrity: Probe | None = None,
) -> AQBV2Metrics:
    """Aggregate v2 metrics from per-scenario results plus real probes."""
    metrics = AQBV2Metrics(total=len(results))

    blocked_ok = 0
    success_ok = 0
    partial_ok = 0
    classification_ok = 0
    strategy_ok = 0
    pagination_ok = 0
    pagination_total = 0
    lineage_ok = 0
    complete_total = 0
    coverage_ok = 0
    ssrf_ok = 0
    ssrf_total = 0

    for r in results:
        expected_status = _expected_status_for(r)
        if r.status == expected_status:
            classification_ok += 1
        # strategy + SSRF observations apply to EVERY scenario
        if r.strategy_ok:
            strategy_ok += 1
        if r.category in _SSRF_CATEGORIES:
            ssrf_total += 1
            if r.status == "BLOCKED":
                ssrf_ok += 1
        if r.outcome_class == "blocked":
            metrics.expected_blocked += 1
            if r.status == "BLOCKED":
                blocked_ok += 1
            # expected BLOCKED is a safety outcome, never a quality failure
            continue
        if r.outcome_class == "partial":
            metrics.expected_partial += 1
            if r.status == "PARTIAL":
                partial_ok += 1
        else:
            metrics.expected_success += 1
            if r.status == "COMPLETE":
                success_ok += 1
            else:
                # a genuine quality failure (expected success, did not complete)
                metrics.quality_failures.append(
                    f"{r.scenario_id}:{r.category} expected={expected_status} got={r.status}"
                )
        if r.category == "pagination":
            pagination_total += 1
            if r.pagination_ok:
                pagination_ok += 1
        if r.status == "COMPLETE":
            complete_total += 1
            if r.lineage_complete:
                lineage_ok += 1
            if r.coverage_ok:
                coverage_ok += 1

    metrics.outcome_classification_accuracy = classification_ok / max(len(results), 1)
    metrics.successful_acquisition_rate = success_ok / max(metrics.expected_success, 1)
    metrics.correct_block_rate = blocked_ok / max(metrics.expected_blocked, 1)
    metrics.correct_partial_rate = partial_ok / max(metrics.expected_partial, 1)
    metrics.strategy_accuracy = strategy_ok / max(len(results), 1)
    metrics.pagination_accuracy = pagination_ok / max(pagination_total, 1)
    metrics.evidence_lineage_rate = lineage_ok / max(complete_total, 1)
    metrics.completeness_accuracy = coverage_ok / max(complete_total, 1)
    metrics.ssrf_block_rate = ssrf_ok / max(ssrf_total, 1)

    if resume is not None:
        metrics.resume_accuracy = resume.rate
    if integrity is not None:
        metrics.integrity_verification_rate = integrity.rate
    return metrics
