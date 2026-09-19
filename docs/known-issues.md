# Known Issues for 1.0.6-rc1

> Supersedes the `1.0.1-rc1` set. Items closed by later releases are removed
> here and recorded in [`CHANGELOG.md`](../CHANGELOG.md) and
> [`docs/releases/`](releases/). Capability disclosure (Response Plane
> provider status, the Zeek input-format boundary, reserved interfaces) is
> carried forward unchanged -- it describes shipped behaviour, not defects.

## Release blockers

None carried from v1.0.0: Phase 28.7 GA Reliability Certification passed 40/40
gates under `CAP_GA_STRICT=1` at certified commit
`b22b7be57f89cd0ef0cf9df8b289ec1f5e74b2b3` (v1.0.0-rc4).

**Current published release.** `1.0.5`: tag `v1.0.5` -> commit
`32ec298951485633c7019f83a18108a2bcddcb76`, GitHub Release created
2026-09-07T14:59:44Z. Certified at anchor `901013a`: strict GA 40/40 gates PASS
with `full_ga_certified: true`, plus a 7200s reliability soak (run
`34116570119`, 480/480 healthy ticks, 0 HTTP errors, 0 downtime, 48 pod-kill
recoveries). Released versions on this line are v1.0.0 through v1.0.5.

**Under certification, not released.** `1.0.6-rc1` (anchor commit `2030192`,
candidate branch `release/1.0.6-rc1`) carries the post-1.0.5 fixes listed under
`## [Unreleased]` in the changelog plus the certification repairs found while
preparing it. `scripts/release/classify_diff.py` classifies the line as
runtime-affecting, so v1.0.5's certification cannot be inherited: this line earns
its own. Nothing is published for it -- no tag, no GitHub Release, no image push --
until the runtime certification below is green and publication is authorised.
The readiness verdict and the exact candidate SHA are recorded in
`docs/quality/`.

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
- **Console coverage is component-level, not browser-level.** 29 Vitest tests
  drive the operator flows (approval centre, incident assignment, acquisition run
  lifecycle, the platform's three error payload shapes) over a real axios adapter
  in jsdom, and CI gates on them with typecheck + ESLint + production build. No CI
  test drives the *built* console in a real browser against a live backend; that
  verification is performed per release and recorded in the certification report.
- OpenTelemetry spans are not exported when `OTEL_EXPORTER_ENDPOINT` is empty.
- Metrics and API docs are public application paths; production networks must restrict metrics, while API docs default to disabled.
- The Web Console defaults to `read-only` for local Compose. It is not a production identity solution.
- The console entry chunk is ~487 kB (157 kB gzipped) after route-level code
  splitting, under the 550 kB threshold. The antd core shared by most views is why
  it cannot shrink much further; the remaining page code is lazy-loaded.
- **The object store's upstream is archived.** MinIO stopped maintaining the
  open-source server, client and KES: `dl.min.io` returns HTTP 410 Gone for every
  community release asset, and `minio/minio` is gone from Docker Hub (pulled fine
  from GitHub runners on 2026-08-22, `pull access denied` on every certification job
  since 2026-09-13). CAP now pins the last certified release by digest from the
  official MinIO organisation on quay.io (`RELEASE.2025-04-22T22-12-26Z`,
  `sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e`) and
  installs `mc` from a pinned GitHub release asset verified against its published
  checksum. That makes the dependency reproducible, **not patched**: no further CVE
  fixes will arrive for this image, so a deployment must compensate with network
  isolation, its own mirror, and a migration plan. Provenance, the verification gap
  that remains, and the upgrade procedure are in
  `deployment/third-party-images.json`.

- **The ORM models and the migrated schema disagree in naming, and have since
  1.0.x.** Enforcement is intact -- `backend/tests/test_migration_catalogue.py`
  proves every model-declared index and constraint is actually backed by something
  in the database -- but the *names* diverge in three documented ways: revision
  `20260729_0003` creates `ck_agents_status` / `ck_tasks_status` /
  `ck_task_executions_status` and the head schema carries `ck_agents_ck_agents_status`
  and friends (the metadata `NAMING_CONVENTION` doubled them when a later revision
  rebuilt the constraint); `agents.runtime_image` is a nullable column only the
  schema has; and `ix_playbook_executions_idempotency_key` / `ix_tools_tool_type`
  exist as a unique constraint and as `ix_tools_type`. Consequence: `alembic
  check`/autogenerate reports ~60 operations on every run and is therefore **not**
  a CI gate here -- a gate that is red from the day it is added gets deleted, so the
  divergence is pinned in that test's `SCHEMA_ONLY_COLUMNS`, `SCHEMA_ONLY_INDEXES`
  and `MODEL_INDEXES_UNDER_OTHER_NAMES` lists, which fail if the drift grows and
  fail if an entry disappears without being deleted. Fixing it for real needs a
  revision that renames constraints to the conventional names, which is
  runtime-affecting and cannot land in a release candidate.
- **Kubernetes certification is 33 gates, not 32.** Reports and runbooks that say
  `K8S-GATE 1..32` predate the console-routing gate; `generate_report_28_6.py`'s
  `ALL_GATES` is the authority.

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

## Live verification against a real PostgreSQL server

The post-1.0.5 delivery audit ran the shipped application -- not the test
harness -- against a real PostgreSQL 16.2 server: `alembic upgrade head` over
asyncpg (21 revisions, 104 tables, including the five constraint-altering
revisions SQLite cannot express), then uvicorn in production settings driving
asset -> incident -> transition -> audit, RBAC refusal of anonymous, spoofed and
non-directory identities, a read-only role getting 403 rather than 401 on a
write, invalid input returning 4xx rather than 500, a governed response plan
appearing in `GET /approvals` and being approved by a second identity, and the
transitioned incident still `TRIAGED` after a full process restart. 30 checks,
all passing, with a clean uvicorn error log.

Three defects that only that path could surface were fixed (asset search 500 on
PostgreSQL, the dead `CAP_ZAP_API_KEY`, the `.env`-dependent test suite); see
the `[Unreleased]` section of `CHANGELOG.md`.

Two properties of the platform are worth stating where an integrator will meet
them, since neither is a defect but both surprised the audit:

- `CAP_ZAP_API_KEY` must be set for the incident and assessment planes to serve
  at all. It is not optional in a real deployment: `POST /incidents` reaches the
  ZAP dependency through the playbook dependency graph.
- Audit rows for business events carry the acting subsystem
  (`asset-service`, `incident-service`) rather than the human; the human is
  preserved in `requested_by` / `approver` / `Asset.deleted_by` and, for
  request-level outcomes, in `operator`. Every row carries `trace_id`, but the
  HTTP response's trace header is a 32-hex request id while audit `trace_id` is
  stored as a UUID string, so the two do not currently join. Correlating an
  action to a request therefore needs the request id, not the audit `trace_id`.

## Running the suite outside a certification environment

The GA tier-2 ops suite (`test_phase_28_7_ga_tier2_ops.py`) is not a unit test:
GA-GATE 32 exercises the *runbook against real certification evidence*. It reads
`GA_REPORT_DIR` (default `outputs/ga-dr`, which is git-ignored) and runs
`scripts/certification/verify_backup_manifest.py` over it.

On a development machine that still holds evidence from an older certification
run, that digest check refuses the stale backup and the gate fails with:

```
FATAL: object backup manifest digest mismatch -- restore refused (fail-closed)
```

**This is the gate working, not a product defect.** A backup whose manifest does
not match its objects cannot be restored, and the verifier is deliberately
fail-closed. On a fresh clone `outputs/ga-dr` does not exist, the gate skips, and
`make test` / the `ci.yml` backend job are green; the evidence is regenerated by
the K8s certification workflow.

To reproduce a green run locally, either point the variable at a directory that
does not exist, or supply the run's own evidence:

```bash
GA_REPORT_DIR=/nonexistent make test                    # certification tier skips
GA_REPORT_DIR=outputs/ga-dr-<this-run> pytest \
  backend/tests/test_phase_28_7_ga_tier2_ops.py -k gate32   # real evidence
```

Do not "fix" the failure by relaxing `verify_backup_manifest.py` or by skipping
the gate when the directory exists -- that is the check that proves a backup is
restorable.

The suite is also hermetic against an untracked `.env`: `Settings` resolves
`env_file=".env"` against the working directory, so a developer file used to be
able to reconfigure tests -- a stale proxy secret (bare 401s), a real
`CAP_ZAP_API_KEY` (ZAP provisioned inside incident-plane tests), or an
`APP_VERSION` one release behind (`/health` reported `1.0.5` while all 16
carriers said `1.0.6-rc1`). `backend/tests/conftest.py` clears `env_file` before
any app module is imported, so tests see process environment plus declared
defaults, exactly like a container; `test_settings_dotenv_hermeticity.py` holds
that invariant.

## Closed items (do NOT re-list)

- **docker.sock mounted in worker** — CLOSED in Phase 28.6. The worker never
  mounts a container-runtime socket; sandbox execution uses the Kubernetes API
  with namespaced RBAC to short-lived Pods in `cap-sandbox`.
- **D1 "per-run lease lacks renewal"** — RESOLVED FALSE by the Phase 28.7
  fact-check. The production K8s path renews the acquisition run-claim lease
  every `lease_ttl/3` on a dedicated session with fencing. See
  `outputs/cap-cert-ga/D1-FACT-CONFLICT-RESOLUTION.md`.
