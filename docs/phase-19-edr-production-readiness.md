# Phase 19 EDR Production Integration Readiness

## Status

Phase 19 is **not production ready by design**. `MockEDRProvider` has no network, credential, filesystem or shell capability. A production provider is a future Architect-approved change and must not be enabled by configuration alone.

## Identity and authentication

- Use a dedicated CAP workload identity per tenant/environment and Provider region.
- Prefer OAuth2 client credentials or workload identity federation; never accept user passwords.
- Separate read (`host.read`, action status) and write (`host.isolate`, `host.unisolate`) scopes.
- Bind tenant, Provider base URL, audience and allowed action scope; reject cross-tenant host IDs.
- Provider actor and CAP requester/approver/operator identities must remain separately attributable.

## API token and secrets

- Store only opaque Secret Provider references in Manifest/Worker records; never place tokens in Response Plan, logs, Evidence or environment maps.
- Resolve secrets just-in-time in the Provider boundary and keep lifetimes shorter than action timeout plus polling budget.
- Support rotation with overlap, revocation drill and audit of reference access; never retry authentication failures with stale tokens indefinitely.
- Phase 19 Manifest declares no secret references because the Mock Provider needs none.

## Network and TLS

- Allow only documented Provider regional API FQDNs through sandbox egress; deny direct Endpoint connectivity.
- Enforce TLS 1.2+, certificate and hostname validation, bounded DNS results, proxy policy and tenant-region pinning.
- Isolated devices may retain only Provider management-cloud connectivity; validate VPN/split-tunnel and out-of-band recovery before rollout.
- Provider callback/webhook ingress, if introduced, requires signature verification, replay protection and a separate ADR.

## Timeout, asynchronous state and retry

- Separate connect, request and total action timeouts; asynchronous action acceptance is not success.
- Poll Provider action status with a bounded deadline and terminal states: succeeded, failed, timed out, cancelled.
- Retry only transport failures, 429 and explicitly retryable 5xx responses with exponential backoff plus jitter and `Retry-After` support.
- Never blind-retry ambiguous writes without an idempotency key and read-back. Authentication, authorization, validation and not-found failures are non-retryable.

## Idempotency and concurrency

- Map `HostAction.id` to Provider external/correlation ID when supported; bind it to canonical checksum.
- A repeated ID with identical checksum returns the same receipt; a repeated ID with different checksum fails closed.
- Use per-host action serialization or optimistic observed-version checks. Conflicting isolate/unisolate actions must not race.
- Reconcile accepted actions through read-back before CAP marks execution or rollback verified.

## Upgrade and compatibility

- Pin and test Provider API version, SDK version, enum/status mappings, rate limits and deprecation dates.
- Contract-test isolate, unisolate, host lookup, action status, error mapping, pagination and tenant/region behavior.
- Roll out by disabled -> read-only health/read-back -> canary host -> bounded group; never globally enable through a package upgrade.
- Retain the previous Provider adapter and rollback runbook until post-upgrade verification completes.

## Required runbooks

1. Token compromise, rotation and emergency revocation.
2. Provider outage/rate-limit saturation and queued action handling.
3. Host isolation stuck pending/failed and out-of-band recovery.
4. Accidental isolation: validate identity, execute approved unisolate, read back, preserve Evidence.
5. Accidental unisolate: re-triage risk and require a new approved isolate Plan; no silent auto-remediation.
6. Agent offline or host missing: stop, do not assume success, use alternate containment and investigate inventory drift.
7. API/SDK upgrade rollback and compatibility freeze.
8. Audit/Evidence export and incident escalation.

## Production entry gate

Production enablement requires a new ADR, threat model, secret/network configuration, vendor sandbox tests, rate-limit/load tests, disaster-recovery exercise, SOC runbook sign-off, least-privilege permission review and Architect approval. Phase 19 itself satisfies none of these by connecting to a live tenant; it validates only the framework boundary.
