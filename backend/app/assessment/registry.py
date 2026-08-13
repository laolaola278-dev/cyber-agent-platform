"""In-process Assessment Plugin and capability registry."""

from app.assessment.contracts import AssessmentPlugin
from app.exceptions import AssessmentValidationError
from app.schemas.assessment import ASSESSMENT_CAPABILITIES

ALLOWED_PLUGIN_PERMISSIONS = frozenset(
    {"assessment.execute", "tool.invoke", "evidence.write", "report.generate"}
)
DENIED_PLUGIN_PERMISSIONS = frozenset(
    {
        "shell.execute",
        "filesystem.write",
        "firewall.write",
        "agent.register",
        "workflow.modify",
        "incident.create",
        "incident.transition",
        "incident.assign",
        "incident.close",
        "incident.reopen",
    }
)


class AssessmentRegistry:
    """Select registered plugins by governed capability without importing scanner internals."""

    def __init__(self) -> None:
        self._plugins: dict[str, AssessmentPlugin] = {}

    def register(self, plugin: AssessmentPlugin) -> None:
        name = plugin.name.strip().casefold()
        if not name or not plugin.version.strip():
            raise AssessmentValidationError("Plugin name and version are required")
        if not plugin.capabilities or not plugin.capabilities <= ASSESSMENT_CAPABILITIES:
            raise AssessmentValidationError("Plugin declares unsupported assessment capabilities")
        denied = plugin.permissions & DENIED_PLUGIN_PERMISSIONS
        unsupported = plugin.permissions - ALLOWED_PLUGIN_PERMISSIONS
        if denied or unsupported:
            raise AssessmentValidationError(
                "Plugin declares forbidden permissions",
                details={"permissions": sorted(denied | unsupported)},
            )
        if name in self._plugins:
            raise AssessmentValidationError(f"Assessment plugin {name} is already registered")
        self._plugins[name] = plugin

    def require(self, name: str) -> AssessmentPlugin:
        try:
            return self._plugins[name.strip().casefold()]
        except KeyError as error:
            raise AssessmentValidationError(
                f"Assessment plugin {name} is not registered"
            ) from error

    def resolve(self, capabilities: set[str]) -> AssessmentPlugin:
        candidates = [
            plugin for plugin in self._plugins.values() if capabilities <= set(plugin.capabilities)
        ]
        if not candidates:
            raise AssessmentValidationError(
                "No Assessment plugin provides all requested capabilities",
                details={"capabilities": sorted(capabilities)},
            )
        return sorted(candidates, key=lambda item: (item.name, item.version))[0]

    @property
    def plugins(self) -> tuple[AssessmentPlugin, ...]:
        return tuple(sorted(self._plugins.values(), key=lambda item: item.name))
