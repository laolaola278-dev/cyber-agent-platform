# Cyber Agent Platform (CAP) 1.0.0-rc1

[![CI](https://github.com/laolaola278-dev/cyber-agent-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/laolaola278-dev/cyber-agent-platform/actions/workflows/ci.yml)
[![Linux Certification](https://github.com/laolaola278-dev/cyber-agent-platform/actions/workflows/cap-linux-certification.yml/badge.svg)](https://github.com/laolaola278-dev/cyber-agent-platform/actions/workflows/cap-linux-certification.yml)
[![Release](https://github.com/laolaola278-dev/cyber-agent-platform/actions/workflows/release.yml/badge.svg)](https://github.com/laolaola278-dev/cyber-agent-platform/actions/workflows/release.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](backend/pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](backend/pyproject.toml)

Cyber Agent Platform is an enterprise security-orchestration control plane that governs Asset, Knowledge, Assessment, Detection, Incident, Response, Notification, Worker, Sandbox, Telemetry, Plugin, and Playbook capabilities through stable interfaces, RBAC, approval, audit, and observability.

## Release status

`1.0.0-rc1` is feature- and API-frozen for Architect Review. Phase 23 permits only bug fixes, release engineering, production certification, documentation, packaging, and deployment. The RC is not an unconditional production certification: Phase 22 high-concurrency API latency and target-environment tests remain open Production Entry Gates.

- Release notes: [`docs/releases/v1.0.0-rc1.md`](docs/releases/v1.0.0-rc1.md)
- Changelog: [`CHANGELOG.md`](CHANGELOG.md)
- Known issues: [`docs/known-issues.md`](docs/known-issues.md)
- Roadmap: [`docs/roadmap.md`](docs/roadmap.md)
- API freeze: [`docs/api-freeze-v1.md`](docs/api-freeze-v1.md)
- Documentation index: [`docs/v1-documentation-index.md`](docs/v1-documentation-index.md)
- Security policy: [`SECURITY.md`](SECURITY.md)

## Architecture and development

- Architecture: [`docs/architecture.md`](docs/architecture.md)
- API guide: [`docs/api-guide.md`](docs/api-guide.md)
- Plugin guide: [`docs/plugin-development-guide.md`](docs/plugin-development-guide.md)
- SDK guide: [`docs/sdk-guide.md`](docs/sdk-guide.md)
- ADRs: [`docs/adr/`](docs/adr/)

The Backend is FastAPI/SQLAlchemy/PostgreSQL/Redis. The Console is React/TypeScript/Vite/Ant Design. PostgreSQL is the durable source of truth; Workers use lease/fencing and Sandbox contracts; Plugins do not access platform persistence directly.

## Security boundary

Production identity must be verified by an OIDC/enterprise gateway that deletes client-supplied identity headers and injects `X-CAP-User` plus `X-CAP-Proxy-Secret`. Browser state and hidden buttons are not security boundaries; Backend RBAC is authoritative.

Production startup rejects repository placeholder secrets and debug mode. API documentation defaults to disabled in release deployment assets. TLS, external secret management, network policy, audit retention, backup/restore evidence, and dependency/image scanning are mandatory production controls.

## Single-node evaluation

```bash
copy .env.example .env
# Replace every placeholder with independent random values.
docker compose config --quiet
docker compose up --build -d
```

- Console: http://localhost:8080
- Backend health: http://localhost:8000/health
- Backend readiness: http://localhost:8000/ready
- Metrics: http://localhost:8000/metrics

Compose is an evaluation/single-node path. See [`docs/deployment/docker-compose.md`](docs/deployment/docker-compose.md).

## Kubernetes packaging

The Helm Chart is at `deployment/helm/cap`. It deploys CAP only; production PostgreSQL and Redis are external. Create the required Secret first and review the production checklist.

```bash
helm lint deployment/helm/cap
helm upgrade --install cap deployment/helm/cap \
  --namespace cap --create-namespace \
  --values production-values.yaml \
  --wait --wait-for-jobs --rollback-on-failure --timeout 10m
```

Deployment index: [`deployment/README.md`](deployment/README.md).

## Quality gates

```bash
uv sync --project backend --extra dev --frozen
uv run --project backend ruff check backend/app backend/tests benchmarks/phase22
uv run --project backend pytest backend/tests -p no:cacheprovider
npm ci --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
```

CI additionally enforces 95% Backend coverage, Compose/Helm validation, Docker builds, npm production dependency audit, and baseline Trivy scanning.

## Operations

- Production checklist: [`docs/deployment/production-checklist.md`](docs/deployment/production-checklist.md)
- Upgrade: [`docs/deployment/upgrade.md`](docs/deployment/upgrade.md)
- Rollback: [`docs/deployment/rollback.md`](docs/deployment/rollback.md)
- Backup/restore: [`docs/deployment/backup-restore.md`](docs/deployment/backup-restore.md)
- Operations guide: [`docs/operations-guide.md`](docs/operations-guide.md)
- Runbook: [`docs/runbook.md`](docs/runbook.md)
- FAQ: [`docs/faq.md`](docs/faq.md)

## Version policy

CAP follows Semantic Versioning 2.0.0. `1.0.0-rc1` has lower precedence than final `1.0.0`. Published release contents are immutable: corrections require a new RC. After final 1.0.0, compatible bug fixes increment PATCH, compatible public additions increment MINOR, and incompatible public API changes increment MAJOR.

## License and community

Apache License 2.0. See [`LICENSE`](LICENSE), [`CONTRIBUTING.md`](CONTRIBUTING.md), and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
