"""Phase 28 -- Adaptive Data Acquisition domain models.

Pure dataclass models (no DB). These are the contracts between the planner,
adapters, completeness engine and the acquisition agent. Persistence models
live in ``app/models/acquisition.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

# -- enums ------------------------------------------------------------------


class SourceType(StrEnum):
    STATIC_HTML = "STATIC_HTML"
    DYNAMIC_HTML = "DYNAMIC_HTML"
    DOCUMENT = "DOCUMENT"
    PUBLIC_JSON_API = "PUBLIC_JSON_API"
    UNKNOWN = "UNKNOWN"


class AcquisitionStatus(StrEnum):
    """Run lifecycle states (durable DB + domain).

    Phase 28.3: QUEUED / CANCEL_REQUESTED / CANCELLED were previously
    string literals used by the durable queue machinery but missing from the
    enum. They are now first-class members so status comparisons never
    depend on untyped string constants.
    """

    PENDING = "PENDING"  # legacy create_and_run only (deprecated)
    QUEUED = "QUEUED"  # enqueued, waiting for a worker claim
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"  # cancel requested, not yet stopped
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"  # operation stopped, no further work


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class Verdict(StrEnum):
    FINISH = "FINISH"
    RETRY = "RETRY"
    REPLAN = "REPLAN"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


class BlockReason(StrEnum):
    NONE = "NONE"
    AUTH_REQUIRED = "AUTH_REQUIRED"  # 401/403
    LOGIN_PAGE = "LOGIN_PAGE"
    CAPTCHA = "CAPTCHA"
    PAYWALL = "PAYWALL"
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    SIZE_LIMIT = "SIZE_LIMIT"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    MALFORMED = "MALFORMED"
    TOO_MANY_REQUESTS = "TOO_MANY_REQUESTS"
    FAILED = "FAILED"


class EndpointState(StrEnum):
    OBSERVED = "OBSERVED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


# -- policies ---------------------------------------------------------------


@dataclass
class AcquisitionPolicy:
    """Bounded crawl policy with safe small defaults (spec 17)."""

    allowed_schemes: tuple[str, ...] = ("http", "https")
    allowed_domains: tuple[str, ...] = ()
    allowed_content_types: tuple[str, ...] = ()
    max_requests: int = 50
    max_pages: int = 20
    max_records: int = 200
    max_bytes: int = 10 * 1024 * 1024  # 10 MiB per response
    max_document_bytes: int = 20 * 1024 * 1024  # 20 MiB per document
    max_duration: float = 300.0  # seconds per run
    request_rate: float = 1.0  # requests per second
    concurrency: int = 1
    redirect_limit: int = 5
    retry: int = 2
    timeout_seconds: float = 30.0
    user_agent: str = "CAP-AdaptiveAcquisition/0.1 (+public-data-acquisition)"
    # -- Phase 28.2 backpressure -------------------------------------------
    # Max QUEUED/RUNNING/CANCEL_REQUESTED runs the durable queue may hold
    # before the API returns 503. 0/None = unlimited (accept and queue).
    max_queued_runs: int | None = 0

    def allows_url(self, url: str) -> bool:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme not in self.allowed_schemes:
            return False
        if self.allowed_domains:
            host = (parsed.hostname or "").lower()
            return any(host == d or host.endswith(f".{d}") for d in self.allowed_domains)
        return True


# -- plans ------------------------------------------------------------------


@dataclass
class AcquisitionStep:
    id: str
    kind: str  # "fetch", "browse", "parse", "extract", "paginate", "discover", "verify"
    source_type: SourceType
    url: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    detail: str = ""


@dataclass
class AcquisitionPlan:
    target: str
    source_type: SourceType
    strategy: str
    steps: list[AcquisitionStep] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    completeness_conditions: dict[str, Any] = field(default_factory=dict)
    budgets: dict[str, Any] = field(default_factory=dict)
    fallback_strategy: str = ""
    urls: list[str] = field(default_factory=list)
    expected_time_range: tuple[str, str] | None = None
    expected_fields: list[str] = field(default_factory=list)
    expected_record_type: str = ""


# -- pagination -------------------------------------------------------------


@dataclass
class PaginationStrategy:
    kind: str = "none"  # next_link | page_param | cursor | load_more | infinite_scroll | none
    max_pages: int = 5
    max_records: int = 200
    max_duration: float = 120.0
    max_requests: int = 20
    next_url: str | None = None
    page_param: str | None = None
    base_url: str | None = None
    records_seen: int = 0
    pages_fetched: int = 0


# -- raw artifact & evidence ------------------------------------------------


@dataclass
class RawArtifact:
    """Immutable content-addressed raw capture (spec 14/15)."""

    object_key: str
    sha256: str
    size: int
    content_type: str
    source_url: str
    final_url: str
    captured_at: datetime
    http_status: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    method: str = "GET"
    tool: str = ""
    tool_version: str = ""
    task_id: str = ""
    trace_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# -- content extraction -----------------------------------------------------


@dataclass
class ExtractedDocument:
    """Normalized extracted content (spec 13). Never a substitute for raw evidence."""

    title: str = ""
    text: str = ""
    sections: list[dict[str, Any]] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    links: list[str] = field(default_factory=list)
    published_at: str | None = None
    author: str | None = None
    language: str | None = None
    source_url: str = ""
    evidence_id: str | None = None  # lineage: reference to persisted Evidence
    artifact_sha256: str | None = None  # lineage: reference to raw artifact
    extraction_backend: str = ""


# -- public endpoint discovery ----------------------------------------------


@dataclass
class PublicEndpointCandidate:
    url: str
    method: str = "GET"
    state: EndpointState = EndpointState.OBSERVED
    observed_from: str = ""  # page url that issued the request
    content_type: str | None = None
    status: int | None = None
    reason: str = ""


# -- completeness -----------------------------------------------------------


@dataclass
class CompletenessReport:
    coverage_score: float = 0.0
    field_completeness: float = 0.0
    time_coverage: float = 0.0
    pagination_complete: bool = False
    duplicates: int = 0
    gaps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    confidence: float = 0.0
    verdict: Verdict = Verdict.PARTIAL


# -- results ----------------------------------------------------------------


@dataclass
class AcquisitionResult:
    run_id: str = ""
    plan: AcquisitionPlan | None = None
    status: AcquisitionStatus = AcquisitionStatus.PENDING
    artifacts: list[RawArtifact] = field(default_factory=list)
    documents: list[ExtractedDocument] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    endpoint_candidates: list[PublicEndpointCandidate] = field(default_factory=list)
    completeness: CompletenessReport | None = None
    blocked_reason: BlockReason = BlockReason.NONE
    blocked_detail: str = ""
    replans: int = 0
    retries: int = 0
    visited_urls: list[str] = field(default_factory=list)
    strategy_history: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total_bytes: int = 0
    # GA-GATE 37/39: transport-level dead attempts (DNS/proxy/conn refused).
    # Distinguishes "fetch died" from "fetched but empty" -- only the
    # former may drive a fail-closed BLOCKED verdict.
    transport_failures: int = 0
    duration_seconds: float = 0.0
    records: list[dict[str, Any]] = field(default_factory=list)
    candidate_bundles: list[Any] = field(default_factory=list)
    pagination_page: int = 0  # checkpoint cursor: next pagination loop start


# -- robots -----------------------------------------------------------------


@dataclass
class RobotsPolicyResult:
    allowed: bool = True
    reason: str = "no robots.txt / not applicable"
    source_url: str | None = None
    rule: str | None = None
