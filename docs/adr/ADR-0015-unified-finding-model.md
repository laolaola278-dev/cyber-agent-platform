# ADR-0015: Finding Is the Unified Assessment Model

- Status: Accepted
- Date: 2026-07-31

## Context

ZAP alerts, Nuclei matches, Greenbone results, Wazuh vulnerability inventory items and other tool outputs have incompatible semantics. Persisting every native result as a platform model would couple all consumers to tools.

## Decision

Normalize validated AssessmentResult entries into one Finding model. Finding separates severity, confidence, risk and lifecycle state; preserves plugin/tool/rule identity; links Evidence, immutable KnowledgeVersion and Asset; and carries a stable SHA-256 deduplication fingerprint plus duplicate lineage.

## Consequences

- Reports, workflows and future response components consume one model.
- Tool-specific details remain in attributes and references rather than core columns.
- Deduplication is reproducible and auditable.
- Cross-tool semantic deduplication and advanced lifecycle APIs remain future extensions.
