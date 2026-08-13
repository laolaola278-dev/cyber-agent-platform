"""Smoke test Phase 27 hybrid engine on frozen Phase 26.1 dataset."""
import asyncio
import json
import sys

sys.path.insert(0, ".")

from app.agent.evaluation2 import build_scenarios_v2
from app.hybrid.engine import HybridEngine, HybridEngineConfig
from app.hybrid.evaluation3 import HybridEvaluationHarness
from app.hybrid.retrieval import MemoryKnowledgeRetriever


async def main() -> None:
    scenarios = build_scenarios_v2()
    print("scenarios:", len(scenarios))
    knowledge = MemoryKnowledgeRetriever(
        entries=[
            {"knowledge_type": "ATT&CK", "external_id": "T1566", "title": "Phishing", "keywords": ["phish", "mail"]},
            {"knowledge_type": "ATT&CK", "external_id": "T1059", "title": "Command and Scripting", "keywords": ["command", "script", "powershell"]},
            {"knowledge_type": "ATT&CK", "external_id": "T1003", "title": "Credential Dumping", "keywords": ["credential", "password"]},
            {"knowledge_type": "ATT&CK", "external_id": "T1021", "title": "Remote Services", "keywords": ["lateral", "remote"]},
            {"knowledge_type": "CVE", "external_id": "CVE-2024-1234", "title": "Test CVE", "keywords": ["cve-2024-1234"]},
        ]
    )
    engine = HybridEngine(
        knowledge=knowledge,
        llm_ranker=None,
        config=HybridEngineConfig(use_llm=False, use_retrieval=True),
    )
    harness = HybridEvaluationHarness(engine=engine, name="retrieval_rules")
    metrics = await harness.run(scenarios)
    print(json.dumps(metrics.to_dict(), indent=1))


if __name__ == "__main__":
    asyncio.run(main())
