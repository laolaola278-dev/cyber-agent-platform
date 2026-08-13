# Known Issues for 1.0.0-rc1

## Release blockers

1. Phase 22 high-concurrency API latency budgets failed. At concurrency 1000, POST `/assets` measured P95 17,236.32 ms and P99 17,248.67 ms in an in-process ASGI/SQLite benchmark. The result is not a PostgreSQL capacity result, but it blocks an unconditional performance certification.
2. Real PostgreSQL lock/connection-pool/migration behavior and Redis/PostgreSQL restart recovery were not tested because Docker Engine was unavailable.
3. Kubernetes/Helm install, rolling upgrade, rollback, probes, and disruption behavior are statically defined but not cluster-certified.
4. The repository has no Git commit history, remote, signed tag, or executed GitHub workflow. `v1.0.0-rc1` is prepared as an asset set, not yet a published immutable release.

## Operational limitations

- Identity is supplied by a trusted reverse proxy; CAP does not provide an OIDC login implementation. Production gateways must overwrite identity headers.
- User/Role/Permission directories are immutable in v1; there is no user-management write API.
- OpenTelemetry spans are not exported when `OTEL_EXPORTER_ENDPOINT` is empty.
- Metrics and API docs are public application paths; production networks must restrict metrics, while API docs default to disabled.
- The Web Console defaults to `read-only` for local Compose. It is not a production identity solution.
- Frontend bundles still produce size warnings; this is not a correctness defect.

## Local certification evidence (updated 2026-08-06)

- Ruff, Python source compilation, repository whitespace checks, Alembic single-head validation, and Compose static configuration validation passed locally. Ruff emitted only a cache-permission warning (`.ruff_cache` access denied) after reporting `All checks passed!`; this does not change the lint result.
- Frontend production dependency audit against the official npm registry reported 0 vulnerabilities.
- Basic repository scans found no committed literal production credentials, explicit TLS-verification disablement, dynamic `eval`/`exec`, `os.system`, `pickle.load(s)`, unsafe `yaml.load`, or shell-enabled subprocess calls in the reviewed source/configuration scope.
- Local `npm ci` could not complete because the WorkBuddy safe-delete hook blocked npm's bulk replacement of 87 `node_modules` paths. This is a workstation policy constraint; lint passed against the existing tree, while the production build is currently blocked because the Windows esbuild optional package `@esbuild/win32-x64` is absent after the interrupted dependency replacement. CI still runs clean `npm ci` on Ubuntu.
- Helm was not installed locally, so local `helm lint/template/package` evidence is unavailable. CI pins Helm `v3.17.3` and retains these commands as mandatory gates.

## Dependency and image evidence

Backend dependencies are lockfile-pinned and locally consistent through test execution. An independent OSV/pip-audit result, CI Trivy filesystem/image scans, generated SBOM/provenance, and target-image digest review remain CI/Architect gates. A local static scan or successful npm audit must not be interpreted as complete supply-chain certification.
