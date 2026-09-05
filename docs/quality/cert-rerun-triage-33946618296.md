# Certification rerun triage — Linux cert run 33946618296 (SHA 87d2409)

> Three-way failure classification per the CAP test discipline:
> **product race / harness race / DB semantic mismatch**.
> Conclusion: **harness race, non-product** — repaired by same-SHA rerun,
> no code change, no certification invalidation.

## Failure record

Run `33946618296` (2026-09-05T05:13Z, ~13 min):
`2 failed, 191 passed, 4 skipped, 1 deselected in 625.88s`

| Test | Symptom |
| --- | --- |
| `test_phase_28_2_backpressure_observability.py::test_claim_loop_executes_end_to_end` | `AssertionError: assert 'RUNNING' == 'COMPLETE'` after `loop.tick()` + `session.refresh(run)` |
| `test_phase_28_2_security_regression.py::test_execution_flows_through_sandbox_boundary` | `WorkerExecutionError: (sqlite3.OperationalError) cannot commit transaction - SQL statements in progress` inside `wp.run_claimed(...)` |

## Evidence

1. **Product code is byte-identical to the last success.**
   `git diff 9dbe60f 87d2409 -- backend/` touches only version metadata
   (Dockerfile ARG, `__init__.__version__`, `settings.app_version`,
   `pyproject version`, `uv.lock` root, `test_phase_23` RC_VERSION + notes
   list). No backend code change sits between run `33881924598` (success,
   9dbe60f) and run `33946618296` (failure, 87d2409).
2. **First failure in 12+ consecutive successes.** The
   `cap-linux-certification.yml` history from 2026-08-31 to 2026-09-04 is
   all `success` on every run; `87d2409` is the first `failure` on this
   line.
3. **Shared-session structure (the hazard itself).** Both tests construct
   the whole stack (loop / worker path / registry / leases / service) on ONE
   `AsyncSession` (`session` fixture). Inside `loop.tick()` the runner
   executes inline (`await`), but the execution path commits/rolls back on
   the same session the test body then uses for `session.refresh(run)`.
   Failure mode 1: an exception branch inside the path triggers
   `self._session.rollback()`, discarding the not-yet-committed terminal
   transition the test was about to observe (`RUNNING != COMPLETE`).
   Failure mode 2: interleaved commits on one SQLite connection hit
   `cannot commit transaction - SQL statements in progress` — a known
   SQLite-only concurrency hazard that PostgreSQL (production target) does
   not exhibit in this form.

## Classification

| Candidate | Verdict |
| --- | --- |
| product race | **No** — zero product-code delta vs the last green run; the race surface is the test's shared session, not the claim/fence/cancel contracts (those are DB-atomic CAS and are exercised green in the same run's other 191 tests). |
| harness race | **Yes** — load-sensitive interleaving between the test body and the execution path on one shared async session over SQLite. |
| DB semantic mismatch | **No** — the `SQL statements in progress` error is a SQLite driver-level artifact of the shared session, not a semantics difference (no lax/strict divergence in the asserted rows). |

## Disposition

- Immediate: rerun the certification on the same SHA
  (`gh run rerun 33946618296`). A green rerun restores the GATE 33 evidence
  chain (evidence remains bound to `87d2409`; the 7200s soak run
  `33946637590` stays valid).
- Follow-up (backlog, NOT a 1.0.4 blocker): give these two tests an isolated
  session for the execution path (or serialize commits via the fixture) so
  the shared-session hazard is removed at the root. Track under the CI
  governance roadmap track.
