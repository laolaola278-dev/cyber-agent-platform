# CAP POST-RC HARDENING — BATCH 1 CLOSURE AND PUSH-READINESS AUDIT

Date 2026-09-21. Purpose: decide whether the staged batch-1 work is `READY FOR REVIEW / PUSH`
or `NOT READY`. No new hardening scope was added in this round; every number below was
re-measured against the current head, and the round's prohibitions were honoured: **no push, no
new tag, no Batch 2, no action against the sealed `v1.0.6-rc1` publication state, and
`scripts/release/classify_diff.py` unmodified** (`git log 4d8f9c7..HEAD --
scripts/release/classify_diff.py` → empty).

## A. Scope and status

Scope is frozen at what batch 1 landed. Nothing was widened, and no unfinished item was
re-scoped to make it look finished.

Status vocabulary, with attempted ≠ done:

| Marker | Meaning |
| --- | --- |
| DONE | the work exists, is committed, and is executed-tested here |
| PARTIAL | some of the step's deliverable exists; the step's own acceptance criterion is unmet |
| DEFERRED | not started, with the reason and the round it belongs to |
| UNVERIFIED | could not be observed from this environment; no claim made in either direction |
| PENDING | work that only a remote system can perform; expected, not a failure |

Accepted states, as frozen for this audit:

| Item | State | What that does and does not mean |
| --- | --- | --- |
| **A / F-33** — release gate reads the round's own verdict artifact | implementation **DONE** · local contract validation **DONE** · remote GitHub Actions validation **PENDING** | the gate code, its 48-item test module and its mutation controls exist and pass here; no Actions run has executed *this* gate yet (§E, §K) |
| **B1 / V1** — read the sealed pushed attestations | **UNVERIFIED** | no `gh`, no token, anonymous ghcr read returns 404 for the RC tag; nothing claimed in either direction (§G) |
| **V2** — read run `35553750674`'s published state | **UNVERIFIED** | same cause; blocks H (§I) |
| **B2** — pin buildx/BuildKit | **DEFERRED** | 4 `setup-buildx-action@v3` refs still carry no `buildx-version` (§G, §L) |
| **B3 / B4** — assert builder identity / Sigstore | **NOT STARTED**, and not executed in this round | B3 waits behind V1 and a written contract; B4 is an independent decision (§L) |
| **E1** — F-37 evidence-pointer guard | **DONE as a transitional tracked-twin contract** | the guard is real and executed, including inside a fresh clone; it is *not* the final "cited pointer is tracked" rule (§F) |
| **E2 / E3** — tracked generator / repoint | **DEFERRED** | E3 is a `deployment/` edit, runtime-affecting by the classifier (§F, §L) |
| **G(i)** — F-39 two-independent-build comparison | **PARTIAL** | the element-by-element measurement exists; the comparison itself does not (§H) |
| **H / F-25** — publication closure record | **DEFERRED** | blocked on V2; no PARTIAL document stood in for it (§I) |

These unfinished items do **not** block push-readiness, and none of them is described as
PASS or DONE anywhere in this report, in the plan, in `docs/known-issues.md` or in the
`CHANGELOG.md`.

## B. Staged commit graph

Fifteen linear commits on local `main`, each on top of the previous, above the sealed line
`4d8f9c7`. No merge, no rebase, no rewrite of published history; this report is the
sixteenth and touches only this file.

```
32ec298 (origin/main, v1.0.5)  …  4d8f9c7 (sealed v1.0.6-rc1, release/1.0.6-rc1)
                                     │
   5f79950 ─ c7687c9 ─ d4069d6 ─ e47b6b4 ─ 05a1553 ─ 036d389 ─ 2ce885b ─ 366a06d
                                     │
   623e820 ─ c31d21d ─ 107867f ─ 21627a9 ─ 66add1c ─ 656de2c ─ b07ba7d (HEAD, main)
```

| # | Commit | Parent | Bucket | Content |
| --- | --- | --- | --- | --- |
| 1 | `5f79950` | `4d8f9c7` | **A/F-33** | gate reads the round's own verdict artifact instead of job colours |
| 2 | `c7687c9` | `5f79950` | **E/F-37** | cited evidence must be readable from the clone; absence justified by name |
| 3 | `d4069d6` | `c7687c9` | Docs | provenance observation: the probes and the UNVERIFIED verdict |
| 4 | `e47b6b4` | `d4069d6` | **G/F-39** | F-39 element-by-element measurement + the reviewed plan |
| 5 | `05a1553` | `e47b6b4` | Docs | prose the batch made false, corrected; CHANGELOG; batch-1 execution report |
| 6 | `036d389` | `05a1553` | Docs | the execution report's own tables re-measured against its head |
| 7 | `2ce885b` | `036d389` | **A/F-33** | `decide()`: the last route from a recorded refusal to a published PASS, closed |
| 8 | `366a06d` | `2ce885b` | **E/F-37** | the guard named as transitional; fresh-clone proof; index-vs-checkout diagnosis |
| 9 | `623e820` | `366a06d` | **G/F-39** | every F-39 cause kept a hypothesis, in the notes as well as the report |
| 10 | `c31d21d` | `623e820` | Docs | the status model applied to the plan and the execution report |
| 11 | `107867f` | `c31d21d` | **A/F-33** | the last way a verdict could be optional: unconditional job and step, pinned |
| 12 | `21627a9` | `107867f` | Docs | the batch-1 closure report (sections A–N at the time) |
| 13 | `66add1c` | `21627a9` | Docs | the closure report counted among the commits it audits |
| 14 | `656de2c` | `66add1c` | Docs | counts anchored to an audited commit instead of a moving HEAD |
| 15 | `b07ba7d` | `656de2c` | **G/H** | reproducibility model marked PROPOSED; B4 split from the B3 chain |
| 16 | this report | `b07ba7d` | Docs | this push-readiness audit |

The last code/test commit is `107867f`; commits 12–16 change documents only, so the audited
code state is `107867f` and every test result below was produced on it (or re-run since).

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
| `66add1c` | `docs/quality/cap-post-rc-batch-1-closure-2026-09-21.md` |
| `656de2c` | `docs/quality/cap-post-rc-batch-1-closure-2026-09-21.md` |
| `b07ba7d` | `docs/quality/cap-f39-reproducibility-measurement-2026-09-21.md`; `docs/quality/cap-post-1.0.6-rc1-hardening-plan.md` |
| this report | `docs/quality/cap-post-rc-batch-1-closure-2026-09-21.md` |

Cumulative `4d8f9c7` → HEAD: **11 distinct files** — one workflow, two test modules, eight
documents. Nothing under `deployment/`, `backend/app/`, `frontend/src/`, `sdk/`, any Dockerfile,
`VERSION`, `pyproject.toml` or a lockfile was touched
(`git diff --stat 4d8f9c7..HEAD -- scripts/release/ deployment/ backend/app/ VERSION pyproject.toml uv.lock`
→ empty).

## D. Classifier evidence per commit

`scripts/release/classify_diff.py <parent> <commit>` run as a separate process for every one of
the fifteen hops, plus three cumulative ranges. Transcript: `_tmp/classifier_audit_push_ready.log`
(machine-generated table, "commits audited: 15 | exceptions: none"). The classifier is the
authority; it was not edited, and no verdict here was assigned by hand from a path pattern.

`release_metadata_only` is the classifier's own property and its name is narrower than its
definition: it means **every changed path falls in an inheritable category** — not "version bump
only". `runtime_affecting` is the blocking property.

| Commit | Parent | Changed files | Categories | runtime_affecting | release_metadata_only | blocking_files | diff verdict |
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
| `66add1c` | `21627a9` | 1 | `docs` | False | True | none | INHERITED |
| `656de2c` | `66add1c` | 1 | `docs` | False | True | none | INHERITED |
| `b07ba7d` | `656de2c` | 2 | `docs` ×2 | False | True | none | INHERITED |
| **cumulative** `4d8f9c7` → `b07ba7d` | | 11 | `ci_workflow` 1, `test_harness` 2, `docs` 8 | False | True | none | **INHERITED** |
| **cumulative** `b671f53` → `b07ba7d` (Linux/GA/soak certified line) | | 15 | adds the closure round's own `docs`/`test_harness` | False | True | none | **INHERITED** |
| **cumulative** `d30b4e7` → `b07ba7d` (K8s certified line) | | 14 | | False | True | none | **INHERITED** |

Stop condition — any commit `RECERTIFICATION_REQUIRED` or `runtime_affecting=true` → **FINAL
VERDICT = NOT READY**: **not triggered.** Fifteen per-commit verdicts and three cumulative
ranges all come back INHERITED with `runtime_affecting=false`, `release_metadata_only=true` and
no blocking files. Two consequences: batch 1 kept its CI-only contract, and a tag cut later at
this head would still resolve its certification from `b671f53` and `d30b4e7` rather than needing
a fresh round. §N records the one range that is *not* inheritable and why that is expected.

## E. F-33 safety proof

**The invariant under test:** PASS is reachable only when `failures == []` **and** no error
signal exists. `decide(evidence, failures)` is the single place the verdict is computed —
`failures` non-empty → `FAIL`; `failures` empty but any recorded authority verdict ≠ `PASS`, or
any recorded diff verdict ≠ `INHERITED` → `RuntimeError`, which the bottom handler turns into
`verdict: ERROR` and a re-raise. An empty `failures` list is therefore not a licence: the verdict
is cross-checked against every signal the gate recorded.

Each of the twelve required states, with its route, the verdict it produces and the test that
holds it:

| # | Condition | Route out of the gate | Verdict | Step exit | Proven by |
| --- | --- | --- | --- | --- | --- |
| 1 | `AuthorityRejected` (structural) | exception → `rejects[]` → `failures.append` | FAIL | 1 | `test_an_absent_authoritative_artifact_refuses_the_release`, `test_an_artifact_holding_two_verdict_files_is_ambiguous_too`, matrix `[absent artifact]`, `[two verdict files in one artifact]` |
| 2 | `RuntimeError` anywhere in `main()` | escapes → handler writes ERROR → re-raise | ERROR | 1 | `test_api_failure_raises_instead_of_looking_like_absent_evidence`, `test_error_and_fail_refuse_publication_by_different_routes`; measured §E↓ |
| 3 | artifact download / API error | `gh_bytes`/`gh` raise → same route as 2 | ERROR | 1 | `test_a_failed_artifact_download_says_could_not_decide_not_uncertified`; measured `error` scenario |
| 4 | malformed JSON | `AuthorityRejected` → `rejects[]` → `failures` | FAIL | 1 | `test_an_unparseable_authoritative_artifact_fails_closed`, matrix `[malformed json]` |
| 5 | missing artifact | `AuthorityRejected` | FAIL | 1 | matrix `[absent artifact]` |
| 6 | duplicate artifact (two with one name) | `AuthorityRejected` — not resolved by guessing | FAIL | 1 | `test_two_artifacts_with_one_name_are_not_resolved_by_guessing`, matrix `[duplicated artifact]` |
| 7 | expired artifact | `AuthorityRejected` | FAIL | 1 | `test_an_expired_artifact_cannot_be_read_back`, matrix `[expired artifact]` |
| 8 | wrong commit (`commit` ≠ run `head_sha`) | content `rejects[]` | FAIL | 1 | `test_an_artifact_describing_another_commit_is_not_this_run_s_verdict`, matrix `[wrong commit]` |
| 9 | development mode (`mode != "final-strict"`) | content `rejects[]`, 4–5 reasons at once | FAIL | 1 | `test_a_green_development_mode_ga_round_is_not_ga_certification`, matrix `[development mode]` |
| 10 | `planned` / `not_run` / `skipped` / `failed` ≠ 0, or a count the artifact does not report | content `rejects[]` ("absent count is not zero") | FAIL | 1 | `test_a_strict_round_that_left_gates_planned_or_failing_is_refused`, `test_a_k8s_round_with_unrun_gates_is_refused_by_the_same_rule`, `test_a_missing_gate_count_is_not_read_as_the_zero_it_should_have_been`, `test_gates_that_are_counted_nowhere_are_still_not_passed`, matrix `[planned gates]`, `[failing gates]`, `[a count the artifact does not report]` |
| 11 | `passed != total` | content `rejects[]`, judged inside the artifact's own table (`release.yml:365`) | FAIL | 1 | `test_a_strict_round_that_left_gates_planned_or_failing_is_refused` (39/40, 35/40); the `passed == total`-dropped control reddens 3 tests |
| 12 | authoritative boolean not actual JSON `true` | content `rejects[]`, type-strict comparison | FAIL | 1 | `test_full_ga_certified_is_read_as_stored_rather_than_reinterpreted` (`1`, `"true"`, `"True"` all refused); the type-strict control reddens 1 test |

**The named regression, executed.** `failures == []` with an error signal must not pass. Run as a
real child interpreter per scenario, with the Actions API answered from fixtures and the verdict
read back from the evidence file the process wrote — so the *process exit code*, which is what
Actions uses to decide publication, is measured rather than asserted about. Transcript
`_tmp/gate_exit_codes_push_ready.log`, at this head:

```
pass    exit=  0 (expected 0)  verdict=PASS          evidence_written=True  OK
fail    exit=  1 (expected 1)  verdict=FAIL          evidence_written=True  OK
error   exit=  1 (expected 1)  verdict=ERROR         evidence_written=True  OK
contra  exit=  1 (expected 1)  verdict=ERROR         evidence_written=True  OK
4/4 scenarios as expected
```

`error` is the download failure (`failures` never gets a chance to fill). `contra` is the
contradiction built by mutation: the `failures.append(f"{workflow}: {reason}")` line was deleted
in memory, so the gate records an authority refusal with an empty `failures` list — the state the
invariant forbids. It exits 1 with `verdict: ERROR`, because `decide()` raises rather than
returning PASS. No renaming of ERROR into FAIL was involved, and the fix was structural (one
verdict computation) rather than cosmetic.

**FAIL and ERROR are both terminal, for different reasons.** FAIL = the gate read evidence saying
uncertified. ERROR = the gate could not read evidence. Both keep their own diagnosis in the
artifact, and both stop publication. The structural half is pinned, not assumed: no
`continue-on-error` on the gate job or any of its steps, no `if:` that could make the gate or its
verdict step conditional, the job's only prerequisite is `validate-tag`, the step's script does not
end in `|| true`, and it ends by raising `SystemExit(GATE_EXIT)` —
`test_nothing_in_the_gate_job_tolerates_its_own_failure`. Adding `continue-on-error: true` to the
gate step was verified to turn that test red and name the offender.

**What the gate requires** (the `AUTHORITY` table, drift-checked against the test module's copy in
every evidence file):

| Workflow | Artifact | Member | Commit binding | Exact fields | Zero counts | Completeness |
| --- | --- | --- | --- | --- | --- | --- |
| `cap-ga-certification.yml` | `ga-cert-artifacts` | `cap-cert-ga/cap-28.7-ga-certification.json` | `commit` == selected run `head_sha` | `mode == "final-strict"`, `full_ga_certified is True` — read as stored, never recomputed from junit or job colours | `failed`, `not_run`, `skipped`, `planned` | `passed == total` |
| `cap-k8s-certification.yml` | `k8s-cert-artifacts` | `cap-28.6-k8s-certification.json` | `commit` == selected run `head_sha` | none (the artifact publishes no `mode`) | `failed`, `not_run` | `passed == total` |

No gate count is hardcoded: real rounds recorded 32/32, 33/33 and 34/34 and all certify — pinned
by `test_an_older_round_with_a_smaller_gate_table_still_certifies`.

**Controls.** Eleven guard-by-guard mutations of the gate source, each caught by its own test and
none unnoticed, `release.yml` restored byte-for-byte after each: authority step never runs (14
tests), type-strict flag (1), commit binding (1), duplicate artifacts (1), duplicate zip members
(1), expired artifact (1), absent count read as zero (1), `passed == total` dropped (3),
unreadable artifact (1), refusal not recorded as a failure (23, and still never a PASS),
`decide()` cross-check disabled (1). Transcript `_tmp/f33_control_final.log`.

**Real-evidence compatibility, read-only, re-run after `decide()`:** 13 artifacts from sealed and
superseded rounds — 6 certified, 7 refused, **0 read the wrong way** — including the
development-mode soak artifact at the strict round's own SHA and three strict-but-incomplete
rounds (`_tmp/f33_compat_final.log`). What this does **not** cover is the live resolution of
certification runs by `head_sha` on GitHub's side: §K.

## F. F-37 transitional contract

**The contract as implemented, in one line:** a cited path that is ignored or absent is allowed
**temporarily** when a tracked twin exists under `docs/quality/artifacts/` and both copies state
the same digest claims; anything else fails. The seven required behaviours, each executed:

| Required case | Result | Evidence |
| --- | --- | --- |
| ignored/absent cited path + valid tracked twin | PASS | `test_cited_evidence_resolves_to_a_tracked_copy_of_the_same_measurement`, on the real five lock entries |
| missing twin | FAIL | `test_pointer_checker_refuses_a_claim_no_clone_can_read`; real-data control "the cited file is the only copy of the measurement" |
| twin digest mismatch | FAIL | `test_pointer_checker_notices_a_diverged_copy_but_not_a_different_date`; controls "measures something else", "a digest only one copy asserts" |
| tracked-in-index but absent from the checkout | FAIL, with its own diagnosis | `test_…_refuses_a_tracked_twin_that_proves_nothing` (both branches); found by running the module in a real clone with the twin moved |
| unsupported evidence absence | FAIL | `test_justification_checker_notices_a_new_silent_entry`; control "an entry cites nothing and says nothing" |
| allow-set silent growth | FAIL | `test_an_entry_with_no_evidence_pointer_justifies_itself_inline` asserts the table's keys == the lock's own no-evidence entries |
| fresh clone | PASS | `git clone` into scratch, `outputs/` absent entirely: 21 passed; red again when the tracked twin is removed inside that clone, naming the file and the pointer it stands for |

Absence semantics were settled before requiring anything: `evidence` exists on exactly the five
base-image entries; `minio-object-store` and `postgres` justify themselves with fields that
already exist (`image.provenance.vendor_signed_tag` + `open_gap`; `lock.policy.postgres`). No new
lock field was invented, so no schema change was needed and no convention was asserted that the
code does not enforce.

**Stale-text check.** `grep -rn "untracked" docs/ CHANGELOG.md
backend/tests/test_third_party_image_lock.py` → the only F-37-relevant hits are
`cap-post-1.0.6-rc1-hardening-plan.md:220` ("the sentence … **is not this repository's contract
and no document states it as one**"), `:249` (the Validation heading naming the replaced sentence)
and this file's §F. Each is a negation or a supersession note; **no document states "an untracked
pointer itself must fail" as the current contract.** The test module's own section heading says
the tracked-twin rule is transitional and that E3 replaces it.

**Not done:** E2 (no tracked registry-resolution generator exists) and E3 (the five
`provenance.evidence` strings still name `outputs/artifact-closure/registry-base-digests.json`,
which no fresh clone contains). F-37 stays OPEN by design, not by an omitted note: rewriting those
strings is a `deployment/` edit and the classifier charges that as runtime-affecting.

## G. Provenance V1/V2 status

**V1 — UNVERIFIED; V2 — UNVERIFIED.** Stage 2 was read-only and it did not answer. Probes, in
`docs/quality/cap-provenance-identity-observation-2026-09-21.md`: no `gh` binary in `PATH` or any
conventional install location; none of `GH_TOKEN` / `GITHUB_TOKEN` / `CR_PAT` / `GITHUB_PAT` /
`REGISTRY_TOKEN` / `DOCKER_PASSWORD` set; `~/.config/gh` and `~/.netrc` absent;
`~/.docker/config.json` with `"auths": {}`, `credsStore: desktop` and no
`docker-credential-desktop` helper installed; the anonymous ghcr token endpoint answers HTTP 200
with a 68-character anonymous token, and
`GET /v2/laolaola278-dev/cap-backend/manifests/v1.0.6-rc1` answers **404** — which is ghcr's
answer to both "absent" and "not visible to you".

Therefore nothing is claimed about `predicateType`, the pushed `subject[].digest`, `buildType`,
`runDetails.builder.id`, the invocation/source/revision fields, materials, or the buildx/BuildKit
versions that produced the sealed images — **in either direction**. No builder-id field, pattern or
regex exists in any executable file
(`grep -rn "builder.id|builder_id|builder-id" --include=*.py --include=*.sh --include=*.yml` →
nothing outside prose); nothing was inferred from Docker's documentation, and no contract was
written for a value nobody has read.

**What is observable, each with its limit stated where it is written:** (a) the build runs
`--provenance=true --sbom=true --push` with buildx defaults
(`scripts/release/build_release_image.sh:137`); (b) the evidence's
`attestations.{sbom,provenance}` booleans are derived from those flags (`:217-219`) and the
completeness gate asserts *that* field (`release.yml`, "no `{kind}` attestation") — so what stands
in front of publication today is a self-report about a command line; (c) all 17 captured build
records are dry builds (`pushed: false`) and the rounds' `cap.provenance.v1` artifact has no
predicate, `runDetails` or invocation field — CAP's summary of a build, **not** substitute
evidence for OCI/buildx pushed provenance.

**B2 — DEFERRED with nothing pinned:** all four `docker/setup-buildx-action@v3` references carry
no `buildx-version`, and the BuildKit image behind the `docker-container` driver is unpinned.
**B3 and B4 were not executed** in this round, and no registry content was mutated.

## H. F-39 status

**G(i) = PARTIAL.** No two independent builds of one commit were produced, so the structural
comparison the step calls for does not exist. Reason, stated not hidden: this machine's container
engine is not running (`docker info` → `failed to connect to the docker API at
npipe:////./pipe/dockerDesktopLinuxEngine`, checked 2026-09-21; CLI 29.6.1 present), and a CI
comparison job needs a push.

The reproducibility target is labelled **PROPOSED MODEL, not established policy**, in
`docs/quality/cap-f39-reproducibility-measurement-2026-09-21.md` §4 (heading: "The reproducibility
model is PROPOSED, not policy") and in plan item G. Concretely: **no "timestamps-normalised config
digest" — and no other digest-selection rule — is fixed as a gate contract** anywhere in this
commit set, because the two-build structural comparison that would justify one has not been run.
The acceptance spec in §5 of the measurement note is a spec, and says so.

Claims allowed, which are all that is claimed:

- The existing evidence is **insufficient to establish reproducibility**, in either direction, for
  all five images.
- The captured same-SHA pairs are **not two independent builds**: each is one build's record
  downloaded twice from two jobs of one run (byte-identical: `6d6f5a6f…`, `f7b19104…`). Every
  genuine cross-build pair differs in `source_revision` as well.
- The required **structural metadata was not recorded**: of 15 comparison fields, ten are absent
  from all 17 records — layer compressed digests, layer diff IDs, platform manifest digest,
  index/manifest-list composition, SBOM descriptor, provenance descriptor, buildx version,
  BuildKit version, builder endpoint, runner metadata. Present everywhere: config digest, base
  refs with digests, Dockerfile hash, context hash, `source_revision`; the index digest appears in
  the four buildx records.
- **Timestamps, package resolution, pip/Playwright, toolchain and attestation explanations all
  remain hypotheses.** Plan item G's five candidates are labelled item by item for what the
  Dockerfiles make observable versus what was never shown to move a digest; the mtime sentence
  reads "may influence build output depending on the pinned frontend/BuildKit behaviour — not
  demonstrated here"; the attestation clause is marked as a definitional choice made because the
  pushed attestations were unread; the CHANGELOG's "because nothing but build timestamps differs"
  is now labelled an inference.
- Two measured facts stand without interpretation: four images show byte-identical recorded inputs
  producing different config digests across commits; and all four `cap-sandbox-browser` records
  name their base as `cap-sandbox-http:1.0.6-rc1`, a mutable tag, where the other four record
  `@sha256:` bases.

No `SOURCE_DATE_EPOCH`, no package-resolution change, no Dockerfile edit, no pin.

## I. F-25 / H status

**H = DEFERRED; no record was written.** Stage 5's own precondition is V2, and V2 is unavailable:
run `35553750674`'s published digests and attestation state cannot be read without credentials,
and the only local capture from that run is a status-transition monitor log containing no
digests. A closure record quoting numbers it cannot cite would be the F-21 failure mode in a new
file, so nothing was written and no PARTIAL draft was passed off as the record.

What stands: **F-25 remains CLOSED by live run `35553750674`** — unchanged, not reopened, not
restated here. No sealed GitHub Release asset was touched; every change to `docs/known-issues.md`
and `CHANGELOG.md` in this batch is a HEAD-side record on the post-rc line, and the assets shipped
inside the tag remain as published. §L lists what the record needs; §L puts it first in batch 2.

## J. Exact test results

Counted from machine-parsed transcripts (pytest junit-xml), and **skips are never added to
passes**. There are no xfail markers in this set: `xfailed` and `xpassed` are absent from the
junit attributes, which is `0`, not "unreported".

Re-run at this head; transcript `_tmp/push_ready_regression.xml`:
**264 collected · 260 passed · 0 failed · 0 errors · 4 skipped · 0 xfailed · 0 xpassed · 96.6 s.**

| Module (15 in the set) | Items | Passed | Failed | Skipped |
| --- | --- | --- | --- | --- |
| `test_release_publication_gate.py` | 48 | 46 | 0 | 2 — live Actions-API checks (§K) |
| `test_release_diff_classifier.py` | 57 | 57 | 0 | 0 |
| `test_release_image_completeness.py` | 27 | 27 | 0 | 0 |
| `test_certification_workflow_contract.py` | 27 | 25 | 0 | 2 — `ci.yml` / `release.yml` "not dispatchable", by design |
| `test_third_party_image_lock.py` | 21 | 21 | 0 | 0 |
| `test_phase_28_8_capability_disclosure.py` | 20 | 20 | 0 | 0 |
| `test_release_build_script.py` | 12 | 12 | 0 | 0 |
| `test_ga_report_preview_mode.py` | 9 | 9 | 0 | 0 |
| `test_compose_env_contract.py` | 8 | 8 | 0 | 0 |
| `test_phase_23_release_candidate.py` | 7 | 7 | 0 | 0 |
| `test_k8s_report_image_set.py` | 7 | 7 | 0 | 0 |
| `test_deployment_doc_contract.py` | 6 | 6 | 0 | 0 |
| `test_sandbox_socket_boundary.py` | 6 | 6 | 0 | 0 |
| `test_quality_gate_parity.py` | 5 | 5 | 0 | 0 |
| `test_release_version_consistency.py` | 4 | 4 | 0 | 0 |

| Other run | Passed | Failed | Skipped / deselected | Xfailed | Notes |
| --- | --- | --- | --- | --- | --- |
| CI-equivalent unit job — `ci.yml`'s own flags (5 container modules ignored, the 28.2 500-run benchmark deselected, `--timeout=300 --timeout-method=thread`, `--cov=app --cov-fail-under=90`) | **1508** | **0** | **135** skipped + 1 deselected by `ci.yml` itself | 0 | `_tmp/ci_unit_final.log`; exit 0; coverage **91.85%** against CI's 90% floor; run on the `107867f` code state, which is the code state of this head |
| `test_third_party_image_lock.py` inside a fresh `git clone` (`outputs/` absent) | 21 | 0 | 0 | 0 | the transitional contract resolves from tracked files alone |
| same, in the clone with the tracked twin removed | 0 | 1 (expected) | 0 | 0 | the guard bites; names the file and the pointer |
| 11 gate mutation controls | all 11 caught by their own test | — | — | — | `_tmp/f33_control_final.log` |
| contradiction control (refusal recorded, no failure line) | ERROR, exit 1, `publication would proceed: False` | — | — | — | §E |
| 5 F-37 real-data controls + 1 fresh-clone control | all noticed by the test that claims each | — | — | — | |
| 13-artifact read-only compatibility run | 6 certified / 7 refused / 0 misread | — | — | — | §E |
| `ruff check` on both changed test modules | clean (exit 0) | — | — | — | re-run at this head |

**Kept out of every PASS column:** pushed attestation content (V1), the published state of run
`35553750674` (V2), the gate's live resolution of certification runs by `head_sha`, and the CI
unit job's authoritative verdict — all of which need a remote system (§K).

## K. Remote-CI validation PENDING

Batch 1's implementation is DONE and its local contract validation is DONE. Its **remote GitHub
Actions validation is PENDING**, and this round could not and did not perform it: no push was
made, no workflow was dispatched, and this machine has no `gh` and no token.

Exactly two tests in the set skip for credential reasons, and they are the reason the label
exists:

| Test | Skip message (junit, verbatim) |
| --- | --- |
| `test_release_publication_gate.py::test_the_authoritative_artifact_is_where_the_gate_reads_it` | "gh could not list GA certification runs from here" |
| `test_release_publication_gate.py::test_the_actions_api_paths_the_gate_uses_exist` | "gh could not answer the live Actions API here (no binary, no credential, a rate limit, or insufficient scopes)" |

The other two skips in the set are structural, not credential-gated
(`test_dispatchable_workflows_declare_a_job_that_can_actually_run[ci.yml]` / `[release.yml]` —
those workflows declare no `workflow_dispatch`).

Everything still to be observed remotely, listed as PENDING rather than assumed:

1. The `ci.yml` unit job's verdict for this head — everything in §J ran locally; CI's is the
   authoritative one.
2. The gate's live run-listing resolution: that it finds the release-scoped rounds by `head_sha`
   through the real API, with the real pagination and scopes. Unchanged by this batch, last
   exercised by the sealed publication run, and covered locally only by fixtures.
3. The two live-API shape checks above, which are precisely the tests that would run if `gh` were
   present.
4. A real gate execution against a tag — the only end-to-end proof that a FAIL or ERROR from this
   gate stops publication in Actions rather than in a test fixture. It is structurally pinned
   (§E) and cannot be produced without a push and a tag, both out of scope here.

## L. Deferred work and dependency graph

The provenance chain, corrected in `b07ba7d` and reflected in plan item B:

```
V1  authenticated read-only observation of pushed attestations   (UNVERIFIED — needs gh/token)
 ↓
    define the observed builder/provenance contract               (impossible before V1: nothing to pin)
 ↓
B3  assertion implementation (gate rejects a non-conforming builder id / provenance)

B2  pin buildx version + BuildKit image                          (CI-only by category; changes the
      producer of any digest anyone pins → belongs in a recert round)

B4  GitHub/Sigstore attestation                                  (INDEPENDENT security/design
      decision — NOT the automatic successor to B3)
```

| Item | State | Prerequisite | Why it is not in this batch |
| --- | --- | --- | --- |
| V1 / V2 | UNVERIFIED | a host with `gh` or a package-scoped token, and TLS to `ghcr.io` / `api.github.com` | blocks B3's assertion and the H record; nothing was guessed in its place |
| B2 | DEFERRED | V1 (what to pin) | its category is inheritable, but it changes the producer of pinned digests, so it belongs in a round that re-certifies |
| B3 | NOT STARTED, and correctly so | V1 → written contract | an assertion about a value nobody has read is a guess with a gate in front of it |
| B4 | NOT STARTED, independent decision | a product/security decision | not a follow-on task to B3; recorded as its own decision in plan item B |
| E2 | DEFERRED | — | the tracked generator was never implemented; its validation (identical digests on re-run) waits with it |
| E3 | DEFERRED | rides the C/D recert round | the repoint is a `deployment/` edit → runtime-affecting; until it lands, F-37 stays OPEN and the guard stays transitional |
| G(i) | PARTIAL | an engine, or a pushed comparison job | the ten-field evidence extension plus one commit built twice is what closes it |
| G(ii)–(v) | NOT STARTED | G(i), then a written reproducibility model | no deterministic-metadata change is proposed before the model is agreed |
| H / F-25 record | DEFERRED | V2 | a record that cannot cite its numbers is worse than none |
| F (24 h soak coverage) | unchanged, later batch | a recert round | plan batch 3 |

Batch 2 was not started. Its shape, per the plan: one branch carrying C (compose pinning) +
D (schema widening) + E3 + B2/B3/B4, paid for by a single mandatory round (CI + Linux + K8s +
2 h soak + strict GA at one SHA, ≈ 3.5 h serial), with H first as soon as V2 is available, and the
F-37 guard tightening to a direct tracked pointer once E3 lands.

## M. Sealed-release integrity

Re-verified read-only at this head (`b07ba7d`). Nothing was pushed at any point, and the working
tree is clean (`git status --short` → empty).

| Required check | State | How, and what was seen |
| --- | --- | --- |
| `v1.0.6-rc1` tag not modified | **untouched** — `d0c7c7dc14cb075c095bd7970650b180a73afbf1` locally **and** on the remote, identical | `git rev-parse v1.0.6-rc1`; `git ls-remote origin refs/tags/v1.0.6-rc1` |
| tag object not rewritten | unchanged annotated `tag` object whose `object` is `4d8f9c72b72dcea74e8588374fe8ef3a04564eb6`, `type commit`, `tag v1.0.6-rc1`, original tagger and timestamp | `git cat-file -t` / `-p` |
| `RELEASE_SHA` not modified | unchanged at `4d8f9c7…`; `origin/release/1.0.6-rc1` still points exactly there | `git ls-remote origin refs/heads/release/1.0.6-rc1` |
| `1.0.6-rc1` images not overwritten | **untouched, and not mutable from here**: no container engine running, no registry credential, no `gh`; no push, retag or delete attempted or possible | §G probes; `docker info` failure |
| sealed GitHub Release and its assets not modified | **no write path exists from here**; the API could not be read either, so their current bytes are **UNVERIFIED**, not re-confirmed | no credentials (§G); the distinction is recorded rather than glossed |
| no stable/latest promotion introduced | nothing touched `latest`; structurally unaffected by this batch — `release.yml` maps `*-rc` tags to `--prerelease`, and the newest GA tag is still `v1.0.5` (tag object `b5c4eeea…` → commit `32ec298…`, still `origin/main`). GitHub's own `latest` flag remains **UNVERIFIED** (no API access) | `release.yml:41-45`; `git tag --points-at`; `git cat-file` |
| no new tag created | 12 tags, none at HEAD, none added this round | `git tag \| wc -l`; `git tag --points-at HEAD` → empty |
| no commits to the sealed line | 15 commits, all local-only on `main`; `origin/main` is still `32ec298`, i.e. local is **133 commits ahead** — direct evidence that nothing was pushed | `git rev-list --count origin/main..HEAD`; `git ls-remote origin refs/heads/main` |
| no workflow rerun or dispatch, no registry mutation | none attempted | §G, §K |

Where a live state could not be read, it is recorded as UNVERIFIED rather than re-confirmed — the
instruction "网络不可用的 live state 不得伪报为重新验证 PASS" is applied to the Release assets and
the `latest` flag specifically.

## N. Push recommendation

**Recommended: push `main` (`32ec298` → `b07ba7d`) for review. Not performed.** What a push does
and does not mean, stated before the recommendation rather than after it:

- Batch 1's fifteen commits are individually INHERITED with `runtime_affecting=false`, and the
  ranges from the sealed line and both certified lines are INHERITED too (§D). The CI-only
  contract held, and a tag cut later at this head would still resolve its certification from
  `b671f53` and `d30b4e7`.
- The **whole** `origin/main → HEAD` range is not inheritable, and that is expected: `origin/main`
  is the v1.0.5 line, and the 133 commits in between are the 1.0.6-rc1 work — 150 files, 71 of them
  runtime-affecting (`production_runtime` 53, `deployment` 15, `test_harness` 33, `docs` 20, …), so
  the classifier answers RECERTIFICATION_REQUIRED for that range (`_tmp/classifier_origin_main_range.json`).
  That is not a batch-1 defect: it is the record that the rc1 line is certified where it was
  certified — at `4d8f9c7` by run `35553750674` — and that no new tag may be cut from the pushed
  head without deciding its own round.
- A push to `main` triggers `cap-ga-certification.yml` in **development mode** and the CI unit job.
  Those runs are the remote validation labelled PENDING in §K; a green development-mode GA round
  is exactly what this batch's gate now refuses as certification, so the new behaviour is
  observable in that run rather than assumed.
- Pushing changes no publication state: `v1.0.6-rc1`, its tag object, `release/1.0.6-rc1`, the
  published images and the sealed Release are all outside the operation (§M).
- Suggested review order: `5f79950` + `2ce885b` + `107867f` (the gate and its proofs, one story),
  then `c7687c9` + `366a06d` (F-37 transitional guard), then `d4069d6` / `e47b6b4` + `623e820` /
  `b07ba7d` (the three status records), then the documents that only relabel history
  (`05a1553`, `036d389`, `c31d21d`, `21627a9`, `66add1c`, `656de2c`).

## O. FINAL VERDICT

The READY conditions, each against the evidence above:

| Condition | Met? | Where |
| --- | --- | --- |
| every commit proposed for push judged INHERITED by the classifier | yes — 15 per-commit hops and 3 cumulative ranges | §D |
| `runtime_affecting=false` everywhere in the batch | yes — no blocking file in any range; stop condition never triggered | §D |
| required local regression tests with no failure | yes — 264 collected → 260 passed / 0 failed / 0 errors / 4 skipped / 0 xfailed; CI-equivalent 1508 passed / 0 failed / 135 skipped, coverage 91.85% over the 90% floor | §J |
| F-33 ERROR **and** FAIL both proven to block publication | yes — 12 states, each non-PASS; verdicts and process exit codes measured (0/PASS, 1/FAIL, 1/ERROR, 1/ERROR), including `failures=[]` + download failure → ERROR exit 1 | §E |
| partial / unverified / deferred items recorded accurately, none mislabelled PASS or DONE | yes — G(i) PARTIAL, V1/V2 UNVERIFIED, B2/E2/E3/H DEFERRED, remote CI PENDING | §A, §F–§K, §L |
| sealed release unmodified | yes — tag, tag object, `RELEASE_SHA`, `release/1.0.6-rc1`, images and `latest` mapping all unchanged; unreadable states labelled UNVERIFIED rather than re-confirmed | §M |

V1/V2, G(i) and H are unresolved and do **not** make this NOT READY: none of them is part of the
landed implementation's completion criteria, and each is recorded as UNVERIFIED, PARTIAL or
DEFERRED rather than done. The one thing that would have forced NOT READY — a commit the classifier
charges as runtime-affecting — did not occur.

**FINAL VERDICT: READY FOR REVIEW / PUSH**

Nothing was pushed. No tag was created. Batch 2 was not started.
