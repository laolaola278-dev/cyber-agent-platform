# CAP POST-RC HARDENING -- BATCH 3 A2.2 PRODUCER AUTHORITY & RECERTIFICATION REPORT

Input verdict: **BATCH 3 A2.1 PRODUCER CONFORMING** (acceptance head `b98eb3b`, run
`35868200920`; the same observation reproduced at full-CI head `9983705`, run `35876065468`).
Approved option: **Option A** -- the published-image build path *is* the pinned producer, and a
producer mismatch blocks publication.
Candidate this report is written against:
`BATCH3_A22_CANDIDATE_SHA=dea8c6f2f576bec667ee27dc8a9458673ca0982e` (**§N**).
Date: 2026-09-24.

The four goals this batch was approved against, and where each is answered:

| # | Goal | Answered in |
| - | ---- | ----------- |
| 1 | release-equivalent builds actually use the controlled producer | §C, §D, §J |
| 2 | producer evidence is bound to each image / run / SHA | §F, §J |
| 3 | producer mismatch or unreadable evidence blocks publication | §G, §H, §P |
| 4 | close F-44 and F-47 only after candidate-direct recertification | §N, §O, §Q |

Still excluded, by approval: B3, B4, F-46, F-39 closure, release publication, tag creation.

**A note on the `_tmp/` paths below.** Scripts under `_tmp/` are session scratch and are **not** part
of the repository, and nothing the repository is obliged to do points at one -- that is F-37's rule,
and this report does not re-file the defect it closed. Every value quoted from one of those scripts
is reproducible instead from the named commit, workflow run id and artifact it read, which is what
the section cites alongside it. §W collects the commands for whoever has this session's `_tmp/`; a
reader without it can re-derive each number from the runs.

---

## A. What changed

The commits between the last certified head `9983705` and the candidate touch six kinds of thing,
and none of them is application runtime code:

* **a frozen contract file** -- `scripts/release/producer_contract.json` (§B);
* **one new installer** -- `scripts/release/install_controlled_buildx.py`, the single mechanism
  every producer-building job now calls (§C);
* **the release build script** -- `scripts/release/build_release_image.sh` builds through the
  installed executable with an explicitly named builder, records the argv it is about to run, and
  treats a broken producer record as a failed build (§D, §F);
* **the producer recorder** -- `scripts/release/record_build_producer.py` gained a build-identity
  block, a build-path derivation, a builder read-back and a pinned-index resolution (§F);
* **two workflows** -- `ci.yml`'s observation and release-rehearsal jobs and `release.yml`'s two
  image jobs install the producer and name it; `release.yml`'s completeness gate gained the
  producer reader (§G);
* **one new reader** -- `scripts/release/oci_image_digest.py`, which resolves the digest an OCI
  archive actually contains (§E).

What did *not* change, deliberately: the sealed `v1.0.6-rc1` tag, release, assets and image tags
(§R); `scripts/release/classify_diff.py` (§K); the certification gate `verify-certification` (§P);
and the F-33 / F-42 test bodies, which are unchanged and green (§I).

## B. Stage 0 -- the freeze A2.2 inherits

A2.2 was approved on top of a measurement, so the first job was to make that measurement
un-quiet-changeable rather than to restate it in prose. `scripts/release/producer_contract.json`
freezes, as data:

* the **controlled buildx**: `v0.37.1`, commit `0b265a9f62db554fa9aba6dd19e1bd5704bc7d8a`, sha256
  `9447199cdb435f25880548343c128a4b6650e8891ee598905d8d29d39a8e359b`, released asset id
  `557511570`, size 65,798,306 bytes, and the four reads that establish a *standalone* binary's
  identity (bytes → hash, `--version` line → version + commit, the asset's own name, the install
  path it was asked for) together with `never_invoked_as: docker buildx`;
* the **builder**: driver `docker-container`, `name_must_be_explicit: true`,
  `default_builder_accepted: false`, BuildKit `moby/buildkit:v0.33.0` at index digest
  `sha256:6c2fa84a6b61ccd72899dde4239f8d5717f05f9a8ca6f3cad185fb1a95a94de3`;
* the **four comparisons** (`lock_vs_workflow`, `controlled_pin_vs_lock`, `workflow_vs_observed`,
  `lock_vs_observed`) with, for each, the exact field paths that feed it, which are `required`,
  which are `blocking`, and the four names that compose `producer_alignment`;
* the **two digest layers** (running-image digest vs pinned index, and running-config digest vs
  pinned config) with `digest_relation` accepting both `same_digest` and
  `running_is_a_child_of_the_pinned_index`, and a `not_required_on_purpose` entry that records the
  refusal to demand `running == child`;
* the **instrument contract**: the 24 record paths that must be non-empty, and the six build-mode
  exemption prefixes that may only ever shrink;
* **`non_weakening.rules[]`**, where each rule names the test that enforces it.

`backend/tests/test_producer_contract_freeze.py` (17 tests) is what makes that file a freeze
instead of a claim: it loads the shipped contract, checks each cited enforcing test still exists by
node id, and runs the *recorder's own output* against the frozen required/blocking/component lists
as supersets -- so adding a field cannot silently drop a check, and removing one fails here. It
also proves the two conforming digest relations against recorder output, and refuses a narrowed
exemption list. `a2_2_obligations` is carried in the same file, labelled **NOT YET EVIDENCED**,
because a freeze must not launder a to-do into a done -- and note that the label is still there at
the candidate, pinned by a test, even though §C–§G are what it was waiting for; that staleness is
**F-50**, and reading `a2_2_obligations` as "what was owed" rather than "what is missing" is the
correction until the next candidate pays it.

## C. Stage 1 -- one install mechanism, and the order it runs in

`scripts/release/install_controlled_buildx.py` is the only place the pinned buildx is downloaded,
verified and turned into a builder. `scripts/release/record_build_producer.py` supplies its three
shared definitions (`load_controlled_pin`, `parse_version_line`, `PLUGIN_DIRS`) and the installer
imports them rather than restating them, so the tool that installs the producer and the tool that
later measures it cannot drift into describing two different files. The order is the contract:

```
pin -> path -> download -> integrity -> install -> read-back -> declaration -> builder -> plugin-diagnostic
```

Each edge is a refusal, and `backend/tests/test_controlled_buildx_install.py` (27 tests) proves
the edges with an injected fetcher/hasher/runner and one shared event log:

* `path` before `download`, six times over: a target inside any docker CLI plugin directory is
  refused with **zero** events recorded -- no fetch, no chmod, no write. The proof is that the
  file at that path is statistically untouched (size, mtime, mode before == after), not that it is
  absent; see §T for why that distinction cost a CI cycle.
* `integrity` before `install`: size mismatch fails as `truncated` before hashing, and a hash
  mismatch stops at `integrity` with nothing executed and the unverified bytes left at the
  `.part` path where they can be inspected. Nothing is chmod-ed, executed or named a builder
  before its digest agrees with the pin.
* `read-back` before `builder`: the installed binary is asked `--version` and its reported version
  **and** commit must equal the pin's; only then is a builder created, and `create` is followed by
  an `inspect` whose returned name and driver must equal what was asked -- a builder that answers
  with a different name is refused rather than adopted.
* `declaration` before `builder`: the install path must be the one the workflow's own `env:` block
  states (read through the `CAP_OBSERVE_*` / `CAP_RELEASE_*` prefix the caller names), so a job
  cannot install one binary and declare another.
* `plugin-diagnostic` last, and never fatal: what `docker buildx version` answers on that runner is
  recorded -- CI measured `v0.37.0` against the `v0.37.1` pin at every head in this batch -- and it
  decides nothing, because the producer being scored is the executable the mechanism installed.

On failure the installer still writes its facts JSON, with `status: ERROR`, `failed_step`, and
whatever `steps_completed` reached, and exits 1. A mechanism that loses its own diagnosis when it
refuses is a mechanism nobody can debug.

## D. Stage 2 -- the release build command

`build_release_image.sh` now builds every release image with

```
"$BUILDX" build --builder "$BUILDER" <args> --tag "$REF" --file "$DOCKERFILE" \
                --metadata-file "$META" "$CONTEXT"
```

where `$BUILDX` is the absolute installed path and `$BUILDER` an explicit name. Validation refuses
before any build: a relative or non-absolute path, a path inside a plugin directory, a non-existent
or non-executable path (whose message points at the installer), and a missing `--builder`. All five
images take this path, in both release jobs and in the CI rehearsal job.

Two properties are read from the run rather than asserted about it:

* the argv is **written before the build** and patched with the exit code afterwards, so a record
  exists for a build that never returned; and
* `build_path` in the record is **derived from that argv**, not restated from configuration --
  which is what lets §G tell a publishing build from a rehearsal by evidence.

The CI dry path uses the same producer and the same script; where the two paths legitimately
differ -- the rehearsal exports an OCI archive instead of pushing, and binds the browser's base
from that archive instead of from a registry digest -- the record says so in
`build_path.destination` and `build_path.base_handoff`, with a `note` spelling out that both are
legitimate and neither is allowed to look like the other.

## E. Stage 3 -- two hand-offs, kept apart

A2.1's fifth image gets its base without a registry: the HTTP sandbox image of the same round is
written to an OCI archive, its digest resolved from `index.json` by
`scripts/release/oci_image_digest.py`, and passed as a named build context
(`--build-arg SANDBOX_HTTP_BASE=cap-sandbox-http` plus
`--named-context cap-sandbox-http=oci-layout://<layout>@<digest>`). That exists because the
observation job pushes nothing, so no pullable digest exists yet.

The publishing path does push, so its dependency is real: `release-images` builds and publishes
`cap-sandbox-http`; `release-sandbox-browser` then reads the `index_digest` out of *that job's own
uploaded record* and layers on `ghcr.io/$OWNER/cap-sandbox-http@<that digest>`, with `--push` and
no `--oci-out` and no `--named-context`. A2.2 was explicit that the layout trick does not become
the release's hand-off, and two things enforce it:

* the reader refuses it (§G: a published image whose recorded hand-off contains
  `same_round_oci_layout` is a MISMATCH), controlled by
  `test_the_oci_layout_hand_off_is_refused_for_a_published_image`; and
* `test_the_published_browser_still_extends_the_digest_this_release_pushed` fails at review time
  if the layout ever reaches `release.yml`, so the failure is one test run rather than a release
  that has already pushed four images.

The guard was negative-controlled: replacing the browser's `--build-arg` line with the CI rehearsal
form (`--named-context "cap-sandbox-http=oci-layout://$LAYOUT@${BASE_DIGEST}"`) makes the test
fail, and the unmutated workflow passes (`_tmp/negative_control_stage3.py`).

## F. Stage 4 -- the record, its identity, and its build path

Per image, the build job now records (field paths as `release.yml` and the freeze read them):

* `identity` -- `source_revision` (declared) against `runner_reports.github_sha` (read back),
  `run_id`, `run_attempt`, `job`, and per-check `relation`s; verdict `CONFORMING` only when the
  declared and read-back commit agree, the run id is present, and the job was supplied.
  `identity` is *belonging*, and it is deliberately **not** one of the four
  `producer_alignment.components`: which commit a build ran at does not merge into which binary
  built it.
* `observed.controlled_buildx` -- version, commit, `integrity.computed` against
  `integrity.expected`, and the path it was invoked from.
* `observed.build_invocation` -- the executable, the builder it was given, and `build_exit`.
* `pinned_index_resolution` -- the pinned index, the platform child resolved through it, and the
  two-layer `digest_relation` / `config_digest_relation`.
* `comparison` + `producer_alignment` -- the four frozen comparisons and their roll-up.
* `contract_gaps` -- the instrument's own list of required paths it could not fill, produced under
  `--self-check`. A hole in the instrument is reported as a hole, not as an absent field.
* `build_path` -- `publishes`, `destination`, `output`, `base_handoff`, `builder_named`,
  `attestations`, derived from the build's recorded argv, with the hand-off value composed from the
  bindings actually measured.

The build job is the authority that performs the read-back: `grep record_build_producer.py
release.yml` is asserted empty, because a workflow that reconstructs producer identity from its own
configuration would be measuring its intentions instead of its machines.

## G. Stage 5 -- the reader F-47 was filed over

`release-image-completeness`'s inline gate now *requires* producer evidence for all five images.
Its requirements are read from the frozen contract (the same file `test_producer_contract_freeze.py`
holds against the recorder), so the list that blocks a release and the list the project promised
cannot drift apart without one of the two suites saying so. Per image it checks:

`recorded` is not false; `contract_gaps` empty; every `a2_2_obligations.evidence_fields` path
non-empty; each of the four comparisons `CONFORMING`; `producer_alignment` `CONFORMING`;
`identity` bound to **this** commit, this run, and a job in `("release-images",
"release-sandbox-browser")`; `build_path.publishes is True`; no `same_round_oci_layout` in the
hand-off; `build_invocation.build_exit == 0`; every base image `status == READ`.

Two belonging rules are load-bearing and were easy to get wrong. Reading `GITHUB_SHA` alone would
accept a record from a *different workflow run of the same commit*, and reading `run_id` alone
would accept a record from the CI rehearsal, which was built at this commit in this very run. So the
gate checks the commit **and** the run **and** the job name, and refuses a rehearsal record as
`MISMATCH: job 'release-image-builds' is not one of [...] -- a rehearsal record is not release
evidence`. That is the code for "do not cherry-pick a producer record from another run".

4-of-5 is not a pass: `EXPECTED` is the five images, a missing record is `MISSING`, two disagreeing
records for one image are `AMBIGUOUS`, and any non-`CONFORMING` state extends `failures`, which is
what the verdict is computed from.

## H. Stage 6 -- six words, all blocking, none interchangeable

```
STATE_ORDER = ("CONFORMING", "UNKNOWN", "MISMATCH", "ERROR", "AMBIGUOUS", "MISSING")
```

ordered by how much each explains: a missing record makes every other symptom meaningless; an
unreadable instrument explains a comparison that could not be scored; a mismatch is the only word
that says the machine disagreed with the repository. A per-image state is the *worst* of its
reasons, all reasons are kept, and the payload carries `producer_verdicts` (state, reasons,
alignment, build_path) and `producer_summary` (which images sit in each word). Neither `ERROR` nor
`MISSING` nor `AMBIGUOUS` is collapsed into a generic false: they call for different repairs, and a
gate whose refusal cannot be diagnosed is a gate an operator will eventually bypass.

## I. Stage 7 -- controls

`backend/tests/test_release_producer_gate.py` carries 25 tests, of which 22 are negative controls:
each injects exactly one defect into an otherwise conforming five-image set and requires both the
exit code and the *specific word*, so a control cannot go green because the gate refused for an
unrelated reason. Coverage by word: MISSING ×2, ERROR ×4, MISMATCH ×8, AMBIGUOUS ×2, UNKNOWN ×1,
set-level ×4, contract-shape ×1, plus 3 positive controls -- including
`test_the_conforming_fixture_is_the_recorder_output_the_gate_claims_to_read`, which generates the
fixture through the real recorder so the suite cannot pass on a hand-written shape.

`test_release_dual_authority_gate.py` (6 tests) holds the composition: the certification gate
PASSes while the producer gate refuses, the producer gate PASSes while the certification gate
refuses, and `publish-release` (with `release-chart`) `needs` both -- so "both must pass" is a
property of the graph, not of prose in a test file.

The approval's other constraint here was explicit and is satisfied unchanged: **F-33's and F-42's
tests were not edited**. `test_release_publication_gate.py` is untouched in this batch (it does not
appear in the diff at all), and both files are green in §J's CI job.

---

## J. Stage 9/11 -- CI evidence at the candidate

Candidate `dea8c6f2f576bec667ee27dc8a9458673ca0982e`, CI run `35958562520` (push to `main`),
conclusion **success**, 11/11 jobs:

| job | conclusion |
| --- | ---------- |
| backend | success |
| frontend | success |
| migration | success |
| packaging | success |
| image-and-security | success |
| producer-observation | success |
| release-image-builds (cap-backend) | success |
| release-image-builds (cap-frontend) | success |
| release-image-builds (cap-sandbox-http) | success |
| release-image-builds (cap-sandbox-browser) | success |
| release-image-builds (cap-egress-proxy) | success |

Test totals, both from the same commit:

* CI `backend` job `107502073867`: **1792 passed, 133 skipped, 1 deselected in 1449.85 s**, with
  the repo's own exclusions (`--timeout=300 --timeout-method=thread`, the five Linux-only phase-28.5
  files ignored, the 500-run durability benchmark deselected) and `--cov-fail-under=90` enforced.
* Local full suite, same command and same exclusions, Windows: **1790 passed, 135 skipped,
  1 deselected in 1427.93 s**. Two tests CI runs and this box skips account for the difference; no
  test is green in one place and red in the other.

### J.1 The installer, on a real runner

From `cap-3a-producer-observation` → `buildx-install.json`, in both the observation job and each of
the five rehearsal jobs:

```
status=INSTALLED
steps_completed=[pin, path, download, integrity, install, read-back, declaration, builder, plugin-diagnostic]
read_back=v0.37.1 0b265a9f62db554fa9aba6dd19e1bd5704bc7d8a
integrity=equal            # the bytes downloaded hash to the pinned sha256
builder=cap3a-producer-observation / driver=docker-container / inspected_driver=docker-container
runner_plugin=v0.37.1      # diagnostic only, and it varies -- see J.4
```

Nine ordered steps, `integrity=equal`, and the read-back matching the pin's version *and* commit:
this is Goal 1 measured rather than asserted -- the release-equivalent builds ran the executable the
repository pins, verified by content, on GitHub's runners.

### J.2 The set verdict

```
producer-set.json: verdict=CONFORMING  problems=[]  authorizes_a2_2=True
rounds=[{run_id: 35958562520, source_revision: dea8c6f2f576bec667ee27dc8a9458673ca0982e}]
  cap-backend            CONFORMING  build_exit=0  alignment=CONFORMING  gaps=[]
  cap-egress-proxy       CONFORMING  build_exit=0  alignment=CONFORMING  gaps=[]
  cap-frontend           CONFORMING  build_exit=0  alignment=CONFORMING  gaps=[]
  cap-sandbox-browser    CONFORMING  build_exit=0  alignment=CONFORMING  gaps=[]
  cap-sandbox-http       CONFORMING  build_exit=0  alignment=CONFORMING  gaps=[]
```

### J.3 The five release-rehearsal image records

Each `ci-release-image-<name>` artifact's image record carries the producer block the gate will
read, bound to this run and this commit. All six records (five images plus the browser job's
`cap-sandbox-http` prerequisite):

```
lock_vs_workflow=CONFORMING  controlled_pin_vs_lock=CONFORMING
workflow_vs_observed=CONFORMING  lock_vs_observed=CONFORMING
producer_alignment=CONFORMING    contract_gaps=[]
identity: verdict=CONFORMING revision=dea8c6f2f576... run=35958562520 job=release-image-builds
built_with=/tmp/cap-controlled-buildx/buildx   builder=cap-ci-release-producer
builder running moby/buildkit@sha256:6c2fa84a6b61ccd72899dde4239f8d5717f05f9a8ca6f3cad185fb1a95a94de3
```

The example digests recorded at this candidate: `cap-backend` index
`sha256:1d7702b53e6f6b61478c2626e7ceb013ad5bce83d2bd8ce7b7db8d291c2cd76e`, `cap-sandbox-http` index
`sha256:5540daba…`, `cap-sandbox-browser` index `sha256:c78e4f04…` with its base bound to
`cap-sandbox-http` at the digest this round measured.

This is Goal 2: each record states the image, the run, the sha, the invocation, the executable's
version/commit/integrity, the builder name and driver, both BuildKit digest layers, the four named
comparisons, and `producer_alignment` -- and the identity block is what lets a reader tell "a record
of *this* build" from "a record".

### J.4 The runner's own buildx, which is the point of all this

`docker buildx version` on the same commit, within one CI run, across six jobs:

| job | runner plugin answered |
| --- | ---------------------- |
| release-image-builds (cap-backend) | v0.37.1 |
| release-image-builds (cap-egress-proxy) | v0.37.0 |
| release-image-builds (cap-frontend) | v0.37.1 |
| release-image-builds (cap-sandbox-browser) | v0.37.0 |
| release-image-builds (cap-sandbox-http) | v0.37.1 |
| producer-observation | v0.37.1 |

Three jobs saw the pinned version and three did not. The earlier measurements point the same way:
**6 of 6** records at candidate `257ba18` read `v0.37.0` under the `v0.37.1` declaration (that is the
number F-44 was filed over), and A2.1's acceptance head `b98eb3b` recorded `v0.37.0` for every
observed image on its runner. So the runner's plugin version is not a property of the repository and
is not stable even between two runners of the same workflow run -- which is exactly why Option A moved
authority to an executable the repository downloads, verifies by hash, and names on the command line.
All six records above scored `CONFORMING` from the installed binary while their `cli_plugin`
diagnostic disagreed with each other.

## K. Stage 8 -- the classifier, and the finding it exposes

`scripts/release/classify_diff.py` was **not modified** (by approval). It was run per commit and
cumulatively, from the last certified head, over the whole batch (`_tmp/a22_classifier_table.py` →
`_tmp/a22_classifier_table.json`). Every commit's answer is the same, and the cumulative answer is:

```
inheritance=INHERITED   runtime_affecting=false   release_metadata_only=true   exit=0
categories: {certification_generator: 6, ci_workflow: 2, test_harness: 8}
runtime_affecting files: none
```

Taken alone that reads as "nothing important changed", which is false, and the reason is structural:
the classifier's categories are keyed to *paths*, and `scripts/release/**` is
`certification_generator` with `runtime_affecting=false`. So `build_release_image.sh` -- which after
this batch decides which executable builds a published image -- is invisible to it, as is
`release.yml`'s gate body. **F-49**: the classifier cannot see a producer-affecting release change,
therefore "INHERITED" is a statement about the application runtime and is **not** permission to
inherit certification. The approval anticipated exactly this and said A2.2 is producer-affecting by
policy, so the final candidate is recertified in full (§O) whatever the classifier prints. The
classifier is left unmodified because widening its categories is a different decision, with its own
cost, and this batch was not given one.

## L. F-48 -- a sidecar the release gate would have mistaken for an image

Found while wiring Stage 4, measured before and after rather than asserted.

Batch 2's `build_release_image.sh` wrote its producer record to `${OUT}.producer.json`, i.e.
*beneath the same directory* the completeness gate globs as `release-image-evidence/*.json`. The
recorder was called there without `--image`, so such a file carries `"image": null` and the gate
keys it by `path.stem`. Running the pre-A2.2 gate body (lifted verbatim from
`git show 9983705:.github/workflows/release.yml`) over the records a release would have uploaded:

```
gate exit code: 1
verdict: FAIL
  * evidence for images the release does not declare: ['cap-backend.json.producer',
    'cap-egress-proxy.json.producer', 'cap-frontend.json.producer',
    'cap-sandbox-browser.json.producer', 'cap-sandbox-http.json.producer']
```

Five phantom images. Any release cut from any commit since Batch 2 would have been blocked by a
refusal whose reason had nothing to do with the images. It never fired because no release has run
since Batch 2 -- the sealed `v1.0.6-rc1` predates it -- which is the only reason this is a filed
finding and not an incident.

Two halves fix it, and both are kept. A2.2 moved the sidecars under `producer/`
(`PRODUCER_DIR="$OUT_PARENT/producer"`, pinned by
`test_the_producer_sidecars_stay_out_of_the_directory_the_gate_globs`), and the reader now
*recognises* a producer record wherever it lands instead of silently absorbing it. Running the
current gate over the identical arrangement (`_tmp/measure_f48_after.py`):

```
gate at HEAD: exit 1, verdict FAIL
  * AMBIGUOUS: cap-backend.json.producer.json is a producer record sitting in the image-record
    directory   (x5)
  * MISSING: cap-backend: the build says its producer was never recorded (recorder exit None)  (x5)
images the payload keyed: ['backend', 'egress_proxy', 'frontend', 'sandbox_browser', 'sandbox_http']
phantom-image refusals: 0 | refusals that name the producer record: 5
```

Both refuse -- the arrangement is genuinely broken -- but the second says what is wrong with it, and
keys the real five images correctly. F-48 is closed in this batch.

## M. Stage 9 -- the CI iteration log

A2.2 was developed against CI, not frozen first and tested after. Every cycle below is a push to
`main`, so each head is reproducible; the conclusion column is what that round *established*.

| # | head | run | outcome | what it established |
| - | ---- | --- | ------- | ------------------- |
| 1 | `4e6975e` | -- | not pushed alone | Stage 0 landed with the freeze test in the same commit |
| 2 | `e1d0ad1` | `35955280596` | **failure** -- `backend/Lint`, ruff `EXE001` | the two new `scripts/release/*.py` had shebangs none of their siblings has; invisible on NTFS, fatal on Linux (§T limit 1) |
| 3 | `1d880c1` | `35955959104` | **failure** -- `backend`, 1 failed / 1790 passed | the plugin-path refusal asserted a path's *absence*, which is false on a real runner (§T limit 3). Everything else about A2.2 was green in this same run: `producer-observation` and all five `release-image-builds` jobs |
| 4 | `1d880c1` | `35955959136` / `35955959114` | success (Linux, K8s) | the certification rounds at that head -- superseded the moment the head moved |
| 5 | `4a648d3` | `35957764141` | **cancelled** by the next push | its `producer-observation` and rehearsal jobs did finish and were read back (set `CONFORMING`, round bound to that sha) before the run was cancelled; `ci.yml` keeps `cancel-in-progress` because a superseded unit-test run carries no certification, so this row contributes evidence about the wiring and none about the candidate |
| 6 | `dea8c6f` | `35958562520` | **success**, 11/11 jobs | the candidate (§J for the producer evidence, §O for the five rounds dispatched at it: CI, release-layer Linux, K8s, 7200 s reliability, final-strict GA) |

Two of the six cycles were CI-only failures that local gates could not reach, and both were in code
this batch wrote. That is the expected cost of treating CI as the measurement rather than as a
formality, and it is why Stage 9 says "iterate with CI before the freeze".

## N. Stage 10 -- the freeze

```
BATCH3_A22_CANDIDATE_SHA=dea8c6f2f576bec667ee27dc8a9458673ca0982e
```

Frozen 2026-09-24 05:38 UTC against the eight conditions the approval lists; the measurements behind
each are in `_tmp/a22_freeze.txt` and §J. In short: CI green 11/11, tree clean, `origin/main` equal
to the candidate, five records CONFORMING with no contract gaps, the classifier measured per commit
and cumulatively, and no B3/B4/F-46/F-39 work in the tree.

Two things about the freeze are worth stating rather than glossing.

* The candidate moved **three times** after the last implementation commit (`e1d0ad1`): once for a
  CI-forced lint fix (`1d880c1`), once for a CI-forced test fix (`4a648d3`), and once to add a
  review-time guard for Stage 3 (`dea8c6f`). Each is a new candidate, not an amendment, and each
  invalidated the certification rounds of the head before it -- the Linux and K8s runs that were
  green at `1d880c1` are not evidence for `dea8c6f`.
* The docs commits that carry this report land *above* the candidate. That is the convention every
  round in this project has used, and it is why §R's re-audit and §O's dispatches were performed
  before any of them was written: `workflow_dispatch` takes a branch name, not a sha, so `main` had
  to still point at the candidate when the strict GA run was created.

## O. Stage 11 -- candidate-direct recertification

Every round below runs at `dea8c6f2f576bec667ee27dc8a9458673ca0982e` itself, not at an ancestor --
the distinction §K explains: the classifier called this batch `INHERITED`, and the approval said that
is not permission to inherit. A round that ran at a superseded head is not evidence for this one,
which is why the Linux and K8s runs green at `1d880c1` are not counted here.

| round | run | how triggered | conclusion | what carries the claim |
| ----- | --- | ------------- | ---------- | -------------------- |
| CI | `35958562520` | push | success, 11/11 jobs, 1792 passed | job conclusions; `cap-3a-producer-observation`, `ci-release-image-*` (§J) |
| Linux certification, **main layer** | `35958562550` | push | success -- and **not counted**, see below | `full-certification` + `postgres-version-matrix`; `cap-production-certification` was *skipped* |
| Linux certification, **release layer** | `35967302293` | `workflow_dispatch`, `layer=release`, `head_sha` verified | **success** | `cap-production-certification` success, and all three `postgres-version-matrix` legs (15/16/17-alpine) success -- the two job names the gate's `REQUIRED` table asks for, at the candidate itself (distance 0) |
| K8s certification | `35958562591` | push | success | `k8s-certification` (unconditional job, so the push run is the release-grade round) |
| GA certification, **development mode** | `35958562519` | push | success | `ga-cert-artifacts` → `cap-cert-ga/cap-28.7-ga-certification.json`: `mode: development`, `full_ga_certified: false`, `commit: dea8c6f…` |
| supply chain | (job in `35958562519`) | push | success | `supply-chain-evidence` → `images.json`, `provenance.json`, both `commit: dea8c6f…` |
| reliability soak, 7200 s | `35960848879` | `workflow_dispatch`, `soak_seconds=7200`, `head_sha` verified | **success** | `reliability` job green; `reliability-evidence` artifact |
| GA certification, **final-strict** | `35971523354` | `workflow_dispatch`, `ga_strict=true`, dispatched by `_tmp/a22_then_strict_ga.py` only after the soak concluded `success`, `head_sha` verified | **success** | `cap-28.7-ga-certification.json` (artifact `10797614781`): `mode: final-strict`, `full_ga_certified: true`, **40 of 40 gates PASS**, `skip-report.skipped: 0`, `commit == the candidate`, `release_status: "FULL GA CERTIFIED -- awaiting explicit release authorization"`, and the soak context it consumed (7200 s, 480/480 healthy ticks, 0 HTTP errors, 0 downtime seconds) |

The soak→strict ordering is not a convention this report asks the reader to trust: `cap-ga-certification.yml`'s
own `ga_strict` input says *"Dispatch the reliability soak for this commit FIRST and let it finish,
otherwise the run fails"*, and the sequencer enforces it by polling the soak's conclusion and
refusing to POST unless it is `success` *and* its `head_sha` equals the frozen candidate.

What the soak actually recorded, from `reliability-evidence` → `ga-dr/soak-context.json` (gate
"GA-GATE 24", started `2026-09-24T05:44:42Z`):

```
soak_seconds 7200 | ticks 480 | healthy_ticks 480 | http_errors 0 | downtime_seconds 0
runs_created 480  | runs_cancelled 48 | pagination_requests 1440
worker_pods_killed: 8 pods, at offsets 600..4800 s (one every ten minutes)
```

480 of 480 ticks healthy across two hours with a worker pod killed every ten minutes, no HTTP error
and no measured downtime. The same artifact also contains `cap-cert-ga/cap-28.7-ga-certification.json`
saying `mode: development`, `full_ga_certified: false`, `commit: dea8c6f…` -- which is precisely the
trap the second bullet below names, present here as a measured value rather than as a warning in the
abstract: a soak round carries a development-mode GA snapshot inside it, and reading that as the
strict claim would be F-33's defect re-opened inside §O.

**The green Linux run at the candidate is not release evidence, and reading its conclusion would
have said otherwise.** `release.yml`'s certification gate asks for a specific *job set* per
workflow:

```
REQUIRED = {
  "cap-linux-certification.yml": ("cap-production-certification", "postgres-version-matrix"),
  "cap-k8s-certification.yml":   ("k8s-certification",),
  "cap-ga-certification.yml":    ("ga-certification", "supply-chain"),
  "cap-ga-reliability.yml":      ("reliability",),
}
```

and `cap-production-certification` runs only on a `v*` tag or a dispatch with `layer: release`. A
push to `main` runs `full-certification` instead, so run `35958562550` is green *and* missing the job
the gate needs. This surfaced because §O was assembled by reading each workflow's run **job list**
against the gate's `REQUIRED` table rather than by reading the workflow conclusions, which is the
difference between "the round passed" and "the round is the evidence". The same comparison was then
made for the other three: `k8s-certification` is an unconditional job, so the push run is the
release-grade round; the GA workflow's two required jobs are also unconditional, and what makes a GA
round count is not a job but the `mode: final-strict` inside its own artifact (F-33), which is why
the strict dispatch is a separate row rather than a reading of `35958562519`.

Two readings this table refuses to make, because both have been mistakes in this repository's
history:

* **the push-triggered GA round is not the GA claim.** It is green, and its own artifact says
  `mode: development` / `full_ga_certified: false`. That is F-33's distinction, and the
  `verify-certification` gate enforces it by reading the artifact rather than the job colour (§P.2).
* **the soak's own evidence zip is not a strict GA round either.** It embeds a development-mode GA
  snapshot from inside the soak; the strict claim lives only in a `ga_strict=true` dispatch's
  `ga-cert-artifacts`.

## P. Stage 12 -- the dual-authority gate dry-run

`release.yml`'s job graph is what makes a publication need both authorities, and it was read out of
the workflow rather than described:

```
validate-tag                -> quality-gates
verify-certification        -> validate-tag                        (F-33's authority)
release-images              -> validate-tag, verify-certification
release-sandbox-browser     -> validate-tag, verify-certification, release-images
release-image-security      -> validate-tag, release-images, release-sandbox-browser
release-image-completeness  -> validate-tag, release-images, release-sandbox-browser,
                               release-image-security              (A2.2's authority)
release-chart               -> validate-tag, verify-certification, release-image-completeness
publish-release             -> validate-tag, verify-certification,
                               release-image-completeness, release-chart
```

`publish-release` needs the producer gate, so a refusal there skips publication; `release-chart`
needs it too, so the chart cannot ship pointing at images that were refused.

### P.1 The producer authority, against the candidate's real CI records

`_tmp/a22_gate_dryrun.py` lifts `ARTIFACT_GATE_PY` out of `release.yml` byte-for-byte and executes it
in a temporary directory against the five records CI actually uploaded at
run `35958562520`, with `GITHUB_SHA`/`GITHUB_RUN_ID` bound to the candidate. Four arms:

| arm | exit | gate verdict | states |
| --- | ---- | ------------ | ------ |
| `rehearsal` -- the records exactly as uploaded | 1 | FAIL | MISMATCH ×5, every one naming `job 'release-image-builds' is not one of ['release-images', 'release-sandbox-browser'] -- a rehearsal record is not release evidence`, while `producer_alignment` itself stays `CONFORMING` |
| `release` -- the same records with only publishing-only fields supplied | 0 | **PASS** | CONFORMING ×5, `failures: []` |
| `mismatch` -- `release`, with one image's `producer_alignment` and its `workflow_vs_observed` flipped | 1 | FAIL | `MISMATCH: cap-backend: workflow_vs_observed disagrees with what this repository pins` and `MISMATCH: cap-backend: producer_alignment disagrees`; the other four remain CONFORMING |
| `oci-layout` -- `release`, with the browser's hand-off left as the local layout | 1 | FAIL | `MISMATCH: cap-sandbox-browser: a published image was layered on a same-round local layout instead of the digest this release published (Stage 3)` |

This is Goal 3 stated in the negative: **one** flipped field, on **one** image, in a set otherwise
identical to the one that passed, turns publication into a refusal -- and the refusal names the
field, the image and the word. Nothing inside any `producer` document was edited for the `release`
arm; the fields supplied are the ones a non-publishing rehearsal cannot carry (`pushed`, the
published tag, `registry`, the registry-reported platform digest, the two attestation entries,
`identity.job`, `build_path.publishes`, `build_path.destination`, and the browser's hand-off), each
printed by the script.

### P.2 The certification authority, at a hypothetical tag

`_tmp/a22_cert_gate_dryrun.py` does the same for `RELEASE_GATE_PY` -- the gate that answers "was this
commit certified" -- with `CERT_TAG_SHA` set to the candidate and version `1.0.7-rc1`, a version that
does not exist as a tag. Two properties of that gate are what this dry run really exercises, and both
came from reading its code rather than from its summary:

* a run that is green **without** executing the required job set is passed over, not accepted --
  `unqualified_jobs` compares each leg against the literal string `"success"`, so `skipped` is as
  disqualifying as `failure`, and the walk-back continues to the next candidate run; and
* a **rejected authority does not send the gate looking further back**. Its own comment is the rule:
  "the newest green release-scoped round is the one this release would be relying on, and if that
  round says it did not certify, 'certified somewhere older' is not the answer." So the newest green
  run of `cap-ga-certification.yml` at the commit is read, and if it answers `mode: development` the
  release is refused even though an older strict round exists.

That second property is why §O dispatched a release-layer Linux round and a final-strict GA round at
the candidate rather than resting on the green push runs at the same commit. Before those dispatches,
the *newest* green round of each of those two workflows at the candidate was one that either skipped
a required job (Linux, main layer) or recorded `mode: development` (GA) -- so there was nothing for
the gate to accept, and no older round would have been reached anyway. The dispatches did not
displace evidence; they are the evidence, as the newest run at the commit.

The assertion that matters for this batch is that each workflow's run is selected **at distance 0** --
at the candidate itself rather than inherited from an ancestor. This batch is producer-affecting by
policy (§K), so an inherited certification is exactly what must not be enough. Result:

```
exit 0 | verdict PASS | failures [] | no ERROR verdict
tag_ref v1.0.7-rc1 (hypothetical) -> candidate dea8c6f2f576...
  cap-linux-certification.yml   run 35967302293  distance 0   (release layer)
  cap-k8s-certification.yml     run 35958562591  distance 0   authority PASS, rejects []
  cap-ga-certification.yml      run 35971523354  distance 0   authority PASS, rejects []
  cap-ga-reliability.yml        run 35960848879  distance 0
Certification gate PASS for dea8c6f2f576
```

Four for four at distance 0, both artifact authorities read `PASS` with empty `rejects`, and the
strictness of the GA row settled by the artifact rather than by the job colour -- which is the only
way that row could be trusted, since a development-mode round at the same commit is also green (§O).
The negative half of this pairing was lived rather than staged: before the two dispatches existed the
gate passed the push-time Linux round over as "green without the release job set", which is exactly
the message §O records, and no older run was reached.

What the pair establishes and what it does not: with §P.1 and §P.2 both green, a tag created at this
candidate would clear `verify-certification` *and* `release-image-completeness`, and `publish-release`
needs both. It does not establish that a release *has* been published -- no tag was created, none was
moved, and §R re-reads the sealed one to show that.

## Q. Stage 13 -- F-44 and F-47, and what this batch filed

The approval conditioned both closures on **candidate-direct recertification**, and that condition is
met: the 7200 s reliability round (`35960848879`) and the final-strict GA round (`35971523354`) both
concluded `success` at `dea8c6f`, the first with 480 of 480 healthy ticks and zero downtime, the
second with `mode: final-strict`, `full_ga_certified: true` and 40 of 40 gates PASS in its own
artifact. Together with CI, the release-layer Linux round and K8s at the same commit (§O), every round
the closure needs is direct evidence for this candidate rather than inherited from an ancestor -- the
distinction §K explains and §P.2 measures at distance 0.

**F-47** -- *"Release evidence carries a producer block no gate reads"*. Its condition was that the
reader be **live and tested**. Live: the reader is inside `release-image-completeness`, and
`publish-release` (and `release-chart`, whose asset points at the images) `needs` that job -- read
out of the workflow in §P, not asserted. Tested: 22 negative controls in
`test_release_producer_gate.py` each injecting one defect and requiring the specific word, plus
§P.1's four arms run against the candidate's real CI records. **Closed**, with the boundary in §S:
never exercised during a live publication, because that needs a tag.

**F-44** -- *"The pinned build producer is never demonstrated to be the producer of a built image"*.
Its three open statements and their measurements:

| what kept F-44 open | what closed it | measured at |
| ------------------- | -------------- | ----------- |
| the release image path builds through `docker buildx` with no `--builder` | `<abs installed path> build --builder <explicit name>` for all five images, in both release jobs and the CI rehearsal | §C, §D, §J.1, §J.3 |
| a producer mismatch blocks nothing | the completeness gate refuses in six words, all blocking | §G, §H, §P.1 |
| no *published* image has been produced by the pinned executable | **not closed by this batch, and not closable in this window** | see below |

That third row is the one worth reading carefully rather than waving. Publishing requires a tag, and
`v1.0.6-rc1`'s lifecycle is closed by standing instruction (§R). So the first published image built
by the pinned producer will be the *next* release this repository cuts -- and what A2.2 changes is
that this time it cannot be anything else without the release being refused. The claim is therefore
"the release path is the pinned producer, and the gate enforces it", not "a published image was seen
to be built by it"; the register entry says exactly that, and §V item 1 tells the next operator how to
make the stronger claim true.

Findings this batch filed:

* **F-48 -- CLOSED.** A producer sidecar beside the release evidence made the completeness gate
  invent five images. Measured before and after (§L).
* **F-49 -- OPEN, by decision.** `classify_diff.py` keys `runtime_affecting` to paths, so a
  producer-affecting release change classifies `INHERITED`. The classifier is unmodified by approval
  (§K), so the finding is the blind spot itself.
* **F-50 -- OPEN, reconciliation owed.** Eight statements in three tracked files -- the contract's
  own status strings, four in the recorder (one of them a `reason` string that lands *in a record*),
  and a `ci.yml` comment that cites a fence test which no longer exists -- still describe the world
  before the release path was switched. Found by auditing every tracked file for claims this batch
  invalidated; the in-batch reconciliation pass caught some and missed these. No behaviour is
  affected and no record is misjudged, so the finding is recorded rather than answered with another
  recertification of its own.
* **Untouched, by approval:** F-46 (the docker-socket policy), F-39 (digest reproducibility), and B3
  and B4. §S carries the same list from the other side -- what this batch was not asked to do.

## R. Stage 14 -- the sealed release, re-read

`v1.0.6-rc1`'s lifecycle is closed and nothing in A2.2 touches it. The audit is the same five reads
used in Batch 2 and Batch 3A, with the same window start (2026-09-22) so the result is comparable
with every earlier round rather than restated against a window picked to look clean -- plus one
strengthening, described below. Read-only: every call is a GET or a manifest fetch, and the
credential never leaves the process that holds it.

| Check | Value | Answer |
| ----- | ----- | ------ |
| tag object | `d0c7c7dc14cb075c095bd7970650b180a73afbf1`, annotated, tagged `2026-09-21T02:18:13Z`, peels to commit `4d8f9c72b72dcea74e8588374fe8ef3a04564eb6` | unchanged |
| tags created on/after 2026-09-22 | none (12 tags, newest `v1.0.6-rc1`) | no tag was created |
| release `v1.0.6-rc1` | `published_at == updated_at == 2026-09-21T02:38:59Z` | never edited |
| its 5 assets | all created and updated `2026-09-21T02:38:58/59Z`, `download_count` 0 each | none replaced |
| the five `1.0.6-rc1` images on ghcr | resolve at HTTP 200, and each tag re-read *by digest* returns the same digest | tags intact |
| digest drift | backend `a733b90c7a84…`, frontend `e1b1889a868c…`, egress-proxy `8ab8c234f278…`, sandbox-http `36bb2f7993ac…`, sandbox-browser `b369618871bd…` -- all five **byte-identical to the Batch 2 record** | no re-pointing |
| promotion | newest non-prerelease, non-draft release is still `v1.0.5` (`v1.0.6-rc1` remains `prerelease=true`) | the RC was never promoted |

Verdict: **SEALED RELEASE INTACT**. The strengthening is the digest-drift row: the previous rounds
could only prove the tags still resolve, because the published `values-release-1.0.6-rc1.yaml` asset
names no image digests (re-measured here: 0 digest lines). Comparing against the digests Batch 2
recorded is what actually proves a tag was not repointed, and the A2.2 audit adds it to the verdict
so "intact" cannot be reported while a published tag has moved.

Three reads that do **not** work from this box and are therefore not claimed: `git ls-remote
--tags` (github.com:443 direct is blocked), ghcr's `/v2/<pkg>/referrers/<digest>` (404 for these
manifests, so the sealed attestations cannot be re-verified from here), and the container-packages
`/versions` + `/versions/{id}/tags` pair (answers 200 but never lists `1.0.6-rc1`, so "which package
version owns the tag" is unanswerable this way).

## S. What this batch deliberately did not do

| Item | State | Why it stayed out |
| ---- | ----- | ----------------- |
| B3 (build-once-promote-many, registry-less promotion) | not started | excluded by approval; its design is in `docs/quality/cap-post-rc-batch-3-design-options-2026-09-23.md` |
| B4 | not started | excluded by approval |
| F-46 (Linux certification pins the docker-socket disclosure instead of requiring isolation) | OPEN, unchanged | a policy decision about a shipped deployment path; excluded by approval, and Batch 3 §I already reviewed it as policy without changing a gate |
| F-39 closure | not claimed | excluded by approval; the reproducibility measurement from Batch 1 stands and nothing here re-ran it |
| release publication / tag creation | **none performed** | no tag was created, moved, deleted or recreated; no image was published; publication was not started automatically. Everything in §P is a dry run of lifted gate code in a temporary directory against a hypothetical version |
| the next *real* release's build | not observed, and cannot be | the gate is live and tested, but `release.yml` has not run since it became a producer authority, because running it requires a tag this batch may not create (§Q states what that leaves unproven) |

## T. Local gates, and where this box's platform hid things

Every local gate was run at the candidate; two of them are weaker on Windows than they look, and
both cost a CI cycle to discover.

* `ruff check backend/app backend/tests benchmarks/phase22 scripts` -- the repo's own gate command.
  **Platform limit 1:** `EXE001` (missing shebang) is decided from the file's *exec bit*, which NTFS
  does not carry, so ruff cannot see the defect here and does see it on Linux. Two new scripts
  carried shebangs their siblings do not have; CI's `backend/Lint` caught it (run `35955280596`).
* **Platform limit 2:** a Python-launched `bash` on this box resolves to the WSL stub, which
  reported 42 syntactically-valid workflow run blocks as errors with empty stderr.
  `_tmp/check_workflows.py` now names Git's own bash (`%ProgramFiles%/Git/bin/bash.exe`) and reports
  0 errors for every `ci.yml` and `release.yml` block in the batch.
* **Platform limit 3, and the batch's one real CI failure:**
  `test_a_target_inside_a_cli_plugin_directory_is_refused_before_anything_is_downloaded` asserted
  `not Path("/usr/libexec/docker/cli-plugins/docker-buildx").exists()` after the refusal. True on
  this box, false on the runner -- where that file is precisely what the refusal protects -- so the
  assertion measured the machine instead of the behaviour and passed locally while CI went red
  (run `35955959104`, 1 failed / 1790 passed). It now snapshots `(size, mtime_ns, mode)` before and
  after and requires them equal, which holds whether or not the path exists, and additionally
  requires the refusal to *name the plugin directory* so the arm cannot pass by refusing for an
  unrelated reason. No behaviour of the installer changed: it refused before any download at both
  heads. This is recorded because it is the same class of mistake as limit 1 -- local green is not
  evidence about the platform the code ships on.
* Backend suite, at the candidate and mirroring CI's command: **1790 passed, 135 skipped,
  1 deselected in 1427.93 s**. Getting there took three attempts, and the first two were this box's
  fault rather than the code's: the first run was started with `-o addopts=` but *without* CI's
  `--ignore` list or `--timeout`, so it reached the Linux-only phase-28.5 files and wedged (no
  output for 20 minutes, one process at 0 CPU); an earlier attempt had been killed while its output
  was still buffered. Run one full suite at a time on this machine -- concurrent ones produce
  `0xC0000142` in subprocess tests, which is resource pressure and not a product bug -- and reproduce
  CI's exclusions, because a local suite that runs a different set of files is measuring a different
  thing.
* **Limit 4, and this session's own tooling.** A script that waits for the soak and then dispatches
  the final-strict GA round reported `exit=0` while the Python child had actually crashed: the
  command line ended in `| tail -8`, so the shell reported the pipe's status, not the tool's. Two
  polling tools then shared one scratch file (`_tmp/_last.json`), so one read the other's
  half-written API answer and died with `JSONDecodeError: Extra data` three frames away from any
  cause. What made it visible was the only check that counts -- the dispatched run was simply absent
  from the workflow's run list -- and both are fixed: `_tmp/gh_ro.py` now writes a per-process
  scratch file, and a dispatch is confirmed by reading back the run the API created and comparing its
  `head_sha` to the candidate, never by a command's exit code. Same lesson as limits 1-3, applied to
  the instruments rather than the product: a green tool exit is not evidence that the thing happened.

## U. Verdict

### BATCH 3 A2.2 COMPLETE — PRODUCER AUTHORITY RECERTIFIED

Against the four goals the batch was approved on:

1. **release-equivalent builds actually use the controlled producer** -- §C's one mechanism, ordered
   so a checksum mismatch stops before execution and a builder is never created by a binary that has
   not identified itself; §D's build line `<abs path> build --builder <name>` for all five images in
   both release jobs and the CI rehearsal; §J's measurement of all nine install steps,
   `integrity=equal`, read-back `v0.37.1 0b265a9f62db…`, `built_with=/tmp/cap-controlled-buildx/buildx`
   and the named builder running the pinned BuildKit digest, on GitHub runners.
2. **producer evidence is bound to each image / run / SHA** -- §F's record, and §J.3: six records with
   four `CONFORMING` comparisons, `producer_alignment CONFORMING`, no contract gaps, and identity
   naming this run and this commit.
3. **producer mismatch or unreadable evidence blocks publication** -- §G's reader inside
   `release-image-completeness`, §H's six blocking words, §I's 22 controls, and §P.1's four arms run
   against the candidate's real CI records: PASS on the release-shaped set, FAIL naming image and
   word when one field is flipped, FAIL on a same-round layout, and the rehearsal records refused as
   release evidence.
4. **F-44 and F-47 closed only after candidate-direct recertification** -- §O's five rounds all
   `success` at `dea8c6f` (CI, release-layer Linux, K8s, 7200 s reliability, final-strict GA with
   `full_ga_certified: true`), §P's two authorities both dry-run at distance 0, §Q's dispositions.

What this batch did **not** do: create, move, delete or recreate any tag; publish any image or
release; start publication automatically; touch B3, B4, F-46 or F-39; modify `classify_diff.py` or
F-33's and F-42's tests. `v1.0.6-rc1` is intact and `v1.0.5` is still the stable release (§R).

Findings: **F-44 CLOSED**, **F-47 CLOSED**, **F-48 CLOSED** (found and fixed here), **F-49 OPEN**
(the classifier's blind spot, unmodified by approval), **F-50 OPEN** (stale producer prose this batch
left behind).

Two boundaries stay true and are part of the verdict rather than footnotes to it. First, no
*published* image has been built by the pinned producer yet: that requires a tag, and the sealed
release may not be re-run, so the strongest claim available is that the release path now is the
pinned producer and the gate refuses a release that is not. Second, F-50's eight sentences in three
tracked files still describe the pre-A2.2 world -- no behaviour depends on them, and they are owed a
reconciliation at the next candidate.

## V. What a reviewer should check next

In order, with the command that answers it:

1. **Whether the next real release actually blocks.** This batch proved the reader refuses in a
   dry run; it could not prove it refuses *during a publication*, because that needs a tag. Cut any
   release from a commit at or after the candidate and read
   `outputs/release-images/release-images-<version>.json`'s `producer_verdicts` and
   `producer_summary` before approving the asset upload. If all five are `CONFORMING`, the claim in
   §Q holds; if any is not, publication stops, and that is the feature.
2. **That the certification rounds behind a release are the *required* jobs, not merely green
   workflows.** §O explains the case this batch nearly recorded wrong: a push-triggered
   `cap-linux-certification.yml` run passes with `cap-production-certification` **skipped**, while
   the gate's `REQUIRED` table demands that job by name. Before treating a round as evidence, compare
   its job list with that table -- `python _tmp/run_status.py <run-id>` prints conclusions per job,
   and the table is in `.github/workflows/release.yml` next to the gate. The same check distinguishes
   a `ga_strict=true` round from a development-mode one that the artifact has to settle (§O).
3. **Whether `identity.job`'s allow-list still matches the workflow.** It is a tuple in the gate
   body and job names in `release.yml`; `test_a_rehearsal_record_cannot_stand_in_for_a_release_build`
   fails if they drift.
4. **Whether the freeze is still true.** Re-run
   `backend/.venv/Scripts/python.exe -m pytest tests/test_producer_contract_freeze.py` -- it fails
   if the recorder stops filling a required path, if an exemption list narrows, or if a cited
   enforcing test is renamed away.
5. **Which producer built a given shipped image.** `record_build_producer.py`'s output is inside the
   image record CI/Release uploads; `built_with`, `observed.controlled_buildx.integrity` and
   `observed.builder.running_image.digest` answer it without asking the runner anything.
6. **F-49 still stands.** If a future change edits `app/` *and* the release scripts, the classifier
   will report `runtime_affecting=true` for the first and nothing for the second. The register entry
   says what that means; do not let an `INHERITED` line in a table be read as "the producer is
   unchanged".
7. **The three deferred decisions** (§S) are unchanged and still need a decision from the project,
   not an implementation from a batch.

## W. Reproduction

Everything below is read-only against the repository or the Actions API; none of it publishes
anything.

```bash
# the batch's own test files, at the candidate
cd backend && .venv/Scripts/python.exe -m pytest tests \
  -p no:cacheprovider -q --tb=short -o addopts=

# the repo's lint gate (note §T limit 1: EXE001 needs Linux)
.venv/Scripts/python.exe -m ruff check backend/app backend/tests benchmarks/phase22 scripts

# Stage 0, the freeze, on its own
python -m pytest tests/test_producer_contract_freeze.py -p no:cacheprovider -q -o addopts=

# Stage 12: the producer authority, run against the five real CI records at the candidate
python _tmp/a22_gate_dryrun.py <ci-run-id> <candidate-sha>

# Stage 12: the certification authority (needs the cert rounds complete at the candidate)
python _tmp/a22_cert_gate_dryrun.py --version 1.0.7-rc1 --expect pass

# Stage 8: the classifier, per commit and cumulatively
python _tmp/a22_classifier_table.py

# Stage 14: the sealed-release re-audit
python _tmp/a22_sealed_integrity.py

# F-48, before and after
python _tmp/measure_f48.py 9983705
python _tmp/measure_f48_after.py HEAD

# the CI evidence read-back
python _tmp/read_a22_evidence.py <ci-run-id>
python _tmp/run_status.py <run-id>
python _tmp/watch_head.py <sha-prefix> 240
```

`_tmp/` is session scratch and is not part of the repository; the values it prints are reproducible
from the named commits, runs and artifacts, which is what the report cites.
