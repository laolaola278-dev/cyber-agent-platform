# ADR-0026: Telemetry Is an Independent Platform Layer

- Status: Accepted
- Date: 2026-07-31

## Context

CAP needs a stable way to receive high-volume, source-specific operational data before that data is interpreted as a security signal. Zeek, Suricata, Windows Event, CloudTrail, OpenTelemetry, Fluent Bit, Vector and future collectors expose different envelopes, transports, offsets and delivery semantics. Mapping those sources directly into Detection would couple ingestion concerns to rule evaluation and would incorrectly imply that every telemetry record is a SecurityEvent.

Fluent Bit separates input, parser, buffer and output; Vector separates source, transform and sink; OpenTelemetry Collector separates receiver, processor, exporter and pipeline. These designs support a source-neutral platform boundary in which collection and transport precede domain interpretation.

## Decision

CAP introduces Telemetry as an independent platform layer with these interfaces:

- `TelemetryRegistry` admits only allowlisted, least-privilege plugins;
- `TelemetryPlanner` creates a bounded, auditable execution plan;
- `TelemetryRuntime` owns the six-stage plugin lifecycle;
- `TelemetryService` owns persistence, audit, checkpoint and replay coordination;
- `TelemetryPlugin` implements `initialize`, `receive`, `parse`, `transform`, `publish` and `shutdown`;
- `TelemetryRecord` is the stable transport record and is not a `SecurityEvent`;
- `StreamRuntime` owns broker-neutral batch, window, ack and replay semantics.

Telemetry plugins cannot access `AsyncSession`, repositories, `DetectionService`, `IncidentService`, shell execution, unrestricted network or filesystem write. They cannot create `SecurityEvent`, `Finding` or `Incident`. Detection remains the only framework that interprets telemetry and owns security-event generation.

Phase 12 includes only a synthetic in-memory plugin for framework validation. It does not connect Zeek, Kafka, Fluent Bit, Windows Event, CloudTrail, Elastic or any real telemetry source.

## Consequences

- Source adapters can evolve without changing Detection or Incident models.
- Ingestion failures, backpressure and replay are observable independently from detection outcomes.
- `TelemetryRecord` provides a stable anti-corruption boundary for future adapters.
- A later Detection consumer must explicitly translate selected telemetry records into detection inputs.
- The additional layer introduces more interfaces and persistence state, accepted in exchange for domain isolation, replaceability and governance.
- Real deployments will require externalized journals and broker adapters; the Phase 12 memory journal is intentionally process-local.
