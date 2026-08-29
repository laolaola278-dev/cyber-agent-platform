# Known Issues for 1.0.1-rc1

> Supersedes the `1.0.0` (GA) known-issues set, which stays published with the
> v1.0.0 release assets and is not rewritten here. Items closed by Phase 28.6
> (docker.sock elimination) and the Phase 28.7 D1 fact-check are removed.
>
> This patch adds **capability disclosure** that the GA set omitted: the
> Response Plane provider status, the Zeek input-format boundary, and the
> reserved interfaces. Those are not new defects -- they describe behaviour
> that shipped in v1.0.0 but was only recorded in design documents, never in
> the operator-facing known-issues list.

## Release blockers

None carried from v1.0.0: Phase 28.7 GA Reliability Certification passed 40/40
gates under `CAP_GA_STRICT=1` at certified commit
`b22b7be57f89cd0ef0cf9df8b289ec1f5e74b2b3` (v1.0.0-rc4 — the security
re-certification anchor carrying the CVE-2026-14456 openssl fix). The GA commit
`0240fbe` is a pure release-metadata bump classified `release_metadata_only=true`
by the fail-closed diff classifier, so that runtime certification is inherited.

**1.0.1-rc1 certification status (2026-08-29).** This patch changes
sandbox/egress startup validation, which is a runtime-affecting security
change, so the rc was put through the remote certification suites. At commit
`c6793a8` all four push-triggered workflows are green: General CI
(33246170456), Linux Certification (33246170455), K8s Certification
(33246170461) and GA Certification (33246170487) — the last a strict-mode
run on a real kind cluster with **35 gates passed, 0 failed, 0 skipped**,
including the security re-certification subset and GA-GATE 1 under the
rc-generic version policy. The remaining 5 gates (GA-GATE 24/25/26/34/35 —
reliability soak, capacity, overload) certify in the nightly
`cap-ga-reliability.yml`; until a soak run against this commit is merged,
those report PLANNED and the 40/40 artifact cannot be regenerated. A 1.0.1
GA tag requires that soak, then a pure release-metadata bump that the
fail-closed diff classifier can inherit. v1.0.0 remains the only released
version.

## Operational limitations

- Identity is supplied by a trusted reverse proxy; CAP does not provide an OIDC login implementation. Production gateways must overwrite identity headers.
- User/Role/Permission directories are immutable in v1; there is no user-management write API.
- OpenTelemetry spans are not exported when `OTEL_EXPORTER_ENDPOINT` is empty.
- Metrics and API docs are public application paths; production networks must restrict metrics, while API docs default to disabled.
- The Web Console defaults to `read-only` for local Compose. It is not a production identity solution.
- Frontend bundles still produce size warnings; this is not a correctness defect.

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
