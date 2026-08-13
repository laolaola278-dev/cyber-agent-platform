"""CAP-SIB v1 evaluation runner.

Runs the frozen CAP-SIB dataset through four architectures:

  rules_only            (deterministic engine, no retrieval, no LLM)
  retrieval_rules       (engine + knowledge retrieval, no LLM)
  hybrid_fake           (engine + retrieval + Fake ranker)
  hybrid_real           (engine + retrieval + Real LLM ranker)

Dev set is used for diagnostics; HOLD-OUT is scored ONE-SHOT (frozen before
any tuning -- we do not modify rules/answers after seeing holdout).

Usage:
  python scripts/sib_eval.py                 # local groups (rules/retrieval/fake)
  python scripts/sib_eval.py --real          # + real LLM group
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

from app.hybrid.retrieval import KnowledgeRetriever, MemoryKnowledgeRetriever
from app.hybrid.sib import build_sib_v1, freeze_sib, label_leakage_audit, sib_stats
from app.hybrid.sibadapters import make_engine_scorer
from app.hybrid.sibharness import SIBHarness, llm_lift, retrieval_lift

OUT = Path("outputs/cap_sib_v1_results.json")
MODELS_JSON = Path(r"C:\Users\JianXi\.workbuddy\models.json")
REAL_MODEL_ID = "deepseek-v4-flash"
REAL_BASE_URL = "https://token.sensenova.cn/v1"
SECRET_NAME = "llm-openai-api-key"
ALLOWED_BASE_URLS = ("https://token.sensenova.cn/v1",)


def build_knowledge() -> KnowledgeRetriever:
    """Knowledge Center projection: ATT&CK catalog with behavior keywords.

    In production the Knowledge Center stores technique entries with
    behavior/indicator keywords so the retriever can map observed behavior to
    candidate techniques. This projection mirrors that.
    """
    from app.hybrid.attck import _KEYWORD_TECHNIQUES, TECHNIQUE_CATALOG

    keyword_map: dict[str, list[str]] = {}
    for keywords, technique_id in _KEYWORD_TECHNIQUES:
        keyword_map.setdefault(technique_id, []).extend(keywords)

    extra_vocabulary: dict[str, list[str]] = {
        "T1566": ["phishing", "attachment", "email", "macro", "link"],
        "T1059": ["powershell", "script", "command", "one-liner", "encoded"],
        "T1003": ["credential", "lsass", "dump", "password", "hash"],
        "T1021": ["remote", "admin", "smb", "wmi", "rdp", "server"],
        "T1071": ["c2", "beacon", "heartbeat", "callback", "https"],
        "T1547": ["registry", "run key", "startup", "autostart"],
        "T1068": ["privilege", "escalation", "container", "pod"],
        "T1567": ["exfil", "upload", "archive", "zip", "share"],
        "T1082": ["discovery", "enumerate", "network scan", "recon"],
        "T1204": ["malware", "document", "download", "binary", "rat"],
        "T1078": ["valid account", "logon", "access key", "region"],
        "T1219": ["remote access", "rat", "teamviewer"],
        "T1498": ["ddos", "denial", "flood"],
        "T1027": ["obfuscated", "encoded", "packed", "encoded script"],
        "T1568": ["dynamic domain", "low ttl", "dns"],
        "T1053": ["scheduled task", "cron", "job"],
        "T1136": ["account created", "new account", "access key created"],
        "T1190": ["sql", "exploit", "webshell", "upload", "public app"],
        "T1485": ["destruction", "delete", "wipe"],
        "T1041": ["exfil over c2", "upload", "compressed"],
    }
    entries = []
    for technique_id, title in TECHNIQUE_CATALOG.items():
        keywords = list(keyword_map.get(technique_id, []))
        keywords.extend(extra_vocabulary.get(technique_id, []))
        entries.append(
            {
                "knowledge_type": "ATT&CK",
                "external_id": technique_id,
                "title": title,
                "description": f"{technique_id} {title}",
                "keywords": list(dict.fromkeys(keywords)),
            }
        )
    return MemoryKnowledgeRetriever(entries=entries)


async def make_real_ranker() -> tuple[Any, httpx.AsyncClient]:
    from app.agent.datapolicy import ModelDataPolicy
    from app.agent.providers import ModelConfig, OpenAICompatibleLLMProvider
    from app.hybrid.ranker import LLMRanker
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
    return LLMRanker(provider, throttle_seconds=15.0), client


def main() -> None:
    use_real = "--real" in sys.argv
    # Dev set drives tuning; the HOLD-OUT is scored ONE-SHOT (--holdout).
    # Default: dev only, so we never tune on holdout.
    if "--holdout" in sys.argv:
        _splits = ("holdout",)
    elif "--all" in sys.argv:
        _splits = ("dev", "holdout")
    else:
        _splits = ("dev",)
    dataset = build_sib_v1()
    scenarios = [s.to_dict() for s in dataset]
    dataset_hash = freeze_sib(dataset)

    # leakage audit (must be zero)
    findings = 0
    for scenario in dataset:
        findings += len(label_leakage_audit(scenario))
    if findings:
        print(f"FATAL: label leakage findings={findings}")
        return

    knowledge = build_knowledge()
    results: dict[str, Any] = {
        "dataset": {
            "version": "cap-sib-v1",
            "total": len(scenarios),
            "sha256": dataset_hash,
            "stats": sib_stats(dataset),
        },
        "leakage_findings": findings,
        "groups": {},
        "random_seed": 42,
        "model_config": {
            "prompt_version": "cap-sib-v1-engine-v1",
            "provider": REAL_MODEL_ID if use_real else "deterministic",
        },
    }

    harness_defs = [
        (
            "rules_only",
            make_engine_scorer(knowledge=None, llm_ranker=None, use_llm=False, use_retrieval=False),
        ),
        (
            "retrieval_rules",
            make_engine_scorer(
                knowledge=knowledge, llm_ranker=None, use_llm=False, use_retrieval=True
            ),
        ),
        (
            "hybrid_fake",
            make_engine_scorer(
                knowledge=knowledge,
                llm_ranker=__import__(  # noqa: E501
                    "app.hybrid.ranker", fromlist=["LLMRanker"]
                ).LLMRanker(
                    __import__("app.agent.llm", fromlist=["FakeLLMProvider"]).FakeLLMProvider()
                ),
                use_llm=True,
                use_retrieval=True,
            ),
        ),
    ]
    for name, scorer in harness_defs:
        harness = SIBHarness(scorer=scorer, name=name)
        reports = harness.run(scenarios, splits=_splits)
        results["groups"][name] = {k: v.to_dict() for k, v in reports.items()}

    if use_real:
        loop = asyncio.new_event_loop()
        ranker, client = loop.run_until_complete(make_real_ranker())

        calls = {"complete": 0, "success": 0, "failures": 0}
        original_complete = ranker._provider.complete

        async def counting_complete(request: Any) -> Any:
            calls["complete"] += 1
            try:
                response = await original_complete(request)
                calls["success"] += 1
                return response
            except Exception:
                calls["failures"] += 1
                raise

        ranker._provider.complete = counting_complete  # type: ignore[method-assign]
        started = time.monotonic()
        scorer = make_engine_scorer(
            knowledge=knowledge, llm_ranker=ranker, use_llm=True, use_retrieval=True
        )

        # collect predictions for explainability evaluation
        collected: list[Any] = []
        original_scorer = scorer

        def collecting_scorer(scenario: dict[str, Any]) -> Any:
            prediction = original_scorer(scenario)
            collected.append(prediction)
            return prediction

        harness = SIBHarness(scorer=collecting_scorer, name="hybrid_real")
        reports = harness.run(scenarios, splits=_splits)
        results["groups"]["hybrid_real"] = {k: v.to_dict() for k, v in reports.items()}
        from app.hybrid.sibharness import explainability_report

        results["hybrid_real_explainability"] = explainability_report(collected)
        results["hybrid_real_wall_seconds"] = round(time.monotonic() - started, 1)
        results["hybrid_real_llm_calls"] = calls
        success_rate = calls["success"] / max(calls["complete"], 1)
        results["hybrid_real_success_rate"] = round(success_rate, 4)
        results["hybrid_real_verdict"] = (
            "SUCCESS_RATE_OK" if success_rate >= 0.95 else "REAL_LLM_BENCHMARK_BLOCKED"
        )
        loop.run_until_complete(client.aclose())
        loop.close()

    # lifts on the primary track (holdout_B for certification, dev_B for tuning)
    lift_keys = (f"{_splits[0]}_B",)
    if "hybrid_fake" in results["groups"]:
        results["retrieval_lift"] = retrieval_lift(
            {k: _track(v) for k, v in results["groups"]["rules_only"].items()},
            {k: _track(v) for k, v in results["groups"]["retrieval_rules"].items()},
            keys=lift_keys,
        )
        results["llm_lift_fake"] = llm_lift(
            {k: _track(v) for k, v in results["groups"]["retrieval_rules"].items()},
            {k: _track(v) for k, v in results["groups"]["hybrid_fake"].items()},
            keys=lift_keys,
        )
    if "hybrid_real" in results["groups"]:
        results["llm_lift_real"] = llm_lift(
            {k: _track(v) for k, v in results["groups"]["retrieval_rules"].items()},
            {k: _track(v) for k, v in results["groups"]["hybrid_real"].items()},
            keys=lift_keys,
        )

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {OUT}")
    print(f"dataset hash: {dataset_hash} | splits: {_splits}")
    for group, reports in results["groups"].items():
        key = "holdout_B"
        if key in reports:
            r = reports[key]
            print(
                f"{group:16s} holdout_B: cls={r['classification_accuracy']:.3f} "
                f"sev={r['severity_exact']:.3f} fp_f1={r['fp_f1']:.3f} "
                f"attck={r['attck_f1']:.3f} chain={r['stage_accuracy']:.3f} "
                f"ground={r['evidence_grounding']:.3f} hn={r['hard_negative_accuracy']:.3f}"
            )
    if use_real:
        print("real llm calls:", results.get("hybrid_real_llm_calls"))
        print("verdict:", results.get("hybrid_real_verdict"))


def _track(data: dict[str, Any]):
    from types import SimpleNamespace

    return SimpleNamespace(**data)


if __name__ == "__main__":
    main()
