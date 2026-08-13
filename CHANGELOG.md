# Changelog

All notable changes follow Keep a Changelog categories and Semantic Versioning 2.0.0.

## [Unreleased]

No new feature development is permitted while `1.0.0-rc1` is under Architect Review.

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
