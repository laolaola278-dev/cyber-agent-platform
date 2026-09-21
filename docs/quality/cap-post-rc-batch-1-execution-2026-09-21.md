# CAP POST-RC HARDENING — BATCH 1 EXECUTION REPORT

Date: 2026-09-21. Executed: the five staged items of batch 1 as instructed, staged and
separately reviewable. Nothing was pushed. The sealed `v1.0.6-rc1` line was not touched
in any way: no tag moved, deleted or recreated, no image overwritten, no release asset
edited, no round rerun or rebuilt, no promotion.

Sealed identity used throughout: `VERSION=1.0.6-rc1`, tag object
`d0c7c7dc14cb075c095bd7970650b180a73afbf1` → `RELEASE_SHA 4d8f9c72b72dcea74e8588374fe8ef3a04564eb6`,
release run `35553750674`, F-25 CLOSED by that run.

## 1. Files changed, per commit

| Commit | Files | What it is |
| --- | --- | --- |
| `5f79950` | `.github/workflows/release.yml`, `backend/tests/test_release_publication_gate.py` | Stage 1 / **F-33**: the publication gate reads the certification round's own verdict artifact |
| `c7687c9` | `backend/tests/test_third_party_image_lock.py` | Stage 3 / **F-37 E1**: cited evidence must be readable from the clone; absence must be justified by name |
| `d4069d6` | `docs/quality/cap-provenance-identity-observation-2026-09-21.md` | Stage 2 / **B-V1, V2**: the attestation observation attempt and its UNVERIFIED verdict |
| `e47b6b4` | `docs/quality/cap-f39-reproducibility-measurement-2026-09-21.md`, `docs/quality/cap-post-1.0.6-rc1-hardening-plan.md` | Stage 4 / **G(i)** measurement; and the reviewed plan, with each item's executed outcome written back into it |
| *this commit* | `CHANGELOG.md`, `docs/known-issues.md`, `docs/quality/cap-1.0.6-rc1-artifact-closure-2026-09-20.md`, the measurement file, this report | Follow-through on finding 8: prose the batch made false, the F-39 attribution withdrawn against the measurement, two closure-report follow-up notes, the Unreleased CHANGELOG entries, and this report |

Stage 5 (**F-25 historical record**) produced no commit, because its own precondition —
"only after V2 available" — did not hold. See §8.

Measured with `git diff --numstat`: `5f79950` +836/−8, `c7687c9` +251/−2, `d4069d6`
+102/−0, `e47b6b4` +479/−0 — cumulatively **6 files changed, 1668 insertions, 10
deletions**. All ten deletions are prose lines the reworded comments and docstrings
replaced; nothing was deleted from any sealed artifact, and no file under `deployment/`,
`backend/app/`, `frontend/src/`, any Dockerfile, or `VERSION` was modified at all.

## 2. Tests executed

| Set | Result |
| --- | --- |
| `backend/tests/test_release_publication_gate.py` — 32 tests, 15 added in this batch | 30 passed, 2 skipped — the two skips are the live Actions-API shape checks (one pre-existing, one added here), which skip because `gh` is not installed |
| `backend/tests/test_third_party_image_lock.py` — 6 new test functions (21 collected items: one parametrised control contributes three cases) | 21 passed |
| The release/contract/doc neighbourhood at the batch-1 head — `test_release_image_completeness`, `test_release_build_script`, `test_release_version_consistency`, `test_certification_workflow_contract`, `test_third_party_image_lock`, `test_deployment_doc_contract`, `test_quality_gate_parity`, `test_release_diff_classifier`, `test_ga_report_preview_mode`, `test_phase_23_release_candidate`, `test_k8s_report_image_set`, `test_compose_env_contract`, `test_sandbox_socket_boundary`, `test_phase_28_8_capability_disclosure`, `test_release_publication_gate` — `test_release_version_consistency` reads `CHANGELOG.md` as a version carrier and `test_sandbox_socket_boundary` / `test_phase_28_8_capability_disclosure` read `docs/known-issues.md`, so this set covers the prose commit too | 244 passed, 4 skipped in 109 s, run alone on an idle machine |
| `ruff check` on both changed test modules | clean (line-length 100, `E,F,I,UP,B`) |
| CI's own unit invocation (`ci.yml`'s flags: five container modules `--ignore`d, the 28.2 500-run benchmark `--deselect`ed, `--timeout=300 --timeout-method=thread`, `--cov=app --cov-fail-under=90`) | **1492 passed, 135 skipped, 1 deselected, exit 0 in 30 m 42 s; coverage 91.91 % against the 90 % floor.** The 135 are the tests that skip when their infrastructure is not on this machine, not failures. Disclosed because it matters: part of this run overlapped a duplicate invocation started on a false alarm (finding 9), so it was not on an idle machine; it finished green regardless, and the doc/gate neighbourhood above was re-run afterwards alone. This batch adds no `app/` code — one workflow file, two test modules and documentation — so the coverage figure is unchanged in kind, and CI measures it authoritatively anyway |
| Negative controls, stage 1 (`_tmp/control_f33.py`) | 9 guard-by-guard mutations of the gate's own source; each was caught by exactly its own test(s) — authority step removed → 14 tests; type-strict flag → 1; commit binding → 1; duplicate artifacts → 1; duplicate zip members → 1; expired artifact → 1; absent count read as zero → 1; `passed == total` dropped → 3; unreadable artifact → 1. `release.yml` restored byte-for-byte afterwards |
| Negative controls, stage 3 (`_tmp/control_f37.py`) | 5 controls against the real tree — a tracked twin measuring something else, no tracked twin under the named roots, a cited path nothing copied, a digest only one copy asserts, an entry citing nothing and saying nothing — all noticed; both mutated files restored byte-for-byte and `git status` verified clean |
| Read-only compatibility check, stage 1 (`_tmp/compat_f33_sealed.py`) | 13 real artifacts from sealed and superseded rounds fed through the modified gate: **0 read the wrong way**. This covers the *new* half — the authority reader — on real bytes. The *resolution* half (`find_evidence`, `head_sha` probes, the ancestor walk) is unchanged by this batch and cannot be re-executed live from this machine at all, because `gh` is not installed. Its live record is the sealed publication run `35553750674`, which could not have published without `verify-certification` green and uploaded `cap-1.0.6-rc1-certification-evidence` as it went; that artifact predates this change, so it does not exercise the new code |

No test was deleted, skipped, loosened or mocked to get these results. The one skip
this batch adds is deliberate: `test_the_authoritative_artifact_is_where_the_gate_reads_it`
asks the live Actions API for a real GA run's artifact and skips when it cannot answer,
following the repository's rule that a network-touching check must never turn CI red for
want of a credential. On GitHub's own runners `gh` and a job token are present, so there —
and only there — these two live checks execute against the real endpoint, which is where
the artifact path and verdict-file shape get verified for real rather than by fixture.

## 3. Classifier verdict per commit (`scripts/release/classify_diff.py`)

| Range | Exit | Verdict | Categories |
| --- | --- | --- | --- |
| `4d8f9c7` → `5f79950` | 0 | INHERITED | `ci_workflow` 1, `test_harness` 1 |
| `5f79950` → `c7687c9` | 0 | INHERITED | `test_harness` 1 |
| `c7687c9` → `d4069d6` | 0 | INHERITED | `docs` 1 |
| `d4069d6` → `e47b6b4` | 0 | INHERITED | `docs` 2 |
| **cumulative** `4d8f9c7` → HEAD | 0 | **INHERITED**, `runtime_affecting=False`, 6 files | `ci_workflow` 1, `test_harness` 2, `docs` 3 |
| **cumulative** `b671f53` (last strict GA) → HEAD | 0 | **INHERITED**, `runtime_affecting=False`, 13 files | as above plus the closure round's own `docs`/`test_harness` |
| **cumulative** `d30b4e7` (last K8s round) → HEAD | 0 | INHERITED | |

Two halves to this, and they are measured differently. The *classification* half is
measured: `classify_diff.py` returns INHERITED for every commit above and for the
cumulative range, so nothing batch 1 changed can make a sealed round's certification
non-inheritable, and the batch's price is one CI cycle rather than a re-certification
round. The *evidence-resolution* half — that a tag at today's head would still find the
sealed rounds' artifacts and accept them — was exercised on 13 real artifacts (§2,
compat check) but against fixture run listings, because this machine cannot reach the
Actions API. It is not a live gate run, and §11 says so again.

## 4. F-33 status: closed, within the scope reviewed

`verify-certification` now reads, for the GA and K8s rounds, the artifact the selected
run uploaded, and requires: `mode == "final-strict"`, `full_ga_certified is True`
(read as stored, type-strict, never recomputed from junit or job colours), `commit`
equal to the selected run's `head_sha`, `gate_summary.{failed,not_run,skipped,planned}`
all zero (K8s: `failed`, `not_run`), and `passed == total`. Fail-closed on every
condition the review listed:

- **artifact absent** → refusal naming the run and the artifact name;
- **artifact/API download failure** → `RuntimeError` → verdict `ERROR`, evidence still
  written, release still blocked: "could not look" is not reported as "uncertified";
- **malformed JSON / unparseable zip / non-object payload** → refusal;
- **wrong commit** → refusal that quotes both values;
- **development-mode GA** → refusal, with all its complaints at once (4–5 reasons from
  one round, because an operator rerunning a 2 h 10 m soak per complaint would be
  asked to do it five times);
- **incomplete or planned gates** → refusal, including the round whose stored flag says
  `True` while its counts do not, which is what a flag-only reader waves through;
- **ambiguous selection** → refusal, for two artifacts of one name, for two verdict files
  in one zip, and for an expired artifact whose verdict can no longer be read back.

Three judgement calls worth reviewing:

- **`passed == total`, not a hardcoded 40/34.** Measured on real evidence: legitimate
  older K8s rounds recorded 32/32 and 33/33, so a fixed number would refuse
  certification the gate ought to inherit.
- **A listing entry with no `id` raises rather than refusing.** That is deliberate, not an
  oversight: the artifact exists and the gate cannot read which one it is, so the honest
  verdict is `ERROR` ("could not decide") with the evidence file written and the release
  blocked, exactly like every other place the gate reads a field the API stopped
  returning. The field-shape drift itself is what
  `test_the_authoritative_artifact_is_where_the_gate_reads_it` watches for on a host with
  a credential — it skips here, and says so.
- **A rejected artifact does not send the gate hunting for an older ancestor.** The
  newest green release-scoped round is the one a release relies on; if that round says
  it did not certify, "some older round certified" is not the answer, and quietly
  falling back would be the same class of lie F-33 is about.

Out of scope by the review's own framing: the Linux and reliability rounds publish no
comparable verdict artifact, so their evidence remains the job-set rule — a PR-layer run
of the Linux workflow cannot produce `cap-production-certification` plus
`postgres-version-matrix`. The GA run's `supply-chain-evidence` artifact is not read.

## 5. V1 / V2: UNVERIFIED

Full detail in `docs/quality/cap-provenance-identity-observation-2026-09-21.md`.
Summary: no `gh` binary anywhere on the machine, no token in the environment, an empty
`auths` map with no credential helper, `~/.config/gh` and `~/.netrc` absent, and an
anonymous `GET /v2/laolaola278-dev/cap-backend/manifests/v1.0.6-rc1` answering 404.
No pushed attestation was read; `predicateType`, `buildType`, `runDetails.builder.id`,
invocation and material fields, and the buildx/BuildKit versions on the publication
runners all remain **unobserved**. B3 and B4 were not implemented; no builder-id pattern
was invented. No registry content was mutated, and nothing was rebuilt or retagged.

What the repository's own evidence does show, kept separate because it is not the
attestation: the build passes `--provenance=true --sbom=true --push` with buildx's own
defaults and no `builder-id`; the evidence block's `attestations` field is derived from
those flags; the completeness gate asserts that derived field; all 17 captured build
records are dry builds; and `cap.provenance.v1` has no builder-id, predicate or
invocation field at all.

## 6. F-37 status: guarded, not closed

The guard landed and is green (`test_cited_evidence_resolves_to_a_tracked_copy_of_the_
same_measurement`, plus the absence-convention test and five controls). The defect is
unchanged: the five `provenance.evidence` strings still name
`outputs/artifact-closure/registry-base-digests.json`, which no fresh clone contains.
Closing that half is a `deployment/` edit — `classify_diff.py` puts every path under
`deployment/` in the runtime-affecting category — so it costs a re-certification round
and belongs in batch 2, exactly as the known-issues entry concluded. What the new guard
buys in the meantime is that the *claim* is readable from the clone and cannot drift:
the tracked twin under `docs/quality/artifacts/…` must assert the same digests, and a
regenerated measurement that diverges from the copy the release cites now fails CI.

Absence semantics, which the review asked to settle before requiring evidence: `evidence`
exists on exactly the five base entries and on neither `minio-object-store` nor
`postgres`; those two justify themselves inline (`vendor_signed_tag` + `open_gap`;
`policy.postgres`), and the new test names those fields and refuses any future entry that
has neither a pointer nor a listed justification. No new lock field was invented, so no
schema change was needed.

## 7. Reproducibility: measured, deliberately unattributed

Full detail in `docs/quality/cap-f39-reproducibility-measurement-2026-09-21.md`.
The 17 captured clean-runner build records (15 distinct builds) carry five of the 15
comparison elements in every record and the index digest in the four buildx ones: no
layer digests, no diff IDs, no manifest composition,
no attestation descriptors, no buildx/BuildKit version, no builder endpoint, no runner
metadata. Four images do show byte-identical recorded inputs producing different config
digests — but every such pair also spans different commits, and the only same-commit
pairs in the set are one build's evidence file downloaded twice (byte-identical). So
**no source of divergence is claimed**, and no `SOURCE_DATE_EPOCH`, package-resolution or
Dockerfile change was introduced. The two-independent-builds criterion was **not met**:
this machine's Docker engine is not running (`docker info` fails on the Linux-engine
npipe) and a new CI job needs a push. The measurement file states the acceptance spec
for whoever can run it.

One measured item that is not a hypothesis: all four `cap-sandbox-browser` records name
their base as `cap-sandbox-http:1.0.6-rc1` — a mutable tag — while the other four images
record `@sha256:` bases.

## 8. F-25 documentation: not written, on instruction

Stage 5 was conditioned on V2 being available. V2 is not: neither the published digests
nor the attestation state from run `35553750674` are reachable from here, and the only
local capture of that run is a status-transition monitor log with no digests. Writing a
"post-publication closure record" without those numbers would produce exactly the kind
of document that states more than it checked, so the record was not written. F-25 stays
CLOSED by run `35553750674` — unchanged, not reopened — and the sealed GitHub Release
and its assets were not touched.

## 9. Unexpected findings

1. **The plan's own batch-1 step for F-37 was infeasible as written.** "Every
   `provenance.evidence` path must exist and be git-tracked" is red until step 3 rewrites
   the pointers, and step 3 costs a re-certification — so a CI-only batch cannot contain
   both. The guard was rebuilt around what a clone must resolve (a tracked twin asserting
   the same digests) and the plan now records the deviation and why.
2. **A comment I wrote claimed a mechanism that does not exist.** My first draft said the
   GA workflow "exits 0 in development mode (`|| true` on the report step)". There is no
   `|| true` there. The real mechanism is stronger evidence for F-33: every push to `main`
   runs that workflow with `CAP_GA_STRICT=0`, in which a gate with no evidence is PLANNED
   rather than failing, so *nothing fails* and the job is legitimately green while its
   artifact says `development`. Corrected in the workflow comment and the test docstring
   before the commit, and the gate's own comment now names the mechanism the tree shows.
3. **A control harness can manufacture a fake verdict.** `_tmp/control_f33.py`'s first
   version wrote the mutated workflow with `Path.write_text`, which on this repo's CRLF
   files re-translated every newline and left `\r\r\n` through the heredoc — every test
   that lifts the gate then failed, and all nine controls looked like total successes at
   once. Fixed by mutating bytes and writing with `write_bytes`. Both harnesses now check
   their own premise before reporting: the F-33 one requires each needle to match exactly
   once and restores `release.yml` byte-for-byte, and the F-37 one refuses to count a
   control whose mutation changed nothing. Worth knowing before any future control script
   in this repository.
4. **The K8s side has no development-mode analogue** — `cap-k8s-certification.yml` sets
   `CAP_K8S_STRICT: "1"` unconditionally — so its authority leg is a binding-and-counts
   check. Measured across the 9 K8s artifacts captured locally, all report
   `failed: 0, not_run: 0`, so **no real rejected K8s round exists to test against**: the
   K8s refusal paths here are proven by fixtures, not by a shipped round. Stated so the
   strength of that half is not oversold.
5. **A hardcoded gate count would have broken inheritance.** Older rounds recorded
   32/32 and 33/33 gates; requiring today's totals would refuse certification that the
   classifier says is inheritable. The compatibility check is what surfaced this.
6. **The completeness gate asserts a command line, not an attestation.**
   `attestations.{sbom,provenance}` is computed from the build's own argv and the gate
   only requires it to be true. Plan item B said "presence, not content"; the sharper
   statement is that a *self-report about flags* currently stands in front of
   publication. Fixing it needs the read-back that V1 is blocking.
7. **`origin/main` is not where the RC work lives — and did not become so here.** The
   remote's `main` still sits at the 1.0.5 promotion (`32ec298`); the sealed line is
   `origin/release/1.0.6-rc1` at exactly `4d8f9c7`, which is also where the tag object
   `d0c7c7dc…` resolves. Local `main` carried the whole 1.0.6-rc1 line before this batch
   and gained four commits on top of it, so the RC work was never on `main` remotely —
   that is the release process, not a lost push. A later push must move neither
   `release/1.0.6-rc1` nor the tags.
8. **Three tracked prose claims became false, and were corrected in the same batch.**
   `docs/known-issues.md` item 5 still described F-33 as open and told whoever authorises
   a publication to confirm by hand that the GA run was dispatched `ga_strict=true` — a
   manual step the gate now performs; item 7 still said "no test looks" at the evidence
   pointers, which stage 3 made untrue. The closure
   report's F-39 bullet went further: it attributed the divergence to "timestamps in the
   image config and in the layer history" because the Dockerfiles carry no
   `VERSION`/`REVISION` label — but the compared pairs differ in a *recorded* field
   (`source_revision`), and the missing label explains nothing since a label would add
   content rather than account for a difference. That attribution is withdrawn against the
   measurement: the candidate variables are exactly the ones no record captured. The two
   `known-issues` items were rewritten, and the closure report — a dated record of its own
   round — was annotated with follow-up notes rather than rewritten. F-25's entry was left
   alone on purpose: rewriting it is stage 5, and stage 5 is blocked on V2 (§8).
9. **A live run was misread as a dead one, and the duplicate cost time.** The coverage
   invocation's log was tailed while it was still writing, looked truncated at the 39 %
   mark, and was declared dead; a second CI-equivalent run was started, and the two then
   competed for CPU — precisely the resource-pressure condition this repository's local
   test history says to avoid inferring bugs from. What changed: nothing about the result.
   The coverage job finished on its own (1492 passed, 135 skipped, exit 0), and the
   doc/gate module set was measured again afterwards with the machine to itself. It is
   recorded here because the false alarm was mine, and because the fix — read the process
   state, not a partial log — is the same discipline the rest of this batch applies to
   artifacts.

## 10. Recommendation for batch 2

Do it as one re-certification round, since its dominant cost is shared:

1. **C (F-24) + D (F-41) + E step 3 (repoint) + B steps 3–4** in one branch, so the
   single mandatory round — CI + Linux + K8s + 2 h soak + strict GA at one SHA, ≈ 3.5 h
   serial — pays for all four. Their classifier cost is identical (`deployment/`), so
   splitting them buys nothing and costs three rounds.
2. Before that round, on a host with credentials: **V1/V2**, then B3's assertion (the
   gate reads `runDetails.builder.id` back from the registry rather than from the
   builder's argv). This was the one batch-1 item that could not be started for lack of
   access rather than lack of money; B4 (Sigstore attestation) should be decided after V1
   rather than now.
3. **E step 2** — the tracked registry-resolution generator — belongs in the same round;
   it makes F-37's remaining half unnecessary rather than merely repointed.
4. **G** stays batch 4: two clean builds at one commit with the §7 fields, then the
   metadata/pin decisions, with the apt/apk content question taken explicitly.
5. **F (24 h multi-leg soak)** is a workflow-shape change; keep it after batch 2 so it
   does not queue against the round that already needs the soak lane.

Per the review instruction, batch 2 was **not** started, and nothing here is pushed.

## 11. What this report does not claim

- It does not claim CI's verdict. The unit job was run *here* with `ci.yml`'s own flags and
  came back green at 91.91 % coverage, but on this machine the container-gated tests skip
  and the two live Actions-API checks cannot answer, so the authoritative run of that job —
  and of the artifact-shape assertions in particular — still belongs to a push, and no push
  has been made.
- It does not claim a publication-path run: no tag, no dispatch, no workflow rerun was
  performed, so the gate change has been executed only by the test harness here (against
  canned API answers and 13 real artifacts read from local captures) — not by a release.
- It does not claim the sealed images' attestation content, in either direction.
- It does not attribute the build divergence to any cause.
- It does not claim F-37 closed, or that the lock's evidence pointers are clone-resolvable
  as written.
- It does not claim the two-independent-build measurement happened; it claims the
  existing evidence cannot support one.
