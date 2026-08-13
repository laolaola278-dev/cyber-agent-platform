"""Telemetry Plugin registry with least-privilege admission checks."""

from app.exceptions import TelemetryConflict, TelemetryNotFound, TelemetryPolicyViolation
from app.telemetry.contracts import TelemetryPlugin

ALLOWED_TELEMETRY_PERMISSIONS = frozenset({"telemetry.receive", "telemetry.publish"})
FORBIDDEN_TELEMETRY_PERMISSIONS = frozenset(
    {
        "database.access",
        "detection.access",
        "security_event.create",
        "incident.create",
        "shell.execute",
        "filesystem.write",
        "network.unrestricted",
    }
)


class TelemetryRegistry:
    """Runtime-only registry; persistence is owned by TelemetryService."""

    def __init__(self) -> None:
        self._plugins: dict[str, TelemetryPlugin] = {}

    @property
    def plugins(self) -> tuple[TelemetryPlugin, ...]:
        return tuple(self._plugins.values())

    def register(self, plugin: TelemetryPlugin) -> None:
        name = plugin.name.strip().casefold()
        if not name or name in self._plugins:
            raise TelemetryConflict(f"Telemetry plugin {name or '<blank>'} is already registered")
        if not plugin.permissions or not plugin.permissions <= ALLOWED_TELEMETRY_PERMISSIONS:
            raise TelemetryPolicyViolation("Telemetry plugin requested unsupported permissions")
        if plugin.permissions & FORBIDDEN_TELEMETRY_PERMISSIONS:
            raise TelemetryPolicyViolation("Telemetry plugin requested forbidden permissions")
        if not plugin.capabilities:
            raise TelemetryPolicyViolation("Telemetry plugin must declare capabilities")
        self._plugins[name] = plugin

    def require(self, name: str) -> TelemetryPlugin:
        plugin = self._plugins.get(name.strip().casefold())
        if plugin is None:
            raise TelemetryNotFound(f"Telemetry plugin {name} not found")
        return plugin
