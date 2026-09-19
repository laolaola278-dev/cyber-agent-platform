"""The CAP_ZAP_API_KEY contract: README promises it, so the deployment has to deliver it.

Before 41bbc49 the promise was false in both directions. ``Settings`` had no
matching field and used ``extra="ignore"``, so the documented variable was
discarded; ``MemorySecretProvider`` was built empty with no other writer, so the
``zap-api-key`` reference that ``get_zap_api_key`` resolves could not exist
outside the test fixtures -- which seeded it by hand, hiding the defect from
every test in the suite. The consequence was user-visible: ``POST /incidents``
depends on ``PlaybookService`` -> ``AssessmentService``, so a fresh deployment
could not create an incident at all.

A second break was found while certifying that fix: compose declares each
container's environment explicitly (no ``env_file``), and ``CAP_ZAP_API_KEY`` was
not in that list, so the single-node path described by the README could still
never provision ZAP. These tests pin both ends -- the code that seeds, the
deployment files that carry, and the fail-closed behaviour when an operator has
not configured a credential.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.config import Settings
from app.dependencies.services import get_zap_api_key
from app.exceptions import SecretNotFound
from app.main import create_app
from tests.conftest import TestSessionFactory

REPO_ROOT = Path(__file__).resolve().parents[2]
CREDENTIAL = "probe-zap-credential-value"


async def _resolve_zap_key(app: object) -> str:
    """Ask the app's own provider, exactly as the HTTP dependency does."""
    async with TestSessionFactory() as session:
        provider = app.state.secret_provider.with_session(session)
        return await get_zap_api_key(provider, app.state.configuration_provider)


async def test_a_configured_variable_resolves_through_the_dependency() -> None:
    app = create_app(Settings(cap_zap_api_key=CREDENTIAL))
    assert await _resolve_zap_key(app) == CREDENTIAL


async def test_an_unprovisioned_deployment_fails_closed() -> None:
    """No value -> the platform's own error, never a placeholder credential.

    ``SecretNotFound`` maps to 404 with the code SECRET_NOT_FOUND; that status is
    a poor fit for "the server lacks a credential" and is called out in the
    release report as a follow-up, but it is the published behaviour and is
    pinned here so it cannot drift silently.
    """
    app = create_app(Settings())
    with pytest.raises(SecretNotFound) as raised:
        await _resolve_zap_key(app)
    assert raised.value.code == "SECRET_NOT_FOUND"
    assert raised.value.status_code == SecretNotFound.status_code == 404


def test_settings_default_means_not_provisioned() -> None:
    assert Settings().cap_zap_api_key == ""


# -- the deployment side of the contract -------------------------------------


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_forwards_the_credential_to_the_api() -> None:
    """A variable that is only in .env never reaches a compose container.

    Compose lists its environment explicitly, so the pass-through has to appear
    in the service definition; the empty default is deliberate -- unset means
    "not provisioned", which the tests above show fails closed.
    """
    environment = _compose()["services"]["backend"]["environment"]
    assert environment["CAP_ZAP_API_KEY"] == "${CAP_ZAP_API_KEY:-}"


def test_compose_does_not_claim_a_credential_the_worker_cannot_use() -> None:
    """AssessmentService is built as an HTTP dependency, so only the api needs it.

    Listed on the worker too, this would read as "the worker runs ZAP scans",
    which is not how the platform is wired.
    """
    worker = _compose()["services"]["acquisition-worker"]["environment"]
    assert "CAP_ZAP_API_KEY" not in worker


def test_helm_backend_mounts_the_secret_whole() -> None:
    """The chart's mechanism for this credential is an operator-managed Secret.

    backend.yaml uses ``envFrom: secretRef``, so adding CAP_ZAP_API_KEY to the
    Secret named by backend.existingSecret is sufficient -- and values.yaml has
    to say so, or the option is invisible.
    """
    template = (
        REPO_ROOT / "deployment" / "helm" / "cap" / "templates" / "backend.yaml"
    ).read_text(encoding="utf-8")
    assert re.search(r"envFrom:\s*\n\s*- secretRef:", template), template
    values = yaml.safe_load(
        (REPO_ROOT / "deployment" / "helm" / "cap" / "values.yaml").read_text(encoding="utf-8")
    )
    assert values["backend"]["existingSecret"] == "cap-runtime"
    documented = (
        REPO_ROOT / "deployment" / "helm" / "cap" / "values.yaml"
    ).read_text(encoding="utf-8")
    assert "CAP_ZAP_API_KEY" in documented
    # The documented Secret contents must not have lost the resource block.
    assert values["backend"]["resources"]["requests"]["cpu"] == "250m"


def test_env_template_advertises_a_placeholder_and_not_a_secret() -> None:
    """.env.example is copied by operators; its value must be obviously fake."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if line.startswith("CAP_ZAP_API_KEY="))
    value = line.split("=", 1)[1]
    assert value.startswith("replace-with-"), value
    assert Settings().cap_zap_api_key == "", (
        "the template placeholder must never become a code default"
    )


def test_readme_states_which_planes_need_the_credential() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "CAP_ZAP_API_KEY" in readme
    deployment_doc = (
        REPO_ROOT / "docs" / "deployment" / "docker-compose.md"
    ).read_text(encoding="utf-8")
    assert "CAP_ZAP_API_KEY" in deployment_doc, (
        "the compose contract has to be documented where operators configure it"
    )


def test_production_settings_still_reject_placeholder_secrets() -> None:
    """Guard against the fix being read as 'put anything in .env'."""
    with pytest.raises(ValidationError, match="insecure placeholders"):
        Settings(app_environment="production")
