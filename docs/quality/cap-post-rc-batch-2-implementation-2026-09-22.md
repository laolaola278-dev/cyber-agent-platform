# CAP POST-RC HARDENING — BATCH 2 IMPLEMENTATION & RECERTIFICATION REPORT

Every number below is anchored to the frozen candidate
`BATCH2_CANDIDATE_SHA = 257ba18d8cb6c22d330701b69713544254e4f03a` (2026-09-22) and was read
from the named artifact, run id or file rather than restated from a plan. The line moved four
times after the first freeze (`3a69607` → `e3177ec` → `d8472d0` → `f5a0cdd` → `257ba18`), each
time because evidence said something was wrong, never to restate prose. Where a read could not
be made from this box, the section says so instead of substituting an assumption.
Final verdict: **BATCH 2 COMPLETE — RECERTIFIED** (§S).


## A. Implemented commits

The Batch 2 line is the commit set `0561a7e..257ba18` (Batch 1's tip to the candidate):

| commit | what it changed | classifier verdict for that commit alone |
| --- | --- | --- |
| `7f015d3` | E2 generator `scripts/release/third_party_registry_evidence.py` + 43 controls + the tracked registry evidence artifact | INHERITED (`certification_generator`, `docs`, `test_harness`) |
| `d68ff50` | C/F-24: five compose refs pinned by index digest, lock grown to 13 images, postgres exception reversed, E3 pointer contract rewritten | RECERTIFICATION_REQUIRED (`deployment`, `ci_workflow`, `certification_generator`, `test_harness`) |
| `1a0d79a` | D/F-41: one Helm image-coordinate contract for all six nodes + Python schema tests + real-helm rendering in CI | RECERTIFICATION_REQUIRED (`deployment`, `ci_workflow`, `test_harness`) |
| `478cec6` | B2: actions pinned by full SHA, explicit buildx/BuildKit/frontend pins, `scripts/release/record_build_producer.py` | RECERTIFICATION_REQUIRED (`deployment`, `ci_workflow`, `certification_generator`, `docs`, `test_harness`) |
| `dc1f4cb` | the two defects CI itself surfaced (128-char helm fixture digest, `--set-string key=null` writing the string `null`, ruff EXE001 on the generator) | INHERITED (`certification_generator`, `ci_workflow`) |
| `3a69607` | base-image invariant stated the way it is honoured | INHERITED (`test_harness`) |
| `e3177ec` | producer read-back: parse `docker buildx version`/`inspect` instead of templating them; failed reads carry stderr + exit code | INHERITED (`certification_generator`, `test_harness`) |
| `d8472d0` | D.4 #1/#2/#3 negative controls (`_unpinned_services`, `_compose_lock_differences`) | INHERITED (`test_harness`) |
| `f5a0cdd` | producer honesty: `--docker-cli-build`, `inspected_builder`, `comparison.*.matches` | INHERITED (`certification_generator`, `test_harness`) |
| `257ba18` | cancel finalisation durability in `backend/app/acquisition/worker_path.py` (§G) + five regression tests | RECERTIFICATION_REQUIRED (`production_runtime`, `test_harness`) |

E3 has no commit of its own: its pointer contract landed inside `d68ff50` together with C/F-24,
so the per-item verdict list can only be reported per commit, not per finding. `257ba18` is the
only commit in the line that changes runtime code, and it is not a Batch 2 item: it exists
because CI at `f5a0cdd` failed on a defect that had nothing to do with the pins, and the goal
forbids re-running a red test until it happens to pass.

## B. E2 registry evidence

Generator: `scripts/release/third_party_registry_evidence.py`. Tracked output:
`docs/quality/artifacts/registry-resolution/third-party-registries.json` — 13 image records,
**zero without an index digest**, every one `digest_kind = manifest-list (multi-arch index)`,
covering all six compose images (postgres, redis, prometheus, grafana, pgadmin4, MinIO), the
five Dockerfile bases, `moby/buildkit:v0.33.0` and `docker/dockerfile:1`. Read from the file
at the candidate, not from the plan: index digests `721873c3…` (postgres), `858f009f…`
(redis), `2659f4c2…` (prometheus), `fa801ab6…` (grafana), `8a68677a…` (pgadmin4),
`a1ea29fa…` (MinIO, unchanged from the vendor-signed quay.io pin).

Method the artifact records in `observation`: index-only `Accept`; served
`Docker-Content-Digest` must equal `sha256(body)`; the same content refetched by digest must
be byte-identical; exactly one `linux/amd64` child; `unknown/unknown` attestation entries
excluded; observation metadata (`verified_on`, target count, method) kept separate from the
digest claims, so two runs of the same measurement are comparable on the claims.

Failure modes the generator refuses (each with its own control test): missing
`Docker-Content-Digest`, served digest ≠ `sha256(body)`, tag response ≠ by-digest response,
non-index where an index is required, `linux/amd64` child count ≠ 1, conflicting results for
one logical target, an incomplete target set, an ambiguous registry/auth response, and a
dropped connection (reported as a refusal, never as a traceback or a partial artifact). Two
*different* logical targets resolving to the same digest is accepted: shared content is legal.

## C. F-24 — every deployed third-party image pinned by digest

`docker-compose.yml` carries `repository@sha256:<index digest>` for all six external images,
each with a comment naming the tag the digest stands for; MinIO's existing immutable ref was
left alone because the E2 read confirmed the recorded index digest. The lock gained
`redis`/`prometheus`/`grafana`/`pgadmin4`, `policy.postgres.form` went from `tag` to
`repository@digest` with the upgrade procedure (generator refresh → review digest diff →
relevant certification → compose/lock update) and its known cost stated, and the mutable-tag
exception is now guarded by a named test rather than absent. D.4 contracts 1–5 are each
enforced by a live check plus a control that mutates a copy of its input.

## D. F-37 — evidence pointers resolve inside a clone

Every `provenance.evidence` string names the tracked artifact from §B, and the transitional
tracked-twin allowance is deleted. The final contract is asserted by
`test_cited_evidence_is_tracked_and_asserts_exactly_the_locked_digests` (cited file exists, is
`git ls-files` tracked, and states exactly that entry's index and `linux/amd64` child digests
for its registry/repository/tag coordinate), with `no basename/twin fallback`: the control
`test_a_pointer_to_an_untracked_file_is_not_rescued_by_a_same_named_copy` re-plants a
same-named twin under `docs/quality/artifacts/` and requires the invalid pointer to stay
invalid. `test_an_entry_with_no_evidence_pointer_is_refused` closes the silence case.

## E. F-41 — one image-coordinate contract

All six chart image nodes are `required: ["repository"]` with `anyOf` over tag-or-digest
(both legal on purpose; `oneOf` was explicitly rejected) and `digest.pattern
^sha256:[0-9a-f]{64}$`; the `digest: ""` defaults are gone. `cap.imageRef` prefers the digest,
which `test_the_digest_is_the_coordinate_the_chart_renders` reads out of `_helpers.tpl`.
Enforcement is not just Python: `ci.yml`'s packaging job renders tag-only, digest-only and
tag+digest with real helm for all six paths, asserts the rendered strings, and refuses
no-coordinate, empty tag, empty digest and malformed digest.

## F. B2 — producer pins, and the producer that actually ran

Pinned and recorded: `docker/setup-buildx-action@8d2750c6…` (v3.12.0),
`docker/login-action@c94ce9fb…` (v3.7.0), explicit `buildx-version: v0.37.1`,
BuildKit `moby/buildkit:v0.33.0@sha256:6c2fa84a…`, and the `docker/dockerfile:1` frontend
`@sha256:ecfaec9e…` in the two Dockerfiles that already carried a directive (the other three
were deliberately left without one). `scripts/release/record_build_producer.py` writes
`configured` (read from the lock) and `observed` (read back from the tools) as separate
objects; a failed read is recorded as failed with the CLI's own complaint and exit code.

What CI's own artifacts at `d8472d0` proved, and what `f5a0cdd` fixes:

* the read-back works — buildx version and commit, engine version, runner identity;
* `incomplete: ["builder"]` on all five was the recorder asking a `docker build` build for a
  BuildKit *container*: that path uses the daemon's embedded BuildKit, so `--docker-cli-build`
  now says so and the misfired inspect is kept as `inspected_builder`;
* **F-44, now measured at the candidate.** All five `ci-release-image-*` artifacts at `257ba18`
  read their buildx back (`ok: true`, no `incomplete` key — the read itself is fixed), and all
  five say `comparison.buildx_version.matches: false`: observed
  `github.com/docker/buildx v0.37.0 ac30b249211430b85fb8f37b6e7154b5c47ba0b6` against the
  declared `v0.37.1`. The record also names the mechanism: `build_path: "docker CLI
  (\`docker build\`)"`, so the buildx that answers is the docker CLI plugin shipped in the runner
  image and `setup-buildx-action`'s download of the pinned binary never becomes the producer;
  the kit that built the bytes is the engine's embedded BuildKit (`engine_version: 28.0.4`), and
  `comparison.buildkit_image.matches` is `null` — an unreadable observation, not a restated pin.
  Closing this properly means switching the release build to `docker buildx build --builder …`,
  which changes the producer of all five images and therefore the bytes and the
  certification — a decision Batch 2 was never given, so it is reported with its price here and
  left for the next batch rather than done quietly inside this one. What B2 did promise — that
  the pin and the producer are recorded separately and that an unreadable field says so — is now
  true on every leg, on a real runner, with no empty reads.

*Correction, 2026-09-23 (post-closure, while reconciling the register).* The bullet above is left
as it was written, and two of its numbers were too narrow. A **sixth** CI producer record exists
at the candidate — `cap-sandbox-http.prerequisite.json`, the one CI build that went through
`docker buildx build` instead of `docker build` — and it reads the same
`comparison.buildx_version.matches: false` against `v0.37.0`, with
`incomplete: ["builder"]`. So the mismatch is 6 of 6, not 5 of 5, and it is **not** explained by
the dry-build path having chosen `docker build`: `ci.yml` asks the installer for `v0.37.1` just as
`release.yml` does, and `--builder` is named nowhere. Two further precisions: `configured` is
read out of the lock (`record_build_producer.py:217-231`), so the comparison cannot see whether
the workflow's own request was honoured; and the release path has never executed, so what the pin
would do to **published** images is unobserved rather than disproved. The canonical statement is
now `docs/known-issues.md` item 11 and
`docs/quality/cap-post-rc-batch-3-design-options-2026-09-23.md` §C.

## G. Tests and Helm validation

Tests collected by pytest at the candidate (two of these modules parametrise cases, so the
collected count exceeds the number of `def test_` functions):
`test_third_party_registry_evidence.py` 43, `test_third_party_image_lock.py` 30,
`test_build_producer_contract.py` 16, `test_release_image_completeness.py` 39 — 85 in the
two runs that covered them, all passing locally — plus `test_release_publication_gate.py` 43
and `test_dockerfile_base_images.py` 9. CI's packaging job is the live helm check: `helm lint`
plus `helm template` for tag-only, digest-only and tag+digest across all six image coordinates,
asserting the rendered strings and refusing the four malformed shapes.

### F-45 — the terminal CANCELLED write could be lost (found by CI, not by this batch)

CI at `f5a0cdd` (run `35749438118`) was **red**: 1753 tests, 1 failure —
`test_phase_28_2_cancellation.py::test_cancelled_runs_have_zero_evidence_writes` with
`sqlite3.OperationalError: database is locked` on `UPDATE acquisition_runs SET
status='CANCELLED' … AND worker_id = ?`. That statement is `AcquisitionWorkerPath`'s
`_finalize_cancelled`, reached from `run_claimed`'s `except WorkerCancelledError` branch — and
that branch is the only one of the five that finalises without rolling back its own session
first. With the fixture's 30s `busy_timeout`, the error means a writer held the lock for the
whole budget: the abandoned operation's own uncommitted work, blocking the dedicated connection
that was about to write the run's last state. A pre-existing defect, load-dependent (the
reproduction rate measured here was ~1/53 executions, worst under artificial CPU pressure),
documented in this repo before as the "zombie transaction holds the write lock until GC" class
(`app/worker/runtime.py:84-91`, `docs/quality/flake-triage-34012500372-pregate-d.md`).

`257ba18` fixes three properties of that write path, with a control per property:

| property | test | control that keeps it honest |
| --- | --- | --- |
| the cancelled-execution branch releases its own writer | `test_the_cancelled_execution_branch_releases_its_own_writer` | the same fixture without the rollback times out, so the test measures the lock, not the happy path |
| a transient lock does not lose the terminal write | `test_cancel_finalize_retries_a_transient_lock_on_the_terminal_write` (injected `database is locked`, asserts the write is retried and lands) | `test_a_non_transient_write_failure_still_propagates` — a `ProgrammingError` still raises and the run is *not* reported cancelled |
| the lease the claim took is given back | `test_cancel_finalize_releases_the_run_lease` (asserts `LeaseStatus.RELEASED`) | `test_a_lease_release_conflict_never_costs_the_terminal_write` — losing the release race must not cost the write |

The second and third properties came out of trying to fix the first. `WorkerLeaseRepository`
has no `update` method, which is what the release called, and the `except Exception: pass`
wrapped around it turned that `AttributeError` into every cancelled run keeping an ACTIVE lease
until the expiry sweep happened to find it (F-45b). And the first attempt at the rollback put it
inside `_finalize_cancelled_if_safe`, which the API-side cancel path also uses with legitimately
uncommitted work: `test_phase_28_1_worker_path.py::test_cancel_tolerates_terminate_failure` went
red (`'RUNNING' == 'CANCELLED'`) and the rollback moved to the branch that actually needed it.
A second attempt gave the lease release its own session; the same test went red again, because on
a single-connection bind closing that session rolls back the caller's uncommitted work. Both
mistakes are recorded here rather than quietly corrected: they are what the pre-existing tests
are for.

The Stage 7 named regression suite was run as one invocation at the frozen candidate
`257ba18` — `test_release_build_script.py`, `test_release_diff_classifier.py`,
`test_quality_gate_parity.py`, `test_deployment_doc_contract.py`, `test_secret_scan.py`,
`test_release_publication_gate.py`, `test_phase_28_5_linux_secrets.py`, plus the four Batch 2
modules (`test_third_party_image_lock.py`, `test_third_party_registry_evidence.py`,
`test_build_producer_contract.py`, `test_release_image_completeness.py`) and
`test_dockerfile_base_images.py` — and answered **313 passed, 4 skipped in 176.49s**. All four
skips are `_need_docker` (`test_phase_28_5_linux_secrets.py:49`, `skipif not docker`): this box
has no running Linux docker daemon, and those cases do execute in the Linux certification round
(§K), so the skip is a statement about the machine, not a gate that was passed over. The F-45
path was run separately as a 32-case group (§G's F-45 table): 32 passed.

The full-suite totals the report quotes are the candidate's own, read from CI (§J: 1758 tests,
0 failures, 0 errors, 133 skipped from `backend-evidence` artifact `10710942616` of run
`35761257654`). The earlier `1745` figure belongs to `3a69607` and is not claimed for this line;
CI runs the suite with its own five `--ignore` entries and one documented `--deselect`, so the
authoritative count is the one that run's junit reports, not a local re-count.

What was *not* measured locally: a whole-suite run on this Windows box aborts on a
`pytest-timeout` stack dump at roughly 36% progress (`_tmp/b2/local_full_suite.log`) and did not
complete locally at any commit of this line, so no local claim is made about the remaining cases
and no cause is attributed either way. The full-suite statement here is CI's, on
`ubuntu-24.04`, at the candidate (§J), supported by the three local groups that did finish: the
313-case named regression list, a 419-case acquisition/worker slice (`test_phase_28_*`, lease,
claim, evidence, reconcile — 418 passed, 37 skipped, 1 failed → that failure is F-45, and it was
fixed before the candidate was pushed), and the 32-case F-45 group.

## H. Classifier results (gate unmodified)

Per-commit and cumulative verdicts are in the table in §A; `_tmp/b2/classifier_audit.json`
holds the full machine-readable output. Cumulative `0561a7e..257ba18` =
RECERTIFICATION_REQUIRED, `runtime_affecting=true`, blocking files exactly
`backend/Dockerfile`, `backend/app/acquisition/worker_path.py`,
`deployment/helm/cap/values.schema.json`,
`deployment/helm/cap/values.yaml`, `deployment/third-party-images.json`,
`docker-compose.yml`, `frontend/Dockerfile`. The design line through `3b1f964` and the E2
commit are INHERITED, as the plan predicted; the three hops `e3177ec`, `d8472d0`, `f5a0cdd`
are INHERITED over `test_harness` + `certification_generator`, which is why each could be made
without re-touching the runtime surface. The fourth (`257ba18`) is not inheritable and does not
claim to be: it is `production_runtime` + `test_harness`, so it joins the recertification the
rest of the line already owes. No unexpected verdict appeared, so the classifier was not
modified.

## I. Candidate SHA

`257ba18d8cb6c22d330701b69713544254e4f03a`, clean tree, `origin/main` == candidate. It is the
fourth freeze of this round: `3a69607` → `e3177ec` (CI's producer record could not be read
back) → `d8472d0` (three D.4 contracts lacked their controls) → `f5a0cdd` (the record
misattributed the builder, and a pin/producer split was invisible) → `257ba18` (CI at
`f5a0cdd` was red on F-45, a real defect in the cancel finalisation path that the pins only
provoked by shifting suite timing). No amend, no rebase, no docs commit on top of any of them.

## J. CI at the candidate — green, read from the run's own artifacts

`ci.yml` run **35761257654** — `head_sha = 257ba18d8cb6c22d330701b69713544254e4f03a`, event
`push`, **completed / success** (https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35761257654).
All ten jobs are `completed/success`: `backend`, `frontend`, `migration`, `image-and-security`,
`packaging`, and the five `release-image-builds` legs (`cap-backend`, `cap-frontend`,
`cap-egress-proxy`, `cap-sandbox-http`, `cap-sandbox-browser`).

Full-suite totals read from that run's `backend-evidence` artifact (id `10710942616`, member
`junit-backend.xml`): **tests=1758, failures=0, errors=0, skipped=133**. The +5 against
`f5a0cdd`'s 1753 are the F-45 regression tests and their controls (§G); the 133 skips are the
docker-daemon-gated cases that the Linux round executes for real. `packaging` is the live helm
check named in §G — `helm lint` plus `helm template` for tag-only, digest-only and tag+digest —
so those assertions ran on a real helm, not only in pytest. The producer records for the five
images are read from the `ci-release-image-*` artifacts in §F, ids as listed by that run:
`cap-backend` `10709683637`, `cap-frontend` `10709488824`, `cap-egress-proxy` `10710445051`,
`cap-sandbox-http` `10709309942`, `cap-sandbox-browser` `10710238765` (plus `helm-chart`
`10710158721` and `frontend-dist` `10709579735`).

## K. Linux production — green at the candidate from the `layer: release` dispatch

`layer: release` is the only input that turns `cap-production-certification` on
(`cap-linux-certification.yml:26-36, 226-229` — read before dispatching, unchanged since). Run
**35763786639**, `head_sha = 257ba18d8cb6c22d330701b69713544254e4f03a`, event
`workflow_dispatch`, **completed / success**, 17:55:22Z → 18:10:58Z
(https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35763786639). Job-level
conclusions, which is what the release gate's `REQUIRED` set checks:

| job | status |
| --- | --- |
| `cap-production-certification` | completed / success |
| `full-certification` | completed / success |
| `postgres-version-matrix (15-alpine / 16-alpine / 17-alpine)` | completed / success (×3) |
| `fast-certification` | completed / skipped — the PR-layer job, correctly not run on a release dispatch |

Both REQUIRED jobs (`cap-production-certification`, `postgres-version-matrix`) are therefore
green at the candidate itself; no inheritance was used.

`cert-artifacts-release` (id `10712355258`, member `cap-28.5-linux-certification.json`) reads
back `commit = 257ba18d8cb6c22d330701b69713544254e4f03a`, `tests = {total: 202, passed: 198,
failed: 0, skipped: 4}` and all twelve gates `PASS` (`container_isolation`, `filesystem`,
`memory`, `cpu`, `pids`, `network_enforcement`, `ssrf_defense_in_depth`, `hard_cancellation`,
`reaper`, `browser`, `secrets`, `real_integration`), with `secret_canary_leaks = []`. The images
it built carry `repo_digest: []` — local-only dry builds, nothing published, consistent with §Q.

Two fields in that same artifact are worth stating rather than smoothing over:
`worker_control_plane_isolation = "PARTIAL"` and
`unrestricted_docker_socket_mounted = true`, and the workflow still passes because neither is a
gate in its own list. That asymmetry — a certification that records a PARTIAL isolation answer
without failing — is a question for the next batch's gate work, not something Batch 2 was asked
to change.

The completed green release-layer run at `3a69607` (`35745049548`) remains supporting evidence
only, not the candidate's.

## L. K8s — green at the candidate, authority read with the gate's own spec

Push-triggered `cap-k8s-certification.yml` run **35761257518**, `head_sha =
257ba18d8cb6c22d330701b69713544254e4f03a`, **completed / success**
(https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35761257518). The gate's
REQUIRED job set for this workflow is `("k8s-certification",)`; that job is
`completed/success` and the check reported no problems.

Its verdict artifact was read the way `release.yml` reads it: artifact
`k8s-cert-artifacts` (id `10710582964`, not expired), member
`cap-28.6-k8s-certification.json`, `commit = 257ba18d8cb6c22d330701b69713544254e4f03a` —
candidate-direct, no inheritance used — and `gate_summary = {total: 34, passed: 34, failed: 0,
not_run: 0}`, so `passed == total` and both counters are zero. No problems were raised by the
reader.

## M. Reliability — 7200s soak green at the candidate

Dispatched with `soak_seconds: "7200"` (the certified value; the 24h soak stays out of scope).
Run **35763798093**, `head_sha = 257ba18…`, event `workflow_dispatch`, **completed / success**,
17:55:28Z → 20:02:57Z (2 h 07 m 29 s wall, the soak window itself 18:00:58Z → 20:00:58Z)
(https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35763798093). The gate's
REQUIRED set for this workflow is `("reliability",)` — that job is `completed/success` and the
job-set check returned no problems.

`reliability-evidence` (id `10716133581`, member `ga-dr/soak-context.json`, gate `GA-GATE 24`)
records what actually happened on the cluster: `soak_seconds 7200`, `ticks 480` with
`healthy_ticks 480`, `runs_created 480`, `runs_cancelled 48`, `pagination_requests 1440`,
`http_errors 0`, `downtime_seconds 0`, eleven `worker_pods_killed` at 600-second offsets (chaos
injected, not observed failure), and RSS samples across the window for the leak analysis. Its
own disclosure line is quoted rather than paraphrased: *"plain HTTP acquisitions only — the
acquisition API exposes no per-run browser/tool selection; browser sandbox is exercised by the
phase 28.5 certification suite"* — i.e. the soak covers the HTTP path, and the browser path is
covered by the Linux round (§K), not by this gate.

One disclosure the artifact itself forces. That same zip (`10716133581`) also contains
`cap-cert-ga/cap-28.7-ga-certification.json` recording `mode: "development"`,
`full_ga_certified: false`, `gate_summary {total 40, implemented 38, passed 5, failed 0,
not_run 35, skipped 0, planned 0}` — the reliability workflow's own development-mode GA
snapshot. It is not §N and must not be read as it: the strict round is a separate
`cap-ga-certification.yml` run with its own `ga-cert-artifacts` artifact. This is precisely
the shape F-33's authority rule exists for — the gate reads the GA claim from the GA
workflow's designated artifact and rejects any other, rather than taking whichever run it
happens to select.

## N. Strict GA — green at the candidate, authority read with the gate's own spec

The guarded script refuses a strict dispatch without a finished green soak at the same SHA, and
it logged exactly that: `soak precedent: 35763798093`. Strict GA was dispatched at 20:03:36Z with
`ga_strict: true` → run **35777783446**, `head_sha = 257ba18d8cb6…`, event `workflow_dispatch`,
**completed / success** 20:03:35Z → 21:03:20Z (~59 min)
(https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35777783446).
`check_required` against the gate's `REQUIRED["cap-ga-certification.yml"]` =
`("ga-certification", "supply-chain")` returned **no problems**: both jobs
`completed/success`.

The authority was then read the way the gate reads it — `ga-cert-artifacts` (artifact id
**10719666889**, digest `sha256:50931bb93a30…`, member `cap-cert-ga/cap-28.7-ga-certification.json`),
`problems: []`:

| Stage 11 field | Read from the artifact |
| --- | --- |
| `mode` | `final-strict` |
| `full_ga_certified` | `true` |
| `commit` | `257ba18d8cb6c22d330701b69713544254e4f03a` — equal to the candidate |
| `gate_summary` | `{total 40, implemented 38, passed 40, failed 0, not_run 0, skipped 0, planned 0}` |
| `passed == total` | 40 of 40; every one of the 40 `gates` entries is `PASS` (`_tmp/b2/ga_strict_summary.json`) |
| test evidence | `test_results`: 67 recorded, 67 `passed`, no other status |
| measured recovery | RPO 9.777 s, RTO 232.762 s (cluster B 38.98 + Cilium 36.26 + infra 113.27 + CAP deploy 20.86 + restore 23.39) |
| its own decision line | `release_status: "FULL GA CERTIFIED -- awaiting explicit release authorization"` |

`implemented: 38` next to `total: 40` needs explaining rather than glossing. In
`scripts/certification/generate_report_28_7.py`, `BASELINE_GATES` is empty — the comment says
"no gate passes by assertion-free default" — and `implemented` counts only the gates with a
mapped certifying testcase. The two outside that set are the suite-level evidence gates,
`GA-GATE 33` (`_suite_status("junit-security.xml", …)`) and `GA-GATE 40`
(`_suite_status("skip-report", …)`), both computed from artifacts rather than from a testcase,
and both **PASS** in this run. A gate with no implementation would be `PLANNED`, and
`planned == 0` here, which in final-strict mode is the condition that would have failed the
decision. So 40/40 is 40 gates with positive evidence behind them, not 38 plus two defaults.

## O. Supply chain — required job and its evidence

`supply-chain` (a REQUIRED job of the same workflow) is **completed/success**, listed both by
`check_required` and in the gate's own selection line:
`jobs: {'ga-certification': ['success'], 'supply-chain': ['success']}`. Its evidence travels in
the same `ga-cert-artifacts` package read above:

- `junit-supply-chain.xml` — 2 suites, **5 tests, 0 failures, 0 errors, 0 skipped**;
- `provenance.json` — `spec_version: cap.provenance.v1`, `builder: github-actions`,
  `commit: 257ba18d8cb6…`, generated 20:06:10Z, with `subject` naming each built image by
  digest (`cap-backend:ci sha256:7fb89d49190d…`, `cap-frontend:ci sha256:3387c272bb5e…`,
  `cap-sandbox-http:ci sha256:94ca06222806…`, `cap-sandbox-browser:ci sha256:1…`) and `sbom`
  entries carrying both `sbom-cyclonedx.json` (`sha256:ce650d221e16…`) and `sbom-spdx.json`
  (`sha256:9a75627cf056…`);
- `images.json` — the same commit, image digests content-addressed, generated 20:05:37Z;
- `security-recert.json` — `gate: GA-GATE 33`, suite "CAP Linux Certification (phase 28.5
  security)", `head_sha = 257ba18d8cb6…`, `conclusion: success`, resolving to runs
  `[35763786639, 35761257668]` — i.e. the security claim is tied to this commit's own runs;
- `skip-report.json` — `skipped: 0` across `junit-ga.xml`, `junit-reliability.xml` and
  `junit-supply-chain.xml`, which is what GA-GATE 40 consumes.

Note the scope of these digests: they are the **CI-built `:ci` images** of this round, not the
sealed `1.0.6-rc1` tags (§Q), because no release publication ran in Batch 2 and none was
permitted to.


## P. Gate dry-run — both halves measured on live data

Executed as a hypothetical `v1.0.7-rc1` (no tag created) by
`_tmp/b2/run_gate_at_candidate.py`, which lifts `release.yml`'s `RELEASE_GATE_PY` verbatim and
routes the missing `gh` binary through a transport shim that decides nothing.

*Before the rounds*, at `3a69607` (saved as `_tmp/b2/gate_dryrun_refuse_3a69607.json`):
`verdict=FAIL`, six failures, `errors=null`, K8s accepted at distance +0, and eight green Linux
runs passed over because `cap-production-certification` was `skipped` — F-42 firing on live data,
not only in tests.
*Before the last two rounds*, at the candidate `257ba18` (exit 1, `verdict=FAIL`,
`errors=null`, `_tmp/b2/gate_dryrun_refuse.json`): five failures, and they were exactly the two
rounds outstanding at that moment — the push-triggered GA artifact answering `mode='development'`,
`full_ga_certified=False`, `gate_summary.planned=5` and 35-of-40 gates, plus
`cap-ga-reliability.yml` inheriting from `b671f53` at a 60-commit distance that is
RECERTIFICATION_REQUIRED. `cap-linux-certification.yml` and `cap-k8s-certification.yml` have
dropped out of the failure list, because §K and §L put both at distance +0 on the candidate.
The refusal's own blocking-file list now names
`backend/app/acquisition/worker_path.py` alongside the compose/lock/helm/Dockerfile set — so the
gate will not inherit across the F-45 change either, which is the behaviour §H predicts.

*After every round landed*, the same command with `--expect pass` at the candidate as
hypothetical `v1.0.7-rc1` (`_tmp/b2/gate_dryrun_pass.json`, gate-written copy at
`outputs/cap-cert-release/release-certification-gate.json`) — **exit 0, `verdict: PASS`,
`failures: []`**, all four selections candidate-direct at distance **+0**, `sha` equal to the
candidate in every one:

| Workflow | Selected run | url | distance | jobs (per leg) |
| --- | --- | --- | --- | --- |
| `cap-linux-certification.yml` | 35763786639 | /actions/runs/35763786639 | +0 | `cap-production-certification: [success]`, `postgres-version-matrix: [success, success, success]` |
| `cap-k8s-certification.yml` | 35761257518 | /actions/runs/35761257518 | +0 | `k8s-certification: [success]` |
| `cap-ga-certification.yml` | 35777783446 | /actions/runs/35777783446 | +0 | `ga-certification: [success]`, `supply-chain: [success]` |
| `cap-ga-reliability.yml` | 35763798093 | /actions/runs/35763798093 | +0 | `reliability: [success]` |

Both authorities PASS with empty `rejects`: K8s `k8s-cert-artifacts` id **10710582964**
(`sha256:5640f54d70a9…`, read `{total 34, passed 34, failed 0, not_run 0}`), GA
`ga-cert-artifacts` id **10719666889** (`sha256:50931bb93a30…`, read `mode final-strict`,
`full_ga_certified true`, `gate_summary {total 40, passed 40, failed 0, not_run 0, skipped 0,
planned 0}`). `ancestors_considered: 80`, `runs_examined` 100/100/100/67. The Linux row is the
`layer: release` production run of §K, not a push run — the same selection the two refusals
above refused to accept from any other source, which is what F-42 requires.

Two wrapper defects surfaced here and were fixed by matching the gate's real schema, not by
relaxing a check. First: `errors == []` was asserted against a key the gate's evidence object
does not have — its keys are `tag_sha, tag_ref, version, required, authority,
ancestors_considered, evidence, failures, verdict`, and "I could not look" is carried as
`verdict="ERROR"` (release.yml's own `verdict="ERROR"` assignment). The assertion now requires
`verdict != "ERROR"` and no authority answering ERROR; the earlier refuse outputs printed
`"errors": null` for exactly this reason. Second: `jobs` maps each required job to a **list of
leg conclusions** (a matrix job has three), so the string comparison `== "success"` was replaced
by "the leg list is non-empty and every leg is `success`", plus `sha == candidate` and
`distance == 0` per workflow — stricter than what it replaced.

## Q. Sealed-release integrity

Read on 2026-09-22 through `_tmp/b2/sealed_release_integrity.py` (GET-only GitHub REST +
ghcr manifest fetches; `_tmp/b2/sealed_release_integrity.json`), replacing an earlier
attempt that leaned on `git ls-remote --tags origin` — that command returns *nothing* on
this box (`github.com:443` direct is blocked), so it could never have shown this.

| Check | Measured |
| --- | --- |
| Tag not moved | `refs/tags/v1.0.6-rc1` is still the annotated object `d0c7c7dc14cb…`, peeling to commit `4d8f9c72b72d…`, tagged-at `2026-09-21T02:18:13Z` — the exact triple recorded in `docs/quality/cap-1.0.6-rc1-publication-closure-2026-09-22.md`. `v1.0.6-rc1_unchanged: True`. |
| No tag created | Every tag's tagger/committer date read from the git API; the newest is `v1.0.6-rc1` at `2026-09-21T02:18:13Z`. **Zero** tags dated on or after `2026-09-22T00:00:00Z`, and no `1.0.7-*` exists. Nothing in Batch 2 created a release tag. |
| Release not edited | Release id 392662225 (`v1.0.6-rc1`), `draft: false`, `prerelease: true`, `published_at == updated_at == 2026-09-21T02:38:59Z` — an edited release moves `updated_at`, and it has never moved. |
| Assets not touched | All five assets (`cap-1.0.6-rc1.tgz`, `CHANGELOG.md`, `known-issues.md`, `v1.0.6-rc1.md`, `values-release-1.0.6-rc1.yaml`) carry ids 578059195–578059201 with `created_at == updated_at == 2026-09-21T02:38:59Z` and `download_count: 0`. No asset was replaced, deleted or updated during Batch 2. |
| Image tags not overwritten | Each `1.0.6-rc1` manifest re-read from ghcr returns HTTP 200 with a `Docker-Content-Digest` that reparses identically when the tag is instead fetched *by that digest*, and all five digests match the full 64 hex characters recorded in tracked docs **before** Batch 2 (`cap-provenance-identity-observation-2026-09-21.md`, `cap-1.0.6-rc1-publication-closure-2026-09-22.md`, `cap-post-rc-batch-1-1-release-gate-closure-2026-09-22.md`; `_tmp/b2/q_digest_compare.json` → `full_match: True` for all five): backend `a733b90c7a8417b1…`, frontend `e1b1889a868c114f…`, egress-proxy `8ab8c234f278436f…`, sandbox-http `36bb2f7993ac8eb9…`, sandbox-browser `b369618871bd3bae…`. Consistent with every Batch 2 build being a local dry build (`pushed: false`) — no process in this round held registry write credentials. |
| No promotion | `/releases` sorted by `published_at`: `v1.0.6-rc1` is still `prerelease: true`, and the newest non-draft, non-prerelease release is `v1.0.5` (`2026-09-07T14:59:44Z`). The RC was never promoted to latest. |

Q verdict: **SEALED RELEASE INTACT** — the scripted conjunction of all six checks.

Two things could not be read and are therefore *not* claimed: ghcr's OCI referrers endpoint
answers 404 for the sealed manifest (so no build attestation was re-verified here — that
observation belongs to Batch 1's `cap-provenance-identity-observation-2026-09-21.md`), and the
packages REST `/versions` endpoint lists versions without exposing their tags for these
user-owned packages, so "which package version owns the tag" is unverified. The immutability
claim above rests on the digest comparison, which does not need either.


## R. Deferred B3/B4

Neither was started. `git diff --name-only 0561a7e..257ba18` is 33 files and touches no B3/B4
surface — nothing matching bump/upgrade/scan/monitor naming appears in that set
(`_tmp/b2/changed_files_candidate.txt`). B3 remains withheld behind the five open answers from
the design freeze, B4 and the 24h soak and G(ii)-(v) stay out of scope. F-44 (§F) is recorded
rather than fixed here, because enforcing a builder-pin match at publication is a gate decision
no approved Batch 2 item authorised.

## S. FINAL VERDICT

# **BATCH 2 COMPLETE — RECERTIFIED**

against `BATCH2_CANDIDATE_SHA = 257ba18d8cb6c22d330701b69713544254e4f03a`
(`origin/main == candidate`, no implementation commit was added after the freeze, and
**no release tag was created**).

| Requirement | Measured | Section |
| --- | --- | --- |
| CI at the candidate | run 35761257654 `completed/success`, `head_sha` = candidate, junit 1758 tests / 0 failures / 0 errors / 133 skipped, 10 jobs green incl. all five release-image builds | §J |
| Linux production, candidate-direct | run 35763786639 via `layer: release`, `cap-production-certification: [success]`, `postgres-version-matrix: [success ×3]`, distance +0 | §K |
| K8s | run 35761257518 at +0; authority `k8s-cert-artifacts` 10710582964 → `commit` = candidate, `{total 34, passed 34, failed 0, not_run 0}`, `verdict PASS`, `rejects []` | §L |
| Reliability | run 35763798093, `soak_seconds 7200`, 480/480 healthy ticks, 0 http_errors, 0 downtime, job `reliability` success at +0 | §M |
| Strict GA | run 35777783446 success at +0; authority 10719666889 → `mode final-strict`, `full_ga_certified true`, `commit` = candidate, `{total 40, passed 40, failed 0, not_run 0, skipped 0, planned 0}` | §N |
| Supply chain | `supply-chain` job success; 5/5 supply-chain tests, provenance + both SBOMs, `security-recert` bound to this commit | §O |
| Gate dry-run, both halves | refuses at `3a69607` (6 failures) and mid-round at the candidate (5 failures); **PASS** at the candidate: exit 0, `verdict PASS`, `failures []`, no `ERROR` verdict, all four workflows at +0 | §P |
| Sealed release untouched | tag object and peel unchanged, zero new tags, release/asset timestamps unmoved, five `1.0.6-rc1` image digests byte-identical to the pre-Batch-2 records, `v1.0.5` still newest stable | §Q |
| Approved scope only | C/F-24, postgres reversal, D/F-41, E2, E3, B2 all implemented and certified; B3/B4 untouched; 24h soak and G(ii)-(v) not attempted | §A, §R |
| Classifier untouched | every predicted verdict held; the four re-freezes were each forced by evidence, never by an unexpected classification | §H |

What this verdict does **not** claim:

- **F-44 stays open** (§F, and see the 2026-09-23 correction there): **no** CI producer record at
  the candidate — 6 of 6, on both build paths — shows the pinned buildx/BuildKit as the producer,
  `--builder` is named nowhere, and the release path has never run so its producer is unobserved.
  Deciding whether that blocks publication is a gate-policy call no approved Batch 2 item
  authorised. **F-45 is closed** (§G) but only for the cancel-finalisation path it described, and
  **F-46** (`docs/known-issues.md` item 13) was filed alongside: the Linux certification pins the
  runtime-socket disclosure rather than requiring isolation, which is a policy question and not a
  Batch 2 regression.
- The reliability round's own caveat stands as written (§M): the soak exercises the plain HTTP
  acquisition path, and the embedded development-mode GA snapshot in that artifact is not the
  strict round's verdict.
- **Two deliverable files are untracked by design.** This report and
  `docs/quality/cap-post-rc-batch-2-findings-reconciliation-2026-09-22.md` exist in the working
  tree at the candidate; the freeze forbids a docs commit on top of it, so committing them is a
  decision for after this report — and the reconciliation notes that commit must classify
  `INHERITED`, which is the proof it cannot disturb what was certified.
- The **local full test suite could not be completed on this box** (§G): it aborted at ~36% on a
  timeout dump under resource pressure, unattributed. The green signals above are CI's and the
  certification rounds' own runs at the candidate, not a local restatement.
- Helm schema assertions run only in CI — helm is not installed here (§G).
- **No next release tag.** `v1.0.6-rc1` stays sealed and `v1.0.5` stays stable; publishing this
  line needs its own authorisation, at which point the gate of §P runs for real against a tag
  rather than as a dry run.

