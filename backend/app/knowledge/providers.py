"""Provider extension contracts for external knowledge sources."""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from app.schemas.knowledge import KnowledgeRecord


@runtime_checkable
class KnowledgeProvider(Protocol):
    name: str

    async def records(self) -> AsyncIterator[KnowledgeRecord]:
        """Yield provider-neutral records without writing platform state."""
        ...


class CVEProvider(KnowledgeProvider, Protocol):
    """CVE source adapter boundary."""


class AttackProvider(KnowledgeProvider, Protocol):
    """MITRE ATT&CK source adapter boundary."""


class KEVProvider(KnowledgeProvider, Protocol):
    """CISA KEV source adapter boundary."""


class VendorProvider(KnowledgeProvider, Protocol):
    """Vendor advisory source adapter boundary."""
