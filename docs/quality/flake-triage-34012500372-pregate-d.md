# Flake triage — CI run 34012500372 / pregate-D "database is locked"

- **Run**: `34012500372` (CI, backend job), HEAD `53b9400` (docs-only commit)
- **Failing test**: `backend/tests/test_phase_28_7_ga_pregate_heartbeat.py::test_pregate_d_stale_owner_cannot_commit_after_reclaim`
- **Symptom**: `sqlite3.OperationalError: database is locked` raised from the
  best-effort lease `release` in `WorkerRuntime.execute`'s `finally`
  (`WorkerLeaseManager.release` → conditional UPDATE, `repositories/worker.py:104`)
- **Classification**: **PRODUCT RACE** (not a harness race, not a DB semantic mismatch)
- **Fix**: commit `0708012` — cooperative lease-heartbeat teardown

## Classification rationale

| Axis | Finding |
| --- | --- |
| Backend code change 53b9400 vs prior | none (docs-only) ⇒ a *docs* commit cannot introduce a product defect ⇒ the failure is latent, load/timing dependent |
| Reproduction | intermittent (~1 in 5 locally), never deterministic on a single run |
| Test parameters | untouched — the 3.0s payload is retained as a strong probe; no test value was weakened to make the failure go away |
| Production analogue | a cancelled-while-in-flight DB statement is a real hazard for any async teardown, on SQLite *and* PostgreSQL (an abandoned statement holds locks / pins a connection) |

The three-way discipline forbids "test environment limitation" hand-waving. This is
a product race because the **runtime code** destroys a DB statement it owns.

## Root cause

`execute()`'s `finally` used a bare `heartbeat_task.cancel()`.

1. The heartbeat renews the execution lease every `max(1.0, ttl/3.0)` seconds.
   With `ttl=1` that is a 1.0s cadence.
2. The sandbox operation sleeps 3.0s ⇒ the op-completion instant is an exact
   integer multiple of the renewal cadence (**structural resonance**): the
   cancellation lands while renewal #3 is mid-`UPDATE`.
3. `asyncio` cancellation cannot stop the driver thread. The renewal UPDATE has
   already been handed to the aiosqlite worker; once it starts, it takes the
   SQLite write lock. The coroutine that would have committed/rolled it back is
   gone, so the transaction is **abandoned**.
4. An abandoned statement is not reclaimed synchronously. The write lock stays
   held by a zombie transaction **until CPython's GC finalises the abandoned
   object**.
5. The subsequent best-effort `release` UPDATE then burns its entire
   `busy_timeout` on that lock: **22.27s** observed (test still passed) and
   **32.7s** observed (busy timeout exhausted ⇒ hard failure).

This also explains why the failure was invisible to static reasoning: the holder
is not a *live* connection, so no connection census can find it.

## Evidence

### 1. Post-close write-lock probe (differentiates the resonant renewal)

A raw `BEGIN IMMEDIATE` probe (50ms busy timeout) fired immediately after every
heartbeat-session teardown:

| Renewal | Cleanup | Probe |
| --- | --- | --- |
| v=1 | rollback + close | **FREE** (0.4 ms) |
| v=2 | rollback + close | **FREE** (0.4 ms) |
| v=3 (cancelled in flight) | rollback + close | **HELD** (50 ms timeout exhausted) |

`v=3` is the *only* differentiating event — the shield cleanup did run to
completion, yet the lock was already held by someone else.

### 2. `gc.collect()` discriminator (identifies the holder class)

The watchdog forces an explicit collection while the lock is held and re-probes:

```
WW gc.collect()=295 FREE-AFTER-GC     (reproduction 1)
WW gc.collect()=300 FREE-AFTER-GC     (reproduction 2)
```

The lock frees **immediately** after an explicit collection ⇒ the holder is
**unreachable garbage**, which is exactly why:

- a `gc.get_objects()` census of live connections never reported a holder, and
- the release delay was nondeterministic (22.27s here, 32.7s there).

### 3. Census blind spot (methodological note)

The first census filtered on `type(obj).__name__ == "Connection" and hasattr(obj, "_tx")`,
i.e. aiosqlite wrappers only. That filter **excludes native `sqlite3.Connection`
handles** — an entire class of candidates. The corrected census enumerates both.

## Fix (commit 0708012)

Teardown is **cooperative** instead of destructive:

- `execute()` creates a `stop` Event and joins the heartbeat task.
- `_heartbeat_lease` observes the stop between renewals via an interruptible
  wait (`asyncio.wait_for(stop.wait(), timeout=interval)`), so a renewal already
  in flight always runs to completion and disposes its session.
- `_HEARTBEAT_JOIN_TIMEOUT = 10s` remains as a last-resort reap for a renewal
  wedged past the budget (a bare `cancel()` is then unavoidable, but only on a
  database that is already unhealthy).
- The `asyncio.shield`-ed session cleanup in `_renew_on_dedicated_session` is
  retained as defence in depth for that last-resort path.

Note the ordering property: because the stop Event is observed *before* a
renewal starts, the resonant renewal (v=3 in the failing scenario) is **not
started at all** in the normal path.

**Shield alone is insufficient.** `asyncio.shield` guarantees the *cleanup
coroutine* runs; it cannot rescue a statement that was already abandoned by a
cancelled coroutine. Cooperative stop is the only fix that removes the hazard.

## Validation

| Check | Result |
| --- | --- |
| Single test, 10 consecutive rounds | 10/10 pass, 4.46–4.99s (baseline 4.5s), zero stalls |
| Release UPDATE latency | 22.27s → **~62 ms** |
| `test_phase_28_7_ga_pregate_heartbeat.py`, 3 consecutive runs | 4/4 each |
| Neighbour regressions (28.2 claim fencing, 28.3 recovery loop / lease heartbeat, heartbeat, GA heartbeat invariant) | 28 passed, 1 skipped |
| `ruff check` + `ruff format` | clean |
| CI run `34107611328` | 4/4 jobs success; backend **1201 passed, 123 skipped** (17m28s) |
| CAP Linux Certification `34107611375` | full-certification success |
| K8s Certification `34107611312` | success |
| GA Certification `34107611345` | supply-chain success; ga-certification in progress |

## Release-impact note

`classify_diff 53b9400 0708012` ⇒ **exit 2** (`backend/app/worker/runtime.py`
classified `production_runtime`) ⇒ certification is **not inheritable**; this
commit belongs on the 1.0.5-rc1 anchor line and requires re-certification.

## Scope note (reverted change)

An unrelated in-flight hardening of `backend/tests/test_phase_28_2_claim_fencing.py`
(per-test file-backed SQLite instead of the shared conftest StaticPool engine)
was **reverted**. Its rationale mis-attributed this run's flake to
`test_run_claimed_full_chain_completes`; the actual failing test was pregate-D,
and the claim-fencing suite has no demonstrated flake. Speculative changes
without evidence do not enter a release-candidate line.
