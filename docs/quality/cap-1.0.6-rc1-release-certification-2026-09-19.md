# CAP 1.0.6-rc1 — Final Release Certification Report

Certified SHA: `c52dcb9` (branch `release/1.0.6-rc1`) — the commit CI, the Linux release layer and
the K8s certification all ran green on. Tip at the time of writing: `b9b7f03`, one audit-tool
commit later; `classify_diff c52dcb9 b9b7f03` reports **INHERITED** (one `repo_tooling` file, one
`test_harness` file), and `outputs/cert-becbad4/diff-b84a13e-to-becbad4.json` /
`outputs/cert-c52dcb9/` hold the machine-readable proofs.
Superseded by runtime changes during this pass, in order: `154f5b6` → `71dad6e` → `0b4e207` →
`2b65368` → `ad91e0e` → `1fc1c98` → `c8c170f` (console authorization, F-13) → `8ffd7bd` (the
report generator's PyYAML dependency, §13) → `7e1f0e2` → `0bc8efa` (approval-gate assertions) →
`d9a2e01` (execution-lease heartbeat isolation + transient-renewal retry, F-14) → `a5d7379` /
`3ebca44` (the renewal cadence contract behind the same defect, plus the lint corrections it
required) → `b84a13e` (`repo_tooling` classification, enforced by a test) → `becbad4` (release
audit tool) → `93d360c` (cadence boundary tests) → **`c52dcb9`** (the single-connection guard that
CI's unit job caught at `becbad4`, F-18) → `b9b7f03`. Each step is classified in §2, and every gate
bound to an earlier SHA is labelled as such rather than silently reused.
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

`python scripts/release/classify_diff.py <from> <to>` (fail-closed by design), re-executed against
the frozen candidate. Machine-readable output in `outputs/cert-becbad4/diff-*.json`.

| Range | Verdict | Runtime-affecting files |
| --- | --- | --- |
| `v1.0.5 → c52dcb9` | **RECERTIFICATION REQUIRED** | 51 `production_runtime` + 1 `database` + 2 `dependency` + 3 `deployment` (116 files total) |
| `a5ce0c4` (last audited tree) `→ c52dcb9` | **RECERTIFICATION REQUIRED** | 6 `production_runtime` + 3 `deployment` (65 files: 21 `test_harness`, 10 `version_bump`, 9 `certification_generator`, 7 `docs`, 5 `ci_workflow`, 3 `repo_tooling`, 1 `release_metadata`) |
| `c8c170f → c52dcb9` | **RECERTIFICATION REQUIRED** | 3 `production_runtime` (`auth/rbac.py`, `middleware/authorization.py`, `worker/runtime.py`) |
| `d9a2e01 → c52dcb9` | **RECERTIFICATION REQUIRED** | 4 `production_runtime` — `worker/runtime.py` and `acquisition/worker_path.py` twice: the renewal cadence (`3ebca44`) and the single-connection guard (`c52dcb9`, F-18). `d9a2e01` itself was certified green on Linux and K8s, and this is the round that replaces it |
| `c52dcb9 → b9b7f03` (audit-tool file rows) | **INHERITED** | none: one `repo_tooling`, one `test_harness` |

Two properties of the classifier are worth naming because they shaped this line's cost:
`.env.example` and any unlisted path fall to `production_runtime` — the fail-closed default,
deliberately kept — and a comment-only edit to a deployment file is still a deployment edit. Both
are conservative in the right direction.

**A category this pass added, stated so it can be judged rather than trusted.** `scripts/quality/**`
used to fall to that fail-closed default, so editing the secret scanner's docstring demanded a
2-hour re-soak. `becbad4` gives it a `repo_tooling` category, which is inheritable. The
justification is not a comment: `backend/tests/test_release_diff_classifier.py` resolves every
`docker build` context CI actually runs and asks whether the repository-root `scripts/` tree is
inside one, then checks both Dockerfiles, `docker-compose.yml` and every chart manifest for a
reference to it, with planted controls for each route — including the route that must NOT fire (the
backend context legitimately owns its own `backend/scripts/`, which stays runtime-affecting). If
scripts ever start shipping inside an image or a pod, that test fails and the category has to go
back to fail-closed. The scan rules were also anchored (`=scripts/quality/`) because the matcher is
substring-based and `backend/scripts/quality/x.py` would otherwise have borrowed the category.

Artifacts: `outputs/cert-becbad4/diff-*.json` plus the earlier
`outputs/cert-1fc1c98/`, `outputs/cert-ad91e0e/diff-2b65368-to-final.json`,
`outputs/cert-71dad6e/diff-*.json`, `outputs/cert-final/diff-0b4e207-to-final.json`.

## 3. Local audit, re-executed, machine-readable

Two runs, both at the certified candidate `c52dcb9`, both green, neither a proxy for the other:

| Run | Result | Coverage |
| --- | --- | --- |
| CI `backend` job (Linux, the gate that blocks merges): `pytest backend/tests` with the five container-coupled suites ignored and the SQLite 500-run benchmark deselected | **1386 passed, 132 skipped, 1 deselected, 0 failed** in 19m31s | **91.72%**, gate `--cov-fail-under=90` reached |
| Local superset on the Windows audit host: the same tests *plus* the five ignored suites, which skip without containers | **1389 passed, 147 skipped, 1 deselected, 0 failed** in 25m19s, exit 0 | **91.9%** on 24 361 statements |

The 18-test difference is the container-coupled files CI excludes, skipping locally; §22 accounts
for every skip. The single deselected node in both runs is
`test_phase_28_2_500_benchmark.py::test_500_runs_durable_no_loss_no_duplicate` — the SQLite 500-run
durability benchmark, which serialises past 20 minutes on a contended runner (§22 states where it
does run and what was verified here instead).

Artifacts, all machine-readable and regenerated from the same junit by the committed tool
`python scripts/quality/audit_junit.py <junit> --coverage <coverage.xml>`:

| File | Content |
| --- | --- |
| `outputs/cert-c52dcb9/backend-junit.xml` | every test's outcome and skip reason (local run) |
| `outputs/cert-c52dcb9/coverage.xml` | line data behind the 91.9% |
| `outputs/cert-c52dcb9/skip-audit.json` | 147 skips grouped by reason family (§22) |
| `outputs/cert-c52dcb9/coverage-low-modules.json` | modules and worst files below the threshold (§21) |
| `outputs/cert-c52dcb9/audit-tables.log` | the generated tables as printed |
| `outputs/cert-c52dcb9/linux-artifacts/`, `…/k8s-artifacts/` | the certification jobs' own artifacts, downloaded (13–14) |
| `outputs/cert-becbad4/secret-scan.json`, `diff-*.json` | §20 scan and §2 classifications |

Console: the CI `frontend` job is green at the candidate — `npm run lint -- --max-warnings=0`,
the 29-test Vitest suite, and `npm run build` (`tsc -p tsconfig.app.json && tsc -p
tsconfig.node.json && vite build`) with the largest entry chunk under the 550 kB gate.
Python lint: `ruff check backend/app backend/tests benchmarks/phase22 scripts` clean — `scripts/`
joined the gate in this pass (§23 F-16), and `test_quality_gate_parity.py` now binds the Makefile
targets to the CI steps so the two cannot drift again.

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
(pgserver bundle), **6 passed** — re-executed at the certified `c52dcb9` in 663 s
(`outputs/cert-c52dcb9/migration-junit.xml`; originally `outputs/cert-8a8711f/migration-catalogue.log`):

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
**all three cells PASS** at `0b4e207` and re-executed **all three PASS on the certified
`c52dcb9`** (run `35439343387`, legs `postgres-version-matrix (15/16/17-alpine)`), recording the
exact servers certified
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
`154f5b6` (`outputs/cert-154f5b6/k8s-artifacts/cap-28.6-k8s-certification.json`), re-executed at
`c8c170f` and `d9a2e01`, and PASS on the certified `c52dcb9` (run `35439344924`, §14).

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
  blocks* and GATE 24/25 backup/restore + data-survives-restart, all PASS at `154f5b6`, re-run at
  `c8c170f` and `d9a2e01`, and PASS on the certified `c52dcb9` (K8s run `35439344924`; the Linux
  layer's 12 `object_store` tests execute there under `CAP_CERTIFICATION_STRICT=1`, §13).

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

Sandbox/egress images on the certified candidate
(`outputs/cert-c52dcb9/linux-artifacts/sandbox-images.json`): `cap-sandbox-http`
`sha256:0e2e471f70d9…` (177 467 307 bytes, base `python:3.13.12-slim-bookworm`,
`dockerfile_sha256 b7efc782b3d9…`) and `cap-sandbox-browser`
`sha256:1620a2c56e7d…` (1 542 705 307 bytes, base `cap-sandbox-http:latest`,
`dockerfile_sha256 64122a4ff8de…`), built at `2026-09-19T11:10:30Z` / `11:11:00Z`. Both
`repo_digest` and `base_digest` are empty/null — not because nothing was pushed, but because the
**bases are tags**: `python:3.13-slim` twice in `backend/Dockerfile`, `node:22-alpine` and
`cap-sandbox-http:latest` in `frontend/Dockerfile` / `sandbox-browser/Dockerfile` float across
patch and minor releases, while `nginx:1.30.4-alpine` and `python:3.13.12-slim-bookworm` are
patch-pinned but still tag-referenced. Rebuilding this exact commit next month can therefore
produce a different image with nothing in the lock saying so. That is **F-20 (MEDIUM)**, and the
place to fix it is the existing `deployment/third-party-images.json` +
`test_third_party_image_lock.py`, which already prove a coordinate is pinned where it is used.

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

**PASS on the certified candidate.** K8S-GATE 33 green and 33/33 overall at `c52dcb9` (run
`35439344924`; artifact `outputs/cert-c52dcb9/k8s-artifacts/cap-28.6-k8s-certification.json`,
`commit: c52dcb98c270…`, `gate_summary: {total: 33, passed: 33, failed: 0, not_run: 0}`). Earlier
rounds on the same line: 33/33 at `c8c170f` (run `35429507835`) and again at `d9a2e01` (run
`35434491797`) after the heartbeat fix. Reaching that took four dispatches in the first leg, each
fixing the *gate* (assumed container port 8080 where the Service publishes 80; called
`.strip()` on a `CompletedProcess`), which is worth recording: the routing behaviour itself has
not been contradicted.

## 13. Linux / OCI runtime certification

`cap-linux-certification.yml`, layer `release`: **fast + full + production all green** at
`0b4e207` (run `35423075901`, 17m10s), 12 gates PASS in each collected artifact
(`cert-artifacts-main/`, `cert-artifacts-release/cap-28.5-linux-certification.json`), including
the 500-run OCI correctness benchmark, 100-run kill-9 HA, full adversarial security and the
28.1–28.5 regression. Environment recorded (Ubuntu 24.04.5, kernel 6.17.0-1022-azure, Docker
info, cgroup, nft/iptables rulesets). Re-running on the candidate chain took three dispatches
after that baseline, and each failure was a real finding rather than noise:

- run `35429505106` (`c8c170f`) — `full-certification` crashed in the report generator with
  `ModuleNotFoundError: No module named 'yaml'`: the socket detector imported PyYAML, which the
  certification runners never installed. Fixed at `8ffd7bd` by an indent scanner in the stdlib,
  with a test that re-executes the generator with `sys.modules["yaml"] = None`.
- run `35430453285` (`8ffd7bd`) — `postgres-version-matrix` (15/16/17) and `full-certification`
  passed; `cap-production-certification` failed `Full regression (28.1 - 28.5)` on
  `test_phase_28_3_lease_heartbeat.py::test_long_operation_survives_lease_ttl` with
  `assert 'CANCELLED' == 'COMPLETE'`. The same commit, the same test, passed in the sibling job:
  that is F-14, a healthy long-running acquisition losing its execution lease under runner
  contention. Fixed in the product, not in the assertion.
- run `35431391962` (`0bc8efa`) — regression **193 passed / 0 failed**, 500-run OCI benchmark
  **PASS**, 100-run kill-9 HA **PASS**, matrix **PASS**; the release-layer artifact gate then
  asserted `worker_control_plane_isolation == "NOT_CERTIFIED"`, a verdict word it does not own
  (F-15).

The artifact's `worker_control_plane_isolation` field is discussed in §23 (F-1): it was a
substring grep and is now a structural, per-path fact.

**Green on the certified candidate.** Run `35439343387` at `c52dcb9`, layer `release`:
`full-certification`, `cap-production-certification` and all three `postgres-version-matrix` legs
**success**. The downloaded release-layer artifact
(`outputs/cert-c52dcb9/linux-artifacts/cap-28.5-linux-certification.json`) records:

| Field | Value |
| --- | --- |
| gates | 12/12 **PASS** — container_isolation, filesystem, memory, cpu, pids, network_enforcement, ssrf_defense_in_depth, hard_cancellation, reaper, browser, secrets, real_integration |
| tests | 197 — 193 passed, **0 failed**, 4 skipped |
| `sandbox_workload_isolation` | **PASS** |
| `worker_control_plane_isolation` | **PARTIAL**, with `production_chart_worker_mounts_runtime_socket: false` and `compose_worker_mounts_runtime_socket: true` — the per-path report §23 F-15 requires, and the gate derived it from the generator rather than pinning the word |
| `commit` | `c52dcb98c270…`, compared by the gate against the SHA its own job checked out (§23 F-10) |

The four skips inside each layer's regression are the Playwright-driven browser variants
(`test_phase_28_2_browser_reaping::test_cancel_race_does_not_leak_browser_contexts`,
`test_phase_28_4_browser_isolation::test_browser_runs_inside_sandbox_and_renders`,
`::test_terminate_kills_browser_process_tree`, `::test_repeated_browser_runs_leave_no_orphans`),
which need a local Chromium; the `browser` **gate** is decided by a different, executed proof —
`test_phase_28_5_container_integration.py::test_browser_renders_page_in_isolated_container`, per the
`GATE_TESTS` map in `scripts/certification/generate_report.py` — so the gate is not riding on a skip.
Error-log audit of both jobs' complete logs — `gh run view --job 105887344482 --log`
(`full-certification`, 3375 lines) and `--job 105887344373 --log`
(`cap-production-certification`, 3594 lines): **0 tracebacks**.

Environment captured alongside: `uname.txt`, `os-release.txt`, `docker-version.txt`,
`docker-info.txt`, `resources.txt`, `cgroup.txt`, `ip-route.txt`, `iptables-save.txt`,
`nft-ruleset.txt`, `sandbox-images.json`, `network-inspect.json`, `infra.txt`.
Critical skips: `CAP_CERTIFICATION_STRICT=1` turns an availability skip into a failure in the
certification-critical set, and the release layer's `GA-GATE 40 evidence: critical skip report`
step is the machine-readable check that the count is zero.

## 14. Kubernetes certification

`cap-k8s-certification.yml`: **33/33 gates PASS** at `c8c170f` (run `35429507835`, artifact
`outputs/cert-8ffd7bd/k8s-artifacts/cap-28.6-k8s-certification.json`, which records
`commit: c8c170f…` and `gate_summary: {total: 33, passed: 33, failed: 0, not_run: 0}`); the
32-gate predecessor ran at `154f5b6` — kind v0.27.0 on Kubernetes
`v1.37.0`, 3 nodes, Cilium NetworkPolicy enforcing, PostgreSQL 16 + MinIO in `cap-infra`, all CAP
images imported into every node — with the DR sequence (GATE 24 backup/restore round-trip, GATE 25
data survives restart), HPA/PDB capacity (22), SLI/SLO metrics (23), alerting config (27),
baseline regression (28), RTO (29), resource limits (30), security baseline (31), overall health
no-stale (32), plus the long-run lease renewal pre-gate. The candidate adds GATE 33 → **33 gates**;
`generate_report_28_6.py`'s `ALL_GATES` is the authority and any report saying "1..32" predates it.
Status: **33/33 PASS three times on this line** — at `c8c170f` (run `35429507835`), at `d9a2e01`
after the execution-lease heartbeat fix (run `35434491797`), and at the certified `c52dcb9`
(run `35439344924`, §14 above).

## 15. Gates inherited vs gates re-run

**Re-run and green at the certified `c52dcb9`**

| Gate | Run | Result |
| --- | --- | --- |
| CI: backend unit+coverage, frontend, migration (PostgreSQL 16), packaging, image-and-security | `35439324779` | all five jobs **success** |
| Linux `full-certification` + `cap-production-certification` (12 gates each, incl. the 500-run OCI correctness benchmark, the 100-run kill-9 HA gate, the adversarial security suite and the 28.1–28.5 regression) | `35439343387` | **success**, artifacts in §13 |
| PostgreSQL version matrix 15 / 16 / 17 | same run | **success** ×3 (§6) |
| K8s certification, GATE 1..33 | `35439344924` | **33/33**, 0 not_run (§14) |
| GA reliability soak, 7200 s with chaos | `35439341789` (`reliability-evidence`) | **success**: 480/480 healthy ticks, 480 runs created, 48 cancelled, 1440 pagination requests, **0 HTTP errors**, **0 downtime seconds**, 11 worker pods killed mid-run, RSS samples 176–538 KB with no monotonic growth, upgrade-under-load 6.1 s and rollback-under-load 0.5 s both with 0 errors, 5 tests / 0 failures over 7297 s |
| Local authoritative suite + migration catalogue + secret scan + skip/coverage audit tables | this host | §3, §5, §20, §21, §22 |

**In flight when this was written:** the strict FULL-GA certification dispatched on the certified
SHA through the `cert/1.0.6-rc1` pointer branch (run `35445391334`, `ga_strict=true`). It resolves
its soak evidence by `head_sha`, which is why it runs on the certified commit and not on the
docs-only tip. §25 states what is not yet closed because of it.

**Why the earlier green rounds are history, not this candidate's certificate**

| Round | Superseded by | Because |
| --- | --- | --- |
| Linux + K8s green at `d9a2e01` (runs `35434486047`, `35434491797`) | `3ebca44` | the renewal-cadence contract fix is `production_runtime` (F-14's third cause) |
| CI green at `0bc8efa`, K8s green at `c8c170f` | `d9a2e01` | the heartbeat session-isolation and transient-retry fix is `production_runtime` (F-14) |
| Linux full+production+matrix green at `0b4e207` | the whole chain | `v1.0.5 → c52dcb9` carries 51 production-runtime files, 1 database, 2 dependency, 3 deployment (§2) |
| K8s 32-gate round at `154f5b6` | GATE 33 added | kept as history: the gate set grew and the console-reads defect (F-13) was found by it |
| soak at `ad91e0e` | cancelled deliberately | a 2 h soak of a superseded commit produces evidence nobody may cite; the run was cancelled rather than allowed to finish and be reinterpreted (§23 F-8 explains why pushes no longer cancel runs like this) |

`c52dcb9 → b9b7f03` (audit-tool file rows) is the only delta after the certified SHA and it
classifies **INHERITED** — one `repo_tooling` file, one `test_harness` file, no product code.

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
after upgrade, and GA-GATE 34 performing a Helm upgrade **during sustained load** inside the soak
— measured on the certified candidate at the 2400 s mark of the 7200 s window: upgrade
**6.1 s** with 0 HTTP errors, rollback at the 4800 s mark from revision 2 **0.5 s** with 0 HTTP
errors, against a live claim/execute loop (`ga-dr/soak-context.json`, `transitions`).
`deployment/helm/cap/values.schema.json` rejects a malformed override before it reaches a cluster.

## 18. Rollback — what is actually true

- **Schema**: identical (`20260812_0021` at v1.0.0, v1.0.5 and the candidate), so
  `helm rollback` to a v1.0.5 release needs no migration and no data repair. Stated as a fact
  with its evidence rather than assumed.
- **What will not roll back cleanly**: a workflow instance parked at the new approval gate.
  `1.0.6` writes `WAITING` steps with `context.approvals`; at v1.0.5 the approval handler is the
  Phase 3 placeholder (`git show v1.0.5:backend/app/workflow/nodes.py`, `ApprovalNodeHandler`
  returning `WAITING` with `{"reason": "Approval provider is not implemented in Phase 3"}`) and
  resume re-parks every `WAITING` step back to `PENDING` (`git show
  v1.0.5:backend/app/workflow/runtime.py`, the `elif instance.status == WAITING` branch), so the
  run is **stranded, not silently approved** — it cannot fail open, but an operator must list open
  `WAITING` approval steps before deciding to roll back, and after rolling forward again they will
  be re-presented for a decision rather than auto-completed.
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
green on the certified `c52dcb9` (CI run `35439324779`, all five jobs). SBOM and provenance
attestations are produced by `release.yml`'s buildx on publish (`sbom: true`, `provenance: true`)
with `VERSION`/`REVISION` build-args; a candidate that is not published therefore has **no registry
attestation**, and this report says so rather than presenting an image ID as one. The GA workflow's
supply-chain job adds
`syft` SBOMs (cyclonedx + spdx) and trivy JSON per image, pinned; those artifacts from earlier runs
live under `outputs/cap-cert-ga/` and are labelled by the commit they describe.
`test_phase_28_7_ga_tier2_supply_chain.py` asserts the toolchain and skips (→ fails under
strict mode) when `syft`/`trivy` are absent — which is exactly why the count appears in §22.

## 20. Secret scan, executed

`scripts/quality/scan_secrets.py` — stdlib-only, so no vendor download can break the gate that
catches credentials (this line's tooling has already been bitten by one). Scanned **1040 tracked
files, 0 findings** at the certified `c52dcb9`
(`outputs/cert-c52dcb9/secret-scan.json`; 1034 at `154f5b6`,
`outputs/cert-154f5b6/secret-scan.json` — the growth is this pass's own tooling and tests).
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

**91.9%** line coverage on **24 361 statements** in the local run at the candidate, **91.72%** in
CI's unit job on the same commit (`outputs/cert-c52dcb9/coverage.xml`; CI gates at 90%). It is the
*unit job's* number: no certification workflow passes `--cov`, so lines that only execute inside a
container are
in no denominator anywhere. `outputs/cert-c52dcb9/coverage-low-modules.json` — generated by
`scripts/quality/audit_junit.py`, not assembled by hand — reports 3 module directories below 80%
(`database` 75.9%, `assessment` 76.0%, `incident` 77.4%) and 14 below 90%. The directory numbers
average away the real gaps, so the file table is the part to read:

| File | Local coverage | Executed by |
| --- | --- | --- |
| `acquisition/reconcile_cli.py` | 0% (0/34) | GA reconciliation gates; no unit run touches it |
| `acquisition/metrics_server.py` | 0% (0/27) | the scrape endpoint the K8s SLI gate (23) queries |
| `core/protocols.py` | 0% (0/7) | protocol-only declarations |
| `acquisition/sandboxed_browser.py` | 17% (11/64) | Linux certification browser gate |
| `acquisition/worker_main.py` | 21% (39/190) | the worker process the Linux/K8s/GA runs start |
| `acquisition/gc.py` | 23% (23/102) | GA DR + orphan-gc certification gates |
| `sandbox/oci_shim.py` | 32% (54/168) | the OCI sandbox provider, Linux layer only |
| `database/session.py` | 50% (7/14) | engine construction paths |

That is the honest shape of the number: the product's unit-tested core is well covered, and what
sits below the line is almost entirely code that only runs *inside the infrastructure* the
certification jobs provide — which is why those jobs exist and why §13–§14, not §21, is the
evidence that they work. No test was added to flatter this figure; the tests added in this pass
found four real defects (§4, §10, §23 F-14/F-15/F-17) and one whose correct answer was to refuse a
test (§9).

## 22. Skipped tests, by category, and where each actually runs

147 skips in the candidate's local run, grouped by what each skip message *says*
(`outputs/cert-c52dcb9/skip-audit.json`, produced by `scripts/quality/audit_junit.py`; every family
is an availability gate, no skip is a parked failure):

| Family | Count | Where it executes for real |
| --- | --- | --- |
| kubernetes (no kind cluster) | 56 | K8s certification (33/33, §14) and GA certification |
| postgres (needs a real server) | 35 | CI `migration` job on PostgreSQL 16, the PG matrix on 15/16/17 (§6), the GA job with `CAP_PG_TEST_DSN` (§23 F-17) — plus the catalogue and this pass's round-trip checks run locally against pgserver 16.2 (§5) |
| OCI / container runtime | 21 | Linux certification (§13) |
| object store (S3/MinIO) | 12 | Linux and GA certification against the digest-pinned Quay MinIO (§10) |
| browser / Playwright | 10 | Linux certification's browser gate (§13) |
| other | 13 | individually listed with reasons in the JSON |

The same file names the **security-relevant tests that did not run locally** (process isolation,
browser isolation, fault injection, `test_phase_28_5_linux_secrets`) — decided by the test's own
module name, not by the infrastructure excuse in its skip message, because a security test that
skipped for want of a container is still a security test that did not run. All of them execute in
the Linux certification layer, which is green at the candidate under
`CAP_CERTIFICATION_STRICT=1`, where an availability skip in the critical set is a **failure**
rather than a skip — the release layer's `GA-GATE 40 evidence: critical skip report` step is the
machine-readable check that the count is zero (§13).

**The one deselected node** (release-audit §12 asks where a deselected test runs):
`test_phase_28_2_500_benchmark.py::test_500_runs_durable_no_loss_no_duplicate` — 500 durable
acquisitions against SQLite. It is deselected in CI's unit job and in both Linux certification layers
because SQLite's single writer serialises it past 20 minutes there. Its homes are `make test` and the
per-release local audit; on this box it exceeded the file's own 1200 s marker under no concurrent
load, so this pass reports it as **not executed** rather than passing it off as covered, and relies
on the durability evidence that *is* release-blocking and *did* run on the candidate: the 500-run
**OCI/PostgreSQL** correctness benchmark in the Linux release layer (§13), plus the 100-run kill-9 HA
gate. `test_quality_gate_parity.py` now fails CI if an ignored suite or a deselected node loses both
its documentation and a home that collects it.

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

- F-14 **A healthy long-running acquisition could be cancelled.** Run `35430453285`
  (`cap-production-certification`, `assert 'CANCELLED' == 'COMPLETE'`) while the *same* commit
  passed the same test in `full-certification` — contention, not chance. Two compounding causes
  in `WorkerRuntime`: (1) the execution-lease heartbeat ran as a task sharing the runtime's
  `AsyncSession` with the main execute flow at every construction site that omitted
  `heartbeat_session_factory`, and the architecture scan that was supposed to prevent that
  exempted `tests/` outright — the Phase 28.3 suite was exactly such a site; (2) any renewal
  failure other than a real ownership loss (SQLite's single-writer `database is locked` is the
  common one) killed the heartbeat coroutine silently, so renewals stopped for the rest of the
  operation, the lease lapsed unrenewed, the fenced commit was *correctly* rejected, and the run
  was finalised CANCELLED. Fixed in the product: the runtime now derives a renewal-only session
  factory from its own bind when a site omits one (no site can share the main session, in `app/`
  or `tests/`), and a transient renewal failure is retried on the next tick while
  `WorkerLeaseConflict` still stops the heartbeat immediately — fencing is untouched. Pinned by
  `test_phase_28_7_ga_heartbeat_invariant.py` (isolation by construction, deterministic
  transient-retry/ownership-loss split, and an `explicit None` scan across both source trees);
  both new tests were verified to fail against the pre-fix code. Those two fixes are certified
  green at `d9a2e01`: Linux full + production + all three matrix legs (run `35434486047`) and K8s
  33/33 (run `35434491797`).
  A third cause was found by measuring what margin was left rather than by a failure: both renewal
  cadences were written `max(1.0, lease_ttl / 3)`, whose 1 s floor silently breaks the documented
  "renew three times per lease" contract for any TTL under ~3 s — exactly the regime the
  certification harnesses run in — leaving a healthy 3.5 s operation with a 1.33 s stall budget on
  a runner that had just executed 500 containers. The cadence is now one named function both call
  sites share, `ttl / 3` and never slower; at the production TTL of 120 s it yields the same 40 s
  as before, so no deployed behaviour changes. That edit is production-runtime code, so the final
  round below re-certifies it.

- F-15 **A release gate asserted a verdict word it did not own.** After F-1's detector started
  reading `docker-compose.yml` structurally, the truthful release-layer value became `PARTIAL`
  (chart isolated, compose mounted), but `cap-linux-certification.yml` still hard-coded
  `== "NOT_CERTIFIED"`, so run `35431391962` failed *after* 193 regression tests, the 500-run
  benchmark, the 100-run HA gate and all three matrix legs had passed. The gate now re-derives
  the per-path facts from `scripts/certification/generate_report.py` at assertion time, forbids
  `PASS` while any shipped deployment path holds a socket, and treats a chart socket mount as a
  release blocker. Two additions make the class impossible to reintroduce: a contract test that
  fails any workflow step pinning an isolation verdict word, and one that **executes** that
  inline gate step against truthful, stale and chart-regression artifacts — inline workflow
  Python that no test had ever run before.

- F-17 **An "authoritative PostgreSQL" gate could never reach PostgreSQL.**
  `test_heartbeat_renewal_isolation_postgres_authoritative` took its DSN from
  `DATABASE_URL` — the very variable `backend/tests/conftest.py` pins to in-memory SQLite for
  every test process (§4's hermeticity fix). In-memory SQLite plus `NullPool` means the
  `create_all` connection and the session's connection are two different empty databases, so the
  variant died on `sqlite3.OperationalError: no such table: workers` the first time the strict GA
  job got far enough to execute it (run `35429972509`, after the MinIO outage stopped blocking the
  workflow). Had it merely skipped, the GA artifact would have reported PostgreSQL row-locking as
  certified on the strength of a SQLite run. It now reads a dedicated, shape-checked
  `CAP_PG_TEST_DSN` (wired into both GA workflows), fails loudly when `CAP_PG_TEST=1` is set
  without a real server, and `test_settings_dotenv_hermeticity.py` forbids the `DATABASE_URL`-as-
  server read pattern across `tests/` with a positive control. Verified both ways against a real
  PostgreSQL 16.2 on this machine: the variant passes, and removing the DSN produces the
  actionable failure instead of a wrong-backend mystery.

- F-18 **The F-14 fix introduced a regression, and CI caught it.** Deriving a renewal-only
  session factory from the runtime's bind (acb170d) is correct wherever the bind can hand out a
  second connection, and wrong where it cannot: conftest's in-memory engine uses `StaticPool`
  precisely so one connection survives checkouts, so the "dedicated" session was the *same*
  connection the crawl was writing on, and its `COMMIT` landed mid-transaction. CI run
  `35436793797` failed
  `test_phase_28_2_legacy_architecture.py::test_worker_path_executes_claimed_run` with
  `WorkerExecutionError: (sqlite3.OperationalError) cannot commit transaction - SQL statements in
  progress`. Fixed at `c52dcb9` by asking one question in one helper
  (`bind_serves_one_connection`) used by both places that used to fork a second session — the
  runtime's heartbeat and the acquisition poll/renewal — and on a one-connection bind renewing
  through the operation's own session / reading through it instead, because there is no second
  connection to hold a durable renewal on. The same commit anchored the acquisition heartbeat's
  first renewal on the loop clock: `last_renew` started at `0.0` while `loop.time()` is a monotonic
  system clock, so the very first poll iteration always renewed, which is what opened the window.
  Multi-connection behaviour — every deployment, plus the certification harnesses that build
  NullPool file engines — is untouched. This one did not reproduce on the audit host in five
  attempts (it needs the exact microsecond where the crawl holds a statement open), so the fix is
  verified by the green CI unit job at `c52dcb9`, not by a local pass.

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
- F-19 The single-connection bind still has two rough edges, both inert in deployment. (a) The
  acquisition heartbeat **skips** its lease renewal there (§23 F-18's guard refuses to commit on the
  connection the crawl is writing on), so a run longer than the TTL in that configuration could still
  be reclaimed. (b) `AcquisitionClaimCoordinator._reject_stale` still persists its rejection counter
  through an isolated session, and on a one-connection bind that `COMMIT` lands inside the *caller's*
  transaction — the exact thing its own docstring forbids, though the counter is observational and
  the fencing rejection itself (`verify_owner` raising first) is unaffected. Neither path is reachable
  where it matters: `ACQ_LEASE_TTL_SECONDS` and the asyncpg/file-SQLite engines behind it always
  provide a pool, and `test_phase_28_2_claim_fencing.py` builds a file-backed engine specifically "so
  each worker session gets its own DB connection", which is where the stale-commit invariant is
  actually proven. Closing both properly means either giving the renewal a connection from a
  separate pool or refusing to run at all on a bind that cannot honour the lease contract — a
  behaviour change worth its own certification cycle, not a late edit to a candidate.
- F-20 Base images are referenced by tag (§11): `python:3.13-slim` (twice, `backend/Dockerfile`),
  `node:22-alpine` (`frontend/Dockerfile`), `cap-sandbox-http:latest` (`sandbox-browser`), plus the
  patch-pinned-but-still-tagged `nginx:1.30.4-alpine` and `python:3.13.12-slim-bookworm`. So a
  rebuild of this commit later can differ with no record, and the certified artifact's
  `base_digest` is null for exactly that reason. The fix belongs in the lock that already exists —
  add each base to `deployment/third-party-images.json` with its manifest-list digest and let
  `test_third_party_image_lock.py` police every `FROM` line the way it polices MinIO; pinning the
  `FROM` lines themselves without being able to build here would trade a documented gap for an
  unverified one.

**LOW**
- F-16 `scripts/` — the code that *generates* the release certification artifact and the quality
  scanners — was outside the declared lint gate in both CI and the Makefile, and carried two
  ruff findings (unparenthesized implicit concatenation inside a list, the shape that hides a
  missing comma) that nobody could see. Both paths now lint it, and
  `test_quality_gate_parity.py` binds `make lint`/`make check` to the CI steps and requires
  every CI-`--ignore`d suite and CI-`--deselect`ed node to have a home that actually collects it
  (the §12 question, made permanent).
- F-10 **resolved here** The Linux certification JSON carried no `commit` field, so a Linux PASS
  could only be bound to a SHA through Actions run metadata (the K8s and GA artifacts already
  recorded theirs). The artifact now records the SHA — `GITHUB_SHA` on a runner, `git rev-parse
  HEAD` locally, `"unknown"` if neither answers — and the release gate refuses an artifact whose
  recorded commit is missing, `"unknown"`, or different from the commit its own job checked out.
  Both refusals are covered by the executed-gate test (§23 F-15).
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

Evidence assembled on the certified `c52dcb9` (and inheritable deltas to the current tip):

- CI's five jobs green on the certified SHA (`35439324779`) and again on the tip: backend unit +
  coverage, frontend (lint/vitest/typecheck/build), migration on PostgreSQL 16, packaging,
  image-and-security with Trivy and pinned actions;
- local backend suite **1389 passed / 147 skipped / 1 deselected / 0 failed** at 91.9% (CI's own
  unit job: 1386 passed, 0 failed, 91.72%), with the skip and coverage tables generated by
  `scripts/quality/audit_junit.py` rather than transcribed (§3, §21, §22);
- credential scan 1040 tracked files, 0 findings, with planted-credential and reference-shaped
  controls both directions (§20);
- migration chain certified on a live PostgreSQL 16.2 — head, object catalogue, no invalid
  indexes, models-vs-schema equality, and a populated `downgrade → upgrade` cycle — and on
  PostgreSQL 15.19 / 16.15 / 17.11 in CI (§5, §6);
- Linux `full-certification` + `cap-production-certification`: 12/12 gates PASS in each collected
  artifact, 193 passed / 0 failed regression, the 500-run OCI correctness benchmark, the 100-run
  kill-9 HA gate, the adversarial security suite, and an artifact that names the SHA it certified
  and is checked against it (§13);
- Kubernetes 33/33 with DR, object-store outage gating, capacity/SLI gates and the console-route
  gate that found F-13 (§12, §14);
- four product defects found by this pass and fixed at the cause, each with tests confirmed to fail
  before the fix: the acquisition false-cancellation (F-14, including the regression its own first
  fix introduced, F-18), the release gate pinning a verdict it does not own (F-15), the
  "authoritative PostgreSQL" gate that could not reach PostgreSQL (F-17) and the unbound Linux
  artifact (F-10).

**Still open when this was written:** the 7200 s reliability soak on the certified SHA (run
`35439341789`, **in progress**), and the strict FULL-GA certification that must be dispatched on
that SHA after it — GA resolves its soak evidence by `head_sha`, so a soak of an earlier commit is
not evidence for this one. Neither result is asserted here as passing. **The §23 MEDIUM items
(F-4…F-9, F-19, F-20) remain open**, each recorded with its evidence and the reason it is not
fixed inside a release candidate.

**CAP v1.0.6-rc1 RELEASE BLOCKED — publication requires explicit authorization, and the soak plus
the strict GA certification are not yet green on the candidate.**

The verdict becomes:

> CAP v1.0.6-rc1 RELEASE READY — awaiting explicit publication authorization.

only when every run above is green on the certified SHA (or on a later tip whose delta the
classifier reports as inheritable, with that classification stored beside it), and each MEDIUM
finding is either fixed or explicitly accepted with an owner.

Until then: **do not tag, do not publish a Release, do not push an image, do not overwrite any
existing tag.** Publication is a separate, explicit act.
