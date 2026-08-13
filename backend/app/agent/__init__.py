"""Agentic engine package (v2.0 / Phase 25).

The Agent is a *decision layer only*. Execution of every real capability is
delegated to the platform Capability -> Policy -> Worker/Sandbox -> Plugin ->
Evidence -> Audit pipeline. This package adds planning, guardrails, session
memory, handoffs, evaluation and observability on top of that pipeline.
"""

from app.agent.agent import INVESTIGATION_AGENT_PROFILE, InvestigationAgent
from app.agent.contracts import (
    AgentObservation,
    AgentProfile,
    InvestigationConclusion,
    InvestigationPlan,
    InvestigationSessionMemory,
)
from app.agent.evaluation import (
    AgentEvaluationHarness,
    EvaluationMetric,
    EvaluationReport,
    Scenario,
    ScenarioResult,
    build_scenarios,
)
from app.agent.executor import CapabilityResult, ReadOnlyCapabilityExecutor
from app.agent.guardrails import (
    CapabilityGuardrail,
    GuardrailDecision,
    InputGuardrail,
    OutputGuardrail,
    PlanGuardrail,
)
from app.agent.handoff import HandoffManager
from app.agent.injection import (
    InjectionAnalysis,
    IsolationResult,
    analyze_dangerous_commands,
    analyze_injection,
    analyze_secret_exposure,
    isolate_untrusted_data,
)
from app.agent.llm import FakeLLMProvider, LLMProvider, ModelCapability, ModelRequest, ModelResponse
from app.agent.loop import AgentLoop, AgentLoopBudget, AgentLoopResult
from app.agent.observability import AgentObservability, AgentRunRecord
from app.agent.planner import AgenticPlanner

__all__ = [
    "INVESTIGATION_AGENT_PROFILE",
    "AgentEvaluationHarness",
    "AgentLoop",
    "AgentLoopBudget",
    "AgentLoopResult",
    "AgentObservation",
    "AgentObservability",
    "AgentProfile",
    "AgentRunRecord",
    "AgenticPlanner",
    "CapabilityGuardrail",
    "CapabilityResult",
    "EvaluationMetric",
    "EvaluationReport",
    "FakeLLMProvider",
    "GuardrailDecision",
    "HandoffManager",
    "InjectionAnalysis",
    "InputGuardrail",
    "InvestigationAgent",
    "InvestigationConclusion",
    "InvestigationPlan",
    "InvestigationSessionMemory",
    "IsolationResult",
    "LLMProvider",
    "ModelCapability",
    "ModelRequest",
    "ModelResponse",
    "OutputGuardrail",
    "PlanGuardrail",
    "ReadOnlyCapabilityExecutor",
    "Scenario",
    "ScenarioResult",
    "analyze_dangerous_commands",
    "analyze_injection",
    "analyze_secret_exposure",
    "build_scenarios",
    "isolate_untrusted_data",
]
