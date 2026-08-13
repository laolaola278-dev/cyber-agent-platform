# CAP v1 Runbook

## Backend not live

Check container/Pod status, startup probe events, logs, configuration validation errors, and resource limits. Do not point liveness at external dependencies.

## Readiness failing

Check PostgreSQL DNS, credentials, TLS policy, connection limits, migration Job, and current Alembic head `20260803_0018`. Keep the Pod out of service until `/ready` succeeds.

## Authentication failures

For 401, verify the gateway overwrites and injects the configured identity and proxy-secret headers. For 403, inspect role permissions and audit logs. Never bypass Backend authorization through frontend changes.

## Queue or Worker degradation

Inspect queue depth, heartbeat age, lease expiry, active/capacity ratio, execution retries, fencing tokens, and Sandbox timeout/termination evidence. Recover through supported state transitions rather than direct database edits.

## Elevated latency or 5xx

Correlate Prometheus route templates with Trace IDs and structured logs. Check PostgreSQL pool/locks, event-loop saturation, log exporter pressure, and external Providers. Phase 22 shows API high-concurrency latency is an open risk.

## Failed release

Stop promotion, preserve logs/events, use Helm rollback or restore the previous immutable Compose image set, verify readiness and smoke tests, and restore the database only when schema/data compatibility requires it. Follow `docs/deployment/rollback.md`.

## Evidence to preserve

For every production incident or failed gate, retain the release version and image/Chart digests, deployment revision, migration head and Job logs, `/health` and `/ready` responses, relevant Trace IDs, Prometheus query results, Worker/queue state, audit events, and the exact command plus environment used for the test. Redact credentials and tokens before sharing evidence.
