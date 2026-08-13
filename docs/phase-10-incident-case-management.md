# Phase 10 Incident and Investigation Case Management

## 1. Architecture

```text
Finding / SecurityEvent / Manual request
        -> IncidentCandidate
        -> IncidentPlanner + IncidentPolicy
        -> IncidentRuntime (fixed internal plan)
        -> IncidentService (exclusive lifecycle owner)
        -> Incident + Timeline + InvestigationCase + Artifact
        -> explicit Finding/Event/Asset/KnowledgeVersion links
        -> Audit event subscribers
```

Phase 10 establishes the platform control-plane framework only. It does not connect TheHive, Cortex, MISP, SOAR products, ticketing systems or response executors, and it does not perform containment. Assessment and Detection remain upstream fact domains; Incident is a separate governed response aggregate.

## 2. Lifecycle and Ownership

Incident states are `NEW -> TRIAGED -> INVESTIGATING -> CONTAINED -> RESOLVED -> CLOSED`, with policy-controlled `REOPENED` returning to TRIAGED or INVESTIGATING. Illegal jumps fail closed through the shared state-machine boundary. Resolution and closure timestamps are written only during legal transitions.

`IncidentService` alone creates, merges, assigns and transitions Incident records. Planner enforces trusted source, severity/confidence thresholds, correlated event count, priority and SLA policy. Runtime accepts only the fixed five-step plan and delegates persistence back to the service; it is not a plugin runtime.

InvestigationCase has an independent collaboration lifecycle (`OPEN`, `ACTIVE`, `ON_HOLD`, `COMPLETED`, `CLOSED`). Phase 10 creates the initial Case with an Incident when requested, permits additional Cases only through the platform-owned IncidentService, and supports immutable comments; full Case status mutation is intentionally deferred until its workflow policy is reviewed.

## 3. Source and Correlation Boundaries

- MANUAL: accepted through the same governed service, with deterministic correlation key supplied in attributes or trace-based fallback.
- ASSESSMENT: requires existing Finding IDs and configured automatic creation, severity and confidence thresholds.
- DETECTION: requires existing SecurityEvent IDs, configured automatic escalation and minimum correlated event count.
- Plugins cannot create Incident or Case rows and receive no database/service references.

`IncidentCorrelation` builds candidates deterministically from Findings or time-windowed SecurityEvents grouped by source, rule or canonical Asset. It does not mutate source records or create lifecycle entities.

## 4. Data Model

- `incidents`: title, description, severity, confidence, priority, status, source, ownership/queue, classification/risk, correlation key, duplicate relation, SLA and closure timestamps, bounded attributes.
- `incident_timelines`: append-only event type, actor, description, from/to status and bounded details.
- `incident_artifacts`: typed platform reference or bounded URL/hash/IP/domain value.
- `investigation_cases`, `case_comments`: investigation workspace and immutable collaboration notes.
- `incident_findings`, `incident_events`, `incident_assets`: explicit restricted cross-domain relationships.
- `incident_knowledge`: stable Knowledge plus exact immutable KnowledgeVersion.

Incident never copies Finding, SecurityEvent, Asset, Evidence, Report or Knowledge payloads. Platform artifacts validate referenced object existence before persistence. Value artifacts require a value and are size bounded by Pydantic contracts.

## 5. Duplicate Merge and SLA

Duplicate merge uses an indexed correlation key and configured time window. A matching non-closed root Incident receives only missing cross-domain links and a MERGED timeline/audit event; a second Incident row is not created. The implementation is deterministic but currently depends on transaction isolation rather than a partial uniqueness constraint, so concurrent duplicate creation requires a future serialization/advisory-lock strategy.

Priority derives from severity unless explicitly overridden. SLA due time is calculated from the effective priority using configuration for every priority. Priority reassignment recalculates SLA from assignment time and is audited.

## 6. API

- `POST /incidents`
- `GET /incidents`
- `GET /incidents/{incident_id}`
- `POST /incidents/{incident_id}/transition`
- `POST /incidents/{incident_id}/assign`
- `POST /incidents/{incident_id}/artifacts`
- `POST /incidents/{incident_id}/cases`
- `GET /cases`
- `GET /cases/{case_id}`
- `POST /cases/{case_id}/comments`

List APIs use the shared `items/page/page_size/total` envelope. Incident filters cover severity, status, priority, owner, assignee and queue; Case filters cover Incident, status and assignee.

## 7. Security and Audit

All mutating operations publish immutable platform events consumed by the transactional audit subscriber: IncidentCreated, IncidentMerged, IncidentTransitioned, IncidentAssigned, IncidentArtifactLinked and CaseCommentAdded. Cross-domain IDs fail closed when missing. Database constraints enforce finite enum values and restricted references.

The framework does not execute response actions. Future containment/remediation must use explicit permissions, approval gates, sandboxed adapters and new Architect review rather than extending Incident attributes or bypassing IncidentService.

## 8. Technical Debt and Future Gates

- Concurrent duplicate candidates need database-level serialization or advisory locking.
- InvestigationCase status transitions require a reviewed Case workflow policy; additional Case creation is already platform-owned through `IncidentService`.
- Timeline immutability is enforced by service/API design, not a database UPDATE/DELETE trigger.
- Artifact platform references are polymorphic and validated in the service; only explicit Incident link tables have physical foreign keys.
- No retention/archive worker, SLA breach scheduler, notification provider, metrics or external Case connector.
- No approval-gated response action or automatic closure; both remain outside Phase 10.
