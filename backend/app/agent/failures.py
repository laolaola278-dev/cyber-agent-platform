"""Model failure handling (v2.0 / Phase 26).

Every model failure is surfaced as a typed error and MUST fail closed: a
failed or misbehaving model never bypasses policy. Real providers never
fall back to an unauthenticated provider.
"""

from __future__ import annotations

from app.agent.exceptions import AgentError


class ModelFailure(AgentError):
    """Base class for all model failures (fail closed)."""

    code = "MODEL_FAILURE"
    status_code = 503


class ModelTimeoutError(ModelFailure):
    code = "MODEL_TIMEOUT"
    status_code = 504


class ModelRateLimitError(ModelFailure):
    code = "MODEL_RATE_LIMIT"
    status_code = 429


class ModelServerError(ModelFailure):
    code = "MODEL_SERVER_ERROR"
    status_code = 502


class ModelMalformedOutputError(ModelFailure):
    code = "MODEL_MALFORMED_OUTPUT"
    status_code = 422


class ModelRefusalError(ModelFailure):
    code = "MODEL_REFUSAL"
    status_code = 422


class ModelContextOverflowError(ModelFailure):
    code = "MODEL_CONTEXT_OVERFLOW"
    status_code = 413


class ProviderUnavailableError(ModelFailure):
    code = "PROVIDER_UNAVAILABLE"
    status_code = 503


FAILURE_SCENARIOS: tuple[str, ...] = (
    "timeout",
    "429",
    "5xx",
    "malformed_json",
    "refusal",
    "context_overflow",
    "provider_unavailable",
)


class ModelFailureHandler:
    """Classifies provider-level failure scenarios for evaluation."""

    def classify(self, scenario: str) -> type[ModelFailure]:
        scenario = scenario.casefold().strip()
        if scenario in {"timeout", "timeout_error"}:
            return ModelTimeoutError
        if scenario in {"429", "rate_limit"}:
            return ModelRateLimitError
        if scenario in {"5xx", "server_error"}:
            return ModelServerError
        if scenario in {"malformed_json", "malformed"}:
            return ModelMalformedOutputError
        if scenario in {"refusal", "refused"}:
            return ModelRefusalError
        if scenario in {"context_overflow", "overflow"}:
            return ModelContextOverflowError
        if scenario in {"provider_unavailable", "unavailable", "missing_secret"}:
            return ProviderUnavailableError
        raise ValueError(f"Unknown failure scenario: {scenario}")

    def handle(self, scenario: str) -> ModelFailure:
        """Raise the typed failure for a scenario (used by the evaluation harness)."""
        failure_type = self.classify(scenario)
        return failure_type(f"Simulated model failure: {scenario}")
