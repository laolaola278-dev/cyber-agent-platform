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

- [ ] PostgreSQL 16 is supported, encrypted as required, monitored, and capacity tested. **Redis is not on any v1 request path** -- no client exists in `backend/app`; `settings.redis_url` feeds only a readiness field that reports whether a URL string was set. Deploy Redis solely if a planned event plane needs it, and do not treat a running Redis as evidence of CAP health.
- [ ] Backup completed; restore was tested into an isolated environment; RPO/RTO are approved.
- [ ] Alembic single head is `20260812_0021`; migration round-trip and lock impact are staging-tested.

## Deployment

- [ ] The sandbox and egress images the worker starts resolve to release artifacts, pinned by digest where possible: `release.yml` publishes `cap-sandbox-http`, `cap-sandbox-browser` and `cap-egress-proxy` under the release version (their digests are in `release-images-<version>.json`), and a self-built deployment can produce the same bytes with `CAP_SANDBOX_IMAGE_TAG=<version> bash backend/docker/build_sandbox_images.sh --with-browser`. The chart no longer defaults to any `:latest` coordinate (F-7); K8S-GATE 34 fails a deployment whose image set is not the released set.
- [ ] The install is pinned to the certified bytes, not just to a version: install the chart with the `values-release-<version>.yaml` the release attaches, which sets `repository`, `tag` and `index_digest` for every image coordinate the chart declares -- all six, `worker.image` included. Without that file the chart's `repository` defaults (`ghcr.io/example/…`) are placeholders and must be overridden by hand.

- [ ] Helm lint/template and cluster server-side dry run pass.
- [ ] Startup/readiness/liveness probes, PDB, rolling update, resource requests/limits, and migration Job pass.
- [ ] External load, restart, rollback, and observability tests pass on target capacity.
- [ ] Operations owns dashboards, alerts, runbook, escalation, and rollback authority.
