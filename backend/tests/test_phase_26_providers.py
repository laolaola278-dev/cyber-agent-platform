"""Phase 26 - real LLM provider, data policy, budget and failure tests."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.agent.budget import BudgetTracker, CostLatencyBudgetConfig
from app.agent.contracts import ModelCapability, ModelRequest, TokenUsage
from app.agent.datapolicy import (
    DataClass,
    ModelDataPolicy,
    RedactionReport,
)
from app.agent.exceptions import AgentLoopLimit
from app.agent.failures import (
    ModelContextOverflowError,
    ModelFailureHandler,
    ModelMalformedOutputError,
    ModelRateLimitError,
    ModelRefusalError,
    ModelServerError,
    ModelTimeoutError,
    ProviderUnavailableError,
)
from app.agent.providers import (
    DEFAULT_ALLOWED_BASE_URLS,
    ModelConfig,
    OpenAICompatibleLLMProvider,
)
from app.sandbox.secret import MemorySecretProvider


def _provider(
    *,
    secret: str | None = "test-key",
    config: ModelConfig | None = None,
    transport: httpx.MockTransport | None = None,
    allowed: tuple[str, ...] = DEFAULT_ALLOWED_BASE_URLS,
) -> OpenAICompatibleLLMProvider:
    values = {"llm-openai-api-key": secret} if secret else {}
    client = httpx.AsyncClient(transport=transport) if transport else None
    return OpenAICompatibleLLMProvider(
        MemorySecretProvider(values=values),
        config or ModelConfig(model="test-model", base_url="https://api.openai.com/v1"),
        http_client=client,
        allowed_base_urls=allowed,
    )


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": '{"classification": "MALICIOUS"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


# ---------------------------------------------------------------------------
# Configuration and capability degradation
# ---------------------------------------------------------------------------


def test_configuration_first_model_name() -> None:
    config = ModelConfig(model="gpt-x", base_url="https://api.openai.com/v1")
    assert config.model == "gpt-x"
    assert config.structured_output is True
    with pytest.raises(ValueError):
        ModelConfig(model="", base_url="https://api.openai.com/v1")  # noqa: PLC0105


def test_base_url_allowlist_rejects_unknown() -> None:
    provider = _provider(config=ModelConfig(model="m", base_url="https://evil.example.com/v1"))
    with pytest.raises(ProviderUnavailableError):
        provider.validate_configuration()
    assert asyncio.run(provider.health_check()) is False


def test_missing_secret_degrades_capability() -> None:
    provider = _provider(secret=None)
    assert asyncio.run(provider.health_check()) is False
    health = asyncio.run(provider.availability())
    assert health.available is False
    assert "degraded" in health.reason


async def test_complete_without_secret_fails_closed() -> None:
    provider = _provider(secret=None)
    with pytest.raises(ProviderUnavailableError):
        await provider.complete(ModelRequest(user_prompt="hi"))


def test_supports_capabilities() -> None:
    provider = _provider()
    assert provider.supports(ModelCapability.STRUCTURED_OUTPUT)
    assert provider.supports(ModelCapability.TEXT)
    config = ModelConfig(model="m", base_url="https://api.openai.com/v1", structured_output=False)
    assert not _provider(config=config).supports(ModelCapability.STRUCTURED_OUTPUT)


# ---------------------------------------------------------------------------
# Completion behaviour (httpx MockTransport)
# ---------------------------------------------------------------------------


async def test_complete_parses_structured_output() -> None:
    provider = _provider(transport=httpx.MockTransport(_ok_handler))
    response = await provider.complete(
        ModelRequest(
            system_prompt="sys",
            user_prompt="triage it",
            required_capability=ModelCapability.STRUCTURED_OUTPUT,
        )
    )
    assert response.structured == {"classification": "MALICIOUS"}
    assert response.usage.total_tokens == 15
    assert response.provider == "openai-compatible"
    assert response.latency_ms >= 0


async def test_retry_on_rate_limit_then_success() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return _ok_handler(request)

    provider = _provider(transport=httpx.MockTransport(handler))
    response = await provider.complete(
        ModelRequest(user_prompt="hi", required_capability=ModelCapability.STRUCTURED_OUTPUT)
    )
    assert response.structured == {"classification": "MALICIOUS"}
    assert calls["count"] == 2


async def test_rate_limit_persists_after_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    provider = _provider(
        transport=httpx.MockTransport(handler),
        config=ModelConfig(model="m", base_url="https://api.openai.com/v1", retry_limit=1),
    )
    with pytest.raises(ModelRateLimitError):
        await provider.complete(ModelRequest(user_prompt="hi"))


async def test_server_error_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": "boom"})

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(ModelServerError):
        await provider.complete(ModelRequest(user_prompt="hi"))


async def test_timeout_fails_closed() -> None:
    def slow_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    provider = _provider(transport=httpx.MockTransport(slow_handler))
    with pytest.raises(ModelTimeoutError):
        await provider.complete(ModelRequest(user_prompt="hi"))


async def test_malformed_json_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(ModelMalformedOutputError):
        await provider.complete(
            ModelRequest(user_prompt="hi", required_capability=ModelCapability.STRUCTURED_OUTPUT)
        )


async def test_malformed_json_with_markdown_fence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = '```json\n{"classification": "SUSPICIOUS"}\n```'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = _provider(transport=httpx.MockTransport(handler))
    response = await provider.complete(
        ModelRequest(user_prompt="hi", required_capability=ModelCapability.STRUCTURED_OUTPUT)
    )
    assert response.structured == {"classification": "SUSPICIOUS"}


async def test_cost_estimation() -> None:
    config = ModelConfig(
        model="m",
        base_url="https://api.openai.com/v1",
        cost_per_1k_prompt=0.5,
        cost_per_1k_completion=1.5,
    )
    provider = _provider(config=config)
    usage = TokenUsage(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000)
    assert provider.estimate_cost(usage) == 2.0


# ---------------------------------------------------------------------------
# ModelDataPolicy
# ---------------------------------------------------------------------------


def test_classify_key_secret_forbidden() -> None:
    policy = ModelDataPolicy()
    assert policy.classify_key("api_key") == DataClass.MODEL_FORBIDDEN
    assert policy.classify_key("authorization") == DataClass.MODEL_FORBIDDEN
    assert policy.classify_key("cookie") == DataClass.MODEL_FORBIDDEN
    assert policy.classify_key("private_key") == DataClass.MODEL_FORBIDDEN
    assert policy.classify_key("email") == DataClass.REDACTED
    assert policy.classify_key("title") == DataClass.MODEL_ALLOWED
    assert policy.classify_key("unknown_field") == DataClass.LOCAL_ONLY


def test_sanitize_payload_removes_secrets() -> None:
    policy = ModelDataPolicy()
    sanitized, report = policy.sanitize_payload(
        {
            "title": "alert",
            "api_key": "sk-secret-value",
            "email": "a@example.com",
            "custom_internal": "never-leave",
        }
    )
    assert "api_key" not in sanitized
    assert report.secrets_removed == 1
    assert "email" in report.redacted_fields
    assert "custom_internal" in report.local_only_fields
    assert "[local:" in sanitized["custom_internal"]
    assert report.summary


def test_sanitize_truncates_long_fields() -> None:
    policy = ModelDataPolicy(max_field_chars=10)
    sanitized, report = policy.sanitize_payload({"description": "x" * 100})
    assert report.truncated_characters == 90
    assert "[truncated]" in sanitized["description"]


def test_validate_outgoing_rejects_secrets() -> None:
    policy = ModelDataPolicy()
    ok, hits = policy.validate_outgoing("normal content")
    assert ok
    rejected, secret_hits = policy.validate_outgoing("api_key=sk-abcdef1234567890")
    assert not rejected
    assert secret_hits


def test_validate_outgoing_rejects_url_pattern() -> None:
    policy = ModelDataPolicy(allowed_url_patterns=(r"https?://internal\.corp",))
    ok, _ = policy.validate_outgoing("https://internal.corp/config")
    assert not ok


def test_redaction_report_counts() -> None:
    report = RedactionReport(
        local_only_fields=("a",),
        redacted_fields=("b",),
        forbidden_fields=("c",),
        secrets_removed=1,
        truncated_characters=5,
    )
    assert "local_only=1" in report.summary


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_budget_tracking_and_limits() -> None:
    tracker = BudgetTracker(CostLatencyBudgetConfig(max_tokens=100, max_requests=3))
    tracker.record(usage=TokenUsage(total_tokens=50), latency_ms=10, cost=0.1)
    tracker.check()
    tracker.record(usage=TokenUsage(total_tokens=60), latency_ms=10)
    with pytest.raises(AgentLoopLimit):
        tracker.check()
    snapshot = tracker.snapshot()
    assert snapshot["total_requests"] == 2
    assert snapshot["estimated_cost"] == 0.1


def test_budget_request_limit() -> None:
    tracker = BudgetTracker(CostLatencyBudgetConfig(max_requests=1))
    tracker.record()
    tracker.record()
    with pytest.raises(AgentLoopLimit):
        tracker.check()


def test_budget_cost_limit() -> None:
    tracker = BudgetTracker(CostLatencyBudgetConfig(max_estimated_cost=1.0))
    tracker.record(cost=2.0)
    with pytest.raises(AgentLoopLimit):
        tracker.check()


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_failure_handler_classifies() -> None:
    handler = ModelFailureHandler()
    assert handler.classify("timeout") is ModelTimeoutError
    assert handler.classify("429") is ModelRateLimitError
    assert handler.classify("5xx") is ModelServerError
    assert handler.classify("malformed_json") is ModelMalformedOutputError
    assert handler.classify("refusal") is ModelRefusalError
    assert handler.classify("context_overflow") is ModelContextOverflowError
    assert handler.classify("provider_unavailable") is ProviderUnavailableError
    with pytest.raises(ValueError):
        handler.classify("unknown-scenario")


def test_failure_handler_handle_raises_typed() -> None:
    handler = ModelFailureHandler()
    error = handler.handle("429")
    assert isinstance(error, ModelRateLimitError)
    assert "Simulated" in str(error)


# ---------------------------------------------------------------------------
# Additional provider branch coverage
# ---------------------------------------------------------------------------


def test_availability_ok_with_secret() -> None:
    provider = _provider()
    health = asyncio.run(provider.availability())
    assert health.available is True
    assert health.reason == "ok"


async def test_complete_without_http_client_fails_closed() -> None:
    provider = _provider(transport=None)  # no client configured
    with pytest.raises(ProviderUnavailableError):
        await provider.complete(ModelRequest(user_prompt="hi"))


async def test_transport_error_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(ModelServerError):
        await provider.complete(ModelRequest(user_prompt="hi"))


async def test_unexpected_status_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(ModelServerError):
        await provider.complete(ModelRequest(user_prompt="hi"))


async def test_non_json_response_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(ModelMalformedOutputError):
        await provider.complete(
            ModelRequest(user_prompt="hi", required_capability=ModelCapability.STRUCTURED_OUTPUT)
        )


async def test_structured_output_must_be_object() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "[1, 2, 3]"}}]},
        )

    provider = _provider(transport=httpx.MockTransport(handler))
    with pytest.raises(ModelMalformedOutputError):
        await provider.complete(
            ModelRequest(user_prompt="hi", required_capability=ModelCapability.STRUCTURED_OUTPUT)
        )
