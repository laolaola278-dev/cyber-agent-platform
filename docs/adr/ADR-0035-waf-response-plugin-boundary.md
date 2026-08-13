# ADR-0035: Keep WAF response provider-neutral and mock-only in Phase 16

## Status

Accepted for Phase 16; subject to Architect Review.

## Context

CAP must prove that the existing Response Framework can govern a WAF rule lifecycle without modifying framework internals or connecting to a production WAF. WAF products expose incompatible representations: SecLang rules and anomaly scoring, transaction engines, policy documents, policy decisions, or declarative cluster resources.

## Decision

Introduce a provider-neutral `WAFRule`, an allowlist-based `WAFPolicyProvider`, a `WAFAdapter`, an application-scoped in-memory `MockWAFProvider`, and a `WAFResponsePlugin` implementing the existing Response SDK.

The provider cannot use network, credentials, processes, filesystem or database. All WAF changes require existing Response approval. Verification is provider state read-back. Rollback is restricted to `REMOVE`, `DISABLE`, or `RESTORE` and requires a token bound to the original Plan and rule checksum.

## Consequences

- The existing Response Framework remains unchanged and reusable.
- Product-specific syntax is isolated behind the Adapter boundary.
- Phase 16 can prove Evidence, Audit, Approval, Verification and Rollback without production risk.
- Mock state is intentionally ephemeral and application-local; restart recovery and distributed concurrency are out of scope.
- A production provider requires a future ADR covering credentials, network policy, staged deployment, reconciliation, locking and compensation.
