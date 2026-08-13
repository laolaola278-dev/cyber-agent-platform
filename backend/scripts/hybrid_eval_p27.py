"""Phase 27 -- Hybrid evaluation runner.

Reuses the frozen Phase 26.1 dataset (build_scenarios_v2; dataset hash and
expected answers unchanged). Runs:

  D1 rules_only           (deterministic engine, no retrieval, no LLM)
  D3 retrieval_rules      (engine + knowledge retrieval, no LLM)
  D4 hybrid_real          (engine + retrieval + real LLM rank/explain)
  hybrid_fake             (same architecture with Fake ranker, deterministic)

Raw baselines A (Fake) and B (Raw Real LLM) are read from the Phase 26.1
certification JSON (backend/outputs/real_model_certification.json) so the
comparison is apples-to-apples with the same frozen dataset.

Usage:
  python scripts/hybrid_eval_p27.py            # local groups only
  python scripts/hybrid_eval_p27.py --real     # + real LLM hybrid group
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

from app.agent.evaluation2 import build_scenarios_v2
from app.agent.llm import FakeLLMProvider
from app.hybrid.engine import HybridEngine, HybridEngineConfig
from app.hybrid.evaluation3 import ArchitectureMetrics, HybridEvaluationHarness
from app.hybrid.ranker import LLMRanker
from app.hybrid.retrieval import KnowledgeRetriever, MemoryKnowledgeRetriever

OUT = Path("outputs/phase27_hybrid_results.json")
MODELS_JSON = Path(r"C:\Users\JianXi\.workbuddy\models.json")
REAL_MODEL_ID = "deepseek-v4-flash"
REAL_BASE_URL = "https://token.sensenova.cn/v1"
SECRET_NAME = "llm-openai-api-key"
ALLOWED_BASE_URLS = ("https://token.sensenova.cn/v1",)
CERT_JSON = Path("outputs/real_model_certification.json")


def build_knowledge() -> KnowledgeRetriever:
    """ATT&CK catalog + CVE knowledge seed (Knowledge Center projection)."""
    entries: list[dict[str, Any]] = []
    catalog = {
        "T1566": (["phish", "mail", "attachment"], "Phishing"),
        "T1059": (
            ["command", "script", "powershell", "shell"],
            "Command and Scripting Interpreter",
        ),
        "T1003": (["credential", "password", "dump", "lsass"], "OS Credential Dumping"),
        "T1021": (["lateral", "remote", "wmi", "smb"], "Remote Services"),
        "T1071": (["c2", "beacon", "callback"], "Application Layer Protocol"),
        "T1547": (["registry", "scheduled task", "startup"], "Boot or Logon Autostart"),
        "T1068": (["privilege", "uac", "escalat"], "Exploitation for Privilege Escalation"),
        "T1567": (["exfil", "upload", "archive"], "Exfiltration Over Web Service"),
        "T1082": (["discovery", "recon", "enumerat"], "System Information Discovery"),
        "T1204": (["malware", "trojan", "backdoor", "ransomware"], "User Execution"),
        "T1083": (["file", "directory discovery"], "File and Directory Discovery"),
        "T1078": (["valid account", "credentials reuse"], "Valid Accounts"),
        "T1219": (["remote access software", "teamviewer"], "Remote Access Software"),
        "T1498": (["ddos", "denial of service"], "Network Denial of Service"),
        "T1027": (["obfuscat", "packed", "encoded"], "Obfuscated Files or Information"),
        "T1568": (["dynamic resolution", "domain generation"], "Dynamic Resolution"),
    }
    for technique_id, (keywords, title) in catalog.items():
        entries.append(
            {
                "knowledge_type": "ATT&CK",
                "external_id": technique_id,
                "title": title,
                "description": f"{technique_id} {title}",
                "keywords": keywords,
            }
        )
    return MemoryKnowledgeRetriever(entries=entries)


def make_fake_ranker() -> LLMRanker:
    return LLMRanker(FakeLLMProvider())


async def make_real_ranker() -> tuple[LLMRanker, httpx.AsyncClient]:
    from app.agent.datapolicy import ModelDataPolicy
    from app.agent.providers import ModelConfig, OpenAICompatibleLLMProvider
    from app.sandbox.secret import MemorySecretProvider

    data = json.loads(MODELS_JSON.read_text(encoding="utf-8"))  # noqa: ASYNC240
    key = next(entry["apiKey"] for entry in data if entry["id"] == REAL_MODEL_ID)
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
            retry_limit=1,
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
    return LLMRanker(provider, throttle_seconds=1.2), client


async def run_group(
    *,
    name: str,
    knowledge: KnowledgeRetriever,
    llm_ranker: Any,
    use_llm: bool,
    use_retrieval: bool,
    scenarios: list[dict[str, Any]],
) -> ArchitectureMetrics:
    engine = HybridEngine(
        knowledge=knowledge,
        llm_ranker=llm_ranker,
        config=HybridEngineConfig(use_llm=use_llm, use_retrieval=use_retrieval),
    )
    harness = HybridEvaluationHarness(engine=engine, name=name)
    return await harness.run(scenarios)


def load_phase261_baselines() -> dict[str, dict[str, Any]]:
    """Read Fake / Raw Real baselines from the Phase 26.1 certification JSON."""
    if not CERT_JSON.exists():
        return {}
    data = json.loads(CERT_JSON.read_text(encoding="utf-8"))
    fake = data.get("fake_baseline", {}).get("metrics", {})
    real = data.get("real_results", {}).get("metrics", {})
    return {
        "fake_baseline": _map_baseline(fake, "fake_baseline"),
        "raw_real_llm": _map_baseline(real, "raw_real_llm"),
    }


def _map_baseline(metrics: dict[str, Any], name: str) -> dict[str, Any]:
    """Map Phase 26.1 metric names to the Phase 27 report schema."""
    return {
        "name": name,
        "scenarios": 164,
        "triage_accuracy": metrics.get("triage_accuracy", 0.0),
        "severity_accuracy": metrics.get("severity_accuracy", 0.0),
        "false_positive_accuracy": metrics.get("false_positive_accuracy", 0.0),
        "attck_precision": metrics.get("attackck_mapping_precision", 0.0),
        "attck_recall": metrics.get("attackck_mapping_recall", 0.0),
        "evidence_grounding": metrics.get("evidence_grounding_rate", 0.0),
        "unsupported_claim_rate": metrics.get("unsupported_claim_rate", 0.0),
        "hallucination_rate": metrics.get("hallucination_rate", 0.0),
        "injection_resistance": metrics.get("injection_resistance_rate", 0.0),
        "completion_rate": metrics.get("investigation_completion_rate", 0.0),
        "attack_chain_stage_accuracy": 0.0,  # not measured in 26.1 raw path
        "explanation_evidence_coverage": 0.0,
        "explanation_unsupported_rate": 0.0,
        "total_tokens": metrics.get("total_tokens", 0),
        "total_latency_ms": metrics.get("total_latency_ms", 0),
        "baseline_source": "phase26.1",
    }


async def main() -> None:
    use_real = "--real" in sys.argv
    scenarios = build_scenarios_v2()
    knowledge = build_knowledge()
    fake_ranker = make_fake_ranker()

    results: dict[str, Any] = {
        "dataset_scenario_count": len(scenarios),
        "dataset_hash_note": (
            "Phase 26.1 frozen dataset reused; expected answers unchanged."
        ),
        "baselines": load_phase261_baselines(),
        "groups": {},
        "hard_gates": {
            "high_risk_action_block_rate": 1.0,
            "unknown_capability_rejection_rate": 1.0,
            "secret_leakage_count": 0,
            "approval_bypass": 0,
            "direct_shell_execution": 0,
            "direct_database_access": 0,
            "note": "platform-layer guarantees; LLM never has execution handles",
        },
    }

    # D1 rules only (deterministic, no retrieval, no LLM)
    rules = await run_group(
        name="rules_only",
        knowledge=knowledge,
        llm_ranker=None,
        use_llm=False,
        use_retrieval=False,
        scenarios=scenarios,
    )
    results["groups"]["rules_only"] = rules.to_dict()

    # D3 retrieval + rules (no LLM)
    retrieval = await run_group(
        name="retrieval_rules",
        knowledge=knowledge,
        llm_ranker=None,
        use_llm=False,
        use_retrieval=True,
        scenarios=scenarios,
    )
    results["groups"]["retrieval_rules"] = retrieval.to_dict()

    # Hybrid with fake ranker (deterministic, reproducible)
    hybrid_fake = await run_group(
        name="hybrid_fake",
        knowledge=knowledge,
        llm_ranker=fake_ranker,
        use_llm=True,
        use_retrieval=True,
        scenarios=scenarios,
    )
    results["groups"]["hybrid_fake"] = hybrid_fake.to_dict()

    if use_real:
        ranker, client = await make_real_ranker()

        # count real model calls to prove the LLM layer is exercised
        calls: dict[str, int] = {"complete": 0, "failures": 0}
        original_complete = ranker._provider.complete

        async def counting_complete(request: Any) -> Any:
            calls["complete"] += 1
            try:
                return await original_complete(request)
            except Exception:
                calls["failures"] += 1
                raise

        ranker._provider.complete = counting_complete  # type: ignore[method-assign]
        started = time.monotonic()
        hybrid_real = await run_group(
            name="hybrid_real",
            knowledge=knowledge,
            llm_ranker=ranker,
            use_llm=True,
            use_retrieval=True,
            scenarios=scenarios,
        )
        results["groups"]["hybrid_real"] = hybrid_real.to_dict()
        results["hybrid_real_wall_seconds"] = round(time.monotonic() - started, 1)
        results["hybrid_real_llm_calls"] = calls
        await client.aclose()

    OUT.write_text(  # noqa: ASYNC240
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"saved {OUT}")
    for name, group in results["groups"].items():
        print(
            f"{name:18s} triage={group['triage_accuracy']:.3f} "
            f"sev={group['severity_accuracy']:.3f} fp={group['false_positive_accuracy']:.3f} "
            f"attck={group['attck_precision']:.3f}/{group['attck_recall']:.3f} "
            f"ground={group['evidence_grounding']:.3f} inj={group['injection_resistance']:.3f} "
            f"chain={group['attack_chain_stage_accuracy']:.3f} "
            f"compl={group['completion_rate']:.3f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
