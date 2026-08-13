"""Ablation smoke: rules-only vs retrieval+rules."""
import asyncio
import sys

sys.path.insert(0, ".")

from app.agent.evaluation2 import build_scenarios_v2
from app.hybrid.engine import HybridEngine, HybridEngineConfig
from app.hybrid.evaluation3 import HybridEvaluationHarness
from app.hybrid.retrieval import MemoryKnowledgeRetriever


async def main() -> None:
    scenarios = build_scenarios_v2()
    knowledge = MemoryKnowledgeRetriever(
        entries=[
            {"knowledge_type": "ATT&CK", "external_id": "T1566", "title": "Phishing", "keywords": ["phish", "mail"]},
            {"knowledge_type": "ATT&CK", "external_id": "T1059", "title": "Command", "keywords": ["command", "powershell"]},
            {"knowledge_type": "ATT&CK", "external_id": "T1003", "title": "Credential", "keywords": ["credential"]},
            {"knowledge_type": "ATT&CK", "external_id": "T1021", "title": "Remote", "keywords": ["lateral", "remote"]},
        ]
    )
    for name, config, use_knowledge in [
        ("rules_only", HybridEngineConfig(use_llm=False, use_retrieval=False), False),
        ("retrieval_rules", HybridEngineConfig(use_llm=False, use_retrieval=True), True),
    ]:
        engine = HybridEngine(
            knowledge=knowledge if use_knowledge else None,
            llm_ranker=None,
            config=config,
        )
        metrics = await HybridEvaluationHarness(engine=engine, name=name).run(scenarios)
        d = metrics.to_dict()
        print(
            name,
            "| triage:", d["triage_accuracy"],
            "| sev:", d["severity_accuracy"],
            "| fp:", d["false_positive_accuracy"],
            "| attck:", d["attck_precision"], d["attck_recall"],
            "| grounding:", d["evidence_grounding"],
            "| chain:", d["attack_chain_stage_accuracy"],
            "| inj:", d["injection_resistance"],
            "| completion:", d["completion_rate"],
        )


if __name__ == "__main__":
    asyncio.run(main())
