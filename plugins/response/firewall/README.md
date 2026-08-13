# Firewall Response Plugin

## Scope

`firewall-response` proves that CAP can govern network-layer rule changes without modifying the Response Framework. Phase 17 is mock-only: there is no firewall credential, socket, HTTP client, shell command, filesystem writer or production device connection.

## Capability and lifecycle

- Capability: `response.firewall`
- Execution: `initialize -> plan -> validate -> execute -> verify -> shutdown`
- Rollback: `initialize -> validate -> rollback -> verify -> shutdown`
- Permissions: `response.execute`, `response.verify`, `response.rollback`
- Approval: mandatory for every rule change.

## Rule contract

`FirewallRule` models `id`, `name`, `action`, `direction`, source/destination CIDR, protocol, source/destination ports, table, chain, priority, version, status, explicit impact scope and canonical SHA-256 checksum. The model is deliberately provider-neutral and maps to nftables Table/Chain/Rule, iptables Rule/Target/Policy, pfSense Policy/Alias, OPNsense Firewall Rule and OPA policy-decision concepts only through an Adapter.

## Safety controls

1. Any-network, default-route and excessively broad CIDR scopes are rejected.
2. Only the `filter` table and direction-aligned `INPUT`, `OUTPUT` or `FORWARD` chains are accepted.
3. Management/control-plane CIDRs, loopback, link-local and multicast networks are protected.
4. Blocking protected management ports is rejected; broad ANY-protocol deny rules are rejected.
5. Provider-owned IDs and enabled-rule semantic replacement are rejected.
6. Rules require explicit Incident/Asset scope, governed approval and deterministic checksum.
7. Verification performs provider state read-back and compares desired with observed state.
8. Rollback is restricted to `REMOVE`, `DISABLE` or `RESTORE` and requires an execution-issued token bound to Plan, Incident, rule version and checksum.

## Provider boundary

`MockFirewallProvider` is an application-scoped in-memory state store with `network_access=false` and `production_access=false`. A real provider requires a later ADR covering credential isolation, canary/staged rollout, out-of-band management reachability, transactional update, state-table effects, reconciliation, emergency bypass and rollback compensation.
