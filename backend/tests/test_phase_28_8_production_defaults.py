"""Phase 28.8 (v1.0.1) -- production sandbox and egress fail-closed defaults.

PATCH-GATE 1   Production weak sandbox fails closed
PATCH-GATE 2   Production approved sandbox passes
PATCH-GATE 3   Development weak sandbox allowed + warning
PATCH-GATE 4   Production missing egress enforcement fails closed
PATCH-GATE 8   No production silent fallback to a weaker provider
PATCH-GATE 13  Deployment templates declare sandbox provider + egress

The trap these tests exist for: ``SubprocessSandboxProvider`` declares
``real_isolation = True`` (it genuinely is a separate OS process) while
offering no network, filesystem, container, VM or resource capability. Any
check written as "``real_isolation`` must be true" admits it. Admission is
therefore decided on the capability set, and
``test_subprocess_isolation_claim_is_not_enough`` pins that distinction.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest
import yaml

from app.acquisition import worker_main
from app.config.settings import Settings
from app.sandbox.production import (
    development_sandbox_warning,
    production_startup_error,
    production_violations,
    provider_facts,
    validate_production_egress,
    validate_production_sandbox,
)
from app.sandbox.runtime import MemorySandboxProvider
from app.sandbox.subprocess_provider import SubprocessSandboxProvider

REPO_ROOT = Path(__file__).resolve().parents[2]

# Production settings also reject placeholder secrets, so every production
# fixture has to supply non-placeholder values to reach the sandbox checks.
_PRODUCTION_SECRETS = {
    "secret_key": "a-long-production-secret-value",
    "jwt_secret": "another-long-production-jwt-secret",
    "rbac_trusted_proxy_secret": "a-long-production-proxy-secret",
}


def _settings(environment: str, provider: str, egress: str = "") -> Settings:
    return Settings(
        app_environment=environment,
        sandbox_provider=provider,
        egress_proxy_url=egress,
        **_PRODUCTION_SECRETS,
    )


# --------------------------------------------------------------------------
# PATCH-GATE 1 -- production rejects providers without an isolation domain
# --------------------------------------------------------------------------


def test_subprocess_isolation_claim_is_not_enough() -> None:
    """``real_isolation = True`` alone must not admit subprocess-sandbox."""

    facts = provider_facts()["subprocess-sandbox"]
    assert facts.real_isolation is True, "subprocess really is a separate OS process"
    assert SubprocessSandboxProvider.real_isolation is True
    # ...and it still fails, because the isolation carries no boundary.
    violations = validate_production_sandbox("subprocess-sandbox")
    assert violations, "subprocess-sandbox must be rejected in production"
    joined = " ".join(violations)
    assert "network" in joined
    assert "isolation_domain" in joined
    assert "resource" in joined


@pytest.mark.parametrize("provider", ["memory-sandbox", "subprocess-sandbox"])
def test_production_weak_sandbox_fails_closed(provider: str) -> None:
    violations = production_violations(_settings("production", provider))
    assert violations, f"{provider} must be rejected in production"
    message = production_startup_error(violations)
    assert "production requires an approved isolated sandbox provider" in message
    assert "no fallback" in message


def test_production_unknown_provider_fails_closed() -> None:
    """A typo must never degrade to a weaker provider."""

    for provider in ("kubernettes-sandbox", "", "firecracker-sandbox", "Docker"):
        violations = production_violations(_settings("production", provider))
        assert violations, f"{provider!r} must be rejected in production"
        assert "not known to this build" in " ".join(violations)


# --------------------------------------------------------------------------
# PATCH-GATE 2 -- approved providers are admitted
# --------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["oci-sandbox", "kubernetes-sandbox"])
def test_production_approved_sandbox_passes(provider: str) -> None:
    settings = _settings("production", provider, egress="http://egress-proxy:8080")
    assert production_violations(settings) == []


def test_kubernetes_provider_capabilities_are_declared_honestly() -> None:
    """The K8s Pod IS a container domain with limits; it must say so.

    Under-declaring here would make the capability policy reject the one path
    Helm actually ships.
    """

    facts = provider_facts()["kubernetes-sandbox"]
    caps = facts.capabilities
    assert facts.real_isolation is True
    assert caps.network is True
    assert caps.container is True
    assert caps.resource is True
    # secrets stay fail-closed: never injected into the Pod spec
    assert caps.secret is False


# --------------------------------------------------------------------------
# PATCH-GATE 4 -- production egress enforcement
# --------------------------------------------------------------------------


def test_production_missing_egress_enforcement_fails_closed() -> None:
    settings = _settings("production", "kubernetes-sandbox", egress="")
    violations = production_violations(settings)
    assert len(violations) == 1
    assert "egress enforcement" in violations[0]
    # precise threat model: layer 1 is still active, layer 2 is what is absent
    assert "Application-layer URL validation (layer 1) remains active" in violations[0]
    assert "defense-in-depth network enforcement is absent" in violations[0]
    assert "no SSRF protection" not in violations[0]


def test_production_configured_egress_passes() -> None:
    settings = _settings("production", "oci-sandbox", egress="http://egress-proxy:8080")
    assert validate_production_egress(settings) == []


def test_egress_not_required_for_a_provider_without_network() -> None:
    """A provider that cannot reach the network has no egress to enforce.

    It is already rejected by the sandbox admission check; reporting an egress
    violation too would bury the actionable message.
    """

    settings = _settings("production", "memory-sandbox", egress="")
    assert validate_production_egress(settings) == []


# --------------------------------------------------------------------------
# PATCH-GATE 3 -- development/test keep working, with one startup warning
# --------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["development", "test"])
def test_non_production_allows_weak_sandbox(environment: str) -> None:
    settings = _settings(environment, "subprocess-sandbox")
    assert production_violations(settings) == []
    assert development_sandbox_warning(settings) is not None


def test_development_memory_sandbox_allowed_with_warning() -> None:
    settings = _settings("test", "memory-sandbox")
    assert production_violations(settings) == []
    warning = development_sandbox_warning(settings)
    assert warning is not None
    assert "memory-sandbox" in warning
    assert "Not approved for production isolation" in warning


def test_development_warning_is_not_emitted_for_approved_providers() -> None:
    settings = _settings("development", "oci-sandbox", egress="http://egress-proxy:8080")
    assert development_sandbox_warning(settings) is None


def test_production_never_emits_the_development_warning() -> None:
    """Production fails closed; it does not warn and continue."""

    settings = _settings("production", "memory-sandbox")
    assert development_sandbox_warning(settings) is None
    assert production_violations(settings)


def test_default_settings_stay_development_compatible() -> None:
    """A v1.0.0 developer workflow must not break on upgrade.

    The shipped defaults are development + subprocess-sandbox. That is still
    allowed; only production is tightened.
    """

    settings = Settings(app_environment="development", sandbox_provider="subprocess-sandbox")
    assert production_violations(settings) == []
    assert development_sandbox_warning(settings) is not None


# --------------------------------------------------------------------------
# PATCH-GATE 8 -- no silent fallback to a weaker provider
# --------------------------------------------------------------------------


def _provider_selection_chain() -> ast.If:
    """Return the ``if/elif`` node that assigns ``network_runtime``."""

    source = textwrap.dedent(inspect.getsource(worker_main._amain))
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.If):
            continue
        assigns_network_runtime = any(
            isinstance(stmt, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "network_runtime"
                for target in stmt.targets
            )
            for stmt in node.body
        )
        if assigns_network_runtime:
            return node
    raise AssertionError("sandbox provider selection chain not found")


def test_unknown_provider_has_no_silent_fallback() -> None:
    """The catch-all branch must raise, not start MemorySandboxProvider.

    Through v1.0.0 the provider chain ended in a bare ``else`` that started
    ``MemorySandboxProvider`` -- zero isolation -- for ANY unrecognised name,
    so a typo silently degraded production.
    """

    node = _provider_selection_chain()
    # every tested branch compares against a literal provider name
    tested_names: list[str] = []
    while True:
        tested_names.extend(
            constant.value
            for constant in ast.walk(node.test)
            if isinstance(constant, ast.Constant) and isinstance(constant.value, str)
        )
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            node = node.orelse[0]
            continue
        break
    assert "memory-sandbox" in tested_names, (
        "memory-sandbox must be an explicit opt-in branch"
    )
    assert "subprocess-sandbox" in tested_names

    # the catch-all raises instead of degrading to a weaker provider
    catch_all = node.orelse
    assert len(catch_all) == 1 and isinstance(catch_all[0], ast.Raise), (
        "the provider catch-all must raise; assigning a provider here is a "
        "silent fallback to a weaker isolation domain"
    )
    assert "SandboxPolicyViolation" in ast.unparse(catch_all[0])


def test_memory_provider_remains_available_for_orchestration() -> None:
    """The orchestration carrier is unchanged -- only layer 1 is gated."""

    assert MemorySandboxProvider.provider_name == "memory-sandbox"
    assert MemorySandboxProvider.real_isolation is False


# --------------------------------------------------------------------------
# PATCH-GATE 13 -- deployment templates must be explicit
# --------------------------------------------------------------------------


def _helm_values() -> dict:
    path = REPO_ROOT / "deployment/helm/cap/values.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_helm_production_template_declares_sandbox_provider() -> None:
    sandbox = _helm_values()["worker"]["sandbox"]
    assert sandbox["provider"] == "kubernetes-sandbox"


def test_helm_worker_template_defaults_egress_to_in_chart_service() -> None:
    """An empty values override must not render an empty EGRESS_PROXY_URL."""

    template = (REPO_ROOT / "deployment/helm/cap/templates/worker.yaml").read_text(
        encoding="utf-8"
    )
    assert "EGRESS_PROXY_URL" in template
    # the default is generated from the Service this chart actually creates
    assert "-egress-proxy:8080" in template
    assert 'include "cap.fullname"' in template


def test_helm_chart_declares_egress_proxy_service() -> None:
    """The default address above must reference a Service that exists."""

    egress = (REPO_ROOT / "deployment/helm/cap/templates/egress-proxy.yaml").read_text(
        encoding="utf-8"
    )
    assert "kind: Service" in egress
    assert '{{ include "cap.fullname" . }}-egress-proxy' in egress
    assert "port: 8080" in egress


def test_helm_sandbox_networkpolicy_is_declared() -> None:
    policy = (REPO_ROOT / "deployment/helm/cap/templates/networkpolicy.yaml").read_text(
        encoding="utf-8"
    )
    # default deny egress in the sandbox namespace is the layer-2 enforcement
    assert "cap-sandbox-default-deny-egress" in policy
    assert "cap-sandbox-allow-dns-and-egress-proxy" in policy


def test_compose_declares_its_environment_explicitly() -> None:
    """Compose builds images locally with dev credentials: it is development.

    v1.0.0 declared ``production`` while defaulting to oci-sandbox, which read
    as a production claim nobody had validated.
    """

    services = _compose()["services"]
    for name in ("backend", "acquisition-worker"):
        environment = services[name]["environment"]
        assert "APP_ENVIRONMENT" in environment, f"{name} must declare APP_ENVIRONMENT"
        assert "development" in str(environment["APP_ENVIRONMENT"]), (
            f"{name} must default to development"
        )


def test_compose_worker_declares_sandbox_and_egress() -> None:
    environment = _compose()["services"]["acquisition-worker"]["environment"]
    assert "SANDBOX_PROVIDER" in environment
    assert "oci-sandbox" in str(environment["SANDBOX_PROVIDER"])
    assert "EGRESS_PROXY_URL" in environment
    assert str(environment["EGRESS_PROXY_URL"]).strip(), "compose must not default to direct egress"
