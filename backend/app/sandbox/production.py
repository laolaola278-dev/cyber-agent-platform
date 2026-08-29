"""Phase 28.8 (v1.0.1) -- capability-based production sandbox and egress admission.

Why this module exists
----------------------
Through v1.0.0 the acquisition worker picked its sandbox provider with a chain
of ``if settings.sandbox_provider == "<name>"`` branches and started whatever
it found. Two consequences were fail-open:

* A host that never set ``SANDBOX_PROVIDER`` silently received
  ``subprocess-sandbox``. That provider declares ``real_isolation = True``
  (it really is a separate OS process) while offering **no** network boundary,
  **no** filesystem jail and **no** resource limits -- so a check written as
  "``real_isolation`` must be true" would have admitted it. The isolation
  claim and the capability set have to be read together.
* An empty ``EGRESS_PROXY_URL`` produced a single INFO line and then direct
  sandbox egress.

v1.0.1 replaces the name chain with a capability check. Admission is decided
by *what a provider proves about itself* through
:class:`~app.sandbox.runtime.SandboxProviderCapability`, so a provider added in
a later phase is admitted or rejected by its own declaration -- no scattered
``if name == ...`` edits, and an unknown name fails closed rather than passing.

Two-layer egress (precise threat model)
---------------------------------------
Layer 1 -- :mod:`app.acquisition.urlpolicy` (``URLPolicyValidator``) **always**
runs, in every environment, with or without a proxy. It validates scheme,
hostname, loopback/RFC1918/link-local/metadata addresses, DNS results and
every redirect target.

Layer 2 -- network enforcement: the sandbox's only route out is the controlled
egress proxy (OCI: isolated bridge network; Kubernetes: NetworkPolicy
default-deny egress). This is defense-in-depth.

An empty ``EGRESS_PROXY_URL`` therefore does **not** mean "no SSRF
protection". It means: *application-layer URL validation remains active, but
defense-in-depth network enforcement is absent.* The concrete consequence
differs by provider, which is why both are refused in production:

* OCI -- the container egresses directly to the Internet. Layer 1 still runs,
  so a private-IP probe is still rejected by the validator, but a layer-1
  bypass reaches the network unchallenged.
* Kubernetes -- the sandbox NetworkPolicy allows egress only to DNS and the
  egress proxy, so without a proxy address the Pod has no HTTP egress at all.
  Nothing leaks, but every network acquisition fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.sandbox.runtime import SandboxProviderCapability

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from app.config.settings import Settings


@dataclass(frozen=True, slots=True)
class SandboxProviderFacts:
    """The admission-relevant facts about one sandbox provider."""

    name: str
    real_isolation: bool
    capabilities: SandboxProviderCapability


def is_production(settings: Settings) -> bool:
    """True when the process is running in the production environment."""

    return settings.app_environment.casefold() == "production"


def provider_facts() -> dict[str, SandboxProviderFacts]:
    """Return the provider registry, built from the provider classes themselves.

    The imports are deferred: :mod:`app.config.settings` validates through this
    module, and importing Kubernetes/container clients at settings time would
    add import cost to every process that only needs to read configuration.

    The registry is derived from each class's own ``provider_name`` /
    ``real_isolation`` / ``capabilities`` attributes, so a new provider is
    registered by writing one entry here -- and its admission follows from the
    capabilities it declares rather than from a second hardcoded list.
    """

    from app.sandbox.k8s_provider import KubernetesSandboxProvider
    from app.sandbox.oci_provider import OCISandboxProvider
    from app.sandbox.runtime import MemorySandboxProvider
    from app.sandbox.subprocess_provider import SubprocessSandboxProvider

    classes = (
        MemorySandboxProvider,
        SubprocessSandboxProvider,
        OCISandboxProvider,
        KubernetesSandboxProvider,
    )
    return {
        cls.provider_name: SandboxProviderFacts(
            name=cls.provider_name,
            real_isolation=bool(cls.real_isolation),
            capabilities=cls.capabilities,
        )
        for cls in classes
    }


def missing_production_capabilities(facts: SandboxProviderFacts) -> list[str]:
    """Return the production requirements ``facts`` fails to satisfy.

    Empty list means the provider is admitted. ``real_isolation`` alone is not
    sufficient: ``subprocess-sandbox`` sets it while declaring no network,
    container, VM or resource capability, and it is exactly that gap this
    function closes.
    """

    caps = facts.capabilities
    missing: list[str] = []
    if not facts.real_isolation:
        missing.append("real_isolation: provider shares the host execution domain")
    if not caps.network:
        missing.append(
            "network: no OS/network-level egress enforcement, so layer-2 SSRF "
            "defence cannot be applied"
        )
    if not (caps.container or caps.vm):
        missing.append("isolation_domain: neither a container nor a microVM boundary")
    if not caps.resource:
        missing.append("resource: no CPU/memory limits inside an isolation domain")
    return missing


def validate_production_sandbox(provider_name: str) -> list[str]:
    """Return the production sandbox violations for ``provider_name``.

    An unrecognised name is a violation, not a pass: production must never
    start on a provider this build cannot vet.
    """

    raw = (provider_name or "").strip()
    facts = provider_facts().get(raw.casefold())
    if facts is None:
        return [
            f"sandbox provider {raw!r} is not known to this build; production "
            "requires an approved isolated sandbox provider"
        ]
    return missing_production_capabilities(facts)


def validate_production_egress(settings: Settings) -> list[str]:
    """Return the production egress violations for ``settings``.

    Only a provider that can reach the network has egress to enforce. A
    provider that already fails sandbox admission is not reported twice -- the
    sandbox violation is the actionable one.
    """

    raw = (settings.sandbox_provider or "").strip()
    facts = provider_facts().get(raw.casefold())
    if facts is None or not facts.capabilities.network:
        return []
    if (settings.egress_proxy_url or "").strip():
        return []
    return [
        "egress enforcement: EGRESS_PROXY_URL is empty, so sandbox traffic is "
        "not routed through the controlled egress proxy. Application-layer URL "
        "validation (layer 1) remains active, but defense-in-depth network "
        f"enforcement is absent for provider {facts.name}."
    ]


def production_violations(settings: Settings) -> list[str]:
    """Every production violation for ``settings``; empty means admitted."""

    if not is_production(settings):
        return []
    violations = validate_production_sandbox(settings.sandbox_provider)
    violations.extend(validate_production_egress(settings))
    return violations


def production_startup_error(violations: list[str]) -> str:
    """Render a fail-fast startup message. There is no weaker fallback."""

    details = "\n".join(f"  - {item}" for item in violations)
    return (
        "production requires an approved isolated sandbox provider; refusing to "
        "start (no fallback to a weaker provider):\n" + details
    )


def development_sandbox_warning(settings: Settings) -> str | None:
    """One-shot warning for a non-production process on an unapproved provider.

    Returns ``None`` in production (which fails closed instead) and ``None``
    when the selected provider already satisfies the production capability
    bar. Emitted once at startup, never per tick.
    """

    if is_production(settings):
        return None
    raw = (settings.sandbox_provider or "").strip()
    facts = provider_facts().get(raw.casefold())
    if facts is None or not missing_production_capabilities(facts):
        return None
    return (
        f"Development sandbox provider in use: {facts.name}. "
        "Not approved for production isolation."
    )
