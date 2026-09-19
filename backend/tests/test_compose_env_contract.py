"""Compose's required-variable contract must be satisfiable by CI and documented.

``docker-compose.yml`` deliberately writes its release-critical settings as
``${VAR:?...}`` (fail fast, no silent default) instead of ``${VAR:-default}``.
That only works if every consumer actually supplies them: the ``packaging`` job
runs ``docker compose config --quiet`` with an explicit env block, and an
operator follows ``.env.example``. When ``APP_VERSION`` became a required carrier,
the CI env block was not told, and the compose gate failed on the candidate with
``required variable APP_VERSION is missing a value`` -- a one-line gap that cost a
full certification cycle. These assertions keep the three lists in step.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = PROJECT_ROOT / "docker-compose.yml"
CI = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

_REQUIRED = re.compile(r"\$\{([A-Z0-9_]+):\?")


def _required_vars(text: str) -> set[str]:
    """Interpolations written as ``${VAR:?message}`` -- no default, must be set."""
    return set(_REQUIRED.findall(text))


def _compose_gate_env(ci_text: str) -> set[str]:
    """Env keys the CI job hands to ``docker compose config``."""
    doc = yaml.safe_load(ci_text)
    assert doc and "jobs" in doc, "ci.yml did not parse as a workflow"
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            if "docker compose config" in run:
                env = step.get("env") or {}
                assert env, "the compose gate has no env block at all"
                return set(env)
    raise AssertionError("no step runs 'docker compose config' -- the gate vanished")


def _env_example_keys(text: str) -> set[str]:
    return set(re.findall(r"^([A-Z0-9_]+)=", text, re.MULTILINE))


def test_compose_actually_requires_variables() -> None:
    """Guard against the assertions below passing vacuously."""
    required = _required_vars(COMPOSE.read_text("utf-8"))
    assert required, "docker-compose.yml has no ${VAR:?} interpolations"
    assert "APP_VERSION" in required, (
        "APP_VERSION stopped being a required carrier; the version-provenance "
        "guarantee in the compose deployment is gone"
    )


def test_ci_supplies_every_required_compose_variable() -> None:
    required = _required_vars(COMPOSE.read_text("utf-8"))
    provided = _compose_gate_env(CI.read_text("utf-8"))
    missing = required - provided
    assert not missing, (
        "the packaging job runs `docker compose config` without these required "
        f"variables, so the gate fails: {sorted(missing)}"
    )


def test_env_example_documents_every_required_compose_variable() -> None:
    required = _required_vars(COMPOSE.read_text("utf-8"))
    documented = _env_example_keys(ENV_EXAMPLE.read_text("utf-8"))
    missing = required - documented
    assert not missing, (
        f"an operator following .env.example cannot start the stack; these are "
        f"required by compose but absent from the template: {sorted(missing)}"
    )


def test_ci_does_not_hardcode_the_version_it_passes_to_compose() -> None:
    """APP_VERSION must reach compose from the canonical VERSION file.

    A literal here is the defect that mislabelled every CI-built image for five
    releases: correct on the day it was written, wrong forever after.
    """
    ci_text = CI.read_text("utf-8")
    provided = _compose_gate_env(ci_text)
    assert "APP_VERSION" in provided
    doc = yaml.safe_load(ci_text)
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            if "docker compose config" in step.get("run", ""):
                value = str((step.get("env") or {}).get("APP_VERSION", ""))
                assert "steps." in value and "outputs.version" in value, value
                assert not re.search(r"\d+\.\d+\.\d+", value), value


@pytest.mark.parametrize(
    "compose_text, expected",
    [
        ("APP_VERSION: ${APP_VERSION:?required}\n", {"APP_VERSION"}),
        ("TAG: ${TAG:-default}\n", set()),
        ("IMAGE: quay.io/minio/minio@sha256:abcd\n", set()),
    ],
)
def test_required_var_extractor_separates_required_from_defaulted(
    compose_text: str, expected: set[str]
) -> None:
    assert _required_vars(compose_text) == expected


def test_ci_gate_reader_rejects_a_workflow_without_a_compose_step() -> None:
    with pytest.raises(AssertionError, match="gate vanished"):
        _compose_gate_env("name: x\non: push\njobs:\n  a:\n    steps:\n      - run: echo hi\n")
