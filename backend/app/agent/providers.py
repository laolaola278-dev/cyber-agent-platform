"""OpenAI-compatible real LLM provider (v2.0 / Phase 26).

Security rules:
- credentials are resolved ONLY through the platform SecretProvider;
  reading .env secrets directly is forbidden;
- base_url is allowlisted (configuration first, fail closed);
- when no valid secret exists the capability is DEGRADED and the provider
  refuses to complete; it never falls back to an unauthenticated provider;
- all model failures fail closed: a failed model never bypasses policy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from time import monotonic
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.agent.contracts import (
    LLMProvider,
    ModelCapability,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)
from app.agent.datapolicy import ModelDataPolicy
from app.agent.failures import (
    ModelMalformedOutputError,
    ModelRateLimitError,
    ModelServerError,
    ModelTimeoutError,
    ProviderUnavailableError,
)
from app.sandbox.secret import SecretProvider, SecretReference

# Allowlisted endpoints (configuration-first, fail-closed).
DEFAULT_ALLOWED_BASE_URLS: tuple[str, ...] = (
    "https://api.openai.com/v1",
    "https://api.openai.com",
    "https://api.deepseek.com/v1",
    "https://api.deepseek.com",
    "https://api.anthropic.com/v1",
    "https://generativelanguage.googleapis.com/v1beta",
    "https://localhost:8000/v1",
    "http://localhost:8000/v1",
)


class ModelConfig(BaseModel):
    """Configuration-first model settings. Domain code never hardcodes models."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    secret_name: str = "llm-openai-api-key"
    timeout_seconds: float = 30.0
    max_tokens: int = 2048
    temperature: float = 0.0
    retry_limit: int = 2
    structured_output: bool = True
    structured_output_hint: str = ""
    prompt_usage_capture: bool = True
    cost_per_1k_prompt: float = 0.0
    cost_per_1k_completion: float = 0.0


class ModelPrompt(BaseModel):
    """The exact payload sent to the model (post policy)."""

    model_config = ConfigDict(frozen=True)

    system_prompt: str
    user_prompt: str
    data_fields: tuple[str, ...]
    redaction_summary: str
    policy_version: str = "phase26-v1"


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    available: bool
    reason: str
    model: str | None = None


class OpenAICompatibleLLMProvider(LLMProvider):
    """Real OpenAI-compatible provider behind the platform security boundary."""

    name = "openai-compatible"

    def __init__(
        self,
        secret_provider: SecretProvider,
        config: ModelConfig,
        *,
        policy: ModelDataPolicy | None = None,
        http_client: Any | None = None,
        allowed_base_urls: tuple[str, ...] = DEFAULT_ALLOWED_BASE_URLS,
    ) -> None:
        self._secrets = secret_provider
        self._config = config
        self._policy = policy or ModelDataPolicy()
        self._http = http_client
        self._allowed_base_urls = allowed_base_urls
        self._last_prompt: ModelPrompt | None = None

    # -- configuration / capability -----------------------------------------

    def validate_configuration(self) -> None:
        if not self._config.model.strip():
            raise ProviderUnavailableError("Model configuration missing: model name is empty")
        if not any(
            self._config.base_url.rstrip("/").startswith(url.rstrip("/"))
            for url in self._allowed_base_urls
        ):
            raise ProviderUnavailableError(f"base_url is not allowlisted: {self._config.base_url}")

    async def _resolve_key(self) -> SecretStr:
        reference = SecretReference(
            name=self._config.secret_name,
            provider="memory",
            purpose="llm-completion",
        )
        resolved = await self._secrets.resolve(reference)
        return resolved.value

    async def health_check(self) -> bool:
        try:
            self.validate_configuration()
            await self._resolve_key()
            return True
        except Exception:  # noqa: BLE001 - capability degradation on any failure
            return False

    async def availability(self) -> ProviderHealth:
        if not await self.health_check():
            return ProviderHealth(
                available=False,
                reason="missing or invalid LLM secret; capability degraded (no fallback)",
                model=self._config.model,
            )
        return ProviderHealth(available=True, reason="ok", model=self._config.model)

    def supports(self, capability: ModelCapability) -> bool:
        if capability == ModelCapability.STRUCTURED_OUTPUT:
            return self._config.structured_output
        return capability == ModelCapability.TEXT

    # -- completion ----------------------------------------------------------

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.validate_configuration()
        if not await self.health_check():
            raise ProviderUnavailableError(
                "LLM capability is degraded: no valid secret (fail closed, no fallback)"
            )

        sanitized, redaction = self._policy.sanitize_payload(
            {**(request.extra if isinstance(request.extra, dict) else {}), **{"data": request.data}}
        )
        allowed, forbidden_hits = self._policy.validate_outgoing(
            request.user_prompt + request.system_prompt + json.dumps(sanitized, ensure_ascii=False)
        )
        if not allowed:
            raise ProviderUnavailableError(
                f"Model data policy rejected outgoing payload: {forbidden_hits}"
            )

        self._last_prompt = ModelPrompt(
            system_prompt=request.system_prompt,
            user_prompt=request.user_prompt,
            data_fields=tuple(sanitized.keys()),
            redaction_summary=redaction.summary,
        )

        payload = self._build_request_payload(request, sanitized)
        started = monotonic()
        response_text, usage = await self._post_with_retry(payload)
        latency_ms = int((monotonic() - started) * 1000)

        structured: dict[str, Any] | None = None
        if (
            self._config.structured_output
            and request.required_capability == ModelCapability.STRUCTURED_OUTPUT
        ):
            structured = self._parse_structured(response_text)

        return ModelResponse(
            content=response_text,
            structured=structured,
            usage=usage,
            provider=self.name,
            latency_ms=latency_ms,
            finish_reason="stop",
        )

    def _build_request_payload(
        self, request: ModelRequest, sanitized: dict[str, Any]
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]
        if sanitized:
            messages.append({"role": "user", "content": json.dumps(sanitized, ensure_ascii=False)})
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
        }
        if self._config.structured_output:
            payload["response_format"] = {"type": "json_object"}
        # Some OpenAI-compatible endpoints require the word "json" to appear in
        # the messages before they accept response_format=json_object. The
        # config-driven hint is a transport-level adaptation only; it never
        # changes the scenario inputs, expected answers or guardrails.
        if self._config.structured_output_hint:
            payload["messages"] = [
                *payload["messages"],
                {"role": "user", "content": self._config.structured_output_hint},
            ]
        return payload

    async def _post_with_retry(self, payload: dict[str, Any]) -> tuple[str, TokenUsage]:
        last_error: Exception | None = None
        for _attempt in range(self._config.retry_limit + 1):
            try:
                return await self._post_once(payload)
            except ModelRateLimitError as error:
                last_error = error
            except ModelServerError as error:
                last_error = error
        if isinstance(last_error, ModelRateLimitError):
            raise ModelRateLimitError("Model rate limit persisted after retries") from last_error
        raise ModelServerError("Model server error persisted after retries") from last_error

    async def _post_once(self, payload: dict[str, Any]) -> tuple[str, TokenUsage]:
        if self._http is None:
            raise ProviderUnavailableError("LLM HTTP client is not configured")
        import httpx

        url = f"{self._config.base_url.rstrip('/')}/chat/completions"
        resolved_key = await self._resolve_key()
        headers = {
            "Authorization": f"Bearer {resolved_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        try:
            response = await self._http.post(
                url, json=payload, headers=headers, timeout=self._config.timeout_seconds
            )
        except httpx.TimeoutException as error:
            raise ModelTimeoutError("Model request timed out") from error
        except httpx.TransportError as error:
            raise ModelServerError(f"Model transport error: {error}") from error

        if response.status_code == 429:
            raise ModelRateLimitError("Model rate limit exceeded")
        if response.status_code >= 500:
            raise ModelServerError(f"Model server error: {response.status_code}")
        if response.status_code != 200:
            raise ModelServerError(f"Model returned HTTP {response.status_code}")

        try:
            body = response.json()
        except ValueError as error:
            raise ModelMalformedOutputError("Model returned non-JSON response") from error

        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage_data = body.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=int(usage_data.get("prompt_tokens", 0)),
            completion_tokens=int(usage_data.get("completion_tokens", 0)),
            total_tokens=int(usage_data.get("total_tokens", 0)),
        )
        return str(content), usage

    @staticmethod
    def _parse_structured(content: str) -> dict[str, Any]:
        """Parse JSON from model output; malformed output fails closed."""
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise ModelMalformedOutputError(f"Model returned malformed JSON: {error}") from error
        if not isinstance(parsed, dict):
            raise ModelMalformedOutputError("Model structured output must be a JSON object")
        return parsed

    # -- cost estimation -----------------------------------------------------

    def estimate_cost(self, usage: TokenUsage) -> float:
        return (
            usage.prompt_tokens / 1000 * self._config.cost_per_1k_prompt
            + usage.completion_tokens / 1000 * self._config.cost_per_1k_completion
        )
