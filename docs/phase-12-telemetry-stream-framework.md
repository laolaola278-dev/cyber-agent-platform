# Phase 12 Architecture: Telemetry and Stream Framework

## 1. Scope and non-goals

Phase 12 establishes CAP's unified telemetry platform and broker-neutral stream semantics. It delivers interfaces, lifecycle, persistence, API, audit, checkpoint, replay and backpressure without integrating a real source or broker.

Explicit non-goals:

- no Zeek, Kafka, Redpanda, Fluent Bit, Windows Event, CloudTrail, Elastic or OpenTelemetry source integration;
- no live socket, file tail, packet capture or arbitrary path ingestion;
- no change to `SecurityEvent`, `Finding` or `Incident`;
- no automatic Detection or Incident invocation;
- no exactly-once delivery claim.

## 2. Reference architecture analysis

### Fluent Bit

Fluent Bit's Input → Parser/Filter → Buffer → Output decomposition shows that source acquisition, representation conversion, buffering and delivery need separate ownership. CAP maps these to Receiver, Parser, Transformer, bounded Stream Runtime and Publisher.

### Vector

Vector's Source → Transform → Sink DAG demonstrates source-neutral composition, fan-out and backpressure propagation. CAP keeps an explicit pipeline snapshot and stable plugin lifecycle while deferring a general DAG scheduler to the existing Workflow Framework.

### OpenTelemetry Collector

Receiver → Processor → Exporter pipelines validate a vendor-neutral collector SDK and reusable processors. CAP adopts the boundary pattern but uses a security-platform `TelemetryRecord` contract and does not claim OpenTelemetry protocol compatibility.

### Kafka and Redpanda

Topic/stream, partition, consumer, consumer group, offset, replay and resume semantics inform the broker-neutral runtime. CAP stores consumer progress by `(stream, partition, consumer)`, enforces monotonic checkpoint movement and deliberately avoids importing a broker client.

## 3. Platform architecture

```text
POST /telemetry/tasks
  -> TelemetryTaskCreate (extra fields forbidden)
  -> TelemetryService
  -> TelemetryRegistry / TelemetryPlanner
  -> TelemetryRuntime
  -> TelemetryPlugin lifecycle
       initialize
       receive
       parse
       transform
       publish
       shutdown
  -> TelemetryRecord
  -> TelemetryJournal
  -> StreamRuntime.ack
  -> CheckpointProvider
  -> Audit Event Bus
```

`TelemetryRecord` is a transport contract containing source, timestamp, stream, offset, sequence, payload, metadata and SHA-256 checksum. It is not a SecurityEvent and cannot own Detection or Incident lifecycle.

## 4. Plugin SDK and lifecycle

`TelemetryPluginContext` is a frozen, slotted least-privilege DTO. It contains task identities, trace ID, stream cursor identity, policy, immutable input and granted permissions. It contains no database session, repository or domain service.

Lifecycle rules:

1. `initialize()` validates the context and establishes bounded in-memory state.
2. `receive()` obtains source envelopes through the plugin boundary.
3. `parse()` validates and converts envelopes.
4. `transform()` emits stable `TelemetryRecord` objects.
5. `publish()` returns `TelemetryExecutionResult`; it does not persist domain events.
6. `shutdown()` always runs after successful initialization, including timeout or validation failure.

Runtime validation covers permission identity, timeout, maximum record count, single-record size, payload checksum, plugin identity and publish-count consistency.

## 5. Stream semantics

### Batch and window

Batching preserves record order and produces offset-bounded `StreamBatch` objects. Time windows sort by timestamp and create deterministic bounded groups.

### Ack and checkpoint

Ack commits the record's offset, sequence and checksum through a provider. Checkpoint identity is `(provider, stream, partition, consumer)`. Offset and sequence cannot move backwards.

### Replay

Replay selects an offset range and optional recent time window from the journal. Replay does not commit or mutate a checkpoint. This prevents a read/reprocessing request from silently acknowledging records.

### Ordering and delivery

The framework preserves per-partition ordering. It makes no global ordering promise and does not claim exactly-once delivery. Future broker adapters should use at-least-once delivery with idempotent consumers, stable checksums and monotonic checkpoints.

## 6. Backpressure

`BoundedTelemetryQueue` makes queue saturation explicit:

- Drop: discard the overflowing item and emit audit metadata;
- Retry: return a retry decision or perform bounded retries in the service/operation helper;
- Pause: wait a bounded duration, then fail if capacity remains unavailable;
- Reject: fail closed immediately.

No policy loops forever. Retry attempts and pause duration are typed configuration. Non-accept decisions publish `TELEMETRY_BACKPRESSURE_APPLIED` with decision, depth, capacity and attempts.

The current synchronous request path fills a bounded in-memory queue before plugin execution. This validates policy semantics but is not the final multi-worker producer/consumer design. A real broker adapter must coordinate producers, consumers and queue draining outside the HTTP request lifecycle.

## 7. Checkpoint providers and persistence

- `MemoryCheckpointProvider`: process-local test/development provider.
- `SQLAlchemyCheckpointProvider`: active-session provider compatible with SQLite and PostgreSQL SQLAlchemy dialects.
- PostgreSQL production mode: configured by the same provider contract; no provider-specific API schema leaks to clients.

Four control-plane tables are introduced:

- `telemetry_pipelines`;
- `telemetry_tasks`;
- `telemetry_checkpoints`;
- `telemetry_runtime_states`.

The runtime-state table captures worker status, stream cursor identity, offset, lag, queue depth, backpressure action, metadata and heartbeat.

## 8. API and audit

APIs:

- `POST /telemetry/tasks` — plan and optionally execute synthetic telemetry;
- `GET /telemetry/tasks` — list task snapshots;
- `GET /telemetry/runtime` — list worker/runtime state and capabilities;
- `GET /telemetry/checkpoints` — provider-neutral checkpoint views;
- `POST /telemetry/replay` — read a bounded replay range without committing.

Audit events cover task creation, execution start/completion/failure, checkpoint commit, replay request and backpressure application. Trace, task, telemetry aggregate and error context are retained by the existing platform event/audit boundary.

## 9. Security boundary analysis

- Registry allowlist permits only `telemetry.receive` and `telemetry.publish`.
- Explicitly forbidden permissions include database, Detection, SecurityEvent creation, Incident creation, shell, filesystem write and unrestricted network.
- API request models reject unknown fields, including arbitrary path or endpoint injection.
- The Phase 12 plugin consumes only request-provided synthetic in-memory objects.
- Record count, record size, queue size, timeout, retries and pause duration are bounded.
- Checksum validation detects payload mutation across plugin stages.
- Plugin results cannot create SecurityEvent; acceptance tests assert the SecurityEvent count remains zero.

## 10. Scalability analysis

Single-node mode uses a process-local journal and optional memory checkpoint provider. This is deterministic for framework validation but not horizontally durable.

A production scale-out path requires:

1. a broker adapter that maps topic/partition/consumer-group semantics to `StreamRuntime` contracts;
2. durable external journal/retention managed outside request memory;
3. shared PostgreSQL or broker-native checkpoint state;
4. partition ownership and rebalance coordination;
5. idempotent downstream consumers;
6. lag, queue depth, retry and dead-letter observability;
7. controlled replay workers separated from live consumers.

Scale is achieved by partitions and independent consumer checkpoints, not by sharing mutable process-local state.

## 11. Interoperability analysis

- Runtime: Telemetry plugins follow the same governed lifecycle philosophy but do not become general Agents.
- Workflow: Workflow nodes may submit telemetry tasks through API/service boundaries; StreamRuntime is not a workflow engine.
- Detection: a future adapter may consume TelemetryRecord and submit Detection input. Telemetry cannot directly persist SecurityEvent.
- Evidence: large or raw source payloads should be retained through governed Evidence/object storage, not expanded into TelemetryRecord metadata.
- Knowledge: enrichment belongs in downstream processing/detection and must preserve knowledge-version governance.
- Asset: future source adapters may resolve source/asset identities through platform services outside plugin context.
- Incident: only Detection correlation and IncidentService can propose/create/transition incidents.
- Future Zeek: implemented as a Telemetry Plugin plus tool/source adapter.
- Future Kafka/Redpanda: implemented as broker/journal/checkpoint adapters, not as core runtime dependencies.

## 12. Architecture trade-offs

### Independent Telemetry layer

Adds interfaces and tables, but prevents source transport concerns from polluting Detection and SecurityEvent.

### Broker-neutral core

Makes local validation and broker replacement straightforward, but postpones production consumer-group behavior to adapters.

### Provider-neutral checkpoint view

Avoids persistence-specific API fields and supports memory/database providers, at the cost of a smaller common read model.

### Process-local memory journal

Enables deterministic cross-request framework tests through application-scoped DI, but is not durable, multi-process safe or production scalable.

### Synchronous task execution

Makes lifecycle and audit easy to verify in Phase 12, but a production stream worker must decouple ingestion from HTTP request duration.

## 13. Known technical debt

- external durable journal provider is not implemented;
- multi-instance memory-state isolation and test reset hooks need explicit lifecycle management;
- consumer-group rebalance and partition leases are future adapter responsibilities;
- current service queue validates bounded policy but does not yet model concurrent producer/consumer draining;
- historical SQLite migration chain contains PostgreSQL-style `now()` defaults, so end-to-end SQLite Alembic upgrade is not a supported validation path without retroactive migration compatibility work.
