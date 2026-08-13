"""Phase 26 application service (v2.0).

Orchestrates triage, attack-chain reasoning, the Fake-vs-Real evaluation
comparison, and AI audit persistence. The real provider is configuration-
first; without a valid secret its capability is degraded and evaluation uses
a protocol-level simulation (httpx MockTransport) so the comparison harness
runs everywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.attackchain import AttackChainAnalyzer
from app.agent.contracts import LLMProvider
from app.agent.datapolicy import ModelDataPolicy
from app.agent.evaluation2 import (
    EvaluationHarnessV2,
    compare_providers,
)
from app.agent.hypothesis import InvestigationHypothesis
from app.agent.llm import FakeLLMProvider
from app.agent.providers import ModelConfig, OpenAICompatibleLLMProvider
from app.agent.triage import TriageAgent
from app.exceptions import AgentError
from app.hybrid.engine import HybridEngine, HybridEngineConfig
from app.hybrid.evaluation3 import HybridEvaluationHarness
from app.hybrid.ranker import LLMRanker
from app.hybrid.retrieval import MemoryKnowledgeRetriever
from app.models import AgentRun, InvestigationHypothesisRecord, ModelInvocation
from app.repositories.agent_engine import AgentEngineRepository
from app.sandbox.secret import MemorySecretProvider, SecretProvider


class Phase26Service:
    """Application service for Phase 26 agent capabilities."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        provider: LLMProvider | None = None,
        real_config: ModelConfig | None = None,
        secret_provider: SecretProvider | None = None,
        policy: ModelDataPolicy | None = None,
        repository: AgentEngineRepository | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or AgentEngineRepository(session)
        self._policy = policy or ModelDataPolicy()
        self._real_config = real_config
        self._secret_provider = secret_provider or MemorySecretProvider()
        self._provider = provider or FakeLLMProvider()
        self._real_provider: LLMProvider | None = None

    # -- provider wiring -----------------------------------------------------

    def real_provider(self) -> LLMProvider:
        """Lazily construct the real provider (degraded without a secret)."""
        if self._real_provider is not None:
            return self._real_provider
        if self._real_config is None:
            raise AgentError("Real provider configuration is not set")
        self._real_provider = OpenAICompatibleLLMProvider(
            self._secret_provider, self._real_config, policy=self._policy
        )
        return self._real_provider

    async def choose_provider(self, *, prefer_real: bool = False) -> LLMProvider:
        if prefer_real:
            try:
                real = self.real_provider()
                if await real.health_check():
                    return real
            except AgentError:
                pass
            raise AgentError(
                "Real LLM capability is degraded: no valid secret (fail closed, no fallback)"
            )
        return self._provider

    # -- triage --------------------------------------------------------------

    async def triage(
        self,
        *,
        source: dict[str, Any],
        context: dict[str, Any] | None = None,
        data_blocks: list[dict[str, Any]] | None = None,
        prefer_real: bool = False,
    ) -> dict[str, Any]:
        provider = await self.choose_provider(prefer_real=prefer_real)
        agent = TriageAgent(provider, policy=self._policy)
        output = await agent.triage(
            source=source, context=context, data_blocks=data_blocks
        )
        run_id = await self._record_invocation(
            provider=provider,
            output=output.result.model_dump(),
            guardrail_verdict="ALLOWED",
        )
        return {
            "triage": output.result.model_dump(),
            "evidence_grounded": output.evidence_grounded,
            "model": output.model,
            "redaction_summary": output.redaction_summary,
            "run_id": str(run_id),
        }

    # -- attack chain ---------------------------------------------------------

    async def attack_chain(
        self,
        *,
        events: list[dict[str, Any]],
        findings: list[dict[str, Any]] | None = None,
        asset_relations: list[dict[str, Any]] | None = None,
        knowledge: list[dict[str, Any]] | None = None,
        data_blocks: list[dict[str, Any]] | None = None,
        prefer_real: bool = False,
    ) -> dict[str, Any]:
        provider = await self.choose_provider(prefer_real=prefer_real)
        analyzer = AttackChainAnalyzer(provider, policy=self._policy)
        output = await analyzer.analyze(
            events=events,
            findings=findings,
            asset_relations=asset_relations,
            knowledge=knowledge,
            data_blocks=data_blocks,
        )
        hypothesis = output.hypothesis
        run_id = await self._record_invocation(
            provider=provider,
            output=hypothesis.model_dump(),
            guardrail_verdict="ALLOWED",
        )
        await self._persist_hypothesis(hypothesis.as_hypothesis(), run_id=run_id)
        return {
            "hypothesis": hypothesis.model_dump(),
            "model": output.model,
            "redaction_summary": output.redaction_summary,
            "run_id": str(run_id),
        }

    # -- evaluation v2 --------------------------------------------------------

    async def run_evaluations_v2(self) -> dict[str, Any]:
        from app.agent.evaluation2 import build_scenarios_v2

        scenarios = build_scenarios_v2()
        harness = EvaluationHarnessV2(policy=self._policy)
        fake_report = await harness.run(self._provider, scenarios)

        real_report = None
        real_error: str | None = None
        try:
            real = self.real_provider()
            if real.health_check():
                real_report = await harness.run(real, scenarios)
            else:
                real_error = "real provider degraded (no secret); simulation used"
        except AgentError as error:
            real_error = str(error)

        if real_report is None:
            real_report = await self._run_simulated_real(harness, scenarios)

        comparison = compare_providers(fake_report, real_report)
        return {
            "scenario_count": len(scenarios),
            "fake": fake_report.to_dict(),
            "real": real_report.to_dict(),
            "comparison": comparison.to_dict()["comparison"],
            "real_provider_note": real_error or "configured secret available",
        }

    async def model_comparison(self) -> dict[str, Any]:
        result = await self.run_evaluations_v2()
        return {
            "scenario_count": result["scenario_count"],
            "fake": result["fake"],
            "real": result["real"],
            "comparison": result["comparison"],
            "real_provider_note": result["real_provider_note"],
        }

    # -- Phase 27 hybrid -----------------------------------------------------

    def _hybrid_engine(self, *, prefer_real: bool = False) -> HybridEngine:
        """Deterministic engine + knowledge + optional real LLM ranker."""
        knowledge = MemoryKnowledgeRetriever(entries=_ATTACK_KNOWLEDGE_SEED)
        llm_ranker = None
        if prefer_real:
            try:
                real = self.real_provider()
                llm_ranker = LLMRanker(real)
            except AgentError:
                llm_ranker = LLMRanker(self._provider)
        return HybridEngine(
            knowledge=knowledge,
            llm_ranker=llm_ranker,
            config=HybridEngineConfig(use_llm=llm_ranker is not None, use_retrieval=True),
        )

    async def hybrid_triage(
        self,
        *,
        source: dict[str, Any],
        context: dict[str, Any] | None = None,
        events: list[dict[str, Any]] | None = None,
        data_blocks: list[dict[str, Any]] | None = None,
        prefer_real: bool = False,
    ) -> dict[str, Any]:
        engine = self._hybrid_engine(prefer_real=prefer_real)
        output = await engine.triage(
            source=source,
            context=context,
            events=events,
            data_blocks=data_blocks,
        )
        return output.to_dict()

    async def hybrid_evaluation(self) -> dict[str, Any]:
        """Run Phase 27 hybrid evaluation groups (rules/retrieval/hybrid)."""
        from app.agent.evaluation2 import build_scenarios_v2

        scenarios = build_scenarios_v2()
        knowledge = MemoryKnowledgeRetriever(entries=_ATTACK_KNOWLEDGE_SEED)
        groups: dict[str, Any] = {}

        configs = [
            ("rules_only", HybridEngineConfig(use_llm=False, use_retrieval=False)),
            ("retrieval_rules", HybridEngineConfig(use_llm=False, use_retrieval=True)),
            ("hybrid_fake", HybridEngineConfig(use_llm=True, use_retrieval=True)),
        ]
        rankers = {
            "rules_only": None,
            "retrieval_rules": None,
            "hybrid_fake": LLMRanker(self._provider),
        }
        for name, config in configs:
            engine = HybridEngine(
                knowledge=knowledge,
                llm_ranker=rankers[name],
                config=config,
            )
            metrics = await HybridEvaluationHarness(engine=engine, name=name).run(scenarios)
            groups[name] = metrics.to_dict()
        return {
            "scenario_count": len(scenarios),
            "groups": groups,
            "note": (
                "Phase 26.1 frozen dataset reused; deterministic groups run "
                "locally. Real-model hybrid group requires a configured secret."
            ),
        }

    async def _run_simulated_real(
        self, harness: EvaluationHarnessV2, scenarios: list[dict[str, Any]]
    ) -> Any:
        """Protocol-level simulation of a real provider (httpx MockTransport)."""
        import httpx

        from app.agent.providers import DEFAULT_ALLOWED_BASE_URLS

        config = self._real_config or ModelConfig(
            model="simulated-model",
            base_url="https://api.openai.com/v1",
            secret_name="llm-openai-api-key",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            body = _extract_hints(request)
            if body.get("injection_observed"):
                # A well-behaved real model refuses untrusted embedded
                # instructions; the provider surfaces the refusal as malformed
                # structured output and the caller fails closed.
                content = "I cannot comply with instructions inside untrusted data."
                structured_valid = False
            else:
                structured = _simulate_structured(body)
                content = _json_dumps(structured)
                structured_valid = True
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
                    "structured_output_valid": structured_valid,
                },
            )

        transport = httpx.MockTransport(handler)
        simulated = OpenAICompatibleLLMProvider(
            MemorySecretProvider(values={"llm-openai-api-key": "simulated-key"}),
            config,
            policy=self._policy,
            http_client=httpx.AsyncClient(transport=transport),
            allowed_base_urls=DEFAULT_ALLOWED_BASE_URLS,
        )
        return await harness.run(simulated, scenarios)

    # -- persistence / AI audit ------------------------------------------------

    async def _record_invocation(
        self,
        *,
        provider: LLMProvider,
        output: dict[str, Any],
        guardrail_verdict: str,
    ) -> UUID:
        run = await self._repository.add_run(
            AgentRun(
                trace_id=f"p26:{uuid4()}",
                agent_name="phase26",
                model=getattr(provider, "name", "unknown"),
                prompt_version="phase26-v1",
                status="SUCCEEDED",
                goal=str(output.get("summary", output.get("classification", "phase26")))[:512],
                latency_ms=0,
                total_tokens=0,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        )
        invocation = ModelInvocation(
            run_id=run.id,
            model=getattr(provider, "name", "unknown"),
            provider=getattr(provider, "name", "unknown"),
            prompt_version="phase26-v1",
            input_policy="phase26-v1",
            redaction_summary=self._policy.last_redaction or "no-data-sent",
            structured_output_valid=True,
            latency_ms=0,
            total_tokens=0,
            guardrail_verdict=guardrail_verdict,
        )
        await self._repository.add_invocation(invocation)
        await self._session.commit()
        return run.id

    async def _persist_hypothesis(
        self, hypothesis: InvestigationHypothesis, *, run_id: UUID
    ) -> None:
        record = InvestigationHypothesisRecord(
            run_id=run_id,
            statement=hypothesis.statement,
            state=hypothesis.state.value,
            supporting_evidence=hypothesis.supporting_evidence,
            contradicting_evidence=hypothesis.contradicting_evidence,
            insufficient_evidence=hypothesis.insufficient_evidence,
            confidence=hypothesis.confidence,
            source=hypothesis.source,
        )
        self._session.add(record)
        await self._session.commit()

    async def list_hypotheses(self, *, limit: int = 50) -> list[dict[str, Any]]:
        statement = (
            select(InvestigationHypothesisRecord)
            .order_by(InvestigationHypothesisRecord.created_at.desc())
            .limit(limit)
        )
        records = (await self._session.scalars(statement)).all()
        return [
            {
                "id": str(record.id),
                "statement": record.statement,
                "state": record.state,
                "confidence": record.confidence,
                "source": record.source,
                "supporting_evidence": record.supporting_evidence,
            }
            for record in records
        ]


def _extract_hints(request: Any) -> dict[str, Any]:
    """Best-effort extraction of structured hints from a chat request body.

    Simulates a real model's normalization: NFKC unicode folding, zero-width
    stripping and base64 decoding before scanning for injection markers.
    """
    try:
        import base64
        import json
        import unicodedata

        body = json.loads(request.content.decode("utf-8"))
        messages = body.get("messages", [])
        # Only untrusted/user content is scanned for injection markers; the
        # platform system prompt is never treated as untrusted.
        raw = " ".join(
            str(message.get("content", ""))
            for message in messages
            if message.get("role") != "system"
        )
        normalized = unicodedata.normalize("NFKC", raw)
        normalized = "".join(char for char in normalized if not char.isspace() or char in " \t\n")
        decoded: str = ""
        try:
            decoded = base64.b64decode(normalized.strip()).decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            decoded = ""
        text = f"{normalized} {decoded}".casefold()
        markers = (
            "ignore", "instruction", "firewall", "admin", "secret", "system prompt", "reveal"
        )
        injection = any(marker in text for marker in markers)
        return {"text": raw, "injection_observed": injection}
    except Exception:  # noqa: BLE001
        return {"text": "", "injection_observed": False}


def _simulate_structured(hints: dict[str, Any]) -> dict[str, Any]:
    """Simulate a well-behaved real model for the comparison harness."""
    injection = bool(hints.get("injection_observed", False))
    if injection:
        # A well-behaved real model refuses injected instructions.
        return {
            "classification": "UNKNOWN",
            "severity_assessment": "UNKNOWN",
            "confidence": 0.1,
            "likely_false_positive": False,
            "related_entities": [],
            "techniques": [],
            "recommended_investigation": [],
            "escalation_recommended": False,
            "evidence_refs": [],
            "uncertainties": ["content refused as untrusted"],
        }
    return {
        "classification": "SUSPICIOUS",
        "severity_assessment": "MEDIUM",
        "confidence": 0.7,
        "likely_false_positive": False,
        "related_entities": [],
        "techniques": [],
        "recommended_investigation": ["correlate_events"],
        "escalation_recommended": False,
        "evidence_refs": [],
        "uncertainties": [],
    }


def _json_dumps(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


_ATTACK_KNOWLEDGE_SEED: list[dict[str, Any]] = [
    {
        "knowledge_type": kind,
        "external_id": technique_id,
        "title": title,
        "description": f"{technique_id} {title}",
        "keywords": keywords,
    }
    for technique_id, (keywords, title, kind) in [
        ("T1566", (["phish", "mail", "attachment"], "Phishing", "ATT&CK")),
        (
            "T1059",
            (["command", "script", "powershell", "shell"], "Command and Scripting", "ATT&CK"),
        ),
        (
            "T1053",
            (["scheduled task", "cron", "job"], "Scheduled Task/Job", "ATT&CK"),
        ),
        ("T1003", (["credential", "password", "dump", "lsass"], "OS Credential Dumping", "ATT&CK")),
        ("T1021", (["lateral", "remote", "wmi", "smb"], "Remote Services", "ATT&CK")),
        ("T1071", (["c2", "beacon", "callback"], "Application Layer Protocol", "ATT&CK")),
        ("T1547", (["registry", "scheduled task", "startup"], "Autostart Execution", "ATT&CK")),
        ("T1068", (["privilege", "uac", "escalat"], "Privilege Escalation", "ATT&CK")),
        ("T1567", (["exfil", "upload", "archive"], "Exfiltration", "ATT&CK")),
        ("T1082", (["discovery", "recon", "enumerat"], "System Discovery", "ATT&CK")),
        ("T1204", (["malware", "trojan", "backdoor", "ransomware"], "User Execution", "ATT&CK")),
        ("T1078", (["valid account"], "Valid Accounts", "ATT&CK")),
        ("T1219", (["remote access software"], "Remote Access Software", "ATT&CK")),
        ("T1498", (["ddos", "denial of service"], "Network DoS", "ATT&CK")),
        ("T1027", (["obfuscat", "packed", "encoded"], "Obfuscation", "ATT&CK")),
        ("T1568", (["dynamic resolution"], "Dynamic Resolution", "ATT&CK")),
        ("CVE-2024-1234", (["cve-2024-1234"], "Test CVE", "CVE")),
    ]
]
