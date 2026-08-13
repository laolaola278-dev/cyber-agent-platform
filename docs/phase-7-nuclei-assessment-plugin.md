# Phase 7 Nuclei Assessment Plugin

## Purpose

Phase 7 validates the existing Assessment Framework with one real external tool while preserving Platform First and Plugin First boundaries. It does not add a second scanner, target discovery, arbitrary URL scanning, dynamic templates, or Internet test execution.

## Component boundaries

- `plugins/nuclei/manifest.yaml`: declares capabilities, permissions, sandbox requirement, and prohibited behaviors.
- `tools/nuclei/manifest.yaml`: declares CLI execution constraints and future sandbox providers.
- `backend/app/plugins/nuclei`: six-stage Plugin and JSONL-to-AssessmentResult Normalizer.
- `backend/app/tools/nuclei`: governed Adapter and typed execution values.
- `backend/app/sandbox`: process execution port, local implementation, and reserved stronger providers.
- `backend/app/assessment`: fingerprint provider, Knowledge mapper, Finding state machine, report/lifecycle orchestration.

## Target and template flow

1. API accepts `asset_id`, approved template IDs, optional policy, and execute flag.
2. AssessmentService rejects missing or soft-deleted Assets and derives an HTTP(S) target from WEBSITE, DOMAIN, HOST, or `APPLICATION.properties.url`.
3. Planner verifies Asset allow/deny rules and `template.scan`/`web.scan` capability permission.
4. Adapter resolves template IDs only from configuration, requires paths under the trusted root, verifies SHA-256, and sums declared request budgets.
5. Sandbox executes Nuclei without a shell and with bounded environment, timeout, and output.
6. Adapter parses JSONL; Normalizer preserves matcher/extractor/request/response metadata and produces tool-neutral RawFindings.
7. Platform maps existing CVE/CWE/CPE/CISA KEV/ATT&CK Knowledge, computes fingerprints and risk, persists Findings/history/report, and emits audit events.

## Finding lifecycle

Allowed states are `NEW`, `TRIAGED`, `CONFIRMED`, `FALSE_POSITIVE`, `ACCEPTED_RISK`, `FIXED`, and `REOPENED`. All accepted transitions produce a `FindingTransition`, append a `FindingHistory` snapshot, update the Finding, and emit `FindingTransitioned`. Invalid transitions fail with HTTP 409.

## Assessment Report

The platform-generated report is one-to-one with AssessmentTask and includes Plugin identity, Asset, template/rule identity, Finding severity/confidence/status, risk, pinned Knowledge links, evidence, and references. Plugins cannot write reports directly.

## Configuration

`backend/config/assessment.yaml` defines the executable name, template root, output limit, approved template IDs, relative paths, SHA-256 values, and maximum request count per template. Changing a template requires review and updating its digest explicitly.

## Operational notes

- Install and version-pin Nuclei separately in environments that will execute assessments.
- Prefer Docker, Firecracker, or a Remote Worker Provider for higher-risk production targets once implemented.
- Keep template directories read-only for the runtime identity.
- Do not enable automatic template updates, remote template URLs, stdin targets, cloud upload, or implicit httpx.
