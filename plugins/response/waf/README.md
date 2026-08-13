# WAF Response Plugin

## Scope

`waf-response` is the first CAP Response Plugin that models a WAF rule lifecycle. It is deliberately **mock-only** in Phase 16. The plugin has no HTTP client, no credentials, no shell execution path, no filesystem write path, and no production WAF integration.

## Capability and lifecycle

- Capability: `response.waf`
- Runtime-only lifecycle: `initialize -> plan -> validate -> execute -> verify -> shutdown`
- Rollback lifecycle: `initialize -> validate -> rollback -> verify -> shutdown`
- Required permissions: `response.execute`, `response.verify`, `response.rollback`
- Approval: mandatory for all WAF rule changes.

## Rule contract

Each declarative `WAFRule` contains `id`, `name`, `action`, `condition`, `priority`, `version`, `status`, `source`, and a deterministic SHA-256 `checksum`. Conditions use a limited `field:value` grammar. Control syntax, templates, newline injection, shell-like syntax and provider-owned identifiers are rejected.

## Safety controls

1. The policy permits only mock execution, allowlisted sources and condition fields.
2. Phase 16 allows `BLOCK` and `LOG`; broad `ALLOW` rules are prohibited.
3. Every planned change is bound to immutable Incident and Asset scope by the existing Response Runtime.
4. The plugin returns a receipt with rule checksum, provider reference, operation and affected asset IDs; the framework persists it as Evidence and Audit events.
5. Verification reads the Mock Provider state and compares the complete expected rule or rollback state.
6. Rollback is limited to `REMOVE`, `DISABLE`, or `RESTORE`; the execution-issued token binds the rollback to the Plan, Incident, rule version and checksum.

## Provider boundary

`MockWAFProvider` is an in-memory deterministic store. It advertises `network_access: false` and `production_access: false`; `health()` is false if either boundary changes. Real WAF adapters are explicitly out of scope for Phase 16.
