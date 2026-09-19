# Production Checklist

## How to use this checklist

Each checked item must have an owner, execution date, environment, and evidence link or artifact digest. A static lint/template result is evidence of configuration validity only; it is not evidence that the target PostgreSQL, Redis, Kubernetes, network, capacity, or recovery behavior passed.

## Governance

- [ ] Architect approved the exact commit, tag, image digests, Chart package, SBOM, release notes, and known issues.
- [ ] The published release artifacts (tag, images by digest, chart package, SBOM, notes) are immutable; any change uses a new RC.
- [ ] Phase 22 API latency risk is closed or formally accepted with capacity limits.

## Security

- [ ] TLS is enforced at ingress/gateway; upstream trust and certificate rotation are documented.
- [ ] All independent secrets come from an approved secret manager; no default or repository values remain.
- [ ] Gateway deletes client identity/proxy-secret headers and injects verified values.
- [ ] RBAC deny paths, approval, audit, and retention are tested.
- [ ] API docs are disabled; metrics, database, Redis, Grafana, and admin endpoints are network restricted.
- [ ] Image, filesystem, dependency, secret, and misconfiguration scans meet policy.

## Data and recovery

- [ ] PostgreSQL 16 and Redis 7 target services are supported, encrypted as required, monitored, and capacity tested.
- [ ] Backup completed; restore was tested into an isolated environment; RPO/RTO are approved.
- [ ] Alembic single head is `20260812_0021`; migration round-trip and lock impact are staging-tested.

## Deployment

- [ ] The sandbox images the worker starts are present in a registry the cluster can pull, pinned by digest, and built from the same commit as the backend image (`backend/docker/build_sandbox_images.sh --with-browser`); the chart's default `cap-sandbox-http:latest` / `cap-sandbox-browser:latest` are published by nothing, so a default install fails at the first acquisition.

- [ ] Helm lint/template and cluster server-side dry run pass.
- [ ] Startup/readiness/liveness probes, PDB, rolling update, resource requests/limits, and migration Job pass.
- [ ] External load, restart, rollback, and observability tests pass on target capacity.
- [ ] Operations owns dashboards, alerts, runbook, escalation, and rollback authority.
