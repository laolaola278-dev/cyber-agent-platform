# Changelog

All notable changes follow Keep a Changelog categories and Semantic Versioning 2.0.0.

## [Unreleased]

## [1.0.2-rc1] - 2026-09-01

Release candidate for the 1.0.2 line, cut to re-earn runtime certification for
the egress tunnel fix.

### Fixed

- Egress proxy consumed only the CONNECT request line, leaving the request's
  remaining headers in the buffer to be piped upstream as tunnel payload.
  TLS through the proxy failed with `WRONG_VERSION_NUMBER` and HTTP origins
  answered `400 malformed HTTP request "Host: ..."`, so every real external
  acquisition returned zero bytes and terminated `BLOCKED`. The headers are
  now drained before the tunnel is established. Present identically in v1.0.0
  and v1.0.1.
- Regression guards: raw-CONNECT tests against a local upstream, plus a
  `network`-marked test that fetches real public HTTPS origins through the
  proxy. The latter runs in the `ci.yml` suite that gates releases — the
  absence of any proxied-fetch test is why this defect survived two releases.

## [1.0.1] - 2026-08-31

GA promotion of 1.0.1-rc1: identical artifacts, promoted version metadata only (pure version bump; no runtime changes since rc1).

## [1.0.1-rc1] - 2026-08-29

Security defaults and capability disclosure patch. v1.0.0 is immutable: no
tag was moved, no image was overwritten, and no historical release was
modified.

### Security

- **Production sandbox admission is now capability-based and fails closed.**
  Admission is decided on the provider's declared capability set
  (`real_isolation` **and** `network` **and** `container|vm` **and**
  `resource`), never on the provider's name alone. Checking `real_isolation`
  by itself is a trap: `SubprocessSandboxProvider` truthfully reports
  `real_isolation = True` (it is a separate OS process) while providing no
  network, filesystem, container or resource isolation at all.
- **Unknown `SANDBOX_PROVIDER` values no longer fall back silently.** In
  v1.0.0 the provider-selection chain ended in an `else` branch that returned
  `MemorySandboxProvider` (zero isolation) for ANY unrecognised name, so a
  misspelled `kubernettes-sandbox` silently downgraded every execution to
  in-process. It now raises `SandboxPolicyViolation` and refuses to start
  rather than falling back to a weaker provider.
- **Production without egress enforcement fails fast.** When the selected
  provider declares network capability, `EGRESS_PROXY_URL` must be set; the
  startup error states the threat model precisely: the application-layer
  validator (`URLPolicyValidator`, layer 1) remains active, but
  defense-in-depth network enforcement is absent. A missing proxy is **not**
  described as "no SSRF protection".
- **The egress proxy is now part of worker readiness.** In production the
  health probe TCP-checks the proxy and fails readiness when it is
  unreachable. There is no direct-egress fallback: under a NetworkPolicy that
  denies all egress except the proxy, an absent proxy means acquisition fails
  loudly instead of leaking traffic.
- Development and test environments may still use a weak provider, but log a
  one-shot startup warning ("not approved for production isolation").

### Added

- Targeted security coverage for the isolation plane
  (`backend/tests/test_phase_28_8_*.py`). The uncovered branches were exactly
  the failure paths: denials, timeouts, cleanup-on-failure and fail-closed
  errors.
  - `app/response/service.py` 47.4% → 98%
  - `app/sandbox/oci_provider.py` 44.9% → 98%
  - `app/sandbox/egress_proxy.py` 65.0% → 95%
  - `app/sandbox/k8s_provider.py` 72.0% → 98%
  - `app/sandbox/production.py` (new) 100%
- `ADR-0037: Production Sandbox and Egress Defaults`.
- CI "Assert production chart defaults" step: the rendered Helm manifest must
  carry `SANDBOX_PROVIDER=kubernetes-sandbox`, an `EGRESS_PROXY_URL` ending in
  `-egress-proxy:8080`, the sandbox NetworkPolicy and a non-development
  `APP_ENVIRONMENT`.

### Fixed

- `KubernetesSandboxProvider` under-declared its capabilities: the Pod spec
  sets `resources.limits/requests` and runs in a container PID namespace, but
  `container`, `process` and `resource` were left at their defaults. A
  capability-based policy that trusted the under-declared provider would have
  rejected the only path Helm ships.
- Helm `worker.egressProxyUrl` defaulted to empty (direct egress). It now
  defaults to the chart's own egress-proxy Service.
- `docker-compose.yml` shipped `APP_ENVIRONMENT=production` for backend and
  acquisition-worker — a local build stack with MinIO development credentials
  that would now trigger the production gates. Default is `development`.
- Zeek TSV ingest produced an unusable error. It now names the supported
  format and the remediation (`LogAscii::use_json=T`, or convert to JSONL
  before ingest) and returns both in `details`.

### Changed

- Version aligned to `1.0.1-rc1` across all 16 version carriers (canonical
  `VERSION`, backend pyproject/uv.lock (PEP 440 `1.0.1rc1`), frontend
  package.json/package-lock, sdk, Helm chart version/appVersion/image tags,
  Dockerfile `ARG VERSION`, runtime `app_version` / `__version__`,
  test_phase_23 `RC_VERSION`).
- Helm chart `artifacthub.io/prerelease` annotation set to `"true"`.
- `GA-GATE 1` version assertion is now rc-policy-generic (any `-rc` version)
  instead of hardcoding the `1.0.0-rc` prefix, so the 40-gate certification
  suite stays reusable for this release line.

### Notes

- **This rc is not yet certified.** 1.0.1-rc1 has not run the 40-gate GA
  certification; the release blocker is recorded in `docs/known-issues.md`.
  The v1.0.0 images and tag are untouched.
- Capability disclosure: the EDR / WAF / Firewall response plugins remain
  `mock_only` (enforced by a model validator), and their action inventory, the
  Zeek JSONL-only limitation and the reserved provider interfaces are now
  documented explicitly rather than implied.

## [1.0.0-rc4] - 2026-08-28

Security re-certification anchor for the v1.0.0 GA release.

### Security

- **CVE-2026-14456** (openssl, HIGH — unbounded QUIC memory growth DoS):
  `frontend/Dockerfile` runtime stage now runs
  `apk upgrade --no-cache libssl3 libcrypto3`, upgrading openssl
  `3.5.7-r0` → fixed `3.5.8-r0` in place at build time. The fixed library
  landed in alpine 3.24 on 2026-08-25, after every published
  `nginx:*-alpine` build, so no base-image tag bump could clear it.

### Fixed

- Removed unused `import pytest` (ruff F401) in
  `backend/tests/test_release_version_consistency.py`, which blocked the
  release workflow quality-gates job.

### Changed

- Version aligned to `1.0.0-rc4` across all 16 version carriers (VERSION,
  backend pyproject/uv.lock, frontend package.json/package-lock, sdk, Helm
  chart version/appVersion/image tags, Dockerfile `ARG VERSION`, runtime
  `app_version` / `__version__`, test_phase_23 `RC_VERSION`).
- Helm chart `artifacthub.io/prerelease` annotation set back to `"true"`
  (this is a pre-release).

### Notes

- The Dockerfile `RUN` fix is runtime-affecting under the fail-closed diff
  classifier, so runtime certification is re-earned on THIS commit (40-gate
  GA certification) rather than inherited from the rc3 anchor `10369e7`.
  The eventual 1.0.0 GA commit will be a pure release-metadata bump from
  this anchor (certification INHERITED).

## [1.0.0] - 2026-08-28

General Availability. Phase 28.7 GA Reliability Certification: **40/40 gates
PASS** under `CAP_GA_STRICT=1`. Runtime certification anchored at commit
`b22b7be57f89cd0ef0cf9df8b289ec1f5e74b2b3` (v1.0.0-rc4, the security
re-certification anchor carrying the CVE-2026-14456 openssl fix) and inherited
by this release (post-cert diff is release-metadata-only, verified by the
automated fail-closed diff classifier).

### Added

- Whole-cluster disaster-recovery certification: real `kind delete cluster`
  destruction, fresh-cluster restore with fail-closed manifest verification.
  Measured RPO = 9.76 s, RTO = 236.75 s.
- 2-hour soak certification (480/480 healthy ticks, 0 HTTP errors, 11
  controlled worker pod kills with only expected crash-recovery reclaims,
  availability 1.0, stable RSS).
- 9-cell capacity matrix and overload/backpressure gates.
- Automated release diff classifier (`scripts/release/classify_diff.py`) and
  release version-consistency gate
  (`backend/tests/test_release_version_consistency.py`).
- GA release notes (`docs/releases/v1.0.0.md`).

### Changed

- Version aligned to `1.0.0` across VERSION, backend pyproject/uv.lock,
  frontend package.json/package-lock, sdk, Helm chart
  (version/appVersion/image tags), Dockerfile `ARG VERSION`, and the runtime
  reported `app_version` / `__version__`.
- Helm chart `artifacthub.io/prerelease` annotation flipped `true` → `false`.
- Corrected the pre-existing `backend/app/__init__.py` `__version__` drift
  (`0.1.0` → `1.0.0`).

### Fixed

- D1 fact conflict resolved: the earlier "per-run lease lacks renewal"
  limitation was fact-checked and found FALSE. The production K8s path renews
  the acquisition run-claim lease every `lease_ttl/3` on a dedicated session
  with fencing. D1 removed from known limitations.

### Security

- **CVE-2026-14456** (openssl, HIGH — unbounded QUIC memory growth DoS)
  fixed: the frontend runtime image upgrades `libssl3`/`libcrypto3`
  `3.5.7-r0` → `3.5.8-r0` in place at build time (carried in from the
  v1.0.0-rc4 re-certification anchor).
- Worker never mounts a container-runtime socket; sandbox execution uses the
  Kubernetes API with namespaced RBAC (Phase 28.6 closure carried into GA).
- Trivy image scans report 0 blocking HIGH/CRITICAL; SBOM (SPDX + CycloneDX)
  and provenance generated for release images.

### Known limitations

- 24-hour soak not yet executed (2-hour soak is the certified baseline).
- SLO candidates are derived, not enforced (require ~30 days production data).
- Cancel vs. terminal-state race (Low severity; cancel API idempotent for
  terminal runs).

## [1.0.0-rc1] - 2026-08-05

### Added

- Release-candidate version policy, release notes, known issues, roadmap, API freeze, security policy, contributor and conduct policies.
- GitHub Actions quality, coverage, build, image, Helm packaging, and baseline security scanning workflows.
- Helm application chart with rolling Deployments, startup/readiness/liveness probes, migration hook, resource controls, PDBs, and external Secret references.
- Single-node, Compose, production checklist, upgrade, rollback, backup/restore, operations, runbook, API, SDK, plugin, v1 documentation index, and FAQ documentation.

### Changed

- Backend, Frontend, SDK, image metadata, and Chart versions aligned to `1.0.0-rc1`.
- Docker builds use multi-stage images; runtime containers are non-root where supported and expose health checks.
- Compose startup order is health-gated and production credentials are mandatory.
- Production configuration rejects repository placeholder secrets and debug mode.

### Security

- Production API documentation defaults off in release deployment assets.
- No Kubernetes Secret values are embedded in the Helm Chart.

### Known limitations

See `docs/known-issues.md`. The API high-concurrency latency budget and environment-gated production tests remain open.
