"""Read-only capability executor (v2.0 / Phase 25).

The Investigation Agent may only execute low-risk *read-only* capabilities.
Execution goes through the platform Repository layer (never through the LLM,
never through shell/worker/sandbox). Each successful call produces an
``AgentObservation`` with evidence references.

Every invocation is pre-checked by :class:`CapabilityGuardrail`; the executor
itself refuses anything that is not in the read-only allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.contracts import AgentObservation
from app.agent.exceptions import AgentExecutionError
from app.core.enums import AssetType
from app.models import (
    Asset,
    Evidence,
    Finding,
    Incident,
    SecurityEvent,
)
from app.repositories.assessment import FindingRepository
from app.repositories.asset import AssetRepository
from app.repositories.base import SQLAlchemyRepository
from app.repositories.detection import SecurityEventRepository
from app.repositories.incident import IncidentRepository
from app.repositories.knowledge import KnowledgeRepository


class _EvidenceRepository(SQLAlchemyRepository[Evidence]):
    model = Evidence


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    """Structured result of one read-only capability call."""

    capability: str
    summary: str
    items: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    error: str | None = None

    def to_observation(self) -> AgentObservation:
        return AgentObservation(
            capability=self.capability,
            summary=self.summary,
            evidence_refs=self.evidence_refs,
            confidence=0.9 if self.error is None else 0.3,
        )


class ReadOnlyCapabilityExecutor:
    """Executes read-only registry capabilities through platform repositories."""

    def __init__(self, session: AsyncSession, registry: set[str]) -> None:
        self._session = session
        self._registry = registry
        self._assets = AssetRepository(session)
        self._knowledge = KnowledgeRepository(session)
        self._findings = FindingRepository(session)
        self._events = SecurityEventRepository(session)
        self._incidents = IncidentRepository(session)
        self._evidence = _EvidenceRepository(session)

    async def execute(
        self,
        capability: str,
        parameters: dict[str, Any],
        *,
        allowed_capabilities: set[str],
    ) -> CapabilityResult:
        if capability not in self._registry:
            raise AgentExecutionError(f"Unknown capability: {capability}")
        if capability not in allowed_capabilities:
            raise AgentExecutionError(f"Capability not granted to agent: {capability}")
        if not capability.endswith(".read"):
            raise AgentExecutionError(
                "Investigation agent may only execute read-only capabilities"
            )
        handler = getattr(self, f"_read_{capability.split('.')[0].replace('-', '_')}", None)
        if handler is None:
            raise AgentExecutionError(f"No read-only executor for capability: {capability}")
        try:
            return await handler(parameters)
        except Exception as exc:  # noqa: BLE001 - translated into a structured failure
            return CapabilityResult(
                capability=capability,
                summary=f"Capability execution failed: {exc}",
                error=str(exc),
            )

    # -- read handlers -----------------------------------------------------

    async def _read_asset(self, parameters: dict[str, Any]) -> CapabilityResult:
        identity = self._safe_str(parameters.get("identity"))
        if identity:
            asset_type = AssetType(str(parameters.get("type", "HOST")).upper())
            asset = await self._assets.get_by_identity(asset_type, identity)
            items = [self._asset_payload(asset)] if asset else []
            return CapabilityResult(
                capability="asset.read",
                summary=f"Asset lookup for {identity}: {len(items)} result(s)",
                items=items,
                evidence_refs=[f"asset:{asset.id}" for asset in [asset] if asset],
            )
        name = self._safe_str(parameters.get("query"))
        page = await self._assets.search(name=name or None, page=1, page_size=20)
        items = [self._asset_payload(asset) for asset in page.items]
        return CapabilityResult(
            capability="asset.read",
            summary=f"Listed {len(items)} asset(s)",
            items=items,
            evidence_refs=[f"asset:{item.id}" for item in page.items],
        )

    async def _read_knowledge(self, parameters: dict[str, Any]) -> CapabilityResult:
        query = self._safe_str(parameters.get("query", ""))
        page = await self._knowledge.search(query=query, page=1, page_size=20)
        items = [
            {"id": str(item.id), "name": getattr(item, "name", None)} for item in page.items
        ]
        return CapabilityResult(
            capability="knowledge.read",
            summary=f"Searched knowledge: {len(items)} result(s)",
            items=items,
            evidence_refs=[f"knowledge:{item.id}" for item in page.items],
        )

    async def _read_finding(self, parameters: dict[str, Any]) -> CapabilityResult:
        page = await self._findings.search(
            severity=self._safe_str(parameters.get("severity")) or None,
            status=self._safe_str(parameters.get("status")) or None,
            page=1,
            page_size=20,
        )
        items = [self._finding_payload(item) for item in page.items]
        return CapabilityResult(
            capability="finding.read",
            summary=f"Listed {len(items)} finding(s)",
            items=items,
            evidence_refs=[f"finding:{item.id}" for item in page.items],
        )

    async def _read_security_event(self, parameters: dict[str, Any]) -> CapabilityResult:
        page = await self._events.search(
            severity=self._safe_str(parameters.get("severity")) or None,
            status=self._safe_str(parameters.get("status")) or None,
            page=1,
            page_size=20,
        )
        items = [self._event_payload(item) for item in page.items]
        return CapabilityResult(
            capability="security_event.read",
            summary=f"Listed {len(items)} security event(s)",
            items=items,
            evidence_refs=[f"security_event:{item.id}" for item in page.items],
        )

    async def _read_incident(self, parameters: dict[str, Any]) -> CapabilityResult:
        page = await self._incidents.search(
            severity=self._safe_str(parameters.get("severity")) or None,
            status=self._safe_str(parameters.get("status")) or None,
            page=1,
            page_size=20,
        )
        items = [self._incident_payload(item) for item in page.items]
        return CapabilityResult(
            capability="incident.read",
            summary=f"Listed {len(items)} incident(s)",
            items=items,
            evidence_refs=[f"incident:{item.id}" for item in page.items],
        )

    async def _read_evidence(self, parameters: dict[str, Any]) -> CapabilityResult:
        page = await self._evidence.list_page(page=1, page_size=20)
        items = [self._evidence_payload(item) for item in page.items]
        return CapabilityResult(
            capability="evidence.read",
            summary=f"Listed {len(items)} evidence record(s)",
            items=items,
            evidence_refs=[f"evidence:{item.id}" for item in page.items],
        )

    # -- payload helpers ---------------------------------------------------

    @staticmethod
    def _safe_str(value: Any) -> str:
        return str(value) if value is not None else ""

    @staticmethod
    def _asset_payload(asset: Asset) -> dict[str, Any]:
        return {
            "id": str(asset.id),
            "type": getattr(asset, "asset_type", None) or getattr(asset, "type", None),
            "name": getattr(asset, "name", None),
            "status": getattr(asset, "status", None),
        }

    @staticmethod
    def _finding_payload(finding: Finding) -> dict[str, Any]:
        return {
            "id": str(finding.id),
            "title": getattr(finding, "title", None) or getattr(finding, "name", None),
            "severity": getattr(finding, "severity", None),
            "status": getattr(finding, "status", None),
        }

    @staticmethod
    def _event_payload(event: SecurityEvent) -> dict[str, Any]:
        return {
            "id": str(event.id),
            "title": getattr(event, "title", None) or getattr(event, "name", None),
            "severity": getattr(event, "severity", None),
            "status": getattr(event, "status", None),
        }

    @staticmethod
    def _incident_payload(incident: Incident) -> dict[str, Any]:
        return {
            "id": str(incident.id),
            "title": getattr(incident, "title", None) or getattr(incident, "name", None),
            "status": getattr(incident, "status", None),
        }

    @staticmethod
    def _evidence_payload(evidence: Evidence) -> dict[str, Any]:
        return {
            "id": str(evidence.id),
            "kind": getattr(evidence, "evidence_type", None) or getattr(evidence, "kind", None),
            "status": getattr(evidence, "status", None),
        }
