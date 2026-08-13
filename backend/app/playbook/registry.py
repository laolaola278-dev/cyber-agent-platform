"""Immutable Playbook definition registry."""

from __future__ import annotations

from uuid import UUID

from app.exceptions import PlaybookConflict, PlaybookNotFound
from app.models.playbook import PlaybookVersion
from app.playbook.contracts import PlaybookDocument, PlaybookDSL
from app.repositories.playbook import PlaybookVersionRepository


class PlaybookRegistry:
    """Resolve only persisted, typed Playbook versions."""

    def __init__(self, versions: PlaybookVersionRepository | None = None) -> None:
        self._versions = versions
        self._memory: dict[UUID, tuple[PlaybookVersion, PlaybookDocument]] = {}

    async def register(self, version: PlaybookVersion) -> PlaybookVersion:
        document = PlaybookDSL.load(version.source_yaml)
        if version.document != document.model_dump(mode="json"):
            raise PlaybookConflict("Persisted Playbook document does not match its YAML")
        self._memory[version.id] = (version, document)
        return version

    async def resolve(self, version_id: UUID) -> PlaybookDocument:
        cached = self._memory.get(version_id)
        if cached is not None:
            return cached[1]
        if self._versions is None:
            raise PlaybookNotFound(f"Playbook Version {version_id} not found")
        version = await self._versions.get(version_id)
        if version is None:
            raise PlaybookNotFound(f"Playbook Version {version_id} not found")
        await self.register(version)
        return self._memory[version.id][1]

    async def latest(self, playbook_id: UUID) -> tuple[PlaybookVersion, PlaybookDocument]:
        if self._versions is None:
            for version, document in self._memory.values():
                if version.playbook_id == playbook_id:
                    return version, document
            raise PlaybookNotFound(f"Playbook {playbook_id} has no version")
        version = await self._versions.latest(playbook_id)
        if version is None:
            raise PlaybookNotFound(f"Playbook {playbook_id} has no version")
        document = await self.resolve(version.id)
        return version, document
