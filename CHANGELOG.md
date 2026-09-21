# Changelog

All notable changes follow Keep a Changelog categories and Semantic Versioning 2.0.0.

## [Unreleased]

Post-1.0.5 delivery audit. These are unreleased fixes on top of the `1.0.5`
anchor; under the version policy they require a new RC (`1.0.6-rc1`) because
published release contents are immutable.

### Added

- `CAP_ZAP_API_KEY` is now actually consumed. `.env.example` advertises it, but
  `Settings` had no matching field and `model_config` uses `extra="ignore"`, so
  the documented variable was silently discarded and `MemorySecretProvider` --
  constructed empty in `create_app()` with no other writer -- could never hold
  the `zap-api-key` reference that `get_zap_api_key` resolves. Every route
  reaching `AssessmentService` therefore failed with `SECRET_NOT_FOUND`, and
  because `POST /incidents` depends on `PlaybookService` which depends on
  `AssessmentService`, **creating an incident was impossible on a fresh
  deployment**. The startup path now seeds the provider with the configured
  reference name.
- Console test suite covering the operator flows the audit closed (29 tests,
  including the platform's three error payload shapes and the incident
  assignment dialog's refuse-a-no-op / post-a-real-change pair).
- Console acquisition run lifecycle: `POST /acquisitions/{run_id}/resume` (requeue
  at the persisted checkpoint) and `POST /acquisitions/{run_id}/cancel` existed
  and were covered by backend tests, but the console exposed neither, so an
  operator could start a run and could not stop or resume one from the UI. Both
  are now wired with a confirmation step and status-gating that mirrors the
  backend's terminal-state rule (resume on a terminal run is a 409).
- `scripts/certification/verify_backup_manifest.py` -- the last gate before a DR
  restore is applied -- is now covered by hermetic tests. Until now it was only
  ever exercised against a live certification run's evidence directory, so the
  fail-closed behaviour itself had no local proof. The new tests build a backup
  whose manifest is produced by the same algorithm, assert it verifies, and then
  tamper an object, truncate the pg dump, append an object after the manifest
  was written (how a stale evidence directory drifts), and delete the manifest
  or the dump -- each refused before any restore step runs.
- The workflow engine's APPROVAL node is a real gate now. It had been the Phase 3
  placeholder that always returned WAITING, so any playbook containing a human
  gate could be started but never finished, and `POST /workflow/run/{id}/resume`
  simply parked it again. The node now reads the reviewer's decision recorded on
  the run: approved continues, refused fails the run with the reviewer's reason
  (a human saying no is not a transient error to retry). `WorkflowService.decide`
  and `POST /workflow/run/{instance_id}/decision` record it, that route requires
  `approval.decide` -- the same permission as a response-plan approval -- and the
  trail (state, actor, reason, timestamp) persists on the run context. Covered by
  seven tests: parking, resume-cannot-pass, approve, refuse, answer-once,
  answering a run that never asked, naming the wrong gate, and the permission
  mapping.

- `deployment/third-party-images.json` locks the containers and binaries that the
  deployment and certification surfaces pull (MinIO server, Postgres, `mc`, `kind`)
  with registry, tag, digest, provenance evidence and an upgrade procedure.
  `backend/tests/test_third_party_image_lock.py` fails when a reference site and the
  lock disagree, when a bootstrap installs a binary without verifying a published
  checksum, or when the retirement note explaining the pin is deleted -- with
  negative controls proving the checker rejects drift *and* accepts the locked
  coordinate.
- `backend/tests/test_compose_env_contract.py` keeps the three lists that have to
  agree for `docker compose config` to succeed: the `${VAR:?...}` carriers compose
  requires, the env block the CI packaging job supplies, and the keys `.env.example`
  documents.
- `backend/tests/test_zap_api_key_contract.py` holds the README's promise about
  `CAP_ZAP_API_KEY` against the code: the secret resolves when configured, the
  documented `SECRET_NOT_FOUND` failure happens when it is not, compose forwards it to
  the API, the chart mounts it through the `cap-runtime` Secret, and a placeholder
  value is rejected in production.

- `scripts/quality/scan_secrets.py` is an executed credential gate, not a promise:
  stdlib-only (so it cannot be broken by a retired vendor download, which is how
  this line's tooling has failed before), scanning every git-tracked file for
  provider-shaped tokens, PEM material with a body, JWTs, credentials embedded in
  URLs, high-entropy values assigned to secret-shaped names, and a tracked `.env`.
  It reports **1029 files scanned, 0 findings**. `test_secret_scan.py` drives it in
  both directions -- five planted credentials must be found, eleven
  reference-shaped values must not be, and a provider token must still be a finding
  inside `tests/`, `docs/` and `scripts/`, because the exceptions are scoped to the
  weak rules and each states its reason.
- `backend/tests/test_migration_catalogue.py`, run by two new CI jobs: `migration`
  in `ci.yml` (fresh install, catalogue, downgrade-to-base rebuild against a real
  PostgreSQL 16 service, every push) and `postgres-version-matrix` in the Linux
  certification workflow (the same chain on 15/16/17, `fail-fast: false` so one
  broken version does not hide the state of the others). Until now the only Alembic
  step in CI was `alembic heads`, which never opens a connection.
- `backend/tests/test_certification_workflow_contract.py` asserts the invariants
  that only surfaced as red jobs: no identifier handed to Docker may embed a git
  ref, every dispatchable workflow must have a job that can actually run and every
  declared input must be read somewhere, and no download that becomes an executable
  may use `curl` without `-f`.
- K8S-GATE 33 certifies the console's routing through the shipped nginx image: the
  built bundle is served, a deep link rewrites to `index.html`, `/api/` proxies to
  the application with no identity headers from the client (which is what proves
  `envsubst` filled `X-CAP-User` and `X-CAP-Proxy-Secret`), and an unknown `/api/`
  path returns the application's 404 rather than the SPA fallback. Previously the
  console's nginx config was certified only by "the pod became ready".

- `backend/tests/test_sandbox_socket_boundary.py` binds the socket boundary to its
  documentation in both directions: no chart template may mount a runtime control
  socket, and while compose does mount one, the warning has to exist in the compose
  file, in `.env.example` and in known-issues -- and if the mount is ever removed,
  the sentences that promise it fail the test, so prose cannot outlive the code.

- The four certification workflows cancelled an in-flight run whenever the
  candidate branch received another commit (`cancel-in-progress: true` on a per-ref
  concurrency group), which destroyed a 17-minute release-layer run mid-flight and
  would have destroyed a 2-hour soak. Certification produces an artifact bound to the
  SHA that ran, so a cancelled run is evidence for nothing; they now queue instead.
  `ci.yml` keeps cancellation -- superseded unit-test runs have no evidence value --
  and `test_certification_workflow_contract.py` pins both halves.

- `scripts/quality/audit_junit.py` turns a job's JUnit (and optional coverage XML) into
  the two tables the release report publishes: which tests did not run, grouped by what
  the skip *message* says, and which modules and files the headline coverage percentage
  hides. Whether a skip matters is decided by the test's own identity, not by its
  excuse -- a security test that skipped for want of a container runtime is still a
  security test that did not run. The tool is descriptive by design: each certification
  job owns its strictness policy, so it never fails a build, it only refuses to let a
  skip be anonymous. Until now both tables were assembled by hand from ad-hoc queries,
  which is how "132 skipped" reached a report without naming the twelve object-store
  skips inside it. CI's unit job now writes that evidence itself: it passes
  ``--junitxml=junit-backend.xml`` and uploads the file beside ``coverage.xml`` in the
  ``backend-evidence`` artifact, and `test_quality_gate_parity.py` binds generation to
  upload -- every machine-readable report a job's own command produces has to appear in
  an ``upload-artifact`` path of the same job, because a file nobody uploads is evidence
  that does not exist.
- Release evidence now binds itself to a commit. The Linux certification artifact records
  the SHA it certified (`GITHUB_SHA`, or `git rev-parse HEAD` locally), and the
  release-layer gate refuses an artifact that records no commit, records `"unknown"`, or
  records a different one than its own job checked out. The same gate stopped asserting a
  control-plane verdict *word* it does not own: it re-derives the per-path socket facts
  from `scripts/certification/generate_report.py`, refuses `PASS` while any shipped
  deployment path mounts a container-runtime socket, and treats a chart socket mount as a
  release blocker. Two contract tests hold the class shut -- one fails any workflow step
  pinning an isolation verdict, the other **executes** that inline gate step against a
  truthful artifact and against every shape it must refuse, inline workflow Python no
  test had ever run before.
- `scripts/` joined the lint gate in both `make lint` and CI, and
  `backend/tests/test_quality_gate_parity.py` binds the Makefile targets to the CI steps
  so the two declared gate sets cannot drift apart again. The same test requires every
  suite CI `--ignore`s and every node CI `--deselect`s to keep a home that actually
  collects it -- another CI job, or an explicit note in `ci.yml` naming where -- because
  an exclusion that quietly ends a test's only execution is a hole in the release
  evidence, not a speed-up. `scripts/quality/**` also became its own classifier category
  (`repo_tooling`), justified by a test that resolves the real `docker build` contexts and
  the chart/compose files rather than by a comment: the moment anything under `scripts/`
  can reach a container, that category has to fall back to fail-closed.
- **Releases were published without any certification gate, under a comment that said
  there was one.** `cap-linux-certification.yml` claimed "release jobs depend on
  `cap-production-certification` (the `release.yml` workflow includes this file via
  `workflow_call` and lists it as a required job before publishing artifacts)". Both halves
  were untrue: `release.yml` calls only `ci.yml`, and no job in any workflow required a
  certification run -- a tag on an uncertified commit would have pushed images, packaged the
  chart and created a GitHub Release while every reader of that comment believed it had been
  checked. `release.yml` now has a `verify-certification` job between `validate-tag` and
  every job that publishes anything: it resolves a completed successful run per certification
  workflow whose *release jobs* actually executed (a green PR-layer run is not release
  evidence) for the tagged commit or the nearest of its 80 ancestors, accepts an ancestor
  only when `scripts/release/classify_diff.py` proves the distance is runtime-neutral, writes
  machine-readable evidence of what it looked at, and fails closed -- an API error raises
  instead of being reported as "uncertified". `test_release_publication_gate.py` executes that
  inline gate against canned Actions-API answers (its acceptance rules *are* the behaviour) and
  pins the `needs` graph, the `fetch-depth: 0` and `actions: read` the ancestor walk depends
  on, and the wording of the comment that hid the gap; each rule was negative-controlled by
  mutating the gate, and each mutation failed its own test. The same sweep corrected two more
  sentences that described enforcement that did not exist (the release-layer header, and GA-GATE 33's
  "runs on every push"), and the job-set rule was measured rather than argued: applied to the last 60
  completed runs of the Linux certification workflow it accepts 4 of the 21 green runs -- the
  dispatched release rounds -- and refuses the other 17, every one of which a colour-only gate would
  have accepted as release evidence.
- **The GA report generator stopped lying in the reliability job, and stopped being lied to.** The
  error-log audit of the soak found `GA certification FAILED gates: [33 gates]` printed by a job that
  *succeeded*: `cap-ga-reliability.yml` generates the GA artifact so its own DR evidence is visible,
  every other gate's tests live in another workflow, the development-mode decision calls `NOT_RUN`
  a failure, and `|| true` threw the exit code away -- one line that was false (a green run does not
  have 33 failures) and blind at once, because a real `FAIL` or a certification test that started
  skipping printed the same words and vanished the same way. The generator now has a third mode,
  `CAP_GA_PREVIEW=1`: absence is reported as absence and excluded from the decision, a `FAIL` or a
  skipped test stays fatal, strict mode ignores the flag so it cannot soften a FULL GA verdict, and
  the workflow keeps the exit code (F-22). Executing the generator outside CI turned up two more of
  its own defects (F-23): with `CAP_GA_OUT` redirected, the JUnit parser fell through to the
  repository's leftover `outputs/cap-cert-ga/junit-ga.xml` and reported 28 gates PASS for a run that
  had produced nothing -- the fallback now applies only where it was ever meant to -- and a Tier-2
  evidence file outside the checkout raised `ValueError` from `relative_to(REPO_ROOT)` *after* the
  artifact was written, killing the report over the cosmetic form of a source path.
  `backend/tests/test_ga_report_preview_mode.py` runs the script as a real process in each mode and
  with each evidence layout; each rule was negative-controlled by reverting it.

- **F-7 closed: the release now publishes the whole image graph the chart
  deploys, and F-20 with it.** The Helm chart could deploy five CAP images while
  `release.yml` built and pushed two, and the other three were named
  `cap-sandbox-http:latest`, `cap-sandbox-browser:latest`,
  `cap-egress-proxy:latest` -- coordinates no artifact ever served, so a fresh
  `helm install` died at its first acquisition and the K8s certification passed
  only because the runner happened to have built them locally. `release.yml` now
  builds and publishes all five through one script
  (`scripts/release/build_release_image.sh`): VERSION/REVISION build args, SBOM and
  provenance attached, and the index digest, linux/amd64 child digest, Dockerfile
  SHA, staged-context hash and source revision written to an evidence record per
  image. `cap-sandbox-browser` can no longer layer on a mutable local image -- its
  base is `--build-arg SANDBOX_HTTP_BASE`, with no default in the Dockerfile, and
  the release passes the digest it published for `cap-sandbox-http` minutes
  earlier. The chart composes every coordinate through `cap.imageRef`, defaults to
  the chart's own `appVersion` instead of `latest`, and accepts a `digest` to pin
  the exact manifest list; `release-image-completeness` renders
  `values-release-<version>.yaml` from the published digests and ships it as a
  release asset. Publication refuses a partial set: `release-chart` and
  `publish-release` both wait for that job, so four of five images -- or a missing
  attestation, digest or tag -- cannot produce a GitHub Release.
  `test_release_image_completeness.py` derives the chart's image set from
  `values.yaml` and the templates (not from a hand-written list of five) and
  compares it with the parsed release matrix in both directions, then **executes**
  the two inline release gates against fabricated evidence -- which is how two bugs
  in the renderer itself were found and fixed before anyone ran it. F-20: every
  external `FROM` is now `name:tag@sha256:…`, the digests read from the registry
  and re-fetched by digest to prove byte identity
  (`docs/quality/artifacts/cap-1.0.6-rc1-artifact-closure/registry-base-digests.json`), and
  `deployment/third-party-images.json` records base, platforms and provenance for
  all five -- with `test_dockerfile_base_images.py` failing on an unpinned or
  unlisted base. The certification installs that relied on the `:latest` defaults
  now name versioned images they build and load themselves, and K8S-GATE 34
  refuses a cluster whose running CAP image set is not the released one.
  Getting that far took three repairs found by running rather than reading, each
  now gated in its own right: the browser image cannot be dry-built with buildx at
  all (a container builder does not see the host docker store, so `--local-docker`
  builds it with `docker build` and the evidence records which driver produced it,
  with attestations read back out of the flags the builder was handed rather than
  restated from `--push`); the shared build script aborted on `set -u` *after* a
  successful buildx build, which would have failed every image on the first real
  release, and `test_release_build_script.py` now executes the script against a
  stubbed `docker` for all three driver paths instead of leaving that to a clean
  runner twenty minutes later; and the certification rounds disagreed with their
  own workflows about which tag the sandbox and proxy images carried, so one
  `CAP_CERT_IMAGE_TAG` now feeds the load list, the helm coordinates and the gates,
  with `test_certification_rounds_name_their_images_with_one_tag` refusing a
  literal that no job builds. Evidence gained `base_refs` resolved from the
  caller's `--build-arg` (a base that resolves to nothing fails the build instead
  of recording an empty list), and the publication gate requires it. Six more
  came from reading what the green rounds *published* rather than noting that they
  were green: the digest-pinned `values-release-<version>.yaml` was uploaded into
  the release assets and never attached to the GitHub Release (F-30); the observed
  image set K8S-GATE 34 writes landed under `backend/outputs/` while the generator
  read `outputs/`, because `CAP_CERT_OUT` is relative and the two processes start in
  different directories, so a passing round's own artifact still said `not_observed`
  (F-31); and that rendered file pinned five image coordinates while the chart reads
  six, leaving `worker.image` -- the deployment that runs acquisitions -- on the
  chart's placeholder registry, with a test that listed the same five paths and so
  agreed with the bug (F-32). The check now enumerates the coordinates out of
  `values.yaml` itself, and a second one evaluates the released file against
  `values.schema.json` itself rather than against a list written beside the test --
  which is also how the schema's own asymmetry got recorded: four coordinates accept
  a tag or a digest, `backend.image` and `worker.image` demand a tag, so a digest-only
  pin is unavailable for the API and the worker (F-41). `context_sha256`, added so that a staging change would show
  up, was hashing the absolute path of its own `mktemp -d` directory: one image's
  records disagreed between matrix cells at the same commit, and identical content
  could not be told apart from a different temporary directory (F-34). CI's browser
  cell builds `cap-sandbox-http` a second time as its base and uploaded that record
  under the released image's own file name, so two different builds of one image
  arrived indistinguishably (F-35); and `platform` was filled by the docker driver
  and left null by buildx -- including on the release's own push path -- so the
  platform a record claims no longer depends on which builder wrote it (F-36).
  A console test then reddened CI on a commit that touched no frontend file:
  `usePageList`'s error test read the antd toast with a synchronous `getByText`
  while the `App` message API mounts on a later tick, and `release.yml`'s first job
  is `ci.yml`, so that race sat between a tag and a release. It awaits the same text
  now, with the assertion's teeth checked by suppressing the toast and watching the
  test fail (F-38) -- and the full re-certification the classifier charged for a
  test-only change under `frontend/src/` is recorded as that rule working, not as
  an argument for an exception. A Kubernetes round then failed five gates on a
  *stale* `kubectl port-forward`: a service forward binds one endpoint, and a
  terminating pod answers `/health` once, so the restart gate declared recovery
  through a socket about to die and the next four gates inherited a dead tunnel on a
  healthy cluster. `_ensure_api` now wants two consecutive answers a second apart and
  a stale forward is rebound before each gate, with the single-answer form failing the
  new test as the control (F-40) -- a repair under `backend/tests/`, which the
  classifier charges as test harness, so it costs a re-run of that suite and not
  another two-hour soak. One last thing is stated rather than assumed: CAP
  images are not reproducible by digest (F-39). Two clean-runner builds of
  `cap-sandbox-http` with the same Dockerfile hash, the same staged-context hash and
  the same pinned base produced different digests, because nothing but build
  timestamps differs. The release contract pins the *published* index digest, so
  what an operator deploys is still exactly what was certified; "rebuild it and
  compare" is not a check that works today.

### Fixed

- **A green GA round no longer stands for GA certification (F-33).** `release.yml`'s
  `verify-certification` accepted any completed, successful run of
  `cap-ga-certification.yml` that carried the release job set — but every push to `main`
  runs that workflow with `CAP_GA_STRICT=0`, and in development mode a gate with no
  evidence in the job is `PLANNED` rather than failing, so nothing fails and the job is
  legitimately green while its own artifact says `mode: development`,
  `full_ga_certified: false`. The run object exposes no `inputs`, so the job colour and the
  dispatch could not be distinguished from inside the gate. The gate now reads the decision
  where the round wrote it: `ga-cert-artifacts` → `cap-cert-ga/cap-28.7-ga-certification.json`,
  requiring `mode: final-strict`, `full_ga_certified` taken exactly as stored (type-strict,
  never recomputed from junit or job colours), `commit` equal to the selected run's
  `head_sha`, zero `failed`/`not_run`/`skipped`/`planned`, and `passed == total` rather than
  a hardcoded count so a legitimate older round is not refused for its smaller gate table.
  Kubernetes is held to its own artifact's commit and counts. An absent, duplicated,
  expired, unparseable or internally ambiguous artifact refuses the release as a finding; a
  refused download is still `verdict: ERROR`, because "could not look" and "it says no" need
  different remedies — both block publication. A rejected artifact does not send the gate
  searching for an older ancestor. Fourteen new executed tests, plus a fifteenth that
  checks the live Actions-API shape and skips wherever `gh` cannot answer; nine
  guard-by-guard mutation
  controls (each caught by its own test and no others), and a read-only pass of 13
  artifacts from sealed and superseded rounds — including the development-mode soak run at
  the strict round's own SHA — every one read the way the round that produced it recorded.
- **Cited evidence must be readable from the clone (F-37, guarded; the finding stays
  open).** Five `provenance.evidence` strings in `deployment/third-party-images.json` name
  a path under gitignored `outputs/`, and nothing looked. `test_third_party_image_lock.py`
  now requires every cited path to have a tracked twin under `docs/quality/artifacts/`
  asserting the **same digests** — the dates and formatting of the two copies are
  explicitly not the claim, because the tree's two copies differ in exactly one date field
  and that is not a defect — and refuses any lock entry that has neither a pointer nor a
  justification named in the test's own table, with that table compared against the lock's
  entries so the convention cannot widen by silence. Five controls against the real data,
  each noticed. The pointers themselves still name the ignored path: rewriting them is a
  `deployment/` edit, which `classify_diff.py` charges as runtime-affecting, so the repoint
  rides the next round that re-certifies anyway.
- **A healthy long-running acquisition could be cancelled under load.** The execution-lease
  heartbeat in `WorkerRuntime` renews the lease while the sandbox operation runs, and it did so on
  the *same* `AsyncSession` as the main execute flow at every construction site that omitted
  `heartbeat_session_factory` — the architecture scan meant to prevent that exempted `tests/`
  outright, and the Phase 28.3 suite was one of those sites. Worse, any renewal failure other than
  a real ownership loss (SQLite's single-writer `database is locked` being the common one under
  contention) ended the heartbeat coroutine silently, so renewals stopped for the rest of the
  operation, the lease lapsed unrenewed, the fenced commit was correctly rejected, and the run was
  finalised `CANCELLED`. `WorkerRuntime` now derives a renewal-only session factory from its own
  bind when a site omits one — no construction site, in `app/` or `tests/`, can share the main
  session any more — and retries a transient renewal on the next tick while `WorkerLeaseConflict`
  still stops the heartbeat immediately, so fencing is unchanged: a stale worker can still neither
  renew nor commit. Found as `assert 'CANCELLED' == 'COMPLETE'` in the release layer of the Linux
  certification (run `35430453285`), where the same commit passed the same test in a sibling job;
  pinned by deterministic tests that were confirmed to fail against the pre-fix code.
  Two follow-on corrections came out of certifying it. The cadence itself — written
  `max(1.0, lease_ttl / 3)` at both call sites — broke the documented "renew three times per lease"
  rule for any TTL under ~3 s, which is the regime the certification harnesses run in, leaving a
  healthy operation a 1.33 s stall budget; it is now one named function (`ttl / 3`, never slower)
  whose production value at 120 s is bit-identical to before, with tests asserting both halves and
  the 0.15 s floor boundary. Then CI's own unit job caught the fix overreaching: deriving a
  renewal-only session from the runtime's bind is right wherever a second connection exists and
  wrong on a single-connection bind (`StaticPool`, the in-memory SQLite the test suite shares),
  where the "dedicated" session is the same connection the crawl is writing on and its `COMMIT`
  fails mid-transaction — `cannot commit transaction - SQL statements in progress`, run
  `35436793797`. Both places now ask one helper (`bind_serves_one_connection`) and renew through
  the operation's own session when there is nowhere else to renew; the heartbeat's first renewal is
  also anchored on the loop clock instead of `0.0`, which had made every first poll iteration renew
  unconditionally. Verified by the green CI unit job at `c52dcb9`.
- Three certification gates were corrected where they were wrong rather than strict. The Linux
  release layer asserted a control-plane `worker_control_plane_isolation` *verdict word* it does not
  own, so it failed run `35431391962` after 193 regression tests, the 500-run OCI benchmark, the
  100-run kill-9 HA gate and all three PostgreSQL matrix legs had passed; it now re-derives the
  per-path facts from `scripts/certification/generate_report.py`, refuses `PASS` while any shipped
  deployment path mounts a container-runtime socket, and treats a chart socket mount as a release
  blocker. That artifact also records the commit it certified, and the gate refuses evidence whose
  commit is missing, unknown, or not the one its own job checked out. And the "authoritative
  PostgreSQL" heartbeat-isolation variant read `DATABASE_URL` — which `backend/tests/conftest.py`
  pins to in-memory SQLite for every test process — so it had never reached PostgreSQL and died on
  `no such table: workers` in the strict GA job (run `35429972509`); it now reads a dedicated,
  shape-checked `CAP_PG_TEST_DSN`, wired into both GA workflows, fails loudly if that is set
  without a real server, and is verified against a real PostgreSQL 16.2.
- Half the console's API surface required the whole platform. `/acquisitions`,
  `/agents`, `/agent/*`, `/tasks`, `/workflow`, `/registry`, `/capabilities` and
  `/runtime` had no rule in the authorization map, so they fell through to
  `platform.manage` -- described in the RBAC catalog itself as "Operate *legacy*
  control-plane management APIs". Consequences: the shipped default deployment
  (console identity `read-only`) answered **403 `Permission required:
  platform.manage`** on the Acquisitions, Agents, Tasks, Workflow, Registry,
  Capabilities and Runtime pages, and the only operator "fix" available was to grant
  the console user `platform.manage` -- the escalation the map exists to prevent.
  K8S-GATE 33 found it by issuing the console's own request through the real nginx
  proxy with no identity headers of its own; no unit test had ever asked as
  `read-only`. The map now covers each resource with read/execute pairs
  (`acquisition.read|execute`, `agent.read|execute`, `task.read|write`,
  `workflow.read|execute`, `registry.read|write`, `capability.read`,
  `runtime.read|write`), SOC Analyst gains the analyst-plane actions it already
  performs, Incident Responder gains reads, and `Read Only` gains exactly the reads
  the console displays -- no writes. `backend/tests/test_rbac_permission_mapping.py`
  then pins the boundary so it cannot rot back: every advertised endpoint maps to a
  real permission, the `platform.manage` fallback is a reviewed allow-list rather
  than the default outcome for a route nobody thought about, and the shipped
  enforcement path is exercised as `read-only`, including the 401s for an unknown
  user and a wrong proxy secret.


- `npm run build` was broken by this audit's own new fixture. The build
  typechecks `src/**/*.test.ts` as well, and `http.test.ts` passed `{}` where
  axios requires an `InternalAxiosRequestConfig` (`headers` is not optional), so
  `tsc` failed with TS2345 before Vite ever ran. The fixture now carries a real
  `AxiosHeaders` config -- which is also closer to the object axios actually
  hands `errorMessage()` at runtime.
- Half-finished console surfaces closed in the remaining pages: five tables
  rendered their action column under an empty header (now `操作`); the Playbook
  detail card announced a "YAML 结构" document while rendering
  `JSON.stringify(...)` output (now labelled as the structured JSON it is); the
  ticket status filter carried an inline copy of the canonical status list
  (now `TICKET_STATUSES`, so it cannot drift); and the incident assignment
  dialog accepted a submission that changed no field at all -- with only the
  pre-filled operator it POSTed an empty partial assignment and reported
  success -- which is now refused with a message naming what to fill in. Dead
  client code (`domainPaths` / `getDomainRecords`) and the unused `useDetail`
  hook, including its dynamic `import("../api/http")` in a catch path, are gone.
- Asset search crashed on PostgreSQL. `AssetRepository.search` applied
  `statement.distinct()` unconditionally, so its count query became
  `SELECT DISTINCT` over every asset column -- and PostgreSQL has no equality
  operator for the `json` columns (`risk`, `capabilities`, `properties`), so
  `GET /assets` answered 500 with `UndefinedFunctionError`. The tag filter is
  now an id semi-join (which cannot duplicate rows: `asset_tags` has a unique
  `(asset_id, name)` constraint, so the `DISTINCT` was never de-duplicating
  anything) and the `DISTINCT` is gone. Found only by running the application
  against a real PostgreSQL server; the SQLite-based unit suite cannot see it.
- Test-suite reproducibility. `Settings` resolves `env_file=".env"` against the
  working directory, so on any machine that had followed the documented
  docker-compose step the test process inherited that file's proxy secret and
  database URL, and three shipped API tests failed with a bare 401. `tests/conftest.py`
  now pins the configuration before the app is imported.
- Test-suite reproducibility, root-caused. Pinning a list of keys was whack-a-mole,
  and two more leaked through it on this line: `CAP_ZAP_API_KEY` (a developer `.env`
  provisioned ZAP inside the incident-plane tests, so a credential-bearing run and a
  clean run tested different systems) and `APP_VERSION` (a `.env` left at the previous
  release made `/health` report `1.0.5` on a tree whose 16 version carriers all said
  `1.0.6-rc1`). `conftest.py` now clears `env_file` on the `Settings` class before any
  app module is imported, so the suite sees process environment plus declared defaults
  only -- which is exactly what a container gets in deployment -- and
  `test_settings_dotenv_hermeticity.py` proves it with a poisoned `.env` plus a
  negative control.
- Audit attribution for API callers. Rejected requests were recorded with
  `operator="api-user"`, and `Asset.deleted_by` was written as the same literal,
  so the trail that exists to answer "who did this" named a shared placeholder
  for every caller even though the middleware had already verified the
  principal. Request-level rejections now record the authenticated user and a
  deletion records who deleted it.
- CI image provenance: `.github/workflows/ci.yml` passed a hardcoded
  `build-args: VERSION=1.0.0-rc1` to both image builds, so every CI-built
  backend and frontend image embedded a version label five releases out of
  date, corrupting the OCI `org.opencontainers.image.version` annotation and
  any SBOM/upgrade attribution derived from it. The workflow now resolves the
  canonical `VERSION` file into a step output.
- Release-gate blind spot: the version-consistency gate enumerated 16 declared
  carriers but never looked at CI or Compose, which is why the literal above
  survived five releases. Adds a fail-closed guard rejecting any
  `VERSION=<literal>` build-arg in a layer that must derive the version,
  verified by a negative control (reintroducing the literal fails the gate).
- Console build configuration: `frontend/vite.config.js` was a stale compiled
  artifact committed alongside `vite.config.ts`. Vite resolves `.js` before
  `.ts`, so `npm run dev` loaded the stale file and silently discarded every
  build setting in the source config (route-splitting chunk hints and the
  size-warning limit). The artifact, its `.d.ts`, and the committed
  `*.tsbuildinfo` files are removed and now ignored.
- `make lint` ran `black --check .`, a formatter no gate enforces that fails on
  42 files, so the documented lint entry point was broken. It now runs exactly
  the gates CI runs (Ruff + ESLint), and every target resolves through
  `uv run --project backend` instead of assuming an activated virtualenv.
- Console list pages could not fail loudly: `usePageList` computed an `error`
  that none of its 19 call sites rendered, so a 500 or an unreachable backend
  rendered an empty table indistinguishable from a genuinely empty dataset.
- Console dead control: the Workers & Sandbox detail drawer set the selected row
  but never opened itself, so the sandbox execution detail feature was
  unreachable from the UI.
- Stuck detail drawers: Assets, Response and Incidents fetch the record on open
  but guarded their body on "no record yet", so after a failed GET the drawer
  showed "加载中…" forever and could leave a previously selected record rendered
  under a new header. They now clear on open and render loading / error-with-retry
  / content. (Detection, Assessment and Playbooks were checked and left alone:
  their drawers display the row the table already holds and issue no second
  fetch, so their empty body was not a stuck state.)
- Acquisition detail fan-out selected a run and then awaited its evidence and
  completeness in sequence without clearing the dependent state first, so a
  failed evidence request left the previous run's evidence rendered under the
  new run's header, and a slow response for an abandoned selection could land on
  top of a newer one. The regions now clear before fetching, only commit on
  success, and are guarded by a selection sequence number. The acquisition list
  and the Knowledge/notification/ticket tables gained the same error, loading and
  empty distinction as the other regions.
- Console shell views had no loading or error surface: `DashboardPage` returned
  `null` while the aggregate was in flight (a blank page that also hid failures
  behind the global banner), `SettingsPage` rendered a bare `Empty` that
  conflated loading, genuinely-empty and failed, and `AccessPage` rendered both
  directory tables with no `loading` prop at all. The shell now passes its
  `loading`/`error`/retry state down and each view renders all three states.
- Console truthfulness: the sidebar badge and page eyebrows displayed a
  hardcoded product version while the real one was already being fetched from
  `/health`; the Investigation view posted fabricated fixture records
  (invented event IDs, evidence references and fixed timestamps) to the live
  API as if they were operational data, and hardcoded a scenario count that the
  fetched result already provides.

- Object-store certification could not start. The MinIO vendor archived the
  open-source server and stopped serving community release files: `dl.min.io` returns
  HTTP 410 Gone and the image now fails on Docker Hub with `pull access denied` --
  verified working from GitHub runners on 2026-08-22 (`_ci_logs/cert_run8_full.log`)
  and failing on every certification job since 2026-09-13. The nightly soak, GA, Linux
  and K8s jobs therefore died before any gate ran, and `docker-compose.yml` shipped the
  same unreachable coordinate. Every reference now pulls the *identical* bytes
  (`RELEASE.2025-04-22T22-12-26Z`,
  `sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e`) from the
  official MinIO organisation on quay.io, pinned by digest because the tag belongs to a
  vendor that no longer maintains it. The GA bootstrap failed the same way silently:
  `curl` without `-f` wrote an HTML error page to `/usr/local/bin/mc`, so `mc` is now
  installed from the pinned GitHub release asset and verified against its published
  `sha256sum` before use.
- CI's compose gate regressed when `APP_VERSION` became a required carrier: the
  `packaging` job supplied the other eight required variables but not that one, so
  `docker compose config --quiet` failed on the candidate. It now resolves the value
  from the canonical `VERSION` file instead of hardcoding one.

- The release layer of the Linux certification could not start its infrastructure
  on a candidate branch: `CAP_CERT_PREFIX` was derived from `github.ref_name`, and a
  branch named `release/1.0.6-rc1` puts a slash into every container and network
  alias built from it, so `setup.sh` failed with `Invalid container name
  (capcert-rel-release/1.0.6-rc1-egress)` four minutes into the job -- after the
  images had built. The prefix now comes from `github.run_id`, and `setup.sh`
  sanitises whatever it is handed, so a manual run that exports a branch name is
  covered by the same rule.

- The documentation, `.env.example` and the compose file disagreed about a security
  boundary. `docs/known-issues.md` listed "docker.sock mounted in worker" as CLOSED
  in Phase 28.6 and said the worker never mounts a container-runtime socket;
  `.env.example` repeated that directly above `SANDBOX_PROVIDER=oci-sandbox`; and
  `docker-compose.yml` mounts `/var/run/docker.sock` into `acquisition-worker`,
  because that provider starts sandbox containers through the docker CLI. Both halves
  are now stated where each applies: the production chart worker mounts no control
  socket (asserted against every chart template, and on a live cluster by K8S-GATE 3),
  while the compose worker is host-root-equivalent by construction and compose is
  therefore the evaluation/single-node path -- said in the compose file at the mount,
  in `.env.example`, and in known-issues as a standing limitation.
- The Linux certification artifact derived `worker_control_plane_isolation` by
  substring-grepping compose and `.env.example`, so a comment about the socket could
  set a security verdict -- and it did. It now reads the compose service's volumes and
  the chart's mount lines, and reports the production and compose paths as separate
  facts instead of one PASS/NOT_CERTIFIED word.
- `.env.example` and the production checklist no longer describe Redis as a dependency
  of the request path. Nothing in `backend/app` imports a Redis client;
  `redis_configured` is `bool(settings.redis_url)`, and the telemetry coordinator
  documents that it has no broker dependency -- yet the checklist asked operators to
  support, secure, monitor and capacity-test Redis 7. The documents state what is true
  rather than the certification inventing a test for behaviour that does not exist.

- `backend/tests/test_rbac_permission_mapping.py` and
  `backend/tests/test_sandbox_socket_boundary.py`: the permission map, the
  least-privilege set of each role, and the runtime-socket boundary between the
  production chart and the compose evaluation path are now asserted in both
  directions, including negative controls for an unmapped future route.

## [1.0.5] - 2026-09-07

GA promotion of 1.0.5-rc1: identical artifacts, promoted version metadata only
(pure version bump; certification inherited from anchor `901013a` per the
fail-closed diff classifier).

### Fixed

- Worker lease-heartbeat teardown is now cooperative: a stop event plus an
  interruptible wait replaces the bare `heartbeat_task.cancel()`. The old cancel
  could land while a TTL renewal `UPDATE` was in flight, and the abandoned
  statement held the SQLite write lock in a zombie transaction until GC
  finalised it -- observed as a 22-33s "database is locked" stall in the release
  `UPDATE`. Release latency 22.27s -> ~62ms.
- Frontend image: `apk upgrade --no-cache libuuid` clears 7 HIGH util-linux CVEs
  from the base-layer drift.

### Added

- CI governance: coverage-matrix accountability gate (stdlib-only assertion plus
  backend job fail-closed); every unverified matrix cell must carry an
  accountability anchor.
- Real outbound probes for notification webhook/ticket delivery against a local
  asyncio acceptance server, clearing two coverage-matrix cells.

### Changed

- Frontend route-level code splitting: 15 pages behind `React.lazy` with the
  antd `manualChunks` pin removed. The 1213 kB monolith is gone and first-paint
  gzip drops 437 -> 243 kB (-44%).
- Tests: per-test file-backed SQLite (NullPool + WAL + timeout) isolates the 28.2
  worker-path/claim-loop suites from the shared StaticPool session.

Certified at anchor `901013a`: 7200s soak (480/480 healthy ticks, 0 HTTP errors,
0 downtime, 48 pod-kill recoveries) and strict GA 40/40 gates PASS,
`full_ga_certified=true`.

## [1.0.5-rc1] - 2026-09-07

Release candidate cut to re-earn runtime certification for the worker
lease-heartbeat race fix, the CI accountability gate, the frontend code
splitting and the util-linux CVE remediation.

## [1.0.4] - 2026-09-05

GA promotion of 1.0.4-rc1: identical artifacts, promoted version metadata only
(pure version bump; certification inherited from anchor `87d2409`).

### Added

- Console completion: the remaining 7 inline views (Dashboard, Investigations,
  Acquisitions, Approvals, Plugins, Access, Settings) are extracted into
  `pages/`, so `App.tsx` is a pure shell and all 16 views are components.
  Navigation semantics are unchanged.
- Docs: `docs/quality/coverage-matrix.md` governance matrix (20 capability rows
  x 5 verification layers with explicit known-limitation marking) and an
  expanded roadmap After-1.0.0 section.

### Changed

- CI: actions moved to Node 24 majors (checkout v6, setup-python v6, setup-node
  v6) across 6 workflows, clearing the Node 20 deprecation warnings.

Certified at anchor `87d2409`: full re-certification, 7200s soak, strict GA
40/40 gates PASS, `full_ga_certified=true`.

## [1.0.4-rc1] - 2026-09-05

Release candidate cut to re-earn runtime certification for the console view
extraction (frontend paths classify as `production_runtime`, so the v1.0.3
certification could not be inherited).

## [1.0.3] - 2026-09-04

GA promotion of 1.0.3-rc1. The shipped delta over rc1 is two
certification-infrastructure commits only (`2e4d0b1` GA wiring and report
fidelity, `4bc5169` heartbeat test fixtures), classified
`runtime_affecting=false` / `release_metadata_only=true`, so the certification
earned at `4bc5169` carries forward.

### Changed

- Frontend console refactor: `App.tsx` had grown to ~1500 lines holding every
  view and is now shell + navigation, with nine views extracted verbatim into
  `pages/` (Incidents, Assets, Assessment, Detection, Response, Playbooks,
  Knowledge, Workers, Audit). No backend, API, schema, migration or deployment
  change; the console calls the same `api/client.ts` surface as before.
- Three shared layers replace copy-pasted logic: `api/http.ts` (single axios
  instance plus an `errorMessage()` helper surfacing the backend `detail`
  field), `api/constants.tsx` (status/severity tags, `formatTime`) and
  `hooks/usePageList.ts` (server-side pagination). `types.ts` gains the typed
  API models, and `Finding` gains `created_at` / `updated_at` to match the
  backend `FindingRead` schema.

Certified at anchor `06b74b8` with the heartbeat invariant suite executing inside
CI: `ci.yml`, Linux and K8s certification green, strict GA 40/40 gates PASS,
`full_ga_certified=true`.

## [1.0.3-rc1] - 2026-09-03

Release candidate cut to re-earn runtime certification for the console refactor
(`frontend/src/**` classifies as `production_runtime`, so the v1.0.2
certification could not be inherited).

## [1.0.2] - 2026-09-01

GA promotion of 1.0.2-rc1: identical artifacts, promoted version metadata only
(pure version bump; no runtime changes since rc1). Certified at the rc1 anchor
`aa9008d` — 40/40 GA gates, 2h soak with availability 1.0.

### Fixed

- Egress proxy consumed only the CONNECT request line, leaving the request's
  remaining headers in the buffer to be piped upstream as tunnel payload.
  TLS through the proxy failed with `WRONG_VERSION_NUMBER` and HTTP origins
  answered `400 malformed HTTP request "Host: ..."`, so every real external
  acquisition returned zero bytes and terminated `BLOCKED`. The headers are
  now drained before the tunnel is established. Present identically in v1.0.0
  and v1.0.1; disclosed as a v1.0.1 known limitation and fixed here.

## [1.0.2-rc1] - 2026-09-01

Release candidate for the 1.0.2 line, cut to re-earn runtime certification for
the egress tunnel fix.

### Fixed

- Egress proxy consumed only the CONNECT request line, leaving the request's
  remaining headers in the buffer to be piped upstream as tunnel payload.
  TLS through the proxy failed with `WRONG_VERSION_NUMBER` and HTTP origins
  answered `400 malformed HTTP request "Host: ..."`, so every real external
  acquisition returned zero bytes and terminated `BLOCKED`. The headers are
  now drained before the tunnel is established. Present identically in v1.0.0
  and v1.0.1.
- Regression guards: raw-CONNECT tests against a local upstream, plus a
  `network`-marked test that fetches real public HTTPS origins through the
  proxy. The latter runs in the `ci.yml` suite that gates releases — the
  absence of any proxied-fetch test is why this defect survived two releases.

## [1.0.1] - 2026-08-31

GA promotion of 1.0.1-rc1: identical artifacts, promoted version metadata only (pure version bump; no runtime changes since rc1).

## [1.0.1-rc1] - 2026-08-29

Security defaults and capability disclosure patch. v1.0.0 is immutable: no
tag was moved, no image was overwritten, and no historical release was
modified.

### Security

- **Production sandbox admission is now capability-based and fails closed.**
  Admission is decided on the provider's declared capability set
  (`real_isolation` **and** `network` **and** `container|vm` **and**
  `resource`), never on the provider's name alone. Checking `real_isolation`
  by itself is a trap: `SubprocessSandboxProvider` truthfully reports
  `real_isolation = True` (it is a separate OS process) while providing no
  network, filesystem, container or resource isolation at all.
- **Unknown `SANDBOX_PROVIDER` values no longer fall back silently.** In
  v1.0.0 the provider-selection chain ended in an `else` branch that returned
  `MemorySandboxProvider` (zero isolation) for ANY unrecognised name, so a
  misspelled `kubernettes-sandbox` silently downgraded every execution to
  in-process. It now raises `SandboxPolicyViolation` and refuses to start
  rather than falling back to a weaker provider.
- **Production without egress enforcement fails fast.** When the selected
  provider declares network capability, `EGRESS_PROXY_URL` must be set; the
  startup error states the threat model precisely: the application-layer
  validator (`URLPolicyValidator`, layer 1) remains active, but
  defense-in-depth network enforcement is absent. A missing proxy is **not**
  described as "no SSRF protection".
- **The egress proxy is now part of worker readiness.** In production the
  health probe TCP-checks the proxy and fails readiness when it is
  unreachable. There is no direct-egress fallback: under a NetworkPolicy that
  denies all egress except the proxy, an absent proxy means acquisition fails
  loudly instead of leaking traffic.
- Development and test environments may still use a weak provider, but log a
  one-shot startup warning ("not approved for production isolation").

### Added

- Targeted security coverage for the isolation plane
  (`backend/tests/test_phase_28_8_*.py`). The uncovered branches were exactly
  the failure paths: denials, timeouts, cleanup-on-failure and fail-closed
  errors.
  - `app/response/service.py` 47.4% → 98%
  - `app/sandbox/oci_provider.py` 44.9% → 98%
  - `app/sandbox/egress_proxy.py` 65.0% → 95%
  - `app/sandbox/k8s_provider.py` 72.0% → 98%
  - `app/sandbox/production.py` (new) 100%
- `ADR-0037: Production Sandbox and Egress Defaults`.
- CI "Assert production chart defaults" step: the rendered Helm manifest must
  carry `SANDBOX_PROVIDER=kubernetes-sandbox`, an `EGRESS_PROXY_URL` ending in
  `-egress-proxy:8080`, the sandbox NetworkPolicy and a non-development
  `APP_ENVIRONMENT`.

### Fixed

- `KubernetesSandboxProvider` under-declared its capabilities: the Pod spec
  sets `resources.limits/requests` and runs in a container PID namespace, but
  `container`, `process` and `resource` were left at their defaults. A
  capability-based policy that trusted the under-declared provider would have
  rejected the only path Helm ships.
- Helm `worker.egressProxyUrl` defaulted to empty (direct egress). It now
  defaults to the chart's own egress-proxy Service.
- `docker-compose.yml` shipped `APP_ENVIRONMENT=production` for backend and
  acquisition-worker — a local build stack with MinIO development credentials
  that would now trigger the production gates. Default is `development`.
- Zeek TSV ingest produced an unusable error. It now names the supported
  format and the remediation (`LogAscii::use_json=T`, or convert to JSONL
  before ingest) and returns both in `details`.

### Changed

- Version aligned to `1.0.1-rc1` across all 16 version carriers (canonical
  `VERSION`, backend pyproject/uv.lock (PEP 440 `1.0.1rc1`), frontend
  package.json/package-lock, sdk, Helm chart version/appVersion/image tags,
  Dockerfile `ARG VERSION`, runtime `app_version` / `__version__`,
  test_phase_23 `RC_VERSION`).
- Helm chart `artifacthub.io/prerelease` annotation set to `"true"`.
- `GA-GATE 1` version assertion is now rc-policy-generic (any `-rc` version)
  instead of hardcoding the `1.0.0-rc` prefix, so the 40-gate certification
  suite stays reusable for this release line.

### Notes

- **This rc is not yet certified.** 1.0.1-rc1 has not run the 40-gate GA
  certification; the release blocker is recorded in `docs/known-issues.md`.
  The v1.0.0 images and tag are untouched.
- Capability disclosure: the EDR / WAF / Firewall response plugins remain
  `mock_only` (enforced by a model validator), and their action inventory, the
  Zeek JSONL-only limitation and the reserved provider interfaces are now
  documented explicitly rather than implied.

## [1.0.0-rc4] - 2026-08-28

Security re-certification anchor for the v1.0.0 GA release.

### Security

- **CVE-2026-14456** (openssl, HIGH — unbounded QUIC memory growth DoS):
  `frontend/Dockerfile` runtime stage now runs
  `apk upgrade --no-cache libssl3 libcrypto3`, upgrading openssl
  `3.5.7-r0` → fixed `3.5.8-r0` in place at build time. The fixed library
  landed in alpine 3.24 on 2026-08-25, after every published
  `nginx:*-alpine` build, so no base-image tag bump could clear it.

### Fixed

- Removed unused `import pytest` (ruff F401) in
  `backend/tests/test_release_version_consistency.py`, which blocked the
  release workflow quality-gates job.

### Changed

- Version aligned to `1.0.0-rc4` across all 16 version carriers (VERSION,
  backend pyproject/uv.lock, frontend package.json/package-lock, sdk, Helm
  chart version/appVersion/image tags, Dockerfile `ARG VERSION`, runtime
  `app_version` / `__version__`, test_phase_23 `RC_VERSION`).
- Helm chart `artifacthub.io/prerelease` annotation set back to `"true"`
  (this is a pre-release).

### Notes

- The Dockerfile `RUN` fix is runtime-affecting under the fail-closed diff
  classifier, so runtime certification is re-earned on THIS commit (40-gate
  GA certification) rather than inherited from the rc3 anchor `10369e7`.
  The eventual 1.0.0 GA commit will be a pure release-metadata bump from
  this anchor (certification INHERITED).

## [1.0.0] - 2026-08-28

General Availability. Phase 28.7 GA Reliability Certification: **40/40 gates
PASS** under `CAP_GA_STRICT=1`. Runtime certification anchored at commit
`b22b7be57f89cd0ef0cf9df8b289ec1f5e74b2b3` (v1.0.0-rc4, the security
re-certification anchor carrying the CVE-2026-14456 openssl fix) and inherited
by this release (post-cert diff is release-metadata-only, verified by the
automated fail-closed diff classifier).

### Added

- Whole-cluster disaster-recovery certification: real `kind delete cluster`
  destruction, fresh-cluster restore with fail-closed manifest verification.
  Measured RPO = 9.76 s, RTO = 236.75 s.
- 2-hour soak certification (480/480 healthy ticks, 0 HTTP errors, 11
  controlled worker pod kills with only expected crash-recovery reclaims,
  availability 1.0, stable RSS).
- 9-cell capacity matrix and overload/backpressure gates.
- Automated release diff classifier (`scripts/release/classify_diff.py`) and
  release version-consistency gate
  (`backend/tests/test_release_version_consistency.py`).
- GA release notes (`docs/releases/v1.0.0.md`).

### Changed

- Version aligned to `1.0.0` across VERSION, backend pyproject/uv.lock,
  frontend package.json/package-lock, sdk, Helm chart
  (version/appVersion/image tags), Dockerfile `ARG VERSION`, and the runtime
  reported `app_version` / `__version__`.
- Helm chart `artifacthub.io/prerelease` annotation flipped `true` → `false`.
- Corrected the pre-existing `backend/app/__init__.py` `__version__` drift
  (`0.1.0` → `1.0.0`).

### Fixed

- D1 fact conflict resolved: the earlier "per-run lease lacks renewal"
  limitation was fact-checked and found FALSE. The production K8s path renews
  the acquisition run-claim lease every `lease_ttl/3` on a dedicated session
  with fencing. D1 removed from known limitations.

### Security

- **CVE-2026-14456** (openssl, HIGH — unbounded QUIC memory growth DoS)
  fixed: the frontend runtime image upgrades `libssl3`/`libcrypto3`
  `3.5.7-r0` → `3.5.8-r0` in place at build time (carried in from the
  v1.0.0-rc4 re-certification anchor).
- Worker never mounts a container-runtime socket; sandbox execution uses the
  Kubernetes API with namespaced RBAC (Phase 28.6 closure carried into GA).
- Trivy image scans report 0 blocking HIGH/CRITICAL; SBOM (SPDX + CycloneDX)
  and provenance generated for release images.

### Known limitations

- 24-hour soak not yet executed (2-hour soak is the certified baseline).
- SLO candidates are derived, not enforced (require ~30 days production data).
- Cancel vs. terminal-state race (Low severity; cancel API idempotent for
  terminal runs).

## [1.0.0-rc1] - 2026-08-05

### Added

- Release-candidate version policy, release notes, known issues, roadmap, API freeze, security policy, contributor and conduct policies.
- GitHub Actions quality, coverage, build, image, Helm packaging, and baseline security scanning workflows.
- Helm application chart with rolling Deployments, startup/readiness/liveness probes, migration hook, resource controls, PDBs, and external Secret references.
- Single-node, Compose, production checklist, upgrade, rollback, backup/restore, operations, runbook, API, SDK, plugin, v1 documentation index, and FAQ documentation.

### Changed

- Backend, Frontend, SDK, image metadata, and Chart versions aligned to `1.0.0-rc1`.
- Docker builds use multi-stage images; runtime containers are non-root where supported and expose health checks.
- Compose startup order is health-gated and production credentials are mandatory.
- Production configuration rejects repository placeholder secrets and debug mode.

### Security

- Production API documentation defaults off in release deployment assets.
- No Kubernetes Secret values are embedded in the Helm Chart.

### Known limitations

See `docs/known-issues.md`. The API high-concurrency latency budget and environment-gated production tests remain open.