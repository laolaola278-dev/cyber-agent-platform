"""Phase 28 -- Adaptive Data Acquisition Agent package."""

from app.acquisition.agent import AdaptiveDataAcquisitionAgent, AgentConfig
from app.acquisition.completeness import CompletenessEvaluator, CompletenessInput
from app.acquisition.dedup import DuplicateRegistry, canonicalize_url, content_sha256
from app.acquisition.documentadapter import DocumentAdapter
from app.acquisition.httpadapter import HTTPAdapter, RestrictedAccessError
from app.acquisition.planner import AcquisitionPlanner, PlannerRequest
from app.acquisition.robots import RobotsPolicy
from app.acquisition.store import LocalFilesystemEvidenceStore
from app.acquisition.urlpolicy import URLPolicyValidator

__all__ = [
    "AdaptiveDataAcquisitionAgent",
    "AgentConfig",
    "CompletenessEvaluator",
    "CompletenessInput",
    "DuplicateRegistry",
    "canonicalize_url",
    "content_sha256",
    "DocumentAdapter",
    "HTTPAdapter",
    "RestrictedAccessError",
    "AcquisitionPlanner",
    "PlannerRequest",
    "RobotsPolicy",
    "LocalFilesystemEvidenceStore",
    "URLPolicyValidator",
]
