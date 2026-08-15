"""Phase 28 -- core tests: SSRF/URL policy, object store, planner,
completeness, deduplication (incl. adversarial URL cases)."""

from __future__ import annotations

import pytest

from app.acquisition.completeness import CompletenessEvaluator, CompletenessInput
from app.acquisition.dedup import DuplicateRegistry, canonicalize_url, content_sha256
from app.acquisition.models import AcquisitionPolicy, Verdict
from app.acquisition.planner import AcquisitionPlanner, PlannerRequest
from app.acquisition.store import LocalFilesystemEvidenceStore, ObjectStoreError
from app.acquisition.urlpolicy import URLPolicyValidator

PRIVATE_RESOLVER = lambda host: ["127.0.0.1"]  # noqa: E731
META_RESOLVER = lambda host: ["169.254.169.254"]  # noqa: E731
PRIVATE_192 = lambda host: ["192.168.1.10"]  # noqa: E731
PUBLIC_RESOLVER = lambda host: ["93.184.216.34"]  # noqa: E731
IPV6_LOOPBACK = lambda host: ["::1"]  # noqa: E731


# -- SSRF / URL policy ------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/admin",
        "http://127.0.0.1/secret",
        "http://127.0.0.2/",
        "http://[::1]/",
        "http://192.168.0.5/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://[::ffff:127.0.0.1]/",
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com/",
        "data:text/plain;base64,AAAA",
        "javascript:alert(1)",
    ],
)
def test_url_policy_rejects_private_and_blocked_schemes(url: str) -> None:
    result = URLPolicyValidator(resolver=PUBLIC_RESOLVER).validate_url(url)
    assert result.allowed is False
    assert result.reason


def test_url_policy_rejects_userinfo() -> None:
    validator = URLPolicyValidator(resolver=PUBLIC_RESOLVER)
    assert validator.validate_url("http://user:pass@example.com/").allowed is False
    assert validator.validate_url("http://attacker@example.com/").allowed is False


def test_url_policy_rejects_non_http_schemes() -> None:
    validator = URLPolicyValidator(resolver=PUBLIC_RESOLVER)
    assert validator.validate_url("ftp://example.com").allowed is False
    assert validator.validate_url("file:///etc/passwd").allowed is False
    assert validator.validate_url("javascript:void(0)").allowed is False


def test_url_policy_accepts_public_https() -> None:
    result = URLPolicyValidator(resolver=PUBLIC_RESOLVER).validate_url("https://example.com/page")
    assert result.allowed is True
    assert result.final_host == "example.com"
    assert result.resolved_ips == ["93.184.216.34"]


def test_url_policy_dns_rebinding_fails_closed() -> None:
    """Host resolves publicly first, but re-validation catches private IP."""
    validator = URLPolicyValidator(resolver=PRIVATE_RESOLVER)
    result = validator.validate_url("https://attacker-controlled.example/x")
    assert result.allowed is False
    assert "non-public" in result.reason


def test_url_policy_ipv6_loopback_blocked() -> None:
    validator = URLPolicyValidator(resolver=IPV6_LOOPBACK)
    assert validator.validate_url("https://v6.example/").allowed is False


def test_url_policy_private_192_blocked() -> None:
    validator = URLPolicyValidator(resolver=PRIVATE_192)
    assert validator.validate_url("https://lan.example/").allowed is False


def test_url_policy_metadata_resolution_blocked() -> None:
    validator = URLPolicyValidator(resolver=META_RESOLVER)
    assert validator.validate_url("https://cloud.example/").allowed is False


def test_url_policy_redirect_revalidation() -> None:
    validator = URLPolicyValidator(resolver=PUBLIC_RESOLVER)
    # public initial target
    assert validator.validate_url("https://example.com/").allowed is True
    # redirect to private must be rejected again
    redirect_validator = URLPolicyValidator(resolver=PRIVATE_RESOLVER)
    assert redirect_validator.validate_redirect("http://127.0.0.1/").allowed is False


def test_url_policy_malformed_url() -> None:
    validator = URLPolicyValidator(resolver=PUBLIC_RESOLVER)
    assert validator.validate_url("not a url").allowed is False


def test_url_policy_allow_private_override() -> None:
    # explicit future policy: authorized internal asset
    validator = URLPolicyValidator(resolver=PRIVATE_RESOLVER, allow_private=True)
    assert validator.validate_url("https://internal.example/").allowed is True


def test_url_policy_no_dns_fails_closed() -> None:
    validator = URLPolicyValidator(resolver=lambda host: [])
    assert validator.validate_url("https://nx.example/").allowed is False
    assert "no DNS" in validator.validate_url("https://nx.example/").reason


# -- object store -----------------------------------------------------------


@pytest.mark.asyncio
async def test_store_content_addressed_immutable(tmp_path) -> None:
    store = LocalFilesystemEvidenceStore(tmp_path)
    first = await store.put(b"hello world", metadata={"source": "a"})
    second = await store.put(b"hello world", metadata={"source": "b"})
    assert first.key == second.key  # same content -> same key
    assert await store.get(first.key) == b"hello world"
    assert await store.exists(first.key) is True
    meta = await store.metadata(first.key)
    assert meta["size"] == 11
    # immutable: no overwrite of content
    assert len(list((tmp_path / "objects").rglob("*"))) == 2  # shard dir + blob


@pytest.mark.asyncio
async def test_store_size_limit(tmp_path) -> None:
    store = LocalFilesystemEvidenceStore(tmp_path, max_object_bytes=16)
    with pytest.raises(ObjectStoreError):
        await store.put(b"x" * 32, metadata={})


@pytest.mark.asyncio
async def test_store_empty_rejected(tmp_path) -> None:
    store = LocalFilesystemEvidenceStore(tmp_path)
    with pytest.raises(ObjectStoreError):
        await store.put(b"", metadata={})


@pytest.mark.asyncio
async def test_store_missing_key(tmp_path) -> None:
    store = LocalFilesystemEvidenceStore(tmp_path)
    with pytest.raises(ObjectStoreError):
        await store.get("deadbeef" * 8)


def test_sha256_hex_deterministic() -> None:
    from app.acquisition.store import sha256_hex

    assert sha256_hex(b"a") == sha256_hex(b"a")
    assert sha256_hex(b"a") != sha256_hex(b"b")


# -- planner ----------------------------------------------------------------


def test_planner_source_type_decision() -> None:
    planner = AcquisitionPlanner()
    cases = [
        ("https://x.example/page.html", "STATIC_HTML"),
        ("https://x.example/report.pdf", "DOCUMENT"),
        ("https://x.example/note.docx", "DOCUMENT"),
        ("https://x.example/data.xlsx", "DOCUMENT"),
        ("https://x.example/api/v1/items", "PUBLIC_JSON_API"),
        ("https://x.example/data.json", "DOCUMENT"),
        ("https://x.example/rest/things", "PUBLIC_JSON_API"),
    ]
    for url, expected in cases:
        plan = planner.plan(PlannerRequest(goal="g", url=url))
        assert plan.source_type.value == expected, url


def test_planner_plan_fields() -> None:
    planner = AcquisitionPlanner()
    request = PlannerRequest(
        goal="collect advisories",
        url="https://x.example/advisories",
        expected_fields=["title", "cve"],
        expected_record_type="advisory",
        expected_record_count=10,
        expected_time_range=("2026-01-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00"),
    )
    plan = planner.plan(request)
    assert plan.target == "collect advisories"
    assert plan.strategy
    assert len(plan.steps) >= 2
    assert plan.expected_outputs
    assert plan.completeness_conditions["expected_record_count"] == 10
    assert plan.completeness_conditions["expected_fields"] == ["title", "cve"]
    assert plan.budgets["max_requests"] > 0
    assert plan.fallback_strategy


def test_planner_document_steps() -> None:
    planner = AcquisitionPlanner()
    plan = planner.plan(PlannerRequest(goal="g", url="https://x.example/a.pdf"))
    kinds = [step.kind for step in plan.steps]
    assert "fetch" in kinds and "parse" in kinds


# -- completeness -----------------------------------------------------------


def test_completeness_finish() -> None:
    evaluator = CompletenessEvaluator()
    report = evaluator.evaluate(
        CompletenessInput(
            expected_fields=["title"],
            observed_fields={"title", "text"},
            record_count=1,
        )
    )
    assert report.verdict == Verdict.FINISH
    assert report.field_completeness == 1.0


def test_completeness_missing_field_partial() -> None:
    evaluator = CompletenessEvaluator()
    report = evaluator.evaluate(
        CompletenessInput(
            expected_fields=["title", "date", "severity"],
            observed_fields={"title"},
            gaps=["date missing", "severity missing"],
        )
    )
    assert report.field_completeness == pytest.approx(1 / 3, abs=0.001)
    assert report.verdict == Verdict.REPLAN


def test_completeness_blocked() -> None:
    evaluator = CompletenessEvaluator()
    report = evaluator.evaluate(CompletenessInput(blocked=True))
    assert report.verdict == Verdict.BLOCKED
    assert report.coverage_score == 0.0


def test_completeness_partial_failure() -> None:
    evaluator = CompletenessEvaluator()
    report = evaluator.evaluate(
        CompletenessInput(
            expected_fields=["title"],
            observed_fields={"title", "text"},
            partial_failure=True,
        )
    )
    assert report.verdict == Verdict.PARTIAL


def test_completeness_record_count() -> None:
    evaluator = CompletenessEvaluator()
    report = evaluator.evaluate(
        CompletenessInput(
            expected_fields=["t"],
            observed_fields={"t"},
            record_count=3,
            expected_record_count=10,
        )
    )
    assert report.verdict == Verdict.PARTIAL


def test_completeness_gaps_never_finish() -> None:
    evaluator = CompletenessEvaluator()
    report = evaluator.evaluate(
        CompletenessInput(
            expected_fields=[],
            observed_fields=set(),
            gaps=["no documents extracted"],
        )
    )
    assert report.verdict != Verdict.FINISH


def test_completeness_no_time_evidence() -> None:
    evaluator = CompletenessEvaluator()
    report = evaluator.evaluate(
        CompletenessInput(
            expected_fields=["title"],
            observed_fields={"title"},
            expected_time_range=("2026-01-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00"),
            observed_timestamps=[],
        )
    )
    assert report.time_coverage == 0.0


# -- deduplication ----------------------------------------------------------


def test_canonicalize_url() -> None:
    # scheme/host are case-folded; PATH case is semantically significant and kept
    assert canonicalize_url("HTTP://Example.com/Path") == "http://example.com/Path"
    assert canonicalize_url("https://example.com/a?utm_source=x&b=2&a=1") == (
        "https://example.com/a?a=1&b=2"
    )
    assert canonicalize_url("https://example.com/page#frag") == "https://example.com/page"


def test_duplicate_registry() -> None:
    registry = DuplicateRegistry()
    first = registry.check("https://example.com/a", "hash1")
    assert first is None
    # same canonical URL + same content -> duplicate
    duplicate = registry.check("https://example.com/a?utm_source=x", "hash1")
    assert duplicate is not None
    # same content at a different URL -> duplicate
    content_dup = registry.check("https://example.com/b", "hash1")
    assert content_dup is not None
    assert len(registry.duplicates) == 2


def test_duplicate_registry_allows_changed_content() -> None:
    """Same URL, different content (e.g. HTTP shell -> rendered) is NOT a dup."""
    registry = DuplicateRegistry()
    assert registry.check("https://example.com/app", "shell_hash") is None
    assert registry.check("https://example.com/app", "rendered_hash") is None
    assert registry.duplicates == []


def test_content_sha256() -> None:
    assert content_sha256(b"data") == content_sha256(b"data")
    assert len(content_sha256(b"data")) == 64


# -- policy defaults --------------------------------------------------------


def test_policy_defaults_safe() -> None:
    policy = AcquisitionPolicy()
    assert policy.max_requests <= 100
    assert policy.max_pages <= 50
    assert policy.max_duration <= 600
    assert policy.redirect_limit >= 3
    assert policy.allowed_schemes == ("http", "https")
    assert "CAP-AdaptiveAcquisition" in policy.user_agent
    assert policy.allows_url("https://example.com/") is True
    assert policy.allows_url("file:///etc/passwd") is False
