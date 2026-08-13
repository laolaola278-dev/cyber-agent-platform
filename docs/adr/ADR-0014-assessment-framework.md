# ADR-0014: Assessment Uses a Framework

- Status: Accepted
- Date: 2026-07-31

## Context

CAP must support heterogeneous assessment tools without becoming a scanner or allowing tool integrations to own platform data and policy.

## Decision

Implement Assessment as a framework comprising Planner, Registry, Runtime, Policy, Plugin SDK, Result Normalizer and Risk Engine. Real tools are adapters/plugins outside the control-plane core. Plugins receive a narrow context and return only AssessmentResult.

## Consequences

- New tools can be added without changing Finding, Asset, Knowledge, Evidence or Report ownership.
- Policy and audit are uniformly enforced.
- Plugins cannot directly access the database or write reports.
- Real scanners require a future isolated worker/adapter before approval.
