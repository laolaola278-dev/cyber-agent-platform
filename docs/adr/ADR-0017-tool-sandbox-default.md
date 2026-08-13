# ADR-0017: Tool Sandbox Is the Default Execution Boundary

- Status: Accepted
- Date: 2026-07-30

## Context

Assessment tools are external programs that may consume untrusted target responses, execute template-defined network actions, write files, use credentials, exceed resource limits, or expose implementation-specific process controls. Allowing Plugins to call subprocess APIs directly would bypass platform governance and make future isolation changes invasive.

## Decision

Every external Assessment Tool must execute through an injected `SandboxProvider`. Plugins call typed Adapters; Adapters build governed `SandboxCommand` values; Sandbox Providers own process creation and isolation.

Phase 7 provides `LocalProcessSandbox` as the deployable baseline and reserves `DockerSandbox`, `FirecrackerSandbox`, and `RemoteWorkerSandbox` extension points. The local provider is shell-free, executable-allowlisted, stdin-disabled, environment-minimized, timeout-bounded, output-bounded, and working-directory validated.

## Consequences

- Process execution is centralized and auditable.
- Plugins cannot obtain arbitrary shell access.
- Replacing local execution with container, microVM, or remote-worker isolation does not change Plugin contracts.
- Local process isolation does not provide a separate kernel, filesystem namespace, or network namespace; high-risk production deployments should select a stronger Provider.
- Provider-specific telemetry, egress policy, CPU/memory quotas, and immutable filesystem controls remain future work.
