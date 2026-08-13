# ADR-0018: OWASP ZAP Uses Daemon Mode and the Official API

- Status: Accepted
- Date: 2026-07-31

## Context

ZAP is a stateful Java DAST platform with Session, Context, Spider, Passive Scanner, Active Scanner and Alert subsystems. Embedding ZAP internals or invoking ad-hoc CLI scripts from an Assessment Plugin would leak tool lifecycle, HTTP transport and mutable Alert types into CAP's Assessment Runtime.

## Decision

CAP runs a version-pinned ZAP daemon in an isolated deployment boundary and controls it through a typed `ZapApiClient` port implemented by `ZapV2ApiClient` over the official `zap-api-python` package. `ZapAssessmentPlugin` depends only on `ZapAdapter`; the Adapter owns Session/Context/policy/API/error handling. Daemon resource and network requirements are declared by `ZapSandboxProfile` and enforced by the selected Sandbox/deployment provider.

The API is authenticated, bound to loopback or an isolated service network, file transfer is disabled, dynamic add-ons are disabled, and every run receives a unique non-persistent Session and Context.

## Consequences

- Assessment Runtime and unified platform models remain unchanged.
- ZAP state is isolated behind an anti-corruption layer and can be mocked without network access.
- Daemon startup and health are operational concerns rather than Plugin process calls.
- API/daemon version compatibility must be pinned and tested during upgrades.
- A production container/remote-worker provider must enforce CPU, memory, timeout and egress policy; `ZapSandboxProfile` is the provider-neutral contract.
