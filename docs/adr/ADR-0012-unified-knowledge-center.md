# ADR-0012: Adopt a Unified Knowledge Center

## Status

Accepted for Phase 5.

## Context

Assessment, Detection, Response, Sandbox, Evidence, and Report require CVE, CWE, CAPEC, CPE, ATT&CK, KEV, OWASP, vendor, IOC, and rule knowledge. If each Agent owns a private dataset, identities, versions, status, mappings, and trust rules diverge. CAP could not prove which knowledge revision supported an action or report.

## Decision

CAP will provide one Knowledge bounded context and write boundary. Stable identities are source-scoped. All changed imports create immutable KnowledgeVersion snapshots. Relationships and Asset/Evidence/Report links are explicit and audited; provenance links pin the exact version used.

Agents may cache read results but may not publish or mutate private vulnerability knowledge outside KnowledgeService.

## Consequences

Positive: consistent semantics, centralized governance, version history, reproducible reports, shared reuse, and rebuildable graph/search projections.

Negative: Knowledge Center becomes a platform dependency and requires source trust/conflict policy. Availability is mitigated through read caching and immutable version references; future projections remain disposable.
