"""Tool manifest contracts used by ToolManager and ToolFactory."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from app.schemas.registry import ToolRegister


class ToolManifest(BaseModel):
    """Validated adapter construction definition stored by Tool Registry."""

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    adapter: str = Field(min_length=1, max_length=128)
    capabilities: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class ToolRegistrationManifest(BaseModel):
    """Portable Tool Registry definition loaded from a trusted YAML file."""

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    tool_type: str = Field(min_length=1, max_length=64)
    description: str | None = None
    required_permissions: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    runtime_requirements: dict[str, Any] = Field(default_factory=dict)

    def as_registration(self) -> ToolRegister:
        return ToolRegister.model_validate(self.model_dump())


class ToolManifestLoader:
    """Load trusted platform Tool definitions without importing implementations."""

    def load(self, path: Path) -> ToolRegistrationManifest:
        if path.name != "manifest.yaml":
            raise ValueError("Tool manifest must be named manifest.yaml")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Tool manifest must contain a YAML mapping")
        return ToolRegistrationManifest.model_validate(raw)
