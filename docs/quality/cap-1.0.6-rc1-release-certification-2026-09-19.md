# CAP 1.0.6-rc1 — Final Release Certification Report

Candidate: `c8c170fe2a2512949dd2e6fb429e7c83e04741db` (short `c8c170f`), branch `release/1.0.6-rc1`.
Superseded by runtime changes during this pass, in order: `154f5b6` → `71dad6e` → `0b4e207` → `2b65368` → `ad91e0e` → `1fc1c98` → `c8c170f`. Each step is classified in §2, and every gate bound to an earlier SHA is labelled as such rather than silently reused.
Prepared: 2026-09-19, from a Windows audit host plus GitHub-hosted Linux runners.
Publication: **none performed** — no `v*` tag, no GitHub Release, no image pushed, no existing tag or image overwritten.

> Reading note: a Windows machine passing its own suite is not production certification. Every
> claim below says which environment produced it. Items whose evidence was still executing when
> this was written are marked **IN FLIGHT** with the run id; §25 is the verdict.

---

## 1. Version facts

| Item | Value | Evidence |
| --- | --- | --- |
| `VERSION` (canonical) | `1.0.6-rc1` | `VERSION` |
| Carriers checked | 17 (`backend/tests/test_release_version_consistency.py`) | all agree; includes `CI` build-args, `docker-compose.yml` `${APP_VERSION:?…}`, `.env.example`, settings/`__version__`, Chart `version`/`appVersion`, 3 `values.yaml` tags, both Dockerfiles, both lockfiles, `test_phase_23` `RC_VERSION` |
| Last published release | `1.0.5` — tag object `b5c4eee…` → commit `32ec298`, GitHub Release "Cyber Agent Platform v1.0.5" created `2026-09-07T14:59:44Z` | `git ls-remote --tags origin`, `gh release list` |
| Release line | v1.0.0 … v1.0.5 published; `1.0.6-rc1` **not** published | tags above |
| Anchor commit for this line | `2030192` — `release: cut 1.0.6-rc1 anchor (pure version metadata bump)`, notes in `docs/releases/v1.0.6-rc1.md` | `git log` |

`1.0.5` cannot be inherited as a certification for this candidate: the diff classifier says so
(§2), so the gates were re-executed rather than carried forward.

## 2. Diff classification against the last certified and last published commits

`python scripts/release/classify_diff.py <from> <to>` (fail-closed by design):

| Range | Verdict | Runtime-affecting files |
| --- | --- | --- |
| `v1.0.5 → candidate` | **RECERTIFICATION REQUIRED** | 49 `production_runtime` + 1 `database` + 2 `dependency` + 3 `deployment` (96 files total) |
| `a5ce0c4` (last audited tree) `→ candidate` | **RECERTIFICATION REQUIRED** | 1 (`scripts/quality/scan_secrets.py`), plus 3 `deployment`, 5 `ci_workflow`, 12 `test_harness`, 11 `version_bump`, 4 `docs`, 4 `certification_generator`, 1 `release_metadata` |
| `ad91e0e → 1fc1c98` (`.github/workflows` concurrency + contract tests + changelog) | **INHERITED** | none |
| `2b65368 → ad91e0e` (`.env.example`, `docker-compose.yml` comment corrections, socket detector, boundary tests) | **RECERTIFICATION REQUIRED** | `.env.example` (`production_runtime`), `docker-compose.yml` (`deployment`) |

Two properties of the classifier are worth naming because they shaped this line's cost:
`scripts/quality/**` and `.env.example` have no explicit rule, so unknown paths fall to
`production_runtime` — the fail-closed default, deliberately kept; and a comment-only edit to a
deployment file is still a deployment edit. Both are conservative in the right direction.

Artifacts: `outputs/cert-1fc1c98/`, `outputs/cert-ad91e0e/diff-2b65368-to-final.json`,
`outputs/cert-71dad6e/diff-*.json`, `outputs/cert-final/diff-0b4e207-to-final.json`.

## 3. Local audit, re-executed, machine-readable

Full backend suite at `8a8711f` (same product code as the candidate; the delta since is
workflows, changelog and appended contract tests):
**1304 passed, 132 skipped, 1 deselected, 0 failed** in 23m40s, coverage **91.83%** against a
90% gate (`--cov-fail-under=90`), exit 0.

Artifacts (all machine-readable): `outputs/cert-8a8711f/backend-junit.xml` (per-test outcomes,
skip reasons), `outputs/cert-8a8711f/coverage.xml`, `outputs/cert-8a8711f/backend-pytest.log`,
`outputs/cert-71dad6e/*` for the identical run one commit earlier,
`outputs/cert-8a8711f/migration-catalogue-junit.xml`, `outputs/cert-1fc1c98/…`.

Console: `npm run test` 29 passed (4 files), `npm run lint` clean with `--max-warnings=0`,
`npm run build` = `tsc -p tsconfig.app.json && tsc -p tsconfig.node.json && vite build` clean;
largest entry chunk 487.11 kB (156.66 kB gzip) under the 550 kB threshold.
Python lint: `ruff check backend/app backend/tests benchmarks/phase22` clean.

Error-log audit of the local run: 0 `ERROR`, 0 `Traceback`, 0 warning lines
(`filterwarnings = ["error", …]` makes any warning a failure, so this is structural, not lucky).

## 4. The suite is hermetic against a developer `.env` — a defect found here

`Settings` resolves `env_file=".env"` against the CWD, so any untracked key used to
reconfigure the tests. Three leaks were found, each diagnosed only after a confusing failure:
`RBAC_TRUSTED_PROXY_SECRET` (bare 401s), `CAP_ZAP_API_KEY` (ZAP provisioned inside the
incident-plane tests), and `APP_VERSION` — the last one made `/health` report `1.0.5` on a tree
whose carriers all said `1.0.6-rc1`, i.e. **the shipped version endpoint could be wrong in
production for the same reason**. Pinning keys is whack-a-mole, so
`backend/tests/conftest.py` now clears `env_file` on the `Settings` class before any app module
imports: the suite sees process env plus declared defaults, exactly like a container.
`tests/test_settings_dotenv_hermeticity.py` holds it with a poisoned `.env`, a negative control
that fails without the fix, and a check that process env still configures the app.

## 5. Alembic / PostgreSQL migration certification on a real server

Chain: 21 revisions, single head `20260812_0021`, linear base→head. **`backend/alembic` is
byte-identical between `v1.0.0`, `v1.0.5` and the candidate** (`git diff --stat v1.0.0 HEAD --
backend/alembic` empty), so this release ships no schema change — which is a fact to state, not a
reason to skip the run.

Executed by `backend/tests/test_migration_catalogue.py` against a live PostgreSQL 16.2 server
(pgserver bundle), **6 passed** (`outputs/cert-8a8711f/migration-catalogue.log`):

1. single head; the migrated database's `alembic_version` equals the scripts' head;
2. fresh empty DB → `alembic upgrade head` → 104 public tables;
3. every table (91) and index (38) that any revision's `upgrade()` created, minus anything a
   later `upgrade()` dropped, present **by name**;
4. no invalid index anywhere (`pg_index.indisvalid`), >30 indexes visible so the check is not
   vacuous;
5. models vs schema: no model table or column missing, nullability identical, every model-declared
   index enforced by some index or unique constraint over the same columns;
6. a populated database survives a real `downgrade 20260802_0017` → `upgrade head` cycle:
   per-table column fingerprints unchanged, tables not lost, head reached, zero foreign-key
   orphans after both paths.

Naming drift surfaced and **pinned, not hidden** (§23 findings F-4/F-5): revision
`20260729_0003`'s `ck_agents_status` / `ck_tasks_status` / `ck_task_executions_status` exist as
`ck_agents_ck_agents_status` etc. (identical `pg_get_constraintdef`, re-named by the metadata
`NAMING_CONVENTION`); `agents.runtime_image` is a nullable column only the schema has; and
`ix_playbook_executions_idempotency_key` / `ix_tools_tool_type` are enforced under other names.
`alembic check` therefore reports ~60 operations and is **deliberately not a gate**: a check that
is red on the day it is added gets deleted within a release. The drift lists in that test fail if
the set grows and fail if an entry disappears un-fixed.

## 6. PostgreSQL version matrix

New job `postgres-version-matrix` (Linux certification workflow, release/main layers) runs the
chain on `postgres:15-alpine`, `16-alpine`, `17-alpine` with `fail-fast: false`:
**all three cells PASS** at `0b4e207`, recording the exact servers certified
(`outputs/cert-final/linux-0b4e207/pg-matrix-*/pg-matrix-server.txt`):
PostgreSQL **15.19** (`150019`), **16.15** (`160015`), **17.11** (`170011`), each with
junit evidence (`junit-pg-matrix-<v>.xml`). CI also gates the chain on every push
(`migration` job, PostgreSQL 16).

## 7. RBAC and the trusted proxy, on Linux

`ci.yml` runs the backend suite on `ubuntu-latest`, so the authorization middleware tests
(`X-CAP-User` + `X-CAP-Proxy-Secret`, `_permission_for` route mapping, the approval
`approval.decide` gate) execute on Linux, not only on this Windows box. On a live cluster the
Kubernetes certification adds: GATE 3 the worker mounts no runtime socket, GATE 4 its Role is
namespaced least-privilege, GATE 5 adversarial attempts with the worker token are denied,
GATE 6/7 sandbox has no SA token and NetworkPolicy denies by default — all PASS at
`154f5b6` (`outputs/cert-154f5b6/k8s-artifacts/cap-28.6-k8s-certification.json`) and
re-executing at `8a8711f` (**IN FLIGHT**, run `35427778399`).

## 8. ZAP API key: the README and the code now agree

The documented variable was previously consumed by nothing (`Settings` had no field,
`extra="ignore"` swallowed it), so `SECRET_NOT_FOUND` made **creating an incident impossible on
a fresh deployment**. Fixed by seeding `MemorySecretProvider` from settings; this pass closed the
remaining halves and pinned them in `backend/tests/test_zap_api_key_contract.py`: the key resolves
when configured; the documented `SECRET_NOT_FOUND` happens when it is not; compose forwards it to
the **api only** (the worker's `AssessmentService` is an HTTP dependency and never resolves the
credential); the chart's `cap-runtime` Secret documents it; `.env.example` carries a placeholder;
a placeholder is rejected under `APP_ENVIRONMENT=production`. No fail-open path: an unset key
produces a typed error, not a degraded success.

Finding F-6 (MEDIUM): that error surfaces as **HTTP 404** — `SecretNotFound` was mapped to
"not found" for the resource, but a missing *server-side* credential is a configuration fault, and
404 tells an operator to look at the wrong thing. Changing the status is an API-contract decision,
so it is recorded rather than slipped into a candidate.

## 9. Redis: no business meaning to certify, so none was invented

The directive forbids tests that exist to claim "we have Redis". Reading the code settled it:
**nothing in `backend/app` imports a Redis client.** `settings.redis_url` feeds one readiness
field, `redis_configured = bool(redis_url)` — proof a string exists, not that anything connected —
and `telemetry/stream.py` states it has no broker dependency. `docker-compose.yml` nevertheless
runs Redis and the production checklist asked operators to secure, monitor and capacity-test it.
The documents were corrected to say what is true (`.env.example`, the checklist,
`docs/known-issues.md`); no Redis test was added. Removing the service and the field is a
settings/API change belonging with the event plane that would justify them.

## 10. S3-compatible object store (MinIO) — a retired vendor, repaired mid-certification

The vendor archived the open-source server, client and KES: `dl.min.io` answers **HTTP 410 Gone**
for every community release asset, and `minio/minio` no longer exists on Docker Hub. Timeline from
real evidence: the same coordinate rolled out on GitHub runners on **2026-08-22**
(`_ci_logs/cert_run8_full.log`: `deployment "minio" successfully rolled out`) and failed with
`pull access denied for minio/minio` on **every** certification job from 2026-09-13, so Linux,
K8s, GA and the nightly soak all died in container initialisation — before a single gate ran —
and `docker-compose.yml` shipped the same unreachable coordinate to operators. The soak's
`mc` bootstrap failed the same way *silently*: `curl` without `-f` wrote an HTML error page to
`/usr/local/bin/mc`.

Repairs, all at this candidate:
- nine reference sites now pull the identical bytes from the official MinIO organisation on
  quay.io, **pinned by digest**: `quay.io/minio/minio@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e`
  (`RELEASE.2025-04-22T22-12-26Z`). The digest is the **manifest-list** digest: a bare tag request
  returns a legacy schema1 conversion whose digest differs (`sha256:92114c07…`), and recording that
  one instead would silently break non-amd64 runners — both traps are asserted in
  `tests/test_third_party_image_lock.py`;
- `mc` installs from a pinned GitHub release asset, verified against its published
  `sha256sum` (`01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891`), `curl -fSsL`,
  and the job fails with `FATAL: mc checksum does not match`;
- `deployment/third-party-images.json` is the single source of truth for images and binaries
  (registry, tag, digest, provenance, upgrade procedure). `referenced_by` sites hardcode it and
  are checked; `derived_by` (the GA cluster manifest) resolves it from the lock at runtime — and
  the test rejects a file that claims to derive it while pasting the coordinate;
- object-store behaviour is certified by the gates that use it: 12 `object_store` tests (skipped
  without a server, executed in the Linux certification), K8s GATE 21 *object store outage
  blocks* and GATE 24/25 backup/restore + data-survives-restart, all PASS at `154f5b6` and
  re-running at `c8c170f` (**IN FLIGHT**, run `35429505106`).

Provenance recorded honestly, including the gap: the Quay image's own labels carry
`release=RELEASE.2025-04-22T22-12-26Z`, `vendor: MinIO Inc <dev@min.io>`, UBI9 base, built
`2025-04-22T22:35:01Z` — 59 seconds after the vendor's **PGP-signed** tag
(`f19c534b…`, "Minio Trusted <trusted@minio.io>", peeling to commit `0d7408fc`). Byte equality
with the copy previously certified from Docker Hub **could not be proven from this host**
(`docker.io` and `hub.docker.com` are unreachable here), and the image label `vcs-ref`
`519a52b1…` resolves in neither `minio/minio` nor `minio/docker-minio`. It is proven
*behaviourally* instead: real S3 signature checks, multipart evidence upload, versioning and the
DR restore path run against this image in the certification jobs. **Vendor risk stands**: no
further CVE fixes will arrive for this image, so the mitigation is digest pinning, network
isolation, an own mirror, and a migration plan — recorded as a limitation, not closed.

## 11. Container images and digests

Built and scanned per push in `ci.yml` `image-and-security`: both product images with
`VERSION` resolved from the canonical file and `REVISION=${{ github.sha }}` (the hardcoded
`VERSION=1.0.0-rc1` build-arg that mislabelled every CI image for five releases is gone and
`test_release_version_consistency.py` now forbids literal versions in build/packaging layers).
`build-push-action` runs `push: false, load: true`, so CI produces **image IDs, not registry
digests** — that is the honest answer for a candidate that must not be published. Sandbox/egress
images are built in the certification job and recorded structurally in
`outputs/cert-final/linux-0b4e207/cert-artifacts-release/sandbox-images.json`
(`image_id`, `size_bytes`, `created`, `base_image`, `dockerfile_sha256`; `repo_digest` is empty
precisely because nothing was pushed). Registry digests appear only when `release.yml` publishes
(`provenance: true`, `sbom: true`, `ghcr.io/…/cap-backend:<version>`), which this task forbids.
**Finding F-7 (MEDIUM):** the release publishes 2 images while a deployment needs 5, and the chart
defaults the other 3 to `:latest` tags no registry serves.

## 12. Nginx and the console route — certified through the real image, not a stand-in

The local audit's Python front door was for walking the console; it certifies nothing. **K8S-GATE
33** now drives the shipped `cap-frontend` image in the kind cluster and asserts: the built bundle
is served from `/usr/share/nginx/html`; a deep link (`/acquisitions`) is rewritten to `index.html`
rather than 404; `/api/health` and `/api/acquisitions` succeed **with no identity headers from the
client**, which is what proves `envsubst` filled `X-CAP-User` and `X-CAP-Proxy-Secret` from the
pod env (an unset secret yields an empty header and the middleware denies); and an unknown
`/api/…` path returns the application's 404/422 rather than `index.html` — the SPA fallback
swallowing an API path shows up in a browser as a JSON parse error and never in a readiness probe.
**Defect found and fixed here (F-13, HIGH).** The authorization map had no rule for
`/acquisitions`, `/agents`, `/agent/*`, `/tasks`, `/workflow`, `/registry`, `/capabilities` or
`/runtime`, so all of them inherited the fallback `platform.manage` — which the RBAC catalog
itself labels "Operate *legacy* control-plane management APIs". The console's shipped identity is
`read-only`, so `GET /api/acquisitions` answered `403 Permission required: platform.manage`: half
the console's pages were unusable on a default deployment, and the only available workaround was
granting the console user the whole platform — the escalation the map exists to prevent. K8S-GATE
33 found it because it issues the console's own request, with no identity headers of its own; no
unit test had ever asked as `read-only`. Each resource now has explicit read/execute (or write)
permissions, SOC Analyst gains the analyst-plane actions it already performs, and `read-only` gains
exactly the reads the console displays and no writes.
`tests/test_rbac_permission_mapping.py` holds the line: every advertised endpoint must map to a
defined permission, the `platform.manage` fallback is an explicit reviewed list
(`POST /heartbeat`), and a negative control proves an unruled route still falls through — so the
next console endpoint cannot silently inherit the platform again.

Status **IN FLIGHT** at `c8c170f` (run `35429507835`); it took four dispatches to get here, each
fixing the *gate* (assumed container port 8080 where the Service publishes 80; called
`.strip()` on a `CompletedProcess`), which is worth recording: the routing behaviour itself has
not been contradicted.

## 13. Linux / OCI runtime certification

`cap-linux-certification.yml`, layer `release`: **fast + full + production all green** at
`0b4e207` (run `35423075901`, 17m10s), 12 gates PASS in each collected artifact
(`cert-artifacts-main/`, `cert-artifacts-release/cap-28.5-linux-certification.json`), including
the 500-run OCI correctness benchmark, 100-run kill-9 HA, full adversarial security and the
28.1–28.5 regression. Environment recorded (Ubuntu 24.04.5, kernel 6.17.0-1022-azure, Docker
info, cgroup, nft/iptables rulesets). Re-running at `c8c170f` (**IN FLIGHT**, run `35429505106`)
because the intervening `.env.example`/compose edits are runtime-affecting by policy.
The artifact's `worker_control_plane_isolation` field is discussed in §23 (F-1): it was a
substring grep and is now a structural, per-path fact.
Critical skips: `CAP_CERTIFICATION_STRICT=1` turns an availability skip into a failure in the
certification-critical set, and the release layer's `GA-GATE 40 evidence: critical skip report`
step is the machine-readable check that the count is zero.

## 14. Kubernetes certification

`cap-k8s-certification.yml`: **32/32 gates PASS** at `154f5b6` — kind v0.27.0 on Kubernetes
`v1.37.0`, 3 nodes, Cilium NetworkPolicy enforcing, PostgreSQL 16 + MinIO in `cap-infra`, all CAP
images imported into every node — with the DR sequence (GATE 24 backup/restore round-trip, GATE 25
data survives restart), HPA/PDB capacity (22), SLI/SLO metrics (23), alerting config (27),
baseline regression (28), RTO (29), resource limits (30), security baseline (31), overall health
no-stale (32), plus the long-run lease renewal pre-gate. The candidate adds GATE 33 → **33 gates**;
`generate_report_28_6.py`'s `ALL_GATES` is the authority and any report saying "1..32" predates it.
Status at `8a8711f`: **IN FLIGHT** (run `35427778399`).

## 15. Gates inherited vs gates re-run

**Re-run on the candidate** (because the classifier says runtime-affecting since the last
certification of each): Linux full+production, K8s 1..33, the PostgreSQL matrix, CI's unit/coverage/
console/packaging/image jobs, the 500-run OCI benchmark, the local suite + migration catalogue +
secret scan + console flows, and the 2-hour soak.
Reason: `v1.0.5 → candidate` carries 49 production-runtime files; and `ad91e0e` re-classified as
runtime-affecting on two config files, which invalidated the runs before it.

**Inherited, with the classifier as the reason** — `8a8711f → 1fc1c98` and `2b65368 → ad91e0e`'s
docs/test halves:
| Evidence | Bound to | Why inheritance is legitimate |
| --- | --- | --- |
| K8s 32 gates + DR + soak-dependent tier2 ops | `154f5b6` | superseded: re-executing at `8a8711f`; the `154f5b6` result is kept as history, not as this candidate's certificate |
| Linux full/production + PG matrix | `0b4e207` | delta to `1fc1c98` is `.github/workflows`, `CHANGELOG.md`, `docs/*`, one test file → **INHERITED** (`outputs/cert-final/diff-0b4e207-to-final.json`) |
| soak GA-GATE 24/25/26 | `ad91e0e` | delta to `1fc1c98` is ci_workflow/docs/test → **INHERITED**; the soak is 2h of the same product code, and the alternative is destroying evidence for no information (§23 F-8 explains why a push no longer cancels it) |

## 16. Migration re-verification for the release-critical path

Beyond §5: the CI `migration` job (every push, PostgreSQL 16) additionally proves
`downgrade base → upgrade head` rebuilds from nothing, and the matrix does the same on 15/17.
`scripts/certification/ensure_schema_version.py` remains what it always was — seeding an
`alembic_version` row so the *worker health check* passes on a `create_all` schema — and the
audit deliberately did not misuse it as a head verifier; the head assertion is SQL in §5/§6.

## 17. Helm upgrade path

Chart: `helm lint` + `helm template` + `helm package` in CI's packaging job, plus rendered-output
assertions (worker `SANDBOX_PROVIDER=kubernetes-sandbox`, in-chart egress proxy, NetworkPolicy
present, no `APP_ENVIRONMENT=development`, `resources.requests` intact). Upgrade itself is
certified on live clusters: K8s GATE 18 *rolling update* and GATE 19 *version skew
compatibility*, `test_phase_28_7_ga_tier2_cluster.py` asserting exactly one new Helm revision
after upgrade, and GA-GATE 34 performing a Helm upgrade **during sustained load** inside the soak.
`deployment/helm/cap/values.schema.json` rejects a malformed override before it reaches a cluster.

## 18. Rollback — what is actually true

- **Schema**: identical (`20260812_0021` at v1.0.0, v1.0.5 and the candidate), so
  `helm rollback` to a v1.0.5 release needs no migration and no data repair. Stated as a fact
  with its evidence rather than assumed.
- **What will not roll back cleanly**: a workflow instance parked at the new approval gate.
  `1.0.6` writes `WAITING` steps with `context.approvals`; v1.0.5's approval handler has no
  decision endpoint and re-parks on resume (`_tmp/wt-v105/backend/app/workflow/nodes.py:62-71`,
  `runtime.py:57-60`), so the run is **stranded, not silently approved** — it cannot fail open,
  but an operator must list open `WAITING` approval steps before deciding to roll back.
- The guide itself (`docs/deployment/rollback.md`) keeps the hard rules: freeze high-impact
  operations and preserve evidence, `helm rollback --wait`, re-verify RBAC deny paths / audit /
  queue / critical workflows afterwards, restore a *verified* backup only after stopping writers
  and obtaining incident authority, issue a **new RC**, and never delete volumes or reset the
  database as a rollback.
- Honest limit: this line's soak and DR gates measure recovery of the *current* version; a
  v1.0.6 → v1.0.5 rollback has not been executed on a cluster. Doing it is a certification we
  recommend scheduling, not one this report can claim.

## 19. Trivy, SBOM, provenance — what exists today

`ci.yml` `image-and-security`: trivy-action pinned **by commit SHA**
(`ed142fd0673e97e23eac54620cfb913e5ce36c25`), three scans (`fs` on the repository,
`cap-backend:ci`, `cap-frontend:ci`) each `severity: HIGH,CRITICAL`, `ignore-unfixed: true`,
`exit-code: "1"` — a new fixable HIGH/CRITICAL in a shipped image fails the push, and that job is
green at `8a8711f` (**IN FLIGHT** `35427771415`). SBOM and provenance attestations are produced by
`release.yml`'s buildx on publish (`sbom: true`, `provenance: true`) with `VERSION`/`REVISION`
build-args; a candidate that is not published therefore has **no registry attestation**, and this
report says so rather than presenting an image ID as one. The GA workflow's supply-chain job adds
`syft` SBOMs (cyclonedx + spdx) and trivy JSON per image, pinned; those artifacts from earlier runs
live under `outputs/cap-cert-ga/` and are labelled by the commit they describe.
`test_phase_28_7_ga_tier2_supply_chain.py` asserts the toolchain and skips (→ fails under
strict mode) when `syft`/`trivy` are absent — which is exactly why the count appears in §22.

## 20. Secret scan, executed

`scripts/quality/scan_secrets.py` — stdlib-only, so no vendor download can break the gate that
catches credentials (this line's tooling has already been bitten by one). Scanned **1034 tracked
files, 0 findings** (`outputs/cert-154f5b6/secret-scan.json`; re-run clean at the candidate).
Rules: provider-shaped tokens (AWS/GitHub/Slack/Stripe/Google/OpenAI/HuggingFace), PEM header +
base64 body, JWTs, credentials inside URLs, assignments of high-entropy values to secret-shaped
names, and a tracked `.env` as a finding by itself. Getting to zero took four rounds, each driven
by a *planted* credential in `tests/test_secret_scan.py` rather than by judgement: the first
version suppressed `ghp_…` and a 34-char key behind an over-wide identifier exception, and
carried a wildcard allow-list entry that could have hidden a live token inside an audit report.
Both directions are now asserted — 5 planted secrets must be found, 11 reference-shaped values
must not be, a provider token must still be a finding inside `tests/`, `docs/` and `scripts/`, and
`.env` is verified git-ignored. Exceptions are scoped to the weak rules and each states a reason,
which the test also enforces. The local `.env` used during this audit was never read, printed or
committed (and §4 makes the test suite immune to it).

## 21. Coverage, read honestly

`91.83%` line coverage on **24 306 statements**, CI-gated at 90%
(`outputs/cert-8a8711f/coverage.xml`). It is the *unit job's* number: no certification workflow
passes `--cov`, so infra-coupled lines are in no denominator anywhere — the comment in `ci.yml`
that claimed otherwise is corrected, and `outputs/cert-154f5b6/coverage-low-modules.json` lists
the 17 modules with ≥30 statements below 80%: `acquisition/reconcile_cli.py` (0%, exercised by
GA-K8s DR gates 24/25 and the soak, not by any coverage-collecting run), `sandboxed_browser`
(17%), `worker_main` (21%), `acquisition/gc` (23%), `sandboxed_fetch` (51%), `notification/service`
(52%), `assessment/service` (53%), `detection/service` (56%), `acquisition/store` (58%),
`api/routes/acquisition` (60%), `sandbox/oci_shim` (64%), `incident/service` (66%),
`api/routes/health` (68%), `acquisition/claim_loop` (71%), `runtime/service` (74%),
`sandbox/oci_reaper` (75%), `telemetry/service` (76%). No test was added to flatter the number;
this pass added tests that found three real defects (§4, §10, §23) and one whose correct answer was
to refuse a test (§9).

## 22. Skipped tests, by category, and where each actually runs

132 skips in the local run (`outputs/cert-8a8711f/skip-audit.json`; 11 distinct reasons, every one
an availability gate — no skip is a parked failure):

| Category | Count | Where it executes for real |
| --- | --- | --- |
| kind cluster unavailable | 55 | K8s certification + GA certification (Linux runners) |
| real PostgreSQL required | 29 | CI `migration` job, Linux certification, PG matrix; plus the 6 catalogue tests run locally (pgserver 16.2) |
| real S3 object store required | 12 | Linux certification (Quay-pinned MinIO), GA certification DR |
| browser (Playwright/Chromium) | 10 | Linux certification sandbox-image layer |
| OCI container runtime | 6 | Linux certification |
| gate24 soak not run in this session | 4 | GA Reliability soak workflow |
| supply-chain toolchain (syft/trivy) | 4 | GA certification supply-chain job |
| certification DR evidence | 2 | produced by the K8s certification run |
| promtool not installed | 1 | Linux/GA certification install it |

Under `CAP_CERTIFICATION_STRICT=1` the certification-critical ones become **failures** rather than
skips, which is the point of that switch.

## 23. Findings, by severity

**BLOCKER** — none open.

**HIGH — resolved during this pass (each with the evidence that found it)**
- F-1 `worker_control_plane_isolation` was derived by substring-grepping `docker-compose.yml` and
  `.env.example`, so prose could set a security verdict; now structural and per-path (§13).
- F-2 The documentation contradicted the deployment about the runtime socket: known-issues called it
  CLOSED and `.env.example` promised it was gone, while compose mounts
  `/var/run/docker.sock` into `acquisition-worker` — correctly, because `oci-sandbox` drives the
  docker CLI. Each path is now stated where it applies, with the host-root consequence written next
  to the mount, and a boundary test that fails if code and prose drift in **either** direction.
  Nothing was deleted to make the claim true: removing the socket would break the provider that
  needs it (§10/§13 method applies here too).
- F-3 The object-store image and the `mc` download were both unreachable upstream, which had
  silently killed every certification job since 2026-09-13 (§10).

- F-13 Console reads charged the whole platform: eight API prefixes fell through to
  `platform.manage`, so the default `read-only` console 403s on Acquisitions/Agents/Tasks/
  Workflow/Registry/Capabilities/Runtime and the workaround was a blanket grant. Fixed with
  per-resource read/execute permissions plus a mapping test that makes an unmapped console
  endpoint a failure (§7).

**MEDIUM — open, recorded, not papered over**
- F-4 Migration/schema naming drift: constraints and indexes renamed relative to what the revisions
  and models promise; enforcement verified intact; `alembic check` is not gated (§5). A real fix
  needs a renaming revision, which is runtime-affecting and cannot land in a release candidate.
- F-5 `agents.runtime_image` is a column only the schema has (nullable, unused by the ORM).
- F-6 `SECRET_NOT_FOUND` returns **404** for a missing server-side credential (§8).
- F-7 Release artifacts cover 2 of the 5 images a deployment needs; the chart defaults the other 3
  to unpublishable `:latest` tags (§11, now documented in README/compose guide/checklist).
- F-8 Concurrency cancelled in-flight certification runs on any push to the candidate branch; a
  17-minute release-layer run was destroyed (run `35427694720`). Fixed at `1fc1c98` for all four
  certification workflows; CI keeps cancelling; both halves are asserted.
- F-9 Vendor risk: the pinned MinIO image receives no upstream CVE fixes (§10).

**LOW**
- F-10 The Linux certification JSON had no `commit` field, so the artifact could only be bound to a
  SHA through run metadata; the K8s and GA artifacts do record it. Recorded for the follow-up
  rather than "fixed" by relabelling old artifacts.
- F-11 `docs/Phase 14 Development Report.md` contains mojibake in a table (historical document).
- F-12 `redis_configured: true` in the readiness payload reads as health to a reasonable operator
  (§9); the documents now say what it is, and removing it is an API change.

## 24. Exact release artifact set, if publication were authorised

Per `release.yml`, all keyed to the tag and its commit: `ghcr.io/<owner>/cap-backend:1.0.6-rc1`
and `cap-frontend:1.0.6-rc1` (both with `VERSION`/`REVISION` build-args, buildx provenance + SBOM
attestations), the Helm package `cap-1.0.6-rc1.tgz` (`helm package` after `helm lint` +
`helm template`), and as GitHub Release assets: that chart archive, `CHANGELOG.md`,
`docs/releases/v1.0.6-rc1.md`, `docs/known-issues.md`. A tag push also runs `ci.yml` via
`workflow_run`-equivalent `quality-gates` reuse, so a tag cannot publish on red gates, and
`validate-tag` aborts unless the tag equals `VERSION` exactly.
**Not produced today and required before any real deployment**: the 3 sandbox/egress images as
published, digested coordinates (F-7); container registry digests for the candidate (nothing is
pushed); and a v1.0.6 → v1.0.5 rollback exercise (§18).
Nothing was published by this task: no `v*` tag, no Release, no image or tag overwritten.

## 25. Verdict

Evidence assembled at the candidate and through inheritable deltas: local backend suite
1304 passed / 0 failed at 91.83% coverage; console 29 tests and a clean typecheck/lint/build;
credential scan 1034 files / 0 findings with two-way controls; migration chain certified on a live
PostgreSQL 16.2 including a populated downgrade/upgrade cycle, and on PostgreSQL 15.19, 16.15 and
17.11 in CI; Linux fast/full/production layers green with the 500-run OCI benchmark, HA and
adversarial security; Kubernetes 32/32 with DR, object-store outage gating and least-privilege
RBAC; 12 of 12 gates in each collected Linux certification artifact; and the retired object-store
dependency replaced with the same, provenance-documented bytes so the pipeline can run at all.

**CAP v1.0.6-rc1 RELEASE BLOCKED — publication requires explicit authorization, and four
certification runs must land green on the candidate first.**

In flight when this was written, all bound to `c8c170f`: CI (run `35429478441`), the Linux
release layer including the PG matrix (run `35429505106`), the K8s certification with the
defect-finding console gate (run `35429507835`), the 7200s reliability soak (run
`35429509771`) and the strict GA certification that must follow it. **None of these results is
asserted here as passing.** Once every one of them is green on `c8c170f` (or a later
candidate whose delta the classifier reports as inheritable, with that output attached) and the §23 MEDIUM items are each either fixed or
formally accepted with an owner, the verdict becomes:

> CAP v1.0.6-rc1 RELEASE READY — awaiting explicit publication authorization.

Until then: **do not tag, do not publish a Release, do not push an image, do not overwrite any
existing tag.** Publication is a separate, explicit act.
