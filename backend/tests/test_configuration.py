"""Strongly typed configuration provider tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import ConfigurationProvider
from app.core.enums import AgentStatus

REGISTRY_YAML = """\
heartbeat:
  stale_after_seconds: 30
  default_health_status: UNKNOWN
registration:
  default_agent_status: OFFLINE
  default_tool_status: ENABLED
  require_unique_name_version: true
"""
ORCHESTRATOR_YAML = """\
dispatcher:
  eligible_agent_statuses: [ONLINE]
  scheduling_strategy: first_available
  task_timeout_seconds: 120
  queue_on_dispatch: true
  fail_when_no_agent: true
security:
  deny_high_risk_without_approval: true
"""
LOGGING_YAML = """\
version: 1
disable_existing_loggers: false
formatters: {simple: {format: '%(message)s'}}
handlers: {console: {class: logging.StreamHandler, formatter: simple}}
root: {level: INFO, handlers: [console]}
"""


def _write_config(directory: Path, orchestrator: str = ORCHESTRATOR_YAML) -> None:
    (directory / "registry.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    (directory / "orchestrator.yaml").write_text(orchestrator, encoding="utf-8")
    (directory / "logging.yaml").write_text(LOGGING_YAML, encoding="utf-8")


def test_provider_loads_typed_values_and_notifies_reload(tmp_path: Path) -> None:
    _write_config(tmp_path)
    provider = ConfigurationProvider(tmp_path)
    observed_generations: list[int] = []
    provider.add_reload_listener(lambda active: observed_generations.append(active.generation))

    provider.load()
    assert provider.registry.heartbeat.stale_after_seconds == 30
    assert provider.orchestrator.dispatcher.eligible_agent_statuses == [AgentStatus.ONLINE]
    assert provider.generation == 1

    provider.reload()
    assert provider.generation == 2
    assert observed_generations == [2]


def test_provider_rejects_invalid_dispatcher_config(tmp_path: Path) -> None:
    invalid = ORCHESTRATOR_YAML.replace(
        "eligible_agent_statuses: [ONLINE]", "eligible_agent_statuses: []"
    )
    _write_config(tmp_path, invalid)

    with pytest.raises(ValidationError):
        ConfigurationProvider(tmp_path).load()
