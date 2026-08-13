# ADR-0030: Keep Response as an Independent Platform Domain

- Status: Accepted
- Date: 2026-08-01
- Phase: 14

## Context

Incident owns investigation lifecycle, Asset owns inventory truth, and Report owns presentation. A
response action can change external infrastructure and therefore needs planning, approval, execution,
verification, evidence and rollback semantics that do not belong in any of those aggregates.

## Decision

CAP introduces an independent Response bounded context. A `ResponsePlan` references an existing
Incident and one or more Assets, but neither the framework nor a plugin mutates those records. Plugins
receive a minimal read-only context and return only `ResponseResult`. Only `ResponseRuntime` invokes
plugin lifecycle methods.

```text
Incident/Asset references -> Planner -> Policy -> Approval -> Runtime -> Plugin
                                            -> Result -> Verify -> Evidence/Audit
```

## Consequences

- Positive: external vendors remain plugins instead of becoming platform domains.
- Positive: Incident, Asset, Evidence and Report ownership boundaries remain intact.
- Positive: response state, failure and rollback are independently auditable.
- Trade-off: additional persistence tables and cross-domain references are required.
- Trade-off: report projection and Incident timeline integration remain explicit application services.
