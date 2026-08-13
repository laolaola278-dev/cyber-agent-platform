"""In-process Detection Plugin registry."""

from app.detection.contracts import DetectionPlugin
from app.exceptions import DetectionValidationError
from app.schemas.detection import DETECTION_CAPABILITIES

ALLOWED_PLUGIN_PERMISSIONS = frozenset({"detection.execute", "evidence.read"})
DENIED_PLUGIN_PERMISSIONS = frozenset(
    {
        "database.access",
        "workflow.access",
        "assessment.access",
        "report.generate",
        "incident.create",
        "incident.transition",
        "incident.assign",
        "incident.close",
        "incident.reopen",
        "shell.execute",
        "filesystem.write",
    }
)


class DetectionRegistry:
    """Resolve governed plugins without importing tool internals."""

    def __init__(self) -> None:
        self._plugins: dict[str, DetectionPlugin] = {}

    def register(self, plugin: DetectionPlugin) -> None:
        name = plugin.name.strip().casefold()
        if not name or not plugin.version.strip():
            raise DetectionValidationError("Plugin name and version are required")
        if not plugin.capabilities or not plugin.capabilities <= DETECTION_CAPABILITIES:
            raise DetectionValidationError("Plugin declares unsupported detection capabilities")
        forbidden = (plugin.permissions & DENIED_PLUGIN_PERMISSIONS) | (
            plugin.permissions - ALLOWED_PLUGIN_PERMISSIONS
        )
        if forbidden:
            raise DetectionValidationError(
                "Plugin declares forbidden permissions",
                details={"permissions": sorted(forbidden)},
            )
        if name in self._plugins:
            raise DetectionValidationError(f"Detection plugin {name} is already registered")
        self._plugins[name] = plugin

    def require(self, name: str) -> DetectionPlugin:
        try:
            return self._plugins[name.strip().casefold()]
        except KeyError as error:
            raise DetectionValidationError(f"Detection plugin {name} is not registered") from error

    def resolve(self, capabilities: set[str]) -> DetectionPlugin:
        candidates = [
            plugin for plugin in self._plugins.values() if capabilities <= set(plugin.capabilities)
        ]
        if not candidates:
            raise DetectionValidationError(
                "No Detection plugin provides all requested capabilities",
                details={"capabilities": sorted(capabilities)},
            )
        return sorted(candidates, key=lambda item: (item.name, item.version))[0]

    @property
    def plugins(self) -> tuple[DetectionPlugin, ...]:
        return tuple(sorted(self._plugins.values(), key=lambda item: item.name))
