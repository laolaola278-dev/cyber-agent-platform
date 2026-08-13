# ADR-0034: Ticket Uses a Unified Platform Model

- Status: Accepted
- Date: 2026-08-01

## Context

Jira Issue, ServiceNow Incident/Task, TheHive Task and other systems expose incompatible fields, statuses, priorities, workflows, transitions and identifiers. Storing one provider schema in CAP would leak vendor coupling into Incident and Workflow and make migration or multi-provider operation difficult.

## Decision

CAP defines a provider-neutral Ticket with `title`, `description`, `priority`, `status`, `external_reference`, `labels`, optional `incident_id`, creator and timestamps. External systems are Notification/Ticket Plugins that map the internal model to provider fields and return a verified external reference. Provider workflow transitions are adapter concerns and cannot directly mutate CAP Incident or Response.

## Consequences

Positive: stable API, provider portability, consistent filtering/audit, support for multiple ticket systems and no vendor fields in Incident.

Negative: provider-only fields require adapter configuration or bounded plugin metadata; status mapping may be lossy; complex provider workflows need explicit mapping and certification.

## Rejected Alternatives

- Adopt Jira Issue as the platform model: vendor lock-in and incompatible transitions.
- Store arbitrary unvalidated ticket JSON only: weak interoperability and audit semantics.
- Reuse Incident as Ticket: conflates security truth with external work tracking and permits transport state to alter Incident lifecycle.
