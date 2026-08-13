# EDR Response Plugin

## Scope

`edr-response` validates that CAP can govern a high-privilege endpoint response integration without changing the Platform Plane, Worker Framework, Sandbox Framework or Response Framework. Phase 19 is provider-neutral and mock-only. It has no EDR credential, API client, socket, shell, subprocess, filesystem writer, database access or path to a real endpoint.

## Capability and lifecycle

- Capability: `response.edr`
- Implemented actions: `host.isolate`, `host.unisolate`
- Reserved actions: `process.terminate`, `collect.package`
- Execution: `initialize -> plan -> validate -> execute -> read-back -> verify -> evidence -> audit -> shutdown`
- Rollback: `initialize -> validate -> host.unisolate -> read-back -> verify -> evidence -> audit -> shutdown`
- Permissions: `response.execute`, `response.verify`, `response.rollback`
- Approval: mandatory; CAP also enforces a distinct approver.

## Typed HostAction

`HostAction` is stored as typed JSON in the existing Response Plan. It contains exactly `id`, `host_id`, `action`, `status`, `version`, `checksum`, `requested_by`, `approved_by`, `reason`, and `created_at`. The canonical SHA-256 checksum excludes mutable Provider status and binds the desired action identity and authorization context.

## Security boundary

The Plugin translates only the governed lifecycle and cannot access an Endpoint. `EDRAdapter` is the exclusive parameter, capability, Provider, verification and rollback boundary. Only a future production Provider may own network clients and secrets. The current `MockEDRProvider` declares and enforces:

- `network_access = false`
- `production_access = false`
- `filesystem_write = false`
- `shell_execute = false`

Every action requires a Response Plan, immutable Incident/Asset scope, approval and exact Host Asset UUID match. `HostAction.approved_by` remains null in requester-supplied Typed JSON; authoritative approver identity is owned by existing Response Approval/Audit records and cannot be forged by the Plugin payload. The Plugin cannot decide approval, access repositories or modify Incident/Asset records.

## Verification, rollback and drift

Execution succeeds only after Provider read-back shows the expected host isolation state, target host is present, Agent is online and the observed last action ID matches the requested action. Evidence contains desired/observed state, action checksum, Provider reference and zero-access flags. Rollback accepts only a token bound to Plan, Incident, HostAction identity/version/checksum and performs `host.unisolate` with independent read-back verification.

Observed state mismatch is recorded as `drift_detected=true` and `incident_candidate=true`. Phase 19 never auto-remediates drift and never creates or mutates an Incident directly.

## Production readiness gate

A production Provider requires a future Architect-approved ADR and phase covering OAuth/API tokens, secret references, tenant/region endpoints, egress allowlists, TLS, timeout and rate-limit budgets, safe retry classification, idempotency keys, asynchronous action polling, version compatibility, canary rollout, emergency access, monitoring and rollback runbooks. This plugin is not production-enabled.
