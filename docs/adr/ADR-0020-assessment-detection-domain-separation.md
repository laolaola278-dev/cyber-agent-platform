# ADR-0020: Assessment and Detection Use Separate Domain Models

- Status: Accepted
- Date: 2026-07-31

## Context

CAP already models Assessment activities as bounded evaluations that produce Findings. Detection instead ingests time-bound observations from logs, traffic, hosts, IDS sensors and rules. Reusing AssessmentTask/Finding would collapse two different lifecycles: an Assessment Finding represents a confirmed weakness or risk conclusion, while a SecurityEvent represents something observed at a specific time and source that may later be correlated, triaged, ignored or archived.

## Decision

Keep Assessment and Detection as separate bounded contexts. They reuse platform Task, Capability, Asset, Evidence, KnowledgeVersion, Workflow, Audit and Report integration points, but own separate Planner, Registry, Runtime, Policy, Plugin SDK, result DTO, persistence model and state machine.

Assessment produces `AssessmentResult -> Finding`. Detection produces `DetectionResult -> SecurityEvent`. Neither domain model subclasses or substitutes for the other. Cross-domain workflows may reference both by stable IDs without merging their persistence schemas.

## Consequences

- Finding lifecycle remains remediation-oriented; SecurityEvent lifecycle remains observation/triage-oriented.
- Detection can support high-volume temporal correlation without polluting vulnerability deduplication and risk semantics.
- Shared platform services prevent infrastructure duplication while preserving domain ownership.
- Reports and future Incident/Case components must explicitly aggregate both model types rather than assuming one universal record.
- Additional mapping code is required at cross-domain boundaries, but semantic ambiguity and breaking migrations are avoided.
