# ADR-0036: Keep Firewall response provider-neutral and mock-only in Phase 17

## Status

Accepted for Phase 17; subject to Architect Review.

## Context

CAP must demonstrate network-layer response through the existing Response Framework without granting a plugin production firewall access. nftables, iptables, pfSense, OPNsense and OPA expose different policy representations and execution semantics. Network filtering also introduces a control-plane lockout risk that is broader than a single application rule.

## Decision

Introduce a provider-neutral `FirewallRule`, fail-closed `FirewallPolicyProvider`, `FirewallAdapter`, application-scoped `MockFirewallProvider` and `FirewallResponsePlugin`. Keep the mandatory `Plugin -> Adapter -> Provider` boundary and use the existing Response approval, runtime, evidence, audit and rollback services without changing `backend/app/response/*`.

The policy rejects default-route/any-network scopes, over-broad CIDRs, protected management and control-plane networks, protected management ports, direction/chain mismatches, provider-owned IDs and semantic replacement of an enabled rule. Verification is full provider state read-back. Rollback permits only `REMOVE`, `DISABLE` or `RESTORE` with a token bound to the Plan and canonical rule checksum.

## Consequences

- The Response Framework remains unchanged and certifies a second real plugin type.
- Product syntax, rule ordering and enforcement are isolated behind the Adapter/Provider boundary.
- Phase 17 proves governed network response with zero external side effects.
- Application-local mock state is ephemeral and not suitable for production convergence.
- A real provider requires a future ADR for credentials, staged rollout, out-of-band reachability, atomic update, state-table handling, reconciliation, emergency access and compensating rollback.
