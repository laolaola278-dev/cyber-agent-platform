"""Dynamic registries for knowledge types, relations, providers, and importers."""

from collections.abc import Iterable
from typing import Any, Protocol

from app.core.enums import KnowledgeRelationType, KnowledgeType
from app.exceptions import KnowledgeValidationError
from app.knowledge.providers import KnowledgeProvider


class ImporterPlugin(Protocol):
    format_name: str

    def parse(self, payload: Any, *, source: str) -> list[Any]: ...


class KnowledgeRegistry:
    """Runtime extension boundary; plugins register without core source changes."""

    def __init__(
        self,
        *,
        knowledge_types: Iterable[str] | None = None,
        relation_types: Iterable[str] | None = None,
    ) -> None:
        self._knowledge_types = {
            value.upper() for value in (knowledge_types or (item.value for item in KnowledgeType))
        }
        self._relation_types = {
            value.casefold()
            for value in (relation_types or (item.value for item in KnowledgeRelationType))
        }
        self._providers: dict[str, KnowledgeProvider] = {}
        self._importers: dict[str, ImporterPlugin] = {}

    def register_type(self, value: str) -> None:
        self._knowledge_types.add(value.strip().upper())

    def register_relation(self, value: str) -> None:
        self._relation_types.add(value.strip().casefold())

    def register_provider(self, provider: KnowledgeProvider) -> None:
        self._providers[provider.name.casefold()] = provider

    def register_importer(self, importer: ImporterPlugin) -> None:
        self._importers[importer.format_name.casefold()] = importer

    def require_type(self, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in self._knowledge_types:
            raise KnowledgeValidationError(f"Unsupported knowledge type: {value}")
        return normalized

    def require_relation(self, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in self._relation_types:
            raise KnowledgeValidationError(f"Unsupported knowledge relation: {value}")
        return normalized

    def require_provider(self, name: str) -> KnowledgeProvider:
        provider = self._providers.get(name.casefold())
        if provider is None:
            raise KnowledgeValidationError(f"Unknown knowledge provider: {name}")
        return provider

    def require_importer(self, format_name: str) -> ImporterPlugin:
        importer = self._importers.get(format_name.casefold())
        if importer is None:
            raise KnowledgeValidationError(f"Unsupported import format: {format_name}")
        return importer

    @property
    def knowledge_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._knowledge_types))
