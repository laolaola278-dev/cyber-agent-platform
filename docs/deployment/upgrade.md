# Upgrade Guide

1. Review the target CHANGELOG, release notes, known issues, API compatibility, Chart values diff, and database migration.
2. Freeze high-impact Response and Playbook operations.
3. Complete and verify a database backup.
4. Validate in staging using production-like PostgreSQL, Redis, gateway, capacity, and observability.
5. Pin the target Chart and image digests. Do not use `latest`.
6. Run Helm lint/template and a server-side dry run without exposing rendered Secrets.
7. Upgrade with waiting and automatic rollback behavior:

```bash
helm upgrade --install cap deployment/helm/cap \
  --namespace cap \
  --values production-values.yaml \
  --wait --wait-for-jobs --rollback-on-failure --timeout 10m
```

8. Confirm the migration Job, rollout status, `/ready`, RBAC deny paths, audit, metrics, Trace correlation, Worker/queue health, and critical user workflows.
9. Observe the agreed soak window before unfreezing operations.

Avoid blind `--reuse-values`; review the final merged values against the new schema.

## 1.0.1: sandbox and egress startup gates

This release changes startup behavior and **can refuse to start the
acquisition worker**. See ADR-0037 for the full rationale.

Before upgrading, confirm both of the following on every production worker:

1. `SANDBOX_PROVIDER` names an approved isolated provider
   (`kubernetes-sandbox` for Helm, `oci-sandbox` for a real container
   runtime). An unrecognised name is now a hard failure. In 1.0.0 it silently
   fell back to `MemorySandboxProvider` (zero isolation) — audit any evidence
   collected under such a configuration, because it was produced with no
   isolation at all.
2. `EGRESS_PROXY_URL` is set whenever the provider has network capability
   (both approved providers do). Helm defaults this to the chart's own
   egress-proxy Service; bare-metal deployments must set it explicitly.

After the rollout, the worker's readiness output must report
`egress_enforcement: true`. If it reports `false`, the worker will not acquire
reliably: the sandbox NetworkPolicy denies all egress except the proxy, so an
unproxied acquisition fails rather than leaking traffic.

Development and test environments are unaffected — a weak provider is still
allowed there and logs a one-shot warning. `docker-compose.yml` now defaults
`APP_ENVIRONMENT` to `development` for this reason.
