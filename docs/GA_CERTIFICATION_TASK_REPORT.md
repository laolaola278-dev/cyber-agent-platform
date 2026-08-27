# CAP Phase 28.7 — GA Certification Task Report

- **Project:** `laolaola278-dev/cyber-agent-platform` (repo alias `cap`)
- **Phase:** 28.7 Tier 2 reliability / GA gate certification
- **Target commit (certified):** `10369e75af7e` ("r19")
- **Version:** `1.0.0-rc3` (rc3 preserved; **no v1.0.0 tag created**)
- **Mode:** `final-strict` (`CAP_GA_STRICT=1`)
- **Decision:** **FULL GA CERTIFIED** → `full_ga_certified: true`
- **Gates:** 40/40 PASS (38 implemented + 2 supply-chain meta, 0 PLANNED, 0 FAIL, 0 NOT_RUN, 0 skipped)

> Output message (strict): `CAP v1.0.0 GA READY — awaiting explicit release authorization.`

---

## 1. Objective

Drive the CAP Phase 28.7 Tier 2 reliability suite to a **strict (final) GA certification**
across two CI workflows:

- `cap-ga-certification.yml` — the `ga-certification` job (40-minute gate suite).
- `cap-ga-reliability.yml` — the `reliability` job (2-hour soak + nightly/dispatch evidence).

Strict semantics: `CAP_GA_STRICT=1` treats any **PLANNED** gate as a failure and requires
**40/40** gates PASS before emitting `full_ga_certified=true`. A passing commit must carry
**both** the GA suite JUnit and the reliability (soak + capacity) evidence for a single,
reproducible merge + local generator run.

---

## 2. Final outcome

| Metric | Value |
|---|---|
| total gates | 40 |
| passed | 40 |
| failed | 0 |
| not_run | 0 |
| skipped | 0 (GATE 40 critical-skip gate = 0) |
| planned | 0 |
| full_ga_certified | **true** |

### Evidence sources merged for the final generator run (all on F: disk)

| Artifact bundle | Run (commit) | Evidence used |
|---|---|---|
| `ga-cert-artifacts` | GA r19 (`32962945566`, `10369e7`) | `junit-ga.xml`, `junit-supply-chain.xml`, `skip-report.json`, supply-chain SBOM/SBOM/trivy/security-recert |
| `reliability-evidence` | soak r19 (`32962961395`, `10369e7`) | `junit-reliability.xml`, `soak-context.json`, `pf-*.log`, `capacity.json`, `backpressure.json` |

Merge procedure:
1. `gh run download 32962945566 -n ga-cert-artifacts -D ga_art`
2. `gh run download 32962961395 -n reliability-evidence -D rel_art`
3. Stage into `outputs/cap-cert-ga/` (`junit-ga.xml`, `junit-reliability.xml`, `junit-supply-chain.xml`, `skip-report.json`, `security-recert.json`, `slo-candidates.json`, `capacity.json`, `backpressure.json`, etc.)
4. `CAP_GA_STRICT=1 python scripts/certification/generate_report_28_7.py`

---

## 3. Gate architecture (40 gates)

The 40 gates are partitioned by tier of evidence:

| Tier | Gates | Evidence location |
|---|---|---|
| Pre-gate (Phase 28.6 baseline) | 1–6 | local heartbeat + pg isolation fixture |
| DR recovery | 7–13 | `outputs/ga-dr/backup/` + object-manifest |
| Reconciliation / control plane | 14–19 | `junit-ga.xml` |
| Cluster lifecycle / Helm | 18–19 | `junit-ga.xml` |
| **Capacity envelope** | **27** | `test_phase_28_7_ga_tier2_capacity.py` → `capacity.json` |
| **Overload backpressure** | **28** | `test_phase_28_7_ga_tier2_capacity.py` → `backpressure.json` |
| Soak / leak / orphan / upgrade+rollback | 24, 25, 26 | reliability soak → `soak-context.json` |
| SLO candidates / promtool alerts / runbook | 30, 31, 32 | `test_phase_28_7_ga_tier2_ops.py` |
| Resilience (PG leak / object-store / DNS / egress) | 36–39 | `test_phase_28_7_ga_tier2_resilience.py` |
| Supply-chain (image digests / SBOM / Trivy / provenance / SLI) | 20, 21, 22, 23, 29 | supply-chain job + `junit-supply-chain.xml` |
| Security re-cert (Phase 28.5 Linux cert) | 33 | `security-recert.json` (SHA-pinned) |
| Skip-report meta | 40 | merged-junit skip scan (must be 0) |

> Gates 27/28/30/31/32 are Tier 2 reliability/ops gates whose tests live in
> `backend/tests/test_phase_28_7_ga_tier2_capacity.py` and
> `backend/tests/test_phase_28_7_ga_tier2_ops.py`. Gates 34/35 are soak sub-gates
> exercised by the reliability soak job.

---

## 4. Round-by-round journey (commit series r12 → r19)

| Round | Commit | Change | Result |
|---|---|---|---|
| r12 `^5b80491` | `GATE 39` root-cause + hardening (wait for proxy pod termination; endpoint + canary assertions; canary stdout to `canary-gate39.log`) | 36/38/39 PASS; 39 red |
| r13 `^8da288d` | `_pf_api()` per-port single instance + stderr→`outputs/ga-dr/pf-<port>.log`; fast-fail on pf death | fixes GATE 37 port-forward flake |
| r13-buggy | — | unclosed `FileIO` → `PytestUnraisableWarning` cascade; strict mode red | r13 GA false-red |
| r14 `^ee248c6` | close parent-side file handle | **pytest 32/32 pass; generator failed=0**; job red = `continue-on-error` placed inside `with:` (GitHub rejects step attr in step inputs) → missing `reliability-evidence` failed the download step |
| r15 `^89d9147` | move `continue-on-error` to step level on both artifact downloads | first manual soak dispatch |
| r16 `^3707bdf` | reliability workflow: `mkdir -p outputs/ga-dr` before writing `pf-*.log` (DirNotFoundError) | soak green-start |
| r16-ga flake | — | new flake: `test_pregate_d_stale_owner_*` sqlite `database is locked` (Rollback-Journal two-writer deadlock; `busy_timeout` ineffective) | r16 GA 1/32 |
| r17 `^d61c8ae` | pregate sqlite fixture: `PRAGMA journal_mode=WAL` + `busy_timeout` | local 4/4 pass; GA red cleared |
| **r17 green** | — | GA run `32944138046` ✅ + soak `32944245832` ✅ | strict merge: passed 30→35, PLANNED 10→5 (27/28/30/31/32) |
| r18 `^4ebcdbe` | generator: add `TEST_GATES` mappings for 30/31/32; workflow: add `capacity.py` to GA pytest list | dev-mode `implemented=38 passed=38 planned=0` |
| r18-ga red | — | 27/28 first CI run: both fail — `assert 202 in (200, 201)`; async create contract is **202 Accepted (QUEUED)** |
| **r19 `^10369e7`** | capacity gates: accept `202` (`(200,201,202)` for create assertion; `rc in (200,201,202)` for overload accept) | **GA `32962945566` ✅ + soak `32962961395` ✅ → strict 40/40 PASS** |

**Key insight — 27/28 were wiring/omission bugs, not product defects:** the test file
existed and was correct for a *local* cluster; it was never (1) listed in the GA pytest
matrix, and (2) its create-status assertion used `200/201` instead of the async `202`
contract used everywhere else in the suite.

---

## 5. Methodology notes

- **Strict = honesty**: `CAP_GA_STRICT=1` makes PLANNED a hard FAIL and runs every
  suite with `SKIP==FAIL`. The generator never fabricates a PASS — missing evidence
  surfaces as `NOT_RUN` / `NOT_EXECUTED` rather than green.
- **Same-commit proof**: strict certification requires the GA suite and the 2-hour
  reliability soak to run against the *same* commit (`10369e7`). Stale soak runs on
  earlier commits were cancelled to avoid wasting ~2 h of kind-cluster time.
- **Port-forward lifecycle**: every test using port forwarding goes through a single
  `_pf_api()` instance per local port (terminate old before new bind); failures are
  fast with `pf-<port>.log` captured.
- **`gh run watch` is unreliable** from this sandbox (TLS `unexpected EOF` to
  `api.github.com`); a retry+backoff polling loop (`gh run view --json
  status,conclusion`) is used instead.

---

## 6. Known caveats (transparent, non-blocking)

1. **`soak`/`capacity` evidence blocks read as `NOT_EXECUTED`.**
   The generator's `_evidence()` reads `outputs/cap-cert-ga/{soak,capacity}.json`,
   while the CI workflow writes those Tier-2 artifacts under `outputs/ga-dr/`
   (e.g. `ga-dr/soak-context.json`, `ga-dr/capacity.json`, `ga-dr/backpressure.json`).
   These fields are **descriptive only** — they do **not** affect gate PASS/FAIL.
   Gates 24, 27, 28 are proven PASS via the merged **JUnit** (`junit-ga.xml` +
   `junit-reliability.xml`), which is what the strict meta-gate inspects.
   *Optional follow-up*: add a one-line staging copy in the workflow so the
   evidence blocks resolve to `executed: true`.

2. **Capacity matrix is load-bearing on runner CPU.** GATE 27 drives 6 cells
   (1×100 … 4×1000) × (10% real executions + 90% paginated traffic) plus a worker
   `scale`+`rollout status` per cell; GA job wall-time grew accordingly (~20–30 min
   for the capacity module alone). Tolerable, noted for future time-boxing.

3. **`ResourceWarning` "subprocess still running"** on teardown is benign here
   (orphaned kind/kubectl procs on GH runners); pytest exit code is driven by test
   results, not by the teardown warnings.

---

## 7. Deliverables

| Path (F: disk) | Description |
|---|---|
| `cap/outputs/cap-cert-ga/cap-28.7-ga-certification.json` | Machine-readable final strict report (`full_ga_certified: true`) |
| `cap/outputs/cap-cert-ga/ CAP Phase 28.7 - GA Certification Gates.md` | Human-readable gate table (40/40 PASS) |
| `cap/outputs/cap-cert-ga/junit-ga.xml` | GA suite JUnit (merged) |
| `cap/outputs/cap-cert-ga/junit-reliability.xml` | Soak JUnit |
| `cap/outputs/cap-cert-ga/junit-supply-chain.xml` | Supply-chain gate JUnit |
| `cap/docs/GA_CERTIFICATION_TASK_REPORT.md` | This report |

---

## 8. Cleanup

- Temporary merged-evidence download directory `_tmp_pytest/final_cert20/` has been
  moved to `cap/_待删_回收区/final_cert20/` and awaits your confirmation prior to
  permanent deletion (nothing deleted yet).
- No scratch scripts or throwaway files remain in the repo working tree.

---

## 9. Next steps

- **Release decision is pending your explicit authorization.** No tag was cut;
  `VERSION` remains `1.0.0-rc3`.
- On authorization, the release path is: bump `VERSION` → create `v1.0.0` tag →
  GitHub Release (artifacts + SBOM + attestation).
- *Optional*: wire the Tier-2 evidence JSON blocks (soak/capacity) so the report's
  descriptive sections resolve to `executed: true` for completeness.
