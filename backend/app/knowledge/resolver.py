"""Canonical identity and source-aware relationship resolution."""

from app.core.enums import KnowledgeType
from app.knowledge.registry import KnowledgeRegistry


class KnowledgeResolver:
    def __init__(self, registry: KnowledgeRegistry | None = None) -> None:
        self._registry = registry or KnowledgeRegistry()

    def canonical_external_id(self, knowledge_type: str, external_id: str) -> str:
        normalized_type = self._registry.require_type(knowledge_type)
        normalized_id = external_id.strip()
        if normalized_type in {
            KnowledgeType.CVE.value,
            KnowledgeType.CWE.value,
            KnowledgeType.CAPEC.value,
            KnowledgeType.CPE.value,
            KnowledgeType.ATTACK_TECHNIQUE.value,
            KnowledgeType.ATTACK_TACTIC.value,
            KnowledgeType.CISA_KEV.value,
            KnowledgeType.OWASP_CATEGORY.value,
        }:
            return normalized_id.upper()
        return normalized_id.casefold()

    def relation_type(self, value: str) -> str:
        return self._registry.require_relation(value)
