# ADR-0023: InvestigationCase Is Independent from Incident

- Status: Accepted
- Date: 2026-07-31

## Context

An Incident is the platform-governed response aggregate and owns the security situation lifecycle. Investigation work is collaborative and may split into parallel tracks: identity analysis, endpoint investigation, evidence review, threat-intelligence enrichment or remediation coordination. Treating that work as Incident fields would couple collaboration to response state, make parallel ownership ambiguous and encourage uncontrolled lifecycle writes.

## Decision

`InvestigationCase` is a separate persistence model and bounded collaboration workspace linked to exactly one Incident. One Incident may own one or more InvestigationCase records. A Case has its own status, owner, assignee, queue, timestamps, attributes and immutable comments. Case creation is exposed only through `IncidentService`; plugins and source-domain services cannot create or mutate Case rows directly.

Incident transitions remain exclusively controlled by `IncidentService` and do not implicitly transition Case status. Case status transitions, reassignment policy and archival rules require a later reviewed workflow contract. Phase 10 provides the initial Case created with an Incident when requested, a platform-owned API for additional Cases, read APIs and append-only comments.

## Consequences

- Parallel investigations are representable without duplicating or splitting the Incident lifecycle.
- Collaboration history is separate from response governance and remains auditable through IncidentTimeline and CaseComment.
- Case APIs require the same authentication, authorization, audit and service boundary as Incident APIs.
- A future Case workflow can evolve independently, but must not bypass IncidentService when it needs to affect the Incident.
- External case-management connectors remain out of scope until a new interoperability and security review.
