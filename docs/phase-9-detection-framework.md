# Phase 9 Detection Framework Analysis

## 1. GitHub Reference Analysis

### Suricata

Suricata separates packet/flow processing from EVE JSON output. EVE records are heterogeneous event documents such as alert, flow, DNS and HTTP; an alert enriches a temporal network observation but does not become a platform Incident by itself. CAP therefore models Suricata as a future adapter/plugin source whose EVE payload must be parsed and normalized into SecurityEvent. Flow identity, alert signature, source and timestamps inform the common event contract; raw EVE trees are Evidence, not the final platform model.

### Zeek

Zeek is event-driven: protocol analyzers generate events, scripts react, and log writers emit typed streams such as `conn.log`. Connection records and script events have different schemas but share temporal/source semantics. CAP maps future Zeek records through collect/parse/detect/normalize, preserving source, rule or notice identity and bounded attributes while keeping Zeek script/runtime internals outside the control plane.

### Sigma

Sigma is a portable detection-rule specification with rule metadata, log-source constraints and a detection expression; it is not a telemetry store or execution engine. CAP treats Sigma as a future rule provider/compiler integration. Rule ID, title, level, references and log source may enrich SecurityEvent/Knowledge, but backend-specific queries and raw source records remain adapter concerns.

### TheHive

TheHive distinguishes Alert, Observable and Case. This supports CAP's decision that a detected event must not directly become an Incident/Case: SecurityEvent is an observation, references/IOCs are evidence or observables, and escalation is a separate governed workflow requiring policy and, where appropriate, approval.

### Wazuh

Wazuh decoders parse events, rules evaluate them, and rule correlation can depend on frequency/timeframe and prior matches. CAP reuses this separation in a small deterministic correlation engine: normalized events are correlated after persistence by bounded time, Asset, source, IOC and rule keys. Phase 9 does not implement Wazuh or its full rule language.

### Reference conclusions

1. Source-specific parsing belongs behind Plugin/Adapter contracts.
2. A normalized time-bound event is the interoperable platform unit.
3. Rules and correlation are governed separately from raw telemetry collection.
4. Alert-to-Case/Incident promotion must not be automatic or Plugin-owned.
5. Phase 9 uses only `FakeDetectionPlugin`; no Suricata, Zeek, Sigma, YARA, Elastic, Splunk, Wazuh or other real detector is connected.

## 2. Architecture

```text
Workflow
  -> DetectionPlanner
  -> Capability Registry
  -> DetectionRegistry
  -> DetectionRuntime
  -> DetectionPlugin
  -> DetectionResult
  -> DetectionResultNormalizer
  -> SecurityEvent
  -> RuleBasedCorrelationEngine
  -> Evidence + KnowledgeVersion + Asset
  -> Report/Audit consumers
```

The control plane owns policy, capability resolution, persistence, correlation and audit. A Plugin owns only its six-stage lifecycle and can return only DetectionResult.

## 3. Security Boundary Analysis

### Why a Plugin cannot access Workflow

Workflow owns orchestration, retries, branching and cross-Agent execution. Exposing it would let a detector create or mutate execution graphs, bypass Planner policy or recursively invoke itself. The Plugin Context contains stable task IDs and trace ID only.

### Why a Plugin cannot access Database

Database access would bypass validation, relational constraints, immutable KnowledgeVersion selection, audit publication and transaction ownership. The Context never exposes AsyncSession, repositories or ORM models. `database.access` is explicitly denied by the registry.

### Why a Plugin cannot access Assessment

Assessment owns active evaluation and Finding semantics. A detector calling Assessment could turn passive observations into unauthorized scanning or conflate Event and Finding lifecycle. `assessment.access` is denied and no Assessment service appears in Context.

### Why a Plugin cannot access Report

Report generation is a platform projection over validated entities. Direct report writes could omit audit, include unsanitized payloads or create contradictory sources of truth. `report.generate` is denied and ReportService is absent from Context.

### Why a Plugin returns only DetectionResult

DetectionResult is an in-memory, plugin-neutral DTO. Runtime verifies plugin identity, timeout, permission match, count and size; applies deterministic sampling/rate bounds; then the platform normalizes and validates Asset/Evidence/Knowledge links before persistence. Plugins cannot create Incident/Case records, change event status, or write platform entities.

### Permissions

Allowed: `detection.execute`, `evidence.read`.

Denied: `database.access`, `workflow.access`, `assessment.access`, `report.generate`, `incident.create`, `shell.execute`, `filesystem.write`, plus every undeclared permission.

## 4. Detection Policy

Policy is strongly typed, fail-closed and snapshotted in DetectionTask:

- allowed log sources;
- allowed plugins;
- allowed parsers;
- Capability allowlist;
- deterministic sampling rate;
- per-execution-batch rate bound;
- maximum event and metadata size;
- maximum event count;
- execution timeout;
- correlation time window;
- retention period.

Planner enforces source/parser/plugin/Capability. Runtime enforces permission identity, timeout, count, size, sampling and rate bounds. `retention_days` is persisted as lifecycle governance metadata for a platform-owned cleanup worker; Plugins never delete records. The current in-process batch rate bound is not a distributed token bucket; a future worker provider must enforce global/stream rate limits without changing Policy or Plugin contracts.

## 5. Interoperability Analysis

### Runtime and Workflow

DetectionTask extends the platform Task identity one-to-one. Workflow can route `*.detect` Capabilities and wait for task outcomes without importing Plugin classes. Detection Runtime reuses platform execution principles while owning a detection-specific six-stage lifecycle.

### Asset

Every DetectionTask references an authorized primary Asset. Every SecurityEvent must retain that primary Asset and may add only existing, non-deleted Assets. Plugins supply IDs, but DetectionService validates and creates links.

### Knowledge

SecurityEvent may link existing Knowledge and the exact current KnowledgeVersion, preserving reproducibility if knowledge changes later. Plugins cannot invent Knowledge rows.

### Evidence

SecurityEvent may link existing Evidence. Full raw logs/packets/tool payloads belong in Evidence/object storage, while SecurityEvent keeps normalized bounded facts. Phase 9 allows evidence references but does not add raw telemetry storage.

### Why Event is not Finding

Event answers: what was observed, by which source, at what time, with what confidence? Its lifecycle is NEW/CORRELATED/TRIAGED/IGNORED/ARCHIVED. It may be noisy, repeated and high-volume.

Finding answers: what weakness or assessed risk was established, on which Asset, with what remediation/risk meaning? Its lifecycle and deduplication support vulnerability management.

### Why Finding cannot replace Event

Finding lacks event source/time-series/correlation semantics and would turn transient telemetry into remediation records. Using it for IDS/log events would corrupt risk reporting, deduplication and state transitions. Conversely, SecurityEvent cannot replace Finding because an observation is not proof of a persistent weakness.

### Report

Phase 9 establishes report interoperability through normalized Task/Event entities, but does not change ReportService rendering. A future reviewed phase may aggregate event statistics and correlation groups without granting Plugins report access.

## 6. Rule-Based Correlation

The default engine performs deterministic grouping only:

- time window;
- same canonical Asset;
- same source;
- same IOC;
- same rule.

Groups require at least two events. Matching events transition to CORRELATED and produce audit events. The engine does not use AI, calculate causality, create Incident/Case records or execute response actions.

## 7. Architecture Trade-off Analysis

### Separate Detection context versus extending Assessment

Chosen: separate bounded context sharing platform primitives. This adds explicit mapping but preserves temporal event and remediation Finding semantics.

### SecurityEvent versus tool-native schemas

Chosen: one normalized model. Some tool fidelity is discarded from attributes; full fidelity belongs in Evidence. This prevents external schema drift from becoming a platform breaking change.

### In-process registry/runtime versus external workers

Chosen for Phase 9: in-process Fake Plugin to validate contracts only. This is easy to test but not sufficient isolation for real telemetry engines. Real plugins require reviewed adapters, worker/sandbox isolation and resource/egress policy.

### Synchronous task execution versus queue

Chosen: optional immediate execution inside the service for framework validation. The Task model and plan remain queue-compatible. High-volume stream ingestion and backpressure are deferred.

### Deterministic rule correlation versus AI correlation

Chosen: bounded, explainable rules. This is auditable and reproducible but cannot infer multi-stage behavior. AI correlation is explicitly outside Phase 9.

### Persist each event versus aggregate-only storage

Chosen: persist normalized events and correlation status. This preserves provenance and filtering but increases storage. Retention policy and future partitioning/archival are necessary before production-scale ingestion.

## 8. Data Model Evolution Analysis

Phase 9 adds a Detection bounded context without changing existing Assessment tables:

- `detection_tasks`: one-to-one extension of Task with Plugin, policy snapshot, plan and result summary;
- `detection_plugins`: governed runtime identities and permissions;
- `detection_capabilities`: projection from Plugin to platform Capability;
- `security_events`: normalized temporal facts;
- `event_references`: normalized external references;
- `event_knowledge`: stable Knowledge plus immutable KnowledgeVersion;
- `event_evidence`: existing Evidence provenance;
- `event_assets`: canonical Asset relationships.

Finding and its link tables remain unchanged. SecurityEvent stores no raw nested tool document, Incident ID or Case state. Future evolution may add partitions, event lineage, correlation-group persistence, stream checkpoints and retention jobs through new migrations rather than overloading attributes.

## 9. Technical Debt and Future Gates

- No real detection engine, network capture, host collector, Sigma compiler or SIEM connector.
- In-process runtime is not a security sandbox for third-party code.
- Rate limiting is a deterministic per-execution batch bound, not distributed streaming backpressure.
- Sampling is deterministic over normalized event JSON; production should define stable pre-normalization source IDs where available.
- Retention policy is persisted but cleanup/archival worker is not implemented.
- Correlation groups are audited but not persisted as first-class entities.
- ReportService does not yet render SecurityEvent sections.
- No Incident/Case/Response creation exists in Phase 9.
