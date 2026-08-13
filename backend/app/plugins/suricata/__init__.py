"""Suricata Detection Plugin exports."""

from app.plugins.suricata.normalizer import SuricataResultNormalizer
from app.plugins.suricata.plugin import SuricataDetectionPlugin

__all__ = ["SuricataDetectionPlugin", "SuricataResultNormalizer"]
