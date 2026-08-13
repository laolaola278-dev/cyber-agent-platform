# ADR-0032: Build Rollback into the Response Framework

- Status: Accepted
- Date: 2026-08-01
- Phase: 14

## Context

Security response can create false blocks, isolate the wrong endpoint or deploy an unsafe rule. A
best-effort compensating script added after execution cannot reliably prove which action it reverses,
which credentials it used, or whether restoration succeeded. Wazuh stateful Active Response also
shows that reverting a time-bounded action is part of the response lifecycle rather than unrelated
cleanup.

## Decision

Every Response Plugin must explicitly declare `supports_rollback`. A rollback-capable execution must
return an opaque rollback token, stored only in `ResponseExecution` and excluded from public result
JSON. Rollback is invoked only through `RollbackService` and `ResponseRuntime`, and produces an
independent `ResponseRollback`, verification, evidence and audit trail.

```text
verified execution
  -> opaque rollback token
  -> rollback request
  -> runtime scope/permission checks
  -> plugin.rollback()
  -> plugin.verify()
  -> rollback evidence + audit
```

Plugins that do not support rollback are marked `NOT_SUPPORTED` and fail closed when rollback is
requested.

## Consequences

- Positive: reversibility is visible before approval and execution.
- Positive: rollback has its own result, verification and evidence lineage.
- Positive: opaque tokens are not exposed through the public API.
- Positive: non-reversible actions cannot falsely imply recoverability.
- Trade-off: a plugin must safely retain or encode provider-specific restoration context.
- Trade-off: rollback is compensating behavior, not a guarantee that every external side effect is atomic.
