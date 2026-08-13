# CAP Roadmap

## 1.0.0 finalization

Only release-blocking bug fixes and certification work are allowed:

- Establish Git history and remote; execute CI and create a signed immutable RC tag.
- Run PostgreSQL/Redis integration, restart, migration, backup, and restore tests.
- Run Kubernetes Helm install/upgrade/rollback and probe/disruption tests.
- Run external k6/Locust/Vegeta load and target-capacity certification.
- Close or formally accept Phase 22 API latency risk.
- Complete image/SBOM/Trivy and backend dependency vulnerability evidence.
- Obtain Architect, Security, Operations, and license approval.

## After 1.0.0

Potential capabilities belong to separately approved phases and are not commitments of this RC. Any backward-compatible public API addition requires a MINOR release; any incompatible v1 change requires a MAJOR release. Deprecated behavior must be documented before removal.
