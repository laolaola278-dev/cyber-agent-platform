# ADR-0019: ZAP Passive Scan Is Default and Active Scan Is Default-Deny

- Status: Accepted
- Date: 2026-07-31

## Context

OWASP ZAP explicitly describes Active Scan as an attack against its targets. A generic DAST capability, arbitrary URL input, or a default policy that includes Active Scan could cause unauthorized or out-of-scope traffic.

## Decision

`ZapPolicy` enables Passive Scan and disables Active Scan by default. `web.active_scan` is supported by the Capability Registry but removed from the default `AssessmentPolicy.capability_allowlist`.

Active Scan runs only when all controls pass:

1. the request explicitly sets `active_scan_enabled=true` and chooses an allowlisted active Scan Policy;
2. the Policy explicitly includes `web.active_scan`;
3. the referenced Asset has `properties.assessment.active_scan_authorized=true`;
4. the Planner accepts Asset allow/deny and Capability rules;
5. the Adapter constrains the Context to the platform-derived HTTP(S) target and enforces Sandbox timeout.

The API accepts `asset_id`, never an arbitrary target. Context include regex is generated from the Asset origin/path, exclusions are policy-owned, and max URL/depth/time/concurrency/request limits are bounded.

## Consequences

- Accidental active attacks are fail-closed.
- Active authorization is explicit, reviewable and auditable at both Asset and Policy levels.
- Passive mode still accesses the Asset once and can optionally spider; it is lower risk, not zero-network.
- Organizations must establish an Asset authorization workflow before active DAST is operationally enabled.
