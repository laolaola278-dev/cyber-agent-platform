"""Zeek Detection Plugin public interfaces."""

from app.plugins.zeek.normalizer import ZeekResultNormalizer
from app.plugins.zeek.plugin import ZeekDetectionPlugin

__all__ = ["ZeekDetectionPlugin", "ZeekResultNormalizer"]
