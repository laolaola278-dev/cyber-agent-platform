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
4. **Compose-path third-party services were pulled by mutable tag (F-24) — CLOSED
   post-rc, on 2026-09-22, at candidate `257ba18`.**
   `redis:7-alpine`, `prom/prometheus:v2.55.1`, `grafana/grafana:11.3.1`,
   `dpage/pgadmin4:8` and `postgres:16-alpine` came from `docker-compose.yml`, not from a
   Dockerfile, so the base-image lock and its digest gate did not reach them -- and the chart
   does not deploy them either. The consequence was that a compose deployment could receive
   different bytes for the same tag on different days while
   `deployment/third-party-images.json` said nothing about them.
   It is closed the way the finding said it should be: the lock was extended to the compose
   services and `docker-compose.yml` rewritten to digest references. All six external images
   now read `repository@sha256:<index digest>`, each with a comment naming the tag the digest
   stands for (MinIO's pre-existing immutable ref was re-read and left alone), and the lock
   gained `redis`, `prometheus`, `grafana` and `pgadmin4` beside the existing entries. Every one
   of those digests is **generated**, not transcribed: `scripts/release/third_party_registry_evidence.py`
   writes `docs/quality/artifacts/registry-resolution/third-party-registries.json`, and
   `policy.compose_images` is the upgrade procedure -- generator refresh, review the digest
   diff, run the relevant certification, update compose and the lock together.
   The postgres mutable-tag exception was reversed in the same change:
   `policy.postgres.form` went from `tag` to `repository@digest`, its known cost (a pinned
   major version now needs that four-step procedure rather than a tag bump) stated in the lock,
   and the guard that used to permit the tag replaced by a D.4 contract case that fails if the
   exception reappears. Contracts 1-5 of that table are each a live assertion plus a control
   that mutates a copy of its own input, so the green is measuring the rule and not the fixture.
   The release was not certified by this document: the candidate carries CI green at
   `head_sha = 257ba18` and the Linux, K8s, reliability and strict-GA rounds at that same SHA,
   and `docs/quality/cap-post-rc-batch-2-implementation-2026-09-22.md` §C records which
   artifact said what.
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
7. **A tracked file could point its evidence into a gitignored directory (F-37) — CLOSED
   post-rc, on 2026-09-22, at candidate `257ba18`.**
   `deployment/third-party-images.json` used to name
   `outputs/artifact-closure/registry-base-digests.json` as the proof behind five digest pins,
   and the 1.0.6-rc1 artifact-closure report cited three more paths under that same ignored
   directory. A fresh clone resolves none of them.
   It used to be true that no test looked, which is why CI said nothing about it; since
   2026-09-21 `test_third_party_image_lock.py` required every cited path to have a tracked twin
   under `docs/quality/artifacts/` asserting the **same digests** -- the dates and layout of the
   two copies were explicitly not the claim -- and refused any entry with neither a pointer nor
   a justification named in the test's own table. That was the **transitional** contract, and it
   is now deleted rather than left in place beside the real one. What replaces it:
   `test_cited_evidence_is_tracked_and_asserts_exactly_the_locked_digests` requires the cited
   file itself to exist, to appear in `git ls-files`, and to state exactly that entry's index
   digest and `linux/amd64` child digests for its own registry/repository/tag coordinate -- so a
   pointer can no longer be satisfied by a file that merely shares a name. Two controls keep the
   contract from decaying: re-planting a same-named twin under `docs/quality/artifacts/` must
   leave an invalid pointer invalid, and an entry with no evidence pointer at all is refused
   instead of passing silently.
   Why it waited for this round rather than riding in a docs-only one: `classify_diff.py` puts
   every path under `deployment/` in the runtime-affecting `deployment` category, so rewriting
   those strings costs a full re-certification of build inputs that did not change -- and the
   only cheap way round that cost is a metadata-only classifier exception, which is the
   manoeuvre the release governance refuses. The repoint therefore went in beside F-24's digest
   pinning, in the commit that re-certified anyway. One consequence for anyone reading the
   finding list by finding rather than by commit: F-37's contract and F-24's pinning share
   commit `d68ff50`, so the per-item verdicts in
   `docs/quality/cap-post-rc-batch-2-implementation-2026-09-22.md` §A can only be reported per
   commit, not per finding.
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
9. **The chart schema forbade a digest-only pin for two of six coordinates
   (F-41) — CLOSED post-rc, on 2026-09-22, at candidate `257ba18`.**
   `helm lint` in CI and the `helm install`s in the certification rounds did apply
   `values.schema.json`, and the released `values-release-<version>.yaml` was already evaluated
   against it coordinate by coordinate in `test_the_released_values_satisfy_the_chart_schema`.
   What was left was the schema's own asymmetry: `worker.sandbox.image`,
   `worker.sandbox.browserImage`, `egressProxy.image` and `frontend.image` accepted tag **or**
   digest through `anyOf`, while `backend.image` and `worker.image` required a non-empty `tag`
   whose pattern rejected the empty string -- so the digest-only form an operator would use to
   pin exact bytes was unavailable for precisely the API and the acquisition worker.
   All six nodes now carry one contract: `required: ["repository"]`, then `anyOf` over a
   well-formed `tag` or a `digest` matching `^sha256:[0-9a-f]{64}$`, with the `digest: ""`
   defaults removed from `values.yaml` so an unset field cannot masquerade as an empty-but-valid
   coordinate. `cap.imageRef` prefers the digest when both are present, which
   `test_the_digest_is_the_coordinate_the_chart_renders` reads out of `_helpers.tpl` rather than
   inferring from the schema. `anyOf` rather than `oneOf` is a deliberate choice, not an
   oversight: tag-plus-digest is a legal, meaningful input (pin the bytes, keep the human-readable
   tag beside them), and `oneOf` would refuse the safest form an operator can write.
   Enforcement moved out of Python-only: `ci.yml`'s packaging job runs real `helm lint` and
   `helm template` for tag-only, digest-only and tag+digest across all six paths, asserts the
   rendered image strings, and refuses the no-coordinate, empty-tag, empty-digest and
   malformed-digest shapes. Note what that means for reproducibility of this claim: helm is not
   installed on every contributor's box, so those four refusals are demonstrated in CI, and the
   local Python tests cover the schema and the renderer but not the binary.
   This is a `deployment/` change and therefore runtime-affecting: it is one of the reasons the
   round that carried it re-certified build inputs instead of shipping as docs.
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

11. **The pinned build producer is never demonstrated to be the producer of a built image
    (F-44) — IMPLEMENTATION CLOSED at candidate `dea8c6f`.**
    Closed as *implementation and enforcement*, which is what the finding asked for when it was
    filed; the distinction and the evidence for it are in the last three paragraphs of this entry,
    and the observation this window cannot make is **F-51**.
    Measured, not inferred, from CI's own release-image records at candidate `257ba18`
    (run `35761257654`, the five `ci-release-image-cap-*` artifacts plus one buildx-path record
    inside them):

    | field | value |
    | --- | --- |
    | configured buildx (the lock's declared pin) | `v0.37.1` |
    | observed buildx (read back on the runner) | `github.com/docker/buildx v0.37.0 ac30b249211430b85fb8f37b6e7154b5c47ba0b6` on **6 of 6** records |
    | `comparison.buildx_version.matches` | `false` on 6 of 6 |
    | build paths those six records cover | five `docker build` cells (`--local-docker`) **and** the `docker buildx build` prerequisite (`cap-sandbox-http.prerequisite.json`) |
    | what the workflows ask for | `ci.yml` (lines 337-341, 395-399) and `release.yml` (680-684, 721-725) both pass `buildx-version: v0.37.1` and `driver-opts: image=moby/buildkit:v0.33.0@sha256:6c2fa84a…` to the same pinned `setup-buildx-action@8d2750c6…` |
    | builder identity behind the bytes | never named: no `--builder` appears in either workflow or in `scripts/release/build_release_image.sh` |
    | `comparison.buildkit_image.matches` | `null` on all six -- an unreadable observation, deliberately not restated as a pin; the buildx-path record says so in the open way: `incomplete: ["builder"]` |
    | engine behind the `docker build` cells | Docker Engine `28.0.4`, whose **embedded** BuildKit compiled those layers |
    | configured BuildKit `moby/buildkit:v0.33.0@sha256:6c2fa84a…` | **not demonstrated as the producer** of any image recorded here |

    Two readings this rules out. It is **not** merely an artefact of the dry-build path using
    `docker build`: the one CI build that went through `docker buildx build` observed the same
    `v0.37.0` and could not read its builder either. And it is **not** a claim about published
    images: the release path (`--push`, `docker buildx build`, attestation attached) has never
    executed -- that is F-25's remaining scope -- so what the pin would do to the bytes that
    operators actually pull is **unobserved**, not proven false. What is observed is that on
    every path that has run, the runner's answer to `docker buildx version` is not the version
    the workflow asked the installer for, and nothing in the evidence says which builder compiled
    the layers.
    What B2 closed, precisely: producer *configuration* pinning (actions by full SHA, an
    explicit `buildx-version`, a BuildKit image digest, a frontend digest in the two Dockerfiles
    that already carried a syntax directive), `configured` versus `observed` recorded as separate
    objects by `scripts/release/record_build_producer.py`, and **visibility** of the mismatch --
    the comparison is now a field rather than something a reader must eye-diff across artifacts.
    What B2 did **not** close, and no document may claim: that the actual build producer is
    pinned. A mechanism detail matters for whoever fixes this: `configured` is read from the
    lock (`configured_values(lock, …)` on the `buildx` / `buildkit-buildkit` entries), so the
    comparison checks the declared pin against the CLI's answer and cannot see whether the
    workflow's own `buildx-version` request was honoured.
    Batch 3A's Observation Step 1 landed on 2026-09-23, and it changes the **instrument**,
    not this finding. `record_build_producer.py --mode observe` now reads three authorities
    apart -- `configured.lock`, `configured.workflow` (builder name, driver and BuildKit
    reference taken out of the workflow's own `env:` / `with:` blocks rather than from an
    argument the recorder was given) and `observed.*` (executing buildx path/version/commit,
    every `docker-buildx` plugin on disk, the named builder's nodes, its container's config
    image, image ID and `RepoDigests`, the engine, the runner) -- and answers three
    comparisons (`lock_vs_workflow`, `workflow_vs_observed`, `lock_vs_observed`) instead of
    one boolean. The digest question is layered rather than string-compared: the lock pins a
    manifest *list*, an image's `RepoDigests` name the *child manifest* fetched for a platform
    and `docker inspect` reports the *config* digest, so the recorder asks the pull digest of
    the image object that carries it (a container has no `RepoDigests`, and asking one is a
    question that can never be answered) and reads the registry down to the pinned index's
    child for the platform the running image itself reports, to that child's `config.digest`.
    Two named relations follow -- `digest_relation` and `config_digest_relation` -- with the
    manifest layer left as the only required digest field *at Batch 3A*, so the easier layer
    cannot satisfy the stronger claim; an equality test across those strings without the layering
    would call the conforming case a mismatch, and a tag match would call an unknown one a pass.
    (From Batch 3 A2.1 both layers are required, each scored against its own pinned-side value,
    which is stricter rather than substitutable.) What CI
    measured at `7d3d6a5` (run `35834970797`): both digest layers `CONFORMING` -- the
    builder's running image is the pinned BuildKit -- while `buildx_version` is still `v0.37.0`
    against a `v0.37.1` declaration, so the open half of F-44 is now the CLI, not the daemon.
    `.github/workflows/ci.yml`'s `producer-observation` job runs it
    against a named, non-publishing `docker-container` builder and a scratch untagged
    `cap-backend` build, and uploads the record. Two things do **not** change: the release
    build path still names no builder (`release.yml` and `build_release_image.sh` are
    untouched, by approval), and no gate reads any of these fields -- a measured mismatch is
    evidence in an artifact, not a refusal, so **F-44 stays OPEN** until the project decides
    whether a mismatch blocks publication and the release path is switched onto an observed
    producer.
    Batch 3 A2.1 (2026-09-23) took the CLI half, in the observation job only. The repository now
    pins one buildx executable -- `scripts/release/controlled_buildx.json`, by version `v0.37.1`,
    the commit that version's annotated tag dereferences to, and the sha256 of the released
    linux-amd64 asset -- the job downloads it to a path declared in its own `env:` block,
    verifies the bytes before executing them, creates the named builder with that absolute path,
    builds with it, and asks the builder and the registry through it too. `docker buildx`'s plugin
    answer is still read and still recorded, explicitly not as the producer. All **five** shipped
    images are now observed, `cap-sandbox-browser` included, its base bound to the HTTP sandbox
    image of the same round by that round's own manifest digest through a local OCI layout -- no
    registry, no host docker store, and no docker-driver fallback. Measured at `b98eb3b`
    (run `35868200920`): five records, `lock_vs_workflow`, `controlled_pin_vs_lock`,
    `workflow_vs_observed` and `lock_vs_observed` all `CONFORMING`, `producer_alignment`
    `CONFORMING`, both digest layers scored, every build exit 0 -- on a runner whose
    `docker buildx version` answered **`v0.37.0`** against the same `v0.37.1` pin. Which buildx
    built an image has stopped being a question about the runner.
    Batch 3 A2.2 (2026-09-24) closed the three things that kept this entry open, and the closure is
    measured at a candidate rather than argued:
    * *"the release image path still builds through `docker buildx` with no `--builder`"* --
      `scripts/release/build_release_image.sh` now runs
      `<absolute controlled buildx path> build --builder <explicit name>`, for all five images, in
      `release.yml`'s two image jobs and in `ci.yml`'s rehearsal of them alike, and the pinned
      executable is installed by one mechanism (`install_controlled_buildx.py`) that refuses a plugin
      path before it downloads anything and refuses to create a builder until the bytes it installed
      have answered with the pinned version *and* commit.
    * *"a producer mismatch still blocks nothing"* -- `release-image-completeness` reads the producer
      record for all five images and blocks publication on `MISMATCH`, `UNKNOWN`, `ERROR`, `MISSING`,
      `AMBIGUOUS`, a record from another run, a record whose revision is not the tagged commit, and a
      record built by a job that is not allowed to build a release image. See **F-47**.
    * *"no published image has been produced by the pinned executable"* -- measured at candidate
      `dea8c6f` (CI run `35958562520`): nine ordered install steps with `integrity=equal` and read-back
      `v0.37.1 0b265a9f62db…`; `producer-set.json` verdict `CONFORMING` with `problems=[]` and every
      image `build_exit=0`; and six release-rehearsal image records each with four `CONFORMING`
      comparisons, `producer_alignment CONFORMING`, empty `contract_gaps`, and identity bound to that
      run and that sha -- on runners whose own `docker buildx version` answered `v0.37.1` in three of
      them and `v0.37.0` in three. Same workflow run, same commit, different machines, different
      plugins: the runner's answer is still recorded as `cli_plugin_buildx_version`, and it now scores
      nothing.
    The decision this entry was waiting on -- whether the published-image build path must *be* the
    pinned producer, or should treat the runner's engine as intended and stop asserting the container
    -- was taken as **Option A** of the two priced in
    `docs/quality/cap-post-rc-batch-3-design-options-2026-09-23.md` §C-§F, and it is now enforced in
    code rather than in prose: `release.yml` declares the builder name, the driver, the BuildKit
    reference and the buildx path in each image job's own `env:` block, and the gate compares what the
    build recorded against what those files say.
    **How the status was decided, and against which text.** F-44's title is about "a built image",
    and the entry as filed at `44fb73d` said so explicitly: "it is **not** a claim about published
    images: the release path (`--push`, `docker buildx build`, attestation attached) has never
    executed -- **that is F-25's remaining scope** -- so what the pin would do to the bytes that
    operators actually pull is **unobserved**, not proven false." What it did require is in the same
    entry: that "the actual build producer is pinned", and a decision about whether a mismatch blocks
    publication -- "which is why it is filed here instead of being enforced by prose." Both are now
    delivered and measured: the producer is an executable the repository downloads, hashes and names
    (§C, §D of the A2.2 report, executed on a runner at §J), and the decision is enforced by
    `release-image-completeness` (§G, §P). `docs/quality/cap-post-rc-batch-3-design-options-2026-09-23.md`
    §D states Option A's contract as six build-time evidence requirements plus "mismatch ⇒ failure",
    with no item asking for a shipped release.
    The one bullet above that is *not* F-44's own criterion is "no published image has been produced
    by the pinned executable". That sentence entered this entry later, at `d255fdc`, and it is
    broader than what the finding ever claimed. It is therefore neither folded into the closure nor
    quietly dropped: the live, tag-triggered observation it asks for is **F-51**, and the durability
    gap that stops a reader ten months from now from answering it from the published assets is
    **F-52**. F-44 closing says the release path is the pinned producer and a disagreement blocks the
    release; it does not say a release has happened, and none has.
    Measurement and controls: `docs/quality/cap-post-rc-batch-3-a22-producer-authority-2026-09-24.md`
    §C, §D, §G, §H and §J; the refusal itself is demonstrated in §P against the candidate's own CI
    records.
    Note on provenance of this entry: the values above are read from the candidate's CI
    artifacts; the entry itself is a docs-only reconciliation that the classifier inherits, so
    it is not new certification evidence for any build.

12. **A cancelled acquisition run could lose its terminal `CANCELLED` write (F-45) — CLOSED at
    candidate `257ba18`.** Found by CI, not designed in: run `35749438118` at `f5a0cdd` went red
    with 1753 tests and one failure,
    `test_phase_28_2_cancellation.py::test_cancelled_runs_have_zero_evidence_writes` raising
    `sqlite3.OperationalError: database is locked` on the worker's own last write
    (`UPDATE acquisition_runs SET status='CANCELLED' … AND worker_id = ?`). A pre-existing defect,
    not a regression from the digest work -- the pins only shifted suite timing enough to expose
    it. It reproduced at roughly 1 in 53 executions and worst under artificial CPU pressure, and
    the repo already documented the class ("a zombie transaction holds the SQLite write lock until
    garbage collection": `app/worker/runtime.py:84-91`,
    `docs/quality/flake-triage-34012500372-pregate-d.md`). If the write is lost the run never
    reaches a terminal state and the reconciler has to clean up a zombie.
    The fix is three properties, each with a control that shows the assertion measures the
    property rather than the fixture: the `WorkerCancelledError` branch now rolls back its own
    session before finalising, like the four sibling branches that always did
    (`test_the_cancelled_execution_branch_releases_its_own_writer`); a transient lock on the
    terminal write is retried under a bounded budget instead of losing the write
    (`test_cancel_finalize_retries_a_transient_lock_on_the_terminal_write`, controlled by
    `test_a_non_transient_write_failure_still_propagates`, where a `ProgrammingError` still raises
    and the run is *not* reported cancelled); and the claim's lease is released in the same
    transaction as that write, so the two cannot diverge
    (`test_cancel_finalize_releases_the_run_lease`, controlled by
    `test_a_lease_release_conflict_never_costs_the_terminal_write`).
    Two wrong attempts are on record because they are what the pre-existing tests caught: a
    rollback placed inside `_finalize_cancelled_if_safe` broke
    `test_phase_28_1_worker_path.py::test_cancel_tolerates_terminate_failure` (the API-side cancel
    path uses the same helper with legitimately uncommitted work), and giving the lease release its
    own session broke it again, because on a single-connection bind closing that session rolls back
    the caller's work. Fixing the lease call also surfaced F-45b: `WorkerLeaseRepository` has no
    `update` method, and an `except Exception: pass` around it had been turning that
    `AttributeError` into every cancelled run keeping an ACTIVE lease until the expiry sweep
    happened to find it.
    This is a `production_runtime` change, and it is the reason the candidate moved a fourth
    time: any code fix after a freeze means a new candidate SHA and a fresh round of rounds, not
    an amendment.

13. **Linux certification pins the socket disclosure instead of requiring isolation (F-46) —
    OPEN, policy review.** Read from the candidate's own Linux certification artifact:
    `worker_control_plane_isolation = "PARTIAL"` and `unrestricted_docker_socket_mounted = true`,
    alongside `sandbox_workload_isolation = "PASS"`,
    `production_chart_worker_mounts_runtime_socket = false` and
    `compose_worker_mounts_runtime_socket = true`.
    What produces them is `scripts/certification/generate_report.py:docker_socket_control_plane()`,
    which derives both deployment paths from the repository at assertion time:
    `PASS` only when neither path mounts a runtime socket, `NOT_CERTIFIED` when the **chart**
    does, `PARTIAL` when only **compose** does, and the legacy boolean is the `or` of the two so
    an older consumer cannot read `PARTIAL` as full isolation.
    The precise state of the policy matters here, because "not gated" would be wrong.
    `cap-linux-certification.yml` does assert on these fields -- it requires
    `worker_control_plane_isolation != "PASS"`, requires
    `production_chart_worker_mounts_runtime_socket is False` (labelled `RELEASE BLOCKER`),
    requires `compose_worker_mounts_runtime_socket is True`, and requires the artifact's recorded
    values to equal what the repo yields at assertion time. So the fields are **not** release
    blockers in the security sense -- nothing fails while the compose worker is
    host-root-equivalent -- but they **are** pinned disclosures: a round cannot quietly claim
    isolation, and it cannot quietly lose the compose mount either, since the gate asserts the
    mount is present and tells an author to change the docs and the gate together. That
    by-design asymmetry is the finding.
    The open questions, none of which Batch 2 answered or was asked to: whether a
    host-root-equivalent worker on the path the README quickstart reaches first should remain an
    accepted shipped state or become a release-blocking gate; what the intended secure value is
    per path (`PASS`/`false` for both, or `PARTIAL` as a documented exception with an owner); and
    what enforcing it would cost -- the compose worker drives sandboxes through the docker CLI
    because `SANDBOX_PROVIDER=oci-sandbox`, so removal means a rootless or socket-brokered
    sandbox runtime on that path, and the `compose_worker_mounts_runtime_socket is True`
    assertion, `.env.example`, `docker-compose.yml`'s warning comment,
    `test_sandbox_socket_boundary.py` and this entry all move together.
    Explicitly not claimed: this does **not** retroactively invalidate Batch 2, whose rounds ran
    against exactly these values and passed the policy as written; and it is a distinct decision
    from F-44's producer question -- different file, different gate, different migration -- so
    the two must not be merged into one "certification is soft" complaint.
    Batch 3 §I reviews this as policy and changes no gate.

14. **Release evidence carries a producer block no gate reads (F-47) — CLOSED at candidate
    `dea8c6f`.**
    Found while wiring Batch 3 A2.1, by looking for the consumer of the record the release
    writes rather than by trusting the sentence above it. `scripts/release/build_release_image.sh`
    states, at the point it writes the field:

    > Absent producer is a finding, not a blank: the release gate below refuses a pushed
    > image whose producer was never read.

    There is no such refusal. `grep -c producer .github/workflows/release.yml` answers **0**, and
    the job that decides whether an image set may be published -- `release-image-completeness` --
    requires exactly these fields per image:

    ```
    REQUIRED_FIELDS = ("tag", "index_digest", "platform_digest_linux_amd64",
                       "dockerfile_sha256", "context_sha256", "base_refs", "source_revision")
    ```

    plus `pushed`, the two attestations and a Trivy record of the published ref. `producer` is in
    none of them, and `verify-certification` reads certification runs, not image records. So a
    release in which every image's producer was never recorded -- the recorder fails, the
    `WARNING` line scrolls past, `"producer": {"recorded": false}` lands in the JSON -- publishes
    unchanged. The failure mode is exactly the one F-33 was filed over for verdicts: an
    *evidence-shaped* field that nothing is obliged to read.

    What makes this worth its own entry rather than a fix-up is that the consequence is already
    on the record. F-44's mismatch (`v0.37.0` executing under a `v0.37.1` declaration, 6/6
    records at candidate `257ba18`) was *visible* precisely because the block was written, and
    was *harmless* for the same reason: nothing consumed it. Recording a disagreement and
    refusing on a disagreement are two different features, and only the first was built.

    Batch 3 A2.1 does **not** close this, and says so: it makes the observation deterministic
    (a pinned, hashed, explicitly-invoked buildx; five images; a set verdict computed by
    `record_build_producer.py --combine`) and it changes no publication path. `--combine` scores
    records inside a **non-publishing CI job**, which is an observation instrument, not a gate --
    the distinction A2.1's own acceptance rule depends on.

    What closed it, item by item against what this entry asked for:
    * *the blocking contract has to be in the completeness gate, not beside it* -- it is in
      `release-image-completeness`'s own inline gate, and the list of producer fields it requires is
      read from `scripts/release/producer_contract.json` at run time, so the requirement and the
      refusal cannot drift apart without a test noticing. The gate refuses the contract file being
      missing, unparseable, or in disagreement with the gate about the image set.
    * *refuse on `MISMATCH`, `UNKNOWN`, `ERROR` or absent* -- all four, plus `AMBIGUOUS` (two
      disagreeing records for one image) and `MISSING` (no record at all), six words that all block
      and none of which is collapsed into a boolean, ordered by how much each explains and reported
      per image in `producer_verdicts` and `producer_summary`.
    * *prove the five records came from the same run and the same revision as the images they
      describe* -- the record's `source_revision` against the tagged commit, its `run_id` against
      this run, and its `job` against the two jobs permitted to build a release image. That third
      check is the one the approval's wording did not require and the code needs: reading the commit
      alone would accept a record from another workflow run of the same commit, and reading the run
      alone would accept *this* run's CI rehearsal. A rehearsal record is refused by name.
    * *four of five is not a pass* -- `EXPECTED` is the five chart images; a missing record is
      `MISSING`, and the verdict is computed from the failure list, not from a count that tolerates a
      gap.
    * *the price is certification, not code* -- paid. `classify_diff.py` still read every commit in
      this batch `INHERITED` with `runtime_affecting=false` (that blind spot is now **F-49**), so the
      candidate was recertified on policy: CI, Linux certification, K8s, a 7200 s reliability soak and
      a final-strict GA round at `dea8c6f`.
    One limit stays true and is stated rather than glossed: the refusal has been *executed* -- the
    gate's own code lifted verbatim from `release.yml`, run against the candidate's five real CI
    producer records, refusing when one `producer_alignment` is flipped and when a published image is
    layered on a same-round layout -- but never during a live publication, because that needs a tag
    and `v1.0.6-rc1` is sealed. That observation is **F-51**'s, not something this closure absorbs.
    Evidence:
    `docs/quality/cap-post-rc-batch-3-a22-producer-authority-2026-09-24.md` §G, §H, §I and §P.

15. **A producer sidecar beside the release evidence made the gate invent five images (F-48) —
    CLOSED at candidate `dea8c6f`.** Found while wiring A2.2 Stage 4, by looking for the consumer of
    a file rather than trusting the line that writes it. Batch 2's `build_release_image.sh` wrote its
    producer record to `${OUT}.producer.json`, i.e. *inside* the directory
    `release-image-completeness` globs as `release-image-evidence/*.json`, and the recorder was
    called there without `--image`, so the file carries `"image": null` and the gate keys it by
    `path.stem`. Running the pre-A2.2 gate body -- lifted verbatim from
    `git show 9983705:.github/workflows/release.yml` and executed, not paraphrased -- over the
    records a release would have uploaded answers:

    ```
    gate exit code: 1 | verdict: FAIL
      * evidence for images the release does not declare: ['cap-backend.json.producer',
        'cap-egress-proxy.json.producer', 'cap-frontend.json.producer',
        'cap-sandbox-browser.json.producer', 'cap-sandbox-http.json.producer']
    ```

    Five phantom images, and a refusal whose reason had nothing to do with the release's actual
    state. It never fired for one reason only: no release has run since Batch 2 was written, so the
    path exists in git history and not in an incident. This is the same class as F-37 (a tracked file
    pointing its evidence somewhere nobody reads it) arriving from the other direction -- evidence in
    the right place is not enough if a glob cannot tell it apart from its neighbour.
    Two halves, both kept. A2.2 moved the sidecars under `producer/`
    (`PRODUCER_DIR="$OUT_PARENT/producer"`, pinned by
    `test_the_producer_sidecars_stay_out_of_the_directory_the_gate_globs`), and the reader now
    *recognises* a standalone producer record wherever it lands and refuses it as itself. Running the
    current gate over the identical arrangement gives exit 1 as well, but for the true reason and
    with the real five images keyed correctly:

    ```
    * AMBIGUOUS: cap-backend.json.producer.json is a producer record sitting in the image-record
      directory                                                  (x5)
    * MISSING: cap-backend: the build says its producer was never recorded (recorder exit None)  (x5)
    images the payload keyed: ['backend', 'egress_proxy', 'frontend', 'sandbox_browser',
                               'sandbox_http']
    phantom-image refusals: 0 | refusals that name the producer record: 5
    ```

    Both measurements are reproducible with `_tmp/measure_f48.py 9983705` and
    `_tmp/measure_f48_after.py HEAD`; the probes are session scratch, and the values above are their
    output at the candidate.

16. **The diff classifier cannot see a producer-affecting change (F-49) — OPEN, by decision.**
    `scripts/release/classify_diff.py` answers one question -- did production *runtime* code change
    -- and it keys that answer to paths. Everything under `scripts/release/` is
    `certification_generator` with `runtime_affecting=false`. So the change that decided which
    executable builds every published CAP image, in `build_release_image.sh` and in
    `release.yml`'s gate body, classifies as:

    ```
    inheritance=INHERITED  runtime_affecting=false  release_metadata_only=true  exit=0
    categories: {certification_generator: 6, ci_workflow: 2, test_harness: 8}
    ```

    which is true about the application and silent about the release pipeline. The consequence is not
    hypothetical: read on its own that line authorises inheriting certification for a batch whose
    entire purpose is to change how certification evidence is produced. The approval anticipated it
    ("INHERITED is not permission to inherit certification"; "A2.2 is producer-affecting by policy")
    and the batch therefore recertified in full on policy rather than on the classifier's answer.
    The classifier is **not** modified here, and that is deliberate rather than an oversight: it is
    the arbiter in `release.yml`'s inheritance path, its categories are cited by other rounds'
    evidence, and widening them to cover "how released bytes are made" is a decision about what
    certification means -- with its own recertification cost -- which this batch was not given.
    What closing it would take: a category for the release-build path (or a `producer_affecting`
    flag beside `runtime_affecting`), the rules that map `scripts/release/build_release_image.sh`,
    `install_controlled_buildx.py`, `record_build_producer.py`, `producer_contract.json`,
    `controlled_buildx.json` and both workflows to it, and the inheritance tests that currently read
    `runtime_affecting` as the only blocking dimension. Until then this entry is why an
    `INHERITED` row in a report must be quoted with the question it actually answers.

17. **A2.2 left prose behind that A2.2 made false (F-50) — CLOSED in the post-A2.2 closure
    round.** Auditing every tracked file for statements this batch invalidated turned up ten,
    in four files. Each was written when it was true; the commits that switched the release path
    made them stale, and the reconciliation pass in those commits did not reach them. All ten are
    corrected here, and "these are only words" was not taken on trust: the publication gate is
    executed over the producer record from before and after the edits and must answer identically,
    including on an arm that passes (method named at the end of this entry).

    * `scripts/release/producer_contract.json` -- `status.a2_2_obligations` ("**OPEN** -- recorded
      here so the reader of a release record can hold it against a stated target…"),
      `a2_2_obligations.status` ("**NOT YET EVIDENCED** … a release record satisfies it only when the
      gate reads it and a test proves the reader refuses without it"), and
      `checks_not_run_here.F-47` ("No gate reads any producer field **until A2.2 Stage 5 lands** and
      a test proves the reader refuses"). All three conditions are now met: the gate reads the record
      and `test_release_producer_gate.py` proves it refuses. The file that defines the producer
      contract therefore understates the repository it describes.
    * `scripts/release/record_build_producer.py` -- `buildx_prefix()`'s docstring ("With no
      controlled path the prefix stays `docker buildx`, which is what the release path **still
      uses** -- A2.1 observes, it does not switch anything"); the controlled-executable branch's
      comment ("The release build path passes no pin, because it **still builds through `docker
      buildx`**"); the `reason` string that branch returns *into the record* ("…the release path
      **until A2.2 switches it**, the observation job as a defect after A2.1"); and the comparison
      list's comment ("In the release path, which has none **until A2.2**"). The release path now
      passes a controlled path, a pin and a named builder; the branch that still yields
      `docker buildx` / no pin is a `--local-docker` developer build, and the record is the place a
      reader will meet the wrong sentence.
    * `.github/workflows/ci.yml` -- the `producer-observation` job's comment: "The release path
      (`release.yml` -> `build_release_image.sh`) is **untouched until A2.2 is approved**, and
      `test_the_release_build_path_is_still_not_switched` is the fence that says so." Both halves
      are wrong now, and the second is worse than stale tense: that test no longer exists anywhere in
      the repository, so the comment points a reader at a file they cannot find. Its successor is
      `test_the_release_build_path_now_runs_the_controlled_producer`, in
      `backend/tests/test_producer_observation_contract.py`, which asserts the opposite of the fence
      -- that the publishing command line names the installed executable and an explicit builder.

    How it was closed. Four surfaces, in one commit above the certified candidate:
    * `scripts/release/producer_contract.json` -- `purpose`, `status.a2_2_obligations` and
      `a2_2_obligations.status` now say the obligations are evidenced **at a named commit**, and
      `checks_not_run_here` loses its stale `F-47` key in favour of `live_publication_F-51`, which
      is what genuinely remains unrun.
    * `scripts/release/record_build_producer.py` -- the `buildx_prefix()` docstring, the no-pin
      branch's comment, the record-side `reason` string, and the comparison-list comment. Each now
      says what reaches that branch: a `--local-docker` developer build, not a release build.
    * `.github/workflows/ci.yml` -- the comment names the fence that exists,
      `test_the_release_build_path_now_runs_the_controlled_producer`.
    * `backend/tests/test_producer_contract_freeze.py` -- the test that pinned the old strings is
      retargeted rather than deleted: it still refuses a status that is free text, requiring each
      string to begin `EVIDENCED AT `, to name a **full 40-hex sha**, for both strings to name the
      **same** one, and for the detailed one to say which artifact enforces the obligation and which
      item is still owed. A weaker status sentence now fails here.
    Then, because "these are only words" is a claim about readers nobody checked: the lifted
    `ARTIFACT_GATE_PY` is executed over the producer record captured before the edits and the one
    captured after (`_tmp/f50_payload_diff.py`, `_tmp/f50_gate_equivalence.py`), across three arms --
    the release-shaped record as the rehearsal job would file it, the no-pin record, and a
    normalised record that the gate must **PASS**. All three answers are byte-identical across the
    edit and the passing arm passes on both sides, which is why that arm is required to pass: a
    comparison that only ever refuses would prove nothing.
    One more thing the audit surfaced, unrelated to tense: the A2.2 report carries a false negative
    -- that the published `values-release-1.0.6-rc1.yaml` asset names no image digests, which was an
    empty read, not a measurement. It names all five. The correction and how the read went wrong are
    recorded in the report's errata.

18. **No live publication has exercised the producer authority (F-51) — PENDING BY AUTHORISATION,
    not a defect.** F-44 never claimed this (see its disposition), and F-47's closure says the
    reader is live in the gate and tested rather than tested in production. What has not happened is
    one `release.yml` run from start to finish since the publishing path was switched: the workflow
    triggers only on a `v*` tag, and `v1.0.6-rc1` is sealed with its lifecycle closed, so no tag may
    be created to make it happen. Two things are therefore unobserved and are named here rather than
    implied by a green batch: that the install step succeeds in a job that also holds registry
    credentials and pushes -- the CI rehearsal installs and builds identically but pushes nothing --
    and that the gate's refusal is reached *on the way to* a publication rather than only in a dry
    run.
    What the first release after `dea8c6f` must show, and where to read it: `release.yml`'s
    `release-image-completeness` job prints `producer verdicts: <image>=<state>` on both paths, and
    the gate payload `outputs/release-images/release-images-<version>.json` carries
    `producer_verdicts` and `producer_summary`. Five `CONFORMING` is the observation; anything else
    and the release stops, which is the point. This item closes by reading that file from a real
    release; it cannot be closed by more testing, and nothing here should be read as if it had been.

19. **A release's producer evidence expires before the release does (F-52) — OPEN.** Option A's
    sixth evidence requirement is that the published-image evidence be bound to the producer record
    "so a reader can tell which builder made the bytes an operator pulls". The binding exists and is
    correct -- `release-images-<version>.json` embeds each image's producer block alongside its
    digests -- but it is published only as a **workflow artifact**. Measured at the candidate:
    artifacts from run `35958562520` carry `expires_at` 89 days after creation, and no workflow in
    this repository sets `retention-days`. The immutable GitHub Release assets are a different list,
    read from the sealed release itself: `cap-<v>.tgz`, `values-release-<v>.yaml`, `CHANGELOG.md`,
    the notes file and `known-issues.md`. The values file names every image digest -- so *what* an
    operator installs stays knowable forever -- while *who built it* lapses in about three months.
    So for any release older than the retention window, F-51's question ("which producer built the
    bytes an operator is pulling?") has no durable published answer, and the gate's own
    `producer_verdicts` cannot be re-read by someone auditing a year later.
    The fix is small and is a publication-content decision, so it is filed rather than folded into
    this round: add the completeness payload to `release-assets` in `release.yml`'s
    `release-chart` job -- one `cp` of `release-images-<version>.json`, whose producer fields are
    already frozen and tested -- and extend `test_release_image_completeness.py`'s asset-set
    assertion so the next chart cannot ship without it. Cost: a release-workflow change, therefore a
    new candidate and its recertification rounds, which is why it belongs with whichever batch next
    touches the release graph rather than being the only reason for one.

20. **Every push to `main` makes the tip uncertified, and nothing tells the releaser what to run
    (F-53) — OPEN, release-operations.** Measured, not reasoned: after the A2.2 candidate was
    certified, two documentation commits landed above it, each triggering a **push**-mode GA round.
    Running `RELEASE_GATE_PY` verbatim with `CERT_TAG_SHA` at the newer tip and a hypothetical
    `v1.0.7-rc1` returns `verdict: FAIL` with four failures, all from
    `cap-ga-certification.yml`:

    ```
    records mode='development', which is not 'final-strict'
    records full_ga_certified=False, which is not True
    gate_summary.planned=5, requires 0
    gate_summary counts 35 of 40 gates passed, so a gate has no PASS behind it
    ```

    The run it read was `35983433128` (event `push`, `ea1fb02`) -- green, newer than the strict
    round `35971523354` (`workflow_dispatch`, `dea8c6f`) -- and the gate declined to look past it.
    That decline is F-33's whole design: *"a rejected artifact does not send the gate looking
    further back: the newest green release-scoped round is the one this release would be relying
    on."* **The refusal is correct and must not be relaxed.** Same run, same discipline, visible in
    two other places: Linux's push-run at `ea1fb02` was passed over as
    `cap-production-certification: ['skipped']` and the release-layer run two commits back was used
    instead, while K8s -- an unconditional job -- was accepted one commit back.

    Operational consequence: to tag anything newer than a certified candidate you must produce
    certification *for that commit* -- a `workflow_dispatch` of `cap-ga-certification.yml` with
    `ga_strict=true` (about 70 minutes), and of `cap-linux-certification.yml` with `layer=release`
    if no release-layer round covers it, plus a soak if the reliability run is older than the tip.
    There is no cheap way around this and none is proposed.
    What would improve it is a sentence, not a gate: the refusal currently says what is wrong and
    never says what to run. `verify-certification` knows the tag sha, the required job set and which
    authority rejected which artifact, so it can print the dispatch that would satisfy it --
    workflow file, inputs, at the sha being tagged. The alternative, running strict GA on every push,
    is the cost decision `cap-ga-certification.yml` already records in its own comment (a soak plus
    40 strict gates per push) and is not recommended here. Filed as a release-operations item; it is
    explicitly **not** an invitation to soften F-33, and any fix must leave the four refusals above
    firing exactly as they did.

21. **A queued certification round is cancelled without running, and four workflow comments say it
    will not (F-54) — OPEN, wording plus a release-operations step.** Measured during this closure's
    own pushes. `4ceac00` was pushed at `11:51:09Z`; its GA round `35995454389` entered the
    `ga-…-refs/heads/main` group while `35992258108` (the round `87d86b3` started at `11:18:31Z`,
    whose `ga-certification` job was still running) still held it. At `12:07:37Z` -- one second after
    the next push landed -- `35995454389` was reported **`cancelled` with zero jobs**: it never
    started, so nothing ran and nothing was overwritten. `4ceac00` therefore has Linux
    (`35995454155`) and K8s (`35995454225`) both `success`, and no CI and no GA round at all. (CI's
    own `cancelled` is by design: `ci.yml` sets `cancel-in-progress: true`, which the certification
    workflows explain on its behalf -- "`ci.yml` keeps cancel-in-progress because a superseded
    unit-test run has no evidence value".)

    What the files claim: `cap-ga-certification.yml`, `cap-linux-certification.yml`,
    `cap-k8s-certification.yml` and `cap-ga-reliability.yml` each carry the sentence *"A push during a
    certification run queues behind it instead"* beside `cancel-in-progress: false`. Measured, the
    promise holds for a run that has **already started** and fails for one still **waiting**. In
    flight, twice over: `35992258108` ran from `11:18:31Z` through two later pushes and completed, and
    the Linux, K8s and GA rounds at `5f550c4` were still `in_progress` after the `a6d2bc9` push landed
    -- none of them cancelled, which is the design working. Waiting: `35995454389` never started (zero
    jobs) and was marked `cancelled` one second after a newer push entered the group while an older
    round still held it. The reading was then tested prospectively, and it held: the next push
    (`2f90450`) displaced the round the *previous* push had left waiting
    (`35998117484`, `cancelled` at `12:21:43Z`, two seconds after `2f90450` landed, again with zero
    jobs), while that commit's already-running Linux (`35998117487`) and K8s (`35998117450`) rounds
    were untouched and its CI (`35998117512`) was cancelled mid-job, which is `ci.yml` behaving as
    configured. The following push (`c9019f8`) made the contrast as sharp as it gets: the three
    certification rounds `2f90450` had left waiting were all displaced with **zero jobs**
    (`35998559524`, `35998559648`, `35998559793`, within a second of it), while that commit's CI run --
    already started, eight jobs already green -- was cancelled part-way through, which is
    `cancel-in-progress: true` doing exactly what it says. A group keeps its newest waiter; a run that
    has started is not its problem. The defect
    is therefore the sentence, and the sentence is load-bearing, because it is why a releaser assumes a
    commit they pushed has a round waiting.

    Why this is not a publication bug: an absent round is *evidence absence*, and the gate refuses on
    absence -- the same `MISSING` / `verify-certification` path F-33 and F-42 established, and §H of
    `docs/quality/cap-post-a22-closure-2026-09-24.md` measured. Nothing here can turn a displaced
    round into a green. Why it still costs something: if the displaced commit is the one someone
    meant to tag, they learn at tag time rather than now, and the run list shows a `cancelled` row
    that reads like an abandoned rerun.

    What is **not** exposed: a tag's own round. Every certification group keys on `github.ref` --
    `ga-${{ github.workflow }}-${{ github.ref }}`,
    `k8s-${{ github.workflow }}-${{ github.ref }}`, `cap-cert-${{ github.ref }}`,
    `ga-reliability-${{ github.ref }}` -- so
    `refs/tags/v1.0.7-rc1` is a different group from `refs/heads/main` and a push to `main` cannot
    displace it. Read from the files, and deliberately not confirmed by observing a tag round -- no
    tag was created.

    What closes it: (a) the four comments corrected to say a waiting run can be replaced, and (b) a
    line in the release procedure -- before tagging, confirm each of the four workflows has a run at
    that sha that **started**, not merely one that is not red. (a) is an edit to `.github/workflows/`,
    so under F-49's own rule it should ride the next batch that changes those files for a real reason
    rather than spend a recertification on wording; that is the same cost judgement F-50 records, and
    it is why this entry was filed instead of fixed in a documentation round.

22. **The K8s certification job can fail before it tests anything, and its own diagnostics cannot see
    it (F-55) — OPEN, harness observability.** `cap-k8s-certification.yml` brings its infrastructure up
    in the `cap-infra` namespace and waits with a fixed budget:

    ```
    156:  kubectl -n cap-infra rollout status deployment/postgres --timeout=120s
    157:  kubectl -n cap-infra rollout status deployment/minio --timeout=120s
    ```

    That wait has timed out on `minio` three times in the last 196 runs of this workflow -- job
    `105819996280` (run `35414380874`, `c7dd1f7`, `2026-09-19T02:07:26Z`), job `107639698508` (run
    `36000276172`, `94b17c6`, `2026-09-24T12:58:16Z`) and job `107647936310` (run `36004166031`,
    `a8d3fb2`, 22 minutes later at `13:20:07Z`) -- and all three logs end the same way: postgres
    "successfully rolled out" about six seconds after being created, then `Waiting for deployment
    "minio" rollout to finish: 0 of 1 updated replicas are available...` for exactly 120 seconds, then
    `error: timed out waiting for the condition`. The same block was sampled across the other
    non-cancelled failures of this workflow and all of them died later, at `Run K8s certification gates
    (strict: SKIP=FAIL)` -- so this is a distinct, rarer failure mode and not the usual one.

    Why the cause is **unknown**: the job's failure dump looks in the wrong places. Its diagnostic step
    runs `kubectl get pods -n cap`, `kubectl get pods -n cap-sandbox` and `kubectl get events -n cap`,
    and never names `cap-infra` -- so in both failed runs it printed `No resources found in cap
    namespace.` / `No resources found in cap-sandbox namespace.` and exited. Nothing in either log says
    whether the MinIO pod was pulling (`quay.io/minio/minio@sha256:a1ea29fa…`), starting, probe-failing
    or unschedulable. The pinned digest and the registry are therefore *not* implicated by this
    evidence, and are not exonerated by it either.

    **Correction, same day.** The cause was found afterwards, but *outside* this job: an independent
    registry probe and `cap-linux-certification.yml`'s container-initialisation error both name the
    refused pull, and the finding is **F-56**. That does not retire this entry -- the gap it describes
    is why a same-registry failure in two workflows took a hand-built probe to explain rather than one
    line of the log the runner already had.

    Why it is worth an entry: `cap-k8s-certification.yml`'s `k8s-certification` job is one of the four
    authorities `release.yml` reads, so a round that dies in infrastructure at tag time blocks
    publication with a log that cannot say why -- and the natural response, raising the timeout, would
    be weakening a wait without knowing what it waits for.

    What closes it: first, **see the failure** -- add `cap-infra` to the dump (pods with `-o wide`,
    `describe pod`, `get events -n cap-infra`, and the container's own logs), which is a workflow edit
    and so rides the next batch that touches these files under the same cost rule F-50 and F-54 record.
    Then, with the cause visible, decide whether 120 s is the right budget or whether the image needs a
    pre-pull -- a question the same day answered in a third way: neither, the pull is refused outright
    (**F-56**). Until then the release procedure should treat a red at this step as **re-run and compare**
    (§J item 7), not as a product failure and not as a timeout to be increased.

23. **The pinned MinIO image is no longer pullable anonymously, and every certification authority
    needs it (F-56) — BLOCKING, third-party availability.** Found by following F-55's red rather than
    explaining it away. The runner's own words, from `cap-linux-certification.yml`'s `Initialize
    containers` step at `a8d3fb2` (job `107647936396`, run `36004166112`, `13:13:26Z`):

    ```
    /usr/bin/docker pull quay.io/minio/minio@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e
    Error response from daemon: unauthorized: access to the requested resource is not authorized
    ```

    retried three times and then failing the job. Independently, from this box at `13:17Z` (`_tmp/quay_probe.py`):
    Quay's own `Www-Authenticate` realm issues an anonymous token for `repository:minio/minio:pull`
    (200), and with *that* token both `manifests/sha256:a1ea29fa…` and `tags/list` answer **401** with
    `access to the requested resource is not authorized` -- while `coreos/etcd` and
    `prometheus/prometheus` return **200** through the identical flow. So this is the `minio`
    repository being closed to anonymous pulls, not Quay refusing anonymous pulls in general, and not a
    missing digest: the repository itself is no longer readable without credentials.

    Measured consequence, same signature on two commits ~25 minutes apart: `36000276172` /
    `107639698508` (K8s at `94b17c6`, `Deploy PostgreSQL + MinIO`, F-55) and `36004166031` /
    `107647936310` (K8s at `a8d3fb2`, same step) -- postgres rolls out in seconds and MinIO never
    becomes ready, which is what a refused pull looks like from a deployment's point of view.

    What depends on it: six image lines across all four certification workflows
    (`cap-linux-certification.yml` ×3, `cap-k8s-certification.yml`, `cap-ga-certification.yml`,
    `cap-ga-reliability.yml`), the **default** `minio` service in `docker-compose.yml:101` (no
    `profiles:` guard, so it is part of the documented compose path an operator follows), the local
    certification bootstrap `scripts/certification/setup.sh:52`, and the lock file
    `deployment/third-party-images.json:32` with its registry-resolution evidence artifact. Every
    required release authority is therefore unable to produce evidence while this stands. What is *not*
    affected: sealed `v1.0.6-rc1` and the CAP images on ghcr, re-verified today (§K of
    `docs/quality/cap-post-a22-closure-2026-09-24.md`), because none of them embeds MinIO.

    Other sources tried, anonymously, from here (`_tmp/mirror_probe.py`): Docker Hub token is issued
    but `library/minio` and `minio/minio` both answer **401** for this digest; `public.ecr.aws` answers
    **401**; `ghcr.io` has no `minio/minio` namespace (token request refused, 403);
    `mirror.gcr.io` would not complete the anonymous token exchange from this network, so it is
    **unresolved rather than excluded**.

    This has a precedent recorded in the repository's own lock file: `previous_ref_status` for
    `docker.io/minio/minio:RELEASE.2025-04-22T22-12-26Z` reads "Unusable. Pulled successfully from
    GitHub runners as late as 2026-08-22 … then every certification job from 2026-09-13 on failed at
    container initialisation with 'pull access denied … may require docker login'". The dependency has
    already moved once because the vendor archived the open-source build; today it moved again for the
    same reason, one registry further along the chain.

    Options, none of which is a gate relaxation and none of which is authorised here:

    1. **Mirror the exact digest into the project's own ghcr namespace** and re-point the six workflow
       lines, the compose service, `setup.sh` and the lock file at it. The mirror must be verified by
       **digest equality** -- `sha256:a1ea29fa…` on ghcr must be the same manifest, not a rebuild --
       and `test_third_party_image_lock.py` plus the registry-evidence generator updated to name the
       new source. Blocked on the step above: someone has to obtain the bytes first, which may need a
       credentialed Quay pull or a vendor-supplied archive.
    2. **Supply credentials** for a private/quay-authenticated pull (a secret, not a source change).
       Fixes CI, leaves the operator-facing compose path broken for anyone without those credentials.
    3. **Change the object-store dependency** -- a product decision with a migration, not a fix.

    What must not happen is treating this as flakiness: F-55's timeout is a symptom, and lengthening it
    converts an unavailable dependency into a 120-second wait followed by the same red.

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
