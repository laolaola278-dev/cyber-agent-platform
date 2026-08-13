# Phase 7 Official Reference and Integration Analysis

## Scope and safety

Phase 7 integrates only ProjectDiscovery Nuclei as CAP's first real Assessment Tool. CAP does not reimplement a scanner, copy exploit logic, dynamically download templates, scan public targets during development, or allow a Plugin to choose arbitrary targets. Official project documentation is used as an architectural reference rather than source code to transplant.

## GitHub Reference Analysis

### ProjectDiscovery Nuclei

Official references: ProjectDiscovery Nuclei repository, Running Nuclei documentation, and Template Introduction.

- Nuclei is a template-driven execution engine whose templates can cause network activity and therefore belong behind a trust boundary.
- Long-running service mode increases the attack surface; CAP instead creates one bounded execution per Assessment.
- JSONL is a stable process boundary for preserving template, matcher, extractor, evidence, classification, and reference data.
- CAP adopts explicit template selection, per-run execution, stable machine output, and local template governance.
- CAP rejects implicit community-template execution, automatic updates, remote templates, Cloud Upload, stdin targets, implicit httpx, and Plugin-owned process execution.

### ProjectDiscovery httpx

Official reference: ProjectDiscovery httpx Running documentation.

- httpx is suited to HTTP probing, metadata collection, and technology fingerprinting.
- Target discovery/probing is distinct from vulnerability-template execution.
- Phase 7 deliberately does not integrate httpx or let Nuclei invoke it implicitly; a future independent Adapter/Capability can provide discovery results through Asset Center.

### DefectDojo

Official reference: Finding Deduplication documentation.

- Scanner-provided stable identifiers are preferred; deterministic hashes are the fallback.
- Duplicate findings retain lineage rather than disappearing silently.
- CAP keeps `duplicate_of_id`, preserves every normalized Finding, and introduces an injectable `FingerprintProvider` while retaining the Phase 6 SHA-256 semantics.

### OWASP ZAP

Official reference: ZAP Alerts documentation.

- Scanner-native risk, confidence, evidence, references, and tags can be normalized, but scanner alert schemas should not become a platform database contract.
- CAP maps Nuclei output into tool-neutral `RawFinding`/`Finding`, keeping native details under attributes and evidence.

### OpenVEX

Official reference: OpenVEX Specification.

- Vulnerability statements distinguish product association and explicit status from the source detection event.
- CAP adopts an explicit, auditable Finding state machine separate from tool output.
- OpenVEX import/export is not implemented in Phase 7; its status semantics only inform the lifecycle boundary.

## Tool Integration Analysis

The integration path is strictly:

```text
AssessmentService -> AssessmentRuntime -> NucleiAssessmentPlugin
                  -> NucleiAdapter -> SandboxProvider
                  -> LocalProcessSandbox -> Nuclei CLI
                  -> JSONL -> NucleiResultNormalizer -> AssessmentResult
                  -> platform Finding / Knowledge / Report / Audit
```

Responsibilities:

- Plugin: implements the six SDK lifecycle methods and delegates process work.
- Adapter: validates target shape, template approval/integrity/request budget, builds safe arguments, invokes Sandbox, and parses JSONL.
- Sandbox: owns the only subprocess boundary, executable allowlist, shell-free execution, minimal environment, timeout, bounded output, and working-directory checks.
- Normalizer: maps tool data into CAP contracts while preserving evidence and references.
- Platform service: owns Asset validation, Policy/Capability enforcement, persistence, deduplication, Knowledge links, reports, transitions, and audit.

## Security Boundary Analysis

1. Target authority: API accepts only `asset_id`; target is derived from an existing, non-deleted Asset.
2. Policy authority: Planner verifies Asset allow/deny lists and requested Capability allowlist before Plugin execution.
3. Plugin authority: Plugin receives a narrow immutable context and no database/session/shell object.
4. Template authority: only configured local files under the trusted root are accepted; each file is SHA-256 verified and request-budgeted.
5. Process authority: Adapter cannot execute directly; only `SandboxProvider` can run a command.
6. CLI safety: no shell, no stdin, no implicit httpx, no template update, no result/template cloud upload, retries disabled, concurrency/rate/timeout bounded.
7. Output authority: invalid JSONL, non-object records, non-zero exit, truncation, timeout, and unsupported severity fail closed.
8. Persistence authority: Plugins cannot create Findings, transitions, reports, or audit rows.
9. Test safety: all Phase 7 tests use Fake Sandbox/local fixture data and initiate no Nuclei process or network request.

## Architecture Trade-off Analysis

- Local process vs stronger isolation: LocalProcessSandbox provides a minimal deployable baseline but shares the host kernel. Docker, Firecracker, and Remote Worker Providers are reserved and should become production defaults according to deployment risk.
- JSONL vs library embedding: JSONL adds serialization cost but prevents scanner types and dependencies from leaking into the platform core.
- Explicit template allowlist vs community-template flexibility: governance reduces coverage velocity but provides reproducibility, integrity, request-budget accounting, and reviewability.
- Asset-derived target vs arbitrary URL: this prevents ad hoc unauthorized scope expansion at the cost of requiring Asset Center registration first.
- Platform report vs Plugin report: centralized aggregation may lose some native presentation, but guarantees consistent Finding, Evidence, Knowledge, Asset, and audit semantics.
- Explicit state machine vs free-form status: controlled transitions add operational discipline and auditability while requiring future policy work for organization-specific workflows.
