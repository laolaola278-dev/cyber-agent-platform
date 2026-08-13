"""Debug chain stage output for multi-stage scenarios."""
import asyncio
import sys

sys.path.insert(0, ".")

from app.agent.evaluation2 import build_scenarios_v2
from app.hybrid.engine import HybridEngine, HybridEngineConfig
from app.hybrid.retrieval import MemoryKnowledgeRetriever


async def main() -> None:
    scenarios = build_scenarios_v2()
    knowledge = MemoryKnowledgeRetriever(
        entries=[
            {"knowledge_type": "ATT&CK", "external_id": "T1566", "title": "Phishing", "keywords": ["phish"]},
            {"knowledge_type": "ATT&CK", "external_id": "T1059", "title": "Command", "keywords": ["command"]},
            {"knowledge_type": "ATT&CK", "external_id": "T1078", "title": "Valid Accounts", "keywords": ["account"]},
        ]
    )
    engine = HybridEngine(
        knowledge=knowledge,
        llm_ranker=None,
        config=HybridEngineConfig(use_llm=False, use_retrieval=True),
    )
    for scenario in scenarios:
        if scenario["category"] in ("multi_stage_attack", "normal_investigation"):
            out = await engine.triage(
                source=scenario["source"], context=scenario["context"], events=[scenario["source"]]
            )
            print(
                scenario["id"],
                "| expected:", scenario["expected"].get("techniques"),
                "| mapped:", out.technique_mapping.mapped_techniques,
                "| stages:", out.chain_stages,
                "| class:", out.classification,
                "| sev:", out.severity.severity,
            )


if __name__ == "__main__":
    asyncio.run(main())
