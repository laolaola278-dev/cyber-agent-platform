"""Phase 23 release engineering, security defaults, and scope-boundary tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RC_VERSION = "1.0.1-rc1"


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
    assert ci.count("scan-type: image") == 2
    assert "image-ref: cap-backend:ci" in ci
    assert "image-ref: cap-frontend:ci" in ci

    release = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    assert "contents: write" in release
    assert "gh release create" in release
    assert "--verify-tag" in release
    assert "--prerelease" in release


def test_phase_23_does_not_add_forbidden_business_artifacts() -> None:
    assert not list((PROJECT_ROOT / "backend" / "alembic" / "versions").glob("*phase_23*"))
    assert not list((PROJECT_ROOT / "backend" / "app" / "api" / "routes").glob("*phase_23*"))
    assert not list((PROJECT_ROOT / "backend" / "app" / "models").glob("*phase_23*"))
    assert not list((PROJECT_ROOT / "backend" / "app" / "plugins").glob("*phase_23*"))
