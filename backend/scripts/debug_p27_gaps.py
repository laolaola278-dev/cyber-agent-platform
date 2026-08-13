"""Debug grounding and completion gaps in hybrid harness."""
import asyncio
import sys
from collections import Counter

sys.path.insert(0, ".")

from app.agent.evaluation2 import build_scenarios_v2
from app.hybrid.engine import HybridEngine, HybridEngineConfig
from app.hybrid.evaluation3 import HybridEvaluationHarness
from app.hybrid.retrieval import MemoryKnowledgeRetriever


async def main() -> None:
    scenarios = build_scenarios_v2()
    knowledge = MemoryKnowledgeRetriever(
        entries=[
            {"knowledge_type": "ATT&CK", "external_id": "T1566", "title": "P", "keywords": ["phish"]},
        ]
    )
    engine = HybridEngine(
        knowledge=knowledge,
        llm_ranker=None,
        config=HybridEngineConfig(use_llm=False, use_retrieval=True),
    )
    harness = HybridEvaluationHarness(engine=engine, name="x")
    not_completed: Counter = Counter()
    not_grounded: list[str] = []
    for s in scenarios:
        o = await harness._run_one(s)
        if not o.completed:
            not_completed[s["category"]] += 1
        elif o.completed and not o.grounded and s["category"] not in _INJ:
            not_grounded.append(f"{s['id']}:{s['category']}")
    print("not_completed:", dict(not_completed))
    print("not_grounded sample:", not_grounded[:12], "total:", len(not_grounded))


_INJ = {
    "web_prompt_injection",
    "log_prompt_injection",
    "unicode_injection",
    "base64_injection",
    "cross_turn_injection",
    "tool_output_poisoning",
    "handoff_poisoning",
}


if __name__ == "__main__":
    asyncio.run(main())
