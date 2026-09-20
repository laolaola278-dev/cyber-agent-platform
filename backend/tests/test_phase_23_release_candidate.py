"""Phase 23 release engineering, security defaults, and scope-boundary tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from tests.test_release_image_completeness import (
    cap_image_names,
    chart_images,
    published_images,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RC_VERSION = "1.0.6-rc1"


def test_release_candidate_version_is_consistent() -> None:
    assert (PROJECT_ROOT / "VERSION").read_text("utf-8").strip() == RC_VERSION
    backend = (PROJECT_ROOT / "backend" / "pyproject.toml").read_text("utf-8")
    frontend = json.loads((PROJECT_ROOT / "frontend" / "package.json").read_text("utf-8"))
    sdk = (PROJECT_ROOT / "sdk" / "python" / "pyproject.toml").read_text("utf-8")
    chart = (PROJECT_ROOT / "deployment" / "helm" / "cap" / "Chart.yaml").read_text("utf-8")
    assert f'version = "{RC_VERSION}"' in backend
    assert frontend["version"] == RC_VERSION
    assert f'version = "{RC_VERSION}"' in sdk
    assert f"version: {RC_VERSION}" in chart
    assert f'appVersion: "{RC_VERSION}"' in chart


def test_production_rejects_placeholder_secrets_and_debug() -> None:
    with pytest.raises(ValidationError, match="insecure placeholders"):
        Settings(app_environment="production")
    with pytest.raises(ValidationError, match="DEBUG must be false"):
        Settings(
            app_environment="production",
            debug=True,
            secret_key="secure-secret-key-value",
            jwt_secret="independent-secure-jwt-value",
            rbac_trusted_proxy_secret="secure-proxy-value",
        )


def test_api_documentation_can_be_disabled() -> None:
    app = create_app(Settings(api_docs_enabled=False))
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_release_assets_and_required_documentation_exist() -> None:
    required = (
        "README.md",
        "LICENSE",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "CHANGELOG.md",
        "docs/releases/v1.0.0.md",
        "docs/releases/v1.0.1.md",
        "docs/releases/v1.0.2-rc1.md",
        "docs/releases/v1.0.2.md",
        "docs/releases/v1.0.3-rc1.md",
        "docs/releases/v1.0.3.md",
        "docs/releases/v1.0.4-rc1.md",
        "docs/releases/v1.0.4.md",
        "docs/releases/v1.0.5-rc1.md",
        "docs/releases/v1.0.5.md",
        "docs/releases/v1.0.6-rc1.md",
        "docs/known-issues.md",
        "docs/roadmap.md",
        "docs/api-guide.md",
        "docs/api-freeze-v1.md",
        "docs/plugin-development-guide.md",
        "docs/sdk-guide.md",
        "docs/operations-guide.md",
        "docs/runbook.md",
        "docs/faq.md",
        "docs/deployment/single-node.md",
        "docs/deployment/docker-compose.md",
        "docs/deployment/production-checklist.md",
        "docs/deployment/upgrade.md",
        "docs/deployment/rollback.md",
        "docs/deployment/backup-restore.md",
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
    )
    assert not [path for path in required if not (PROJECT_ROOT / path).is_file()]


def test_license_and_release_workflows_are_complete() -> None:
    license_text = (PROJECT_ROOT / "LICENSE").read_text("utf-8")
    assert "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" in license_text
    assert "1. Definitions." in license_text
    assert "9. Accepting Warranty or Additional Liability." in license_text
    assert "END OF TERMS AND CONDITIONS" in license_text

    ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    assert "scan-type: fs" in ci
    # F-7 made the two counts below wrong in the instructive way: this assertion
    # used to say `scan-type: image` appears twice and that the refs are
    # `cap-backend:ci` and `cap-frontend:ci` -- the same "there are exactly two
    # images" assumption the finding was about. CI now builds and scans the whole
    # release matrix from one per-cell step, so the check is set equality with
    # what the release publishes and what the chart deploys.
    ci_doc = yaml.safe_load(ci)
    builds = (ci_doc.get("jobs") or {})["release-image-builds"]
    cells = ci_matrix_cells(builds)
    assert cells == published_images(), (
        f"CI builds and scans {sorted(cells)} but the release publishes "
        f"{sorted(published_images())}"
    )
    assert cells == cap_image_names(chart_images()), (
        f"CI covers {sorted(cells)} but the production chart deploys "
        f"{sorted(cap_image_names(chart_images()))}"
    )
    scans = [
        step for step in builds.get("steps") or []
        if (step.get("with") or {}).get("scan-type") == "image"
    ]
    assert len(scans) == 1, (
        f"expected one per-cell image scan in the matrix job, found {len(scans)}"
    )
    assert "matrix.image" in str(scans[0]["with"]["image-ref"]), (
        "the scan is not tied to the matrix cell, so an added image would go unscanned"
    )

    release = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    assert "contents: write" in release
    assert "gh release create" in release
    assert "--verify-tag" in release
    assert "--prerelease" in release


def ci_matrix_cells(builds: dict) -> set[str]:
    """The images a CI matrix job builds, by name."""
    return {entry["image"] for entry in builds["strategy"]["matrix"]["include"]}


def test_the_ci_image_coverage_check_is_sensitive() -> None:
    """Control for the set equality above: dropping a cell has to break it.

    An equality between two derived sets passes vacuously if one side is empty or
    if the reader ignores the thing being compared. Removing one matrix cell from
    the parsed job must make CI cover strictly less than the release publishes --
    otherwise this is the "there are exactly two images" assertion again, wearing
    a derivation.
    """
    ci_doc = yaml.safe_load((PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    builds = ci_doc["jobs"]["release-image-builds"]
    cells = ci_matrix_cells(builds)
    assert cells == published_images()
    assert cells == cap_image_names(chart_images())
    entries = builds["strategy"]["matrix"]["include"]
    for index in range(len(entries)):
        without = [entry for position, entry in enumerate(entries) if position != index]
        assert ci_matrix_cells({"strategy": {"matrix": {"include": without}}}) != cells, (
            f"removing cell {index} changed nothing -- the reader is not reading"
        )


def test_phase_23_does_not_add_forbidden_business_artifacts() -> None:
    assert not list((PROJECT_ROOT / "backend" / "alembic" / "versions").glob("*phase_23*"))
    assert not list((PROJECT_ROOT / "backend" / "app" / "api" / "routes").glob("*phase_23*"))
    assert not list((PROJECT_ROOT / "backend" / "app" / "models").glob("*phase_23*"))
    assert not list((PROJECT_ROOT / "backend" / "app" / "plugins").glob("*phase_23*"))
