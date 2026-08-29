# ADR-0037: Production Sandbox and Egress Defaults

## Status

Accepted for v1.0.1-rc1; subject to Architect Review.

## Context

v1.0.0 shipped a real isolation sandbox (OCI containers, Kubernetes Pods) but
the *decision* about which provider may run in production was left to
configuration, and the configuration had safe-looking defaults that were not
safe:

- `SANDBOX_PROVIDER` defaulted to `subprocess-sandbox`, which runs the
  acquisition payload as a child process of the worker with no network,
  filesystem, container or resource isolation.
- The provider-selection chain ended in a catch-all `else` that returned
  `MemorySandboxProvider` (zero isolation, in-process) for ANY unrecognised
  provider name.
- `EGRESS_PROXY_URL` defaulted to empty in the Helm chart, and nothing checked
  whether enforcement was actually configured.

None of these are exploitable in isolation — the application-layer validator
(`URLPolicyValidator`, layer 1) still runs inside every sandbox — but they
defeat the *defense-in-depth* model the certification argues for, and one of
them fails open: a misspelled provider name silently removes all isolation.

The trap that shaped this ADR: `SubprocessSandboxProvider` declares
`real_isolation = True`, and that is *true* — it is a separate OS process. A
policy written as "admit the provider when `real_isolation` is true" admits a
provider with no network, filesystem, container or resource isolation.

## Decision

Production sandbox admission is decided on the provider's *capability set*,
never on its name, and never on a single capability flag.

A provider is admitted in production only when it declares all of:

| Capability | Why it is required |
| --- | --- |
| `real_isolation` | the execution is not the worker process |
| `network` | the sandbox has its own network domain to enforce |
| `container` or `vm` | a real execution domain with a lifecycle |
| `resource` | CPU/memory limits are actually applied |

Admission is fail-closed in both directions:

- a provider that under-declares is rejected, and
- an **unknown provider name is a violation, not a fallback**. There is no
  silent downgrade to a weaker provider. Ever.

Egress enforcement is checked only for providers that declare `network`. When
it is required and missing, the worker refuses to start. The error states the
threat model precisely: *"Application-layer URL validation (layer 1) remains
active, but defense-in-depth network enforcement is absent."* A missing proxy
is not described as "no SSRF protection" — that would be false, and it would
also overstate what layer 1 alone can guarantee.

The egress proxy is part of worker readiness. In production the health probe
TCP-checks the proxy and fails readiness when it is unreachable. There is no
direct-egress fallback: under a NetworkPolicy that denies all egress except
the proxy, an absent proxy means acquisition fails loudly instead of leaking
traffic or silently producing empty network evidence.

### Old behaviour

| Area | v1.0.0 |
| --- | --- |
| Production provider | whatever `SANDBOX_PROVIDER` said; `subprocess-sandbox` by default |
| Unknown provider name | `MemorySandboxProvider` (zero isolation), silently |
| Missing `EGRESS_PROXY_URL` | allowed; direct egress, no warning |
| Readiness | proxy reachability not checked |
| Compose `APP_ENVIRONMENT` | `production` (with MinIO dev credentials) |
| Helm `worker.egressProxyUrl` | empty |

### Threat model

1. **Isolation removed by typo.** `kubernettes-sandbox` → every acquisition
   runs in-process. No log line, no error, no signal.
2. **Isolation removed by drift.** A provider renamed or removed upstream
   leaves the `else` branch to absorb the change silently.
3. **Single-tenant blast radius.** Without network/filesystem/resource limits,
   a hostile acquisition target shares the worker's address space, file
   descriptors and memory.
4. **Weakened second line.** Without egress enforcement, layer 1
   (`URLPolicyValidator`) is the *only* SSRF control. It is a good control, but
   one bug in it is now a whole-network problem instead of a partial one.
5. **Silent evidence corruption.** A NetworkPolicy that denies all egress means
   an unproxied acquisition does not leak — it fails. Failing loudly is
   required so operators see it.

### New behaviour

- Production: violation → `logger.error` + exit code 2, no fallback.
- Development/test: a weak provider is allowed, and logs a **one-shot**
  warning at startup: *"Development sandbox provider in use … Not approved for
  production isolation."*
- Readiness gains an `egress_enforcement` check; it is only *required* in
  production, so dev/test are unaffected.
- The check lives in the acquisition worker's startup path, not in global
  `Settings`: the API process legitimately runs without a sandbox provider,
  and a global validator would take it down.

### Development compatibility

- `APP_ENVIRONMENT` continues to gate everything: `development` and `test`
  keep working exactly as before, including `memory-sandbox` and
  `subprocess-sandbox`.
- `docker-compose.yml` now defaults `APP_ENVIRONMENT` to `development`. The
  previous `production` default described a stack this compose file does not
  provide (MinIO development credentials, a locally built image), and after
  this change it would also trigger the production gates.
- No new environment variable was introduced for this policy. CAP already has
  the `environment` concept; reusing it avoids a second, competing notion of
  "what mode am I in".

### Migration guidance

Upgrading from v1.0.0:

1. **Helm (recommended path).** No action required — the chart already sets
   `SANDBOX_PROVIDER=kubernetes-sandbox`, and `worker.egressProxyUrl` now
   defaults to the chart's own egress-proxy Service
   (`http://<release>-egress-proxy:8080`). Confirm the egress-proxy Deployment
   is running before the worker rolls.
2. **Bare-metal / custom deployments.** Two things will now stop the worker
   from starting, by design:
   - `SANDBOX_PROVIDER` names a provider without the required capabilities →
     set it to `oci-sandbox` (a real container runtime) or
     `kubernetes-sandbox`.
   - the provider declares `network` but `EGRESS_PROXY_URL` is unset → set it
     to the proxy, or drop to a non-production environment if you really
     intend direct egress (not supported in production).
3. **Check for silent downgrades.** If you were running with an unrecognised
   provider name, v1.0.0 was executing in-process. Audit any evidence
   collected under that configuration — it was produced with zero isolation.
4. **Verify readiness.** After the rollout, `egress_enforcement` must be
   `true` in the worker's readiness output. A `false` there means the worker
   will not acquire reliably.

## Consequences

- A misconfigured production deployment fails at startup with an actionable
  message instead of running with weak or no isolation.
- The failure mode for a typo in `SANDBOX_PROVIDER` changes from "silent
  downgrade" to "refuse to start" — this is intentional and is the point of
  the patch.
- Operators with custom bare-metal deployments must set two values before
  upgrading; the startup error names both.
- Admission now depends on providers declaring their capabilities honestly.
  `KubernetesSandboxProvider` was under-declaring (`container`, `process` and
  `resource` were left at their defaults while the Pod spec sets
  `resources.limits/requests` and runs in a container PID namespace); that is
  fixed here. A new provider that lies about its capabilities is admitted, so
  capability declarations are now part of provider review.
- The policy is enforced at worker startup, not per-execution, so a provider
  swapped at runtime is not re-validated. Providers are static configuration;
  accepting this is deliberate.
