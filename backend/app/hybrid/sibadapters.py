"""CAP-SIB adapters: bridge SIB scenarios to HybridEngine / LLM predictions.

Each adapter builds engine input from a SIB scenario and maps the engine
output to the SIBPrediction schema used by the harness.

Track B inputs never contain an ATT&CK id; only Track A may pass rule
metadata (realistic for detection products).
"""

from __future__ import annotations

from typing import Any

from app.hybrid.engine import HybridEngine, HybridEngineConfig
from app.hybrid.retrieval import KnowledgeRetriever
from app.hybrid.sibharness import Scorer, SIBPrediction


def _scenario_to_engine_input(
    scenario: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Map a SIB scenario to (source, context, events) for HybridEngine."""
    scenario_input = scenario["input"]
    events = list(scenario_input.get("events") or [])
    evidence_refs = [e.get("evidence_refs", [])[0] for e in events if e.get("evidence_refs")]

    source: dict[str, Any] = {
        "id": scenario["scenario_id"],
        "title": scenario_input.get("title", ""),
        "status": "OPEN",
        "entities": events[0].get("entities", []) if events else [],
        "evidence_refs": evidence_refs,
        "techniques": [],
    }
    # Track A only: detection rule metadata may carry an ATT&CK id.
    rule_metadata = scenario_input.get("rule_metadata")
    if isinstance(rule_metadata, dict) and rule_metadata.get("attck"):
        source["techniques"] = [str(rule_metadata["attck"])]

    context = dict(scenario_input.get("context") or {})
    return source, context, events


def engine_scorer(
    engine: HybridEngine,
) -> Scorer:
    """Adapter: HybridEngine (any config) -> SIBPrediction."""

    async def _run(scenario: dict[str, Any]) -> SIBPrediction:
        source, context, events = _scenario_to_engine_input(scenario)
        output = await engine.triage(
            source=source,
            context=context,
            events=events,
        )
        prediction = SIBPrediction(
            classification=output.classification,
            severity=output.severity.severity,
            false_positive_probability=output.false_positive.false_positive_probability,
            false_positive=output.false_positive.likely_false_positive,
            techniques=list(output.technique_mapping.mapped_techniques),
            technique_scores={c.technique_id: c.score for c in output.technique_mapping.candidates},
            chain_stages=[s.removeprefix("technique:") for s in output.chain_stages],
            entity_links=[],
            grounded=any(
                c.status in ("SUPPORTED", "PARTIALLY_SUPPORTED") for c in output.grounded_claims
            ),
            explanations=[output.explanation.statement],
            knowledge_refs=[r.external_id for r in output.knowledge_hits],
            evidence_refs=list(output.explanation.evidence_refs),
            factors=list(output.explanation.factors),
            completed=True,
        )
        return prediction

    def scorer(scenario: dict[str, Any]) -> SIBPrediction:
        import asyncio

        return asyncio.get_event_loop().run_until_complete(_run(scenario))

    return scorer


def make_engine_scorer(
    *,
    knowledge: KnowledgeRetriever | None,
    llm_ranker: Any | None,
    use_llm: bool,
    use_retrieval: bool,
) -> Scorer:
    """Build a scorer for one architecture config."""
    engine = HybridEngine(
        knowledge=knowledge,
        llm_ranker=llm_ranker,
        config=HybridEngineConfig(use_llm=use_llm, use_retrieval=use_retrieval),
    )
    return engine_scorer(engine)


def llm_only_scorer(llm_agent_factory: Any) -> Scorer:
    """Adapter: raw LLM triage (Phase 26.1 style) -> SIBPrediction.

    ``llm_agent_factory`` returns an object with an async ``triage`` method
    returning TriageResult-compatible output. Used for the LLM-only baseline.
    """

    async def _run(scenario: dict[str, Any]) -> SIBPrediction:
        source, context, events = _scenario_to_engine_input(scenario)
        agent = llm_agent_factory()
        try:
            output = await agent.triage(source=source, context=context)
        except Exception:  # noqa: BLE001 -- fail closed
            return SIBPrediction(classification="UNKNOWN", completed=False)
        result = getattr(output, "result", output)
        prediction = SIBPrediction(
            classification=getattr(result, "classification", "UNKNOWN"),
            severity=getattr(result, "severity_assessment", "UNKNOWN"),
            false_positive_probability=0.0,
            false_positive=getattr(result, "likely_false_positive", False),
            techniques=list(getattr(result, "techniques", []) or []),
            chain_stages=[],
            grounded=getattr(output, "evidence_grounded", False),
            completed=True,
        )
        return prediction

    def scorer(scenario: dict[str, Any]) -> SIBPrediction:
        import asyncio

        return asyncio.get_event_loop().run_until_complete(_run(scenario))

    return scorer
