# CAP Phase 28.3 — Production Runtime Closure Report

Date: 2026-08-13
Base: Phase 28.2 (durable queue / atomic claim / fencing / durable cancellation / checkpoint resume)
Version target: 1.0.0-rc1 → production closure candidate
DB certification: real PostgreSQL 12.12 (isolated cluster, 127.0.0.1:55432, user `cap`, trust auth)

---

## 1. Executive Summary

Phase 28.3 closed the gap between Phase 28.2's durable-execution components and a
startable, migratable, crash-recoverable production Worker Runtime on PostgreSQL.
Four production blockers were confirmed and resolved:

- **A. Recovery gap closed** — the claim loop now discovers expired RUNNING runs and
  atomically reclaims them (new fencing epoch, persistent `recovery_count`, checkpoint
  preserved). The reclaim CAS was hardened with a **lease-subquery recheck** so two
  recovering workers can never both win (PG READ COMMITTED re-evaluates on lock wait).
- **B. Alembic migration** — revision `20260812_0021` creates all 7 acquisition tables
  (FKs, indexes, unique `idempotency_key`); verified on PG: fresh upgrade → downgrade →
  re-upgrade, Phase 16/17 worker tables preserved.
- **C. Production worker daemon** — `python -m app.acquisition.worker_main` registers,
  declares `acquisition.http`, polls the durable queue, drains, and closes resources;
  `docker-compose.yml` gained an `acquisition-worker` service.
- **D. PostgreSQL certification** — atomic claim / recovery / fencing / cancellation /
  concurrent idempotency / three races all pass on real PG (8/8), plus a 500-run
  durability benchmark (500/500 terminal, zero loss).

Two **additional production defects** were found and fixed during the critical
side-effect audit (see §11): the run-level fencing gate could be escaped, and the
execution runtime shared a session with the service — which would have let a stale
worker's evidence rows commit through the sandbox commit, bypassing the fenced final
commit.

Additionally: execution-time lease heartbeat added (healthy long operations are never
falsely reclaimed), `AcquisitionStatus` unified with durable DB states, `create()`
made concurrency-safe for idempotency on PG, and a pre-existing PG blocker (unquoted
`offset` column in migration `20260801_0013` + ORM) was fixed.

## 2. Files Changed

| File | Change |
|---|---|
| `app/acquisition/claim.py` | Hardened `reclaim_expired` CAS (lease-subquery recheck); added `renew()` (fencing-gated heartbeat); reclaim only EXPIRED (never RELEASED/ACTIVE) leases |
| `app/acquisition/claim_loop.py` | `tick()` now runs recovery: `_next_recoverable` (EXPIRED leases only) + `_recover_and_run`; `populate_existing` on scans; batch capped by available execution slots |
| `app/acquisition/worker_path.py` | Execution-time lease heartbeat (`_renew_lease`, interval = ttl/3, fencing-gated); `_finalize_cancelled_if_safe` ownership guard; Critical Gate `verify_owner` wrapped (no escape); cancel-aware loop cancels operation on lost ownership |
| `app/acquisition/worker_main.py` | **New** production daemon entrypoint (register → capability → loop → drain → dispose); `runtime_session` isolated from service session |
| `app/worker/runtime.py` | Execution-lease heartbeat (`_heartbeat_lease`, cancelled in `finally`); `commit_result` fences against the live lease (not the stale execution-row snapshot) |
| `app/repositories/worker.py` | `commit_result` no longer requires the execution row's start-time `lease_version` (legitimately diverges after renewal) |
| `app/acquisition/service.py` | `create()` concurrent idempotency (IntegrityError → rollback → re-read → existing or 409); agent/task FK targets ensured (PG) |
| `app/acquisition/models.py` | `AcquisitionStatus` + `QUEUED` / `CANCEL_REQUESTED` / `CANCELLED` |
| `app/models/telemetry.py`, `alembic/versions/20260801_0013_*.py` | Quoted `offset` (PG reserved word) — pre-existing blocker for `upgrade head` on PG |
| `alembic/versions/20260812_0021_acquisition_durable_runtime.py` | **New** migration: 7 acquisition tables |
| `app/telemetry/runtime.py` | Lazy `PluginWorkerRuntime` import (pre-existing circular import) |
| `docker-compose.yml` | `acquisition-worker` service (same PG, separate process) |
| `pyproject.toml` | Registered `postgres` pytest marker |

## 3. Recovery Architecture

`AcquisitionWorkerLoop.tick()` flow:
1. `_next_batch()`: claimable `QUEUED`/`CANCEL_REQUESTED` runs, capped by remaining
   execution slots (registry `max_concurrency - active_executions`).
2. `_next_recoverable()`: `RUNNING`/`PARTIAL` runs whose bound lease is **EXPIRED**
   (never RELEASED = finished normally, never ACTIVE = still executing).
3. `_recover_and_run()`: `coordinator.reclaim_expired(run_id, worker_id, token)`
   — the CAS is conditional on the bound lease NOT being ACTIVE/RELEASED at UPDATE
   time (lease-subquery recheck). On PG READ COMMITTED this re-evaluates after a lock
   wait, so exactly one concurrent recovery winner exists.
4. On success: `recovery_count += 1`, new fencing token, `claimed_at` refreshed,
   checkpoint/page cursor untouched, runner executes with the new token.

Evidence: `backend/tests/test_phase_28_3_recovery_loop.py` (10/10) + PG
`test_automatic_recovery_increments_recovery_count` + `test_concurrent_create_idempotency_single_row` (see §9).

## 4. Lease Heartbeat Design

- **Run lease** (`AcquisitionClaimCoordinator.renew`): `verify_owner` (run owner +
  token hash) then lease version/token CAS renewal — a stale owner cannot renew.
- **Execution lease** (`WorkerRuntime._heartbeat_lease`): renews the sandbox
  execution lease every `ttl/3` while the operation runs; cancelled in `execute`'s
  `finally` (no background-task leak). `commit_result` fences on `expires_at > now`,
  so an unrenewed lease correctly rejects the commit.
- **Worker-path heartbeat** (`_renew_lease` inside the cancel-aware poll loop):
  renews the run lease while `run_agent_operation` executes; stops on completion /
  cancellation / lost ownership (the loop cancels the operation and rolls back).
- Interval = `lease_ttl_seconds / 3` (configurable, no hard-coded test constant).

Evidence: `backend/tests/test_phase_28_3_lease_heartbeat.py` (5/5): long op > TTL
survives, `expires_at` advances, stale token rejected, heartbeat stops after
completion, no renewal → reclaim succeeds.

## 5. Worker Daemon Architecture

`python -m app.acquisition.worker_main` (module entrypoint):
Settings → async engine/session factory → `_ensure_schema` (fails fast with an
alembic hint; never `create_all`) → register worker → `WorkerRegistry` /
`WorkerLeaseManager` / `WorkerScheduler` / `SandboxRuntime(MemorySandboxProvider,
SandboxPolicyEngine)` / `PluginWorkerRuntime` / `AcquisitionService` /
`AcquisitionClaimCoordinator` / `AcquisitionWorkerPath` / `AcquisitionWorkerLoop`
→ `run_forever()` → graceful stop (drains, `ACQ_RUN_SECONDS` test hook on Windows
where `add_signal_handler` is unavailable) → runtime/DB resource closure.

- API only enqueues (`POST /acquisitions` → 202 QUEUED); no `create_task`, no
  sync execution, no in-process singleton dependency.
- The runtime uses a **separate session** from the service/evidence session (§11).

Evidence: `backend/tests/test_phase_28_3_worker_daemon.py` (2/2, real subprocess):
start → register → poll → graceful stop → engine disposed; and refuses to run on an
un-migrated database.

## 6. Alembic Migration

`alembic/versions/20260812_0021_acquisition_durable_runtime.py` (down_revision
`20260808_0020`) creates: `acquisition_runs`, `acquisition_plans`, `acquisition_steps`,
`acquisition_artifacts`, `extracted_documents`, `completeness_reports`,
`public_endpoint_candidates` — with all columns required by the ORM
(`idempotency_key` UNIQUE, `request_fingerprint`, `checkpoint` JSON, `worker_id`,
`lease_id`, `sandbox_execution_id`, `worker_execution_id`, `claim_token_hash`,
`claim_attempts`, `claimed_at`, `recovery_count`, `cancel_requested_at`,
`cancelled_at`, `stale_result_rejected`, …) and correct FKs (tasks, agents, evidence,
acquisition_runs).

Pre-existing blocker fixed: migration `20260801_0013` used an unquoted `offset`
column (PG reserved word), which made `upgrade head` non-runnable on PG. Both the
migration and `app/models/telemetry.py` now quote it.

Evidence: `backend/tests/test_phase_28_3_migration.py` (3/3, real PG throwaway DBs):
fresh `upgrade head` → 7 tables + expected columns/indexes; `idempotency_key` UNIQUE
enforced by the DB (second insert with the same key raises); `downgrade
20260808_0020` removes acquisition objects while `workers` / `worker_leases` /
`sandbox_executions` survive.

## 7. PostgreSQL Certification

Real, un-mocked PostgreSQL 12.12 (isolated cluster, trust auth, port 55432; DBs
`cap283`, `cap283_migrate`). Marked `@pytest.mark.postgres`; skipped when unreachable.

## 8. Atomic Claim Results

PG `test_atomic_claim_single_winner`: 10 independent connections claim one QUEUED
run → **exactly 1 winner**; `claim_attempts == 1`; winner's worker_id recorded.
`test_concurrent_create_idempotency_single_row`: 10 concurrent `create()` with the
same idempotency_key → **exactly one row**, one `created=True`. This test also
validated the `create()` fix: on PG the loser of the unique-key race now rolls back,
re-reads, and returns the winner's run (or raises an explicit `AcquisitionConflict`)
— no leaked IntegrityError.

## 9. Crash Recovery Results

- PG `test_automatic_recovery_increments_recovery_count`: A claims (TTL 5s) → lease
  expired → B's loop reclaims → `recovery_count == 1`, B owns the run, runner executed.
- PG `test_reclaim_vs_complete_race` / `test_lease_renewal_vs_reclaim_race`: terminal
  state always recorded; if renewal wins, A stays owner with an ACTIVE lease;
  otherwise ownership moves to B.
- SQLite `test_phase_28_3_recovery_loop.py` (10/10): expired RUNNING auto-reclaimed;
  ACTIVE never; checkpoint + page cursor preserved; repeated tick no double
  execution; terminal runs never reclaimed; CANCEL_REQUESTED + expired lease handled;
  draining worker skips new recovery; recovered owner can commit; stale worker's
  commit rejected.

## 10. Fencing Results

- `test_phase_28_2_claim_fencing.py` (regression, green) + PG
  `test_stale_commit_rejected_after_reclaim`: after B reclaims, A's `verify_owner`
  raises `AcquisitionStaleCommit`, `stale_result_rejected` increments, and the run
  still belongs to B.
- `test_stale_token_cannot_renew`: a stale fencing token cannot renew the lease.
- No plaintext fencing token appears in any log (audited).

## 11. Side-effect Fencing Audit (Critical)

Audit findings (code-verified, not assumed):

1. Evidence/artifact rows are written into the **worker session** (flush, uncommitted)
   during the operation and become durable ONLY through the fenced final commit
   (`verify_owner` then `commit()`). Blob bytes are written to disk immediately
   (content-addressed; orphan-capable, never attached without a fenced artifact row).
2. **Defect found & fixed (a)**: the run-level Critical Gate (`verify_owner` after the
   sandbox commit) sat OUTSIDE the try/except — a lost-ownership `AcquisitionStaleCommit`
   escaped `run_claimed`. Now handled: rollback + clean "ownership lost" payload.
3. **Defect found & fixed (b)**: `WorkerRuntime` shared the service/evidence session, so
   `commit_result`'s commit would have committed the operation's evidence/artifact rows,
   **bypassing the run-level fencing gate**. The daemon and all 28.3 helpers now give
   the runtime a dedicated session; a stale rejection rolls the service session back,
   so no stale evidence row survives.
4. Cancel during object write: the blob may exist as a content-addressed orphan, but no
   artifact row is attached after cancellation (test 2); an orphan GC is a known
   limitation (below).

Evidence: `backend/tests/test_phase_28_3_side_effect_fencing.py` (3/3): stale worker's
intermediate artifacts cannot attach; cancel during object write → no post-CANCELLED
attachment; stale worker leaves no orphan evidence row (fresh-connection assert).

## 12. Cancellation Results

- PG `test_durable_cancellation`: API session flips RUNNING → CANCEL_REQUESTED; the
  worker's cancel-aware execution observes the durable flag and finalizes CANCELLED.
- SQLite `test_phase_28_2_cancellation.py` (regression, 8/8) + `_cancel_via_api`
  retry hardened for `PendingRollbackError` (SQLite single-writer contention).
- `test_cancel_requested_with_expired_lease`: CANCEL_REQUESTED + expired lease →
  finalized without starting network work.
- `test_cancel_vs_complete_race`: the run always ends terminal, never stuck.

## 13. Process Isolation Results

`backend/tests/test_phase_28_3_process_isolation.py` (2/2, real subprocess):
- Process A (test process) enqueues → QUEUED; Process B (`python -m
  app.acquisition.worker_main`) claims, executes, finalizes (BLOCKED for a private
  URL — no external network needed). Asserts terminal state + `worker_id` set +
  `claim_attempts >= 1`.
- Process A cancels (durable flag); Process B observes and finalizes CANCELLED with
  zero network requests. Shared state is PostgreSQL only.

## 14. Graceful Shutdown Results

`test_phase_28_3_worker_daemon.py::test_daemon_starts_polls_and_gracefully_stops`:
daemon logs registration → polling → auto-shutdown → engine disposed, exit code 0.
`test_draining_worker_skips_recovery`: a draining loop starts no new recovery.
`test_repeated_tick_no_double_execution`: no duplicate terminal commit on repeated
ticks. Lease heartbeat is cancelled in `execute`'s `finally` — no task leak; a dead
worker's lease expires and is reclaimed (test 5 of the heartbeat suite).

## 15. Security Regression

`test_phase_28_2_security_regression.py` + `test_phase_28_2_legacy_architecture.py`
green (SSRF / URL policy / no sync-execution / no create-and-run / no plaintext token
/ no bypass endpoint). The worker daemon does not alter `allow_private`, does not
bypass `URLPolicyValidator`, and defaults to the same sandbox policy; a private URL is
rejected to BLOCKED (verified by the benchmark: 500/500 BLOCKED).

## 16. Browser Resource Reaping

`test_phase_28_2_browser_reaping.py` green (regression).

## 17. Benchmark Results

| Metric | 100-run | 500-run (formal) |
|---|---|---|
| Enqueue | 100 in 0.72s (139/s) | 500 in 2.94s (170/s) |
| Drain (real daemon process) | 22.23s (4/s) | 80.85s (6/s) |
| Terminal states | 100/100 BLOCKED | 500/500 BLOCKED |
| Lost / stuck / recovery storms | 0 | 0 |

Every run was executed by a registered worker (`worker_id` non-null), none required
recovery (`recovery_count` ≤ 1).

## 18. Test Matrix

| Suite | Count | Backend | Result |
|---|---|---|---|
| 28.1 worker_path | — | SQLite | green |
| 28.2 claim_fencing / cancellation / checkpoint_resume / backpressure_observability / legacy_architecture / security_regression / browser_reaping | 63 | SQLite | green |
| 28.3 recovery_loop | 10 | SQLite | green |
| 28.3 lease_heartbeat | 5 | SQLite | green |
| 28.3 side_effect_fencing | 3 | SQLite | green |
| 28.3 status_model | 4 | SQLite | green |
| 28.3 migration | 3 | **PostgreSQL** | green |
| 28.3 postgres_concurrency | 8 | **PostgreSQL** | green |
| 28.3 worker_daemon | 2 | **PostgreSQL** | green |
| 28.3 process_isolation | 2 | **PostgreSQL** | green |
| 28.3 benchmark | 1 | **PostgreSQL** | green (500-run) |
| **Total** | **101 + benchmark** | | **all green** |

## 19. Known Limitations

- **Sandbox isolation**: the shipped provider is the in-process `MemorySandboxProvider`
  (`real_isolation=False`); network I/O is gated by `URLPolicyValidator`, but this is
  NOT claimed as OS-level sandboxing (future work).
- **Orphan blobs**: content-addressed object blobs written before a stale/cancelled
  rejection may remain on disk; they are never attached to a run without a fenced
  artifact row. A GC/reaper for orphan blobs is not implemented in this phase.
- **Exactly-once**: NOT claimed. The system provides: exactly one current owner per
  lease epoch, at-least-once execution attempts, a fenced terminal commit, and
  idempotent (content-hash-keyed) evidence persistence.
- **SQLite vs PG**: SQLite tests are not passed off as production certification; PG
  certification is a separate marked suite. SQLite's single-writer semantics can make
  concurrent reclaim look different (mitigated in tests; PG is authoritative).
- **Windows signals**: the daemon uses an `ACQ_RUN_SECONDS` test hook for graceful
  self-termination where `loop.add_signal_handler` is unavailable; POSIX platforms can
  use SIGTERM/SIGINT directly.
- The 500-run benchmark uses `url=http://127.0.0.1:9/` (private → BLOCKED); it
  certifies durability/consumption, not end-to-end network acquisition throughput.

## 20. Production Readiness Decision

**Gate summary (all PASS unless noted):**

| Gate | Status |
|---|---|
| GATE 1 Durable Queue (API enqueue only) | **PASS** — legacy_architecture + process_isolation |
| GATE 2 Automatic Recovery | **PASS** — recovery_loop + PG recovery test |
| GATE 3 Healthy Lease Renewal | **PASS** — lease_heartbeat 5/5 |
| GATE 4 Fencing (stale commit rejected) | **PASS** — claim_fencing + PG |
| GATE 5 Side-effect Safety | **PASS** — side_effect_fencing 3/3 (2 defects fixed) |
| GATE 6 Cancellation | **PASS** — cancellation 8/8 + PG durable cancellation |
| GATE 7 Migration | **PASS** — migration 3/3 on PG (incl. DB-level UNIQUE) |
| GATE 8 Worker Daemon | **PASS** — worker_daemon 2/2 (real subprocess) |
| GATE 9 PostgreSQL | **PASS** — postgres_concurrency 8/8 + 500-run benchmark |
| GATE 10 Security | **PASS** — security_regression green |
| GATE 11 No sync execution path | **PASS** — legacy_architecture green |
| GATE 12 Crash Restart | **PASS** — automatic recovery + PG race tests |

**Decision**: Phase 28.3 production runtime closure is **certified on PostgreSQL** for
durable queueing, automatic crash recovery, fencing, cancellation, migration, and
process isolation, with the explicit limitations above (notably: in-process sandbox
provider and orphan-blob GC remain future work). No claim of exactly-once execution or
exactly-once side effects is made.
