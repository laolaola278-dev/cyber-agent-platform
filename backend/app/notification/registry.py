"""Notification Plugin registry and certification boundary."""

from app.exceptions import NotificationValidationError
from app.notification.contracts import NotificationPlugin
from app.schemas.notification import NOTIFICATION_CAPABILITIES

ALLOWED_PLUGIN_PERMISSIONS = frozenset(
    {"notification.render", "notification.send", "notification.verify"}
)
DENIED_PLUGIN_PERMISSIONS = frozenset(
    {
        "database.access",
        "incident.modify",
        "incident.close",
        "response.modify",
        "report.write",
        "workflow.modify",
        "shell.execute",
        "filesystem.write",
        "template.execute",
    }
)


class NotificationRegistry:
    """Resolve only certified, least-privilege notification integrations."""

    def __init__(self) -> None:
        self._plugins: dict[str, NotificationPlugin] = {}

    def register(self, plugin: NotificationPlugin) -> None:
        name = plugin.name.strip().casefold()
        if not name or not plugin.version.strip():
            raise NotificationValidationError("Plugin name and version are required")
        if not plugin.capabilities or not plugin.capabilities <= NOTIFICATION_CAPABILITIES:
            raise NotificationValidationError(
                "Plugin declares unsupported notification capabilities"
            )
        forbidden = (plugin.permissions & DENIED_PLUGIN_PERMISSIONS) | (
            plugin.permissions - ALLOWED_PLUGIN_PERMISSIONS
        )
        if forbidden:
            raise NotificationValidationError(
                "Plugin declares forbidden permissions",
                details={"permissions": sorted(forbidden)},
            )
        required = {"notification.render", "notification.send", "notification.verify"}
        if not required <= plugin.permissions or not plugin.supports_verification:
            raise NotificationValidationError(
                "Plugin certification requires the complete verifiable lifecycle"
            )
        if not plugin.sandbox_compatible or not plugin.operational_documentation.strip():
            raise NotificationValidationError(
                "Plugin certification requires sandbox compatibility and documentation"
            )
        if name in self._plugins:
            raise NotificationValidationError(f"Notification plugin {name} is already registered")
        self._plugins[name] = plugin

    def require(self, name: str) -> NotificationPlugin:
        try:
            return self._plugins[name.strip().casefold()]
        except KeyError as error:
            raise NotificationValidationError(
                f"Notification plugin {name} is not registered"
            ) from error

    def resolve(self, capability: str) -> NotificationPlugin:
        candidates = [
            plugin for plugin in self._plugins.values() if capability in plugin.capabilities
        ]
        if not candidates:
            raise NotificationValidationError(
                "No Notification plugin provides the requested capability",
                details={"capability": capability},
            )
        return sorted(candidates, key=lambda item: (item.name, item.version))[0]

    @property
    def plugins(self) -> tuple[NotificationPlugin, ...]:
        return tuple(sorted(self._plugins.values(), key=lambda item: item.name))
