# Rollback Guide

## Trigger

Rollback on failed migration, readiness/rollout timeout, elevated errors/latency, authorization regression, audit loss, queue/Worker instability, or failed critical smoke tests.

## Procedure

1. Freeze high-impact operations and preserve logs, events, metrics, Trace IDs, and release metadata.
2. Inspect Helm history and select the last approved revision.
3. Execute and wait:

```bash
helm history cap -n cap
helm rollback cap PREVIOUS_REVISION -n cap --wait --timeout 10m
```

4. Verify Backend/Frontend rollout, `/health`, `/ready`, read/write smoke, RBAC deny paths, audit, queue, Worker, metrics, and critical workflows.
5. If schema/data is incompatible with the previous application, follow the release-specific migration decision. Restore the verified pre-upgrade backup only after stopping writers and obtaining incident authority.
6. Document the incident and issue a new RC. Never rewrite the failed release artifacts.

Do not delete volumes or use destructive database reset as rollback.
