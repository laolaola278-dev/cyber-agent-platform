"""Probe real LLM success rate on a small SIB subset (>=95% gate)."""
import asyncio
import json
import sys

sys.path.insert(0, ".")

import httpx

from app.agent.datapolicy import ModelDataPolicy
from app.agent.providers import ModelConfig, OpenAICompatibleLLMProvider
from app.hybrid.ranker import LLMRanker
from app.sandbox.secret import MemorySecretProvider

MODELS_JSON = r"C:\Users\JianXi\.workbuddy\models.json"
REAL_MODEL_ID = "deepseek-v4-flash"


async def main() -> None:
    data = json.loads(open(MODELS_JSON, encoding="utf-8").read())  # noqa: ASYNC230
    key = next(e["apiKey"] for e in data if e["id"] == REAL_MODEL_ID)
    client = httpx.AsyncClient(timeout=120.0)
    provider = OpenAICompatibleLLMProvider(
        MemorySecretProvider(values={"llm-openai-api-key": key}),
        ModelConfig(
            model=REAL_MODEL_ID,
            base_url="https://token.sensenova.cn/v1",
            secret_name="llm-openai-api-key",
            timeout_seconds=120.0,
            max_tokens=1024,
            temperature=0.0,
            retry_limit=2,
            structured_output=True,
            structured_output_hint="Output a single JSON object.",
        ),
        policy=ModelDataPolicy(),
        http_client=client,
        allowed_base_urls=("https://token.sensenova.cn/v1",),
    )
    print("health:", await provider.health_check())
    ranker = LLMRanker(provider, throttle_seconds=10.0)

    success = 0
    failures = 0
    for index in range(12):
        try:
            response = await ranker.rank_techniques(["T1566", "T1059", "T1071"])
            if response.order:
                success += 1
            else:
                failures += 1
        except Exception as error:  # noqa: BLE001
            failures += 1
            print(f"  [{index}] fail: {type(error).__name__} {str(error)[:80]}")
    rate = success / (success + failures)
    print(f"rank success: {success}/{success+failures} = {rate:.4f}")
    print("GATE >=0.95 ->", "PASS" if rate >= 0.95 else "FAIL")
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
