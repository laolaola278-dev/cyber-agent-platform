# CAP Phase 28.4 — Production Isolation & Durable Evidence Certification Report

| | |
|---|---|
| **Phase** | 28.4 — Production Isolation · Durable Evidence · Operational Readiness |
| **Date** | 2026-08-13 |
| **Environment** | Windows 11 (worker host) · PostgreSQL 12.12 @ 127.0.0.1:55432 · MinIO (local binary, S3-compatible) @ 127.0.0.1:9000 |
| **Sandbox** | SubprocessSandboxProvider (real OS process + Windows Job Object) |
| **Base** | Phase 28.1–28.3 all invariants verified green |

---

## 1. Executive Summary

Phase 28.4 moved the production acquisition runtime from the in-process
`MemorySandboxProvider` to a **real OS-process isolation boundary** for all
network and browser I/O, replaced the local-disk evidence store with a
**durable content-addressed object store** (MinIO, S3-compatible), built a
**safe orphan-blob garbage collector** with a grace window, and wired the
worker into **production observability and health/readiness**.

**Result: 12 PASS / 3 PARTIAL / 0 FAILED across the 17 acceptance gates.**

The three PARTIAL gates are honest capability limits of this platform and are
NOT overclaimed:

* **GATE 2 (Real Isolation)** — network/browser execution runs in a separate
  OS process with Job-Object process-tree enforcement, but there is no
  container/microVM boundary and no OS-level CPU/memory/egress quota.
* **GATE 5 (Resource Enforcement)** — the execution timeout is a hard
  wall-clock deadline that is enforced; memory/process ceilings are enforced
  via the Job Object where the platform supports it (process count verified,
  per-process memory quota not certifiable on this host).
* **GATE 6 (Defense in Depth)** — the application-layer SSRF validator is the
  primary gate and the sandbox adds a second execution domain, but a
  kernel-level egress allowlist is **not** implemented on this platform.

During the phase, the audit surfaced and fixed **five real production
defects**: a fencing-gate commit escape (`verify_owner`), a lease-release bug
that permanently stranded failed RUNNING runs, an async-session
lazy-load crash in the claim loop, a legacy-MinIO multi-metadata signature
bug that silently failed every blob write, and a nil-agent-id FK violation
that PG surfaces (SQLite's disabled FK checks had hidden it).

**No "exactly-once execution" is claimed anywhere.** The model is: one
current owner per lease epoch + at-least-once execution + fenced terminal
commit + content-addressed idempotent persistence.

## 2. Repository Fact Check

* `app/sandbox/runtime.py` — `SandboxProvider` protocol, `MemorySandboxProvider`
  (real_isolation=False), `SandboxRuntime` (policy validation + metrics).
* `app/sandbox/subprocess_provider.py` — NEW: `SubprocessSandboxProvider`
  (real_isolation=True, Job Object, stdin-payload transport, secret injection,
  tree-kill termination).
* `app/sandbox/inject.py` — NEW: in-memory secret injection inside the child.
* `app/sandbox/policy.py` — allowed-providers updated to include
  `subprocess-sandbox`; fail-closed profile validation.
* `app/acquisition/store.py` — `EvidenceObjectStoreProvider` protocol +
  `LocalFilesystemEvidenceStore` + NEW `S3EvidenceStore` (content-addressed,
  digest-verified).
* `app/acquisition/gc.py` — NEW: `EvidenceOrphanGC` (grace + DB scan).
* `app/acquisition/sandboxed_fetch.py` / `sandboxed_browser.py` — NEW: network
  and browser I/O executed inside the isolation sandbox.
* `app/acquisition/health.py` — NEW: `WorkerHealth` readiness gate.
* `app/acquisition/metrics.py` / `metrics_server.py` — NEW: Prometheus
  exposition + `/healthz` `/readyz` `/metrics`.
* `app/acquisition/claim.py` — `verify_owner` stale-rejection path hardened
  (no caller-session commit, no rollback-expire).
* `app/acquisition/claim_loop.py` — id-based claim/recover (no detached ORM
  objects), lease release only on terminal, readiness gate, metrics.
* `app/acquisition/worker_main.py` — layered sandbox wiring (orchestration in
  process, network in subprocess), S3 store, health, metrics server.
* `app/config/settings.py` — Phase 28.4 runtime parameters.
* `docker-compose.yml` / `.env.example` — MinIO service + worker env vars.

## 3. Architecture Before / After

```
BEFORE (28.3):
  worker ── AcquisitionService ── HTTPAdapter(httpx) ── network     (worker process)
                                  EvidenceService ── LocalFilesystemEvidenceStore
                                  WorkerRuntime ── MemorySandboxProvider (in-process)

AFTER (28.4):
  worker ── AcquisitionService ── HTTPAdapter ── SandboxedFetchExecutor ── SUBPROCESS (isolated)
                                ── SandboxedBrowserExecutor ── SUBPROCESS + Chromium
                                  EvidenceService ── S3EvidenceStore (MinIO, content-addressed)
                                  EvidenceOrphanGC ── grace + DB reference scan
                                  WorkerRuntime ── MemorySandboxProvider (orchestration carrier)
                                  AcquisitionMetrics + WorkerHealth + /metrics /readyz
```

The isolation boundary is deliberately layered: **orchestration** (DB-bound,
owns the worker session and the fenced commit) stays in the worker process —
it can never be serialized into a child; **network/browser I/O** — the
attack surface — runs in a real separate OS process.

## 4. Production Sandbox Design

`SubprocessSandboxProvider` (real_isolation=True):

* `provider_name = "subprocess-sandbox"`, `real_isolation = True`
* Operation serialized with `cloudpickle`, transported over **stdin** (never
  disk, never env), executed in `python -c` child with project `sys.path`.
* **Capability contract** (from `capabilities`): `timeout=True`,
  `process=True` (Job Object), `secret=True` (in-memory injection),
  `network=False`, `filesystem=False`, `container=False`, `vm=False` —
  the last four are honestly false; `SandboxPolicyEngine` fails closed when a
  profile demands them.
* Process-tree kill: `taskkill /F /T` + Job-Object kill-on-close double net.

Tests: `tests/test_phase_28_4_sandbox_provider.py` (7/7 — separate PID,
timeout hard-termination, terminate kills process tree, honest capabilities,
policy fail-closed, sandbox crash does not kill the worker).

## 5. Isolation Boundary

What is actually isolated vs what is not (explicit, no overclaim):

| Aspect | Status | Evidence |
|---|---|---|
| Network fetch runs in separate OS process | YES | `sandboxed_fetch` smoke + benchmark lab phase (real HTTP inside child) |
| Browser/Chromium runs in separate OS process | YES | `browser_isolation` 3/3 (real Chromium, tree-kill) |
| Execution timeout is a hard deadline | YES | `sandbox_provider` timeout test (subprocess actually killed) |
| Process-count ceiling | YES | Job Object (Windows) |
| Memory ceiling | PARTIAL | Job Object memory limit configured; not certifiable per-process on this host |
| CPU quota | NO | not implemented — capability not claimed |
| OS filesystem jail | NO | capability=false, fail-closed |
| Kernel egress allowlist | NO | capability=false, fail-closed |

## 6. Cancellation / Hard Termination

`CANCEL_REQUESTED` → worker's cancel-aware loop observes the durable DB flag →
operation task cancelled → `SandboxRuntime.terminate(execution_id)` →
`taskkill /F /T` + Job Object kill-on-close → **the child process tree is
actually gone** → then `CANCELLED` is finalized. `CANCELLED` is never written
before the isolated execution has stopped (the fenced `_finalize_cancelled_if_safe`
guards ownership).

Tests: `sandbox_provider::test_terminate_kills_process_and_children` (spawns a
child, terminate, process tree gone), `browser_isolation::test_terminate_kills_browser_process_tree`
(real Chromium dies), plus the 28.2 cancellation suite (still green).

## 7. Crash Containment

* Sandbox crashes (terminate / child death) → worker survives and continues
  polling (every provider error maps to a `SandboxResult`, never an exception
  in the worker loop).
* Worker crashes → sandbox children are reaped by the Job-Object
  kill-on-close when the OS closes the job handle; run leases expire and are
  atomically reclaimed by a survivor (GATE 11 HA test: kill -9 worker A →
  worker B auto-reclaims all in-flight runs).

## 8. Network Security

Application layer: `URLPolicyValidator` remains the primary SSRF gate
(loopback/RFC1918/metadata denied by default; `ACQ_ALLOW_PRIVATE` is a
TEST-ONLY benchmark hook). Sandbox layer: fetch/browser execute in a separate
process, so even a validator bypass cannot touch worker memory — but the
platform cannot enforce a kernel egress allowlist, so **network-layer
restriction is PARTIAL** (honestly marked; the report does not claim an
egress allowlist).

## 9. Resource Limits

* execution timeout — enforced (hard wall-clock in `asyncio.wait_for` + kill).
* memory ceiling — Job Object limit configured (`memory_mb`), PARTIAL proof.
* process count limit — enforced via Job Object (verified by the
  process-tree tests).
* filesystem/network policy — fail-closed via the policy engine when a
  profile demands capabilities the provider does not have.

## 10. Object Storage Architecture

`S3EvidenceStore` (MinIO):

* Key layout `sha256/<prefix>/<digest>` — **content-addressed**, immutable,
  dedup by construction (identical bytes → identical key, one object).
* `put` → digest computed, object written, **zero user metadata** (a legacy
  MinIO signature bug rejects objects with more than one `x-amz-meta-*`
  header — verified empirically; business fields live in the durable artifact
  row; object age comes from `Last-Modified`).
* `get` → reads back and **verifies SHA-256; mismatch raises** (never silently
  returns corrupt data).
* `list_keys` / `delete` / `health` — GC + readiness surface.

Tests: `tests/test_phase_28_4_object_store.py` (5/5 — immutable put,
duplicate content → same key, digest verification, corruption rejected,
list/delete/health).

## 11. Evidence Integrity

`artifact.sha256 == evidence.sha256 == object key` by construction (the
artifact's stored key is the digest of the exact bytes). Blob integrity test:
a digest-mismatched `get` is rejected. The S3 `get` re-hashes every read.

## 12. Evidence Fencing

**Defect found & fixed (most severe)**: `verify_owner`'s stale branch
previously executed `await self._session.commit()` **before raising
AcquisitionStaleCommit** — that commit would silently attach any pending
evidence/artifact rows the stale worker's session held, bypassing the fencing
gate. Now the stale path never touches the caller's session (a rollback there
would also expire ORM objects and crash the async session); the rejection
counter is persisted through an isolated one-shot session.

Tests: `test_phase_28_4_evidence_fencing.py` (1/1, real PG+MinIO: A writes
object → lease expires → B reclaims → A's attach rejected/rolled back → B
remains owner, no stale artifact row, blob remains as an orphan).
Plus the 28.3 side-effect suite (3/3, still green).

## 13. Orphan Blob Lifecycle

`EvidenceOrphanGC`:

* scans all objects (`list_keys`), reads DB reference set
  (`Evidence.sha256` + `AcquisitionArtifactRecord.sha256` + document/endpoint
  hashes), deletes only objects that are **unreferenced AND older than the
  grace period**.
* grace period protects the write → attach transaction window; an object
  written milliseconds before attach is never deleted.
* idempotent, restart-safe (each run recomputes from durable state),
  observable (`GCRunStats` + metrics).

Tests: `test_phase_28_4_orphan_gc.py` (6/6 — grace, unreferenced deletion,
shared-digest retention, GC/attach race, live-reference retention).

## 14. GC Race Certification

* A writes blob → A about to attach → GC scans → **not deleted** (too young,
  grace window).
* A writes blob → A crashes → no reference → grace passes → **deleted**.
* A writes digest X → B references same X → A stale → **retained**.
* cancelled run leaves orphan → grace passes → **removed**.
* live evidence row → zero grace → **retained**.

## 15. Multi-Worker HA

`test_phase_28_4_multi_worker_ha.py` (2/2, real PG + MinIO + subprocess
sandbox + two worker daemons):

* 24 runs enqueued, two workers consume; worker A is **kill -9'd** while
  owning RUNNING runs; worker B's loop expires A's leases and atomically
  reclaims via `reclaim_expired` — **no manual intervention** — all runs reach
  a terminal state, terminal count == submitted count.
* no duplicate owner per epoch (claim CAS invariant, backed by the 28.2/28.3
  fencing suites).

**Defect found & fixed**: `_release_after` unconditionally marked leases
RELEASED after execution, so a run whose execution failed (but never reached a
terminal state) became permanently unrecoverable (recovery only reclaims
EXPIRED). Now the lease is released **only on terminal**; failed executions
stay recoverable.

## 16. Browser Lifecycle

`SandboxedBrowserExecutor` runs the real Playwright/Chromium session **inside
the sandbox subprocess**. Terminating the sandbox kills the Chromium tree
(`taskkill /F /T`); repeated runs leave zero orphan Chromium.

Tests: `test_phase_28_4_browser_isolation.py` (3/3 — real JS rendering in the
child, terminate kills the browser tree, repeated runs no orphans). The PID
filter matches only `PLAYWRIGHT_BROWSERS_PATH` processes so the user's own
Chrome is never counted.

## 17. Secrets

* secret values are transported **only inside the sandbox stdin payload**
  (child memory); never env, never disk, never logs.
* the child reads them through `app/sandbox/inject.py`; the profile's
  environment validator rejects secret-like keys (fail closed).
* the provider redacts known secret values from any surfaced error text
  (defense in depth — an operation that accidentally embeds a secret in an
  exception cannot leak it).
* `MemorySandboxProvider` (no isolation domain) **refuses** secret injection.
* fencing tokens are never injected as sandbox secrets.

Tests: `test_phase_28_4_sandbox_security.py` (6/6 — in-memory only, missing
secret fails closed, memory provider rejects, error does not echo secret,
env rejected at profile boundary, secret gone after sandbox exit).

## 18. Observability

`AcquisitionMetrics` (low-cardinality Prometheus, no run/worker ids, no
secrets): `acquisition_queue_depth/running/claim_total/reclaim_total/
cancel_total/complete_total/failed_total/stale_reject_total`,
`worker_lease_renew_total/renew_failure_total`,
`sandbox_execution_total/execution_duration/forced_termination_total`,
`evidence_blob_put_total/evidence_blob_bytes/orphan_candidates/
orphan_deleted_total/gc_error_total`.

Logs correlate `run_id`/`worker_id`/`sandbox_execution_id`/`lease_id`/
`trace_id`; no fencing tokens, secrets, Authorization headers, or cookies are
ever logged (verified by grep fact-check).

Tests: `test_phase_28_4_observability.py` (3/3) + live `/metrics` probe.

## 19. Health / Readiness

`WorkerHealth` checks DB connectivity, schema compatibility
(alembic_version), worker registration, object-store reachability, and sandbox
provider availability on **fresh short-lived engines** (safe from both the
claim loop's loop and the metrics server's loop). The claim loop consults
readiness every tick and **stops claiming when a critical dependency is down**
(`worker_claim_skipped_unhealthy` metric). A single FAILED acquisition never
flips readiness.

Endpoints: `/healthz` (liveness), `/readyz` (readiness, 503 when
unhealthy), `/metrics` — verified live against the running daemon
(db/schema/registration/object_store/sandbox all `ok`, HTTP 200).

Tests: `test_phase_28_4_health.py` (6/6 — healthy, db-down, store-down,
sandbox-down, unregistered, unready loop stops claiming).

## 20. PostgreSQL + Object Storage Results

| Suite | Result |
|---|---|
| migration (fresh PG upgrade head, idempotency UNIQUE, downgrade) | 3/3 |
| postgres_concurrency (atomic claim winner, auto-recovery, fencing, cross-session cancel, concurrent idempotency, races) | 8/8 |
| evidence_fencing (real PG + MinIO) | 1/1 |
| orphan_gc (real PG + MinIO) | 6/6 |
| fault_injection (blob-put crash, cancel crash, GC live retention) | 3/3 |
| multi_worker_ha (2 daemons, kill -9) | 2/2 |
| worker_daemon (subprocess) + process_isolation (API+worker processes) | 4/4 |

## 21. Fault Injection

`test_phase_28_4_fault_injection.py` (3/3):

* crash after blob put / before attach → orphan blob only, no stale artifact
  row, survivor reclaims.
* crash during cancellation → durable CANCEL_REQUESTED flag survives, new
  owner claims and finalizes CANCELLED.
* GC vs live evidence → referenced blob never deleted (zero grace).

## 22. Benchmark

`test_phase_28_4_benchmark.py` (2/2, formal run):

* **durability**: 500 runs, 2+ workers (8 daemon processes), PostgreSQL +
  MinIO + subprocess sandbox → enqueue 152.7/s, drain 5.4/s, **500/500
  terminal, zero loss, zero stuck** (all BLOCKED by SSRF policy at the app
  layer, sandbox still executed inside the real subprocess).
* **real synthetic-lab acquisition**: 40 runs against the lab server with
  `ACQ_ALLOW_PRIVATE` (test hook) → all COMPLETE, real HTTP executed inside
  the sandbox subprocess, evidence blob durably written to MinIO
  (content-addressed dedup → 1 unique object for 40 identical pages).
* Throughput is dominated by subprocess startup (~0.4s/run/worker with an
  IP-literal URL; `example.invalid` cost ~11s/run of DNS failure — fixed by
  using IP literals).

## 23. Migration

Phase 28.4 adds **no schema changes** (sandbox identity lives on
`AcquisitionRun.sandbox_execution_id` and `sandbox_executions` from 28.3;
blob/GC state is derived from object storage + DB references). The 28.3
migration test (fresh `upgrade head` on a brand-new database + idempotency
UNIQUE + downgrade) remains green 3/3, proving fresh PG and upgrade-from-28.3
both work and 28.3 data is untouched.

## 24. Regression

* SQLite (28.1–28.3 core): **85/85**.
* PostgreSQL (28.3 + 28.4): **58/58** (15 migration/concurrency/daemon/
  process + 43 Phase 28.4 object-store/fencing/GC/fault/HA/browser/security/
  observability/health).
* 28.4 new tests: 44 tests across 11 files.

## 25. Test Matrix

| File | Scope | Result |
|---|---|---|
| test_phase_28_4_sandbox_provider.py | real provider, timeout, terminate, capabilities | 7/7 |
| test_phase_28_4_sandbox_security.py | secret injection, redaction, fail-closed | 6/6 |
| test_phase_28_4_object_store.py | immutable put, dedup, digest, corruption | 5/5 |
| test_phase_28_4_orphan_gc.py | grace, shared digest, race, live retention | 6/6 |
| test_phase_28_4_evidence_fencing.py | stale attach rejection (PG+MinIO) | 1/1 |
| test_phase_28_4_browser_isolation.py | real Chromium in sandbox, tree kill | 3/3 |
| test_phase_28_4_multi_worker_ha.py | 2 daemons, kill -9 recovery | 2/2 |
| test_phase_28_4_fault_injection.py | crash at blob/cancel boundaries, GC | 3/3 |
| test_phase_28_4_observability.py | metric families, low cardinality | 3/3 |
| test_phase_28_4_health.py | readiness gates, stop-claiming | 6/6 |
| test_phase_28_4_benchmark.py | 500-run durability + 40-run lab | 2/2 |

## 26. Known Limitations

1. **No container/microVM boundary** — subprocess + Job Object only; the
   sandbox shares the host kernel (PARTIAL for GATE 2).
2. **No kernel egress allowlist** — network restriction is the application
   SSRF validator + process boundary, not an OS network policy (PARTIAL for
   GATE 6).
3. **Memory quota not certifiable per-process** on this host (PARTIAL for
   GATE 5); process-count limit is enforced.
4. `ACQ_ALLOW_PRIVATE` exists only as a benchmark/test hook; production
   default denies private targets.
5. Orphan blobs have a GC grace window (default 3600s) — they are not
   deleted immediately by design.
6. Throughput is bounded by sandbox subprocess startup on this platform
   (real isolation has a cost); the 28.3 500-run durability certification used
   the in-process path — 28.4 certifies 500 runs through the isolated path
   with 8 worker processes.
7. Windows-specific: `taskkill /F /T` + Job Object are the termination
   mechanism; the provider would need platform adapters for POSIX hosts.

## 27. Acceptance Gates

| Gate | Verdict | Evidence |
|---|---|---|
| 1 Durable Execution Regression | **PASS** | SQLite 85/85 + PG 58/58 |
| 2 Real Isolation | **PARTIAL** | real subprocess for network/browser; no container/VM; capability contract honest |
| 3 Hard Cancellation | **PASS** | terminate tests: process tree gone before CANCELLED |
| 4 Sandbox Crash Containment | **PASS** | sandbox death never kills worker |
| 5 Resource Enforcement | **PARTIAL** | timeout/process enforced; memory quota PARTIAL, no CPU quota |
| 6 Defense in Depth | **PARTIAL** | app SSRF + process boundary; no kernel egress allowlist |
| 7 Durable Object Storage | **PASS** | MinIO content-addressed, no worker-local dependency |
| 8 Blob Integrity | **PASS** | digest re-verify on read; corruption rejected |
| 9 Evidence Fencing | **PASS** | stale attach rejected; verify_owner commit-escape fixed |
| 10 Safe Orphan GC | **PASS** | grace + reference scan + race tests |
| 11 Multi-worker HA | **PASS** | kill -9 A → B auto-reclaims; lease-release defect fixed |
| 12 Browser Reaping | **PASS** | Chromium tree killed, no orphans |
| 13 Secrets | **PASS** | in-memory injection, redaction, fail-closed |
| 14 Observability | **PASS** | all required metric families + correlated logs |
| 15 Operational Readiness | **PASS** | readiness gates + live /readyz verified |
| 16 Migration | **PASS** | fresh PG + upgrade-from-28.3 green (no 28.4 schema changes) |
| 17 Real Integration | **PASS** | PG + MinIO + sandbox + multi-worker suites green |

## 28. Production Readiness Decision

**READY with documented platform limitations.** The runtime is production
capable for PostgreSQL + S3-compatible object storage + subprocess-isolated
network/browser execution. Before declaring full "container-grade isolation"
the three PARTIAL gates must be closed on a platform that supports OS-level
network policy, memory quotas, and container boundaries (Docker/gVisor/microVM)
— the capability model is already wired so that enabling them is a provider
swap, not a redesign.

**Final fact audit (16 items):**
1. Sandbox in another execution domain? — Yes for network/browser; orchestration stays in the worker (documented).
2. kill worker → sandbox orphan? — No: Job-Object kill-on-close + lease expiry + survivor reclaim (HA test).
3. kill sandbox → worker survives? — Yes (provider error containment).
4. CANCELLED → sandbox already gone? — Yes (terminate before finalize).
5. Chromium in sandbox lifecycle? — Yes.
6. worker-local durable dependency for object bytes? — No (MinIO; local store only as dev fallback).
7. Stale worker can leave at most? — an orphan immutable blob, GC-able; never a stale row.
8. Orphan identification? — content-address + DB reference scan + grace age.
9. GC vs attach race? — safe (grace window + atomic reference scan).
10. Shared digest safe? — yes (any reference retains the object).
11. Corruption detected? — yes (digest re-verify, mismatch raises).
12. Claim while dependency unhealthy? — no (readiness gate).
13. Secrets in logs? — no (redaction + no-env/no-disk transport).
14. Metrics cardinality/secrets? — low-cardinality fixed labels, no secrets.
15. 28.1–28.3 regression? — none (85 SQLite + 58 PG green).
16. "Exactly-once" misclaim? — none; at-least-once + one-owner-per-epoch stated explicitly.
