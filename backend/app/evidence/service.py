"""Evidence application service."""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EvidenceType
from app.events import EventPublisher, EventType, PlatformEvent
from app.models import AssetEvidence, Evidence


class EvidenceService:
    """Persist normalized capture evidence through one platform capability."""

    def __init__(
        self, session: AsyncSession, publisher: EventPublisher, storage_directory: Path
    ) -> None:
        self._session = session
        self._publisher = publisher
        self._storage_directory = storage_directory

    def _emit(self, event: PlatformEvent) -> None:
        """Publish an event; a null publisher (tests/embedded) is a no-op."""
        if self._publisher is not None:
            self._publisher.publish(event)

    async def save_capture(
        self,
        *,
        task_id: UUID,
        agent_id: UUID,
        trace_id: str,
        url: str,
        http_status: int | None,
        title: str | None,
        html: str,
        screenshot: bytes | None,
        asset_id: UUID | None = None,
    ) -> Evidence:
        """Hash the capture, safely persist a screenshot and create evidence metadata."""

        html_bytes = html.encode("utf-8")
        screenshot_path: str | None = None
        if screenshot is not None:
            self._storage_directory.mkdir(parents=True, exist_ok=True)
            image_path = self._storage_directory / f"{task_id}-{uuid_safe(trace_id)}.png"
            image_path.write_bytes(screenshot)
            screenshot_path = str(image_path)
        html_sha256 = sha256(html_bytes).hexdigest()
        evidence = Evidence(
            task_id=task_id,
            agent_id=agent_id,
            trace_id=trace_id,
            url=url,
            http_status=http_status,
            title=title,
            evidence_type=EvidenceType.HTML.value,
            sha256=html_sha256,
            content_type="text/html; charset=utf-8",
            object_storage_path=None,
            html_hash=html_sha256,
            content_hash=sha256(html_bytes + (screenshot or b"")).hexdigest(),
            screenshot_path=screenshot_path,
            captured_at=datetime.now(UTC),
        )
        self._session.add(evidence)
        await self._session.flush()
        if asset_id is not None:
            self._session.add(AssetEvidence(asset_id=asset_id, evidence_id=evidence.id))
            await self._session.flush()
            self._emit(
                PlatformEvent(
                    type=EventType.ASSET_EVIDENCE_LINKED,
                    trace_id=trace_id,
                    aggregate_id=asset_id,
                    actor="evidence-service",
                    resource=f"asset:{asset_id}",
                    agent_id=agent_id,
                    task_id=task_id,
                    payload={"evidence_id": str(evidence.id), "source": "capture"},
                )
            )
        self._emit(
            PlatformEvent(
                type=EventType.EVIDENCE_SAVED,
                trace_id=trace_id,
                aggregate_id=evidence.id,
                actor="evidence-service",
                resource=f"evidence:{evidence.id}",
                agent_id=agent_id,
                task_id=task_id,
                payload={
                    "url": url,
                    "http_status": http_status,
                    "html_hash": evidence.html_hash,
                },
                result={"screenshot_path": screenshot_path},
            )
        )
        return evidence

    async def save_object(
        self,
        *,
        task_id: UUID,
        agent_id: UUID,
        trace_id: str,
        url: str,
        http_status: int | None,
        title: str | None,
        content: bytes,
        content_type: str,
        object_storage_path: str | None = None,
        asset_id: UUID | None = None,
    ) -> Evidence:
        """Persist a RAW acquisition artifact as platform evidence.

        The evidence SHA-256 is the exact hash of the raw bytes, so the
        chain ``object store key == Evidence.sha256 == Artifact.sha256``
        holds and integrity can be re-verified by re-reading the object.

        Phase 28.2 -- idempotent persistence: if an evidence row with the
        same content hash ALREADY exists (same bytes saved by a previous
        attempt whose checkpoint commit crashed), the existing row is
        returned instead of inserting a duplicate. This gives the platform
        at-least-once execution + idempotent persistence (never exactly-once
        claims), so an idempotent resume cannot produce duplicate Evidence
        facts.
        """
        from sqlalchemy import select

        content_hash = sha256(content).hexdigest()
        existing = await self._session.scalar(
            select(Evidence).where(Evidence.content_hash == content_hash).limit(1)
        )
        if existing is not None:
            return existing
        evidence = Evidence(
            task_id=task_id,
            agent_id=agent_id,
            trace_id=trace_id,
            url=url,
            http_status=http_status,
            title=title,
            evidence_type=EvidenceType.HTML.value,
            sha256=content_hash,
            content_type=content_type or "application/octet-stream",
            object_storage_path=object_storage_path,
            html_hash=content_hash,
            content_hash=content_hash,
            screenshot_path=None,
            captured_at=datetime.now(UTC),
        )
        self._session.add(evidence)
        await self._session.flush()
        if asset_id is not None:
            self._session.add(AssetEvidence(asset_id=asset_id, evidence_id=evidence.id))
            await self._session.flush()
            self._emit(
                PlatformEvent(
                    type=EventType.ASSET_EVIDENCE_LINKED,
                    trace_id=trace_id,
                    aggregate_id=asset_id,
                    actor="evidence-service",
                    resource=f"asset:{asset_id}",
                    agent_id=agent_id,
                    task_id=task_id,
                    payload={"evidence_id": str(evidence.id), "source": "object"},
                )
            )
        self._emit(
            PlatformEvent(
                type=EventType.EVIDENCE_SAVED,
                trace_id=trace_id,
                aggregate_id=evidence.id,
                actor="evidence-service",
                resource=f"evidence:{evidence.id}",
                agent_id=agent_id,
                task_id=task_id,
                payload={"url": url, "http_status": http_status, "sha256": content_hash},
                result={"object_storage_path": object_storage_path},
            )
        )
        return evidence


def uuid_safe(value: str) -> str:
    """Keep trace IDs safe when used in a platform-owned filename."""

    return "".join(char for char in value if char.isalnum() or char in {"-", "_"})[:64]
