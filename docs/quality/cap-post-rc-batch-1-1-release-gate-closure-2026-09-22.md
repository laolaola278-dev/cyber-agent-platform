# CAP POST-RC HARDENING — BATCH 1.1 RELEASE-GATE CLOSURE REPORT

Date 2026-09-22. Input state: batch 1 `REMOTE VALIDATED` at `49de108`, with one new finding
(F-42) and one CI capability gap. Batch 1.1 closes exactly those two CI-only gaps — no Batch 2
work, no tag, no change to the sealed `v1.0.6-rc1` publication, and `classify_diff.py` untouched
(`git log 4d8f9c7..HEAD -- scripts/release/classify_diff.py` → empty).

The stage was interrupted once, by the workspace volume detaching; §A and §B record what that cost
and what was re-verified afterwards rather than carried forward.

**Status: `BATCH 1.1 RELEASE-GATE CLOSED`** (§N), anchored at `0561a7e`. This stage first reported
`INCOMPLETE`: the two live checks, once unsilenced, failed in CI for want of a token (§K). The
one-line fix was then reviewed, applied as `0561a7e`, and pushed; CI run `35689729682` at that head
came back **success** with both checks executed and passing (§K.1). The red run is kept in §K as the
record it is and as the remote control for §K.1's green one — not rewritten away.

## A. Workspace recovery

| Check | Result |
| --- | --- |
| `Test-Path F:\` | **True** (was False at the interruption) |
| `Test-Path F:\work\buddy_work\2026-08-21-16-58-16\cap` | **True** |
| Volumes now | C(OS) D E(laoda) F(EAGET) G(EAGET) H I(LAODA) — F is back and `Healthy` |
| Side note, not acted on | H: reports `Warning / Full / Repair Needed`. **No repair, chkdisk, or write operation was performed on any volume** — none was authorized, and none was needed for this work |
| Cause of the interruption, as observed | Two independent things happened: the F: volume disappeared for ~16 hours, and separately `github.com`'s git endpoints became intermittently unreachable (`https://github.com` and `api.github.com` answered 200 while `/…/git-upload-pack` paths timed out, then recovered). The second one is why the push is retried rather than assumed |

No re-clone, no new working directory, no substitute checkout: this is the original workspace, and
every claim below was re-verified against it.

## B. Git integrity and fsck

Read-only, before touching the working tree:

| Check | Result |
| --- | --- |
| `git rev-parse --show-toplevel` | `F:/work/buddy_work/2026-08-21-16-58-16/cap` |
| `git rev-parse HEAD` at recovery | `8efa47d721479542a2a285786ed368decfdefcc3`, branch `main` — exactly the last known committed state |
| Both known commits present | `git cat-file -t 2ac74e5` → `commit`; `git cat-file -t 8efa47d` → `commit`; both `--is-ancestor` of HEAD, as is `3426246` |
| `git fsck --full` | **exit 0**, no missing or corrupt objects. One `dangling commit ad0f9113` — a `WIP on main: 7a738fc` stash leftover dated 2026-09-18, i.e. pre-existing and unrelated to the detach |
| Index / working tree at recovery | index readable; `git status --short` showed exactly the two expected modified files (the plan, `docs/known-issues.md`); nothing else |
| Actions not taken | no `reset --hard`, no `checkout --force`, no lock-file removal, no re-clone |

**Repository integrity: PASS.** The recovery then proceeded to the working tree, not the other way
round, so nothing unverified was overwritten.

## C. Surviving interrupted edits

| File | Expected state | Found | Action |
| --- | --- | --- | --- |
| `docs/quality/cap-post-1.0.6-rc1-hardening-plan.md` | item B reworked, Sequencing row added | present, complete: `git diff` = 70 changed lines, matching the edit applied before the detach; no truncation, no partial replacement, no duplicated block | kept as-is; extended with the H status (§H below) |
| `docs/known-issues.md` | item 10 rewritten to closed-by-1.1 | present, complete: 48 changed lines, the whole item replaced once | kept; scope tightened to *implementation* CLOSED / remote PENDING (§C of the register text) |
| `CHANGELOG.md` | F-42 entry | **untouched** — the edit never landed (it was the write in flight when the volume went, and it failed loudly) | written afresh, after the tests that back it were re-run (§F) |
| `git diff --check` | no damage markers | only `LF will be replaced by CRLF` notices; no conflict markers, no whitespace corruption | — |

No file was restored from HEAD, because none was damaged: the two survivors were checked
file-by-file (presence, size, structure, diff content) rather than assumed, and the one that never
arrived was rewritten rather than guessed at. Nothing in the working tree was reset or discarded.

## D. F-42 implementation

**Root cause.** `find_evidence` decided eligibility from the *run* conclusion plus the presence of
each required job name in the jobs listing, and the comment above `REQUIRED` asserted that a job
skipped by its `if` "is absent, hence 'missing'". GitHub reports a skipped job **present** with
`conclusion: "skipped"` — measured on the run batch 1's push produced
(`cap-linux-certification.yml` run `35594554182`, `cap-production-certification: ["skipped"]`,
postgres legs all green, run conclusion success), which the gate then accepted as that commit's
Linux certification evidence at `+0 commits from the tag`. F-33's own lesson ("a colour where a
decision should be"), one layer down.

**Change** (commit `2ac74e5`, `.github/workflows/release.yml`):

```
required job listed            → still required
+ every conclusion recorded     → new: unqualified_jobs(wanted, verdicts)
  under that name == "success"
```

Equality against `"success"`, deliberately not a blocklist: `skipped`, `failure`, `cancelled`,
`timed_out`, `action_required`, `neutral`, `stale`, an unstarted `null`, an empty list, and any value
not yet enumerated (a test uses `queued`) are refused alike. Matrix legs share a base name, so all of
a name's conclusions must be green — a run with two passing legs and one cancelled is not evidence,
and picking the green one would be the gate editing the record. That is the same no-ambiguity
posture the authority leg already applies to duplicated artifacts and multi-member zips.

**What was deliberately not changed:** the ancestor-selection policy. An ineligible run is *passed
over*, exactly as a run missing a required job already was, and the search continues to the next
commit in the chain; distance, the classifier re-check, and the ≤80-commit window are untouched.
The evidence file gained an `ineligible` list (run id, sha, what each job recorded, capped at 5) and
the failure line now names the newest passed-over run, so an operator sees which round to look at
instead of being told "no evidence exists".

## E. Required-job conclusion matrix

| Conclusion recorded by a required job | Eligible before | Eligible now | Pinned by |
| --- | --- | --- | --- |
| `success` (single) | yes | **yes** | every pre-existing green test; `test_the_rule_is_equality_against_success_not_a_blocklist` last assertion |
| `success` ×N matrix legs | yes | **yes** | `test_one_cancelled_matrix_leg_…` all-green case |
| `skipped` | **yes (the defect)** | no | `test_a_required_job_that_did_not_succeed_is_never_evidence[skipped]`, plus per-workflow cases |
| `failure` | no (run-level colour usually blocked it) | no | `[failure]`, `test_two_jobs_of_one_name_are_not_resolved_by_picking_the_green_one` |
| `cancelled` | yes | no | `[cancelled]`, mixed-leg case |
| `timed_out` | yes | no | `[timed_out]` |
| `action_required` | yes | no | `[action_required]` |
| `neutral` | yes | no | `[neutral]` |
| `stale` | yes | no | `[stale]` |
| `null` (never started) | yes | no | `[None]` |
| `""` (empty) | yes | no | `[]` case |
| anything unenumerated (`queued`) | yes | no | `[queued]`, and the direct-rule test over `False`, `1`, `"Success"` |
| job absent from the listing | no | no | pre-existing `test_gate_refuses_a_green_run_that_did_not_execute_the_release_jobs` |
| name listed twice, one non-success | **yes (cherry-pick)** | no | `test_two_jobs_of_one_name_are_not_resolved_by_picking_the_green_one` |

Consistency across the four `REQUIRED` workflows is asserted, not assumed:
`test_the_success_rule_binds_every_required_workflow` runs one skipped-job case per required job —
`cap-production-certification`, `postgres-version-matrix`, `k8s-certification`, `ga-certification`,
`supply-chain`, `reliability` — and each must refuse the release with exit 1.

**Publication verdict chain (Stage 2 of the brief):** skipped job → run not eligible → no
alternative reading as success (the rule is equality, so there is no default) → if no eligible
ancestor exists, `failures` gains the no-evidence line naming what was passed over → `decide()`
returns `FAIL` → step exits 1 → every publisher behind `needs` is skipped. Measured in the executed
gate: `test_an_ineligible_newest_run_yields_to_the_older_eligible_one` proves both halves at once —
the skipped run is refused *and* the older eligible ancestor is used at its real distance
(`run_id` 8, `+1`, `INHERITED`), so the fix does not silently become a blanket rejection.

## F. CI permission contract and live-check behaviour

**`ci.yml`** (commit `8efa47d`): the `backend` job alone gains

```yaml
    permissions:
      contents: read
      actions: read
```

Read scopes only, no write, nothing added to unrelated jobs. The `contents: read` repetition is not
decorative: in GitHub Actions a job-level `permissions` block **replaces** the workflow-level one,
so inheriting-by-omission would have cost the job its own checkout scope. That semantics question is
answered by observation rather than by documentation (§J: the run either checks out and reads Actions,
or it does not), and the new contract test pins the exact pair with equality —
`test_ci_backend_job_holds_the_scope_its_live_checks_depend_on` — so neither losing the checkout
scope nor quietly gaining a write scope can pass.

**The two live checks** were then made to stop hiding: off CI (no `gh`, no credential) a missing
capability still skips, with the diagnosis attached; **inside CI it fails** — one retry after 20 s
first, so a degraded API is not read as a contract break, and a message that names both readings
("the scope changed under us" vs "GitHub was degraded, re-run this job") because the difference is
decided by whether a re-run fixes it. Data conditions with no path forward (no completed runs listed,
no artifact uploaded, no jobs) follow the same two-regime rule. The soft `gh`-helpers that made every
silence a skip are gone, so a future check cannot accidentally re-adopt them.

## G. Local tests

Re-run at the recovered tree after the interruption, not carried over from before it:

| Run | Passed | Failed | Errors | Skipped | Xfailed |
| --- | --- | --- | --- | --- | --- |
| 17-module release/contract/doc set (`_tmp/b11_r3_regression.xml`) | **296** | **0** | **0** | **4** | **0** (`xfailed`/`xpassed` attributes absent = 0, not unreported) |
| `test_release_publication_gate.py` alone within that set | 66 | 0 | 0 | 2 (the two live checks, `the call itself failed: FileNotFoundError` — no `gh` here) | 0 |
| Doc-contract subset after the docs edits | 128 | 0 | 0 | 4 (same two live + two non-dispatchable workflows) | 0 |
| F-42 cases specifically | **20 executed, 20 passed** | 0 | 0 | 0 | 0 |
| `ruff check` on the four changed Python files | clean (exit 0) | — | — | — | — |
| Mutation control: never-refuse (the new check disabled) | 19 tests red, naming `cap-production-certification` | — | — | — | — |
| Mutation control: blocklist-only (`skipped` refused, everything else accepted) | 11 red, 1 green — the skipped-only case stays caught, proving equality-not-blocklist matters | — | — | — | — |
| Restore check after both controls | `release.yml` byte-for-byte identical | — | — | — | — |

The 4 skips are the honest kind and are not counted as passes: 2 capability-gated live checks (their
CI execution is §K) and 2 structural `ci.yml`/`release.yml` "not dispatchable" cases.

## H. Docs reconciliation and the H/F-25 record

Two documents-only commits, deliberately separate so the record can be reviewed apart from the
reconciliation:

- `730e17a` — **`docs/quality/cap-1.0.6-rc1-publication-closure-2026-09-22.md`**, the record plan
  item H asked for. Every figure is a `GET`: run `35553750674` (25 jobs, all success, 02:18:37Z →
  02:39:02Z), its `cap-1.0.6-rc1-release-images` / `cap-1.0.6-rc1-certification-evidence` /
  `cap-1.0.6-rc1-release-assets` artifacts, the Release asset listing, and the `Docker-Content-Digest`
  served today for each published tag — all five **equal** to the recorded index digests. It quotes
  the completeness gate's own line (`release image set complete: cap-backend, cap-egress-proxy,
  cap-frontend, cap-sandbox-browser, cap-sandbox-http`), the six-coordinate values file with its
  `sha256:bfcb2aa2d02e5d5a…` identical between pipeline artifact and Release asset, per-image Trivy
  PASS/0 findings, and the sealed gate artifact's four evidence resolutions — noting it carries
  **no `authority` field at all**, which is the F-33 blind spot visible in the release's own bytes.
  It records the honest gap: `config_digest` is `null` in all five published records, so the release
  pinned index + platform manifest digests and not the OCI config digest. **No sealed asset was
  touched, and nothing was re-run.** F-25 stays CLOSED by the run itself.
- `7da216c` — the plan's item B split into **CURRENT OBSERVED CONTRACT** (SLSA v1, empty
  `builder.id`, platform-manifest subject, `resolvedDependencies` self-declared incomplete, VCS
  revision on 2 of 5, no builder version, no signature) and **DESIRED FUTURE CONTRACT** with B3's
  five open design questions and the explicit rule that B3 may not just assert `builder.id == ""`;
  item H's status; the Sequencing batch-1.1 row; register item 10 scoped to implementation CLOSED /
  remote PENDING; and the CHANGELOG entries, which claim local evidence only.

**B2/B3/B4 remain unimplemented** — this stage touched planning text, not a workflow or a Dockerfile.

## I. Classifier evidence

| Range | Verdict | runtime_affecting | release_metadata_only | files | blocking |
| --- | --- | --- | --- | --- | --- |
| `3426246` → `2ac74e5` (F-42 fix) | INHERITED | false | true | 2 (`ci_workflow`, `test_harness`) | none |
| `2ac74e5` → `8efa47d` (CI scope) | INHERITED | false | true | 2 (`ci_workflow`, `test_harness`) | none |
| `8efa47d` → `730e17a` (record) | INHERITED | false | true | 1 (`docs`) | none |
| `730e17a` → `7da216c` (reconciliation) | INHERITED | false | true | 3 (`docs`) | none |
| `7da216c` → `e9e8d8b` (this report) | INHERITED | false | true | 3 (`docs`) | none |
| `e9e8d8b` → `5acc33a` (remote picture) | INHERITED | false | true | 1 (`docs`) | none |
| `5acc33a` → `0561a7e` (`GH_TOKEN` step) | INHERITED | false | true | 2 (`ci_workflow`, `test_harness`) | none |
| `7da216c` → HEAD | INHERITED | false | true | 5 (`ci_workflow` 1, `test_harness` 1, `docs` 3) | none |
| `49de108` → HEAD | INHERITED | false | true | 12 (`ci_workflow` 2, `test_harness` 2, `docs` 8) | none |
| `4d8f9c7` → HEAD (sealed line) | INHERITED | false | true | 16 | none |
| `b671f53` → HEAD (Linux/GA/soak) | INHERITED | false | true | 20 | none |
| `d30b4e7` → HEAD (K8s) | INHERITED | false | true | 19 | none |

All ranges measured with the real classifier as a separate process, re-run after the volume
recovery rather than quoted from the pre-interruption transcripts, and re-run again at the final
tip: the four cumulative ranges read 11/15/19/18 files when `HEAD` was `7da216c`, and the rows
above are the new measurements rather than edited ones. The report commits stacked on top of this
tip are `docs`-only hops (`0561a7e` → report: INHERITED, 4 `docs` files), and re-measuring every
cumulative range there leaves these counts as they are — the pages they add already appear in
those ranges — except `7da216c → HEAD`, which grows from 5 files to 7. `RECERTIFICATION_REQUIRED`
never appeared, and the classifier itself is unmodified (`git diff --name-only 49de108..HEAD --
scripts/release/classify_diff.py` → empty).

## J. Push and the runs it triggered

**Push** (Stage R8): after the transport recovered, `git push origin HEAD:refs/heads/main` succeeded
on its first attempt — `49de108..7da216c`, exit 0, remote `refs/heads/main` =
`7da216ce0ecea85d6ed5073c77c47482106d0ed0`, matching the intended head, no force, no tag
(`_tmp/push_b11_1.log`).

Four runs were created by the push, all `head_sha == 7da216ce`, `event: push`
(snapshots in `_tmp/b11/`):

| Run | Workflow | Status / conclusion | Jobs |
| --- | --- | --- | --- |
| `35683797478` | `ci.yml` | **completed / failure** | 10: 9 success, `backend` failure |
| `35683797484` | `cap-linux-certification.yml` | completed / success | `full-certification` + 3 `postgres-version-matrix` legs success; `cap-production-certification` and `fast-certification` **skipped** — the same shape that produced F-42 |
| `35683797517` | `cap-k8s-certification.yml` | completed / success | `k8s-certification` success; uploaded `k8s-cert-artifacts` (id `10676491468`) |
| `35683797529` | `cap-ga-certification.yml` | completed / **success** (62 m, `03:36:45Z`→`04:38:39Z`) | `supply-chain` and `ga-certification` both success; uploaded `ga-cert-artifacts` (id `10677074002`, 2 294 050 bytes) — green, development mode, and now the **newest** GA evidence for this head |

**CI's own junit** (`backend-evidence` → `junit-backend.xml`), for the backend job:
`tests=1664 · failures=2 · errors=0 · skipped=133`. Coverage and the other jobs are irrelevant until
those two failures are explained, so the important part is *which* two:

- **all 20 F-42 cases ran remotely and passed** (`test_a_required_job_that_did_not_succeed_is_never_evidence`
  ×10, `test_the_success_rule_binds_every_required_workflow` ×6, plus the walk-back, mixed-leg,
  duplicate-name and direct-rule cases), inside a 68-item `test_release_publication_gate.py` whose
  only failures are the two live checks;
- the 2 failures are the newly-unsilenced capability checks, and they now report a cause instead of
  a shrug — §K.

**F-42's behaviour against live state**, measured the same way batch 1 measured F-33: the gate code
lifted from `release.yml`, `gh api` answered by GETs, `git` and the classifier real, nothing tagged
(`_tmp/b11/dryrun_7da216c.log`):

| | pre-F-33/F-42 gate (`4d8f9c7`) | gate at the pushed head |
| --- | --- | --- |
| Linux evidence | run `35683797484` at `7da216ce`, **+0** — green, names present, `cap-production-certification: ["skipped"]` | that run **passed over**, and so were two more green-with-skipped-jobs runs (`35594554182` at `49de1081`, `35553750491` at `4d8f9c72` whose `postgres-version-matrix` legs were skipped); it landed on `35506716466` at `b671f537`, all legs `success`, distance INHERITED |
| K8s | — | run `35683797517` at `7da216ce`, authority **PASS** on `34/34`, `commit` bound to `7da216ce0ece…` |
| GA | accepted the newest green run | first pass (`7da216c` as the tag, while this head's own GA round was still running): run `35594554041` at `49de1081` (+5) **REJECTED**. Second pass, after `35683797529` finished: the leg selects **this head's own** development round (`10677074002`, `mode='development'`, `full_ga_certified=False`, `planned=5`, `35 of 40`) and refuses it, so the gate exits 1 with `RELEASE BLOCKED` |

Distances in the two passes differ by one commit (`+40`/`+41` for the walked-back legs, `+1` for the
legs at this head) because the first was resolved with the tag `7da216ce…` and the second with
`e9e8d8ba…`, the report commit on top; neither tag exists — the SHA was only an environment value.
Both logs: `_tmp/b11/dryrun_7da216c.log`, `_tmp/b11/dryrun_after_ga_dev.log`.

Batch 1 recorded that head's Linux leg being *accepted* at `+0` with a skipped release job; the same
state now walks to the genuine release-layer round instead, with the distance classified INHERITED as
the policy requires. That is the defect's fix observed on the repository's real runs, not on a
fixture. (The GA refusal is F-33, unchanged, still firing.)

### J.1 The second push, and what it produced

Between the report commit and this paragraph the tip moved twice more (`5acc33a`, `0561a7e`), so
batch 1.1 was pushed a second time. How that happened is worth keeping, because the first attempt
did *not* push:

- The guarded chain started 13:03 and aborted at **13:10:20** with
  `ABORT: remote main is , not the authorised 7da216ce0ec…; nothing pushed` — `api.github.com`
  was timing out at that moment, so the guard refused to act on an unreadable remote state. It
  pushed nothing, which is the designed behaviour, not a failure of it.
- Transport, measured rather than assumed: `github.com:443` timed out repeatedly while
  `github.com:22` and `:80` opened in 0.05 s, `codeload.github.com` and `raw.githubusercontent.com`
  answered, and `api.github.com:443` worked sometimes. `nslookup` and `getaddrinfo` both returned
  the single address `20.205.243.166`, so it was not DNS. `~/.ssh/id_rsa` is not enrolled on the
  account (`Permission denied (publickey)`), and enrolling a key would have modified the user's
  GitHub account, so that route was left alone. What does reach `github.com` from this box is the
  proxy the machine already had configured for itself: WinINET `127.0.0.1:7890`.
- **The push** — plain fast-forward, through that proxy, for this one command only:

  ```
  git -c http.proxy=http://127.0.0.1:7890 push origin 0561a7eafe73…:refs/heads/main
  ```

  Guards passed first: local `HEAD` exactly the audited `0561a7eafe73c48b99c80eeca9b219616bbbf923`,
  `git status --porcelain` empty, remote `refs/heads/main` read back as the authorised base
  `7da216ce0ecea85d6ed5073c77c47482106d0ed0`. Exit **0**; git reported `7da216c..0561a7e`; four
  seconds later the API read `main = 0561a7eafe73…`. No `--force`, no `--force-with-lease`, no tag.
  `http.sslVerify` stayed on and TLS terminates end to end inside the proxy's CONNECT tunnel;
  afterwards `git config --local`/`--global` hold no proxy entry and the remote URL is unchanged —
  the routing was a single command-line `-c`, not a repository or machine change.
- **Four runs**, all `event: push`, `head_sha == 0561a7eafe73…`, started `05:11:18Z`
  (`_tmp/b11/ci_rerun/`):

  | Run | Workflow | Result as read back |
  | --- | --- | --- |
  | `35689729682` | `ci.yml` | **completed / success**, 10/10 jobs success (§K.1) |
  | `35689729683` | `cap-linux-certification.yml` | completed / **success** — but `fast-certification` and `cap-production-certification` are `skipped`, i.e. the same green-with-skipped-release-jobs shape that *is* F-42 |
  | `35689729714` | `cap-k8s-certification.yml` | completed / success — `k8s-certification` success, uploaded `k8s-cert-artifacts` (id `10677309453`, 3 323 bytes) |
  | `35689729668` | `cap-ga-certification.yml` | completed / **success** (`05:11:18Z`→`06:09:32Z`, 58 m 14 s) — `ga-certification` and `supply-chain` both success, uploading `ga-cert-artifacts` (id `10678749549`, 2 223 793 bytes) and `supply-chain-evidence` (id `10677698049`, 623 045 bytes). A development round: green, and still unable to certify a release, which §J.2 measures |

- **The gate, again against live state, at the new head** — same read-only method (gate source from
  `release.yml`, `gh api` answered by GETs, `git` and the classifier real, nothing tagged), log at
  `_tmp/b11/dryrun_0561a7e_ga_running.log`, tag SHA supplied only as an environment value:

  | | gate at `4d8f9c7` (pre-F-33/F-42) | gate at the pushed head |
  | --- | --- | --- |
  | Linux | selected `35689729683` at `0561a7ea`, **+0**, jobs including `cap-production-certification: ["skipped"]` → whole gate **exit 0 / PASS** | four runs **passed over** — `35689729683` (+0), `35683797484`, `35594554182`, and `35553750491` (skipped on its `postgres-version-matrix` legs) — then `35506716466` at `b671f537`, every leg `success`, distance **+43 INHERITED** |
  | K8s | — | `35689729714` at `0561a7ea`, **+0**, authority **PASS** — artifact member `cap-28.6-k8s-certification.json`, `commit == 0561a7eafe73…`, `gate_summary 34/34`, `failed=0`, `not_run=0` |
  | GA | accepted the newest green run | `35683797529` at `7da216ce` (+3) **REJECTED** on four of its own fields: `mode='development'`, `full_ga_certified=False`, `planned=5`, `35 of 40` gates |
  | Reliability | — | `35506714369` at `b671f537`, +43, INHERITED |

  `ancestors_considered = 80`; process exit **1**, `verdict = FAIL`, `RELEASE BLOCKED`, publication
  would proceed: `False`. The same remote state, two gate versions, one of them ready to publish a
  release whose production-certification leg never ran — which is the defect batch 1.1 was for,
  demonstrated on the repository's actual runs at the new head rather than on a fixture.

### J.2 The same dry-run once this head's GA round landed

The refusal in §J.1 still had an available excuse: the head's own GA round had not finished, so the
leg resolved to an older one. That excuse is now gone. The round completed at `06:09:32Z`, and the
identical read-only dry-run was re-taken (`_tmp/b11/dryrun_0561a7e_ga_finished.log`):

| | gate at `4d8f9c7` | gate at the pushed head |
| --- | --- | --- |
| GA | selects `35689729668` at `0561a7ea`, **+0** — its own round, complete — and does not read what it says | selects the **same run at the same +0**, downloads its own `ga-cert-artifacts` (`10678749549`, member `cap-cert-ga/cap-28.7-ga-certification.json`), and **REJECTS** it |
| Linux | accepts `35689729683` at `+0`, `cap-production-certification` skipped | passed over, as in §J.1, to `35506716466` at `b671f537` (+43, INHERITED) |
| Whole gate | **exit 0 / `PASS`** | **exit 1 / `FAIL`**, `RELEASE BLOCKED` |

What the GA rejection rests on, quoted from the artifact it just read: `commit = "0561a7eafe73…"` —
the binding check *passes*, this is unambiguously this commit's own round — and then

```
read mode = "development"            → refused: records mode='development', which is not 'final-strict'
read full_ga_certified = false       → refused: records full_ga_certified=False, which is not True
read gate_summary.planned = 5        → refused: gate_summary.planned=5, requires 0
read gate_summary 35 passed/40 total → refused: counts 35 of 40 gates passed, so a gate has no PASS behind it
```

so the refusal is about **what the round says about itself**, not about staleness, distance or
colour: a complete, correctly-bound, entirely green development round is still not certification.
`ancestors_considered = 80`; the selection and walk-back policy did not move; nothing was tagged —
the tag SHA was an environment value, and no remote call in either pass was anything but a `GET`.

Which is also why this is not, and does not claim to be, the end-to-end proof: the tag-time path
stays **PENDING** (§M.4), because demonstrating it would require cutting a release tag, and the
sealed-release rules for this line forbid that.

## K. The two live checks, unsilenced

They **executed** — that part of the contract held: no skip, no grey mark, CI's `backend` job went
red because of them:

| Test | CI outcome | CI-recorded diagnosis (its own words, twice, after the retry) |
| --- | --- | --- |
| `test_the_authoritative_artifact_is_where_the_gate_reads_it` | **FAILED** | `` `gh api` exited 4: gh: To use GitHub CLI in a GitHub Actions workflow, set the GH_TOKEN environment variable. `` |
| `test_the_actions_api_paths_the_gate_uses_exist` | **FAILED** | the same `gh` exit 4, on the K8s runs listing |

What that rules in and out, in order of how the diagnosis was reached:

- **Not the token scope.** `actions: read` is granted and pinned; the request never got far enough to
  be refused by a scope, so the batch-1 §F hypothesis ("the CI job's token has no Actions read") was
  incomplete, and §F of `cap-post-rc-batch-1-remote-validation-2026-09-21.md` is annotated with the
  correction rather than left standing.
- **Not a degraded API.** `gh` exited 4 with a configuration message before any HTTP exchange, and
  the same endpoints answered this session's `GET`s seconds later. The retry fired, then the failure,
  as designed — which is the check distinguishing "GitHub hiccuped" from "this cannot work".
- **What it is:** the runner has `gh`, but nothing in this job puts a token in its environment. In a
  workflow, `secrets.GITHUB_TOKEN` is available to steps only if the step actually reads it —
  `gh` looks for `GH_TOKEN`/`GITHUB_TOKEN` and exits 4 when neither is set. So the missing piece is
  one line on the unit-test step, and it is a *credential-plumbing* decision, not a permissions one:

  ```yaml
          env:
            GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  ```

  with the scope already declared covering what those two reads do (list runs, list artifacts,
  download an artifact zip — all `actions: read`). Not applied here: the rule for this stage after a
  red CI is to record the cause, not to keep editing the thing under observation until it flatters
  the verdict.

**So the outcome is a genuine finding, not a false alarm**: the release gate's *live read path* — the
half the fixtures cannot stand in for — is still unvalidated, and it is now CI's red job that says so
instead of a green job full of skips. `LIVE API READ PATH = PENDING`, pending that one-line change,
its own review, and a re-run. *(That was the state at `7da216c`; the re-run below is what it led to.)*

### K.1 The re-run, after the one line was reviewed and applied

The change is `0561a7e` — `env: GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}` on the
"Unit tests and coverage" step alone, nothing else in the workflow, no scope widened — and its
contract is pinned twice over in `test_ci_backend_job_holds_the_scope_its_live_checks_depend_on`:
the job's `permissions` still equal `{"contents": "read", "actions": "read"}`, and the list of steps
carrying that env must be exactly `["Unit tests and coverage"]`. Removing the two lines reddens that
test with `GH_TOKEN is set on []`, and the workflow was restored byte-exactly afterwards.

CI run `35689729682`, `head_sha == 0561a7eafe73…`, `event: push`, completed **success** —
`05:11:18Z` → `05:32:00Z`, `run_duration_ms` 1 242 000 (20 m 42 s) (`_tmp/b11/ci_rerun/`):

| What was read | Result |
| --- | --- |
| Jobs | 10 of 10 **success**, including `backend` — the job that was red an hour earlier |
| `backend-evidence` → `junit-backend.xml` (artifact id `10677389966`, downloaded from *this* run) | `tests=1664 · failures=0 · errors=0 · skipped=133` |
| `test_the_authoritative_artifact_is_where_the_gate_reads_it` | **PASSED** — it listed this repository's completed GA runs, downloaded the authoritative artifact for real, and asserted the verdict file still carries `commit`/`mode`/`full_ga_certified`/`gate_summary` with `commit == run.head_sha` |
| `test_the_actions_api_paths_the_gate_uses_exist` | **PASSED** — the K8s runs listing and the jobs listing returned the fields the gate resolves evidence by |
| The 20 F-42 cases | all present, **none** non-passing |
| The 133 skips | the same set as in the failing run — no test was moved into a skip to buy the green |

The pair that matters for the verdict is the diff between the two runs at two heads, not the second
one alone: `1664/2/0/133` at `7da216c` (both live checks FAILED, `gh` exit 4, no token) against
`1664/0/0/133` at `0561a7e` (both PASSED, same skip count). That is a remote negative control for
these two checks on the runner itself — the same assertion set, reddened by withdrawing one
environment variable.

`LIVE API READ PATH = VALIDATED IN CI`. The gate's read path is now green *executed*, with its
failure mode demonstrated on the same job an hour earlier.

## L. Sealed-release integrity after the push

| Required check | State |
| --- | --- |
| No new tag | **confirmed** — the remote tag list is still the same 12 (`v1.0.0` … `v1.0.6-rc1`), none at the pushed head |
| `v1.0.6-rc1` unmoved | **confirmed** — tag object `d0c7c7dc14cb…` peeling to `4d8f9c72b72d…`, identical locally and via `git ls-remote` / `GET /git/ref/tags/v1.0.6-rc1` |
| `release/1.0.6-rc1` unmoved | **confirmed** — still `4d8f9c72b72d…` |
| No publication run caused by this push | **confirmed** — `release.yml`'s newest run is still `35553750674` at `4d8f9c72`; no run exists at `7da216c` |
| `/releases/latest` still `v1.0.5` | **confirmed** — `v1.0.5`, `prerelease: false`, published `2026-09-07T14:59:44Z`; the newest release remains `v1.0.6-rc1`, published `2026-09-21T02:38:59Z` |
| No image digest mutation | **confirmed** — re-read after the push, `Docker-Content-Digest` for `1.0.6-rc1` equals the recorded index digest for all five images (`_tmp/b11/image_digest_recheck.json`) |
| No registry or release write at any point | all remote access was `GET` (API, artifact zips via credential-free redirect fetch, ghcr with `pull` scope) |

### L.1 The same audit after the second push (`0561a7e`)

Re-run read-only afterwards, not inferred from the table above (`_tmp/b11/sealed_audit.py`,
response saved to `_tmp/b11/sealed_audit_after_push2.json`):

| Required check | State as re-read |
| --- | --- |
| Refs | `main = 0561a7eafe73…` (the push), `release/1.0.6-rc1 = 4d8f9c72b72d…`, `v1.0.6-rc1` → tag object `d0c7c7dc14cb…`, `v1.0.5` → tag object `b5c4eeea758b…` |
| No new tag | **confirmed** — still 12 tags, `v1.0.0-cert` … `v1.0.6-rc1`, none at `0561a7e` |
| Releases untouched | **confirmed** — `/releases/latest` = `v1.0.5`, `prerelease: false`, published `2026-09-07T14:59:44Z`; newest release still `v1.0.6-rc1` (id `392662225`, published `2026-09-21T02:38:59Z`) |
| No publication run caused by this push | **confirmed** — `release.yml`'s newest run is still `35553750674` at `4d8f9c72`, and the repository has **zero** `event: release` runs |
| No image digest mutation | **confirmed** — `Docker-Content-Digest` served today for `1.0.6-rc1` is byte-identical to what was read earlier the same day for all five images (`cap-backend a733b90c7a84…`, `cap-egress-proxy 8ab8c234f278…`, `cap-frontend e1b1889a868c…`, `cap-sandbox-browser b369618871bd…`, `cap-sandbox-http 36bb2f7993ac…`), and those were the digests matched against the sealed publication record in §H |
| Write side effects | none — every call in this round was a `GET` (API, artifact zips via credential-free redirect fetch, ghcr with `pull` scope) |

## M. Remaining work

1. **Done, and closed by measurement: `main`'s unit job is green again.** CI run `35683797478` had
   failed on the two live checks (§K) because `gh` was handed no token in the `backend` job. The
   clear was one step-level `env: GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`; it was reviewed, applied as
   `0561a7e`, pushed, and CI run `35689729682` at that head returned **success** with both checks
   executed and passed (§K.1). Nothing was muted, skipped or narrowed to get there — the 133 skips
   are the same set as in the red run.
2. **One sentence is now stale, and was left alone deliberately.** The docstring of
   `test_the_actions_api_paths_the_gate_uses_exist` still ends "…which is the gap this job's
   permissions now close". The measurement says otherwise: `actions: read` alone did not close it,
   the step-level `GH_TOKEN` did (§K.1, and §F's correction in batch 1's remote-validation report).
   It is not fixed in this round because editing code after the green run would move `HEAD` off the
   tip CI validated; it belongs to the next CI cycle, where the classifier can see it as the
   `test_harness`-only change it is.
3. **B3 still waits on the five design questions** now written down (identity required? source?
   machine verifier? subject = index or platform digest? failure policy?), and B4 remains a separate
   security decision. B2 pinning is unchanged. None of them was started here.
4. **END-TO-END TAG PATH stays PENDING**: no tag was created, so the gate's refusal at tag time is
   demonstrated by the executed code and by the live-state dry-runs (batch 1's at `7da216c`, §J.1's
   at `0561a7e`), not by a publication.
5. Unchanged backlog: C (compose pinning), D (schema widening, F-41), E2/E3 (F-37's remaining half),
   F (24 h soak roll-up), G(i) completion and G(ii)–(v), i.e. Batch 2 — **not started**.
6. Environment note worth keeping: this box reaches `github.com` only through its own local proxy —
   direct `:443` times out while `:22`/`:80` are open and the API is intermittent — and the workspace
   volume can detach. Both were re-checked here rather than assumed, and the push guard is what kept
   the first attempt from acting on an unreadable remote state.

## N. FINAL VERDICT

Anchored at the CI-validated tip `0561a7eafe73c48b99c80eeca9b219616bbbf923`, which is also the
remote `main` as read back after the second push.

The seven conditions the verdict requires:

| Condition | Met? | Where |
| --- | --- | --- |
| repository integrity PASS after the volume loss | **yes** — HEAD exactly where it was, both commits present, `git fsck --full` clean, no destructive recovery step taken | §A, §B, §C |
| F-42 local regression PASS | **yes** — 296 passed / 0 failed / 4 skipped / 0 xfailed across 17 modules; 20 executed F-42 cases; two mutation controls redden 19 and 11 tests with byte-exact restore | §D, §E, §G |
| classifier INHERITED, unmodified | **yes** — 7 per-commit hops and 5 cumulative ranges re-measured at this tip, all `runtime_affecting=false`, no blocking file, `RECERTIFICATION_REQUIRED` never returned, `classify_diff.py` untouched in every range | §I |
| push fast-forward success | **yes**, twice — `49de108..7da216c` and `7da216c..0561a7e`, both exit 0 with the remote read back equal to the intended head, no force, no tag | §J, §J.1 |
| **authoritative CI green** | **yes** — CI run `35689729682` at this head: completed **success**, 10 of 10 jobs success, `backend-evidence` junit `1664 tests / 0 failures / 0 errors / 133 skipped` | §K.1 |
| the two live Actions/API checks **execute** rather than permission-skip | **yes, and they pass** — both `test_the_authoritative_artifact_is_where_the_gate_reads_it` and `test_the_actions_api_paths_the_gate_uses_exist` ran in CI and passed against the real API; the same two checks failing at `7da216c` for want of a token is the remote control that shows they are not vacuous | §K, §K.1 |
| sealed release untouched | **yes** — re-audited after the second push: no new tag, `v1.0.6-rc1` and `release/1.0.6-rc1` unmoved, no publication run at any pushed head, zero `event: release` runs, `latest` still `v1.0.5`, all five served `1.0.6-rc1` digests identical to the record | §L, §L.1 |

**BATCH 1.1 RELEASE-GATE CLOSED**

What that rests on, beyond the table: **F-42 is fixed** — presence plus all-success conclusions,
equality not a blocklist, ambiguity un-cherry-picked, walk-back policy untouched — verified three
ways over: twenty executed cases locally, **the same twenty green in CI**, and the gate's own code run
against this pushed head's live remote state, passing over four green-with-skipped-jobs Linux runs to
reach the real release-layer round at `b671f537` where the pre-fix gate accepted the `+0` one and
printed `PASS`. The permission contract and the token-carrying step are pinned by test. The
publication closure record (H/F-25) exists from read-only evidence, and the provenance plan now
separates what was observed from what is still a decision.

What this verdict does **not** claim, stated as plainly as what it does:

- **END-TO-END TAG PATH = PENDING.** No tag was created, so the refusal at tag time is demonstrated
  by executing the gate's own code against live state, not by a publication. A publication round
  would additionally require the GA leg's `final-strict` authority, which no development round —
  including this head's — can supply.
- **This head's own GA round landed green and was still refused.** `35689729668` completed success
  (58 m 14 s) as a development round; the re-taken dry-run (§J.2) has the GA leg select *that* run at
  `+0`, confirm its recorded `commit` is this very SHA, and then reject it on four things it says
  about itself. So the block is not staleness, distance or a colour — it is certification's content
  requirement, and no development round can satisfy it.
- **The stale sentence in §M.2** stays in the tree, on purpose, with its correction written down here
  rather than a code edit slipping past the tip CI validated.
- Nothing was loosened to obtain the green: no test deleted, narrowed, skipped or re-marked, no
  expectation edited, no gate weakened. The two checks that had to become truthful became truthful by
  being handed the credential they read with, and the one that failed because it had been told a
  half-truth (`actions: read` is necessary but not sufficient) is now documented as such.

Batch 2 was not started. No tag was created, no sealed artifact, release or image was touched, and
the pushed tip is the tip CI validated.
