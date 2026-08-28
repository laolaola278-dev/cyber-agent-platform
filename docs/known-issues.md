# Known Issues for 1.0.0 (GA)

> Supersedes the `1.0.0-rc1` known-issues set. Items closed by Phase 28.6
> (docker.sock elimination) and the Phase 28.7 D1 fact-check are removed.

## Release blockers

None. Phase 28.7 GA Reliability Certification passed 40/40 gates under
`CAP_GA_STRICT=1` at certified commit `b22b7be57f89cd0ef0cf9df8b289ec1f5e74b2b3`
(v1.0.0-rc4 — the security re-certification anchor carrying the CVE-2026-14456
openssl fix). This release's runtime certification is inherited from that anchor;
the GA commit `0240fbe` is a pure release-metadata bump classified
`release_metadata_only=true` by the fail-closed diff classifier.

## Operational limitations

- Identity is supplied by a trusted reverse proxy; CAP does not provide an OIDC login implementation. Production gateways must overwrite identity headers.
- User/Role/Permission directories are immutable in v1; there is no user-management write API.
- OpenTelemetry spans are not exported when `OTEL_EXPORTER_ENDPOINT` is empty.
- Metrics and API docs are public application paths; production networks must restrict metrics, while API docs default to disabled.
- The Web Console defaults to `read-only` for local Compose. It is not a production identity solution.
- Frontend bundles still produce size warnings; this is not a correctness defect.

## GA known limitations (carried from the Phase 28.7 readiness report)

1. **24-hour soak not yet executed.** The certified soak ran for 2 hours
   (7,200 s): 0 false reclaims on healthy runs, 11 controlled worker pod kills
   with only expected crash-recovery reclaims, availability 1.0, stable RSS. A
   24-hour soak is future work for deeper confidence on long-tail memory leaks
   and run-reclaim behavior.
2. **SLO candidates are not enforced SLOs.** They are derived from a single
   2-hour soak + one DR cycle. Promotion to production SLOs requires ~30 days
   of production data and a product decision.
3. **Cancel vs. terminal-state race (Low).** A restrictive policy can finalize
   a run as BLOCKED before a cancel request lands; the cancel API is idempotent
   for already-terminal runs.

## Closed items (do NOT re-list)

- **docker.sock mounted in worker** — CLOSED in Phase 28.6. The worker never
  mounts a container-runtime socket; sandbox execution uses the Kubernetes API
  with namespaced RBAC to short-lived Pods in `cap-sandbox`.
- **D1 "per-run lease lacks renewal"** — RESOLVED FALSE by the Phase 28.7
  fact-check. The production K8s path renews the acquisition run-claim lease
  every `lease_ttl/3` on a dedicated session with fencing. See
  `outputs/cap-cert-ga/D1-FACT-CONFLICT-RESOLUTION.md`.
