# CAP POST-RC HARDENING — BATCH 1 CLOSURE REPORT

## A. Scope

Date 2026-09-21. This round adds **no new hardening scope**: it audits what batch 1
actually executed, closes the F-33 publication-safety proof, names the F-37 contract as
the transitional thing it is, keeps F-39 inside its evidence, records the two blocked
items as blocked, and gives each commit its own classifier audit.

Standing boundaries, honoured: **no push, no Batch 2, no action against the sealed
`v1.0.6-rc1` publication state.** Integrity evidence is in §M.

Status vocabulary used throughout, with attempted ≠ done:

| Marker | Meaning |
| --- | --- |
| DONE | the work exists, is committed, and is executed-tested here |
| PARTIAL | some of the step's deliverable exists; the step's own acceptance criterion is unmet |
| DEFERRED | not started, with the reason and the round it belongs to |
| UNVERIFIED | could not be observed from this environment; no claim made in either direction |

Item states: **A/F-33 DONE · B1/V1 UNVERIFIED · B2 DEFERRED · B3/B4 NOT STARTED ·
E1 DONE (transitional contract) · E2 DEFERRED · E3 DEFERRED · G(i) PARTIAL ·
H/F-25 DEFERRED.** The plan's per-item status lines and its Sequencing table now say the
same thing (commit `c31d21d`).

## B. Staged commits

Twelve commits on local `main`, in order above the sealed line `4d8f9c7`, with the reviewer's
bucket each one belongs to — twelve counting this report's own commit `21627a9`, which is listed
below. The commit that writes that sentence adds a thirteenth and changes nothing else; a report
that tried to exclude itself would only be stale in a different direction.

| # | Commit | Bucket | Content |
| --- | --- | --- | --- |
| 1 | `5f79950` | **A** | gate reads the round's own verdict artifact instead of job colours |
| 2 | `c7687c9` | **E** | cited evidence must be readable from the clone; absence justified by name |
| 3 | `d4069d6` | **Docs** | provenance observation: the probes and the UNVERIFIED verdict |
| 4 | `e47b6b4` | **G** | F-39 element-by-element measurement + the reviewed plan |
| 5 | `05a1553` | **Docs** | prose the batch made false, corrected; CHANGELOG; batch-1 execution report |
| 6 | `036d389` | Docs | the execution report's own tables re-measured against its head |
| 7 | `2ce885b` | **A** | `decide()`: the last route from a recorded refusal to a published PASS, closed |
| 8 | `366a06d` | **E** | the guard named as transitional; fresh-clone proof; index-vs-checkout diagnosis |
| 9 | `623e820` | **G** | every F-39 cause kept a hypothesis, in the notes as well as the report |
| 10 | `c31d21d` | **Docs** | the DONE/PARTIAL/DEFERRED/UNVERIFIED model applied to the plan and the report |
| 11 | `107867f` | **A** | the last way a verdict could be optional: unconditional job and step, pinned |
| 12 | `21627a9` | **Docs** | this closure report, with the per-commit classifier audit and measured test results |

Buckets recorded as they actually landed rather than reshaped to fit the scheme:
commits 4 and 5 each carry two documents (all `docs`), so the plan travelled with the
measurement file and the register corrections travelled with the report. No code and no
document were moved across a bucket boundary after the fact; commits 1, 2, 7 and 8 are
the code/test ones, and each is classifier-auditable alone (§D). If a tighter history is
wanted before any push, 4/5/9 are separable by content, and none of it changes what was
verified.

## C. Changed files per commit

| Commit | Files |
| --- | --- |
| `5f79950` | `.github/workflows/release.yml`; `backend/tests/test_release_publication_gate.py` |
| `c7687c9` | `backend/tests/test_third_party_image_lock.py` |
| `d4069d6` | `docs/quality/cap-provenance-identity-observation-2026-09-21.md` (new) |
| `e47b6b4` | `docs/quality/cap-f39-reproducibility-measurement-2026-09-21.md` (new); `docs/quality/cap-post-1.0.6-rc1-hardening-plan.md` (new) |
| `05a1553` | `CHANGELOG.md`; `docs/known-issues.md`; `docs/quality/cap-1.0.6-rc1-artifact-closure-2026-09-20.md`; `docs/quality/cap-f39-reproducibility-measurement-2026-09-21.md`; `docs/quality/cap-post-rc-batch-1-execution-2026-09-21.md` (new) |
| `036d389` | `docs/quality/cap-post-rc-batch-1-execution-2026-09-21.md` |
| `2ce885b` | `.github/workflows/release.yml`; `backend/tests/test_release_publication_gate.py` |
| `366a06d` | `backend/tests/test_third_party_image_lock.py`; `docs/known-issues.md`; `docs/quality/cap-post-1.0.6-rc1-hardening-plan.md` |
| `623e820` | `CHANGELOG.md`; `docs/quality/cap-f39-reproducibility-measurement-2026-09-21.md`; `docs/quality/cap-post-1.0.6-rc1-hardening-plan.md` |
| `c31d21d` | `docs/quality/cap-post-1.0.6-rc1-hardening-plan.md`; `docs/quality/cap-post-rc-batch-1-execution-2026-09-21.md` |
| `107867f` | `backend/tests/test_release_publication_gate.py` |
| `21627a9` | `docs/quality/cap-post-rc-batch-1-closure-2026-09-21.md` (new) |
| this report | `docs/quality/cap-post-rc-batch-1-closure-2026-09-21.md` |

Cumulative `4d8f9c7` → HEAD: **11 files** — one workflow, two test modules, eight documents
(including this report). Nothing under `deployment/`, `backend/app/`, `frontend/src/`, any
Dockerfile, `VERSION`, `pyproject.toml` or a lockfile was touched.

## D. Classifier result per commit

`scripts/release/classify_diff.py <parent> <commit>` for every hop, plus three
cumulative ranges; transcript `_tmp/classifier_audit2.log`. The classifier is the
authority; it was not modified, and no verdict here was inferred from paths by hand.

`release_metadata_only` is the classifier's own property, and its name is narrower than
its definition: it means **every changed path is in an inheritable category** (not
"version-only"). `runtime_affecting` is the blocking one.

| Commit | Parent | Changed files | Categories | runtime_affecting | release_metadata_only | blocking_files | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `5f79950` | `4d8f9c7` | 2 | `ci_workflow`, `test_harness` | False | True | none | INHERITED |
| `c7687c9` | `5f79950` | 1 | `test_harness` | False | True | none | INHERITED |
| `d4069d6` | `c7687c9` | 1 | `docs` | False | True | none | INHERITED |
| `e47b6b4` | `d4069d6` | 2 | `docs` ×2 | False | True | none | INHERITED |
| `05a1553` | `e47b6b4` | 5 | `docs` ×5 | False | True | none | INHERITED |
| `036d389` | `05a1553` | 1 | `docs` | False | True | none | INHERITED |
| `2ce885b` | `036d389` | 2 | `ci_workflow`, `test_harness` | False | True | none | INHERITED |
| `366a06d` | `2ce885b` | 3 | `test_harness`, `docs` ×2 | False | True | none | INHERITED |
| `623e820` | `366a06d` | 3 | `docs` ×3 | False | True | none | INHERITED |
| `c31d21d` | `623e820` | 2 | `docs` ×2 | False | True | none | INHERITED |
| `107867f` | `c31d21d` | 1 | `test_harness` | False | True | none | INHERITED |
| `21627a9` | `107867f` | 1 | `docs` | False | True | none | INHERITED |
| **cumulative** `4d8f9c7` → HEAD | | 11 | `ci_workflow` 1, `test_harness` 2, `docs` 8 | False | True | none | **INHERITED** |
| **cumulative** `b671f53` → HEAD | | 15 | adds the closure round's own `docs`/`test_harness` | False | True | none | **INHERITED** |
| **cumulative** `d30b4e7` → HEAD | | 13 | | False | True | none | **INHERITED** |

Stop condition — any commit `RECERTIFICATION_REQUIRED` or `runtime_affecting=true`:
**not triggered** (all twelve measured; `_tmp/classifier_audit3.log`, plus `21627a9` and the
cumulative ranges re-run afterwards — 11 files from `4d8f9c7`, 15 from `b671f53`, both INHERITED). Two
consequences worth stating: batch 1 kept its CI-only contract, and a tag cut at this head
would still resolve its certification from `b671f53` (Linux/GA/soak) and `d30b4e7`
(K8s) rather than needing a fresh round.

## E. F-33 closure proof (DONE)

**Control flow, end to end.** `authoritative_artifact()` → `AuthorityRejected` (structural)
or a `rejects[]` entry (content) → `failures.append()` → `decide()` → verdict → step exit
status → `needs` → publication. Independently, a `RuntimeError` from any `gh`/`git`/
classifier call escapes `main()` → the bottom handler writes `verdict: ERROR` and
re-raises → the step fails → job fails → every publisher behind `needs` is skipped.

| # | Case required | Route | Final verdict | Publication | Test |
| --- | --- | --- | --- | --- | --- |
| 1 | `AuthorityRejected` blocks | refusal → `rejects[]` → `failures` | FAIL, exit 1 | blocked | `test_an_absent_authoritative_artifact_refuses_the_release`, `…_two_verdict_files_is_ambiguous_too`, matrix |
| 2 | `RuntimeError`/API failure blocks | raises out of `main()` | ERROR, step re-raises | blocked | `test_a_failed_artifact_download_says_could_not_decide_not_uncertified`, `test_api_failure_raises_instead_of_looking_like_absent_evidence`, `test_error_and_fail_refuse_publication_by_different_routes` |
| 3 | `failures == []` with an error signal must NOT pass | `decide()` raises | ERROR | blocked | `test_the_verdict_is_computed_from_every_signal_the_gate_records`; measured by mutation, §E↓ |
| 4 | `failures != []`, no error, must NOT pass | `decide()` returns FAIL | FAIL | blocked | every refusal test; `test_no_refused_authority_state_leaves_the_release_able_to_pass` |
| 5 | all authorities valid, nothing recorded against the release | `decide()` returns PASS | PASS, exit 0 | allowed | `test_a_strict_round_that_certified_is_accepted_and_what_was_read_is_recorded`, `test_gate_accepts_evidence_for_the_tagged_commit_itself` |
| 6 | malformed JSON blocks | `AuthorityRejected` | FAIL | blocked | `test_an_unparseable_authoritative_artifact_fails_closed`, matrix `[malformed json]` |
| 7 | duplicate artifact blocks | `AuthorityRejected` | FAIL | blocked | `test_two_artifacts_with_one_name_are_not_resolved_by_guessing`, matrix `[duplicated artifact]` |
| 8 | expired artifact blocks | `AuthorityRejected` | FAIL | blocked | `test_an_expired_artifact_cannot_be_read_back`, matrix `[expired artifact]` |
| 9 | wrong commit blocks | content `rejects[]` | FAIL | blocked | `test_an_artifact_describing_another_commit_is_not_this_run_s_verdict`, matrix `[wrong commit]` |
| 10 | development GA blocks | content `rejects[]` (4–5 at once) | FAIL | blocked | `test_a_green_development_mode_ga_round_is_not_ga_certification`, matrix `[development mode]` |

FAIL and ERROR keep different diagnoses and are both terminal non-PASS. The structural
half of that claim is pinned rather than assumed: no `continue-on-error` on the job or any
step, no `if:` that could make the gate or its verdict step conditional, the job's only
prerequisite is `validate-tag`, the step's script does not end in `|| true`, and it ends by
raising `SystemExit(GATE_EXIT)` — `test_nothing_in_the_gate_job_tolerates_its_own_failure`.
Adding `continue-on-error: true` to the gate step was verified to turn that test red,
naming the offender.

**Case 3 measured, not argued.** The one edit that would create it — deleting the
`failures.append(f"{workflow}: {reason}")` line that records an authority refusal — was
applied to the workflow and the gate run against a development-mode artifact: outcome
`verdict: ERROR`, `publication would proceed: False`, `release.yml` restored
byte-for-byte (`_tmp/control_f33_contradiction.py`). So the invariant holds even against
an edit that drops a refusal's failure line; no renaming of ERROR to FAIL was involved
or needed, and the fix was structural (single verdict computation) rather than cosmetic.

**What the gate requires** (the `AUTHORITY` table, compared against this module's copy in
every evidence file so neither can drift):

| Workflow | Artifact | Member | Commit binding | Exact fields | Zero counts | Completeness |
| --- | --- | --- | --- | --- | --- | --- |
| `cap-ga-certification.yml` | `ga-cert-artifacts` | `cap-cert-ga/cap-28.7-ga-certification.json` | `commit` == selected run `head_sha` | `mode == "final-strict"`, `full_ga_certified is True` (type-strict, never recomputed from junit or job colours) | `failed`, `not_run`, `skipped`, `planned` | `passed == total` |
| `cap-k8s-certification.yml` | `k8s-cert-artifacts` | `cap-28.6-k8s-certification.json` | `commit` == selected run `head_sha` | none (the artifact publishes no `mode`) | `failed`, `not_run` | `passed == total` |

No gate count is hardcoded: `passed == total` is judged inside the artifact's own table,
and real rounds that recorded 32/32, 33/33 and 34/34 all certify — pinned by
`test_an_older_round_with_a_smaller_gate_table_still_certifies`.

**Controls.** Eleven guard-by-guard mutations of the gate source: authority step never
runs (14 tests), type-strict flag (1), commit binding (1), duplicate artifacts (1),
duplicate zip members (1), expired artifact (1), absent count read as zero (1),
`passed == total` dropped (3), unreadable artifact (1), refusal not recorded as a failure
(23, and still never a PASS), `decide()` cross-check disabled (1). Each caught by its own
test; no control was unnoticed; `release.yml` restored byte-for-byte after each.

**Real-evidence compatibility, re-run after `decide()`:** 13 artifacts from sealed and
superseded rounds — 6 certified, 7 refused, **0 read the wrong way** — including the
development-mode soak artifact at the strict round's own SHA and three
strict-but-incomplete rounds.

## F. F-37 transitional state (E1 DONE; finding OPEN; E2/E3 DEFERRED)

**The contract as implemented, in one line:** a cited path that is ignored or absent is
allowed **temporarily** when a tracked twin exists and both state the same digest claims;
anything else fails. It is written into the test module's section heading, the plan
(`c31d21d`) and `docs/known-issues.md`, and the plan's old validation sentence — "a
pointer to an untracked path must fail", a rule never landed and red on this tree — was
replaced by the six acceptance rules below. **E3 upgrades the contract to a direct tracked
pointer; until then the transitional rule is the one under test.**

| Required case | Result | Evidence |
| --- | --- | --- |
| matching tracked twin, same digest claims | PASS | `test_cited_evidence_resolves_to_a_tracked_copy_of_the_same_measurement` on the real five entries |
| no tracked twin | FAIL | `test_pointer_checker_refuses_a_claim_no_clone_can_read`; real-data control "the cited file is the only copy of the measurement" |
| twin digest mismatch | FAIL | `test_pointer_checker_notices_a_diverged_copy_but_not_a_different_date`; controls "measures something else" and "a digest only one copy asserts" |
| index says tracked, checkout file absent | FAIL, distinct diagnosis | `…_refuses_a_tracked_twin_that_proves_nothing` (both branches); found by running the module in a clone with the twin moved |
| unsupported no-evidence entry | FAIL | `test_justification_checker_notices_a_new_silent_entry`; control "an entry cites nothing and says nothing" |
| allow-set silently expands | FAIL | `test_an_entry_with_no_evidence_pointer_justifies_itself_inline` asserts table keys == the lock's no-evidence entries |
| fresh clone resolves every claim from tracked files | PASS | `git clone` into scratch (`outputs/` absent entirely): 21 passed; red again when the tracked twin is removed inside that clone |

Absence semantics were settled before requiring anything: `evidence` exists on exactly the
five base-image entries, and `minio-object-store` / `postgres` justify themselves with
fields that already exist (`image.provenance.vendor_signed_tag` + `open_gap`;
`lock.policy.postgres`). No new lock field was invented, so no schema change was needed.

**Not done:** E2, the tracked registry-resolution generator (nothing implemented), and E3,
the repoint — the five strings still name
`outputs/artifact-closure/registry-base-digests.json`, which no fresh clone contains.
F-37 remains OPEN by design, not by omission of the note: rewriting them is a
`deployment/` edit, and `classify_diff.py` charges that as runtime-affecting.

## G. Provenance V1/V2 state (UNVERIFIED; B2 DEFERRED; B3/B4 not started)

Read-only, and it did not answer. Probes and results, in
`docs/quality/cap-provenance-identity-observation-2026-09-21.md`: no `gh` binary in `PATH`
or any conventional install location; no `GH_TOKEN`/`GITHUB_TOKEN`/`CR_PAT`/`GITHUB_PAT`/
`REGISTRY_TOKEN`/`DOCKER_PASSWORD`; `~/.config/gh` and `~/.netrc` absent;
`~/.docker/config.json` with `"auths": {}`, `credsStore: desktop`, and no
`docker-credential-desktop` helper installed; the anonymous ghcr token endpoint answers
HTTP 200 with a 68-character anonymous token, and
`GET /v2/laolaola278-dev/cap-backend/manifests/v1.0.6-rc1` answers **404** — which is
ghcr's answer to both "absent" and "not visible to you".

Therefore nothing is claimed about `predicateType`, the pushed `subject[].digest`,
`buildType`, `runDetails.builder.id`, invocation/source/revision fields, materials, or the
buildx/BuildKit versions that produced the sealed images — **in either direction**. No
builder-id field, pattern or regex exists in any executable file:
`grep -rn "builder.id|builder_id|builder-id" --include=*.py --include=*.sh --include=*.yml`
returns nothing outside prose. B2 remains DEFERRED with nothing pinned: all four
`docker/setup-buildx-action@v3` references carry no `buildx-version`, and the BuildKit
image behind the `docker-container` driver is unpinned. B3/B4 were not implemented, and no
registry content was mutated.

**The three things that are observable, each with its limit stated where it is written:**
(a) the build runs `--provenance=true --sbom=true --push` with buildx defaults
(`scripts/release/build_release_image.sh:137`); (b) the evidence's
`attestations.{sbom,provenance}` booleans are derived from those flags
(`:217-219`) and the completeness gate asserts *that* field
(`release.yml`, "no `{kind}` attestation") — so what stands in front of publication today
is a self-report about a command line; (c) all 17 captured build records are dry builds
(`pushed: false`), and the rounds' `cap.provenance.v1` artifact has no predicate,
`runDetails` or invocation field. (c) is CAP's summary of a build and **is not substitute
evidence** for OCI/buildx pushed provenance; nor is (a) or (b) a reading of the registry.

## H. F-39 measurement state (G(i) PARTIAL; G(ii)–(v) NOT STARTED)

**No two independent builds of one commit were produced**, so the structural comparison
the step calls for does not exist and G(i) is PARTIAL, not DONE: this machine's container
engine is not running (`docker info` → `failed to connect to the docker API at
npipe:////./pipe/dockerDesktopLinuxEngine`, checked 2026-09-21; CLI 29.6.1 present) and a
CI comparison job needs a push.

**Allowed claims, which are all that is claimed:**

- The existing evidence is **insufficient to establish reproducibility**, in either
  direction, for all five images.
- The captured same-SHA pairs are **not two independent builds**: each is one build's
  record downloaded twice from two jobs of one run (byte-identical: `6d6f5a6f…`,
  `f7b19104…`). Every genuine cross-build pair differs in `source_revision` as well.
- The required **structural metadata was not recorded**: of 15 comparison fields, ten are
  absent from all 17 records — layer compressed digests, layer diff IDs, platform manifest
  digest, index/manifest-list composition, SBOM descriptor, provenance descriptor, buildx
  version, BuildKit version, builder endpoint, runner metadata. Present everywhere: config
  digest, base refs with digests, Dockerfile hash, context hash, `source_revision`; the
  index digest appears in the four buildx records.
- **Timestamps, package resolution, pip/Playwright, toolchain and attestation
  explanations all remain hypotheses.** Plan item G's five candidates are labelled so
  item by item: what the Dockerfiles make observable, versus what was never shown to move
  a digest. The mtime sentence now reads "source mtimes may influence build output
  depending on the pinned frontend/BuildKit behaviour — not demonstrated here", the
  attestation clause is marked as a definitional choice made because the pushed
  attestations were unread, and the CHANGELOG's "because nothing but build timestamps
  differs" says plainly that it was an inference.
- Two measured facts stand without interpretation: four images show byte-identical
  recorded inputs producing different config digests across commits; and all four
  `cap-sandbox-browser` records name their base as `cap-sandbox-http:1.0.6-rc1`, a mutable
  tag, where the other four record `@sha256:` bases.

No `SOURCE_DATE_EPOCH`, no package-resolution change, no Dockerfile edit, no pin. Full
detail: `docs/quality/cap-f39-reproducibility-measurement-2026-09-21.md`.

## I. F-25 documentation state (DEFERRED — no record written)

Stage 5's own precondition is V2, and V2 is unavailable: run `35553750674`'s published
digests and attestation state cannot be read without credentials, and the only local
capture from that run is a status-transition monitor log with no digests. A closure record
quoting numbers it cannot cite would be F-21 in a different file, so **nothing was
written**, and no PARTIAL document was passed off as the record.

What stands: **F-25 remains CLOSED by live run `35553750674`** — unchanged, not reopened,
not restated here. No sealed GitHub Release asset was touched; every change to
`docs/known-issues.md` and `CHANGELOG.md` in this batch is a HEAD-side record on the
post-rc line, and the shipped assets inside the tag remain as published. §K lists what the
record needs; §L puts it first in batch 2.

## J. Test results

Counted separately, and **skips and unverified items are not added to passes**.

| Invocation | PASSED | FAILED | SKIPPED | UNVERIFIED |
| --- | --- | --- | --- | --- |
| `test_release_publication_gate.py` — 37 functions, 48 items (20 functions added by batch 1), re-run at the final head | **46** | **0** | the 2 live Actions-API shape checks (no `gh` here) | the live half of the same file |
| `test_third_party_image_lock.py` — 19 functions, 21 items, on this tree | 21 | 0 | 0 | — |
| same module inside a fresh `git clone`, `outputs/` absent | 21 | 0 | 0 | — |
| same module in the clone with the tracked twin removed | 0 | 1 (expected: the guard bit) | 0 | — |
| `test_release_diff_classifier.py` + the release/contract/doc set (15 modules, incl. image completeness, build script, version consistency, certification workflow contract, deployment doc contract, quality-gate parity, GA preview mode, phase-23 RC, K8s report image set, compose env contract, socket boundary, capability disclosure) | **260** | **0** | 4 (credential-gated live checks) | — |
| CI-equivalent unit job — `ci.yml`'s own flags (5 container modules ignored, 28.2 500-run benchmark deselected, `--timeout=300 --timeout-method=thread`, `--cov=app --cov-fail-under=90`) | **1508** | **0** | **135**, plus 1 deselected by `ci.yml` itself — infra-gated tests this machine cannot serve | the live API checks, and anything needing the container services CI provides |
| 11 gate mutation controls | all 11 caught by their own tests | — | — | — |
| contradiction control (refusal not recorded → must not PASS) | ERROR, `publication would proceed: False` | — | — | — |
| 5 F-37 real-data controls + 1 fresh-clone control | all noticed by the test that claims each | — | — | — |
| 13-artifact read-only compatibility run | 6 certified / 7 refused / 0 misread | — | — | live run-listing resolution (see §K) |
| `ruff check` on both changed test modules | clean | — | — | — |

**UNVERIFIED-BY-CREDENTIALS, kept out of every PASS column:** pushed attestation content
(V1); published digests and attestation state of run `35553750674` (V2); the gate's live
resolution of certification runs by `head_sha`, which this batch did not change and which
was last exercised by the sealed publication run; the CI unit job's authoritative verdict,
which needs a push to produce.

## K. Deferred Batch-1 items

1. **B1/V1 + V2 — UNVERIFIED.** Needs a host with `gh` (or a package-scoped token) and TLS
   to `ghcr.io`/`api.github.com`. Blocks B3's assertion and the F-25 record.
2. **B2 — DEFERRED.** `setup-buildx-action@v3` unpinned by version in 4 jobs, BuildKit
   image unpinned. CI-only by category, but it changes the producer of any digest people
   pin, so it belongs in a round that re-certifies.
3. **E2 — DEFERRED.** No tracked registry-resolution generator exists.
4. **E3 — DEFERRED.** The five `provenance.evidence` strings still name an ignored path;
   repointing is `deployment/` → runtime-affecting. Until it lands, F-37 stays OPEN and the
   guard stays transitional.
5. **G(i) — PARTIAL.** The two-independent-build comparison was never produced: no engine
   here, and the CI job needs a push. Its prerequisite is the ten-field evidence extension
   plus one commit built twice.
6. **H/F-25 — DEFERRED.** Record not written; blocked on V2.
7. **Live CI verdict for this batch — UNVERIFIED.** Everything in §J ran on this machine.

## L. Batch-2 prerequisites (Batch 2 not started)

1. Credentialed read-only access for §K item 1, **before** any B3 assertion is written; the
   sealed tag and its images stay out of scope.
2. A written decision on the reproducibility model (which digest is the claim) before any
   deterministic-metadata change is proposed.
3. One branch carrying C (compose pinning) + D (schema widening) + E3 (repoint) + B2/B3/B4,
   so a single mandatory round — CI + Linux + K8s + 2 h soak + strict GA at one SHA,
   ≈ 3.5 h serial — pays for them; the classifier cost is identical, so splitting them
   buys rounds, not safety.
4. E2 in the same round, so F-37's remaining half becomes unnecessary rather than merely
   repointed; then the F-37 guard tightens to a direct tracked pointer (§F).
5. Dispatch discipline unchanged: soak first, strict GA only after it succeeds at the same
   SHA from a pinned ref. The new gate now refuses a GA round whose artifact says
   development, so an unpaired soak surfaces as a blocked release instead of an inherited
   green tick.
6. Finish G(i) as the opening step of the reproducibility batch (plan batch 4).
7. Before any push: decide that batch 1's ten commits go to `main`, and confirm no
   operation moves `release/1.0.6-rc1` or either tag.

## M. Working-tree and sealed-release integrity

Verified at `107867f`, the last commit before this report; the working tree was clean at that
point and nothing was pushed. This file's own commit is deliberately not named here, so the
statement cannot go stale by being committed:

| Item | State | How |
| --- | --- | --- |
| `v1.0.6-rc1` tag | **untouched** — `d0c7c7dc14cb075c095bd7970650b180a73afbf1` both locally and on the remote | `git for-each-ref`; `git ls-remote origin refs/tags/v1.0.6-rc1` |
| tag object | unchanged, and still a lightweight-annotated `tag` object whose `object` is `4d8f9c72b72dcea74e8588374fe8ef3a04564eb6`, `type commit`, `tag v1.0.6-rc1` | `git cat-file -t/-p` |
| `RELEASE_SHA` | unchanged (`4d8f9c7…`); `origin/release/1.0.6-rc1` still points at exactly it | `git ls-remote origin`, `git rev-parse` |
| `v1.0.5` stable line | unchanged: tag object `b5c4eeea…` → commit `32ec298…`, which is still `origin/main` | `git cat-file`, `git ls-remote origin refs/heads/main` |
| published `1.0.6-rc1` images | **untouched, and not mutable from here**: no container engine running, no registry credential, no `gh`; no push/retag/delete was attempted or possible | §G probes; `docker info` failure |
| GitHub Release and its assets | **not modified by this batch**; the API could not be read either, so their current bytes are **UNVERIFIED** rather than re-confirmed | no write path exists without credentials |
| `/releases/latest` | not touched; structurally it is unaffected by this batch, since `release.yml` maps `*-rc` tags to `--prerelease` and the newest GA tag remains `v1.0.5` — GitHub's own `latest` flag is **UNVERIFIED** here (no API access) | `release.yml:41-45`; `git tag --points-at` |
| commits made | 10, all on local `main` above the sealed line; `git status --short` empty; no push, no tag, no dispatch, no workflow rerun | `git log`, `git status` |

## N. FINAL VERDICT

Batch 1's auditable state: ten commits, all classified INHERITED with
`runtime_affecting=false` and no blocking files, so the CI-only contract held; the F-33
publication-safety proof complete across all ten enumerated cases, with the
refusal-to-PASS route closed structurally and measured by mutation; the F-37 guard named
as the transitional contract it is and demonstrated inside a fresh clone; F-39 kept
inside its evidence with the two-build comparison marked PARTIAL rather than completed;
V1/V2 and the F-25 record reported as UNVERIFIED and DEFERRED rather than as done; and
the sealed publication state verified untouched in every respect that can be checked from
here, with the rest labelled UNVERIFIED instead of rewritten.

The conditions for READY are each met on the evidence above: unresolved items are labelled
UNVERIFIED or DEFERRED rather than completed (§A, §G, §H, §I, §K); the F-33 publication-safety
proof covers all ten enumerated cases with the refusal-to-PASS route closed structurally and
measured by mutation (§E); the classifier allows inheritance for all eleven commits and all three
cumulative ranges, with no runtime-affecting path and the stop condition never triggered (§D);
the required local regression suites pass — 1508 / 260 / 46 with zero failures — and coverage
clears CI's floor (§J); the sealed release is untouched in every respect checkable from here and
labelled UNVERIFIED where it is not (§M); and no PARTIAL item is reported as DONE — G(i), B2,
E2, E3 and H say so themselves (§A, §F, §G, §H, §K).

**FINAL VERDICT: READY FOR REVIEW / PUSH**

Nothing was pushed. Batch 2 was not started.
