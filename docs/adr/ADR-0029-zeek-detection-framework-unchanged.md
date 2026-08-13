# ADR-0029: Keep the Detection Framework unchanged for Zeek

- Status: Accepted
- Date: 2026-08-01
- Phase: 13

## Context

Zeek differs from Suricata: most Zeek rows are observations, while `notice.log` carries higher-level
notices. The integration nevertheless needs the same governed lifecycle, policy, normalization,
persistence, correlation and audit controls already supplied by the Detection Framework.

## Decision

No Detection Framework contract, runtime, service, persistence model or migration is changed.
Zeek is added only through existing extension points:

- `ZeekDetectionPlugin` implements the existing six-stage Detection Plugin protocol;
- `ZeekResultNormalizer` returns existing `DetectionResult` / `RawSecurityEvent` contracts;
- existing `DetectionService` exclusively creates `SecurityEvent` rows;
- existing Asset, Evidence, Knowledge, Reference and Incident candidate relations are reused;
- Zeek configuration, manifests, dependency registration and convenience API are additive.

## Rationale

`RawSecurityEvent` already supports tool identity, rule, IOC, references, asset IDs, evidence IDs,
knowledge IDs and bounded attributes. `DetectionPolicy` already supports source/parser/plugin
allowlists and execution limits. No Zeek-specific table is justified.

## Consequences

- Zero database migration and no Zeek-specific persistence silo.
- Existing audit and correlation behavior applies consistently.
- Raw Zeek content is not copied into SecurityEvent attributes; only allowlisted fields and evidence
  lineage hashes are persisted.
- Zeek package-specific semantics require future normalizer extensions, not framework changes.
