# ADR-0033: Notification Is a Platform Capability

- Status: Accepted
- Date: 2026-08-01

## Context

Incident, Response and Workflow all need outbound communication, but Email, Webhook, Chat, SMS and Ticket systems differ in recipients, credentials, rate limits, retries, verification, templates and outage behavior. Embedding adapters in each domain duplicates controls and grants transports cross-domain mutation authority.

## Decision

Create an independent Notification bounded context with Service, Planner, Policy, Routing, Template Provider, Registry, Runtime, Result, Verification, Evidence, Audit and Plugin SDK. Incident and Response Plan are read-only references. Only NotificationRuntime invokes certified plugins. Recipients originate from platform allowlisted groups and routes.

## Consequences

Positive: one governance plane, auditable policy snapshots, bounded plugins, provider portability, consistent storm controls and failure isolation.

Negative: an additional persisted Plan/Execution layer and mapping work for each external provider. Distributed queueing and provider-specific retry semantics remain future adapters/runtime work.

## Rejected Alternatives

- Put notification code in IncidentService: excessive coupling and transport failures could affect Incident state.
- Put notification code in ResponseService: notification is not necessarily a response and requires distinct safety controls.
- Let workflows call providers directly: bypasses routing, recipient allowlist, template, verification, evidence and audit boundaries.
