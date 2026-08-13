"""In-process Response Plugin registry with certification checks."""

from app.exceptions import ResponseValidationError
from app.response.contracts import ResponsePlugin
from app.schemas.response import RESPONSE_CAPABILITIES

ALLOWED_PLUGIN_PERMISSIONS = frozenset({"response.execute", "response.verify", "response.rollback"})
DENIED_PLUGIN_PERMISSIONS = frozenset(
    {
        "database.access",
        "incident.modify",
        "asset.modify",
        "report.write",
        "approval.decide",
        "workflow.modify",
        "shell.execute",
        "filesystem.write",
    }
)


class ResponseRegistry:
    """Resolve only certified, least-privilege response plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, ResponsePlugin] = {}

    def register(self, plugin: ResponsePlugin) -> None:
        name = plugin.name.strip().casefold()
        if not name or not plugin.version.strip():
            raise ResponseValidationError("Plugin name and version are required")
        if not plugin.capabilities or not plugin.capabilities <= RESPONSE_CAPABILITIES:
            raise ResponseValidationError("Plugin declares unsupported response capabilities")
        forbidden = (plugin.permissions & DENIED_PLUGIN_PERMISSIONS) | (
            plugin.permissions - ALLOWED_PLUGIN_PERMISSIONS
        )
        if forbidden:
            raise ResponseValidationError(
                "Plugin declares forbidden permissions",
                details={"permissions": sorted(forbidden)},
            )
        required = {"response.execute", "response.verify"}
        if plugin.supports_rollback:
            required.add("response.rollback")
        if not required <= plugin.permissions:
            raise ResponseValidationError(
                "Plugin permissions do not satisfy declared lifecycle support"
            )
        if not plugin.supports_approval:
            raise ResponseValidationError("Response plugins must support governed approval")
        if not plugin.sandbox_compatible or not plugin.operational_documentation.strip():
            raise ResponseValidationError(
                "Plugin certification requires sandbox compatibility and documentation"
            )
        if name in self._plugins:
            raise ResponseValidationError(f"Response plugin {name} is already registered")
        self._plugins[name] = plugin

    def require(self, name: str) -> ResponsePlugin:
        try:
            return self._plugins[name.strip().casefold()]
        except KeyError as error:
            raise ResponseValidationError(f"Response plugin {name} is not registered") from error

    def resolve(self, capability: str) -> ResponsePlugin:
        candidates = [
            plugin for plugin in self._plugins.values() if capability in plugin.capabilities
        ]
        if not candidates:
            raise ResponseValidationError(
                "No Response plugin provides the requested capability",
                details={"capability": capability},
            )
        return sorted(candidates, key=lambda item: (item.name, item.version))[0]

    @property
    def plugins(self) -> tuple[ResponsePlugin, ...]:
        return tuple(sorted(self._plugins.values(), key=lambda item: item.name))
