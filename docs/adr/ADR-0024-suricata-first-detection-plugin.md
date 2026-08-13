# ADR-0024: Suricata Is the First Real Detection Plugin

- Status: Accepted
- Date: 2026-08-01

## Context

CAP already provides a Detection Framework with a governed six-stage Plugin lifecycle, capability and parser allowlists, least-privilege permissions, bounded ingestion, platform-owned persistence and deterministic correlation. Phase 11 must validate that framework against one real IDS without changing its core contracts or introducing several tool-specific assumptions at once.

Suricata is selected because it has a documented, structured EVE JSON output; stable alert rule identity through GID, SID and revision; multiple event families for alert and telemetry validation; and a mature deployment model that can be operated independently from CAP. This makes it a useful anti-corruption-layer test while keeping the first integration narrow.

## Decision

Suricata is the only real Detection Tool integrated in Phase 11. The integration consists of:

- `SuricataAdapter` for allowlisted, read-only EVE JSONL collection and envelope validation;
- `SuricataDetectionPlugin` for lifecycle orchestration only;
- `SuricataResultNormalizer` for conversion to the existing `DetectionResult` contract;
- a typed `SuricataSandboxProfile` and platform-owned data-source configuration;
- existing DetectionService, Runtime, Planner, Registry, SecurityEvent and correlation components without framework changes.

The Plugin cannot read arbitrary paths, parse JSON directly, access a database, create an Incident or invoke response actions. Client requests select a configured `data_source_id`; the actual path remains platform configuration.

## Consequences

- CAP proves the Detection Framework against a real IDS before adding another integration.
- Suricata-specific schema knowledge is isolated in the Adapter and Normalizer.
- The test fixture and API are safe-by-default and do not start live packet capture or active network scanning.
- Future tools must demonstrate equivalent boundaries and must not be added by copying Suricata assumptions into the framework.
- A later operational deployment may consume a governed Suricata EVE source, but live capture, rule distribution and response remain separate deployment decisions.
