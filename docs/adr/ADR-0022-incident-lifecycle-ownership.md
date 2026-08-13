# ADR-0022: Incident as the Unified Response Event Model

- Status: Accepted
- Date: 2026-07-31

## Context

Assessment produces Findings and Detection produces SecurityEvents. Neither a confirmed weakness nor a correlated observation is automatically an operational Incident. Direct Incident writes from plugins, workflows or source-domain services would bypass escalation policy, duplicate correlation, SLA assignment, lifecycle guards, relationship validation and audit publication.

The platform also needs collaboration records without collapsing distinct semantics: Incident is the governed response aggregate, InvestigationCase is an investigation workspace, Timeline is immutable activity history, and Artifact is a typed reference or bounded indicator.

## Decision

Incident is CAP's unified response-event aggregate: the governed object through which the platform triages, assigns, investigates, contains, resolves and closes security situations. This does not replace ADR-0021's SecurityEvent detection model or the Assessment Finding model. Finding remains a weakness fact, SecurityEvent remains a time-bound detection fact, and Incident references either through explicit associations without copying their payload or lifecycle.

`IncidentService` is the exclusive owner of Incident creation, merge, assignment, state transition, SLA calculation, cross-domain linking and audit events. `IncidentPlanner` validates source and escalation thresholds, while `IncidentRuntime` executes only the fixed internal sequence `validate -> correlate -> create -> link -> audit`. Neither is exposed as a plugin extension point.

Assessment and Detection may produce `IncidentCandidate` values only. Plugins cannot access `AsyncSession`, repositories, Workflow, Assessment, Detection, Report or IncidentService. Automatic creation and escalation are configuration-first and fail closed. Manual creation remains governed by the same service and lifecycle.

Incident stores response governance metadata rather than copying source facts. Explicit link tables reference Finding, SecurityEvent, Asset and exact KnowledgeVersion records. Timeline rows and Case comments are append-only. Duplicate candidates within the policy window merge new links into the existing Incident and append a MERGED timeline entry rather than creating a second source of truth.

## Consequences

- Incident transitions are deterministic and audited through one state machine.
- Finding and SecurityEvent retain independent lifecycle ownership and provenance.
- Duplicate suppression is explainable through the correlation key and timeline.
- Cross-domain deletion is restricted, preventing dangling Incident references.
- Case collaboration and Incident state remain separate but traceable.
- Real response automation, approval-gated containment and external case connectors remain future reviewed work; Phase 10 does not execute response actions.
