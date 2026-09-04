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

### Candidate tracks (evidence-anchored, not commitments)

Each track below originates from an in-repo artifact and follows the v1 API
freeze policy (`docs/api-freeze-v1.md`). Verification-layer status for every
promised capability lives in `docs/quality/coverage-matrix.md`.

1. **SLO enforcement** (evidence: `outputs/cap-cert-ga/slo-candidates.json`).
   Four measured candidates exist (recovery RPO ≤ 24.4s, recovery RTO ≤ 315.7s,
   API availability ≥ 99.5%/30d, lease false-reclaim rate = 0). Promotion to
   enforced SLOs requires a full month of production data plus an SLI export
   pipeline (the GA report's `sli` section intentionally has no producer until
   then). Version impact: none (observability only).
2. **Real device integration** (evidence: `backend/app/response/fake_plugin.py`,
   coverage-matrix known limitations). Replace the mock-only EDR/WAF/Firewall
   response providers with production integrations; evaluate provider-contract
   compatibility per the freeze policy (MINOR if additive, MAJOR if breaking).
   A 24-hour soak (current certification soak is 7200s) accompanies this track.
   Zeek TSV ingestion is a design-level rejection (`tools/zeek/adapter.py`);
   implementing a parser would be a PATCH-GATE-12 extension.
3. **CI blind-spot governance rollout** (evidence:
   `docs/quality/ci-blindspot-governance.md`). P0 real-path probes and the
   quality-gates matrix assertion, P1 assertion-semantics audit, P2 matrix
   (created), P3 release-cadence rule. Aims to move releases from
   defect-driven to prevention-driven.
4. **Console completion & maintenance** (evidence: `frontend/src/`).
   All 16 views now live in `pages/` (completed after v1.0.3); remaining work
   is bundle code-splitting (antd chunk > 500 kB) and periodic GitHub Actions
   major bumps.
