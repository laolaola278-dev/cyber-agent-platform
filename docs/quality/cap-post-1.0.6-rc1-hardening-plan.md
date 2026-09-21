# CAP POST-RC HARDENING PLAN (post-v1.0.6-rc1)

Status: **ADOPTED FOR EXECUTION.** Reviewed against the tree at `4d8f9c7`; §R records what the
incoming draft asserted wrongly and where each claim here comes from. Every `path:line` was read off
that tree. Batch 1 -- the CI-only items -- is being executed in staged, separately reviewable
commits; the `deployment/` items wait for a round that re-certifies anyway.

Sealed release: `VERSION=1.0.6-rc1`, tag `v1.0.6-rc1`
(`d0c7c7dc14cb075c095bd7970650b180a73afbf1`) → `4d8f9c72b72dcea74e8588374fe8ef3a04564eb6`, release run
`35553750674`, **PUBLICATION CLOSED**. No item below moves, deletes, recreates or rebuilds that tag;
none overwrites a published `1.0.6-rc1` image tag, edits a published Release or its assets, or promotes
the RC to `latest`. `v1.0.5` stays the stable release until a separate stable-release process changes
it. F-25 is closed by run `35553750674` and stays closed although the shipped `known-issues` asset
describes the state at its own creation time — item H writes that history down without touching it.

Certification arithmetic used throughout, measured on this line rather than estimated: CI ≈ 15–20 min,
Linux release layer ≈ 30 min, Kubernetes ≈ 25 min, reliability soak 2 h 10 min, strict FULL GA ≈ 57 min.
A change `scripts/release/classify_diff.py` calls runtime-affecting therefore costs ≈ 3.5 h of serial
re-certification (soak and its paired strict GA at one SHA); a `ci_workflow`/`test_harness`/`docs` change
costs a CI cycle. The classifier is the arbiter and this plan does not propose editing it — every
"this is cheap / this is expensive" claim below is its output, not an opinion.

---

## R. Review corrections to the incoming draft

| # | Draft said | Tree says |
| --- | --- | --- |
| R1 | `builder-id` unset ⇒ SLSA v1 `runDetails.builder.id` is `""` for current builds, so adding the attribute is the fix | Docker's SLSA definitions state that **inside GitHub Actions BuildKit already records the action run URL as the builder id**, and that `builder-id` is a user-settable override. The premise must be checked against the sealed images' attestations (V1) before anything is "fixed"; the durable gaps are that *nothing asserts* the identity and that buildx provenance is self-certified, not a GitHub-registered (Sigstore-signed) attestation. |
| R2 | "`apt-get upgrade`/`apt-get install` (all five images)" | apt appears in `backend/Dockerfile:37`, `backend/docker/sandbox-http/Dockerfile:29`, `backend/docker/egress-proxy/Dockerfile:13`; `frontend/Dockerfile:22` uses `apk upgrade --no-cache`; the **browser image's nondeterminism is elsewhere** — `pip install playwright==1.49.1` then `python -m playwright install --with-deps chromium` (`backend/docker/sandbox-browser/Dockerfile:30`). |
| R3 | "`# syntax=docker/dockerfile:1` floats" (implying all images) | Only two Dockerfiles carry the directive (`backend/Dockerfile:1`, `frontend/Dockerfile:1`); the three sandbox/proxy files do not, so that pin is a two-file change plus a separate decision for the others. |
| R4 | lock entries carry `resolved_on`, `method`, `evidence` | Entry keys are `name, role, registry, repository, tag, digest, digest_kind, image_ref, referenced_by, provenance, derived_by, previous_ref, previous_ref_status`, with `provenance = {verified_on, repository_namespace, image_config_labels, image_config_created, vendor_signed_tag, open_gap, evidence?}`. `evidence` exists on **exactly the five base entries** (`deployment/third-party-images.json:99,128,154,183,207`) and on neither `minio-object-store` nor `postgres` — so a "pointers must resolve" test must first decide what absence means. |
| R5 | 24-hour soak as a duration change | `cap-ga-reliability.yml:45` sets `timeout-minutes: 300` on a 6-hour hosted-job ceiling, and `:40` defaults `CAP_SOAK_SECONDS` to 7200 with `:14` a nightly 18:00 UTC cron. A 24 h soak is a **workflow-shape change (legs)**, not a number edit. |
| R6 | F-41 fix is "drop `tag` from required", tests as an afterthought | `cap.imageRef` (`deployment/helm/cap/templates/_helpers.tpl`) already prefers `digest` and falls back to `tag`/`Chart.AppVersion`, so digest-only *renders* today; the schema alone blocks it (`backend.image`, `worker.image`: `required: [repository, tag]`, tag pattern rejecting empty, no `anyOf`; the other four: `required: [repository]` + `anyOf`, `values.schema.json:180,222,305,410`). And the repo's newest checker **asserts the asymmetry on purpose** (`test_the_released_values_satisfy_the_chart_schema`), so flipping that assertion is part of the change. |
| R7 | trailing `- Priority: P1. Target: next RC.` with no owning section | Removed. F-41 carries its own priority/target; the two missing task items (24 h soak, F-25 closure record) are here as F and H. |

---

## A. F-33 — publication gate cannot distinguish strict GA from development GA — P0

- **Issue.** The gate that decides whether a tag may publish cannot tell a final-strict GA round from a
  development-mode one.
- **Root cause / current limitation.** `release.yml`'s `verify-certification` job (the step
  "Require green certification for this commit or an inheritable ancestor") accepts a run of
  `cap-ga-certification.yml` whose job set contains `ga-certification` + `supply-chain` and whose
  conclusion is success. Development mode exits 0 with `PLANNED` gates, and the Actions API returns no
  `inputs` field for a run at all, so dispatch strictness is invisible. The authoritative indicator sits
  one hop away: `scripts/certification/generate_report_28_7.py:411` computes
  `full_ga_certified = final_strict and passed == len(ALL_GATES) and failed == 0`, published into
  `cap-cert-ga/cap-28.7-ga-certification.json` (`:455`) beside `mode`, `commit` and `gate_summary`, and
  that generator already rejects stale evidence from another commit (`:315`).
- **Proposed remediation.** After the gate picks a GA run, fetch that run's `ga-cert-artifacts` (the job
  holds `actions: read`, `release.yml:20`) and require `mode == "final-strict"`,
  `full_ga_certified is true`, `commit ==` the run's `head_sha`, and
  `gate_summary.{failed,not_run,skipped,planned} == 0`. Fetch/parse failure fails closed and the reason
  lands in the gate's own evidence JSON, so a retry is explainable. Apply the same read to Kubernetes:
  require `cap-28.6-k8s-certification.json` to show `failed == 0`, `not_run == 0` and a matching
  `commit` — a 34/34-colour run that published `not_observed` (F-31's shape) must not satisfy the job
  colour rule. The gate stays a *reader* of the round's decision: it must not recompute
  `full_ga_certified` from junit, so exactly one authority owns that number.
- **Files likely affected.** `.github/workflows/release.yml` (the verify block);
  `backend/tests/test_release_publication_gate.py`; `docs/known-issues.md` item 5; a closure note.
- **Runtime impact:** none — `.github/` is `ci_workflow`, the tests are `test_harness`.
- **Certification impact:** INHERITED; CI only. It *tightens* a gate, so review must ask how a future
  candidate could pass it by accident; the negatives below answer that.
- **Validation strategy.** Executed negatives in the style the completeness gate already uses:
  development artifact (`mode: development`, `planned: 5`) → FAIL; strict artifact whose `commit` ≠ head
  → FAIL; artifact absent → FAIL; strict, matching, 40/40 → PASS. Then a dry run of the extracted
  resolver against the sealed line (GA run `35512844679`) to prove real shipped evidence satisfies the
  new rule — read-only.
- **Priority / target:** **P0, first CI-only commit** — ahead of any new publication, because it is the
  gate that says "this was certified strictly".
- **Migration / compatibility risk:** none for published artifacts; future publications must be backed
  by strict rounds.
- **As executed (batch 1, stage 1).** The gate now holds an `AUTHORITY` table keyed by workflow: artifact
  name, verdict-file name, the commit field to bind, the fields to match exactly, the gate counts that
  must be zero, and `passed_all_gates`. Structural problems (no such artifact, two of them, an expired
  one, a zip with no single verdict file, unreadable JSON) raise `AuthorityRejected` and are reported as
  FAIL reasons; a download the API refuses stays `RuntimeError` and is reported as ERROR, because
  "could not look" and "it says no" need different remedies. The stored flag is read type-strictly (an
  `1` or `"true"` is not the `True` the round recorded), and no count is recomputed from junit.
  `passed == total` replaced a hardcoded 40/34 on purpose: legitimate older rounds recorded 32/32 and
  33/33, and a fixed number would refuse certification it should inherit. The evidence file carries the
  table it applied and, per workflow, the artifact id, digest, member path and the fields it read.
  Verification: 32 tests in the gate's module -- 30 executed green here, the two live
  Actions-API shape checks skipping without a credential -- plus nine single-guard
  mutations with each one caught by its own test and
  no others, and a read-only pass of 13 real artifacts from sealed and superseded rounds -- every one
  read the way the round that produced it recorded, including the development-mode soak at the strict
  round's own SHA and three strict-but-incomplete rounds.

## B. Provenance builder identity — P0 verification, P1 implementation

- **Issue.** Published attestations are not asserted anywhere and are not cryptographically bound to
  the workflow identity that produced them.
- **Root cause / current limitation.** Release builds run
  `docker buildx build --provenance=true --sbom=true --push`
  (`scripts/release/build_release_image.sh:137`); `release-image-completeness` requires only that the
  two `attestations` booleans are true (its `no {kind} attestation` refusal) — presence, not content.
  Nothing records *which* builder produced a published artifact, and buildx-written provenance is
  self-certified inside the job rather than registered as a GitHub attestation.
- **Proposed remediation, in this order.**
  1. **V1 — verify before changing.** Fetch the sealed images' attestation manifests by digest
     (read-only registry GET with a package-scoped token) and record what `runDetails.builder.id`,
     `buildType` and the invocation fields actually contain. Per R1, Actions builds are documented to
     carry the run URL already; if that holds, the fix is assertion (step 3), not a new flag value.
  2. **Pin the builder surface.** `docker/setup-buildx-action@v3` installs a default buildx and its
     `docker-container` builder pulls an unpinned BuildKit image — pin both. This is also what makes
     "same builder" a meaningful phrase in G.
  3. **Assert identity in evidence, then in the gate.** Have the build script record
     `attestations.builder_id` **read back from the pushed manifest** (the same rule that fixed
     `attestations`/`platform`: read it, never restate the flags), and let completeness require it to be
     a `github.com/<owner>/<repo>/actions/runs/<id>` URL equal to the publishing run.
  4. **Then, optionally:** `actions/attest-build-provenance` per image (`attestations: write`,
     `id-token: write`) so the subject digest is bound to a Sigstore-signed statement issued over OIDC
     and verifiable with `gh attestation verify`. Keep buildx provenance alongside — they answer
     different questions, and this is the step that makes the binding machine-checkable by a third party.
- **Files likely affected.** `scripts/release/build_release_image.sh`; `.github/workflows/release.yml`,
  `ci.yml`; `backend/tests/test_release_build_script.py`, `test_release_image_completeness.py`; later
  `deployment/helm/cap/README.md` and the production checklist.
- **Runtime impact:** none (tooling/CI). **Certification impact:** INHERITED, CI only — unless a
  Dockerfile is touched, which is G's territory.
- **Validation strategy:** V1's output tracked as an evidence file so the claim is inspectable; unit
  assertions on the read-back parse; live verification executed on the **next** RC's scratch push, never
  against sealed tags; a gate negative (builder id absent or foreign → refuse).
- **Priority / target:** P0 for V1 + pinning, P1 for assertion and the attest step; **next RC**. Risk: a
  new step in `release.yml` can fail a *future* publication rather than a sealed one, which is the
  acceptable direction, and CI's dry-build path should cover as much of it as it can.
  **As executed (batch 1, stage 2):** V1 was attempted and could not be answered — no `gh`, no
  credential, anonymous ghcr reads 404 — so steps 3 and 4 were not started and no builder-id pattern
  was invented. The probes, and what the repository's own evidence does and does not record, are in
  `docs/quality/cap-provenance-identity-observation-2026-09-21.md`.

## C. F-24 — compose third-party images by mutable tag — P1

- **Issue.** A compose deployment can receive different bytes for the same tag on different days.
- **Root cause / current limitation.** `docker-compose.yml` pulls `postgres:16-alpine` (:10),
  `redis:7-alpine` (:27), `prom/prometheus:v2.55.1` (:231), `grafana/grafana:11.3.1` (:247),
  `dpage/pgadmin4:8` (:266); only MinIO is digest-pinned (:101). The lock's declared scope is Dockerfile
  `FROM` plus the two binaries, so nothing polices these five.
- **Proposed remediation.** Bring compose inside the lock's scope using the **existing** field names:
  five new `images[]` entries (`image_ref`, `digest`, `digest_kind`, `referenced_by`,
  `provenance.{verified_on, repository_namespace, vendor_signed_tag, evidence}`), rewrite compose refs to
  `repository@sha256:…` with the human-readable tag kept in the lock (and a comment where compose allows
  it), then extend `backend/tests/test_third_party_image_lock.py` to treat `docker-compose.yml` as a
  reference site: every external compose image must be digest-pinned **and** present in the lock, each
  direction with its own negative control. The manifest-list-vs-platform-child reasoning already written
  for the bases applies to `postgres`/`redis` too, and `updated` must move with `verified_on`.
- **Files likely affected.** `docker-compose.yml`, `deployment/third-party-images.json`,
  `backend/tests/test_third_party_image_lock.py`, compose/deployment docs.
- **Runtime impact:** both files are `deployment/`-category ⇒ runtime-affecting ⇒ full re-certification.
  Compose is the compose path; the chart and its gates are untouched by the edit itself.
- **Certification impact:** invalidates inheritance ⇒ CI + Linux + K8s + soak + strict GA at one SHA —
  so it should **share** that round with D and E3 rather than pay for it alone.
- **Validation strategy:** the extended lock test on a fresh clone; `docker compose config --quiet`
  (already the packaging job's step, `ci.yml:196`) plus a digest-presence assertion; one Linux
  `docker compose up` smoke where an engine exists (not on this audit host).
- **Priority / target:** P1, **next RC**, bundled. Migration risk: low in effect (same images, same
  versions) but a pinned digest fails closed when a vendor retires a manifest — intended, and the
  refresh procedure must be written down with it.

## D. F-41 — chart schema blocks digest-only pinning for two of six coordinates — P1

- **Issue.** `cap.imageRef` advertises digest pinning as the strongest form, and the schema refuses it
  for the API and the acquisition worker.
- **Root cause (verified).** `deployment/helm/cap/values.schema.json`:
  `backend.image` and `worker.image` are `required: [repository, tag]` with a tag pattern
  (`^[A-Za-z0-9][A-Za-z0-9._-]*$`) that rejects the empty string and **no** `anyOf`;
  `frontend.image`, `egressProxy.image`, `worker.sandbox.image`, `worker.sandbox.browserImage` are
  `required: [repository]` + `anyOf` tag-or-digest (`:180,222,305,410`). Rendering already supports
  digest-only, so the schema is the entire gap.
- **Proposed remediation.** Widen the two strict coordinates to the shared shape
  (`required: [repository]` + the same two-branch `anyOf`), keeping each property's `pattern`/
  `minLength` so `tag: ""` and `digest: ""` stay illegal. This is strictly a widening: every existing
  values file sets a tag, so nothing currently valid becomes invalid.
- **Tests to add or change.** Flip the asymmetry control in
  `backend/tests/test_release_image_completeness.py::test_the_released_values_satisfy_the_chart_schema`
  into a uniformity assertion (all six accept digest-only; all six refuse neither); if the schema grows a
  keyword, `SCHEMA_KEYWORDS` in that module must grow with it — that check fails loudly on purpose; add a
  `helm template --set backend.image.digest=…` (no tag) case to the chart step in `ci.yml` so helm itself
  executes the claim.
- **Files likely affected.** `deployment/helm/cap/values.schema.json`,
  `backend/tests/test_release_image_completeness.py`, `deployment/helm/cap/README.md`, production
  checklist.
- **Runtime impact:** `deployment/` ⇒ runtime-affecting ⇒ recert (same round as C).
- **Validation:** the coordinate matrix above; `helm lint`/`helm template` in CI; the K8s round renders
  the chart unchanged with tags, proving backward compatibility rather than asserting it.
- **Priority / target:** P1, next RC with C. Migration risk: low — the only breakage would be an external
  tool that *assumed* `tag` presence when reading chart values.

## E. F-37 — tracked evidence pointers into gitignored `outputs/` — P1

- **Issue.** A tracked file can cite proof that no fresh clone contains.
- **Root cause.** Five `provenance.evidence` values in `deployment/third-party-images.json` name
  `outputs/artifact-closure/registry-base-digests.json`, and `outputs/` is gitignored
  (`.gitignore:67-68`); no test looks, so CI stayed silent. The closure's captures were moved to the
  tracked `docs/quality/artifacts/cap-1.0.6-rc1-artifact-closure/`, and the measurement itself came from
  an ad-hoc HTTPS method with no checked-in generator.
- **Proposed remediation, ordered so the cheap half does not wait for the expensive half.**
  1. `backend/tests/test_third_party_image_lock.py`: every `provenance.evidence` path must exist **and**
     be git-tracked; an entry without one must carry an explicit reason field (R4: two entries are
     silent today). `test_harness` ⇒ inheritable, lands immediately.
     **As executed (batch 1, stage 3) — differently, and necessarily so.** Requiring the *cited* path to
     be tracked is red until step 3 rewrites it, so a CI-only batch cannot contain both halves as
     written. The guard that landed demands what a fresh clone must actually be able to resolve: for
     every cited pointer, a tracked twin under `docs/quality/artifacts/` that asserts the same digests,
     with the dates and formatting of the two copies explicitly not the claim (the tree's two copies
     differ in exactly one date field today). An entry that names no pointer must justify that by named
     field, and the allowed set is compared against the lock's own entries so the convention cannot
     widen by silence. Five controls with the real data, each noticed. Step 2 (a tracked generator
     under `scripts/release/`) was not executed in this stage.
  2. Add a tracked generator under `scripts/release/` (`certification_generator` ⇒ inheritable) that
     performs the documented registry method, writes to a tracked path, and records a machine-readable
     `verified_on` — the pattern this round already established for the date field.
  3. Repoint the five `evidence` strings at that tracked path — the only `deployment/` step; schedule it
     inside C/D's recert round.
- **Runtime impact:** step 1–2 none; step 3 runtime-affecting. **Certification impact:** 1–2 INHERITED;
  step 3 rides C/D.
- **Validation:** the new lock test with a mutation control (a pointer to an untracked path must fail);
  the test running from a fresh clone in CI; the generator re-run reproducing identical digests for
  unchanged bases (digests as the invariant, not formatting).
- **Priority / target:** P1 — steps 1–2 in the first CI-only batch, step 3 in the recert round.

## F. 24-hour soak coverage — P2

- **Issue.** Release assurance rests on a 2 h window; longer leaks and orphan accumulation can hide
  inside it.
- **Root cause / current limitation.** `cap-ga-reliability.yml:40` defaults `CAP_SOAK_SECONDS` to 7200,
  `:45` caps the job at 300 minutes, `:14` runs it nightly at 18:00 UTC — and a hosted job cannot run
  24 h anyway (6-hour ceiling). So the shape, not the number, has to change.
- **Proposed remediation.** A **multi-leg** soak: a `soak_hours` input; N sequential jobs each ≤ 5 h in
  one workflow sharing cluster state via a persisted artifact (or a persistent/larger runner); a roll-up
  step that aggregates the per-leg `soak-context.json` and requires (a) `http_errors == 0` across legs,
  (b) RSS non-increasing across the *whole* window, which is GA-GATE 25's leak signal seen per-leg today,
  (c) `non_terminal_runs` bounded cumulatively rather than per leg. Keep the certified 7200 s leg as the
  release gate and run the 24 h roll-up weekly, so it is assurance rather than a new blocker.
- **Policy position (task 8).** No written policy requires 24 h for a candidate, so **v1.0.6-rc1 owes no
  retroactive soak**; the release-evidence contract stays 7200 s unless a deliberate future change moves
  both the gate and the docs together.
- **Files likely affected.** `.github/workflows/cap-ga-reliability.yml`;
  `backend/tests/test_phase_28_7_ga_tier2_resilience.py` (or a new leg module);
  `scripts/certification/generate_report_28_7.py` (roll-up read); GA-GATE 24/25/26 wording.
- **Runtime impact:** none to the product. **Certification impact:** INHERITED while it only adds
  workflow/tests/report reading; a change to what GA-GATE 24/25/26 *accept* should be proven by a strict
  GA round at one SHA.
- **Validation:** one dry 3-leg run on a throwaway branch, and the negative control that matters — a
  roll-up that refuses a leak visible only across legs, proving aggregation is not decoration.
- **Priority / target:** P2, later release. Cost honesty: three 5-hour jobs per cycle is the runner bill,
  and they queue against the same ref-level concurrency group as certification.

## G. F-39 — CAP images are not reproducible by digest — P2, investigate first

- **Issue.** "Reproducible build" in this project's title is not a verification an operator can perform.
- **Root cause (measured; the list below is candidates, not demonstrated causes).** Two
  clean-runner builds of `cap-sandbox-http` and `cap-egress-proxy` with identical
  `dockerfile_sha256`, `context_sha256` and pinned bases produced different config and index
  digests — that much is measured. What is *not* measured is which unrecorded variable did it;
  the sources below are hypotheses ordered by cost to remove. Batch 1's stage-4 measurement
  (`docs/quality/cap-f39-reproducibility-measurement-2026-09-21.md`) found that the evidence
  format records none of the variables these hypotheses name (no layer digests, no diff IDs, no
  manifest composition, no attestation descriptors, no buildx/BuildKit version, no runner
  metadata), and that not one captured pair is two independent builds of the same commit.
  1. **Timestamps** — no `SOURCE_DATE_EPOCH` anywhere: image config `created` and history carry build
     time, and `git checkout` mtimes flow into `COPY` layer tars (all five images).
  2. **Repository state** — apt (`backend:37`, `sandbox-http:29`, `egress-proxy:13`) and
     `apk upgrade` (`frontend:22`) resolve whatever the distro serves that day. That is *content*, not
     metadata: removing it means shipping older packages, a security decision to make explicitly, not to
     take quietly inside a reproducibility change.
  3. **Unlocked resolvers** — `pip install "httpx==0.27.2" "pydantic==2.9.2"` (`sandbox-http:33`) and
     `playwright==1.49.1` plus `playwright install --with-deps chromium` (`sandbox-browser:30`) are
     version-pinned but not hash-pinned, and the browser fetch is a CDN download; `uv sync --frozen`
     (`backend:12`) and `npm ci` (`frontend:5`) *are* locked. The two sandbox images are the weak pair —
     and they are the two that produced the differing digests.
  4. **Toolchain float** — unpinned syntax directive in two images (R3), unpinned buildx/BuildKit (B2),
     and the console's `npm run build` output feeding `COPY --from=builder /app/dist` (`frontend:30`).
  5. **Attestations inside the index digest** — SBOM/provenance manifests carry invocation timestamps, so
     the index digest can move while every layer matches.
- **Define the model before claiming anything.** Proposed reproducibility target = the per-platform
  manifest digest and the config digest with timestamps normalised, **excluding** attestation manifests;
  the published `index_digest` remains what `values-release-<version>.yaml` pins (the "what an operator
  runs" coordinate) and is explicitly *not* the rebuild-compare target. Write that definition down first,
  because today's F-39 entry is a statement about index digests.
- **Proposed remediation sequence.** (i) a two-build comparison job on one commit, same driver, that
  reports *which* element differs (layer vs config vs attestation) — measurement only, no product change;
  (ii) deterministic metadata: `SOURCE_DATE_EPOCH` from the tag target commit
  (`git show -s --format=%ct`) passed into the build, plus buildx timestamp rewriting **only after**
  verifying that attribute against the pinned buildx/BuildKit version — same no-inventing rule as B;
  (iii) hash-pin pip and Playwright (per-image lockfile or `--require-hashes`, vendored browser archive
  pinned by digest); (iv) decide the apt/apk policy on the record (snapshot pin versus
  "a rebuild means fresh patches"); (v) pin syntax directives and the builder. Only (i)+(ii)+(iii)
  entitle any doc to say "rebuild and compare", and only when two independent clean builds match.
- **Files likely affected.** all five Dockerfiles; `scripts/release/build_release_image.sh`;
  `backend/docker/build_sandbox_images.sh`, `backend/docker/prepare_sandbox_context.sh`;
  `.github/workflows/ci.yml` (comparison job); new `backend/tests/test_release_reproducibility.py`;
  `deployment/third-party-images.json` if base pins move; docs.
- **Runtime impact:** Dockerfiles and package sets ⇒ `deployment`/build-input ⇒ **full
  re-certification**; and because it changes what every image *is*, operators pinned by digest move to
  new digests with the version.
- **Certification impact:** full recert at landing, at one SHA, with the strict GA pairing.
- **Validation:** two independent clean builds matching under the defined model is the *acceptance
  criterion*, not the plan; plus a guard that the comparison job failing to produce artifacts reddens the
  run rather than skipping (the absent-evidence failure mode this repository keeps meeting).
- **Priority / target:** **P2, later release** (1.1.0 or a dedicated hardening RC) — deliberately not the
  next RC, so it is not bundled with gate/CI work whose value does not depend on image bytes changing.

## H. F-25 closure record — P3, documentation only

- **Issue.** F-25 ("the release image graph has never executed") is closed by live run `35553750674` at
  `4d8f9c7`, while the shipped `known-issues.md` asset and the closure report's §10 entry legitimately
  describe the pre-publication world.
- **Proposed remediation.** Add a **new** dated record —
  `docs/quality/cap-1.0.6-rc1-publication-closure-<date>.md` — quoting only from that run: the five
  published refs with index/platform digests, per-image Trivy verdicts, SBOM/provenance presence, the
  rendered `values-release-1.0.6-rc1.yaml` digest set, and the completeness gate's own output; then open
  register entries only for whatever that run shows is *still* unproven. Do not edit the sealed Release,
  its assets, or any shipped file.
- **Files:** new `docs/quality/…`, `docs/known-issues.md`, `CHANGELOG.md`. **Runtime impact:** none
  (`docs`). **Certification impact:** INHERITED, CI only. **Validation:** every number traceable to a
  downloadable artifact of run `35553750674`, with read-only API access.
- **Priority / target:** P3, next docs batch.

---

## Sequencing

| Batch | Items | Classifier cost | Recert needed |
| --- | --- | --- | --- |
| 1 (CI-only) | A (F-33 gate), B steps 1–2 (verify + pin builder), E steps 1–2 (pointer test + tracked generator), G step (i) (two-build measurement), H (closure record) | `ci_workflow` + `test_harness` + `certification_generator` + `docs` → INHERITED | no — CI cycle only |
| 2 (next RC) | C (compose pinning) + D (schema widening) + E step 3 (repoint) + B steps 3–4 (assert, attest) | `deployment/` → runtime-affecting | yes — CI + Linux + K8s + soak + strict GA at one SHA, ≈ 3.5 h serial |
| 3 (later) | F (24 h multi-leg soak roll-up) | `ci_workflow`/`test_harness`, or recert if GA-gate accounting changes | decide after batch 2 |
| 4 (own round) | G steps (ii)–(v) (deterministic metadata, hash pins, apt policy, pins) | Dockerfiles ⇒ full recert, image bytes change | yes, dedicated round |

## Open verification items (read-only; V1 and V2 attempted in batch 1, still unanswered)

- **V1** — what `runDetails.builder.id` actually holds for the sealed images. **Attempted in batch 1,
  not answered:** no `gh` binary, no `GH_TOKEN`/`GITHUB_TOKEN`/`CR_PAT` in the environment, an empty
  `auths` map in `~/.docker/config.json` with no credential helper installed, and an anonymous
  `GET /v2/laolaola278-dev/cap-backend/manifests/v1.0.6-rc1` answers 404. The sealed attestations are
  therefore unreadable from here and B's premise stays "verify" (R1) in both directions. What *is*
  observable locally is that CAP's own `cap.provenance.v1` artifact (`provenance.json` inside
  `ga-cert-artifacts`) records `builder: "github-actions"`, subject digests and SBOM hashes, and has no
  builder-id, predicate-type or invocation field at all -- a fact about this repository's evidence, not
  about the pushed attestation.
- **V2** — published per-image digests and attestation state from run `35553750674`, needed for H's
  numbers and B step 3's wording. The build side was read from the captured round evidence (subject
  digests, `config_digest`, `index_digest` where a buildx build produced one); the pushed attestation
  content remains unread, so H cites the run rather than the attestation.
- **V3** — whether the Playwright browser download for `playwright==1.49.1` is content-stable, which
  decides how much of G step (iii) is hash-locking versus vendoring the archive.

*Batch 1 is being executed in staged commits against this plan; nothing is pushed until its results
are summarised for review. Batches 2-4 need a round that re-certifies, and batch 1's V1/V2 remain
unanswered for the reason recorded above.*
