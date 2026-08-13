"""Phase 26.1 section re-check: attack-chain + evidence grounding.

Measurement-fix re-run ONLY (frozen scenarios/answers unchanged):
- attack chain: max_tokens raised to 4096 (2048 truncated the chain JSON)
- all real calls throttled (1.5s) + 429 backoff to avoid rate-limit
  invalidation (the full run's grounding section got rate-limited to 0)

Saves outputs/p26_1_recheck.json. Nothing here changes the benchmark.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, ".")

from app.agent.attackchain import AttackChainAnalyzer
from app.agent.datapolicy import ModelDataPolicy
from app.agent.failures import ModelFailure
from app.agent.providers import ModelConfig, OpenAICompatibleLLMProvider
from app.agent.triage import TriageAgent
from app.sandbox.secret import MemorySecretProvider
from scripts.certify_real_model import (
    build_attack_chain_cases,
    build_scenarios_v2,
    freeze_dataset,
)

MODELS_JSON = Path(r"C:\Users\JianXi\.workbuddy\models.json")
REAL_MODEL_ID = "deepseek-v4-flash"
REAL_BASE_URL = "https://token.sensenova.cn/v1"
SECRET_NAME = "llm-openai-api-key"
ALLOWED_BASE_URLS = ("https://token.sensenova.cn/v1",)
PROMPT_VERSION = "phase26-triage-v1"
OUT = Path("outputs/p26_1_recheck.json")


def build_provider() -> tuple[OpenAICompatibleLLMProvider, httpx.AsyncClient]:
    data = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
    key = next(entry["apiKey"] for entry in data if entry["id"] == REAL_MODEL_ID)
    client = httpx.AsyncClient(timeout=120.0)
    return (
        OpenAICompatibleLLMProvider(
            MemorySecretProvider(values={SECRET_NAME: key}),
            ModelConfig(
                model=REAL_MODEL_ID,
                base_url=REAL_BASE_URL,
                secret_name=SECRET_NAME,
                timeout_seconds=120.0,
                max_tokens=4096,
                temperature=0.0,
                retry_limit=1,
                structured_output=True,
                structured_output_hint=(
                    "Output a single JSON object. \"classification\" must be exactly "
                    "one of: BENIGN, SUSPICIOUS, MALICIOUS, UNKNOWN. "
                    "\"severity_assessment\" one of: LOW, MEDIUM, HIGH, CRITICAL, "
                    "UNKNOWN. \"confidence\" must be a number between 0 and 1. "
                    "\"evidence_refs\" must be a list; reference only evidence "
                    "identifiers present in the input, otherwise use []."
                ),
            ),
            policy=ModelDataPolicy(),
            http_client=client,
            allowed_base_urls=ALLOWED_BASE_URLS,
        ),
        client,
    )


async def _throttled_call(call: Any, *, step_name: str) -> Any:
    """Await a real model call factory with throttle + 429 backoff.

    ``call`` must be a zero-arg callable returning a fresh coroutine each
    invocation so retries never reuse an awaited coroutine.
    """
    for attempt in range(4):
        try:
            return await call()
        except Exception as error:  # noqa: BLE001
            code = getattr(error, "code", "") or type(error).__name__
            if "429" in str(code) or "rate" in str(error).lower():
                wait = 5.0 * (attempt + 1)
                print(f"[{step_name}] 429 backoff {wait}s ...", flush=True)
                await asyncio.sleep(wait)
                continue
            raise
    raise RuntimeError(f"{step_name}: sustained rate limiting")


async def run_chains(
    provider: OpenAICompatibleLLMProvider, cases: list[dict[str, Any]]
) -> dict[str, Any]:
    analyzer = AttackChainAnalyzer(provider, policy=ModelDataPolicy())
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        try:
            output = await _throttled_call(
                lambda: analyzer.analyze(events=case["events"]),
                step_name=f"chain-{case['id']}",
            )
            hypothesis = output.hypothesis
            actual = [
                stage.technique_id
                for stage in hypothesis.ordered_stages
                if stage.technique_id
            ]
            results.append(
                {
                    "case": case["id"],
                    "stages": case["stages"],
                    "expected_techniques": case["expected_techniques"],
                    "actual_techniques": actual,
                    "technique_hit": len(set(actual) & set(case["expected_techniques"])),
                    "grounded": bool(hypothesis.supporting_evidence),
                    "alternative_hypotheses": len(hypothesis.alternative_hypotheses),
                    "gaps": len(hypothesis.gaps),
                }
            )
        except ModelFailure as error:
            results.append(
                {
                    "case": case["id"],
                    "stages": case["stages"],
                    "expected_techniques": case["expected_techniques"],
                    "actual_techniques": [],
                    "technique_hit": 0,
                    "grounded": False,
                    "alternative_hypotheses": 0,
                    "gaps": 0,
                    "error": f"{type(error).__name__}: {str(error)[:100]}",
                }
            )
        await asyncio.sleep(1.5)
        print(f"[chains] {index + 1}/{len(cases)} done", flush=True)
    total = len(cases)
    hit = sum(result["technique_hit"] for result in results)
    expected_total = sum(len(case["expected_techniques"]) for case in cases)
    failed = sum(1 for result in results if result.get("error"))
    return {
        "total_cases": total,
        "failed_cases": failed,
        "error_sample": [r["error"] for r in results if r.get("error")][:3],
        "technique_mapping_recall": round(hit / expected_total, 4) if expected_total else 0.0,
        "grounded_rate": (
            round(sum(1 for r in results if r["grounded"]) / total, 4) if total else 0.0
        ),
        "stage_order_ok": (
            round(
                sum(1 for r in results if r["stages"] == len(r["actual_techniques"])) / total,
                4,
            )
            if total
            else 0.0
        ),
        "cases": results,
    }


async def run_grounding(
    provider: OpenAICompatibleLLMProvider, scenarios: list[dict[str, Any]]
) -> dict[str, Any]:
    agent = TriageAgent(provider, policy=ModelDataPolicy())
    supported = 0
    unsupported = 0
    failed = 0
    checked = 0
    samples: list[dict[str, Any]] = []
    for index, scenario in enumerate(scenarios[:40]):
        source = scenario["source"]
        try:
            output = await _throttled_call(
                lambda: agent.triage(source=source, context=scenario["context"]),
                step_name=f"grounding-{scenario['id']}",
            )
        except ModelFailure as error:
            failed += 1
            samples.append(
                {
                    "id": scenario["id"],
                    "status": "FAILED",
                    "error": f"{type(error).__name__}: {str(error)[:80]}",
                }
            )
            await asyncio.sleep(1.5)
            print(f"[grounding] {index + 1}/40 failed", flush=True)
            continue
        checked += 1
        known = set(source.get("evidence_refs", []))
        refs = output.result.evidence_refs
        if not known:
            unsupported += 1
            samples.append({"id": scenario["id"], "status": "NO_EVIDENCE_INPUT", "refs": refs})
        elif refs and all(r in known for r in refs):
            supported += 1
            samples.append({"id": scenario["id"], "status": "SUPPORTED", "refs": refs})
        else:
            unsupported += 1
            samples.append({"id": scenario["id"], "status": "UNSUPPORTED", "refs": refs})
        await asyncio.sleep(1.5)
        print(f"[grounding] {index + 1}/40 checked={checked} supported={supported}", flush=True)
    return {
        "checked": checked,
        "supported": supported,
        "unsupported": unsupported,
        "failed": failed,
        "support_rate": round(supported / checked, 4) if checked else 0.0,
        "samples": samples[:10],
    }


async def main() -> None:
    provider, client = build_provider()
    health = await provider.health_check()
    print(f"health_check: {health}", flush=True)
    if not health:
        print("BLOCKED: provider unhealthy")
        await client.aclose()
        return

    scenarios = build_scenarios_v2()
    chains = build_attack_chain_cases()
    frozen = {"scenarios": scenarios, "chains": chains}
    dataset_hash = freeze_dataset(frozen)

    chain_results = await run_chains(provider, chains)
    grounding_results = await run_grounding(provider, scenarios)
    await client.aclose()

    result = {
        "provider": REAL_MODEL_ID,
        "base_url": REAL_BASE_URL,
        "prompt_version": PROMPT_VERSION,
        "dataset_sha256": dataset_hash,
        "frozen_scenario_count": len(scenarios),
        "attack_chain_benchmark": chain_results,
        "grounding_report": grounding_results,
        "note": (
            "Measurement-fix re-run: attack chain max_tokens 4096 (was 2048, "
            "truncated JSON), all calls throttled 1.5s + 429 backoff. Frozen "
            "scenarios/expected answers unchanged."
        ),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {OUT}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
