"""OWASP ZAP Assessment Plugin exports."""

from app.plugins.zap.normalizer import ZapResultNormalizer
from app.plugins.zap.plugin import ZapAssessmentPlugin

__all__ = ["ZapAssessmentPlugin", "ZapResultNormalizer"]
