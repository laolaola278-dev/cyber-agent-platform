"""Security Assessment Framework public API."""

from app.assessment.contracts import AssessmentPlugin, AssessmentPluginContext
from app.assessment.fake_plugin import FakeAssessmentPlugin
from app.assessment.finding_state import FindingStateMachine
from app.assessment.fingerprint import FingerprintProvider, SHA256FingerprintProvider
from app.assessment.knowledge_mapper import FindingKnowledgeMapper
from app.assessment.normalizer import ResultNormalizer
from app.assessment.planner import AssessmentPlanner
from app.assessment.registry import AssessmentRegistry
from app.assessment.risk import RiskAssessment, RiskEngine, RuleBasedRiskEngine
from app.assessment.runtime import AssessmentRuntime, AssessmentScheduler
from app.assessment.service import AssessmentService

__all__ = [
    "AssessmentPlanner",
    "AssessmentPlugin",
    "AssessmentPluginContext",
    "AssessmentRegistry",
    "AssessmentRuntime",
    "AssessmentScheduler",
    "AssessmentService",
    "FakeAssessmentPlugin",
    "FindingKnowledgeMapper",
    "FindingStateMachine",
    "FingerprintProvider",
    "ResultNormalizer",
    "SHA256FingerprintProvider",
    "RiskAssessment",
    "RiskEngine",
    "RuleBasedRiskEngine",
]
