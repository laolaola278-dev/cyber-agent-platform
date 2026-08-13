"""Extensible Tool Adapter factory."""

from collections.abc import Callable

from app.sdk.tool_adapter import BaseToolAdapter
from app.tool_manager.manifest import ToolManifest

ToolBuilder = Callable[[ToolManifest], BaseToolAdapter]


class ToolFactory:
    """Create adapters by manifest identifier without leaking implementations."""

    def __init__(self) -> None:
        self._builders: dict[str, ToolBuilder] = {}

    def register(self, adapter: str, builder: ToolBuilder) -> None:
        self._builders[adapter] = builder

    def create(self, manifest: ToolManifest) -> BaseToolAdapter:
        try:
            builder = self._builders[manifest.adapter]
        except KeyError as error:
            raise LookupError(f"Unsupported Tool Adapter {manifest.adapter}") from error
        return builder(manifest)

    @classmethod
    def with_platform_defaults(cls) -> "ToolFactory":
        factory = cls()

        def build_playwright(manifest: ToolManifest) -> BaseToolAdapter:
            from app.tools.playwright.adapter import PlaywrightAdapter
            from app.tools.playwright.browser import BrowserManager

            return PlaywrightAdapter(BrowserManager(), manifest.config)

        factory.register("playwright", build_playwright)
        return factory
