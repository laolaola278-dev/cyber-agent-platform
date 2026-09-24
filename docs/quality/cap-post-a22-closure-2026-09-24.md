# CAP POST-RC HARDENING — POST-A2.2 CLOSURE REPORT

Input: **BATCH 3 A2.2 COMPLETE — PRODUCER AUTHORITY RECERTIFIED**, candidate
`dea8c6f2f576bec667ee27dc8a9458673ca0982e`, reported in
[`cap-post-rc-batch-3-a22-producer-authority-2026-09-24.md`](cap-post-rc-batch-3-a22-producer-authority-2026-09-24.md).
Date: 2026-09-24. Scope: documentation and governance closure above that candidate, plus the
remaining-blocker inventory. Forbidden and not done: creating a tag, starting publication, touching
sealed `v1.0.6-rc1`, modifying the candidate, re-running certification for a docs change, and
B3/B4/F-46/F-39 remediation.

One thing this round produced that the A2.2 report did not contain: **the repository tip is not
publish-ready right now, and the certification gate says so with four reasons** (§H, §J). That is
the gate working, not a regression -- but it is a fact a releaser meets only at tag time unless it
is written down.

---

## A. A2.2 candidate identity

| field | value |
| ----- | ----- |
| candidate | `dea8c6f2f576bec667ee27dc8a9458673ca0982e` |
| frozen | 2026-09-24 05:38 UTC, against the eight Stage-10 conditions (`_tmp/a22_freeze.txt`) |
| certified by | CI `35958562520` · release-layer Linux `35967302293` · K8s `35958562591` · reliability soak `35960848879` (7200 s) · final-strict GA `35971523354` |
| strict GA verdict | `mode: final-strict`, `full_ga_certified: true`, 40/40 gates, `skipped: 0`, artifact `10797614781` |
| commits above it | the A2.2 report and its register entry, this round's F-50 reconciliation, and the closure report plus its follow-up wording commits -- `git log --oneline dea8c6f..HEAD` is authoritative, this row deliberately does not count them |
| modified by this round | **no** -- the candidate is immutable; everything here lands on top of it |

## B. F-44 — exact disposition

**F-44: IMPLEMENTATION CLOSED at `dea8c6f`.** Typed, because the finding's own text separates the
two things a reader might mean by "closed".

What the definition requires, taken from the entry as filed at `44fb73d` rather than from any later
restatement of it:

| element | original words | state |
| ------- | -------------- | ----- |
| scope disclaimer | "it is **not** a claim about published images: the release path … has never executed -- **that is F-25's remaining scope** -- so what the pin would do to the bytes that operators actually pull is **unobserved**, not proven false" | a disclaimer, so it cannot be a closure criterion |
| the title | "never demonstrated to be the producer of **a built image**" | met: five release-rehearsal images + the observation set built by the pinned executable |
| the gap | "that the actual build producer is pinned" | met: downloaded, hashed, explicitly invoked, named builder |
| the decision | "Whether a mismatch should block publication is a gate decision the project has not made, which is why it is filed here instead of being enforced by prose" | made (Option A) and enforced in code by `release-image-completeness` |
| Option A's contract | six build-time evidence requirements + "mismatch ⇒ failure" (`…design-options…` §D) | met; the dry-run at §P of the A2.2 report exercises each refusal |

**How the drift got in, and how it was handled.** "no *published* image has been produced by the
pinned executable" entered this entry at `d255fdc` -- after the filing -- and A2.2's closure list
quoted it as if it were the original criterion. Reading strictly would have made that sentence either
a reason F-44 could not close (wrong: the filing disclaims it) or a reason to drop the question
(wrong: it is a real gap). It became **F-51**. No historical text was edited to change a status:
`44fb73d`'s wording is quoted above because that, not a later paraphrase, is what the status is
decided against.

## C. F-47 — disposition

**CLOSED at `dea8c6f`**, unchanged from the A2.2 report's §G/§Q, with one addition from this round:
its closure note now points at F-51 for the live-publication observation instead of describing it in
prose. The reader is inside `release-image-completeness`; `publish-release` and `release-chart` both
`needs` that job; 22 negative controls and the four executed dry-run arms cover the refusals.

## D. F-48 — disposition

**CLOSED at `dea8c6f`**, unchanged. Measured both ways: the pre-A2.2 gate invents five images from a
producer sidecar in the globbed directory; the current gate refuses the same arrangement naming the
sidecar for what it is, with the five real images keyed correctly.

## E. F-50 — reconciliation

**CLOSED in `6eb2ecc`.** Ten stale statements in four tracked files:

| file | what was stale | now |
| ---- | -------------- | --- |
| `scripts/release/producer_contract.json` | `purpose` ("the other is a promise"), `status.a2_2_obligations` (`OPEN`), `a2_2_obligations.status` (`NOT YET EVIDENCED`), `checks_not_run_here.F-47` ("until A2.2 Stage 5 lands") | obligations stated as evidenced **at a named candidate**; the stale `F-47` key replaced by `live_publication_F-51` |
| `scripts/release/record_build_producer.py` | `buildx_prefix()` docstring, the no-pin branch comment, the `reason` string written **into a record**, and the comparison-list comment -- all saying the release path still runs `docker buildx` and changes "until A2.2" | each names the shape that actually reaches them now: a `--local-docker` developer build |
| `.github/workflows/ci.yml` | "The release path … is **untouched until A2.2 is approved**, and `test_the_release_build_path_is_still_not_switched` is the fence that says so" -- a test this batch deleted, so the comment pointed at a file that does not exist | names `test_the_release_build_path_now_runs_the_controlled_producer`, the successor, and keeps the two-facts distinction |
| `backend/tests/test_producer_contract_freeze.py` | pinned the two old strings (`startswith("NOT YET EVIDENCED")`, `…startswith("OPEN")`) | retargeted to the new shape, and made **harder to satisfy** (below) |

**"Only wording" was measured, not asserted.** Producer records were captured from the real recorder
before and after the edits (`_tmp/f50_payload_diff.py`), and the lifted `ARTIFACT_GATE_PY` was
executed over both (`_tmp/f50_gate_equivalence.py`) in three arms: the rehearsal-shaped record, a
no-pin record, and a normalised record the gate must reach **PASS** on. All three answers are
byte-identical, including the passing one. The field-level diff did its job too: it flagged
`observed.controlled_buildx.reason` as inside a decision-bearing block, which is why the answer is
the executed gate rather than a judgement about fields.

**The retargeted freeze test is stronger than the one it replaces.** Four weakening mutations were
run against it and all four were refused: a free-text status naming no commit; the two status
strings naming *different* shas; the enforcing artifact left unnamed; and the owed item hidden. It
now requires `EVIDENCED AT `, a full 40-hex sha, agreement between the two strings, a named enforcer,
and a named owed item.

**Erratum found while doing this.** The A2.2 report claimed the published
`values-release-1.0.6-rc1.yaml` asset "names no image digests". It names all five. The read asked
`/repos/…/releases/assets/{id}` without `Accept: application/octet-stream`, got 200-with-metadata,
wrote nothing, and reported the empty result as a measurement -- the F-33/F-42 class of error
arriving through a helper instead of a job colour. The audit now fetches bytes, verifies them against
the `sha256` GitHub itself records for the asset, and compares every digest the asset names with what
the tag resolves to on ghcr; the reader refuses to answer from metadata. Recorded as §R.1 of that
report rather than edited silently into the table.

## F. F-49 — governance design (designed, not implemented)

**The desired contract.** A change is *producer-affecting* -- and must not inherit runtime
certification on a path lookup alone -- if it can alter any of:

1. the bytes of a published image (what builds them, with which executable, which builder, which base);
2. producer **identity** as a release record states it (the instrument, the read-back, the bindings);
3. **publication authority** (what a gate requires, and when it refuses);
4. the **release image graph** (which jobs must run, in what order, before `publish-release`);
5. **release security evidence semantics** (what an attestation, scan or digest record is taken to prove).

Today `classify_diff.py` answers with path-keyed categories: everything under `scripts/release/**` is
`certification_generator` with `runtime_affecting=false`, and `.github` is `ci_workflow`. Measured
consequence in this very round: the classifier returned `INHERITED` for **both** the commit that
switched which executable builds every published image **and** a commit that changed no behaviour --
the two are indistinguishable to it (§G).

### Option A — path-level reclassification

Add a `producer_affecting` dimension computed from a *declared list* of paths and workflow regions:
`scripts/release/build_release_image.sh`, `install_controlled_buildx.py`, `record_build_producer.py`,
`producer_contract.json`, `controlled_buildx.json`, `oci_image_digest.py`, plus named regions of
`release.yml` (`release-images`, `release-sandbox-browser`, `release-image-completeness`,
`publish-release`) and `ci.yml` (`release-image-builds`, `producer-observation`).

### Option B — semantic manifest

The repository carries a manifest, e.g. `scripts/release/producer-authority.json` (**proposed; no
such file exists today, and none is created here**), listing each
artifact that participates in those five questions with the role it plays, and the classifier reads
the manifest instead of hard-coding path rules. The manifest becomes the same kind of frozen target
`producer_contract.json` already is: reviewed, and checkable by a test that every listed artifact
exists and every release-path artifact is listed.

### Comparison

| axis | Option A: path list in the classifier | Option B: semantic manifest |
| ---- | ------------------------------------- | --------------------------- |
| false positives (harmless edits blocked) | High on whole files: this round's comment edits in three listed files (`producer_contract.json`, `record_build_producer.py`, `ci.yml`) would be flagged, and there is no way to say "line 667 of this file is prose" | Lower: a role can carry a `prose_only_paths`/`comment_only` qualifier, and `release.yml`'s `run:` blocks can be listed per-step. Still file-granular unless it grows line ranges |
| maintenance burden | Moderate and *silent*: the list lives in classifier code, so adding a release script without touching the classifier yields an unflagged gap -- the exact failure mode being fixed | Higher up front, lower over time: a new artifact appears in a data file whose completeness a test can enforce (`every scripts/release/* referenced by a release job is declared`) |
| testability | Needs a new test file asserting the classifier's verdict per path; weak on "did you forget to add the new script" | Directly testable two ways: manifest entries resolve, and the set of artifacts the release jobs actually invoke is a subset of the manifest -- which is a check that *can* fail on omission |
| catching new release scripts | Only if someone remembers | Measurably: parse the workflows for `python3 scripts/release/…` and `bash scripts/release/…`, and refuse an undeclared one. That is the property F-49 is really about |
| backwards compatibility | Additive: `runtime_affecting` keeps its meaning and `producer_affecting` is a second flag; existing inheritance tests keep passing | Same, plus the manifest is new so no existing rule is reinterpreted. Costs a schema and a loader |
| risk | Drift by omission; the classifier becomes the authority on what is a release file, which it is poorly placed to know | The manifest can go stale in the same way, but *detectably* -- a test compares it to the workflows |

**Recommendation, for the round that takes it:** Option B, with the classifier's `producer_affecting`
flag derived from the manifest *and* an omission test that reads the workflows. Option A is a smaller
diff that fixes today's instance and leaves tomorrow's; the reason to prefer B is that F-49's failure
mode is specifically "someone added a file nobody thought to list", and only B can fail loudly on
that. Either way the classifier's existing `runtime_affecting` semantics should be left alone: it
answers a different question, and this report's §B is an example of what conflating them costs.
Not implemented here -- no classifier change was authorised.

## G. Classifier result of the reconciliation

`python scripts/release/classify_diff.py dea8c6f HEAD`, run after `6eb2ecc`:

```
RESULT: runtime certification INHERITED (release_metadata_only=True)   exit 0
runtime_affecting: false
files: ci_workflow .github/workflows/ci.yml
       docs CHANGELOG.md | docs known-issues.md | docs the A2.2 report
       test_harness test_producer_contract_freeze.py
       certification_generator producer_contract.json | record_build_producer.py
```

**That line was not accepted as the answer**, because F-49 says a `certification_generator` path
carries no information about whether a release artifact changed. The boundary was decided on
measurements instead:

| question | measurement | result |
| -------- | ----------- | ------ |
| could the workflow's semantics have changed? | parse `ci.yml` at `dea8c6f` and at `HEAD`, compare documents (YAML discards comments) | **identical** |
| could the gate's requirements have changed? | compare the contract subsets `release.yml` reads: `contract`, `inherited_from_a2_1` (whole frozen block), `a2_2_obligations.evidence_fields` | **identical** |
| could the recorder's logic have changed? | parse both versions, strip docstrings, compare ASTs | **identical** |
| could a record's decisions change? | execute `ARTIFACT_GATE_PY` over before/after producer records, three arms incl. a required PASS | **identical answers** |

**Decision: no new candidate is owed, and no certification round was re-run.** Every measured
surface a certification round produces evidence about -- workflow semantics, contract requirements,
recorder logic, gate decisions -- is byte-identical across this commit; the residue is prose and the
test that pins prose. The cumulative answer across everything above the candidate was re-measured at
each commit of this round and never changed: still `INHERITED` / exit 0, with `docs` the only category
any later commit added to the list quoted above (which is the set as of `6eb2ecc`). That re-run is
reported for completeness, not as the reason -- an `INHERITED` line that grows by documentation
commits is exactly the answer that carries no information about the decision. What this does *not*
claim: that `INHERITED` proves a
release commit safe in general -- it did not distinguish A2.2 from a comment fix, which is F-49
standing. The rule being applied is therefore stated for the next reader: *an edit to a
`certification_generator` path needs the four measurements above, not an `INHERITED` line, before it
can ride an existing candidate.*

## H. Remaining publication blockers

| # | item | state | evidence |
| - | ---- | ----- | -------- |
| 1 | producer authority live in the release path | **DONE** | §C/§D of the A2.2 report, §J measurements |
| 2 | candidate recertified directly | **DONE** | five rounds at `dea8c6f`, §O there |
| 3 | F-33 authority in the gate; F-42 executed-jobs rule | **DONE**, unchanged and green | `test_release_publication_gate.py`, CI `backend` |
| 4 | F-47 producer gate | **DONE** | §G/§P, 22 controls |
| 5 | F-50 reconciliation | **DONE in `6eb2ecc`** | §E above |
| 6 | version bump + release authorisation | **PENDING, and it is a human decision** | no tag may be created in these stages |

**7. An item that reads like a blocker and is the gate refusing: the tip is not certified.**
Running `RELEASE_GATE_PY` with `CERT_TAG_SHA=6eb2ecc` at a hypothetical `v1.0.7-rc1`:

```
verdict FAIL, 4 failures, all cap-ga-certification.yml:
  records mode='development', which is not 'final-strict'
  records full_ga_certified=False, which is not True
  gate_summary.planned=5, requires 0
  gate_summary counts 35 of 40 gates passed, so a gate has no PASS behind it
```

What it selected, and why the answer is right: the newest green GA round at the tip's ancestry is
`35983433128`, a **push**-triggered round at `ea1fb02` -- development mode -- and F-33's rule is that
a rejected authority does **not** send the gate fishing for an older favourable round. Meanwhile
Linux was accepted at distance 2 from the release-layer run (its push-run at `ea1fb02` was correctly
passed over: "green but did not execute its release job set: `cap-production-certification:
['skipped']`"), K8s at distance 1 with authority PASS, reliability at distance 2. So a tag at
`6eb2ecc` today is blocked on one missing round, and **that is the mechanism working as designed**,
not a defect: the certified candidate is `dea8c6f`, and the tip has two commits above it that
certification has never seen in strict mode.

## I. Remaining hardening backlog

| item | kind | state after this round |
| ---- | ---- | ---------------------- |
| F-49 | governance design | designed (§F), not implemented -- no authorisation |
| F-46 | policy decision | untouched, unchanged |
| F-39 | measurement, decision | untouched; Batch 1's measurement stands |
| B3 / B4 | design approved? no | untouched |
| F-51 | validation item | **new**, open, closable only by a real release |
| F-52 | evidence durability | **new**, open, one-line fix with a recertification price |
| F-54 | workflow wording + a release step | **new**, open -- filed rather than fixed, because the edit is to four workflow files (§M) |
| F-53 | release-cost governance | **new**, open -- see §J item 3 |
| 24 h soak | planned validation | not run; the 7200 s round is what `dea8c6f` has |

## J. First-live-release validation checklist

What the next release must actually check, in the order it can check it. Read-only unless stated.

1. **Decide the tip you will tag, then make certification true of *that* commit** (F-53). Either tag
   `dea8c6f` directly -- the only commit with all five rounds -- or run a final-strict GA round at the
   newer tip, which is ~70 min and is the cost of the docs commits sitting above it. Do not read a
   push-triggered green GA run as satisfying this (§H shows it being refused).
2. **Confirm the required job sets, not the workflow colours.** For a tag at commit *X*, each of the
   four workflows needs a run at *X* or an inheritable ancestor whose jobs match `REQUIRED` in
   `release.yml` -- notably `cap-production-certification`, which only exists in a
   `layer: release` dispatch or a tag run. And the run must have **started**: F-54 measured a queued
   certification round being reported `cancelled` with zero jobs when a later push entered the same
   ref group, which is how a commit can end up with no evidence at all while its run list still looks
   like an abandoned rerun rather than a missing round.
3. **Watch the install step in a job that pushes.** `release.yml`'s two image jobs install the pinned
   executable and then authenticate and push -- the first time that combination runs anywhere. Read
   `outputs/release-images/producer/buildx-install.json`: `status=INSTALLED`, all nine
   `steps_completed`, `integrity.relation=equal`, `read_back` matching the pin. This is F-51's first
   half, and F-53's price if it fails: a defect here is a new candidate.
4. **Read the producer verdicts before approving assets.** `release-images-<version>.json` →
   `producer_verdicts` / `producer_summary`: five `CONFORMING`. Anything else stops the release, which
   is the point (§P of the A2.2 report shows the refusals).
5. **Preserve the producer evidence durably** (F-52). The artifact holding it expires ~90 days after
   creation; copy `release-images-<version>.json` into the Release assets or accept that in a year
   nobody can say which producer built the published bytes.
6. **Re-read the seal afterwards** and confirm nothing moved that this release did not move: the
   audit is `_tmp/closure_sealed_audit.py`, and its checks are §K.

## K. Sealed-release integrity

Re-run after every commit in this round (`_tmp/closure_sealed_audit.py` →
`_tmp/closure_sealed_audit.json`). Ten checks, all OK, verdict **SEALED RELEASE INTACT**:

| check | measured |
| ----- | -------- |
| tag object | `d0c7c7dc14cb075c095bd7970650b180a73afbf1`, annotated, `2026-09-21T02:18:13Z`, peels to `4d8f9c72b72dcea74e8588374fe8ef3a04564eb6` -- unchanged |
| tags in window | none created on/after 2026-09-22; 12 tags, newest `v1.0.6-rc1` |
| release | `published_at == updated_at == 2026-09-21T02:38:59Z`, `prerelease: true` -- never edited |
| assets | all five created/updated `2026-09-21T02:38:58/59Z`, `download_count` 0 -- none replaced |
| asset content | `values-release-1.0.6-rc1.yaml` bytes hash `bfcb2aa2d02e5d5a…`, equal to the `sha256` GitHub records for it |
| what it ships | the asset names all five digests, and each equals what `1.0.6-rc1` resolves to on ghcr today (backend `a733b90c7a84…`, frontend `e1b1889a868c…`, egress-proxy `8ab8c234f278…`, sandbox-http `36bb2f7993ac…`, sandbox-browser `b369618871bd…`) |
| tag/digest round-trip | each tag re-read by its own digest returns that digest |
| promotion | newest non-prerelease, non-draft release is still `v1.0.5` |

This is a stronger read than the earlier rounds', for the reason in §E: it compares the published
tags against the Release's **own** statement of what it shipped, not against a previous round's notes.

## L. FINAL VERDICT

### POST-A2.2 CLOSURE COMPLETE

Documentation and governance closure is done: F-44's status is typed against the definition as
filed, with the criterion that drifted in later named and relocated rather than absorbed; F-47,
F-48 and F-50 are closed; F-49 is designed with two costed options and a recommendation, unimplemented
as required; the reconciliation's classifier answer was checked by four measurements rather than
accepted; the sealed release is intact on a stronger read than before; and no tag was created, no
publication started, no candidate modified, and no certification round re-run for a documentation
change.

**Open items, none of which this round was authorised to close:** F-49, F-46, F-39, B3, B4 (policy
and design); F-51, F-52, F-53, F-54 (new, all four requiring a release decision rather than more
testing). **The one thing a release must do first** is §J item 1: choose the tip and make strict
certification true of that commit, because the gate at the current tip refuses, correctly, and will
keep refusing every tip whose newest GA round came from a push.

## M. What the push confirmed, including about itself

Every commit pushed above the certified candidate is documentation plus the F-50 wording
reconciliation, and each one goes through the same Linux gate. The concrete verified instance: CI run
`35992258088` at `87d86b3` -- **success, every job green** -- which is the Linux authority for the
edits to `scripts/release/` and `.github/workflows/ci.yml` (the retargeted freeze test, ruff's line
rules, the recorder), with CAP Linux Certification `35992258152` and K8s `35992258188` success at the
same commit.

**The commit carrying this sentence is deliberately not claimed here.** It triggers the same
workflows, and writing "CI is green at <this tip>" would make the report stale the instant it was
committed -- a document cannot certify its own head, which is the same error F-42 was about from the
other side. Check the run list for the tip rather than trusting a line like this one.

The pushes also re-demonstrated **F-53** simply by happening, and one of them demonstrated something
nobody had written down. Each push adds a development-mode GA round to the tip's ancestry, and
whichever of those is the newest green one when a tag is read is the authority the certification gate
will use -- exactly the refusal §H measured. But they do not all survive: `CI` and the GA round at
`4ceac00` (`35995454184`, `35995454389`) both ended `cancelled`, the first because `ci.yml` sets
`cancel-in-progress: true` by design and the second with **zero jobs**, one second after the next push
landed while an older round still held the `refs/heads/main` group. The line the measurement drew is
in-flight versus waiting, not old versus new: the round that was actually running at `87d86b3` passed
through two later pushes untouched and completed (`35992258108`, success at `12:15:55Z`), and the
reading then survived a prospective test: pushing `2f90450` displaced the round `a6d2bc9` had left
waiting (`35998117484`, `cancelled` two seconds later, again with zero jobs) while leaving that
commit's already-running Linux and K8s rounds alone. So a commit
can carry Linux and K8s evidence and no CI or GA round at all -- and four certification workflow
comments say that a push "queues behind" a run in flight, which is true of a run that has started and
not of one still waiting. Filed as **F-54**, with §J item 2's check that the run at a sha actually
*started*; **not fixed here**, because the only correction is prose in `.github/workflows/`, and that
is the price F-50's reasoning says not to pay in a documentation round. Nothing in the gate is
softened by any of this: an absent round is absence, and absence is refused. §J item 1 stands
unchanged, and no tag was created and no publication started.

Outside this round, deliberately: F-49 is designed and unimplemented; F-51, F-52, F-53 and F-54 each
need a release decision from the project, not more work in a documentation round.
