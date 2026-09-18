# Final delivery audit -- 1.0.5 working tree (2026-09-19)

Auditor's view: everything below was re-executed against the tree at
`b7ac7ef` + the follow-up commit of this document, on a Windows workstation with
no Docker and no WSL, and the numbers are the real outputs rather than the
figures inherited from CI.

## Gates

| # | Item | Command | Result |
|---|------|---------|--------|
| 1 | Install dependencies | `uv lock --check --project backend`, `frontend/package-lock.json` | lockfiles consistent; `Resolved 87 packages`, exit 0 |
| 2 | Typecheck | `npm run build` (runs `tsc -p tsconfig.app.json` + `tsconfig.node.json`) | clean -- after fixing the TS2345 the audit itself introduced |
| 3 | Lint (frontend) | `npm run lint -- --max-warnings=0` | clean, zero warnings |
| 3b | Lint (backend) | `ruff check backend/app backend/tests benchmarks/phase22` | All checks passed |
| 4 | Unit + integration (backend) | CI's exact pytest invocation | **1224 passed, 123 skipped, 1 deselected, 0 failed** in 21m56s |
| 5 | Coverage gate | same run, `--cov-fail-under=90` | 91.85% |
| 6 | Console flow tests | `vitest run` | **29 passed** in 4 files (real axios adapter, unstubbed request = failure) |
| 7 | Production build | `npm run build` | built in ~19s, no chunk-size warning (entry 487 kB, limit 550) |
| 8 | Start the production build | `_audit/live_pg_e2e.py` -- bundled PostgreSQL 16.2, alembic over asyncpg, uvicorn with `APP_ENVIRONMENT=production` | `/health` -> `{"status":"ok","version":"1.0.5"}` |
| 9 | Core functions by hand | same script, 37 sequential checks over real HTTP | **LIVE POSTGRES END-TO-END: PASS (37 checks)** |
| 10 | UI final check | built bundle served from `frontend/dist` behind a front door that injects the proxy identity, driven in a real browser | Dashboard aggregates read from PostgreSQL (Assets 1 / Incidents 1 / Worker 1-1 healthy / Plugin 4-4 healthy / v1.0.5); Assets table header row `类型 名称 值 环境 关键度 标签 更新时间 操作`; the persisted `live.example` asset renders with its tags and timestamp |
| 11 | Error logs | uvicorn log + browser console after the whole flow | 27 log lines, 0 error/traceback; 0 browser console messages |
| 12 | Diff review | `git show --stat` per commit | 5 focused commits, no unrelated churn, no secrets, no commented-out code |
| 13 | Residual TODO/FIXME | grep over `backend/app` + `frontend/src` | **0** |
| 14 | Temporary code | grep + review | 0 debug scratch files in the tree (the two `dbg_*.py` files were removed); verification harnesses live in the gitignored `_audit/` and `_tmp/` |
| 15 | Debug output | grep `console.log`/`debugger` in shipped TS, `print(` in `backend/app` | none, except `sandbox/oci_shim.py`'s startup banner in its own standalone process |
| 16 | Secrets | tracked-file scan (`git ls-files`, private-key pattern, credential regexes, high-entropy blobs) | only `.env.example` files tracked; no real `.env`; the one "BEGIN RSA PRIVATE KEY" string is a test input for `analyze_secret_exposure`; long blobs are release URLs, an action commit SHA and an image digest |
| 17 | README | read as a first-time operator | install/run/verify path is complete and matches the Makefile and CI; states that `CAP_ZAP_API_KEY` is required for the incident/assessment planes |

## What item 9 covers

The 37 checks are: real PostgreSQL up (not SQLite) -> migrations (including the
five constraint-altering ones SQLite cannot express) -> schema readable through
the app's own asyncpg engine -> production startup -> RBAC fails closed (no
identity, wrong secret, unknown user, viewer write, viewer approval route) ->
asset + incident creation and transition -> invalid input rejected with 4xx not
500 (missing fields, unknown id, bad enum, bad decision value) -> list and
dashboard read the durable store -> audit trail recorded and attributed to the
authenticated principal -> approval queue -> separation of duties -> decision
recorded and readable -> the workflow approval gate (parks, cannot be passed by
resume, needs `approval.decide`, 422 on a nonsense decision, completes when
approved, reviewer named on the run, cannot be answered twice) -> clean server
log -> full process restart with the transitioned incident still there.

## Known limits of this audit

Container images, nginx routing, Redis, MinIO and the Kubernetes sandbox
provider are not exercised here (no container runtime on this machine); they are
certified by the CI and certification workflows, and `docs/known-issues.md` keeps
that inheritance explicit.
