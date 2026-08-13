"""Cost & latency budget for agent investigations (v2.0 / Phase 26).

Tracks tokens, requests, latency and estimated cost per investigation and
enforces a configurable budget. Exceeding any budget stops the agent loop
(fail closed, never silently continues).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field

from app.agent.contracts import TokenUsage
from app.agent.exceptions import AgentLoopLimit


class CostLatencyBudgetConfig(BaseModel):
    """Configuration-first budget limits."""

    model_config = ConfigDict(frozen=True)

    max_tokens: int = Field(default=100_000, ge=1)
    max_requests: int = Field(default=50, ge=1)
    max_latency_seconds: float = Field(default=300.0, gt=0)
    max_estimated_cost: float = Field(default=10.0, ge=0.0)


@dataclass(slots=True)
class BudgetTracker:
    """Per-investigation usage tracking."""

    config: CostLatencyBudgetConfig
    total_tokens: int = 0
    total_requests: int = 0
    total_latency_ms: int = 0
    estimated_cost: float = 0.0
    started_at: float = field(default_factory=monotonic)

    def record(
        self,
        *,
        usage: TokenUsage | None = None,
        latency_ms: int = 0,
        cost: float = 0.0,
    ) -> None:
        self.total_requests += 1
        self.total_latency_ms += latency_ms
        self.estimated_cost += cost
        if usage is not None:
            self.total_tokens += usage.total_tokens

    def check(self) -> None:
        """Raise AgentLoopLimit when any budget is exceeded (fail closed)."""
        elapsed = monotonic() - self.started_at
        if self.total_tokens > self.config.max_tokens:
            raise AgentLoopLimit(
                f"token budget exceeded: {self.total_tokens} > {self.config.max_tokens}"
            )
        if self.total_requests > self.config.max_requests:
            raise AgentLoopLimit(
                f"request budget exceeded: {self.total_requests} > {self.config.max_requests}"
            )
        if elapsed > self.config.max_latency_seconds:
            raise AgentLoopLimit(
                f"latency budget exceeded: {elapsed:.1f}s > {self.config.max_latency_seconds}s"
            )
        if self.estimated_cost > self.config.max_estimated_cost:
            raise AgentLoopLimit(
                "cost budget exceeded: "
                f"${self.estimated_cost:.4f} > ${self.config.max_estimated_cost}"
            )

    def snapshot(self) -> dict[str, float | int]:
        return {
            "total_tokens": self.total_tokens,
            "total_requests": self.total_requests,
            "total_latency_ms": self.total_latency_ms,
            "estimated_cost": round(self.estimated_cost, 6),
            "elapsed_seconds": round(monotonic() - self.started_at, 3),
        }
