# CAP POST-RC HARDENING — BATCH 3 DESIGN OPTIONS

Design only. No build path was changed, no gate was edited, no release tag was created, and
nothing in this file is implemented. Every observation below is read from a named file, line or
artifact at the Batch 2 candidate
`BATCH2_CANDIDATE_SHA = 257ba18d8cb6c22d330701b69713544254e4f03a`; where something is inferred or
merely plausible, it is labelled as such and carries the check that would settle it.

## A. Post-Batch-2 docs commit

One commit, on top of the candidate, containing **only** documentation:

| file | change |
| --- | --- |
| `docs/known-issues.md` | items 4 / 7 / 9 marked CLOSED (F-24, F-37, F-41); new item 11 (F-44, OPEN), item 12 (F-45, CLOSED at `257ba18`), item 13 (F-46, OPEN, policy review) |
| `CHANGELOG.md` | `[Unreleased]` preamble corrected (the RC it referred to is sealed); new `### Changed` block for F-24 / F-37 / F-41 / B2; new `### Fixed` entries for F-45 and for filing F-44 + F-46 |
| `docs/quality/cap-post-rc-batch-2-findings-reconciliation-2026-09-22.md` | the prepared reconciliation, now superseded by its application (kept as the audit trail of what was applied where) |
| `docs/quality/cap-post-rc-batch-2-implementation-2026-09-22.md` | the Batch 2 report, previously untracked |
| `docs/quality/cap-post-rc-batch-3-design-options-2026-09-23.md` | this file |

Deliberately absent from it: `deployment/**`, `docker-compose.yml`, both `Dockerfile`s,
`.github/workflows/**`, `scripts/**`, `backend/app/**`. `scripts/release/classify_diff.py` treats
every one of those as runtime-affecting (`deployment`, `production_runtime`) or
publication-affecting (`ci_workflow`), and the point of this commit is that it must not be able to
disturb a certified candidate.

- Parent: `257ba18d8cb6c22d330701b69713544254e4f03a` (the certified runtime candidate).
- Docs tip: the single commit whose parent is that candidate —
  `git log -1 --format=%H` on `main` reads it. This file cannot contain its own hash, so the
  pointer is given as a command rather than as a stale literal, and §B's measured values are the
  proof it is the right commit. Folding §A/§B's measurement in took amends of **the docs commit
  only**; `257ba18` was never amended, rebased or moved, and once the commit was pushed further
  corrections could only be new commits, since amending a pushed commit needs a force push —
  which this round forbids. (That is why this paragraph exists.)

**As pushed.** The reconciliation went to `origin` as a plain fast-forward
(`257ba18 → 44fb73d3e88ed277058f1c6bae933a24624c6a50`, no force), and the push-triggered CI run
`35811320327` at the docs tip completed **success** with all ten jobs green, including
`packaging` (the real-helm check) and `image-and-security`. That CI is feedback that a docs-only
commit does not break the pipeline — it is **not** a certification round, and nothing here is
described as certified by it. The certified runtime candidate remains `257ba18`; the repository
documentation tip is the commit this file is in, one commit later.

## B. Classifier inheritance proof

`python scripts/release/classify_diff.py 257ba18d8cb6c22d330701b69713544254e4f03a HEAD` on the
commit described in §A. Required and recorded outcome:

| field | required | measured |
| --- | --- | --- |
| `inheritance` | `INHERITED` | **`INHERITED`** — "RESULT: runtime certification INHERITED (release_metadata_only=True)" |
| `runtime_affecting` | `false` | **`false`** |
| blocking files | `[]` | **`[]`** — all five files `category: docs`, each `runtime_affecting: false` |
| exit code | `0` | **`0`** |

The classifier was not modified at any point in this round, and if the three values above do not
come out as required the reconciliation stops: the fallback is not a category exception, because
"a metadata-only classifier exception" is the manoeuvre the release governance refuses (it is
named as such in `docs/known-issues.md` item 7).

Two notes on what this proof does and does not mean:

- It proves the docs commit **inherits** Batch 2's certification, i.e. `release.yml`'s gate would
  accept `257ba18`'s rounds for a tag at the docs tip. It does not re-certify anything, and no
  certification round was dispatched for it.
- Inheritance is asymmetric and stays useful precisely because of that: the certified runtime
  candidate is `257ba18` forever; the repository documentation tip moves. §Stage 4 of the
  instruction set records both SHAs side by side for exactly this reason.

## C. F-44 — the exact problem statement

**Question.** Does the project require the actual published-image build path to use the pinned
buildx/BuildKit producer?

The measured state, from CI's own producer records at the candidate (run `35761257654`, six
records: five `ci-release-image-cap-*` artifacts plus `cap-sandbox-http.prerequisite.json`
inside the browser cell's directory):

| element | value | source |
| --- | --- | --- |
| declared pin | `buildx-version: v0.37.1`, `driver-opts: image=moby/buildkit:v0.33.0@sha256:6c2fa84a…` | `ci.yml:337-341`, `ci.yml:395-399`, `release.yml:680-684`, `release.yml:721-725` |
| observed buildx | `github.com/docker/buildx v0.37.0 ac30b249211430b85fb8f37b6e7154b5c47ba0b6` — **6 of 6** | each record's `producer.observed.buildx_version` |
| `comparison.buildx_version.matches` | `false` — 6 of 6 | each record's `producer.comparison` |
| `comparison.buildkit_image.matches` | `null` on all six; the buildx-path record carries `incomplete: ["builder"]` | same |
| build command actually run | `docker build` (five cells, `--local-docker`) / `docker buildx build` (prerequisite, and what the release path uses with `--push`) | `build_release_image.sh:128`, `:142` |
| `--builder` anywhere in the two workflows or the script | absent | grep over `.github/workflows/{ci,release}.yml`, `scripts/release/build_release_image.sh` |
| release path execution | never run (triggers only on a `v*` tag; publication not authorised) | `docs/known-issues.md` item 6 (F-25 scope) |

That combination rules out two easy readings and leaves one real gap:

1. It is **not** "the dry-build path just used the wrong command." The build that did go through
   `docker buildx build` on the same runner observed the same `v0.37.0`.
2. It is **not** a statement that published images are built by the wrong producer. The release
   path has never executed, so its producer is **unobserved**. Claiming either way would be the
   colour-over-decision error F-33 and F-42 were about.
3. The gap that is real: **nothing in the evidence chain binds a built image to a named builder**,
   and the one version field that can be read on a runner disagrees with the pin the workflow
   itself asked for. So the pin is currently an unenforced declaration: `configured` comes out of
   `deployment/third-party-images.json` via `configured_values(lock, …)`
   (`record_build_producer.py:217-231`), which cannot detect an installer that did not take
   effect, and `observed` is read from `docker buildx version` — the CLI shim, not the binary the
   action may have installed.

Consequences worth naming: `builder.id` in buildx-attached provenance inherits this ambiguity
(the plan's R1 row), which is why B3's content assertions must not be written against a producer
this build path cannot demonstrate (§G); and F-39's reproducibility question cannot be answered
at all until "same producer" is a checkable predicate (§J).

## D. Option A — the pinned producer is authoritative

**Contract.** A published image may only be published if the build that produced it ran on the
configured producer, and the evidence proves it rather than describing the intent.

Required evidence per image, all of it read back at build time:

1. configured buildx version and configured BuildKit image digest, both from the lock (today's
   `configured` block, unchanged);
2. observed buildx **of the binary that executed the build**, not of `docker buildx version` —
   which means either invoking the installed buildx directly or capturing the action's own
   reported version, because the CLI plugin shim is what answers today;
3. `docker buildx inspect <builder>` showing `Driver: docker-container`, a node whose
   `BuildKit` version and image ref equal the pinned `moby/buildkit:v0.33.0@sha256:6c2fa84a…`,
   and the resolved digest of the running container image agreeing with the pin (a tag match is
   not enough — the same trap F-20 closed for third-party bases);
4. the build command recorded with an **explicit `--builder <name>`**, so "which builder" is a
   field of the evidence rather than a property of whatever the action left as default;
5. the published-image evidence (`images.json` / `provenance.json` subject digests) bound to
   that producer record, so a reader can tell which builder made the bytes an operator pulls;
6. **mismatch ⇒ failure.** Any of 2-5 disagreeing, unreadable or absent fails the build;
   a "could not look" case stays an error, not a pass, exactly as `AuthorityRejected` versus
   `verdict: ERROR` distinguishes them in `release.yml`.

`--local-docker` in CI should exercise the same producer where practical: the honest version of
that is a CI job that builds through the pinned builder and compares, rather than a dry build that
silently uses a different one and publishes a `matches: false` field nobody blocks on.

**What changes as a result.** The release path stops calling `docker build`-equivalent builds and
starts calling `docker buildx build --builder` for all five images; the CI dry path either matches
it or the divergence becomes visible and asserted. That means **the bytes of every published image
can change** (a different BuildKit version, a different frontend resolution, a different layer
history), so Option A is a producer change by policy — the same reasoning that made B2
recertification-relevant even though the classifier calls a workflow-only diff inheritable
(`docs/quality/cap-post-rc-batch-2-design-freeze-2026-09-22.md`, Image-digests row).

## E. Option B — the docker-engine producer is authoritative

**Contract.** If the intentional producer is the Docker Engine's embedded BuildKit (which is what
every build that has actually run used), then the project says so, and the pin moves to the
surface that is really in charge.

1. `build_release_image.sh` keeps `docker build` for the local/CI path, and the release path is
   explicitly declared to be engine-embedded BuildKit rather than a docker-container builder;
2. `deployment/third-party-images.json` stops declaring `buildx v0.37.1` /
   `moby/buildkit:v0.33.0` as *the producer* and instead pins/records the surfaces that actually
   govern the bytes: the runner image or Docker Engine version (`28.0.4` observed at the
   candidate), the engine's embedded BuildKit version, and the Dockerfile frontend
   (`docker/dockerfile:1@sha256:ecfaec9e…`, already pinned where a directive exists);
3. misleading producer assertions are **removed** rather than left descriptive: no field may
   imply the pinned BuildKit container built something it did not, and `setup-buildx-action`'s
   `buildx-version`/`driver-opts` are dropped where they do nothing — an input that has no effect
   is how a comment becomes a lie later;
4. reproducibility and provenance are defined against *that* producer: the attestation subject,
   the SBOM, and any future assertion must be readable as "engine BuildKit X at commit Y built
   these layers", not as a builder identity nobody inspected;
5. the runner becomes load-bearing input, so it has to be pinned or at least recorded and
   compared — GitHub updates `ubuntu-24.04` images under the same name, which is the same
   mutable-tag-by-another-name problem F-24 just closed for images.

**What changes as a result.** Ideally nothing: the same engine that produced every observed build
keeps producing them, so image bytes are stable and the change is to claims, configuration and
evidence. The risk is honesty-shaped rather than byte-shaped: Option B makes the Docker Engine and
the hosted runner part of the release surface, and a runner-image update becomes a
re-certification trigger. That is a real cost, and it is paid whether or not the register admits it.

## F. Recommendation

**Recommend Option A, with a narrowing that removes most of its cost — and recommend it as a
two-step landing, not a single switch.**

Reasoning, on the axes the decision is supposed to turn on:

| axis | Option A (pinned producer authoritative) | Option B (engine authoritative) |
| --- | --- | --- |
| image bytes | May change for all five images (different BuildKit/frontend resolution, layer history) → full re-certification | Should not change; claims and config move instead |
| CI compatibility | Needs work: today's CI proves the CLI shim answers `v0.37.0` even on the buildx path, so "call `docker buildx build`" is not by itself the fix — the executing binary and a named builder must be pinned and observed | Native: CI already runs this way, and the 6/6 records become conforming instead of violating |
| provenance | `builder.id`/`invocation` can be made to describe an inspected builder; assertion becomes possible (§G) | Provenance describes the engine+runner; `builder.id` stays runner-flavoured unless B4 signs it (§H) |
| SBOM | Unaffected in shape; re-scanned if digests change | Unaffected |
| local development | A developer needs the pinned builder to reproduce release bytes, or CI is the only conforming path | Works with a plain Docker install, at the price of a floating engine |
| certification cost | High: producer change ⇒ candidate + all rounds (CI, Linux production, K8s, 7200 s reliability, strict GA), measured in Batch 2 at roughly a full day of round time | Low if bytes really do not move; but any runner-image drift re-triggers it, repeatedly |
| F-39 / G(i) | Directly enabling: "same producer" becomes a checkable predicate before two builds are compared (§J) | Also enabling, but only for the engine surface; the frontend and layer-history fields are still missing |

Why not the easier path by default: Option B is cheaper, and choosing it *because* it is cheaper
is what the instruction set forbids. It is the right answer only if the project decides the engine
**is** the intended producer as a matter of fact — and that decision has not been made, which is
exactly what §M asks. Option A is the recommendation because the project already made the
comparable decision twice: F-24 pinned third-party images by digest precisely so that a mutable tag
could not decide the bytes later, and F-41 refused "the operator can't pin it" as an acceptable
state for two of six Helm coordinates. A declared builder that no evidence connects to the output
is the same class of hole.

The narrowing that makes A affordable: **do not switch the release path in the same commit that
proves the producer.** Step 1 (docs + recorder only, `ci_workflow` + `certification_generator` →
INHERITED, CI cycle cost): build through an explicitly named builder in a non-publishing CI job,
and record executing-binary version, `buildx inspect` driver/node BuildKit version and image
digest, and the builder name — with no gate change, so nothing is blocked on a field nobody has
seen yet. Step 2 (the real switch, runtime-affecting by policy, full candidate + rounds): the
release path adopts the pinned builder and the mismatch becomes release-blocking, with step 1's
records as the evidence that the assertion can actually be satisfied. This keeps the project out of
the failure mode Batch 2 spent two re-freezes avoiding — writing a gate before the data exists to
know it will pass.

## G. B3 relationship

B3 (provenance assertions in the release gate) stays **behind** this decision, and the reason is
sharper now than at the design freeze: `builder.id` is not a stable thing to assert while the
producer is undetermined. The freeze already refused the cheap version — "a B3 that asserted
`builder.id == \"\"` would be an assertion that a field stays unpopulated" — and the plan's R1 row
keeps that as a design decision rather than a fix.

- **Under Option A**, B3-A becomes meaningful: an inspected named builder with a pinned BuildKit
  image gives content assertions something true to point at. Even then, assert what is measured
  (builder name, driver, node BuildKit image digest, buildx binary version) rather than
  `builder.id == <guess>`.
- **Under Option B**, B3 must not assert against the docker-container builder at all, since the
  build path does not use it. Its provenance contract becomes the engine/runner surface.
- Either way: **do not require provenance assertions against a producer the build path does not
  actually use.** That is the sentence that decides B3's order of business, and it is why B3 is not
  scheduled before F-44 resolves.
- If F-44 were resolved as "no requirement, leave it as-is", B3 does not become free to land on
  the current facts — it becomes blocked on a different question (what is the producer *supposed*
  to be), which is why §M lists the two as separate approvals.

## H. B4 boundary

B4 stays an independent security decision, and the three contracts it must answer separately are
exactly the ones this finding shows the risk of collapsing:

1. **Content provenance** — what the attestation says about the build: builder/binary version,
   BuildKit version and image digest, driver, frontend, inputs, material. Option A changes what
   can honestly be written here; Option B changes what should be.
2. **Workflow identity** — which GitHub workflow/commit produced it (OIDC subject claims:
   `repository`, `ref`, `run_id`, `workflow`), which is a claim about *who ran the build*, not about
   *what compiled the layers*. Conflating the two is how a builder identity ends up standing in for
   a workload identity, and the Batch 1 observation that a builder id may be empty or self-declared
   is the warning.
3. **Signature/verification** — who signed it (Sigstore/GitHub-registered vs buildx's self-certified
   attachment) and what a verifier must be able to check at the pull site. Reading an artifact is
   not verifying a signature; that distinction is already in the plan's B3 note and stays the
   reason B4 cannot be smuggled in as "we already have attestations".

`builder.id` is where all three collide, so it gets its own decision line in §M and must not be
resolved as a side effect of F-44.

## I. Linux isolation findings

Observed at the candidate's Linux certification artifact
(`cap-28.5-linux-certification.json` in artifact `10712355258`):
`worker_control_plane_isolation = "PARTIAL"`, `unrestricted_docker_socket_mounted = true`, next to
`sandbox_workload_isolation = "PASS"`, `production_chart_worker_mounts_runtime_socket = false`,
`compose_worker_mounts_runtime_socket = true`.

**What produces each field.** `scripts/certification/generate_report.py:docker_socket_control_plane()`
(lines 215-239) derives both facts from the repository at assertion time —
`chart_worker_mounts_control_socket()` and `compose_worker_mounts_control_socket()` — and returns
`PASS` only if neither path mounts a runtime socket, `NOT_CERTIFIED` if the **chart** does, and
`PARTIAL` if only **compose** does. The legacy boolean is the `or` of the two, deliberately, so an
older consumer cannot read `PARTIAL` as full isolation.

**Why a PASS round is allowed today — and it is not "ungated".**
`cap-linux-certification.yml` asserts on these fields directly:
`worker_control_plane_isolation != "PASS"`,
`production_chart_worker_mounts_runtime_socket is False` (message: `RELEASE BLOCKER`),
`compose_worker_mounts_runtime_socket is True`, and that the artifact's recorded values equal what
the repo yields at assertion time. `backend/tests/test_certification_workflow_contract.py`
polices that block (including a dead-detector check and a pinned-`== "PASS"` mutation), and
`backend/tests/test_phase_28_5_rc_certification_artifact.py` pins `PARTIAL` / `true`. So the
policy pins the **disclosure** and forbids claiming isolation; it does **not** require isolation on
the compose path, and by asserting the mount is present it also refuses an undocumented removal.

**Intended secure state (to be decided, not assumed).** Either (a) `PASS` /
`unrestricted_docker_socket_mounted: false` for both paths, i.e. the compose worker stops holding
the control socket; or (b) `PARTIAL` accepted as a permanent, owned exception for the
evaluation/single-node path with a stated threat model, in which case the field pair should be
documented as a decision rather than as a measurement.

**Whether either should become release-blocking.** The honest framing: today's policy already
blocks the *chart* path (`is False` with a RELEASE BLOCKER message) and blocks *silence* (the
`!= "PASS"` and `reported == live` assertions). The open question is narrower — whether a
host-root-equivalent worker on the compose path should be able to ship at all. Compose is the
path the README quickstart reaches first, which is why this is a security question and not a
cosmetic one; it is also the path the K8s certification does not cover.

**Migration/runtime impact if enforced.** `SANDBOX_PROVIDER=oci-sandbox` in
`docker-compose.yml` makes the worker drive sandbox containers through the docker CLI, so
removing the socket means replacing that mechanism (rootless-in-rootless, a socket-brokered
sandbox API, or dropping the compose sandbox path in favour of the chart). Enforcing it also
requires changing, in the same commit: the `compose_worker_mounts_runtime_socket is True`
assertion, `docker-compose.yml`'s warning comment, `.env.example`, the `compose_control_socket_scope`
string baked into `generate_report.py`, `test_sandbox_socket_boundary.py` (which asserts in both
directions that the prose and the mount agree) and `docs/known-issues.md`. Those are
`deployment`/runtime-affecting surfaces, so it is a re-certifying change, not a docs change.
**This stage changed none of them.**

F-46 is filed separately from F-44 on purpose: different file, different gate, different
migration, and merging them into one "certification is soft" complaint would lose the one thing
both findings turn on — which assertion is missing versus which state is accepted.

## J. F-39 experiment impact

F-44's mismatch is an input to G(i), and it changes the experiment's preconditions rather than
its conclusion. A valid two-build reproducibility measurement must record, for **each** build:

- which producer actually ran (docker CLI + engine BuildKit, or a named docker-container builder);
- buildx version **of the executing binary**, plus the CLI shim version, kept as separate fields;
- BuildKit / engine version (node `BuildKit` version for a container builder;
  `engine_version` for the CLI path — `28.0.4` observed at the candidate);
- driver (`docker` vs `docker-container`);
- frontend (`# syntax` ref and resolved digest where a directive exists);
- base index digest **and** the `linux/amd64` child digest used (the E2 artifact's two levels);
- layer digests and `diff_ids`;
- the platform manifest as served;
- index composition (which children the manifest list names, attestation entries included);
- attestation descriptors (digest, `mediaType`, `artifactType`) attached to the subject.

Two builds whose producer fields differ are **not** evidence of image nondeterminism; they are two
different builds, and any digest difference between them is unattributable. That is precisely the
mistake the F-39 entry had to withdraw a claim over ("no captured pair is two independent builds of
one commit", `docs/known-issues.md` item 8), and it is why the producer fields are prerequisites
rather than nice-to-haves: without them, "same inputs" cannot be asserted, and the measurement
cannot say what it controls for.

Feasible ordering: the per-build record can be extended first (`ci_workflow` +
`certification_generator` → inheritable, CI-cycle cost) and the two-build pair run after it, so the
pair is produced by a build whose producer is legible. Under Option B the pair is cheap — both
builds are engine builds on pinned-ish runners — but "pinned-ish" is the weak link: a hosted
runner image that moves under a fixed name silently changes the producer, so Option B needs its
own runner/engine pin to make the experiment reproducible at all.

## K. Expected changed files

**Batch 3 step 1 — observe the producer (Option A's prerequisite; inheritable classification,
CI-cycle cost only):**

- `scripts/release/record_build_producer.py` — executing-binary version vs CLI shim, builder name,
  `buildx inspect` driver/node BuildKit version **and image digest**, resolution of the running
  container's digest;
- `deployment/third-party-images.json` — if the recorded fields grow a schema-shaped form, the
  lock's own documentation of them (note: this path is `deployment`, i.e. runtime-affecting — so
  keep step 1 inside the script and the workflows, and leave the lock alone unless a field must be
  added);
- `.github/workflows/ci.yml` — a non-publishing build through the explicitly named builder, so the
  producer is observed on the path CI actually runs;
- `scripts/release/build_release_image.sh` — accept/record `--builder` without changing which
  command the release path uses yet;
- `backend/tests/test_build_producer_contract.py` — new fields plus a control per field
  (a builder that is not the pinned image must be reported as a mismatch, not as `null`);
- `docs/known-issues.md`, `CHANGELOG.md` — F-44 status text only after it is measured.

**Batch 3 step 2 — make it authoritative (runtime-affecting by policy, full candidate + rounds):**

- `.github/workflows/release.yml` (all five release image jobs, including the two
  `setup-buildx-action` blocks), `scripts/release/build_release_image.sh` (release path calls
  `docker buildx build --builder …`), the evidence consumers that read `images.json` /
  `provenance.json`, `backend/tests/test_release_image_completeness.py`,
  `test_release_publication_gate.py` (mismatch ⇒ `AuthorityRejected`-style refusal, keeping
  "could not look" as `ERROR`), plus the F-39 measurement file.

**Option B instead (bytes stable, claims move):**
`deployment/third-party-images.json` (the `buildx` / `buildkit-buildkit` entries redefined as
engine/runner surfaces), `ci.yml` + `release.yml` (drop ineffective `buildx-version` /
`driver-opts`, add engine/runner recording), `record_build_producer.py`,
`test_build_producer_contract.py`, and the register text that currently implies a container
producer.

**F-46, if enforced (separate batch, design in §I):** `docker-compose.yml`,
`backend/app/**` sandbox provider path, `.env.example`,
`scripts/certification/generate_report.py`, `.github/workflows/cap-linux-certification.yml`,
`backend/tests/test_sandbox_socket_boundary.py`,
`test_certification_workflow_contract.py`, `test_phase_28_5_rc_certification_artifact.py`,
`docs/known-issues.md`.

## L. Expected classifier / recertification impact

| change | classifier | recertification |
| --- | --- | --- |
| this docs-only reconciliation | `docs` → INHERITED, `runtime_affecting=false`, blocking `[]` | none — proven in §B |
| step 1: recorder + a CI-path observation, no release behaviour change | `ci_workflow` + `certification_generator` + `test_harness` → INHERITED by classification | by classification: CI cycle. Deliberately staged this way so the producer question is answered with data before any gate can block on it |
| step 2: release path switches builder | workflow+script files may still classify `ci_workflow`, but the effect is producer-affecting, and Batch 2 already established that a build-input change must be treated as recertification-relevant (B2's own classification note in the design freeze) | full: candidate freeze, CI, Linux production, K8s, 7200 s reliability, strict GA, gate dry-run — roughly Batch 2's round cost, ~a day of round time |
| Option B: engine surface pinned, ineffective inputs removed | `deployment` (the lock) → RECERTIFICATION_REQUIRED, `runtime_affecting=true` | full, even though bytes should not move, because build inputs *as declared* changed — the same reasoning that moved `values.schema.json` and the compose refs |
| F-46 enforcement | `deployment` + `production_runtime` | full, plus the compose-path certification that asserts the mount exists must change in the same commit |

A rule worth restating because both options tempt the same shortcut: an unexpected verdict is an
investigation, not a reason to touch `classify_diff.py`.

## M. Open decisions requiring approval

1. **F-44's contract** (the root question, §C): must the published-image build path *be* the
   pinned producer (→ Option A, §D), is the engine the intended producer (→ Option B, §E), or is
   the pin declared-but-unenforced, which the register must then say plainly instead of implying
   a guarantee?
2. **Should a producer mismatch block publication?** Distinct from 1: a project may want the
   evidence without the gate. Today nothing blocks it.
3. **Does "configured" mean the lock or the workflow's request?** The recorder compares the lock
   against the CLI shim (`record_build_producer.py:217-231`), so an installer that silently did not
   take effect is invisible. Fixing this is what makes step 1 worth doing.
4. **B3 gating**: approve B3's content assertions only after 1, and only against fields the chosen
   producer actually exposes; no assertion on `builder.id` while 1 and 6 are open.
5. **F-46's policy**: is a host-root-equivalent compose worker an acceptable shipped state, or a
   release blocker? §I lists what moves either way.
6. **B4's three contracts separately** (§H): content provenance, workflow identity,
   signature/verification — and which of them `builder.id` is allowed to answer, if any.
7. **F-39/G(i) sequencing**: extend the per-build record first, then run the two-build pair
   (§J); and under Option B, how the runner/engine is pinned so "same producer" means anything.
8. **Push of this docs reconciliation** — see §N note; not authorised by anything in this batch.

## N. FINAL DESIGN STATUS

**BATCH 3 — DESIGN ONLY, NOT APPROVED, NOT IMPLEMENTED.**

- Delivered: the producer question stated from measured evidence rather than from intent, two
  contracts with their byte/CI/provenance/SBOM/local/certification/reproducibility prices, a
  recommendation with a staged path that avoids gating on a field nobody has observed, the B3 and
  B4 boundaries, F-46 as its own policy finding, and the F-39 preconditions.
- Not done, on purpose: no `Dockerfile`, workflow, script, lock, gate or compose change; no
  certification dispatch; **no release tag**; `v1.0.6-rc1` and `v1.0.5` untouched.
- Standing relationship between the two SHAs: `257ba18` remains the certified runtime candidate,
  and the docs tip advances past it because the classifier allows docs to inherit. That is a
  documented inheritance, not a re-certification, and no part of this round may be read as one.
- Implementation of Batch 3 waits on an explicit approval of §M items 1-3 at minimum; §M 4-7 can
  follow the first decision but not replace it.
