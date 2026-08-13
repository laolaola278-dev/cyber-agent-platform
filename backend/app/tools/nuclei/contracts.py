"""Nuclei adapter value objects."""

from dataclasses import dataclass
from pathlib import Path

from app.schemas.assessment import AssessmentPolicy


@dataclass(frozen=True, slots=True)
class ApprovedNucleiTemplate:
    template_id: str
    path: Path
    sha256: str
    max_requests: int


@dataclass(frozen=True, slots=True)
class NucleiExecutionRequest:
    target: str
    templates: tuple[str, ...]
    policy: AssessmentPolicy


@dataclass(frozen=True, slots=True)
class NucleiExecutionResult:
    records: tuple[dict[str, object], ...]
    request_budget: int
    stderr: str
    duration_seconds: float
