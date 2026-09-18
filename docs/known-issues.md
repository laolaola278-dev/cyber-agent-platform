# Known Issues for 1.0.5

> Supersedes the `1.0.1-rc1` set. Items closed by later releases are removed
> here and recorded in [`CHANGELOG.md`](../CHANGELOG.md) and
> [`docs/releases/`](releases/). Capability disclosure (Response Plane
> provider status, the Zeek input-format boundary, reserved interfaces) is
> carried forward unchanged -- it describes shipped behaviour, not defects.

## Release blockers

None carried from v1.0.0: Phase 28.7 GA Reliability Certification passed 40/40
gates under `CAP_GA_STRICT=1` at certified commit
`b22b7be57f89cd0ef0cf9df8b289ec1f5e74b2b3` (v1.0.0-rc4).

**Current release.** `1.0.5` is the released version (tag `v1.0.5`), certified at
anchor `901013a`: strict GA 40/40 gates PASS with `full_ga_certified: true`, plus
a 7200s reliability soak (run `34116570119`, 480/480 healthy ticks, 0 HTTP
errors, 0 downtime, 48 pod-kill recoveries). Released versions on this line are
v1.0.0, v1.0.1, v1.0.2, v1.0.3, v1.0.4 and v1.0.5.

**Unreleased work.** The `## [Unreleased]` section of the changelog lists
post-1.0.5 fixes (CI image provenance, a stale committed `vite.config.js` that
shadowed the console build config, the broken `make lint` target, and several
Console error-state and dead-control defects). Under the version policy these
require a new RC (`1.0.6-rc1`) and a fresh runtime certification before release;
they are not part of the shipped v1.0.5 assets.

## Operational limitations

- Identity is supplied by a trusted reverse proxy; CAP does not provide an OIDC login implementation. Production gateways must overwrite identity headers.
- **Audit attribution is client-supplied.** Transition/assign/decision endpoints take
  an `actor` (or `approver`) in the request body, and the Web Console submits the
  fixed string `console-operator`. Nothing binds that value to the authenticated
  principal, so audit rows record the console, not a person. The trusted-proxy
  header (`X-CAP-User`) is the only real identity control; a deployment that
  needs attributable actions must derive the actor server-side from that header.
  Changing the request contract is an API-freeze break and needs an ADR.
- User/Role/Permission directories are immutable in v1; there is no user-management write API.
- **The Web Console has no router.** Navigation is a state switch in `App.tsx`, so
  per-page state (investigation and acquisition results, filters, selections) is
  discarded on every menu click.
- **The Web Console has no automated test suite.** Its CI gate is TypeScript
  typecheck + ESLint + production build; API contracts are covered by the backend
  suite, but no test drives a console user flow.
- OpenTelemetry spans are not exported when `OTEL_EXPORTER_ENDPOINT` is empty.
- Metrics and API docs are public application paths; production networks must restrict metrics, while API docs default to disabled.
- The Web Console defaults to `read-only` for local Compose. It is not a production identity solution.
- The console entry chunk is ~578 kB (183 kB gzipped) after route-level code
  splitting and still trips the 550 kB warning. This is the antd core shared by
  most views; it is a size warning, not a correctness defect.

## Response Plane — production provider status

**The Response Plane ships with simulation/test providers only. It is not
integrated with any production EDR, WAF or firewall device.** The approval,
evidence, audit and rollback framework around it is real and certified; the
device adapters behind it are not.

| Capability | Provider | Identifier | Production device integration |
|---|---|---|---|
| EDR | `MockEDRProvider` | `mock-edr` | **None** |
| WAF | `MockWAFProvider` | `mock-waf` | **None** |
| Firewall | `MockFirewallProvider` | `mock-firewall` | **None** |

There is no non-mock provider implementation in the tree. Enabling one is not a
configuration option: each of the three policies (`EDRPolicy`, `WAFPolicy`,
`FirewallPolicy`) declares `mock_only: bool = True` and a model validator
raises `ValueError("... policy must remain mock-only")` when it is set to
`False`. A production operator therefore cannot turn a simulation provider into
a production provider by changing an environment variable — that requires a new
ADR plus a real provider implementation.

See ADR-0035 (WAF) and ADR-0036 (firewall), and
`docs/phase-19-edr-production-readiness.md`, which states that Phase 19 is
"not production ready by design".

### EDR action detail

| Action | Status |
|---|---|
| `host.isolate` | Simulated. State transitions are recorded in the mock provider store and returned with a `mock-edr://` provider reference. |
| `host.unisolate` | Simulated, as above. |
| `process.terminate` | **Reserved / unavailable.** Listed in `EDRPolicy.reserved_actions`; the policy rejects it as executable. |
| `collect.package` | **Reserved / unavailable.** Listed in `EDRPolicy.reserved_actions`; the policy rejects it as executable. |

### WAF action detail

Allowed and simulated: `BLOCK`, `LOG`. Rollback: `REMOVE`, `DISABLE`,
`RESTORE`. `ALLOW` rules are rejected by policy ("Broad allow rules are not
permitted"). Rules are stored in the mock provider store and referenced as
`mock-waf://rules/{id}/{version}`; nothing is pushed to a WAF product.

### Firewall action detail

Allowed and simulated: `BLOCK`, `REJECT`, `LOG` on the `filter` table across
the `INPUT`, `OUTPUT` and `FORWARD` chains. No rule reaches a host firewall or
network device; references are `mock-firewall://tables/{table}/chains/{chain}/`.

### Out of scope for this patch

Real integrations (CrowdStrike, Microsoft Defender, Palo Alto, Cloudflare WAF,
AWS WAF, host firewall commands) are **not** planned for 1.0.1. Each needs a
new ADR, a credential model, rate limits, rollback semantics, a vendor
contract and an integration test environment.

## Zeek adapter — input format

The Zeek adapter requires **JSON output / JSONL** input. It parses JSONL and
rejects TSV.

Zeek's default ASCII log format is TSV, so an unconfigured sensor produces
exactly the input this adapter cannot read. Deployments must configure Zeek for
JSON logs (for example `LogAscii::use_json=T`) or convert TSV to JSONL before
ingest. TSV input raises `DetectionPolicyViolation` with that remediation in
the message and in `details.remediation`.

This is a supported-format boundary, not "Zeek is unsupported": JSONL ingest,
allowlisted sources, bounded reads, per-line lineage and schema fingerprints all
work. TSV parsing is not implemented and is not planned for 1.0.1.

## Reserved / unimplemented interfaces

These exist in the tree and are **not** production implementations. They are
listed so that an import name is not mistaken for a working capability.

- `app/sandbox/reserved.py` — `DockerSandbox`, `FirecrackerSandbox` and
  `RemoteWorkerSandbox` raise `NotImplementedError`. They are exported from
  `app.sandbox` but never instantiated at runtime, so they are not reachable
  and are not a GA blocker. Firecracker microVM isolation in particular does
  not exist and is not planned for 1.0.1.
- `app/assessment/runtime.py` — `AssessmentScheduler.schedule()` raises
  `NotImplementedError` and is never called from any production path. It is an
  unimplemented extension point, recorded here so it is not read as a
  scheduling capability.

## GA known limitations (carried from the Phase 28.7 readiness report)

1. **24-hour soak not yet executed.** The certified soak ran for 2 hours
   (7,200 s): 0 false reclaims on healthy runs, 11 controlled worker pod kills
   with only expected crash-recovery reclaims, availability 1.0, stable RSS. A
   24-hour soak is future work for deeper confidence on long-tail memory leaks
   and run-reclaim behavior.
2. **SLO candidates are not enforced SLOs.** They are derived from a single
   2-hour soak + one DR cycle. Promotion to production SLOs requires ~30 days
   of production data and a product decision.
3. **Cancel vs. terminal-state race (Low).** A restrictive policy can finalize
   a run as BLOCKED before a cancel request lands; the cancel API is idempotent
   for already-terminal runs.

## Closed items (do NOT re-list)

- **docker.sock mounted in worker** — CLOSED in Phase 28.6. The worker never
  mounts a container-runtime socket; sandbox execution uses the Kubernetes API
  with namespaced RBAC to short-lived Pods in `cap-sandbox`.
- **D1 "per-run lease lacks renewal"** — RESOLVED FALSE by the Phase 28.7
  fact-check. The production K8s path renews the acquisition run-claim lease
  every `lease_ttl/3` on a dedicated session with fencing. See
  `outputs/cap-cert-ga/D1-FACT-CONFLICT-RESOLUTION.md`.
