"""Phase 28 -- AcquisitionPlanner (spec 6).

Inputs: user goal, target asset, URL, expected time range, expected fields,
expected record type, collection scope, available capabilities, policy.
Output: AcquisitionPlan with target / source_type / strategy / steps /
expected_outputs / completeness_conditions / budgets / fallback_strategy.

Source type decision: STATIC_HTML / DYNAMIC_HTML / DOCUMENT /
PUBLIC_JSON_API / UNKNOWN (deterministic, no network access).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.acquisition.models import (
    AcquisitionPlan,
    AcquisitionPolicy,
    AcquisitionStep,
    SourceType,
)

_DOCUMENT_EXTENSIONS = {
    ".pdf": SourceType.DOCUMENT,
    ".docx": SourceType.DOCUMENT,
    ".doc": SourceType.DOCUMENT,
    ".xlsx": SourceType.DOCUMENT,
    ".xls": SourceType.DOCUMENT,
    ".json": SourceType.DOCUMENT,
    ".txt": SourceType.DOCUMENT,
    ".csv": SourceType.DOCUMENT,
    ".html": SourceType.STATIC_HTML,
    ".htm": SourceType.STATIC_HTML,
}

_API_PATH_MARKERS = ("/api/", "/rest/", "/v1/", "/v2/", "/graphql", "?format=json")


@dataclass
class PlannerRequest:
    goal: str
    url: str
    target_asset: str = ""
    expected_time_range: tuple[str, str] | None = None
    expected_fields: list[str] = field(default_factory=list)
    expected_record_type: str = ""
    expected_record_count: int | None = None
    available_capabilities: list[str] = field(default_factory=list)
    policy: AcquisitionPolicy | None = None
    user_agent: str = ""


class AcquisitionPlanner:
    """Deterministic plan builder (no I/O)."""

    def __init__(self, policy: AcquisitionPolicy | None = None) -> None:
        self._default_policy = policy or AcquisitionPolicy()

    def plan(self, request: PlannerRequest) -> AcquisitionPlan:
        policy = request.policy or self._default_policy
        source_type = self._decide_source_type(request.url)
        strategy = self._strategy_for(source_type)
        steps = self._build_steps(source_type, request.url)
        fallback = self._fallback_for(source_type)
        return AcquisitionPlan(
            target=request.goal,
            source_type=source_type,
            strategy=strategy,
            steps=steps,
            expected_outputs=self._expected_outputs(request),
            completeness_conditions={
                "expected_fields": request.expected_fields,
                "expected_time_range": (
                    list(request.expected_time_range) if request.expected_time_range else None
                ),
                "expected_record_type": request.expected_record_type,
                "expected_record_count": request.expected_record_count,
            },
            budgets={
                "max_requests": policy.max_requests,
                "max_pages": policy.max_pages,
                "max_records": policy.max_records,
                "max_duration": policy.max_duration,
                "max_bytes": policy.max_bytes,
            },
            fallback_strategy=fallback,
            urls=[request.url],
            expected_time_range=request.expected_time_range,
            expected_fields=request.expected_fields,
            expected_record_type=request.expected_record_type,
        )

    # -- decision logic ------------------------------------------------------

    def _decide_source_type(self, url: str) -> SourceType:
        parsed = urlparse(url)
        path = parsed.path.lower()
        for ext, source_type in _DOCUMENT_EXTENSIONS.items():
            if path.endswith(ext):
                return source_type
        lower = url.lower()
        if any(marker in lower for marker in _API_PATH_MARKERS):
            return SourceType.PUBLIC_JSON_API
        return SourceType.STATIC_HTML

    def _strategy_for(self, source_type: SourceType) -> str:
        return {
            SourceType.STATIC_HTML: "static-http-fetch+extract",
            SourceType.DYNAMIC_HTML: "browser-render+observe+extract",
            SourceType.DOCUMENT: "http-fetch+document-parse",
            SourceType.PUBLIC_JSON_API: "http-fetch+json-parse+paginate",
            SourceType.UNKNOWN: "probe-http-fetch",
        }[source_type]

    def _build_steps(self, source_type: SourceType, url: str) -> list[AcquisitionStep]:
        steps = [AcquisitionStep(id="fetch", kind="fetch", source_type=source_type, url=url)]
        if source_type in (SourceType.STATIC_HTML, SourceType.DYNAMIC_HTML):
            steps.append(AcquisitionStep(id="extract", kind="extract", source_type=source_type))
            steps.append(AcquisitionStep(id="paginate", kind="paginate", source_type=source_type))
        elif source_type == SourceType.DOCUMENT:
            steps.append(AcquisitionStep(id="parse", kind="parse", source_type=source_type))
        elif source_type == SourceType.PUBLIC_JSON_API:
            steps.append(AcquisitionStep(id="parse", kind="parse", source_type=source_type))
            steps.append(AcquisitionStep(id="paginate", kind="paginate", source_type=source_type))
        return steps

    def _fallback_for(self, source_type: SourceType) -> str:
        return {
            SourceType.STATIC_HTML: "if content is JS-rendered -> switch to browser capability",
            SourceType.DYNAMIC_HTML: "if browser unavailable -> HTTP fetch + note degraded",
            SourceType.DOCUMENT: "if parse fails -> record raw artifact only (PARTIAL)",
            SourceType.PUBLIC_JSON_API: "if JSON parse fails -> record raw artifact (PARTIAL)",
            SourceType.UNKNOWN: "probe; blocked statuses -> BLOCKED",
        }[source_type]

    @staticmethod
    def _expected_outputs(request: PlannerRequest) -> list[str]:
        outputs = ["evidence", "extracted_document"]
        if request.expected_record_type:
            outputs.append(f"records:{request.expected_record_type}")
        if request.expected_fields:
            outputs.append("fields:" + ",".join(request.expected_fields))
        return outputs
