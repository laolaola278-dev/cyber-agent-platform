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

- **The compose worker holds the container-runtime control socket.**
  `docker-compose.yml` mounts `/var/run/docker.sock` into `acquisition-worker`
  because `SANDBOX_PROVIDER=oci-sandbox` starts sandbox containers through the
  docker CLI. Consequence, stated plainly: on a compose deployment the worker is
  host-root-equivalent, so a compromise of the process that fetches untrusted
  content is a compromise of the host. Sandbox containers themselves never see the
  socket, and the chart path has no such mount. This is why Compose is the
  evaluation/single-node path; a production install that needs container
  sandboxes must use `SANDBOX_PROVIDER=kubernetes-sandbox` on Kubernetes.
- **Redis is configured but unused.** `settings.redis_url` has a default, `docker-compose.yml`
  runs a `redis` service, and `GET /readiness` reports `redis_configured` -- which is
  `bool(settings.redis_url)`, i.e. proof that a string exists, not that anything connects.
  No module in `backend/app` imports a Redis client, and the telemetry coordinator's docstring
  states it has no broker dependency. Consequences an operator should know: a deployment that
  does not run Redis is unaffected; a deployment that does is protecting a stateful service for
  nothing; and `redis_configured: true` must never be read as health. Deliberately **not
  certified with invented tests** -- there is no business behaviour to observe. Removing the
  service and the field is a settings/API change and belongs with the event-plane work that
  would justify it.
- **Closed in this line (F-7): the release publishes all five images the product runs.**
  `release.yml` used to build and push `cap-backend` and `cap-frontend` while the chart
  deployed five CAP images and pointed the other three at `:latest` tags no registry served,
  so a default install died at the first acquisition (K8S-GATE 34 now checks exactly that).
  It now publishes `cap-sandbox-http`, `cap-sandbox-browser` and `cap-egress-proxy` too, each
  tagged with the release version, each with SBOM and provenance attestations and a recorded
  manifest digest, and `release-image-completeness` refuses to let a partial set produce a
  GitHub Release. The chart defaults to versioned coordinates and accepts a `digest` per
  image, so the release-recorded digest can be pinned instead of a tag. **What remains**:
  a compose deployment on a single node can still build the sandbox images locally
  (`backend/docker/build_sandbox_images.sh`); that path does not need the registry, and the
  compose guide says which coordinate to set if you would rather pull than build.
- **Closed in this line (F-20): every Dockerfile base image is pinned by digest.** The
  five external bases (`python:3.13-slim`, `python:3.13.12-slim-bookworm`,
  `node:22-alpine`, `nginx:1.30.4-alpine`, `ghcr.io/astral-sh/uv:0.8.3`) are now written
  as `name:tag@sha256:…`, with the manifest-list digest, the platform set and the
  linux/amd64 child digest recorded in `deployment/third-party-images.json` together with
  the registry verification that produced them
  (`outputs/artifact-closure/registry-base-digests.json`). Before this, a rebuild of a
  release commit could pull different base bytes than the certified build with nothing in
  the lock to say so, and the certification artifact's `base_digest` was null for exactly
  that reason. `backend/tests/test_dockerfile_base_images.py` fails the build if a Dockerfile
  FROMs an external image that is not locked, or names one by tag alone, and refuses a new
  base that nobody recorded.
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
- **The console's default identity is `read-only`, and the chart/compose default to
  it.** After this line's permission fix that is enough to *view* every console page,
  but not to start an acquisition, run a workflow or write the registry: a deployment
  that lets operators act must point the gateway at an identity holding the matching
  `*.execute`/`*.write` permissions (SOC Analyst is the intended role), because the
  trusted proxy -- not CAP -- is the only thing binding a header to a person. See
  `docs/deployment/production-checklist.md` and the audit-attribution limitation above.
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
4. **Compose-path third-party services are still pulled by mutable tag (F-24).**
   `redis:7-alpine`, `prom/prometheus:v2.55.1`, `grafana/grafana:11.3.1`,
   `dpage/pgadmin4:8` and `postgres:16-alpine` come from `docker-compose.yml`,
   not from a Dockerfile, so the base-image lock and its digest gate do not reach
   them -- and the chart does not deploy them either. Consequence: a compose
   deployment can receive different bytes for the same tag on different days, and
   `deployment/third-party-images.json` says nothing about them. Closing this
   means either extending the lock to compose services and rewriting
   `docker-compose.yml` to digest references, or dropping the compose stack in
   favour of the chart; both are larger than the artifact-closure scope and were
   left alone deliberately rather than half-done.
5. **The certification gate could not tell a strict GA round from a development one
   (F-33) — closed post-rc, on 2026-09-21.**
   `release.yml`'s `verify-certification` used to accept a *successful* run of
   `cap-ga-certification.yml` that carried the release job set. That is not "FULL GA
   40/40": every push to `main` runs the workflow with `CAP_GA_STRICT=0`, and in that mode
   a gate with no evidence in the job is `PLANNED` instead of failing, so nothing fails and
   the job is legitimately green. This was checked, not assumed: the Actions API serving
   this repository returns no `inputs` field for a run at all (a `workflow_dispatch` soak
   run started with `soak_seconds=7200` has no `inputs` key in its response), so the gate
   cannot filter on how a round was dispatched.
   The remedy is in place: the gate now reads the round's own decision out of the artifact
   it uploaded -- `cap-28.7-ga-certification.json`, with `mode`, `full_ga_certified`,
   `commit` and `gate_summary` -- and requires `mode: final-strict`,
   `full_ga_certified: true` read exactly as stored (never recomputed from junit or job
   colours), `commit` equal to the selected run's `head_sha`, and zero `failed`, `not_run`,
   `skipped` and `planned` counts with `passed == total`. Kubernetes is held to its own
   artifact's counts and commit. An absent, duplicated, expired, unparseable or ambiguous
   artifact refuses the release; a refused download is reported as `verdict: ERROR`, which
   blocks publication too but says what actually happened. So an authoriser no longer has to
   confirm the dispatch inputs by hand -- the gate refuses when the round's own artifact does
   not say FULL GA CERTIFIED -- and the release notes on this line still quote the artifact,
   never the green tick.
   It has now been seen refusing on live state: run read-only against `0561a7e` on 2026-09-22,
   the gate selected *that head's own* GA round -- complete, green, its recorded `commit`
   genuinely equal to the head -- and refused it on `mode='development'`,
   `full_ga_certified=False`, `planned=5` and `35 of 40` alone, exiting 1 with `RELEASE BLOCKED`
   where the pre-fix gate printed `PASS` on the identical remote state. No tag was created, so
   the refusal at real tag time remains demonstrated by executed code rather than a publication.
6. **The release image graph has never run (F-25).** `release.yml` builds and
   publishes all five CAP images, and its completeness gate refuses a partial
   set, but the workflow triggers only on a `v*` tag and publication is not
   authorised. What a clean runner did prove: the same script builds all five
   (CI `release-image-builds`), and the defect that would have broken the first
   real release -- the evidence block aborting after a successful build -- was
   found by that dry build and fixed. What is still verified only by reading:
   `--push`, registry authentication, the stored SBOM/provenance attachments and
   the index-digest hand-off between the two sandbox jobs.
7. **A tracked file can point its evidence into a gitignored directory (F-37).**
   `deployment/third-party-images.json` names
   `outputs/artifact-closure/registry-base-digests.json` as the proof behind five
   digest pins, and the 1.0.6-rc1 artifact-closure report cited three more paths
   under that same ignored directory. A fresh clone resolves none of them.
   It used to be true that no test looked, which is why CI said nothing about it;
   since 2026-09-21 `test_third_party_image_lock.py` requires every cited path to
   have a tracked twin under `docs/quality/artifacts/` asserting the **same
   digests** -- the dates and layout of the two copies are explicitly not the claim
   -- and refuses any entry that has neither a pointer nor a justification named in
   the test's own table. That guard is a **transitional contract**: it verifies the
   claim a clone must be able to resolve, and it was executed inside a fresh clone
   where the cited generated file does not exist at all. Once the five strings point
   at tracked paths, the guard tightens to requiring the cited path itself to be
   tracked. The report's own captures are tracked beside it
   (`docs/quality/artifacts/cap-1.0.6-rc1-artifact-closure/`); the five pointers
   in the lock were deliberately not rewritten, because `classify_diff.py` puts
   every path under `deployment/` in the runtime-affecting `deployment` category,
   so changing those strings costs a full re-certification of build inputs that did
   not change -- and the only cheap way round that cost is a metadata-only
   classifier exception, which is the manoeuvre the release governance refuses.
   Closing it means either generating the measurement inside the check that consumes
   it, or deciding that evidence lives at a path the lock may name; the repoint is
   scheduled into the round that re-certifies anyway.
8. **CAP images are not reproducible by digest (F-39) -- divergence measured, cause
   not established.** Two clean-runner builds of `cap-sandbox-http` with the same
   `dockerfile_sha256`, the same `context_sha256` and the same pinned base produced
   different config digests -- and different index digests on the buildx-produced pair --
   at `a79d29c` and `b671f53`; the same holds for `cap-egress-proxy`. What that does
   *not* show is why: every such pair spans different commits, and the only same-commit
   pair in the captured evidence is one build's record downloaded twice from two jobs of
   one run. The claim that image-config and layer-history timestamps account for it, or
   that a missing `VERSION`/`REVISION` label is involved, is not demonstrated and has been
   removed: the build evidence records no layer digests, no diff IDs, no manifest
   composition, no attestation descriptors, no buildx or BuildKit version and no runner
   metadata, so the variables that move together with a commit change are precisely the
   ones never captured. `docs/quality/cap-f39-reproducibility-measurement-2026-09-21.md`
   lists the fifteen elements a real comparison needs and which six the evidence has.
   What still holds: `values-release-<version>.yaml` pins the *published* index digest, so
   an operator runs the bytes that were scanned and certified, and the recorded input
   hashes say what went into them. What does not: rebuild at the tag and compare digests --
   that verification fails today. Closing it is expected to mean
   `--timestamp`/`SOURCE_DATE_EPOCH` discipline across all five Dockerfiles plus layer
   metadata and copied-in file times, which changes what every image is and so costs a full
   re-certification -- but the acceptance criterion is two independent clean builds of one
   commit matching on the digests the measurement file defines, not the fix being in
   place. See also the 1.0.6-rc1 artifact closure report, §10.
9. **The chart schema forbids a digest-only pin for two of six coordinates
   (F-41, half closed).** `helm lint` in CI and the `helm install`s in the
   certification rounds do apply `values.schema.json`, and the released
   `values-release-<version>.yaml` is now evaluated against it coordinate by
   coordinate in `test_the_released_values_satisfy_the_chart_schema`. What is left
   is the schema's own asymmetry: `worker.sandbox.image`,
   `worker.sandbox.browserImage`, `egressProxy.image` and `frontend.image` accept
   tag **or** digest through `anyOf`, while `backend.image` and `worker.image`
   require a non-empty `tag` whose pattern rejects the empty string. `cap.imageRef`
   prefers a digest, so the digest-only form an operator would use to pin exact
   bytes is unavailable for precisely the API and the acquisition worker. Unifying
   it means editing `deployment/helm/cap/values.schema.json`, which
   `classify_diff.py` charges as runtime-affecting, so the change is scheduled in
   front of its own re-certification rather than slipped into a docs-only round;
   the test above asserts the difference, and will fail when it is removed.
10. **A skipped release job read as executed evidence (F-42 — implementation and remote validation
    CLOSED by batch 1.1; tag-time path PENDING).** Found by the
    batch-1 remote validation, in the same class as F-33 — a colour where a decision should be — one
    layer down. The comment above `REQUIRED` in `release.yml` claimed "a job skipped by its `if` is
    absent, hence 'missing'", and `find_evidence` checked only that each required job *name* appeared
    in the run's job list. Remote reality is different: GitHub reports a skipped job present with
    `conclusion: "skipped"`, so the push-triggered Linux run `35594554182` at `49de1081`
    (`cap-production-certification: ["skipped"]`) satisfied the check and would have stood as that
    commit's certification evidence.

    **Closed as:** the eligibility test is now presence *and* equality against `"success"` for every
    conclusion recorded under a required name — so `skipped`, `failure`, `cancelled`, `timed_out`,
    `action_required`, `neutral`, `stale`, an unstarted `null`, an empty list, or a value nobody has
    enumerated yet are refused alike, and a matrix leg is not rescued by its passing siblings (the same
    no-cherry-picking rule the authority leg applies to duplicated artifacts). An ineligible run is
    passed over to the next eligible ancestor exactly as a run missing a job already was; the selection
    policy did not move. The evidence file gained `ineligible`, and the failure line names the run,
    the job and what it recorded. Pinned by ten conclusions through the executed gate, one case per
    required job of all four workflows, the mixed-leg and duplicate-name cases, and a direct call of
    the gate's own rule so it cannot decay into a blocklist; two mutation controls over the live
    workflow file (never refuse; blocklist-only) redden 19 and 11 tests respectively, which is what
    says the tests are load-bearing rather than descriptive.

    **What the batch-1.1 CI runs settled, and what they did not.** Remotely, the fix is confirmed
    twice over. The twenty F-42 cases passed in CI at both heads, and the gate's own code, run
    read-only against the pushed head's live state at `0561a7e`, passed over four green runs whose
    release jobs were skipped (including that head's own `+0` round) to reach the real release-layer
    round at `b671f53` (+43, INHERITED) — where the pre-fix gate accepted the `+0` skipped run and
    printed `PASS`. The **live Actions/API read path is now validated in CI**: run `35689729682` at
    `0561a7e` completed **success**, 10/10 jobs, with junit
    `1664 tests / 0 failures / 0 errors / 133 skipped` and both capability checks passing once the
    unit-test step was handed `GH_TOKEN`; the
    same two checks failing an hour earlier at `7da216c` for want of it is the control that says they
    are not vacuous, and no test was skipped or narrowed to get there. What remains open is the
    **tag-time execution** — no tag was created, so the gate's refusal at publication is demonstrated
    by executed code and live-state dry-run, not by a real release. That is the same pending item
    F-33 left behind, now with its cause measured rather than assumed.

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

- **docker.sock mounted in the production (Kubernetes) worker** — CLOSED in Phase
  28.6 and re-verified per release: `deployment/helm/cap/templates/worker.yaml`
  mounts no container-runtime socket, sandbox Pods are created through the
  Kubernetes API with namespaced RBAC into `cap-sandbox`, and K8S-GATE 3 asserts
  the absence on a live cluster. The compose path is the other story and is
  listed above as a standing limitation, not closed.
- **D1 "per-run lease lacks renewal"** — RESOLVED FALSE by the Phase 28.7
  fact-check. The production K8s path renews the acquisition run-claim lease
  every `lease_ttl/3` on a dedicated session with fencing. See
  `outputs/cap-cert-ga/D1-FACT-CONFLICT-RESOLUTION.md`.
