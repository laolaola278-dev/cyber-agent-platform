# ADR-0027: Stream Runtime Is Independent from Detection

- Status: Accepted
- Date: 2026-07-31

## Context

Stream processing and detection solve different problems. A stream runtime coordinates partitions, consumers, offsets, acknowledgement, ordering, checkpoints, replay, batch, windows and backpressure. A detection framework validates detection policy, runs detection plugins, normalizes findings into `SecurityEvent`, correlates events and produces incident candidates.

Binding stream mechanics to Detection would make Kafka or another broker an implicit Detection dependency, force non-security telemetry through SecurityEvent semantics, and make replay capable of creating domain effects without an explicit detection policy decision.

Kafka and Redpanda demonstrate topic, partition, consumer-group and offset semantics. Vector and OpenTelemetry Collector demonstrate that transport pipelines can have multiple downstream consumers. These properties must remain usable by future Evidence, Asset, Knowledge, Detection and observability consumers.

## Decision

CAP defines a broker-neutral `StreamRuntime` outside the Detection Framework.

- Stream identity is `(stream, partition)` and consumer progress is keyed by `(stream, partition, consumer)`.
- Checkpoint persistence is abstracted by `CheckpointProvider`.
- Memory and SQLAlchemy providers implement the same contract; SQLAlchemy supports SQLite and PostgreSQL sessions.
- Ack commits a monotonic offset and sequence.
- Replay reads a journal range and never commits a checkpoint.
- Batch and time windows operate on `TelemetryRecord` without creating a `SecurityEvent`.
- Backpressure actions are explicit and bounded: Drop, Retry, Pause and Reject.
- No Kafka, Redpanda, Redis Streams or other broker client is imported by the core runtime.
- Detection integration, when introduced, must be a downstream consumer/adapter that invokes Detection through its public boundary.

## Consequences

- Broker replacement does not require Detection Framework changes.
- Replay is transport-safe by default and cannot silently advance consumer state.
- Different downstream consumers can maintain independent checkpoints.
- Per-partition ordering is preserved by contract; global ordering is not promised.
- Memory journal and memory checkpoint state are process-local and unsuitable for horizontal production workers.
- Production horizontal scaling requires an external broker, durable journal, shared checkpoint provider and consumer-group coordination.
- Exactly-once delivery is not claimed. The framework is designed for at-least-once-compatible processing with idempotency and stable checksums at downstream boundaries.
