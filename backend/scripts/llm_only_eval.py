"""CAP-SIB v1 LLM-only baseline evaluation (Holdout, real LLM).

Raw LLM triage (Phase 26.1 style TriageAgent) on the SIB holdout without any
deterministic engine / retrieval. Companion to sib_eval.py --real --holdout.
Run AFTER the hybrid certification so the two runs do not compete for the
rate-limited endpoint.

Usage:
  python scripts/llm_only_eval.py            # holdout, real LLM (15s throttle)
  python scripts/llm_only_eval.py --dev      # dev split instead
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from app.hybrid.sib import build_sib_v1, freeze_sib
from app.hybrid.sibadapters import llm_only_scorer
from app.hybrid.sibharness import SIBHarness, explainability_report

sys.path.insert(0, ".")

OUT = Path("outputs/cap_sib_v1_llm_only.json")
MODELS_JSON = Path(r"C:\Users\JianXi\.workbuddy\models.json")
REAL_MODEL_ID = "deepseek-v4-flash"
REAL_BASE_URL = "https://token.sensenova.cn/v1"
SECRET_NAME = "llm-openai-api-key"
ALLOWED_BASE_URLS = ("https://token.sensenova.cn/v1",)
THROTTLE_SECONDS = 15.0


async def make_real_provider() -> tuple[Any, httpx.AsyncClient]:
    """Raw provider + client (no ranker wrapper; TriageAgent calls complete)."""
    from app.agent.datapolicy import ModelDataPolicy
    from app.agent.providers import ModelConfig, OpenAICompatibleLLMProvider
    from app.sandbox.secret import MemorySecretProvider

    data = json.loads(MODELS_JSON.read_text(encoding="utf-8"))  # noqa: ASYNC240
    key = next(e["apiKey"] for e in data if e["id"] == REAL_MODEL_ID)
    client = httpx.AsyncClient(timeout=120.0)
    provider = OpenAICompatibleLLMProvider(
        MemorySecretProvider(values={SECRET_NAME: key}),
        ModelConfig(
            model=REAL_MODEL_ID,
            base_url=REAL_BASE_URL,
            secret_name=SECRET_NAME,
            timeout_seconds=120.0,
            max_tokens=1024,
            temperature=0.0,
            retry_limit=2,
            structured_output=True,
            structured_output_hint="Output a single JSON object.",
        ),
        policy=ModelDataPolicy(),
        http_client=client,
        allowed_base_urls=ALLOWED_BASE_URLS,
    )
    health = await provider.health_check()
    if not health:
        await client.aclose()
        raise RuntimeError("real provider unhealthy")
    return provider, client


def main() -> None:
    splits = ("dev",) if "--dev" in sys.argv else ("holdout",)
    dataset = build_sib_v1()
    scenarios = [s.to_dict() for s in dataset]
    dataset_hash = freeze_sib(dataset)

    loop = asyncio.new_event_loop()
    provider, client = loop.run_until_complete(make_real_provider())

    calls = {"complete": 0, "success": 0, "failures": 0}
    original_complete = provider.complete
    last_call = {"t": 0.0}

    async def throttled_complete(request: Any) -> Any:
        elapsed = time.monotonic() - last_call["t"]
        if elapsed < THROTTLE_SECONDS:
            await asyncio.sleep(THROTTLE_SECONDS - elapsed)
        last_call["t"] = time.monotonic()
        calls["complete"] += 1
        try:
            response = await original_complete(request)
            calls["success"] += 1
            return response
        except Exception:
            calls["failures"] += 1
            raise

    provider.complete = throttled_complete  # type: ignore[method-assign]
    from app.agent.triage import TriageAgent

    scorer = llm_only_scorer(lambda: TriageAgent(provider))
    collected: list[Any] = []
    original_scorer = scorer

    def collecting_scorer(scenario: dict[str, Any]) -> Any:
        prediction = original_scorer(scenario)
        collected.append(prediction)
        return prediction

    started = time.monotonic()
    harness = SIBHarness(scorer=collecting_scorer, name="llm_only")
    reports = harness.run(scenarios, splits=splits)
    wall = round(time.monotonic() - started, 1)

    results: dict[str, Any] = {
        "dataset": {"version": "cap-sib-v1", "sha256": dataset_hash, "total": 300},
        "splits": list(splits),
        "model_config": {
            "provider": REAL_MODEL_ID,
            "prompt_version": "phase26-triage-v1",
            "throttle_seconds": THROTTLE_SECONDS,
        },
        "groups": {"llm_only": {k: v.to_dict() for k, v in reports.items()}},
        "llm_only_wall_seconds": wall,
        "llm_only_llm_calls": calls,
        "llm_only_success_rate": round(calls["success"] / max(calls["complete"], 1), 4),
    }
    if splits == ("holdout",):
        results["llm_only_explainability"] = explainability_report(collected)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")  # noqa: ASYNC240
    print(f"llm_only {splits} done | hash: {dataset_hash} | wall: {wall}s | calls: {calls}")
    success_rate = calls["success"] / max(calls["complete"], 1)
    print(f"success_rate: {success_rate:.4f}")


if __name__ == "__main__":
    main()
