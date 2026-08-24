"""Phase 28 -- AdaptiveDataAcquisitionAgent (spec 5, 20, 23).

The agent: understands the goal -> plans -> picks capabilities -> executes
bounded acquisition -> evaluates completeness -> replans when needed ->
emits an AcquisitionResult with full evidence lineage.

Hard rules:
  * The agent NEVER accesses the network, filesystem, or browser directly.
    All real I/O happens through the tool adapters (which in production run
    inside Worker/Sandbox behind Policy).
  * 401/403/captcha/login/paywall -> STOP (BLOCKED). No bypass attempts.
  * Replan may only change HOW we acquire the same target (e.g. HTTP ->
    Browser), never the scope: no new domains, no new auth, no new endpoints.
  * Raw artifacts are always preserved; extracted content references
    evidence; candidates are handed downstream -- never written as verified.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from app.acquisition.candidates import extract_candidates
from app.acquisition.completeness import CompletenessEvaluator, CompletenessInput
from app.acquisition.dedup import DuplicateRegistry, content_sha256
from app.acquisition.documentadapter import DocumentAdapter
from app.acquisition.httpadapter import HTTPAdapter, RestrictedAccessError
from app.acquisition.models import (
    AcquisitionPlan,
    AcquisitionResult,
    AcquisitionStatus,
    BlockReason,
    ExtractedDocument,
    RawArtifact,
    SourceType,
    Verdict,
)
from app.acquisition.pagination import detect_strategy, next_page_url
from app.acquisition.planner import AcquisitionPlanner, PlannerRequest
from app.acquisition.robots import RobotsPolicy, robots_url_for
from app.acquisition.store import EvidenceObjectStoreProvider
from app.acquisition.urlpolicy import URLPolicyValidator


def _url_host(url: str) -> str:
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").lower()


_HEADER_MARKERS = ("title", "cve", "date", "severity", "name", "id", "status")


def _record_rows(document: Any, source_url: str) -> list[dict[str, Any]]:
    """Extract record-level rows from a parsed document's tables.

    A table row becomes one record so per-page granularity (e.g. 3 pages x
    ten rows = 30 records) feeds the Completeness Engine correctly. Header
    rows are skipped; non-tabular documents yield no rows.
    """
    rows: list[dict[str, Any]] = []
    for table in getattr(document, "tables", []) or []:
        for row in table:
            # header row: EVERY cell is a plain marker word (e.g. title/cve/date)
            cells = [cell.strip() for cell in row]
            if cells and all(cell.lower() in _HEADER_MARKERS for cell in cells):
                continue
            if not any(cell for cell in cells):
                continue
            rows.append(
                {
                    "title": cells[0] if cells else "",
                    "cve": cells[1] if len(cells) > 1 else "",
                    "date": cells[2] if len(cells) > 2 else "",
                    "source_url": source_url,
                    "evidence_id": getattr(document, "evidence_id", None),
                    "artifact_sha256": getattr(document, "artifact_sha256", None),
                }
            )
    return rows[:200]


class EvidenceSink(Protocol):
    """Persistence hook for evidence records (platform EvidenceService in prod)."""

    async def save_evidence(
        self, artifact: RawArtifact, object_key: str, content: bytes = b""
    ) -> str: ...

    async def commit(self) -> None: ...


@dataclass
class AgentConfig:
    user_agent: str = "CAP-AdaptiveAcquisition/0.1 (+public-data-acquisition)"
    tool_version: str = "0.1.0"
    max_replans: int = 2
    max_retries_per_step: int = 2
    robots_respect: bool = True
    task_id: str = field(default_factory=lambda: str(uuid4()))
    trace_id: str = field(default_factory=lambda: uuid4().hex[:16])


class AdaptiveDataAcquisitionAgent:
    """Orchestrates acquisition through adapters only."""

    def __init__(
        self,
        *,
        http: HTTPAdapter,
        store: EvidenceObjectStoreProvider,
        planner: AcquisitionPlanner,
        browser: Any | None = None,
        document: DocumentAdapter | None = None,
        completeness: CompletenessEvaluator | None = None,
        robots: RobotsPolicy | None = None,
        evidence_sink: EvidenceSink | None = None,
        config: AgentConfig | None = None,
    ) -> None:
        self._http = http
        self._store = store
        self._planner = planner
        self._browser = browser
        self._document = document or DocumentAdapter()
        self._completeness = completeness or CompletenessEvaluator()
        self._robots = robots or RobotsPolicy()
        self._evidence_sink = evidence_sink
        self._config = config or AgentConfig()
        self._validator = URLPolicyValidator()

    # -- public entry --------------------------------------------------------

    async def acquire(
        self,
        request: PlannerRequest,
        *,
        checkpoint: Any | None = None,
    ) -> AcquisitionResult:
        """Execute (or resume) an acquisition plan.

        ``checkpoint`` (an ``AcquisitionCheckpoint``) restores accumulated
        state -- visited URLs, records, used budgets, replan count and the
        pagination cursor -- so a resumed run continues the SAME AcquisitionRun
        instead of restarting from page 1.
        """
        plan = self._planner.plan(request)
        result = AcquisitionResult(
            run_id=self._config.task_id,
            plan=plan,
            status=AcquisitionStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        started = time.monotonic()
        dupes = DuplicateRegistry()
        if checkpoint is not None:
            # the primary URL is re-entered so pagination resumes from the
            # checkpoint cursor (already-fetched pages stay skipped)
            primary = getattr(checkpoint, "current_url", "")
            result.visited_urls = [url for url in (checkpoint.visited_urls or []) if url != primary]
            result.evidence_ids = list(checkpoint.evidence_refs or [])
            result.replans = checkpoint.replan_count or 0
            result.total_bytes = checkpoint.bytes_used or 0
            result.strategy_history = checkpoint.strategy.split(".") if checkpoint.strategy else []
            for seen in checkpoint.records_seen or []:
                # the primary URL is deliberately re-fetched on resume (to
                # re-establish pagination context); it is not a duplicate
                if seen.get("url") == primary:
                    continue
                dupes.seen_urls[seen.get("url", "")] = seen.get("url", "")
                if seen.get("sha256"):
                    dupes.seen_hashes[seen["sha256"]] = seen.get("url", "")

        # robots gate (public compliance)
        if self._config.robots_respect and plan.source_type in (
            SourceType.STATIC_HTML,
            SourceType.DYNAMIC_HTML,
        ):
            robots_check = await self._check_robots(plan.urls[0], result)
            if not robots_check:
                result.status = AcquisitionStatus.BLOCKED
                result.finished_at = datetime.now(UTC)
                result.duration_seconds = round(time.monotonic() - started, 2)
                return result

        for url in plan.urls:
            await self._acquire_url(
                url=url,
                plan=plan,
                result=result,
                dupes=dupes,
                page_start=checkpoint.page_number if checkpoint is not None else 1,
            )
            if result.status in (AcquisitionStatus.BLOCKED,):
                break

        # completeness evaluation
        if result.status != AcquisitionStatus.BLOCKED:
            report = self._evaluate_completeness(plan, result, dupes)
            result.completeness = report
            result.status = self._status_from_verdict(report.verdict, result)

        result.finished_at = datetime.now(UTC)
        result.duration_seconds = round(time.monotonic() - started, 2)
        return result

    # -- internals -----------------------------------------------------------

    async def _check_robots(self, url: str, result: AcquisitionResult) -> bool:
        robots_url = robots_url_for(url)
        try:
            robots_result = await self._http.fetch(robots_url)
        except RestrictedAccessError as error:
            result.blocked_reason = error.reason
            result.blocked_detail = error.detail
            return False
        robots_text = None
        if robots_result.status == 200 and robots_result.content:
            robots_text = robots_result.content.decode("utf-8", "replace")
        policy = self._robots.evaluate(url, robots_text)
        if not policy.allowed:
            result.blocked_reason = BlockReason.ROBOTS_DISALLOWED
            result.blocked_detail = policy.reason
            return False
        return True

    async def _acquire_url(
        self,
        *,
        url: str,
        plan: AcquisitionPlan,
        result: AcquisitionResult,
        dupes: DuplicateRegistry,
        page_start: int = 1,
    ) -> None:
        if url in result.visited_urls:
            return
        result.visited_urls.append(url)

        source_type = plan.source_type
        strategy_history = [source_type.value]

        for _attempt in range(self._config.max_replans + 1):
            outcome = await self._execute_step(
                url=url,
                source_type=source_type,
                plan=plan,
                result=result,
                dupes=dupes,
                page_start=page_start,
            )
            if outcome in ("ok", "partial"):
                return
            if outcome == "blocked":
                result.status = AcquisitionStatus.BLOCKED
                return
            if outcome == "replan":
                # only switch transport (HTTP -> Browser); scope never grows
                source_type = SourceType.DYNAMIC_HTML
                result.replans += 1
                strategy_history.append("DYNAMIC_HTML")
                result.strategy_history = strategy_history
                continue

    async def _execute_step(
        self,
        *,
        url: str,
        source_type: SourceType,
        plan: AcquisitionPlan,
        result: AcquisitionResult,
        dupes: DuplicateRegistry,
        page_start: int = 1,
    ) -> str:
        """Returns ok | partial | blocked | replan."""
        # 1. transport
        if source_type in (SourceType.DYNAMIC_HTML,) and self._browser is not None:
            observation = await self._browser.browse(url)
            if not observation.available:
                return "replan" if source_type == SourceType.STATIC_HTML else "partial"
            content = observation.html.encode("utf-8", "replace")
            content_type = "text/html"
            final_url = observation.final_url
            status = observation.status
            method = "BROWSER"
            tool = "acquisition.browser"
            endpoints = observation.endpoints
            result.endpoint_candidates.extend(endpoints)
        else:
            try:
                fetch = await self._http.fetch(url)
            except RestrictedAccessError as error:
                result.blocked_reason = error.reason
                result.blocked_detail = error.detail
                return "blocked"
            if fetch.blocked_reason == BlockReason.SSRF_BLOCKED:
                result.blocked_reason = BlockReason.SSRF_BLOCKED
                result.blocked_detail = fetch.blocked_detail
                return "blocked"
            if fetch.blocked_reason in (
                BlockReason.CAPTCHA,
                BlockReason.PAYWALL,
                BlockReason.LOGIN_PAGE,
            ):
                result.blocked_reason = fetch.blocked_reason
                result.blocked_detail = fetch.blocked_detail
                return "blocked"
            if fetch.blocked_reason == BlockReason.TIMEOUT:
                result.blocked_reason = BlockReason.TIMEOUT
                result.blocked_detail = fetch.blocked_detail
                return "partial"
            if fetch.status == 429:
                result.blocked_reason = BlockReason.RATE_LIMITED
                result.blocked_detail = "HTTP 429 rate limited"
                return "partial"
            if fetch.blocked_reason == BlockReason.SIZE_LIMIT:
                result.blocked_reason = BlockReason.SIZE_LIMIT
                result.blocked_detail = fetch.blocked_detail
                return "blocked"
            if fetch.status == 0 or not fetch.content:
                return "partial"
            content = fetch.content
            content_type = fetch.content_type
            final_url = fetch.final_url
            status = fetch.status
            method = fetch.artifact.method if fetch.artifact else "GET"
            tool = "acquisition.http"
            endpoints = []

        # 2. content-addressed raw artifact + evidence lineage
        artifact_sha = content_sha256(content)
        duplicate_of = dupes.check(final_url, artifact_sha)
        if duplicate_of:
            # never delete; just mark (dedup spec 21)
            result.records.append({"duplicate_of": duplicate_of, "url": final_url})
            return "partial"

        try:
            stored = await self._store.put(
                content,
                metadata={"url": url, "final_url": final_url, "content_type": content_type},
            )
        except Exception:  # noqa: BLE001 -- storage failure is partial, not crash
            result.errors = result.completeness.errors if result.completeness else []
            return "partial"

        artifact = RawArtifact(
            object_key=stored.key,
            sha256=stored.key,
            size=len(content),
            content_type=content_type,
            source_url=url,
            final_url=final_url,
            captured_at=datetime.now(UTC),
            http_status=status,
            method=method,
            tool=tool,
            tool_version=self._config.tool_version,
            task_id=self._config.task_id,
            trace_id=self._config.trace_id,
        )
        result.artifacts.append(artifact)
        result.total_bytes += len(content)

        evidence_id = None
        if self._evidence_sink is not None:
            evidence_id = await self._evidence_sink.save_evidence(artifact, stored.key, content)
            if evidence_id:
                result.evidence_ids.append(evidence_id)
            # Release the DB write lock before fetching the next page: on
            # SQLite the open evidence write transaction would otherwise hold
            # the single-writer lock across the next fetch, blocking a
            # concurrent cancel's CANCEL_REQUESTED write for the busy timeout
            # (Phase 28.5-L cancel race). On PostgreSQL this is a harmless
            # finer-grained commit that also improves checkpoint resume.
            await self._evidence_sink.commit()

        # 3. content extraction -> ExtractedDocument
        document = self._extract(content, content_type, final_url, evidence_id, stored.key)
        result.documents.append(document)

        # JS-rendered shell: static HTTP produced no extractable body and a
        # browser capability is available -> replan (transport only, same scope)
        if (
            source_type == SourceType.STATIC_HTML
            and self._browser is not None
            and not document.text
        ):
            return "replan"

        # 4. candidates (downstream validation only)
        bundle = extract_candidates(
            document.text,
            evidence_id=evidence_id,
            source_url=final_url,
            title=document.title,
        )
        rows = _record_rows(document, final_url)
        if rows:
            result.records.extend(rows)
        else:
            result.records.append(self._records_from_document(document))
        result.candidate_bundles.append(bundle)

        # 5. pagination (bounded)
        if source_type in (
            SourceType.STATIC_HTML,
            SourceType.DYNAMIC_HTML,
            SourceType.PUBLIC_JSON_API,
        ):
            await self._paginate(
                url=final_url,
                content=content,
                content_type=content_type,
                source_type=source_type,
                plan=plan,
                result=result,
                dupes=dupes,
                page_start=page_start,
            )
        return "ok"

    async def _paginate(
        self,
        *,
        url: str,
        content: bytes,
        content_type: str,
        source_type: SourceType,
        plan: AcquisitionPlan,
        result: AcquisitionResult,
        dupes: DuplicateRegistry,
        page_start: int = 1,
    ) -> None:
        html = content.decode("utf-8", "replace")
        strategy = detect_strategy(
            page_url=url,
            html=html,
            budgets=plan.budgets,
        )
        if strategy.kind == "none":
            return
        current_url = url
        origin_host = _url_host(url)
        for page_number in range(page_start, strategy.max_pages):
            result.pagination_page = page_number
            next_url = next_page_url(strategy, current_url, page_number)
            if not next_url or next_url in result.visited_urls:
                break
            if _url_host(next_url) != origin_host:
                break  # scope guard: pagination never leaves the origin domain
            result.visited_urls.append(next_url)
            try:
                fetch = await self._http.fetch(next_url)
            except RestrictedAccessError:
                break  # pagination stops on restricted pages (no bypass)
            if fetch.status in (401, 403) or fetch.blocked_reason != BlockReason.NONE:
                break
            if not fetch.content:
                break
            artifact_sha = content_sha256(fetch.content)
            if dupes.check(fetch.final_url, artifact_sha):
                break
            try:
                stored = await self._store.put(
                    fetch.content,
                    metadata={
                        "url": next_url,
                        "final_url": fetch.final_url,
                        "content_type": fetch.content_type,
                    },
                )
            except Exception:  # noqa: BLE001
                break
            artifact = RawArtifact(
                object_key=stored.key,
                sha256=stored.key,
                size=len(fetch.content),
                content_type=fetch.content_type,
                source_url=next_url,
                final_url=fetch.final_url,
                captured_at=datetime.now(UTC),
                http_status=fetch.status,
                method="GET",
                tool="acquisition.http",
                tool_version=self._config.tool_version,
                task_id=self._config.task_id,
                trace_id=self._config.trace_id,
            )
            result.artifacts.append(artifact)
            result.total_bytes += len(fetch.content)
            document = self._extract(
                fetch.content, fetch.content_type, fetch.final_url, None, stored.key
            )
            result.documents.append(document)
            rows = _record_rows(document, fetch.final_url)
            if rows:
                result.records.extend(rows)
            else:
                result.records.append(self._records_from_document(document))
            strategy.pages_fetched = page_number
            result.strategy_history.append(f"page:{page_number}")
            # follow next links page-by-page (each page may advance the cursor)
            if strategy.kind == "next_link":
                refreshed = detect_strategy(
                    page_url=fetch.final_url,
                    html=fetch.content.decode("utf-8", "replace"),
                    budgets=plan.budgets,
                )
                if refreshed.kind == "next_link" and refreshed.next_url:
                    if refreshed.next_url == strategy.next_url:
                        break  # cyclic next link
                    strategy.next_url = refreshed.next_url
                else:
                    break
                current_url = fetch.final_url

    def _extract(
        self,
        content: bytes,
        content_type: str,
        source_url: str,
        evidence_id: str | None,
        artifact_sha: str | None,
    ) -> ExtractedDocument:
        parsed = self._document.parse(content, content_type=content_type, source_url=source_url)
        if parsed.ok and parsed.document is not None:
            document = parsed.document
            document.evidence_id = evidence_id
            document.artifact_sha256 = artifact_sha
            return document
        return ExtractedDocument(
            text="",
            source_url=source_url,
            evidence_id=evidence_id,
            artifact_sha256=artifact_sha,
            extraction_backend=parsed.parser_backend,
            metadata={"parse_error": parsed.error},
        )

    def _evaluate_completeness(
        self, plan: AcquisitionPlan, result: AcquisitionResult, dupes: DuplicateRegistry
    ) -> Any:
        observed_fields: set[str] = set()
        for doc in result.documents:
            observed_fields.update(doc.metadata.keys())
            if doc.title:
                observed_fields.add("title")
            if doc.text:
                observed_fields.add("text")
            if doc.tables:
                observed_fields.add("tables")
        for record in result.records:
            observed_fields.update(record.keys())
        expected = plan.completeness_conditions.get("expected_fields") or []
        expected_count = plan.completeness_conditions.get("expected_record_count")
        # GA-GATE 37/39 (fail-closed): a run that acquired NOTHING must
        # never surface as a success verdict -- empty documents produced by
        # dead fetches (egress/DNS down) previously still counted as
        # observed coverage and drove a false FINISH/COMPLETE.
        nothing_acquired = result.total_bytes == 0
        partial_failure = result.blocked_reason in (
            BlockReason.TIMEOUT,
            BlockReason.RATE_LIMITED,
        )
        data = CompletenessInput(
            expected_fields=expected,
            observed_fields=observed_fields,
            expected_time_range=plan.expected_time_range,
            record_count=len(result.records),
            expected_record_count=expected_count,
            duplicates=len(dupes.duplicates),
            gaps=[] if len(result.documents) else ["no documents extracted"],
            errors=[],
            blocked=nothing_acquired,
            partial_failure=partial_failure,
        )
        return self._completeness.evaluate(data)

    @staticmethod
    def _status_from_verdict(verdict: Verdict, result: AcquisitionResult) -> AcquisitionStatus:
        if verdict == Verdict.BLOCKED:
            return AcquisitionStatus.BLOCKED
        if verdict == Verdict.FINISH:
            return AcquisitionStatus.COMPLETE
        # PARTIAL / RETRY / REPLAN all surface as PARTIAL to the caller
        # (RETRY/REPLAN are internal loop signals, not terminal states).
        return AcquisitionStatus.PARTIAL

    @staticmethod
    def _records_from_document(document: ExtractedDocument) -> dict[str, Any]:
        return {
            "title": document.title,
            "text_length": len(document.text),
            "source_url": document.source_url,
            "evidence_id": document.evidence_id,
            "artifact_sha256": document.artifact_sha256,
        }
