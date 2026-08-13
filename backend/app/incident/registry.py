"""Registry for trusted platform Incident escalation sources."""

from app.exceptions import IncidentValidationError

TRUSTED_INCIDENT_SOURCES = frozenset({"MANUAL", "ASSESSMENT", "DETECTION"})


class IncidentRegistry:
    """Resolve platform-owned source handlers without exposing lifecycle methods to Plugins."""

    def __init__(self, sources: frozenset[str] = TRUSTED_INCIDENT_SOURCES) -> None:
        normalized = frozenset(item.strip().upper() for item in sources if item.strip())
        if not normalized or not normalized <= TRUSTED_INCIDENT_SOURCES:
            raise IncidentValidationError("Incident Registry contains untrusted sources")
        self._sources = normalized

    def require(self, source: str) -> str:
        normalized = source.strip().upper()
        if normalized not in self._sources:
            raise IncidentValidationError(
                f"Incident source {source} is not registered",
                details={"registered_sources": sorted(self._sources)},
            )
        return normalized

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted(self._sources))
