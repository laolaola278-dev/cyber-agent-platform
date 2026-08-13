# Phase 23 GitHub Reference Analysis

## Scope and sources

This analysis uses official Kubernetes, Helm, Docker, GitHub Actions, and Semantic Versioning documentation. It defines release-engineering rules only and introduces no CAP business capability.

## Kubernetes

Official Deployment guidance treats Pods as replaceable and uses a Deployment for controlled ReplicaSet rollout and rollback. CAP therefore uses `RollingUpdate`, `maxUnavailable: 0`, `maxSurge: 1`, rollout progress deadlines, revision history, and PodDisruptionBudgets.

Probe semantics are separated:

- Startup probes allow slow initialization without premature liveness failure.
- Readiness probes decide whether a Pod receives traffic; CAP `/ready` verifies database access.
- Liveness probes detect a process that should be restarted; CAP `/health` does not depend on external services.

Source: https://kubernetes.io/docs/concepts/workloads/controllers/deployment/ and https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#container-probes

## Helm

A Helm application Chart requires `Chart.yaml`, a SemVer `version`, and default `values.yaml`; `appVersion` is informational and independent from Chart version. CAP aligns both to `1.0.0-rc1` for this RC and validates user values with `values.schema.json`.

Upgrade operations must explicitly select values and wait for workloads/jobs. CAP documents `--wait --wait-for-jobs --rollback-on-failure --timeout 10m`; secret values are never rendered into the Chart. Migration is a pre-install/pre-upgrade Job and must complete before rollout.

Source: https://helm.sh/docs/topics/charts/ and https://helm.sh/docs/helm/helm_upgrade/

## Docker

Docker recommends small trusted bases, multi-stage builds, minimal runtime content, repeatable CI builds, `.dockerignore`, non-root execution when possible, image metadata, regular rebuilds, and vulnerability scanning. CAP uses multi-stage Backend/Frontend images, locked dependency installation, OCI labels, health checks, and CI image scanning.

Compose starts dependencies in order but does not imply readiness unless `condition: service_healthy` is used. CAP gates Backend on PostgreSQL/Redis health and Frontend on Backend readiness.

Source: https://docs.docker.com/build/building/best-practices/ and https://docs.docker.com/compose/how-tos/startup-order/

## GitHub Actions

Official guidance recommends explicit runtime setup, lock-based dependency installation, caching, lint/test/build commands identical to local commands, coverage artifacts, and least-privilege workflow permissions. CAP separates pull-request CI from tag-triggered RC packaging. CI covers Ruff, pytest, 95% coverage, TypeScript, ESLint, Vite, Compose, Helm, Docker builds, npm audit, and Trivy.

Source: https://docs.github.com/en/actions/use-cases-and-examples/building-and-testing/building-and-testing-python

## Semantic Versioning

SemVer requires a declared public API. `1.0.0-rc1` is a pre-release with lower precedence than `1.0.0` and may not satisfy final compatibility guarantees. Once an RC artifact is published, its contents must not be modified; fixes require `rc2` or another version. After final `1.0.0`, compatible fixes increment PATCH, compatible additions increment MINOR, and incompatible public API changes increment MAJOR.

Source: https://semver.org/

## CAP decisions

1. The v1 OpenAPI surface and documented SDK/Plugin contracts are the public API.
2. No business feature, Plugin, model, Migration, or Platform Plane is added in Phase 23.
3. RC artifacts are immutable and identified by tag, image tag/digest, Chart version, revision, SBOM, and release notes.
4. Production certification requires evidence from the target PostgreSQL/Redis/Kubernetes environment; local SQLite or static rendering cannot substitute.
5. Phase 22 API latency failures remain a production release blocker until independently retested and accepted.
