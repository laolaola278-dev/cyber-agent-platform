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
