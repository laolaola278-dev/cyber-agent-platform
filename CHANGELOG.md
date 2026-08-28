# Changelog

All notable changes follow Keep a Changelog categories and Semantic Versioning 2.0.0.

## [Unreleased]

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
