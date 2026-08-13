# ADR-0021: SecurityEvent Is the Unified Detection Event Model

- Status: Accepted
- Date: 2026-07-31

## Context

Suricata EVE JSON, Zeek logs/events, Sigma matches, Wazuh alerts and future host or IOC detectors expose incompatible payloads. Persisting those tool-native structures as final platform records would make Workflow, Knowledge, Asset, Report and correlation consumers depend on each tool's schema and upgrade cycle.

## Decision

Normalize validated `DetectionResult.events` into a single `SecurityEvent` platform model. SecurityEvent records event type, source, severity, confidence, UTC timestamp, plugin/tool/rule identity, status, bounded attributes and stable fingerprint. References, Evidence, immutable KnowledgeVersion and canonical Asset links are relational projections owned by the platform.

Tool-native nested payloads are not persisted as the final event model. Only bounded scalar/list attributes and explicit references survive normalization. Plugins return `DetectionResult`; only `DetectionService` validates cross-domain IDs, persists SecurityEvent records, correlates events and publishes audit events.

## Consequences

- Detection consumers use one stable contract across heterogeneous tools.
- Correlation by time, Asset, source, IOC and rule is deterministic and auditable.
- Tool-specific fidelity is intentionally bounded; full raw telemetry belongs in governed Evidence/object storage, not SecurityEvent attributes.
- Schema evolution is controlled by CAP rather than external tool releases.
- Future Incident/Case creation remains a separate platform decision and cannot be initiated directly by a Detection Plugin.
