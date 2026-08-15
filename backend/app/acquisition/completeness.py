"""Phase 28 -- Completeness Engine (spec 19).

The differentiator vs a plain crawler: after a run (or a step), evaluate how
complete the acquisition is against the plan's expectations and decide the
next verdict: FINISH / RETRY / REPLAN / PARTIAL / BLOCKED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.acquisition.models import (
    CompletenessReport,
    PaginationStrategy,
    Verdict,
)


@dataclass
class CompletenessInput:
    """Raw observations the evaluator consumes."""

    expected_fields: list[str] = field(default_factory=list)
    observed_fields: set[str] = field(default_factory=set)
    expected_time_range: tuple[str, str] | None = None
    observed_timestamps: list[str] = field(default_factory=list)
    pagination: PaginationStrategy | None = None
    record_count: int = 0
    expected_record_count: int | None = None
    duplicates: int = 0
    gaps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    blocked: bool = False
    partial_failure: bool = False


class CompletenessEvaluator:
    """Score coverage/fields/time/pagination and pick the verdict."""

    def evaluate(self, data: CompletenessInput) -> CompletenessReport:
        if data.blocked:
            return CompletenessReport(
                coverage_score=0.0,
                field_completeness=0.0,
                time_coverage=0.0,
                pagination_complete=False,
                duplicates=data.duplicates,
                gaps=data.gaps,
                errors=data.errors,
                confidence=0.0,
                verdict=Verdict.BLOCKED,
            )

        # field completeness
        field_completeness = 1.0
        if data.expected_fields:
            matched = sum(1 for f in data.expected_fields if f in data.observed_fields)
            field_completeness = matched / len(data.expected_fields)

        # time coverage
        time_coverage = 1.0
        if data.expected_time_range:
            time_coverage = self._time_coverage(data.expected_time_range, data.observed_timestamps)

        # pagination completeness
        pagination_complete = True
        if data.pagination is not None and data.pagination.kind != "none":
            budget_hit = (
                data.pagination.pages_fetched >= data.pagination.max_pages
                or data.record_count >= data.pagination.max_records
                or data.pagination.records_seen >= data.pagination.max_records
            )
            pagination_complete = budget_hit

        # coverage score = weighted blend
        coverage_score = (
            0.5 * field_completeness
            + 0.3 * time_coverage
            + 0.2 * (1.0 if pagination_complete else 0.0)
        )
        coverage_score = max(0.0, min(1.0, coverage_score))

        # errors / gaps depress confidence
        error_penalty = min(0.5, 0.1 * len(data.errors))
        gap_penalty = min(0.4, 0.1 * len(data.gaps))
        confidence = max(0.0, min(1.0, coverage_score - error_penalty - gap_penalty))

        verdict = self._verdict(
            field_completeness=field_completeness,
            time_coverage=time_coverage,
            pagination_complete=pagination_complete,
            errors=data.errors,
            gaps=data.gaps,
            record_count=data.record_count,
            expected_record_count=data.expected_record_count,
            partial_failure=data.partial_failure,
        )

        return CompletenessReport(
            coverage_score=round(coverage_score, 4),
            field_completeness=round(field_completeness, 4),
            time_coverage=round(time_coverage, 4),
            pagination_complete=pagination_complete,
            duplicates=data.duplicates,
            gaps=list(data.gaps),
            errors=list(data.errors),
            confidence=round(confidence, 4),
            verdict=verdict,
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _time_coverage(expected: tuple[str, str], observed_timestamps: list[str]) -> float:
        if not observed_timestamps:
            return 0.0  # no temporal evidence at all
        try:
            start = datetime.fromisoformat(expected[0].replace("Z", "+00:00"))
            end = datetime.fromisoformat(expected[1].replace("Z", "+00:00"))
        except ValueError:
            return 1.0
        if start >= end:
            return 1.0
        total = (end - start).total_seconds()
        if total <= 0:
            return 1.0
        covered: set[datetime] = set()
        for stamp in observed_timestamps:
            try:
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=start.tzinfo)
            except ValueError:
                continue
            if start <= parsed <= end:
                covered.add(parsed)
        return round(min(1.0, len(covered) / max(total / 3600.0, 1.0) * 0.05 + 0.5), 4)

    @staticmethod
    def _verdict(
        *,
        field_completeness: float,
        time_coverage: float,
        pagination_complete: bool,
        errors: list[str],
        gaps: list[str],
        record_count: int,
        expected_record_count: int | None,
        partial_failure: bool = False,
    ) -> Verdict:
        if errors:
            return Verdict.RETRY
        if partial_failure:
            return Verdict.PARTIAL
        if gaps and field_completeness < 0.99:
            return Verdict.REPLAN
        if gaps:
            # observed gaps (missing docs/fields) -> never FINISH
            return Verdict.PARTIAL
        if not pagination_complete:
            return Verdict.RETRY
        if expected_record_count is not None and record_count < expected_record_count:
            return Verdict.PARTIAL
        if field_completeness >= 0.95 and time_coverage >= 0.5 and pagination_complete:
            return Verdict.FINISH
        return Verdict.PARTIAL
