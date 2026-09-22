# CAP POST-RC HARDENING — BATCH 1 REMOTE VALIDATION REPORT

Date 2026-09-21. Input verdict: `READY FOR REVIEW / PUSH`
(`docs/quality/cap-post-rc-batch-1-closure-2026-09-21.md` §O). This stage does four things and
nothing else: confirm the integration policy, push the audited HEAD, observe what the push
triggered, and test batch 1's implementation in the real GitHub Actions environment. It creates no
tag, touches no part of the sealed `v1.0.6-rc1` publication, dispatches no strict/final GA round,
and does not start Batch 2.

Transcripts are under `_tmp/` (`push_batch1_main.log`, `remote/`, `v1_*`, `stage5*`); the reader
can re-issue every remote number below with GETs against
`api.github.com/repos/laolaola278-dev/cyber-agent-platform` and `ghcr.io/v2/…`.

## A. Integration-policy decision

The question Stage 0 asks is whether `main` may receive this history directly. Four independent
reads, plus one limit:

| Check | Result |
| --- | --- |
| `GET /repos/…/branches/main` (anonymous) | `"protected": false`, `protection.enabled: false`, `required_status_checks.enforcement_level: "off"` |
| `GET /repos/…/branches/main/protection` (authenticated) | **404 "Branch not protected"** — no rule set exists on `main` |
| `GET /repos/…/rulesets` | `[]` — no repository rulesets |
| `CONTRIBUTING.md` | requires a check list (`ruff`, full `pytest`, frontend lint/build) and "reviewed bug fixes" for the `release/1.0` line; it states no PR/merge-queue requirement for `main` |
| History shape of `origin/main` | no merge commits anywhere; the last two commits on it are `release: cut 1.0.5-rc1 anchor` and `release: promote 1.0.5-rc1 to 1.0.5`, both landed directly on `main` |
| Workflow triggers | `ci.yml` and `cap-ga-certification.yml` both declare `push: branches: [main]` (and `pull_request`), so a direct push to `main` is a supported integration path in this repository's own automation |

**Decision: direct fast-forward push to `main` is the repository's integration path as configured.**
No review branch, PR or merge queue is required by anything observable, and the history shows that
path actually used. The limit worth stating: `protected: false` describes GitHub's *server-side*
rule set, not a human convention; if the owner's unwritten practice is "rc work stays on
`release/**` until promotion", that is not visible to this check and the user is the authority on
it. The push was executed because it was explicitly authorized for this stage and because the
server enforces its own policy at push time (Stage C) — an unprotected branch is what GitHub
accepted.

**Policy consequence for certification, stated before the push rather than after:** pushing does
not certify anything. `origin/main → HEAD` classifies `RECERTIFICATION_REQUIRED` (150 files, 71
runtime-affecting) because `origin/main` is still the v1.0.5 line and the 136 commits in between
carry the whole rc1 implementation. What is INHERITED is batch 1 relative to the sealed and
certified lines (§B), which is the only inheritance claim this report makes.

## B. Push topology

Measured immediately before the push, at the audited tip:

| Item | Value |
| --- | --- |
| `git rev-parse HEAD` | `49de1081fa56d6f08687c5b652dc4ab4fa9e3157` |
| `git rev-parse origin/main` | `32ec298951485633c7019f83a18108a2bcddcb76` |
| `git merge-base origin/main HEAD` | `32ec298951485633c7019f83a18108a2bcddcb76` → **origin/main is an ancestor of HEAD: the push is a fast-forward** |
| `git rev-list --count origin/main..HEAD` | 136 ahead, 0 behind |
| `git diff --name-only origin/main..HEAD` | 150 paths |
| `git log origin/main..HEAD --not --remotes=origin` | exactly **18 commits** — batch 1's 15 audited commits plus this report's 3 (`a7c3c20`, `803712f`, `49de108`); every other commit in the range is already public on `release/1.0.6-rc1` or `cert/*` |
| `classify_diff.py 4d8f9c7 HEAD` | `INHERITED`, `runtime_affecting=false`, `release_metadata_only=true`, 11 files, exit 0 |
| `classify_diff.py origin/main HEAD` | `RECERTIFICATION_REQUIRED` (expected; see §A) |

So the push discloses 18 commits that were not previously public and changes no ref that was.

Pre-push integrity checks, all re-run rather than carried over from the audit transcript: working
tree clean; HEAD equal to the audited tip; `v1.0.6-rc1` → `d0c7c7dc14cb075c095bd7970650b180a73afbf1`
peeling to `4d8f9c72b72dcea74e8588374fe8ef3a04564eb6`; `origin/release/1.0.6-rc1` still
`4d8f9c7…`; `scripts/release/classify_diff.py` unmodified (0 commits, 0 diff lines in the range);
and because three docs-only commits had landed after the audit's measurement point, the cumulative
ranges were re-measured at the push candidate — `4d8f9c7`/`b671f53`/`d30b4e7` → HEAD all
`INHERITED` with 11/15/14 files (`_tmp/prepush_*.json`). The 15-module release/contract/document
regression set was re-run at the push candidate too: **260 passed, 0 failed, 4 skipped**
(`_tmp/prepush_regression.xml`), and `ruff check` on both changed test modules is clean.

## C. Push evidence

`git push origin HEAD:refs/heads/main` — plain refspec, no `--force`, no `--force-with-lease`, no
`--no-verify`. A `--dry-run` of the same command was executed first and printed
`32ec298..49de108 HEAD -> main`.

| Item | Value |
| --- | --- |
| old `origin/main` | `32ec298951485633c7019f83a18108a2bcddcb76` |
| pushed | `49de1081fa56d6f08687c5b652dc4ab4fa9e3157` |
| remote output | `32ec298..49de108  HEAD -> main` (a `..` range, i.e. fast-forward; a rewrite would print `+` and `forced update`) |
| push exit status | **0** |
| `git ls-remote origin refs/heads/main` after push | `49de1081fa56d6f08687c5b652dc4ab4fa9e3157` — **matches the intended HEAD** |
| server-side policy reaction | accepted: consistent with §A's unprotected `main` |
| tags created | none (`git tag --points-at HEAD` empty; remote tag list identical, 12 tags) |
| transcript | `_tmp/push_batch1_main.log` |

## D. Remote workflow runs

Four runs were created by this push, all with `head_sha == 49de108…` and `event == push`; nothing
older is used as evidence here. Enumerated from
`GET /actions/runs?head_sha=49de1081fa56d6f08687c5b652dc4ab4fa9e3157` and snapshotted per run in
`_tmp/remote/run_<id>.json`.

| Run | Workflow | Event | head_sha | Status / conclusion | Jobs | URL |
| --- | --- | --- | --- | --- | --- | --- |
| `35594554033` | `ci.yml` (CI) | push | `49de1081` | completed / **success** | 10: 10 success | <https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35594554033> |
| `35594554182` | `cap-linux-certification.yml` | push | `49de1081` | completed / **success** | 6: 4 success, 2 skipped (`cap-production-certification`, `fast-certification`) | <https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35594554182> |
| `35594554041` | `cap-ga-certification.yml` | push | `49de1081` | completed / **success** | 2: `supply-chain` 11:32:03→11:34:39Z, `ga-certification` 11:32:03→12:29:01Z (**57 minutes, green**); uploaded `ga-cert-artifacts` (2 209 151 bytes) alongside `supply-chain-evidence` | <https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35594554041> |
| `35594554119` | `cap-k8s-certification.yml` | push | `49de1081` | completed / **success** | 1: `k8s-certification` success; uploaded `k8s-cert-artifacts` (3325 bytes) recording `commit 49de1081fa56…`, `gate_summary 34/34, failed 0, not_run 0` | <https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35594554119> |

No `release.yml` (Release/publication) run exists for this head — `GET
/actions/workflows/release.yml/runs` still has `35553750674` at `4d8f9c7` as its newest (§I).

The Linux run's two skipped jobs are the interesting part, and they did **not** behave as
expected. In substance a push-to-`main` Linux run is not release evidence — the
`cap-production-certification` leg never executed — and §G shows the gate accepting it anyway:
`find_evidence` requires each listed job to be *present* in the run, while the comment above
`REQUIRED` claims a job skipped by its `if` is *absent*. GitHub reports it as present with
`conclusion: "skipped"`. That is recorded as finding **F-42** in `docs/known-issues.md`, with its
severity limit and its reviewed fix; it was not repaired inside this stage.

## E. CI results

`ci.yml` at the pushed head, from the run's own job records and its uploaded
`backend-evidence` artifact (`junit-backend.xml`), not from a paraphrase:

| Item | Value |
| --- | --- |
| run / conclusion | `35594554033` — **success**, 10 of 10 jobs successful |
| jobs | `frontend`, `backend`, `migration`, `packaging`, `image-and-security`, and five `release-image-builds (…)` legs — all success |
| backend unit job | `collected 1644 items / 1 deselected / 1643 selected` → **1508 passed, 135 skipped, 1 deselected** in 971.41 s (16:11) |
| junit totals | `tests=1643 failures=0 errors=0 skipped=135` |
| coverage contract | `Required test coverage of 90% reached. Total coverage: 91.79%` |
| batch-1 modules in that run | `test_release_publication_gate.py` 48 items (46 run, 2 skipped); `test_third_party_image_lock.py` 21 items, 0 skipped |
| duration | `backend` job `11:32:04Z → 11:48:41Z`; the run opened `11:32:00Z` and closed `11:48:42Z` |

The 135 skips are infrastructure-gated, not quiet passes: CI's own junit lists 18 distinct reasons,
headed by `kind cluster unavailable` (57), `PostgreSQL not reachable` (34), `MinIO not reachable`
(12), `real chromium browsers not installed` (7), `sandbox image not built` (4), `gate24 soak has
not run in this session` (4) and `supply-chain toolchain unavailable: ['syft', 'trivy']` (4), with
the largest module contributors being `test_phase_28_6_k8s_certification` (31),
`test_phase_28_7_ga_certification` (16) and `test_phase_28_3_postgres_concurrency` (13) — the legs
that need a cluster, a real server or a container runtime the unit runner does not provide. The two
credential-gated checks in §F are among them. Locally, the same command line produced the same
1508/135/1 split. No test was added, removed, altered or re-marked to get here.

## F. Live API test results

The two checks that skipped locally for want of credentials ran in CI and **skipped there too**,
with their reasons read out of CI's own junit transcript rather than inferred:

| Test | CI outcome | Recorded reason |
| --- | --- | --- |
| `test_the_authoritative_artifact_is_where_the_gate_reads_it` | **SKIPPED** | `gh could not list GA certification runs from here` |
| `test_the_actions_api_paths_the_gate_uses_exist` | **SKIPPED** | `gh could not answer the live Actions API here (no binary, no credential, a rate limit, or insufficient scopes)` |

So the credential-free assumption "a GitHub runner means these checks execute" is false here, and
the report does not pretend otherwise. The configuration explains it: `ci.yml` declares
`permissions: {contents: read}` at workflow level, so the `GITHUB_TOKEN` the `backend` job receives
has no Actions read scope, and every endpoint these two tests call is under
`/repos/…/actions/…`. That much is read from the workflow file in this commit; whether `gh` itself
is on the runner image was **not** asserted, because nothing was run to observe it and the skip
reason deliberately covers both possibilities.

**Consequence: REMOTE-CI VALIDATION PENDING, narrowed but not closed.** What CI now proves is the
executed half — 46 of the gate module's items, the whole 21-item F-37 guard, and the coverage
contract. What it still does not prove is that the resolver's *live* reads (`gh api` for run
listings and artifact zips, and the artifact-member path inside a real uploaded zip) behave as the
fixtures model them. Closing that needs one reviewed change — a job-level
`permissions: actions: read` on the `backend` job — and that is a workflow edit with a CI cost, not
something to do unreviewed inside a validation stage.

> **Follow-up, 2026-09-22 (batch 1.1): that hypothesis was incomplete, and the record says so.**
> `actions: read` was granted and pinned by a contract test, and the two checks then **executed** —
> but failed, on `gh`'s own words: `` `gh api` exited 4: To use GitHub CLI in a GitHub Actions
> workflow, set the GH_TOKEN environment variable ``. A scope was necessary, not sufficient: nothing
> in that job puts a token into `gh`'s environment, so what remains is one step-level
> `env: GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}` and a re-run. `LIVE API READ PATH` stays PENDING; what
> changed is that the gap now announces itself as a red job instead of a green one full of skips.
> Detail: `docs/quality/cap-post-rc-batch-1-1-release-gate-closure-2026-09-22.md` §K.

## G. F-33 development-GA rejection evidence

**What the push produced is the F-33 scenario in the flesh.** `cap-ga-certification.yml:37` sets
`CAP_GA_STRICT: ${{ (github.event_name == 'workflow_dispatch' && inputs.ga_strict == true) && '1'
|| '0' }}`, so a `push` event runs that workflow in development mode by construction; there, a gate
with no evidence in the job is `PLANNED` instead of failing, so the job finishes green while the
round's own record says it did not certify. Run `35594554041` did exactly that: **57 minutes, both
jobs success, run conclusion success** — and it uploaded its verdict under `ga-cert-artifacts` with
`if: always()`, the same name a strict round uses, so nothing about the artifact's *name* says
which mode produced it.

**The dry-run, and what it substituted.** `_tmp/stage5_gate_live_dryrun.py` lifts the gate heredoc
verbatim out of `.github/workflows/release.yml` and executes it here — `find_evidence`,
`authoritative_artifact`, `decide`, the error handler, the exit code. `git rev-list`, `git
rev-parse` and `scripts/release/classify_diff.py` run for real in this checkout; every `gh api`
call is answered by a **GET to the same endpoint** through `curl`, because `gh` is not installed
here. That is the only substitution, and it is the same substitution §F's two skipped tests stand
for, so this section should be read as *the gate's reading logic over live data*, not as the
Actions runtime. Nothing was tagged, dispatched or pushed; the tag SHA was passed in as an
environment value and the evidence file went to a temp directory.

Two gate versions, one remote state (`_tmp/stage5_dryrun_final.log`):

```
the same remote state, two gate versions (read-only, nothing tagged):
  gate at 4d8f9c7 (pre-F-33)   exit=0 verdict=PASS  GA run=35594554041 at 49de1081  authority=(not read by this gate version)
  gate at the pushed HEAD      exit=1 verdict=FAIL  GA run=35594554041 at 49de1081  authority=REJECTED
```

| Leg | Selected | What was read from the run's own artifact | Verdict |
| --- | --- | --- | --- |
| `cap-ga-certification.yml` | run `35594554041` at `49de1081`, **+0**, jobs `{"ga-certification": ["success"], "supply-chain": ["success"]}` | `mode = "development"`, `full_ga_certified = false`, `gate_summary = {"total": 40, "implemented": 38, "passed": 35, "failed": 0, "not_run": 0, "skipped": 0, "planned": 5}`, `commit = 49de1081fa56…` | **REJECTED**, four reasons: `records mode='development', which is not 'final-strict'`; `records full_ga_certified=False, which is not True`; `gate_summary.planned=5, requires 0`; `gate_summary counts 35 of 40 gates passed, so a gate has no PASS behind it` → gate `FAIL`, **exit 1**, `RELEASE BLOCKED` |
| `cap-k8s-certification.yml` (positive control) | run `35594554119` at `49de1081`, +0, `k8s-certification: ["success"]` | `commit` equal to the selected SHA, `gate_summary {"total": 34, "passed": 34, "failed": 0, "not_run": 0}` | **PASS** — the authority leg accepts what a round actually certified, so the refusal above is not a blanket rejection |
| `cap-linux-certification.yml` | run `35594554182` at `49de1081`, +0 | no `AUTHORITY` entry exists for this workflow | accepted on job presence, with `cap-production-certification: ["skipped"]` — **F-42**, §D |
| `cap-ga-reliability.yml` | run `35506714369` at `b671f53`, **+35** | distance re-checked live: `diff_verdict INHERITED`, `runtime_affecting false` | accepted, and the walk-back still works at 35 commits |

**The instrument is not doing the work.** Run against the same endpoint *before* the dev round
finished — while the newest GA run was still the sealed strict one — both gate versions agreed
(`exit=0 PASS`, authority `PASS` on run `35512844679` at `b671f53`):
`_tmp/stage5_dryrun_both_before_ga.log`, `_tmp/stage5_dryrun_before_ga.log`. The divergence appears
precisely when a development round becomes the newest evidence, which is the behaviour F-33 was
written to produce, demonstrated on this repository's real runs rather than on a fixture.

**Operational consequence, and the limit of this proof.** The gate does not walk past a refusal, so
a tag cut at this head is now blocked until a strict GA round runs at the tag's own commit or a
newer ancestor — the dispatch discipline (soak, then strict GA at the same SHA) is load-bearing, not
optional. What remains unproven is the *Actions runtime* itself: that the step's exit status really
skips the publishers behind `needs`. It is structurally pinned and measured locally by exit code
(closure report §E), and it cannot be observed without creating a tag, which this stage forbids.
**END-TO-END TAG PATH = PENDING.** No tag was created to test it.

## H. V1 / V2 status

**Both moved from UNVERIFIED to observed, read-only, on the same day as the push** — and the
earlier "cannot be read from here" verdict had a wrong premise that is corrected rather than
quietly replaced.

| Item | Previous record | Measured now |
| --- | --- | --- |
| V1 — pushed attestation content | UNVERIFIED; `GET …/manifests/v1.0.6-rc1` → 404 | **Answered.** The 404 was the wrong ref: the image tag is `1.0.6-rc1` (the `v` prefix belongs to the git tag). `manifests/1.0.6-rc1` and the recorded index digest both answer 200 for all five images. Observed as stored: `predicateType https://slsa.dev/provenance/v1`; `subject` = the **platform manifest** digest, named `pkg:docker/ghcr.io/laolaola278-dev/<image>@1.0.6-rc1?platform=linux%2Famd64`; `buildType https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md`; **`runDetails.builder.id` is the empty string on all five**; `buildkit_completeness {"request": true, "resolvedDependencies": false}`; `vcs.revision 4d8f9c72…` present on `cap-backend` and `cap-frontend` only; `resolvedDependencies` 4 / 4 / 2 / 2 / 2 entries carrying base-image digests; 3–10 per-step layer digests; no `materials`, no `runDetails.version`, empty `buildDefinition.invocation`; no BuildKit or buildx version string anywhere in the predicate |
| V1 — signing | not observable | No `sha256-….sig` / `.att` tag exists for any of the five packages, the attestation layers arrive as unsigned OCI artifacts, and `referrers/<index-digest>` answers 404. **No `gh attestation verify` was run** (no `gh` here), so all of the above is a hand-parsed reading of served bytes, not a signature verification |
| V2 — sealed release state | UNVERIFIED | **Answered.** Run `35553750674` and its 21 artifacts are readable; `release-images-1.0.6-rc1.json` names five refs with index digests and `attestations {sbom: true, provenance: true}`, all `pushed: true`; the old gate's own `release-certification-gate.json` (verdict `PASS`) contains **no `authority` field at all** — the F-33 blind spot, in the sealed release's own evidence. Registry-side, the digests served today for `1.0.6-rc1` equal the recorded index digests for all five images (`_tmp/v1_tag_digest_check.json`) |
| Effect on item B | chain unstarted | Its first link is satisfied: `V1 → define the observed contract → B3`. The observed value is what B3 must now reckon with — an assertion on `builder.id` would assert a blank. B4's premise is measured too: signing is absent. **B2, B3 and B4 are still unexecuted.** Full record: `docs/quality/cap-provenance-identity-observation-2026-09-21.md` §6 |

Access note, because it is the kind of thing a later reader should not have to rediscover: this
box still has no `gh` and no `GH_TOKEN`/`GITHUB_TOKEN` in the environment, and `~/.docker/config.json`
still has an empty `auths` map. What was missing before was not a credential — it was the right ref
and the plain HTTPS path. The git credential in the Windows credential manager (a `ghp_`-prefixed
PAT) is accepted by both the GitHub API and ghcr's token endpoint. It was used for **GETs only**;
the token was passed to `curl` through stdin config so it appears in no argument list, no file and
no log line, artifact redirects were followed in a second request **without** the credential so it
could not ride along to a storage host, and no write call of any kind was made.

## I. Sealed-release side-effect audit

Re-read after the push, from the remote itself:

| Required check | State | How |
| --- | --- | --- |
| No new release tag | **confirmed** — remote tag list is the same 12 (`v1.0.0` … `v1.0.6-rc1`), none at the pushed head | `GET /git/refs/tags`; `git tag --points-at HEAD` → empty |
| `v1.0.6-rc1` tag not moved | **confirmed** — `d0c7c7dc14cb…` peeling to `4d8f9c72b72d…`, identical locally and remotely | `git rev-parse`, `git ls-remote`, `git cat-file -p` |
| `release/1.0.6-rc1` not modified | **confirmed** — still `4d8f9c72b72d…` | `git ls-remote origin refs/heads/release/1.0.6-rc1` |
| No publication workflow run from a branch push | **confirmed** — `release.yml`'s newest run is still `35553750674` at `4d8f9c7`; no run exists at `49de108` | `GET /actions/workflows/release.yml/runs` |
| No new GitHub Release, no asset rewritten | **confirmed** — the newest release is still `v1.0.6-rc1` (created `02:18:13Z`, published `02:38:59Z`, all five assets `updated_at 02:38:59Z`), and the push happened at `11:31:54Z` | `GET /releases`, `GET /releases/tags/v1.0.6-rc1` |
| No stable/latest promotion | **confirmed** — `GET /releases/latest` → `v1.0.5`, `prerelease: false`, published `2026-09-07T14:59:44Z` | same |
| Published images unchanged | **confirmed from the registry side** — the index digests served for `1.0.6-rc1` equal the publication record for all five; nothing was pushed, retagged or deleted, and all ghcr calls were `pull`-scoped GETs | `_tmp/v1_tag_digest_check.json` |
| Development GA produces no release publication | **confirmed** — `cap-ga-certification.yml` has no registry login and no `--push` path: its side effects are test execution plus `upload-artifact` (`ga-cert-artifacts`, `supply-chain-evidence`). Publication lives in `release.yml`, which triggers only on tag pushes and has no run at this head, and the five index digests the registry serves for `1.0.6-rc1` are still the recorded ones | `cap-ga-certification.yml`; `GET /actions/workflows/release.yml/runs`; `_tmp/v1_tag_digest_check.json` |

## J. Remaining pending items

1. **F-42 — a skipped release job reads as executed evidence (§D, §G).** Recorded in
   `docs/known-issues.md` item 10 with its measured basis and severity limit. The fix is one
   predicate in `find_evidence` (every listed job's conclusions must be `"success"`) plus a fixture
   where the required job is skipped; it is a `ci_workflow` edit, so it costs a CI cycle and no
   re-certification. Deliberately not made in this stage.
2. **The two live Actions/API tests still skip in CI** (§F). Needs one reviewed workflow change —
   `permissions: actions: read` on the `ci.yml` `backend` job — to make them execute. Until then the
   resolver's live read path is modelled, and read here only through the curl substitution in §G.
3. **An actual tag-time execution of the gate remains untested** (§G). This stage produced the
   remote inputs the gate reads, but creating a tag to watch it refuse one is prohibited here, so
   END-TO-END TAG PATH = **PENDING**.
4. **A tag at this head is now blocked by design.** The newest GA round is the development one at
   `49de1081`, and the gate does not walk past a refusal — so the next RC must run the soak and then
   a strict GA round at its own SHA before it can publish. That is F-33 working, and it makes the
   dispatch discipline load-bearing rather than conventional.
5. **`gh attestation verify` equivalent never run** (§H): the pushed attestations are read, not
   verified by a reference tool; no signing identity exists to verify against.
6. **G(i) two-independent-build comparison — still PARTIAL.** The push did not create it; the
   registry reading only shows that part of the missing structural metadata (per-step layer
   digests) already sits in the pushed attestations.
7. **H / F-25 record — still DEFERRED**, now blocked on a decision rather than on access: run
   `35553750674`'s artifacts are downloadable and the served digests match the record.
8. **Batch 2 items unchanged:** B2 pinning, B3 (now writable from observation), E2, E3, F (24 h
   soak), G(ii)–(v).

## K. Batch-2 readiness recommendation

Ready to start, with four concrete changes to the plan's assumptions that this stage produced:

1. **F-42 first** (§D, §G). Same failure mode F-33 closed — a colour where a decision should be —
   one layer down, found by running the gate over live data rather than by reading it. One predicate
   plus a skipped-job fixture; `ci_workflow`, so it costs a CI cycle and no re-certification. It
   should land before anything else edits `release.yml`, because every later claim about that gate
   inherits the hole.
2. **The CI permission gap next**: one job-level `permissions: actions: read` makes the two live
   resolver tests (§F) execute, which is what turns §G's curl substitution into an observed Actions
   result. `ci_workflow` ⇒ inheritable; a CI cycle and nothing else.
3. **B3 can now be designed from observed values** instead of from documentation. The honest
   contract to write first is: SLSA v1 with an empty `builder.id`, `resolvedDependencies`
   self-declared incomplete, VCS fields present only where the build passes `vcs:*` build args, and
   no signature. Deciding which of those to assert — or to fix at the producer (B2 pinning, a
   `builder-id` attribute, `actions/attest-build-provenance`) — is now a choice with facts in front
   of it, not a guess.
4. **H is unblocked** and its record can be written from the publication run's own artifacts, in the
   same re-certifying round as C/D/E3 if one is being paid for anyway.

Unchanged, with one sharpening: one branch for C + D + E2/E3 + B2 (+ B3 once its contract is
written), one mandatory round at one SHA, B4 as its own decision, and the `*-rc` → `--prerelease`
mapping that keeps `latest` on `v1.0.5`. The soak-then-strict-GA dispatch discipline is now
**load-bearing rather than conventional**: §G shows a development round at the head blocking a tag
until a strict one runs at or above it.

## L. FINAL VERDICT

The five conditions, each against the evidence above:

| Condition | Met? | Where |
| --- | --- | --- |
| integration policy obeyed | yes — `main` is unprotected (server read: 404 "Branch not protected"), no rulesets, no merge/PR requirement in `CONTRIBUTING.md`, direct pushes are the branch's own history, and the server accepted a plain fast-forward | §A, §C |
| push succeeded to the intended target | yes — exit 0, `32ec298..49de108`, `origin/main` reads `49de1081fa56d6f08687c5b652dc4ab4fa9e3157` afterwards, no force | §C |
| authoritative required CI green | yes — CI run `35594554033` completed/success, 10/10 jobs; junit `1643 tests, 0 failures, 0 errors, 135 skipped`; coverage 91.79% over the 90% floor; batch 1's two modules executed there (46/48 with the 2 §F skips, and 21/21) | §E |
| no publication side effect | yes — no tag created, `v1.0.6-rc1` and `release/1.0.6-rc1` unmoved, no `release.yml` run at this head, newest GitHub Release still `v1.0.6-rc1` with assets last written at publication, `/releases/latest` still `v1.0.5`, and the five served index digests still equal the publication record | §I |
| no evidence that F-33's contract is broken remotely | yes, and positively so: on this push's own remote state the gate **refused** the green development-mode GA round with four quoted reasons and exited 1, while accepting the K8s round that really certified (34/34) — the pre-F-33 gate, over the same state, returned PASS. That is the contract doing its job in the real environment | §G |

**BATCH 1 REMOTE VALIDATED**

What that verdict deliberately does **not** cover:

- The two live Actions/API checks still **skip in CI** (§F), so the resolver's remote read path is
  demonstrated here through a disclosed `curl` substitution (§G), not by Actions itself.
- **END-TO-END TAG PATH = PENDING** (§G): no tag was created, so the step-status → `needs` →
  publisher-skip chain is proven structurally and by local exit codes, not by a live release.
- **F-42 is open** (§D, §J.1): a real defect in the same gate, pre-existing at the sealed line,
  found by this stage and deliberately not repaired inside a validation stage.
- The pushed attestations are **read, not verified** (§H): no signing identity exists to verify
  against, and no reference tool ran.
- **G(i) stays PARTIAL** and **H stays DEFERRED** (§J.6, §J.7), both unchanged by this stage.

No tag was created. Batch 2 was not started. Nothing was pushed after the audited HEAD
`49de108`: this report and its corrections are committed locally on top of it, and that document
commit awaits its own authorization — `origin/main` is still exactly what §C–§G validated.
