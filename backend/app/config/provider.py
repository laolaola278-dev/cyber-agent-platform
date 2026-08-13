"""Central configuration provider for all YAML-backed platform policies."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar, cast

import yaml
from pydantic import BaseModel

from app.config.models import (
    AssessmentSettings,
    DetectionSettings,
    IncidentSettings,
    LoggingConfig,
    NotificationSettings,
    OrchestratorConfig,
    RegistryConfig,
    ResponseSettings,
    RuntimeSettings,
    TelemetrySettings,
)

ConfigModelT = TypeVar("ConfigModelT", bound=BaseModel)
ReloadListener = Callable[["ConfigurationProvider"], None]


class ConfigurationProvider:
    """Load, validate, cache, and expose platform configuration.

    Reload listeners form the extension boundary for a future file watcher. Phase 1.1
    intentionally does not start a background watcher.
    """

    def __init__(self, config_directory: Path) -> None:
        self._config_directory = config_directory
        self._registry: RegistryConfig | None = None
        self._orchestrator: OrchestratorConfig | None = None
        self._logging: LoggingConfig | None = None
        self._runtime: RuntimeSettings | None = None
        self._assessment: AssessmentSettings | None = None
        self._detection: DetectionSettings | None = None
        self._incident: IncidentSettings | None = None
        self._telemetry: TelemetrySettings | None = None
        self._response: ResponseSettings | None = None
        self._notification: NotificationSettings | None = None
        self._listeners: list[ReloadListener] = []
        self._generation = 0

    @property
    def config_directory(self) -> Path:
        return self._config_directory

    @property
    def generation(self) -> int:
        """Monotonically increasing reload generation."""

        return self._generation

    @property
    def registry(self) -> RegistryConfig:
        self._ensure_loaded()
        return cast(RegistryConfig, self._registry)

    @property
    def orchestrator(self) -> OrchestratorConfig:
        self._ensure_loaded()
        return cast(OrchestratorConfig, self._orchestrator)

    @property
    def logging(self) -> LoggingConfig:
        self._ensure_loaded()
        return cast(LoggingConfig, self._logging)

    @property
    def runtime(self) -> RuntimeSettings:
        """Return validated Runtime configuration."""

        self._ensure_loaded()
        return cast(RuntimeSettings, self._runtime)

    @property
    def assessment(self) -> AssessmentSettings:
        """Return validated Assessment policy defaults."""

        self._ensure_loaded()
        return cast(AssessmentSettings, self._assessment)

    @property
    def detection(self) -> DetectionSettings:
        """Return validated Detection policy defaults."""

        self._ensure_loaded()
        return cast(DetectionSettings, self._detection)

    @property
    def incident(self) -> IncidentSettings:
        """Return validated Incident policy defaults."""

        self._ensure_loaded()
        return cast(IncidentSettings, self._incident)

    @property
    def telemetry(self) -> TelemetrySettings:
        """Return validated source-neutral Telemetry settings."""

        self._ensure_loaded()
        return cast(TelemetrySettings, self._telemetry)

    @property
    def response(self) -> ResponseSettings:
        """Return validated Response, Approval and Rollback settings."""

        self._ensure_loaded()
        return cast(ResponseSettings, self._response)

    @property
    def notification(self) -> NotificationSettings:
        """Return validated Notification, Routing and Ticket settings."""

        self._ensure_loaded()
        return cast(NotificationSettings, self._notification)

    def load(self) -> None:
        """Load and strongly validate every platform YAML document."""

        self._registry = self._load_model("registry.yaml", RegistryConfig)
        self._orchestrator = self._load_model("orchestrator.yaml", OrchestratorConfig)
        self._logging = self._load_model("logging.yaml", LoggingConfig)
        runtime_path = self._config_directory / "runtime.yaml"
        self._runtime = (
            self._load_model("runtime.yaml", RuntimeSettings)
            if runtime_path.exists()
            else RuntimeSettings(runtime={})
        )
        assessment_path = self._config_directory / "assessment.yaml"
        self._assessment = (
            self._load_model("assessment.yaml", AssessmentSettings)
            if assessment_path.exists()
            else AssessmentSettings()
        )
        detection_path = self._config_directory / "detection.yaml"
        self._detection = (
            self._load_model("detection.yaml", DetectionSettings)
            if detection_path.exists()
            else DetectionSettings()
        )
        incident_path = self._config_directory / "incident.yaml"
        self._incident = (
            self._load_model("incident.yaml", IncidentSettings)
            if incident_path.exists()
            else IncidentSettings()
        )
        telemetry_path = self._config_directory / "telemetry.yaml"
        self._telemetry = (
            self._load_model("telemetry.yaml", TelemetrySettings)
            if telemetry_path.exists()
            else TelemetrySettings()
        )
        response_path = self._config_directory / "response.yaml"
        self._response = (
            self._load_model("response.yaml", ResponseSettings)
            if response_path.exists()
            else ResponseSettings()
        )
        notification_path = self._config_directory / "notification.yaml"
        self._notification = (
            self._load_model("notification.yaml", NotificationSettings)
            if notification_path.exists()
            else NotificationSettings()
        )
        self._generation += 1

    def reload(self) -> None:
        """Reload all configuration and notify registered watcher adapters."""

        self.load()
        for listener in tuple(self._listeners):
            listener(self)

    def add_reload_listener(self, listener: ReloadListener) -> None:
        """Register a callback used by a future filesystem-watcher adapter."""

        self._listeners.append(listener)

    def _ensure_loaded(self) -> None:
        if self._generation == 0:
            self.load()

    def _load_model(self, filename: str, model: type[ConfigModelT]) -> ConfigModelT:
        path = self._config_directory / filename
        with path.open(encoding="utf-8") as config_file:
            raw: Any = yaml.safe_load(config_file)
        if not isinstance(raw, dict):
            raise ValueError(f"Configuration file {path} must contain a YAML mapping")
        return model.model_validate(raw)
