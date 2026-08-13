# ADR-0031: Make Approval a Platform Capability over Response Plans

- Status: Accepted
- Date: 2026-08-01
- Phase: 14

## Context

Blocking, isolation, firewall, WAF and EDR actions can disrupt production. Approving a plugin is too
coarse: the same plugin may be safe for notification but dangerous for isolation, and each action has a
different Incident, Asset, parameter and time scope. Plugin-owned approval could approve its own work
and bypass separation of duties.

## Decision

The approval object is the immutable `ResponsePlan`, not the plugin. The platform owns the state
machine:

```text
DRAFT -> PENDING_APPROVAL -> APPROVED | REJECTED | EXPIRED
APPROVED -> EXECUTED -> ROLLED_BACK
```

Approval records include approver, decision, comment, level, decision time and expiry. Policy controls
required levels, TTL and whether requester and approver must differ. Runtime execution requires an
approved plan and enforces its captured Incident, Asset, capability, plugin and parameter scope.

## Consequences

- Positive: approval is specific, reviewable, expiring and auditable.
- Positive: plugins cannot approve themselves or alter approved parameters.
- Positive: multi-level approval and external identity integration can evolve independently.
- Positive: policy decisions and runtime enforcement remain separate.
- Trade-off: Phase 14 identifies actors by validated strings; identity/RBAC federation remains future work.
- Trade-off: expired and rejected plans require a new plan rather than silent reuse.
