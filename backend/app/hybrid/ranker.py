"""Phase 27 -- LLM ranker adapter.

Wraps any LLMProvider (Fake or Real) behind a *rank/explain only* interface.
The LLM never produces facts, techniques outside a candidate set, severity
overrides, or execution. This is the only surface the hybrid engine exposes
to a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TechniqueRankResponse:
    order: list[str] = field(default_factory=list)
    explanation: str = ""


class LLMRanker:
    """Adapters to a provider via lightweight structured prompts."""

    def __init__(
        self,
        provider: Any,
        *,
        max_tokens: int = 256,
        throttle_seconds: float = 0.0,
    ) -> None:
        self._provider = provider
        self._max_tokens = max_tokens
        self._throttle_seconds = throttle_seconds

    async def _throttle(self) -> None:
        """Optional inter-call pacing to avoid provider rate limits."""
        if self._throttle_seconds > 0:
            import asyncio

            await asyncio.sleep(self._throttle_seconds)

    async def rank_techniques(self, technique_ids: list[str]) -> TechniqueRankResponse:
        """Ask the model to rank the CLOSED candidate set. Never expands it."""
        if not technique_ids:
            return TechniqueRankResponse(order=[], explanation="")
        await self._throttle()
        prompt = (
            "You rank ATT&CK technique candidates for a security investigation. "
            'Respond ONLY with JSON: {"order": ["T...", ...], "explanation": "..."}. '
            "Your order must be a permutation of the given candidates; you MUST NOT "
            "invent or add techniques. Candidates: " + ", ".join(technique_ids)
        )
        content = await self._prompt(prompt)
        order, explanation = self._parse(content)
        return TechniqueRankResponse(order=order, explanation=explanation)

    async def explain(
        self,
        *,
        statement: str,
        factors: list[str],
        evidence_refs: list[str],
    ) -> str:
        """Produce a one-paragraph rationale that cites the given factors."""
        await self._throttle()
        prompt = (
            "Explain the following security judgment. Your answer must reference "
            "the listed factors and evidence references explicitly; do not add "
            "unfounded claims. Keep it to 3 sentences.\n"
            f"Judgment: {statement}\n"
            f"Factors: {', '.join(factors)}\n"
            f"Evidence: {', '.join(evidence_refs) if evidence_refs else 'none'}"
        )
        content = await self._prompt(prompt)
        return self._clean(content)

    async def _prompt(self, prompt: str) -> str:
        from app.agent.contracts import ModelCapability, ModelRequest

        request = ModelRequest(
            system_prompt=(
                "You are a reasoning assistant inside a governed security "
                "platform. You only rank, explain and summarize. You never "
                "execute, never invent facts, and never output hidden reasoning."
            ),
            user_prompt=prompt,
            required_capability=ModelCapability.STRUCTURED_OUTPUT,
        )
        response = await self._provider.complete(request)
        return response.content or ""

    @staticmethod
    def _parse(content: str) -> tuple[list[str], str]:
        import json
        import re

        text = content.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return [], ""
        try:
            payload = json.loads(match.group(0))
            order = [str(t).upper() for t in payload.get("order", [])]
            explanation = str(payload.get("explanation", ""))
            return order, explanation
        except (json.JSONDecodeError, ValueError):
            return [], ""

    @staticmethod
    def _clean(content: str) -> str:
        return content.strip().strip('"').strip()
