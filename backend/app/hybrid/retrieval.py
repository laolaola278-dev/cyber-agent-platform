"""Phase 27 -- KnowledgeRetriever.

Reuses the platform Knowledge Center (app.knowledge.service) -- no second
knowledge database. Supports CVE / CWE / CAPEC / ATT&CK / KEV / IOC lookups.

PostgreSQL is the primary backend (the platform's existing knowledge tables).
Vector search is reserved behind a provider interface and NOT introduced now.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.hybrid.facts import SecurityFact

SUPPORTED_TYPES = ("CVE", "CWE", "CAPEC", "ATT&CK", "KEV", "IOC")


class KnowledgeSourceError(RuntimeError):
    """Raised when the knowledge backend is unavailable."""


@dataclass
class RetrievedKnowledge:
    """One knowledge hit with a deterministic match score."""

    knowledge_type: str
    external_id: str
    title: str = ""
    description: str = ""
    score: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    evidence_ref: str | None = None


class KnowledgeRetriever(ABC):
    """Abstract retrieval interface.

    The platform currently provides a PostgreSQL-backed implementation. A
    vector provider (e.g. pgvector / qdrant) can be added later by
    implementing this interface -- no changes required upstream.
    """

    @abstractmethod
    async def lookup(
        self,
        *,
        knowledge_type: str,
        external_id: str | None = None,
        query: str | None = None,
        limit: int = 5,
    ) -> list[RetrievedKnowledge]:
        """Retrieve knowledge entries. external_id takes precedence."""

    @abstractmethod
    async def lookup_fact(self, fact: SecurityFact, *, limit: int = 5) -> list[RetrievedKnowledge]:
        """Retrieve knowledge relevant to a SecurityFact."""


class PlatformKnowledgeRetriever(KnowledgeRetriever):
    """PostgreSQL-backed retriever delegating to the platform Knowledge Service."""

    def __init__(self, knowledge_service: Any) -> None:
        self._service = knowledge_service

    async def lookup(
        self,
        *,
        knowledge_type: str,
        external_id: str | None = None,
        query: str | None = None,
        limit: int = 5,
    ) -> list[RetrievedKnowledge]:
        if knowledge_type.upper() not in SUPPORTED_TYPES:
            return []
        try:
            if external_id:
                entry = await self._service.get_by_external_id(knowledge_type.upper(), external_id)
                if entry is None:
                    return []
                return [_to_retrieved(entry, score=1.0)]
            if query:
                page = await self._service.search(
                    query=query, knowledge_type=knowledge_type.upper(), page_size=limit
                )
                items = getattr(page, "items", page) or []
                return [
                    _to_retrieved(item, score=max(0.0, min(1.0, (limit - index) / limit)))
                    for index, item in enumerate(items)
                ]
        except KnowledgeSourceError:
            raise
        except Exception as error:  # noqa: BLE001 -- surface as domain error
            raise KnowledgeSourceError(f"knowledge lookup failed: {error}") from error
        return []

    async def lookup_fact(self, fact: SecurityFact, *, limit: int = 5) -> list[RetrievedKnowledge]:
        if fact.fact_type == "vulnerability" and fact.value.startswith("CVE-"):
            return await self.lookup(knowledge_type="CVE", external_id=fact.value, limit=limit)
        if fact.fact_type == "technique" and fact.value.startswith("T"):
            return await self.lookup(knowledge_type="ATT&CK", external_id=fact.value, limit=limit)
        return await self.lookup(knowledge_type="IOC", query=fact.value, limit=limit)


class MemoryKnowledgeRetriever(KnowledgeRetriever):
    """In-memory retriever for tests and offline evaluation.

    Entries: list of dicts with knowledge_type / external_id / title /
    description / keywords / attributes.
    """

    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self._entries = entries or []

    async def lookup(
        self,
        *,
        knowledge_type: str,
        external_id: str | None = None,
        query: str | None = None,
        limit: int = 5,
    ) -> list[RetrievedKnowledge]:
        if knowledge_type.upper() not in SUPPORTED_TYPES:
            return []
        matches: list[RetrievedKnowledge] = []
        for entry in self._entries:
            if str(entry.get("knowledge_type", "")).upper() != knowledge_type.upper():
                continue
            if external_id and str(entry.get("external_id", "")).lower() == external_id.lower():
                return [_to_retrieved(entry, score=1.0, evidence_ref=entry.get("evidence_ref"))]
            if query:
                # Real retrieval semantics: any knowledge keyword appearing in
                # the query text is a hit (behavior text contains technique
                # vocabulary), rather than the reverse substring test.
                query_lower = query.lower()
                for entry in self._entries:
                    if str(entry.get("knowledge_type", "")).upper() != knowledge_type.upper():
                        continue
                    keywords = entry.get("keywords") or []
                    haystack = " ".join(
                        [
                            str(entry.get("title", "")),
                            str(entry.get("description", "")),
                            " ".join(str(k) for k in keywords),
                        ]
                    ).lower()
                    keyword_hit = any(str(kw).lower() in query_lower for kw in keywords)
                    if keyword_hit or query_lower in haystack:
                        matches.append(
                            _to_retrieved(
                                entry,
                                score=max(0.0, min(1.0, (limit - len(matches)) / limit)),
                                evidence_ref=entry.get("evidence_ref"),
                            )
                        )
                        if len(matches) >= limit:
                            break
        return matches[:limit]

    async def lookup_fact(self, fact: SecurityFact, *, limit: int = 5) -> list[RetrievedKnowledge]:
        if fact.fact_type == "vulnerability" and fact.value.startswith("CVE-"):
            return await self.lookup(knowledge_type="CVE", external_id=fact.value, limit=limit)
        if fact.fact_type == "technique" and fact.value.startswith("T"):
            return await self.lookup(knowledge_type="ATT&CK", external_id=fact.value, limit=limit)
        return await self.lookup(knowledge_type="IOC", query=fact.value, limit=limit)


class NoopKnowledgeRetriever(KnowledgeRetriever):
    """Fail-closed retriever when no knowledge backend is configured.

    Used by ablation (rules-only) to prove that retrieval contributes value.
    """

    async def lookup(
        self,
        *,
        knowledge_type: str,
        external_id: str | None = None,
        query: str | None = None,
        limit: int = 5,
    ) -> list[RetrievedKnowledge]:
        return []

    async def lookup_fact(self, fact: SecurityFact, *, limit: int = 5) -> list[RetrievedKnowledge]:
        return []


def _to_retrieved(
    entry: Any,
    *,
    score: float,
    evidence_ref: str | None = None,
) -> RetrievedKnowledge:
    attributes = (
        entry.get("attributes") if isinstance(entry, dict) else getattr(entry, "attributes", {})
    )
    title = entry.get("title") if isinstance(entry, dict) else getattr(entry, "title", "")
    description = (
        entry.get("description") if isinstance(entry, dict) else getattr(entry, "description", "")
    )
    external_id = (
        entry.get("external_id") if isinstance(entry, dict) else getattr(entry, "external_id", "")
    )
    knowledge_type = (
        entry.get("knowledge_type")
        if isinstance(entry, dict)
        else getattr(entry, "knowledge_type", "")
    )
    return RetrievedKnowledge(
        knowledge_type=str(knowledge_type),
        external_id=str(external_id),
        title=str(title or ""),
        description=str(description or ""),
        score=max(0.0, min(1.0, score)),
        attributes=attributes or {},
        evidence_ref=evidence_ref,
    )
