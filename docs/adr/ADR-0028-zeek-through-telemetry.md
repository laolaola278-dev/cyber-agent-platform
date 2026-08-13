# ADR-0028: Route Zeek through the Telemetry Framework

- Status: Accepted
- Date: 2026-08-01
- Phase: 13

## Context

Zeek produces high-volume, append-oriented network telemetry rather than only alert records. Its
`conn.log`, `dns.log`, `http.log`, `ssl.log`, `files.log`, and `notice.log` streams have different
schemas, and Zeek scripts/packages may add fields. Calling the Detection Framework directly from a
file-reading plugin would couple source acquisition, backpressure/checkpoint concerns and event
persistence.

## Decision

CAP uses the following boundary:

```text
Configured data_source_id
  -> ZeekAdapter (bounded JSONL + hash/lineage)
  -> ZeekTelemetryPlugin
  -> existing TelemetryPlanner/TelemetryRuntime
  -> validated TelemetryRecord[]
  -> ZeekDetectionPlugin
  -> existing DetectionService/DetectionRuntime
  -> DetectionResult
  -> SecurityEvent
```

A small Zeek application bridge composes existing public Telemetry interfaces. It does not modify
the Telemetry Framework. The Detection Plugin receives only serialized `TelemetryRecord` values;
it never reads a file and never calls DetectionRuntime or DetectionService.

## Consequences

- Positive: source ingestion, checksums and schema drift metadata remain transport concerns.
- Positive: Detection remains responsible for semantic projection and SecurityEvent creation.
- Positive: a future broker can replace the in-process bridge without changing Zeek normalization.
- Trade-off: Phase 13 bridge execution is in-process and does not persist a separate TelemetryTask.
- Trade-off: JSONL is supported; TSV remains a fail-closed reserved interface.
