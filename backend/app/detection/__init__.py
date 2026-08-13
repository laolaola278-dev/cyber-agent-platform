"""Detection Framework public interfaces."""

from app.detection.contracts import DetectionPlugin, DetectionPluginContext, DetectionRecord
from app.detection.correlation import RuleBasedCorrelationEngine
from app.detection.fake_plugin import FakeDetectionPlugin
from app.detection.normalizer import DetectionResultNormalizer
from app.detection.planner import DetectionPlanner
from app.detection.registry import DetectionRegistry
from app.detection.runtime import DetectionRuntime
from app.detection.service import DetectionService

__all__ = [
    "DetectionPlanner",
    "DetectionPlugin",
    "DetectionPluginContext",
    "DetectionRecord",
    "DetectionRegistry",
    "DetectionResultNormalizer",
    "DetectionRuntime",
    "DetectionService",
    "FakeDetectionPlugin",
    "RuleBasedCorrelationEngine",
]
