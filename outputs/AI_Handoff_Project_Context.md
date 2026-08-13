# CAP — AI 开发交接扫描（Project Context Handoff）

> 本文档为 **代码级 onboarding 文档**。所有结论均以 `cyber-agent-platform` 仓库当前工作树的实际代码为唯一事实来源（Git `main` 分支当前 **无任何 commit**，因此无法从 git 历史推断演化；下文凡涉及"演化"均为基于代码与测试的现状反推，已显式标注）。
> 配套认证报告见同级 `outputs/CAP Phase 28.2 Durable Execution Certification Report.md`（该报告为 Phase 28.2 的认证叙事；本文档以代码为准，二者冲突时以代码为准）。

---

## A. PROJECT IDENTITY

- **项目名称**：Cyber Agent Platform（简称 **CAP**），仓库目录名 `cyber-agent-platform`。
- **项目目标**：企业级安全编排控制平面（security-orchestration control plane），统一治理 Asset / Knowledge / Assessment / Detection / Incident / Response / Notification / **Worker / Sandbox / Telemetry / Plugin / Playbook** 等能力，通过稳定接口、RBAC、审批、审计与可观测性对外暴露。
- **核心业务场景（本文档聚焦）**：**数据获取（Acquisition）** 子系统——受控地抓取公开网页/文档/公开 JSON API，产出证据链（Source → Raw Artifact → Evidence → ExtractedDocument → candidates），并用于安全情报生产。
- **当前开发阶段**：Phase 28 系列。Phase 28.1 = "Acquisition Production Path"（把获取从同步直跑改到 Worker/Sandbox 边界）；Phase 28.2 = "Durable Execution"（DB 即真相的持久队列 + 原子 Claim + Fencing + 真正取消 + 检查点续跑 + 背压/可观测）。两者代码均已落地并通过测试。
- **当前 maturity**：`VERSION` = `1.0.0-rc1`（release candidate）。README 称 backend 为 FastAPI/SQLAlchemy/PostgreSQL/Redis；acquisition 路径**已具备生产化执行链骨架但尚未部署运行**（见 Q/R/W）。
- **主要语言/框架**：Python 3.12 后端（FastAPI + SQLAlchemy 2.0 async + Pydantic v2），React/TypeScript/Vite/Ant Design 控制台（frontend/，本文档不深入）。
- **关键运行时版本（可确认）**：
  - Python 3.13（本机解释器）；`pyproject.toml` 的 `target-version` 为 `py312`。
  - fastapi `>=0.115,<1.0`；sqlalchemy `[asyncio] >=2.0.35,<3.0`；uvicorn `[standard] >=0.30.6`；playwright `>=1.49,<2.0`；asyncpg `>=0.29`（Postgres 驱动，已声明）；aiosqlite `>=0.20`（测试用 SQLite 驱动）。
  - 浏览器：Playwright Chromium（headless）。
- **包管理 / 依赖管理**：`uv`（README 用 `uv run --project backend ...`）；锁文件 `uv.lock` 存在。也可直接用 `backend/.venv`（历史会话用 `backend/.venv/Scripts/python.exe`）。
- **当前 Git branch / HEAD**：`git branch --show-current` → `main`；`git log` → **"your current branch 'main' does not have any commits yet"**。即全部代码在 **未提交工作树** 中。无法从 git 确认历史。
- **repository 根目录**：`cyber-agent-platform/`（绝对路径 `F:/work/buddy_work/2026-07-29-12-17-38/cyber-agent-platform/`）。后端代码在 `backend/`。

---

## B. REPOSITORY MAP

```
cyber-agent-platform/
├── backend/                      # Python 后端（核心）
│   ├── app/                      # 应用代码
│   │   ├── acquisition/          # ★ 数据获取子系统（Phase 28 核心）
│   │   ├── worker/               # 通用 Worker 框架（registry/lease/scheduler/runtime/plugin_runtime）
│   │   ├── sandbox/              # 沙箱运行时 + 策略引擎（MemorySandboxProvider）
│   │   ├── tools/playwright/     # 平台级 Playwright 工具（BrowserManager/PlaywrightAdapter）
│   │   ├── api/routes/           # FastAPI 路由（含 acquisition.py）
│   │   ├── api/router.py         # 顶层路由装配
│   │   ├── models/               # 平台 ORM 模型（worker.py 含 Worker/WorkerLease/SandboxExecution 等）
│   │   ├── repositories/worker.py# WorkerLeaseRepository / WorkerRepository / SandboxExecutionRepository
│   │   ├── evidence/             # EvidenceService（证据落库，获取链路复用）
│   │   ├── database/             # Base + 会话工厂（session.py）/ 配置（config/settings.py）
│   │   └── ...                   # agent/assessment/detection/incident/... 其他子系统
│   ├── tests/                    # pytest 测试（大量 test_phase_*.py）
│   ├── alembic/versions/         # 20 个迁移（最新到 phase26）；★ acquisition 表无迁移
│   ├── alembic.ini
│   ├── pyproject.toml            # 依赖 + pytest 配置（asyncio_mode=auto）
│   ├── Dockerfile
│   └── scripts/                  # 各 phase 的辅助脚本（无 worker 守护进程启动器）
├── frontend/                     # React 控制台（本文档不深入）
├── deployment/                   # Helm chart 等（README 提及）
├── outputs/                      # 交付物（报告、认证文档）
├── docs/                         # 文档
├── docker-compose.yml            # 含 postgres 服务（默认 DB URL 指向 postgres:5432）
├── Makefile                      # up/down/test/lint/migrate 等目标
├── VERSION, README.md, CHANGELOG.md, SECURITY.md, LICENSE
└── _待删_回收区/                 # 本机清理用隔离目录（非项目产物）
```

### 核心目录职责

- **`backend/app/acquisition/`** — 获取子系统全部逻辑：
  - `service.py`：平台门面 `AcquisitionService`（create/enqueue、requeue、持久化结果、证据 sink）。
  - `models_db.py`：ORM 模型（`acquisition_runs` 等 7 张表）。
  - `models.py`：纯 dataclass 领域模型 + 枚举（SourceType/AcquisitionStatus/BlockReason 等）+ `AcquisitionPolicy`（含背压 `max_queued_runs`）。
  - `claim.py`：★ `AcquisitionClaimCoordinator`（原子 Claim / Fencing 校验提交 / `reclaim_expired` 崩溃恢复）。
  - `claim_loop.py`：★ `AcquisitionWorkerLoop`（有界轮询的持久队列 Worker）。
  - `worker_path.py`：★ `AcquisitionWorkerPath`（Fencing 校验的执行入口 `run_claimed`、取消 `cancel`、终态定稿 `_finalize_cancelled`）。
  - `checkpoint.py`：`AcquisitionCheckpoint`（续跑状态 dataclass）。
  - `urlpolicy.py`：`URLPolicyValidator`（SSRF 防护）。
  - `httpadapter.py`：`HTTPAdapter`（唯一的真实 HTTP I/O，强制策略 + SSRF）。
  - `browseradapter.py`：`PlaywrightAcquisitionAdapter`（动态页获取，包装平台 Playwright 工具）。
  - `agent.py`：`AdaptiveDataAcquisitionAgent.acquire`（计划→抓取→分页→抽取→完整性评估；支持 checkpoint 续跑）。
  - `exceptions.py` / `observability.py` / `planner.py` / `completeness.py` / `dataset.py` / `evaluation.py` / `report_v2.py` / `store.py` / `dedup.py` / `robots.py` / `candidates.py` / `capabilities.py` / `documentadapter.py` / `pagination.py`。
- **`backend/app/worker/`** — 通用 Worker 框架（Phase 16/17 引入，被 acquisition 复用）：
  - `contracts.py`：`WorkerStatus`/`LeaseStatus`/`WorkerRecord`/`WorkerLease`/`PluginExecutionRequest`/`WorkerExecutionResult`（Pydantic 契约）。
  - `lease.py`：`WorkerLeaseManager`（数据库权威的租约 + fencing token 语义，Acquisition 复用它）。
  - `registry.py`：`WorkerRegistry`（注册/心跳/状态机校验）。
  - `scheduler.py`：`WorkerScheduler.select(capability)`（能力感知选活 Worker）。
  - `state_machine.py`：`validate_transition`（Worker 生命周期严格状态机）。
  - `runtime.py`：`WorkerRuntime`（执行历史持久化 + 仅当前租约持有者可提交结果）。
  - `plugin_runtime.py`：`PluginWorkerRuntime`（跨 Worker/Sandbox 边界序列化结果；`synthetic()` 用内存 DB 合成控制面）。
- **`backend/app/sandbox/`** — 沙箱边界：`runtime.py`（`SandboxRuntime` + `MemorySandboxProvider`）、`policy.py`（`SandboxPolicyEngine` fail-closed）、`profile.py`、`secret.py`、`contracts.py`、`local.py`。
- **`backend/app/tools/playwright/`** — `browser.py`（`BrowserManager`：管理一个 Browser 进程 + 隔离 `BrowserContext` 集合 `_contexts`）、`adapter.py`（`PlaywrightAdapter`：校验仅公开 HTTP GET、无 cookie/header/credential/proxy 注入）。
- **`backend/app/api/routes/acquisition.py`** — 获取 API（前缀 `/acquisitions`）。
- **`backend/tests/`** — pytest；acquisition 相关：`test_phase_28_1_*.py` 与 `test_phase_28_2_*.py`；通用 `conftest.py` 用 SQLite in-memory + `StaticPool` 提供 `session` fixture，并通过 `Base.metadata.create_all` 建表（**不走 alembic**）。

---

## C. SYSTEM ARCHITECTURE

宏观链路（以一次"创建并真正执行一次获取"为例）：

```
Client
  │  POST /acquisitions   (goal, url, idempotency_key, ...)
  ▼
api/routes/acquisition.py: create_acquisition()
  │  ├─ _backpressure_guard()                    # 若 policy.max_queued_runs>0 且 pending>=limit → 503
  │  └─ AcquisitionService.create(...)           # 仅持久化：插入 acquisition_runs(status=QUEUED)
  ▼  返回 202（绝不在此执行获取，绝不 asyncio.create_task）
AcquisitionRun 行 (QUEUED)  ——  DB 是持久队列的唯一 source of truth

   （独立进程/循环：AcquisitionWorkerLoop —— 当前未部署守护进程，见 W）
   ▼
AcquisitionWorkerLoop.tick()
  │  ├─ _expire_stale()          # WorkerLeaseManager.expire() 使过期租约 EXPIRED
  │  ├─ _next_batch()            # SELECT acquisition_runs WHERE status IN (QUEUED, CANCEL_REQUESTED) ORDER BY created_at
  │  └─ _claim_and_run(run):
  │        ├─ (若 CANCEL_REQUESTED 且未 claim) → 直接定稿 CANCELLED
  │        └─ coordinator.claim(run.id, worker_id, token)   # 原子 UPDATE ... WHERE status IN (QUEUED,CANCEL_REQUESTED)
  │              │  CAS rowcount==1 → 赢家；否则 AcquisitionClaimConflict（落败者跳过）
  │              └─ WorkerLeaseManager.acquire(...)           # 绑定 owner 租约 + fencing token
  │        └─ runner(run.id, token)   # = AcquisitionWorkerPath.run_claimed(...)
  ▼
AcquisitionWorkerPath.run_claimed(run_id, worker_id, token)
  │  ├─ coordinator.verify_owner(...)            # Fencing 校验：仍是当前 owner 且租约 ACTIVE，否则 AcquisitionStaleCommit
  │  ├─ 若已 CANCEL_REQUESTED → _finalize_cancelled() 直接返回
  │  └─ PluginWorkerRuntime.execute(operation=cancel_aware)
  ▼
PluginWorkerRuntime.execute() → WorkerRuntime.execute() → SandboxRuntime.execute() → MemorySandboxProvider.execute(operation)
  │   operation = cancel_aware = 轮询 DB cancel 标志的包装：
  │       asyncio.create_task(service.run_agent_operation(run, checkpoint))
  │       while not done: 用独立连接 poll acquisition_runs 的 CANCEL_REQUESTED；
  │                       若被取消 → operation_task.cancel(); rollback(); 抛 WorkerCancelledError
  ▼
AdaptiveDataAcquisitionAgent.acquire(request, checkpoint)
  │  - 重建 PlannerRequest（从 durable checkpoint）
  │  - HTTPAdapter.fetch() / PlaywrightAcquisitionAdapter.browse()   # 仅在 Worker/Sandbox 内触网
  │  - 每页：store.put() + _EvidenceSink.save_evidence() → EvidenceService（写入证据链）
  │  - 分页：result.pagination_page 作为续跑游标
  │  - 完整性评估 → AcquisitionResult
  ▼
service.run_agent_operation() 返回 AcquisitionRunPayload
  │  run_claimed 再次 verify_owner（提交前 Critical Gate）
  │  └─ 用独立连接再读一次 cancel 标志（避免 SQLite 快照隔离盲点）
  │  └─ _apply_payload() + service.commit()   # 仅当仍是 owner 且无取消
  ▼
AcquisitionRun 行 → COMPLETE / PARTIAL / BLOCKED / FAILED
```

**关键点**：API 层只 enqueue（QUEUED）。执行完全发生在 `AcquisitionWorkerLoop` → `run_claimed` → Worker/Sandbox 边界。所有适配器（HTTP/Browser/Store/Evidence sink）在 **Worker 操作内部** 通过 `service._build_agent()` 构造，**API 层从不构造适配器、从不触网**。

---

## D. CORE DOMAIN MODEL

所有 acquisition ORM 模型在 `backend/app/acquisition/models_db.py`，均继承 `app.database.base.Base`（`backend/app/database/base.py` 的 `UUIDPrimaryKeyMixin` + `TimestampMixin`）。

### `AcquisitionRun`（表 `acquisition_runs`）—— 核心
- PK：`id` UUID。
- 重要字段：
  - `task_id` UUID → FK `tasks.id` ON DELETE CASCADE（index）。
  - `agent_id` UUID → FK `agents.id` ON DELETE RESTRICT（index）。
  - `trace_id` str(index)。
  - `goal` Text；`target_asset` Text nullable。
  - `status` str(32) default `"PENDING"`（index）。**注意**：实际生命周期用 `"QUEUED"`/`"RUNNING"`/`"CANCELLED"` 等；`"PENDING"` 仅 `create_and_run`（legacy）使用。
  - `source_type`、`strategy`、`blocked_reason`(default `"NONE"`)、`blocked_detail`。
  - 计数/度量：`replans`、`retries`、`total_bytes`、`total_requests`、`duration_seconds`、`strategy_history`(JSON)。
  - 时间：`started_at`、`finished_at`（DateTime tz）。
- **Phase 28.1 production path**：`idempotency_key` str(128) **unique, index**（幂等键）、`request_fingerprint` str(64)、`checkpoint` JSON（续跑状态，**避免 lazy relationship**）、`worker_id`、`lease_id`、`sandbox_execution_id`、`worker_execution_id`（均为 UUID nullable）。
- **Phase 28.2 durable claim / fencing / observability**：
  - `claim_token_hash` str(64) nullable —— **仅存 fencing token 的 SHA-256，明文永不落库**（Critical Gate 要求）。
  - `claim_attempts` int default 0；`claimed_at`；`recovery_count` int default 0。
  - `cancel_requested_at`；`cancelled_at`；`stale_result_rejected` int default 0。
- 关系：`plan` → `AcquisitionPlanRecord`（one-to-one, cascade all delete-orphan）。
- **生命周期**：QUEUED → RUNNING → (COMPLETE|PARTIAL|BLOCKED|FAILED|CANCELLED)。CANCELLED/COMPLETE 等为终态（见 E）。

### 其余 acquisition 表
- `AcquisitionPlanRecord`（`acquisition_plans`）：运行期计划快照（target/source_type/strategy/steps/expected_outputs/completeness_conditions/budgets/fallback_strategy）。FK `run_id`→`acquisition_runs` CASCADE。
- `AcquisitionStepRecord`（`acquisition_steps`）：计划单步执行记录（step_id/kind/status/url/detail）。
- `AcquisitionArtifactRecord`（`acquisition_artifacts`）：证据血缘（object_key/sha256/index/content_type/source_url/final_url/http_status/etag/.../evidence_id→`evidence.id` SET NULL/duplicate_of）。
- `ExtractedDocumentRecord`（`extracted_documents`）：抽取内容元数据（title/source_url/evidence_id/artifact_sha256/extraction_backend/text_length/...）。
- `CompletenessReportRecord`（`completeness_reports`）：每 run 完整性报告（`run_id` **unique**）；coverage_score/field_completeness/time_coverage/pagination_complete/duplicates/gaps/errors/confidence/verdict。
- `PublicEndpointCandidateRecord`（`public_endpoint_candidates`）：观察/校验的公开端点（url/method/state OBSERVED|VALIDATED|REJECTED/observed_from/...）。

### 通用 Worker/Sandbox 模型（`backend/app/models/worker.py`，已迁移 phase16/17）
- `workers`（WorkerRecord）、`worker_leases`（WorkerLease：fencing_token UUID、version、status、expires_at、owner）、`sandbox_executions`（SandboxExecution：status/lease_id/lease_version/attempt/recovery_of_execution_id/terminated）、`sandbox_profiles`、`secret_references`。
- 这些被 acquisition 的 `WorkerLeaseManager` / `WorkerRuntime` 复用（acquisition **不**自创第二套租约系统——这是领域约束）。

### 枚举（纯 dataclass，`models.py`）
- `AcquisitionStatus`：PENDING/RUNNING/COMPLETE/PARTIAL/BLOCKED/FAILED。
- `SourceType`：STATIC_HTML/DYNAMIC_HTML/DOCUMENT/PUBLIC_JSON_API/UNKNOWN。
- `BlockReason`：NONE/AUTH_REQUIRED/LOGIN_PAGE/CAPTCHA/PAYWALL/ROBOTS_DISALLOWED/SSRF_BLOCKED/POLICY_VIOLATION/SIZE_LIMIT/TIMEOUT/RATE_LIMITED/MALFORMED/TOO_MANY_REQUESTS/FAILED。
- `EndpointState`：OBSERVED/VALIDATED/REJECTED。`Verdict`：FINISH/RETRY/REPLAN/PARTIAL/BLOCKED。

> ⚠️ **差异提示**：`AcquisitionStatus` 枚举里**没有 `CANCELLED`**，但 `AcquisitionRun.status` 实际会取字符串 `"CANCELLED"`（由 `worker_path._finalize_cancelled` 写入）。`TERMINAL` 常量（`worker_path.py:30`）显式包含 `"CANCELLED"`。`Cancelled` 状态是字符串而非枚举成员——修改状态比较逻辑时务必包含 `"CANCELLED"` 字面量。

---

## E. STATE MACHINES

### Run lifecycle（`acquisition_runs.status`）
```
                 create()                         claim() 原子 CAS
   (new) ───► QUEUED ─────────────────────────────────────► RUNNING
              │                                               │  ├─ COMPLETE
              │ resume()/requeue()                            │  ├─ PARTIAL
              │ (保持 checkpoint)                             │  ├─ BLOCKED
              │                                               │  └─ FAILED
   cancel() ──┤ (未 claim: claim_token_hash is None)         │
              ▼                                               │
          CANCELLED ◄───────────────────────── _finalize_cancelled()
              ▲                              (operation 已终止/回滚后)
   cancel() ──┤ (已 claim: RUNNING)                          │
              │ 先 durable CANCEL_REQUESTED                   │
              │ 等待 worker 终止 sandbox 后定稿               │
              ▼                                               │
        CANCEL_REQUESTED ──(worker 观察到 / loop 直接定稿)──► CANCELLED
```
- **transition 实现**：
  - QUEUED→RUNNING：`claim.py:claim()` 的原子 `UPDATE ... WHERE status IN (QUEUED, CANCEL_REQUESTED)`。
  - →COMPLETE/PARTIAL/BLOCKED/FAILED：`service._persist_result()` / `run_claimed._apply_payload()` + `commit()`。
  - →CANCEL_REQUESTED：`worker_path.cancel()`（已 claim 时）。
  - →CANCELLED：`worker_path._finalize_cancelled()`（用**独立 session** 写）。
  - QUEUED→QUEUED（续跑）：`service.requeue()`（reset status，保留 checkpoint）。
- **durable**：全部落 `acquisition_runs` 表，事务提交后对其他 session/进程可见。
- **terminal states**：`COMPLETE / PARTIAL / BLOCKED / FAILED / CANCELLED`（`worker_path.TERMINAL`）。
- **非法 transition 处理**：`run_claimed` 在 operation 前/后均 `verify_owner`；并发 claim 由 CAS rowcount 决定唯一赢家；cancel 与 complete 的竞态见 G。

### Worker lifecycle（`workers.status`，`worker/state_machine.py`）
- REGISTERED → {ONLINE, DEAD}；ONLINE → {BUSY, DRAINING, OFFLINE, UNHEALTHY, DEAD}；BUSY → {ONLINE, DRAINING, UNHEALTHY, DEAD}；DRAINING → {OFFLINE, DEAD}；OFFLINE → {REGISTERED, ONLINE, DEAD}；UNHEALTHY → {ONLINE, DRAINING, OFFLINE, DEAD}；DEAD → {REGISTERED}。
- 由 `WorkerRegistry.heartbeat()` 经 `validate_transition()` 强制；非法转换抛 `InvalidStateTransition`。
- **注意**：BUSY 不能直接回到 REGISTERED；必须先 ONLINE/DRAINING。状态机在 `EXPIRE/mark_stale` 路径被 `WorkerRegistry.mark_stale()` 调用（心跳超时 → UNHEALTHY → DEAD）。

### Lease lifecycle（`worker_leases.status`）
- ACTIVE → RELEASED（正常释放）/ EXPIRED（TTL 过期，由 `WorkerLeaseManager.expire()` 检测）。
- `acquire`/`renew`/`release` 均带 `version` CAS + `fencing_token` 校验（见 F）。

### Cancellation lifecycle（见 G 专章）
### Sandbox/execution lifecycle（`sandbox_executions.status`）
- RUNNING → SUCCEEDED / CANCELLED / FAILED / TIMED_OUT / RECOVERED。`WorkerRuntime._terminal_status()` 决定映射（recovered 标记：attempt>1 的 SUCCEEDED 记作 RECOVERED）。

---

## F. DURABLE QUEUE / CLAIM / LEASE / FENCING

- **queue 的 source of truth**：`acquisition_runs` 表。✅ 无内存队列；`AcquisitionWorkerLoop._next_batch()` 直接 `SELECT` 该表。**代码证实**（`claim_loop.py:129`）。
- **claim algorithm**（`claim.py:claim()`）：
  1. `session.get(AcquisitionRun)`；若 `status not in (QUEUED, CANCEL_REQUESTED)` → `AcquisitionClaimConflict`。
  2. 生成 `fencing = token or uuid4()`。
  3. **原子 CAS**：`UPDATE AcquisitionRun SET status=RUNNING, worker_id, claim_token_hash=sha256(fencing), claim_attempts+=1, claimed_at, ... WHERE id=run_id AND status IN (QUEUED, CANCEL_REQUESTED)`。**rowcount==1 即赢家**；否则 `rollback()` + `AcquisitionClaimConflict`（落败者跳过）。
  4. 随后 `WorkerLeaseManager.acquire(...)` 绑定 owner 租约（fencing_token 存于 `worker_leases`）。
  5. `commit()` + `refresh(run)`。
- **fencing token 如何生成/存储**：`claim.py:fencing_hash()` = `sha256(str(token))`；**只存 hash 到 `acquisition_runs.claim_token_hash`**，明文 token 仅流转于内存（`run_claimed` 持有）。✅ 符合"明文永不落库"。
- **fencing 如何验证**（`claim.py:verify_owner()`）：重新 `SELECT` run（带 `populate_existing=True`），校验 `run.worker_id == worker_id AND run.claim_token_hash == fencing_hash(token)`；再校验 `worker_leases` 中 `lease.status == ACTIVE`。任一不满足 → `run.stale_result_rejected += 1; commit(); raise AcquisitionStaleCommit`（**Critical Gate**）。
- **lease 创建/心跳/过期/释放**：`worker/lease.py` 的 `WorkerLeaseManager`。acquire 写 `worker_leases`；expire 由 `expire_active(now)` 把 `expires_at <= now` 的 ACTIVE 置 EXPIRED；release/renew 带 version+fencing CAS。
- **reclaim**（`claim.py:reclaim_expired()`）：仅当 `status in (RUNNING, PARTIAL)` 且旧 lease 非 ACTIVE 时，原子 `UPDATE ... WHERE status IN (RUNNING, PARTIAL)`，写入新 fencing、`recovery_count += 1`。
- **stale worker protection / stale commit protection**：`verify_owner` 在 `run_claimed` 的**入口与提交前各调用一次**（worker_path.py:121 与 :249）。提交时若 lease 已过期且被别人 reclaim，则 `AcquisitionStaleCommit` → 陈旧结果丢弃。
- **worker 所有权**：`acquisition_runs.worker_id` 指向当前 fencing owner；`lease_id` 指向 `worker_leases`。

### 并发 invariant（均由代码+测试保证）
- **「同一 run 在任一 lease epoch 最多一个有效 owner」**：由 CAS `UPDATE ... WHERE status IN (QUEUED,CANCEL_REQUESTED)` 的 rowcount 唯一性保证；并发测试 `test_phase_28_2_claim_fencing.py` 用 ≥10 真实并发连接竞争 1 个 run，断言恰好 1 个赢家。
- **「fencing 明文永不落库」**：`claim_token_hash` 唯一存储；`grep` 全仓 `claim_token` 写入 `acquisition_runs` 仅 hash。
- **「stale writer 不能提交」**：`verify_owner` Critical Gate；`claim_fencing` 测试构造过期/被 reclaim 的 token 调 `run_claimed`，断言 `AcquisitionStaleCommit`。
- **「at-least-once 尝试，per-run 恰好一次终态」**（**非** exactly-once side effect）：交付语义是"每个 run_id 最终到达一个终态且不被重复创建"（500-run benchmark 验证 500→500 零丢失零重复）；但 **单次 run 可能因崩溃恢复被执行两次**（reclaim + resume 续跑同一 run_id），每次尝试可能产生部分证据，cancel/回滚时丢弃。不要把它表述为"exactly-once execution"——应是"exactly-once terminal outcome per run_id via fencing + idempotent requeue"。

> ⚠️ **已确认但未接线的恢复 gap**：`AcquisitionWorkerLoop.tick()` 只选 `QUEUED`/`CANCEL_REQUESTED` 且**从不调用 `coordinator.reclaim_expired()`**。`_expire_stale()` 仅把 `worker_leases` 过期，但 `acquisition_runs` 中 RUNNING 行不会因此被重新选中/回收。即：一个 RUNNING 且租约过期的 run 在当前 loop 实现下会**卡在 RUNNING**（recovery 机制在 coordinator 层有实现且被测，但 loop 未接线）。详见 S/R/W。

---

## G. CANCELLATION SEMANTICS

完整追踪一次取消（`worker_path.cancel()` + `run_claimed` 的 `cancel_aware`）：

1. **API cancel request**：`POST /acquisitions/{id}/cancel` → `cancel_acquisition()` → `worker_path.cancel(run_id)`。
2. **durable CANCEL_REQUESTED**：
   - 若 `run.claim_token_hash is None`（从未 claim，无后台工作）→ 直接 `_finalize_cancelled()`，**立即** CANCELLED。
   - 若已 claim（RUNNING/PARTIAL）→ 先 `run.status="CANCEL_REQUESTED"; run.cancel_requested_at=now; run.checkpoint["status"]="CANCEL_REQUESTED"; service.commit()`（**先 durably 请求**，再终止）。
3. **worker 如何发现**：生产取消**不依赖共享内存 sandbox handle**（worker 与 API 是独立进程）。`run_claimed` 的 `cancel_aware` 用一个**独立只读连接**轮询 `acquisition_runs` 的 `CANCEL_REQUESTED`/`cancel_requested_at`（每 ~50ms，≤ 轮询间隔）。API 提交的 CANCEL_REQUESTED 因此跨进程可见。
4. **当前 operation 如何被取消**：轮询发现取消 → `operation_task.cancel()`（asyncio 取消执行中的 `run_agent_operation`）→ 捕获 `CancelledError` → `await self._service.session.rollback()` 丢弃撕裂的中间 flush（**永不提交**）。
5. **HTTP/browser/sandbox 如何终止**：
   - 若在 operation 开始前就已知 CANCEL_REQUESTED → 不启动网络工作，直接 `_finalize_cancelled`。
   - 若已启动且 `run.sandbox_execution_id` 已记录（on_start 回调写入）→ `cancel()` 调 `self._plugin.terminate(sandbox_execution_id)` → `WorkerRuntime.terminate` → `SandboxRuntime.terminate` → provider 取消底层 task（MemorySandboxProvider 取消 asyncio task；真实 Playwright 取消 browser context）。
6. **transaction 如何 rollback**：cancel 路径一律 `session.rollback()` 后由 `_finalize_cancelled` 用**独立 session** 写终态。
7. **resource 如何 cleanup**：`finally` 释放 lease（`_release_after` → `release_claim`）；sandbox 在 terminate 时关闭；浏览器 context 在 `browse()` 的 `finally: close_context` 关闭（见 K）。
8. **lease 如何释放**：`_finalize_cancelled` 用独立 session 把对应 `worker_leases` 置 `RELEASED`（best-effort）。
9. **CANCELLED 如何最终提交**：`_finalize_cancelled` 在独立 session 写 `status=CANCELLED, cancelled_at=now, checkpoint.status=CANCELLED, finished_at=now` 并 `commit()`。

### 必须回答的问题
- **是否可能先 CANCELLED 后 operation 仍在执行？** ✅ 设计上**不会**：CANCELLED 仅在 operation 已被 cancel/terminate/未启动后写入；`_finalize_cancelled` 在 `cancel_aware` 捕获取消**之后**调用。`run_claimed` 提交前还会再读一次 cancel 标志，若发现取消则放弃成功结果改定稿 CANCELLED（`worker_path.py:263`）。
- **worker 与 API 不同进程时是否仍可 cancel？** ✅ 是——靠 DB 持久标志 + worker 轮询独立连接（这是生产取消通道）。
- **polling interval 是多少？** `cancel_aware` 中 `await asyncio.sleep(0.05)`（50ms）；`on_start` 之前的操作靠 `operation_task.cancel()` 即时中断。
- **使用同 session 还是独立 session？** 轮询用 `async_sessionmaker(self._service.session.bind, expire_on_commit=False)` 的**独立连接**；终态定稿用另一个独立 session。原因见 M（SQLite 快照隔离 + 跨进程可见性）。
- **cancel 与 COMPLETE race 如何处理？** 提交前 `verify_owner` + 独立连接再读 cancel 标志；若 COMPLETE 与 CANCEL_REQUESTED 并发，先到 commit 的生效，后到者因 `run.status` 已变（verify_owner 或 cancel 检查）而走取消/丢弃分支，**不会同时写 COMPLETE 与 CANCELLED**。
- **cancel 与 evidence write race？** operation 被 cancel 时其 `run_agent_operation` 未正常完成 → `_persist_result`（写 evidence 行）**不会被调用** → 零证据写入。测试 `test_cancelled_runs_have_zero_evidence_writes` 断言 CANCELLED 后 `acquisition_artifacts` 数为 0。
- **CANCELLED 后还能否写 evidence？** ✅ 不能——`_finalize_cancelled` 只写状态字段，不调 `_persist_result`；且终态 run 不会被 loop 再选中（不在 QUEUED/CANCEL_REQUESTED）。
- **浏览器 context/process 是否释放？** ✅ 是——`browse()` 的 `finally: close_context(context)`；cancel 时 `terminate` 取消页面 task；`BrowserManager.stop()` 关闭整个 browser。测试 `test_phase_28_2_browser_reaping.py` 用真实 Chromium 跑 6 轮取消竞态断言 0 context 泄漏。

**关键实现文件**：`app/acquisition/worker_path.py`（`cancel` / `run_claimed` / `_finalize_cancelled` / `cancel_aware`）；**关键测试**：`test_phase_28_2_cancellation.py`（8 个，覆盖 CANCEL_REQUESTED→CANCELLED 矩阵、零证据、并发取消、cancel 与 complete 竞态）。

---

## H. CHECKPOINT / RESUME / IDEMPOTENCY

### Checkpoint
- **存在哪里**：`acquisition_runs.checkpoint` JSON 列（dict）。
- **schema**（`checkpoint.py:AcquisitionCheckpoint`）：`run_id / current_url / page_number / records_seen(list of {url,sha256}) / requests_used / bytes_used / evidence_refs / strategy / replan_count / visited_urls / documents_captured / status / blocked_reason / blocked_detail`。
- **transaction boundary**：`create()` 在插入 run 行时即写入初始 checkpoint（`status=QUEUED, page_number=1, current_url=plan.urls[0]`），与 run 行**同一事务**（原子）。每次 `run_agent_operation` 结束后 `AcquisitionCheckpoint(run_id).snapshot(result)` 生成新 checkpoint dict，随 payload 经 `_apply_payload` 写回（与结果同一事务）。
- **page/url/cursor 如何保存**：`agent.acquire` 用 `checkpoint.page_number` 作为分页起点（`page_start`）；`result.pagination_page` 回写 `page_number`（`agent.py:447,198`）。续跑时**主 URL 重新进入**以重建分页上下文，已抓页通过 `visited_urls`/`records_seen` 去重（`agent.py:156-178`）。
- **crash 时可能损失什么**：最后一次 `commit()` 之后的在途证据/页会丢失；但 checkpoint 已持久的部分（成功页）可续跑——**失败页会被重试**（checkpoint 只保留成功页的 visited_urls，`snapshot()` 过滤掉未进 documents 的 URL）。

### Resume
- **从哪里读取**：`service.requeue()` 保留 `run.checkpoint`；`run_claimed` → `AcquisitionCheckpoint.from_dict(run.checkpoint)` → `run_agent_operation` → `agent.acquire(request, checkpoint=...)`。
- **如何重新生成 request**：`service._planner_request_from_state(run, state)` 从 checkpoint 的 `current_url/expected_fields/...` 重建 `PlannerRequest`。
- **是否会从 page 1 重跑**：✅ **不会**——`page_start = checkpoint.page_number`，从游标续跑。
- **requeue 是否保留 checkpoint**：✅ 是（`requeue()` 只改 `checkpoint["status"]="QUEUED"` 与 `run.status`，不清空游标）。

### Idempotency
- **key 存在哪里**：`acquisition_runs.idempotency_key`（**unique** 约束，index）。
- **唯一性如何实现**：`create()` 在有 `idempotency_key` 时先 `SELECT ... WHERE idempotency_key==key`；命中则比对 `request_fingerprint`：相同 → 返回已有 run（`created=False`）；不同 → 抛 `AcquisitionConflict`（409，key 被复用为不同请求）。
- **相同 key + 相同 request**：返回已有 run（幂等）。
- **相同 key + 不同 request**：409 Conflict。
- **跨进程/跨 session 是否有效**：✅ 是——基于 DB 唯一约束 + 事务内 SELECT，任何 session 都可见（测试用独立 session 验证 durable）。`request_fingerprint` = `sha256(json(goal,url,target_asset,expected_fields,expected_time_range,expected_record_count))`（`service._request_fingerprint`）。

---

## I. WORKER ARCHITECTURE

- **registration**：`WorkerRegistry.register(WorkerRecord)` → 写 `workers` 表（status=REGISTERED），幂等（同名返回已有）。
- **selection**：`WorkerScheduler.select(capability)` → 从 DB `list()` 过滤 `status in (ONLINE,BUSY) AND capability in capabilities AND active_executions < max_concurrency`，按 `(active/max, last_heartbeat, id)` 选最闲。
- **capability**：`WorkerRecord.capabilities`（frozenset）。acquisition 的 capability 字符串为 `"acquisition.http"`（`AcquisitionWorkerPath.CAPABILITY`，`worker_path.py:60`）。⚠️ 测试 worker 必须注册 `"acquisition.http"` 才能被 scheduler 选中——这是历史会话中一个踩坑点。
- **scheduler**：见上；在 `WorkerRuntime.execute` 与 `PluginWorkerRuntime._execute_synthetic` 中使用。
- **max concurrency**：`WorkerRecord.max_concurrency`（1..1024）；心跳 `active_executions` 超 `max_concurrency` 抛 `WorkerConflict`。
- **runtime**：`WorkerRuntime.execute()` 选 worker → `WorkerLeaseManager.acquire` → 心跳 BUSY → `SandboxRuntime.execute(operation)` → 提交 `sandbox_executions` 历史（带 fencing）→ 释放 lease → 心跳回 ONLINE/DRAINING。`finally` 中 tolerate broken transaction 并 best-effort release。
- **plugin runtime**：`PluginWorkerRuntime` 跨边界序列化结果；`synthetic()` 用内存 SQLite + `MemorySandboxProvider` 做**合成控制面**（无 OS 隔离）。API 的 `get_acquisition_worker_path` 用它仅做 cancel/terminate  plumbing。
- **execution flow**：`request` → `WorkerRuntime` → `SandboxRuntime` → `MemorySandboxProvider.execute(operation)`（真正跑 `operation()`，即 acquisition 的 `cancel_aware`）。
- **heartbeat**：`WorkerRegistry.heartbeat(WorkerHeartbeat)`；`AcquisitionWorkerLoop.heartbeat()` 在循环存活时维持 ONLINE/DRAINING。`WorkerRegistry.mark_stale()` 依心跳超时转 UNHEALTHY/DEAD。
- **lease**：`WorkerLeaseManager`（acquisition 复用）；TTL 默认 120s（`WorkerRuntime`/`AcquisitionClaimCoordinator` 均默认 `lease_ttl_seconds=120`）。
- **failure handling**：`WorkerRuntime` 重试循环（retry_limit）；`SandboxRuntime` 超时/终止 → `SandboxExecutionStatus`；异常经 `_platform_error_types()` 映射回具体 `PlatformError` 子类。
- **shutdown / backpressure**：`AcquisitionWorkerLoop` 用 `request_shutdown()`（协作停止：置 draining + 设置 `_shutdown` Event）、`drain()`（等 in-flight 完成）、`run_forever()`（每 tick 后 `sleep(poll_interval)`，空队列也不忙等 → **有界轮询**）。`batch_size` 限制每 tick 最多 claim N 个。`poll_interval` 默认 0.05，`batch_size` 默认 5。

---

## J. SANDBOX AND SECURITY BOUNDARIES

- **SandboxRuntime**（`sandbox/runtime.py`）：执行前 `policy.validate(profile, provider_name)`；若 profile 要求 network/filesystem/secret/timeout 而 provider 不支持 → `SandboxExecutionError`（fail-closed）。校验返回结果的 execution identity 一致性。
- **SandboxPolicyEngine**（`sandbox/policy.py`）：`SandboxPolicy`（默认 `allow_network=False`、`allow_host_filesystem_write=False`、`allowed_providers={"memory-sandbox"}`）。任何超限 → `SandboxPolicyViolation`。
- **capability checks**：`WorkerScheduler` 按 capability 选 worker；`PluginExecutionRequest.capability` 必须命中。
- **SSRF protection**（`acquisition/urlpolicy.py:URLPolicyValidator`）：**必须经过的 Critical Gate**。
  - scheme 白名单（仅 http/https；file/ftp/gopher/data/javascript/unix socket 拒绝）。
  - userinfo（`user:pass@`）拒绝。
  - 主机检查：localhost/loopback/RFC1918 私有/link-local/metadata(169.254.169.254)/IPv6 ::1/`.internal`/`.local`/`.localhost`/云 metadata 主机名均拒绝。
  - **DNS rebinding 防御**：解析出的每个 IP 也必须为公开地址（`_is_private_ip`）。
  - 重定向目标**再次校验**（`validate_redirect`）。
  - `allow_private=True` 才能放开私有地址——仅 lab 显式 opted-in 的 `URLPolicyValidator(allow_private=True)` 实例（测试用），**绝不被生产路径复用**（生产 `AcquisitionService` 默认 `URLPolicyValidator()` 即 `allow_private=False`）。
- **private IP handling**：默认全拒；lab 变体（`lab_url_validator()` 测试辅助）显式 `allow_private=True`。
- **localhost lab exception**：仅存在于测试构造的 lab validator；生产 `create()` 用默认 validator，仍拦私有/localhost。
- **browser sandbox**：`PlaywrightAdapter.validate` 强制仅公开 http(s) GET，禁止 cookie/header/credential/proxy 注入（`adapter.py:28-40`）。`PlaywrightAcquisitionAdapter` 只记录同源 XHR/Fetch 且过滤 `/admin|/internal|/debug|/api/v1/private|/manage|/console` 等隐藏端点（`browseradapter.py:27,115`）。
- **filesystem / process / network restrictions**：当前 provider 为 `MemorySandboxProvider`（`real_isolation=False`，`capabilities.network=False`）。**注意**：这意味着当前 acquisition 在内存 provider 下**并不真正触网隔离**——真实网络获取由 `HTTPAdapter` 内部的 `httpx` 完成，`httpx` 受 `URLPolicyValidator` 约束，而非由 sandbox provider 的 network 能力约束。生产中若要真隔离需接入支持 network 的 provider（OCI/gVisor/microVM，见 `SandboxProvider` Protocol 注释），但当前仓库未提供该实现。
- **evidence / security handling**：原始字节经 `EvidenceService.save_object` 落库，evidence SHA-256 == artifact SHA-256 == object-store key（`service._EvidenceSink`），血缘可端到端校验。
- **secrets/token storage**：`sandbox/secret.py` 提供 secret 引用机制；`PluginExecutionRequest.secret_references`。acquisition 当前未使用 secret。`claim_token` 明文**不落库**（见 F）。

### Critical Gate（必须经过，不可绕过）
1. **SSRF**：每个初始 URL、重定向 hop、DNS 解析后都必须过 `URLPolicyValidator`（HTTPAdapter + PlaywrightAdapter）。
2. **Fencing**：只有当前 owner 且租约 ACTIVE 才能提交 run 结果（`verify_owner`）。
3. **Sandbox policy**：profile 超限 / provider 不在 allowlist → 拒绝。
4. **Worker capability**：scheduler 按 capability 选 worker，无对应 capability 的 worker `WorkerUnavailable`。
5. **lab validator 隔离**：生产 validator 永不被 lab 实例污染（`test_phase_28_2_security_regression.py` 断言 `prod is not lab_val`）。

### 绕过路径检查
- API **无** `create_and_run` 端点、无 `run-now`/`execute-now`/`debug`/`sandbox-bypass`（`acquisition.py` 路由仅 create/list/get/resume/cancel/evidence/completeness）。
- `AcquisitionWorkerPath.execute()`（legacy 28.1 直跑）存在但**无路由调用**；`run_claimed` 是 28.2 生产路径。
- `create_and_run`（`service.py:312`）标 `@deprecated`，走 PENDING 同步路径，**不应重新启用**（见 L/R）。

---

## K. HTTP AND BROWSER EXECUTION

### HTTP acquisition（`httpadapter.py:HTTPAdapter`）
- **唯一真实 HTTP I/O 点**（agent 不直接 import httpx）。
- **timeout**：`httpx.Timeout(policy.timeout_seconds)`（默认 30s）；`follow_redirects=False`（手动处理重定向以逐跳校验）。
- **redirect**：逐跳 `validate_redirect`；超 `redirect_limit`（默认 5）fail-closed。
- **SSRF**：`fetch()` 首步 `validate_url`；每 hop 再校验；不通过 → `blocked_reason=SSRF_BLOCKED`。
- **response handling**：401/403 → 抛 `RestrictedAccessError(AUTH_REQUIRED)`（**不绕过**）；content-type/size 限制；压缩炸弹防护（`_guard_size` 拒绝压缩体、超限膨胀体）；captcha/login/paywall 标记 → `BLOCKED`。
- **cancellation**：HTTP 层本身无异步取消；取消由外层 `cancel_aware` 取消整个 operation task 实现（HTTP 请求在 task 取消时被中断）。

### Browser acquisition（`browseradapter.py` + `tools/playwright`）
- **Playwright**：`PlaywrightAcquisitionAdapter.browse()` 经 `BrowserManager.new_context()` → `page.goto` → 捕获 DOM + 同源 XHR/Fetch 端点。
- **Chromium**：`BrowserManager.start()` 用 `playwright.async_api` 启动 headless Chromium。
- **context lifecycle**：`browse()` 内 `new_context()`，成功/失败均 `finally: close_context(context)`（**context 必释放**）。
- **process reaping**：`BrowserManager.stop()` 关所有 context + browser + playwright；`shutdown()` 调 `stop()`。cancel 时 `terminate` 取消 page task。
- **resource tracking**：`BrowserManager._contexts` 集合；`_count_live_contexts`（测试用）读该集合。测试断言取消竞态下 context 数回到基线 0（真实 Chromium）。
- **cancellation**：`cancel_aware` 取消 operation task → `browse()` 的 asyncio task 被取消 → 页面 task 取消；`finally` 仍关 context。
- **synthetic/lab 隔离**：`URLPolicyValidator(allow_private=True)` 仅测试 lab 构造；生产 `AcquisitionService` 用默认（拦私有）。Playwright 本身 `validate` 禁止 cookie/header/credential/proxy——与生产 policy 同源约束。

---

## L. API SURFACE

路由前缀 `/acquisitions`（`api/routes/acquisition.py`）。✅ **纯 enqueue 边界，无 bypass/run-now/execute-now/create_and_run 端点**。

| METHOD PATH | 用途 | service / worker 函数 | 状态码 |
|---|---|---|---|
| `POST /acquisitions` | 创建 run（QUEUED），返回 202 | `service.create(...)` | **202** |
| `GET /acquisitions` | 分页列表 | `session` 直接查 | 200 |
| `GET /acquisitions/{id}` | 获取 run 详情 | `session.get` | 200 / 404 |
| `POST /acquisitions/{id}/resume` | 重新入队（保留 checkpoint） | `service.requeue(...)` | **202** / 409(已终态) / 404 |
| `POST /acquisitions/{id}/cancel` | 请求取消 | `worker_path.cancel(...)` | **202** / 404 |
| `GET /acquisitions/{id}/evidence` | 列出 artifact 血缘 | `session` 查 `acquisition_artifacts` | 200 / 404 |
| `GET /acquisitions/{id}/completeness` | 完整性报告 | `session` 查 `completeness_reports` | 200 / 404 |

- **request schema**（`AcquisitionCreateRequest`）：goal(str 1..1000), url(str 1..2000), target_asset, expected_fields(list), expected_time_range(list), expected_record_count(int?), idempotency_key(str? ≤128)。
- **response schema**：`AcquisitionCreateResponse`(id/status/goal/source_type/strategy/blocked_reason/blocked_detail)；list 返回 `AcquisitionSummary` 分页。
- **backpressure**：`_backpressure_guard()` 在 `create` 前调用；若 `policy.max_queued_runs>0` 且 pending≥limit → **503**。
- **authentication/authorization**：本路由文件**未见** RBAC/依赖注入 auth（对比 README 声称的全平台 RBAC）。⚠️ 需确认：acquisition 路由当前**未挂载认证依赖**（可能依赖更外层中间件，或未实现）。属于未确认项，见 V。
- **deprecated**：`service.create_and_run()` 标 `@deprecated`（Scheduled for removal in v2.0），**无对应 API 端点**，不应重新启用。

---

## M. DATABASE / TRANSACTIONS

- **使用什么数据库**：生产目标 **PostgreSQL**（`app/config/settings.py:26`：`postgresql+asyncpg://cap:cap@postgres:5432/cap`）；驱动 `asyncpg` 已声明。测试用 **SQLite in-memory**（`tests/conftest.py:25`：`sqlite+aiosqlite://`）。
- **SQLAlchemy**：async（`sqlalchemy.ext.asyncio`）。`AsyncSessionFactory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)`（`session.py:16`）。
- **session lifecycle**：每请求 `get_db_session()` yield 一个 session（API）；测试中 conftest 用 module/function 级 fixture 提供 `session`（StaticPool 共享连接）。
- **isolation assumptions**：acquisition 的取消可见性**关键依赖"独立连接读取已提交状态"**：`run_claimed.cancel_aware` 与提交前检查均用 `async_sessionmaker(self._service.session.bind, expire_on_commit=False)` 新建连接轮询/校验（worker_path.py:163,258）。代码注释明确说明这是为规避 **SQLite 快照隔离 / 任何 MVCC 存储** 的会话级快照盲点（worker_path.py:251-255）。
- **NullPool/StaticPool**：测试 conftest 用 **StaticPool**（单连接共享 in-memory DB）；生产 engine 用默认 pool（非 StaticPool，`pool_pre_ping=True`）。⚠️ 不要在生产沿用 StaticPool。
- **atomic UPDATE / CAS**：`claim.py` 的 claim/reclaim 用单条 `UPDATE ... WHERE status IN (...)` + `rowcount` 判定（见 F）。
- **transaction boundaries**：
  - `create()`：插入 run + plan 同一事务，`flush` 后返回（由调用方/API 提交）。
  - `claim()`：CAS UPDATE + lease acquire 同事务，`commit`。
  - `run_claimed`：operation 在独立 task；提交前 `verify_owner`；结果 `_apply_payload` + `commit`；取消/异常统一 `rollback` 后由 `_finalize_cancelled` 用**独立 session** 写终态。
- **rollback strategy**：取消/陈旧提交路径 `session.rollback()` 丢弃撕裂中间态；终态走独立 session 提交（避免依赖可能已损坏的 worker session 快照）。
- **SQLite 特殊处理**：测试用 SQLite；跨 session 取消靠独立连接。⚠️ **不可把 SQLite 测试结果直接推广为 PostgreSQL 保证**——PostgreSQL 的隔离语义更强，但 cancellation 的正确性**同时**依赖"独立连接读已提交"这一代码层面保证（两库都成立）。fencing/claim 的并发正确性在 SQLite 下用真实多连接已验证，PostgreSQL 下预期等价（CAS 语义两库一致）。
- **PostgreSQL compatibility**：模型用 `Uuid(as_uuid=True)`、`JSON`、`DateTime(timezone=True)`、`server_default=func.now()`——均兼容 Postgres；无 SQLite-only 类型。

---

## N. OBSERVABILITY

- **run observability fields**（`acquisition_runs`）：`claim_attempts`、`claimed_at`、`recovery_count`、`cancel_requested_at`、`cancelled_at`、`stale_result_rejected`、`worker_id`、`lease_id`、`sandbox_execution_id`、`worker_execution_id`、`total_bytes/requests/duration/strategy_history`。
- **worker statistics**：`AcquisitionWorkerLoop.LoopStats`（claimed/reclaimed/completed/cancelled/skipped_terminal/stale_rejected/errors）；`WorkerRegistry`/`WorkerLeaseManager` 经 `app.events.transactional.publish_audit` 发审计事件（WORKER_*/SANDBOX_EXECUTION_*）。
- **recovery statistics**：`recovery_count`（coordinator.reclaim_expired 递增）；`stale_result_rejected`（verify_owner 拒绝陈旧写入时递增）。
- **logs/metrics/traces**：`observability.py` 定义 `RunTracker`/`AcquisitionRunRecord`（结构化每 run 观测），但**当前未在 acquisition 执行链中实际实例化/持久化**（agent/worker_path 未使用 `RunTracker`）——属于"定义存在、接线缺失"。
- **observability 缺口**：
  - 无 Prometheus/OpenTelemetry metrics 端点（README 提及 telemetry 子系统，但 acquisition 未接入）。
  - `LoopStats` 仅在测试中断言，无运行时导出。
  - `RunTracker` 未被调用。
  - 无 run 级结构化日志（执行链主要靠 `httpx`/INFO 日志与审计事件）。

---

## O. TEST ARCHITECTURE

测试目录 `backend/tests/`（`pyproject.toml`：`asyncio_mode="auto"`，`testpaths=["tests"]`，`filterwarnings=[error, ignore::DeprecationWarning]`）。

### acquisition 相关测试（按类别）
- **unit / integration**：`test_phase_28_1_worker_path.py`(18) — Worker/Sandbox 边界执行 + 状态机。
- **concurrency / race**：
  - `test_phase_28_2_claim_fencing.py`(9) — 原子 claim 并发（≥10 连接争 1 run）、fencing 拒绝陈旧提交、reclaim_expired、recovery_count。
  - `test_phase_28_2_cancellation.py`(8) — 取消矩阵、零证据、cancel×complete 竞态、并发取消。
- **security**：`test_phase_28_2_security_regression.py`(6) — 生产 SSRF 仍拦私有、lab validator 隔离、无 capability worker 不可执行。
- **browser / resource**：`test_phase_28_2_browser_reaping.py`(1) — 真实 Chromium 6 轮取消竞态 0 context 泄漏。
- **durability / benchmark**：`test_phase_28_2_500_benchmark.py`(1) — 500 run 经 claim loop 全量终态、零丢失零重复。
- **checkpoint / idempotency / resume**：`test_phase_28_2_checkpoint_resume.py`(8) — idempotency_key 跨 session durable、checkpoint 与 run 行原子、requeue 续跑游标。
- **backpressure / observability**：`test_phase_28_2_backpressure_observability.py`(7) — 有界轮询、batch 限流、max_concurrency、claim_attempts/recovery_count durable。
- **legacy / architecture**：`test_phase_28_2_legacy_architecture.py`(6) — create_and_run 触发 DeprecationWarning、API 无 create_and_run/run-now 端点、依赖方向。
- **28.1 其余**：`test_phase_28_1_integrity_hybrid.py`(6)、`test_phase_28_1_playwright_real.py`(6)（真实 Playwright 100 次无泄漏）。

### 实测计数（与代码一致）
- **Phase 28.2 八套件合计 = 46**：cancellation 8 + claim_fencing 9 + checkpoint_resume 8 + legacy_architecture 6 + backpressure_observability 7 + security_regression 6 + browser_reaping 1 + 500_benchmark 1。
- 其中 **6 个核心套件 = 44**（不含 browser_reaping、500_benchmark，二者单独运行）。
- **Phase 28.1 三套件 = 30**：worker_path 18 + integrity_hybrid 6 + playwright_real 6。
- acquisition 28.x 合计 = **76** 个 test cases。
- 仓库另有大量 `test_phase_2_*`…`test_phase_27_*` 等非 acquisition 测试（其他子系统），未在此列举。

### 与报告数字一致性
- 认证报告称"44/44 全部通过"= 上述 6 核心套件；"46" = 含 browser_reaping + 500_benchmark。✅ 与代码实际 test 数一致。

---

## P. HOW TO RUN

> 以下命令基于仓库实际配置（Makefile / pyproject / README / conftest）。未确认项标注。

### 依赖安装
```bash
# 推荐 uv（README 用法）
uv sync --project backend           # 或 uv run --project backend <cmd>
# 或本地 venv
cd backend && python -m venv .venv && .venv/Scripts/python -m pip install -e .
```
依赖见 `backend/pyproject.toml`（asyncpg/fastapi/sqlalchemy[asyncio]/playwright/uvicorn/pytest/pytest-asyncio/pytest-cov/aiosqlite）。

### 数据库 / migration
```bash
# 生产：PostgreSQL（docker compose 提供 postgres 服务）
make migrate        # = cd backend && alembic upgrade head
```
⚠️ **重要**：`alembic/versions/` 含 20 个迁移（最新 phase26），**均不含 acquisition 表**（grep `acquisition_runs` 命中 0）。测试靠 `Base.metadata.create_all` 建表；**生产部署前必须新增 acquisition 表的 alembic 迁移**（见 Q/R/W）。worker/sandbox 表（workers/worker_leases/sandbox_executions/...）已在 phase16/17 迁移中。

### 启动 backend
```bash
cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000
# 或 docker compose： make up   （docker-compose.yml 含 postgres + backend）
```
⚠️ **worker 守护进程未实现**：当前**没有**启动 `AcquisitionWorkerLoop.run_forever()` 的进程/服务/Dockerfile 服务（见 W）。`make up` 只起 API + Postgres；acquisition run 创建后**不会被任何已部署进程执行**（除非手动接一个 worker 进程）。

### 运行测试
```bash
cd backend
pytest                                            # 全量（Makefile: make test）
pytest tests/test_phase_28_2_cancellation.py tests/test_phase_28_2_claim_fencing.py \
      tests/test_phase_28_2_checkpoint_resume.py tests/test_phase_28_2_legacy_architecture.py \
      tests/test_phase_28_2_backpressure_observability.py tests/test_phase_28_2_security_regression.py \
      -p no:cacheprovider                          # 6 核心套件（44）
pytest tests/test_phase_28_2_browser_reaping.py -p no:cacheprovider   # 真实 Chromium（需 playwright install chromium）
pytest tests/test_phase_28_2_500_benchmark.py   -p no:cacheprovider   # 500-run 基准（较慢）
pytest tests/test_phase_28_1_worker_path.py     -p no:cacheprovider   # 28.1 回归
```
⚠️ 运行测试需设 `CODEBUDDY_SAFE_DELETE_SANDBOX=0`（本机回收 guard 会拦截 pytest 临时目录清理；历史会话因此多次中断）。非本机环境可能不需要。
⚠️ 真实 Playwright 测试需 `playwright install chromium`；否则 adapter `available=False` 退化为合成契约。

### coverage
```bash
cd backend
.venv/Scripts/python -m pytest tests/test_phase_28_2_*.py \
   --cov=app.acquisition --cov-report=term-missing -p no:cacheprovider
```
⚠️ coverage `.coverage` 数据文件本机会被回收 guard 拦截；需用 pytest-cov 内存收集或把 data_file 指到 C 盘 Temp（历史会话踩坑）。`app.acquisition` 实测总覆盖 ~59%；核心执行链：models_db 100%、checkpoint 98%、service 95%、claim 89%、claim_loop 80%、httpadapter 73%、agent 66%。

### 环境变量
- 未见 `.env.example` 内容（`backend/.env.example` 为空/缺失）；DB URL 来自 `app/config/settings.py` 默认值或环境变量 `DATABASE_URL`（settings 读取方式未深入确认）。
- `CODEBUDDY_SAFE_DELETE_SANDBOX=0`（本机测试用，见上）。

---

## Q. CURRENT IMPLEMENTATION STATUS

基于代码+测试（非宣传文档）：

**Implemented（已落地且测试覆盖）**
- Acquisition 持久化模型（7 张表）+ `AcquisitionService.create/enqueue/requeue/_persist_result`。
- 原子 Claim + Fencing（`AcquisitionClaimCoordinator`）+ `WorkerLeaseManager` 复用。
- `AcquisitionWorkerLoop`（有界轮询、batch、shutdown/drain）。
- 真正取消（CANCEL_REQUESTED → 终止 → CANCELLED，跨进程，零证据）。
- Checkpoint 续跑（游标、去重、requeue 保留 checkpoint）。
- Idempotency（唯一键 + fingerprint，跨 session）。
- SSRF（URLPolicyValidator）+ Sandbox policy（fail-closed）+ Playwright 受控浏览器。
- 背压（`max_queued_runs` 503）+ 可观测字段（claim_attempts/recovery_count/stale_result_rejected）。
- Legacy `create_and_run` 标 deprecated。

**Partially implemented（部分）**
- **Crash recovery（reclaim）**：coordinator 层 `reclaim_expired` 已实现且被测，但 **`AcquisitionWorkerLoop.tick()` 未调用它** → RUNNING+过期租约的 run 会卡住（见 F/S）。
- **Worker 守护进程**：loop 已实现但**无生产启动器**；acquisition run 创建后无进程执行（见 W）。
- **Observability 接线**：`RunTracker`/`LoopStats` 定义存在，但未接入运行时导出/metrics（见 N）。

**Deprecated**
- `AcquisitionService.create_and_run`（`@deprecated`，PENDING 同步路径，Scheduled for removal in v2.0）。

**Not implemented（确认缺失）**
- acquisition 表的 alembic 迁移（生产建表未覆盖）。
- 真实 OS 隔离 sandbox provider（当前仅 `MemorySandboxProvider`，`real_isolation=False`）。
- acquisition 路由的认证依赖（未见 RBAC 挂载，未确认）。
- Redis 在 acquisition 路径未使用（README 称后端含 Redis；acquisition 持久队列用 DB，非 Redis）。

**Known technical debt**：见 S。

**Phase 28.1 / 28.2 实际完成度**
- 28.1：Worker/Sandbox 边界执行 + 状态机 + 真实 Playwright 无泄漏，已认证（30 tests）。
- 28.2：DB 即真相的持久队列 + 原子 Claim + Fencing + 真正取消 + Checkpoint/Idempotency + 背压/可观测 + 安全回归 + 500-run 基准，已认证（46 tests，覆盖核心模块 59%+）。两者**代码与测试均存在**；唯一缺口是"生产接线"（loop 守护进程 + 迁移 + reclaim 接线）。

---

## R. CRITICAL INVARIANTS

| Invariant | Implementation location | Test protecting it |
|---|---|---|
| DB 是持久队列唯一 source of truth（无内存队列） | `claim_loop._next_batch` SELECT 表 | `claim_fencing` / `500_benchmark` |
| API create 不直接执行获取 | `acquisition.py:create_acquisition` 仅 `service.create` 返回 202 | `legacy_architecture` |
| queued run 必须经 worker claim | `claim_loop._claim_and_run` → `coordinator.claim` | `worker_path`/`claim_fencing` |
| 同一 lease epoch 最多一个有效 owner | `claim.py` CAS `UPDATE ... WHERE status IN (...)` rowcount | `claim_fencing`(并发争 1) |
| fencing 明文永不落库 | `claim_token_hash` 仅存 sha256 | `security_regression`/`claim_fencing` |
| stale worker 不能提交结果 | `claim.verify_owner` → `AcquisitionStaleCommit` | `claim_fencing`(陈旧 token) |
| active lease 不能被 reclaim | `reclaim_expired` 检查 lease 非 ACTIVE | `claim_fencing` |
| 取消必须先请求→终止 operation→最后写 CANCELLED | `worker_path.cancel`/`_finalize_cancelled`/`cancel_aware` | `cancellation` |
| CANCELLED 后不得产生 evidence | `_finalize_cancelled` 不调 `_persist_result`；operation 被 cancel 前未完成 | `cancellation: zero_evidence` |
| checkpoint 与 run 行事务一致 | `service.create` 同事务写 checkpoint；`_apply_payload` 同事务 | `checkpoint_resume` |
| resume 必须使用 cursor（不从 page1） | `agent.acquire` 用 `checkpoint.page_number` | `checkpoint_resume` |
| 生产 SSRF policy 不被 lab 污染 | 默认 `URLPolicyValidator()` vs 测试 lab 实例 | `security_regression`(`prod is not lab_val`) |
| worker capability 不可绕过 | `WorkerScheduler.select` 过滤 capability | `security_regression`(无 capability → WorkerUnavailable) |
| browser context 必须释放 | `browseradapter.browse` finally close_context | `browser_reaping` |
| API 无 bypass/run-now/create_and_run 端点 | `acquisition.py` 路由集合 | `legacy_architecture` |

> 注："exactly-one claim owner" 指**每 epoch 唯一 owner**（由 CAS 保证）；"exactly-once execution" 不成立——单次 run 可能因 reclaim 被执行两次（同一 run_id 续跑），仅终态唯一。勿混淆两者。

---

## S. KNOWN RISKS / TECHNICAL DEBT

**Confirmed issue（代码确认）**
1. **Worker loop 未接线 reclaim**：`AcquisitionWorkerLoop.tick()` 只选 QUEUED/CANCEL_REQUESTED，从不调 `coordinator.reclaim_expired()`。RUNNING 且租约过期的 run 会卡在 RUNNING（recovery 能力在 coordinator 层存在但 loop 未用）。→ 生产前必须接 `reclaim_expired`（在 `_expire_stale` 之后，把过期 RUNNING run 重新入队或 reclaim）。
2. **acquisition 表无 alembic 迁移**：20 个迁移均不含 acquisition 表；生产 `alembic upgrade head` 不会建这些表，测试靠 `create_all`。→ 部署前需 `alembic revision --autogenerate` 并为 acquisition 表生成迁移。
3. **无 worker 守护进程**：`AcquisitionWorkerLoop` 仅在其模块被引用；无 `scripts/worker.py`、无 Dockerfile worker 服务、无 `main.py` 启动。→ acquisition run 创建后无进程执行。
4. **`AcquisitionStatus` 枚举无 CANCELLED**：状态字符串 `"CANCELLED"` 游离于枚举外（`TERMINAL` 常量含它）。→ 修改状态逻辑须包含字面量，易遗漏。

**Potential risk（潜在风险）**
5. **SQLite 测试 → PostgreSQL 推广**：并发/fencing 在 SQLite 多连接下验证；PostgreSQL 下 CAS 等价，但隔离细节（如 `populate_existing` 行为）需 PostgreSQL 实测。
6. **`MemorySandboxProvider.real_isolation=False`**：当前 acquisition 在内存 provider 下不真隔离网络；真实隔离依赖未来 provider 实现 + SandboxPolicy.allow_network。
7. **`RunTracker`/`LoopStats` 未接线运行时**：可观测性定义存在但未导出，生产排障缺指标。
8. **cancel 轮询独立连接对 PostgreSQL 连接数的影响**：每 run 每 50ms 开短连接轮询，高频取消场景可能压连接池（需连接池调优/改 pub-sub）。
9. **`filterwarnings=[error]`**：测试中警告即失败；`create_and_run` 的 `@deprecated` 在 import 时可能触发 DeprecationWarning（已 ignore），但其他弃用路径需留意。

**Documentation-only concern**
- README 声称"PostgreSQL 是 durable source of truth；Workers use lease/fencing"——与代码一致；但 README 称后端含 Redis，acquisition 路径未用 Redis（持久队列在 DB）。
- README 声称全平台 RBAC/审批——acquisition 路由未见 auth 依赖（未确认是否外层中间件统一处理）。

**Low coverage / untested paths**
- `agent.py` 66%、`httpadapter.py` 73%——大量分支（各类 BlockReason、压缩炸弹、重定向预算）未在 28.2 套件全覆盖。
- reclaim 在 loop 层的缺失路径未被集成测试覆盖（因为 loop 没接）。
- PostgreSQL 端到端（含真实 worker 守护进程）无测试。

---

## T. RECENT DEVELOPMENT HISTORY

⚠️ **无法从 git 确认**：`main` 分支无 commit，仓库为未提交工作树。以下为基于代码/测试/报告/注释的**现状反推**（非 git 历史）：

- **Phase 16/17（Plugin/Sandbox/Worker Framework）**：引入 `worker/`、`sandbox/`、`tools/playwright`、`models/worker.py`；迁移 phase16/17 建 workers/worker_leases/sandbox_executions 等表。这是 acquisition 复用的底座。
- **Phase 25–27（Agent Engine / Intelligence / Hybrid）**：`agent.py`、`dataset.py`、`evaluation.py`、`report_v2.py`、`planner.py`、`completeness.py` 等获取智能核心成型（大量 `test_phase_25_*`/`test_phase_27_*`）。
- **Phase 28.1（Acquisition Production Path）**：新增 `app/acquisition/`（service/models_db/models/worker_path/httpadapter/browseradapter/urlpolicy/checkpoint/...），把获取从同步直跑改到 Worker/Sandbox 边界；API `POST /acquisitions` 返回 202；`create_and_run` 保留为 deprecated 兼容。
- **Phase 28.2（Durable Execution）**：新增 `claim.py`（`AcquisitionClaimCoordinator`）、`claim_loop.py`（`AcquisitionWorkerLoop`）、`worker_path.run_claimed`/`cancel`/`_finalize_cancelled`、models_db 的 fencing/observability 字段；重写取消为"先请求→终止→定稿"；引入 idempotency_key 唯一约束；500-run 基准 + 安全回归 + 浏览器回收测试。
- **HEAD 核心变化（工作树现状）**：28.2 全套实现 + 8 测试套件（46 tests）+ 28.1 回归（30 tests）+ 21 章认证报告。期间修复的真实缺陷（历史会话记录）：`claim_loop.stats.claimed` 未递增、`_finalize_cancelled` 用独立 session 提交、`cancel_aware` 用独立连接读取消标志（规避 SQLite 快照隔离）。

---

## U. NEXT-DEVELOPER GUIDE

**1. 开始前最该读的 10–20 个文件**
- `backend/app/acquisition/service.py`（enqueue/持久化）
- `backend/app/acquisition/claim.py`（原子 claim + fencing + reclaim）
- `backend/app/acquisition/claim_loop.py`（持久队列 Worker）
- `backend/app/acquisition/worker_path.py`（执行/取消/终态定稿——最核心）
- `backend/app/acquisition/models_db.py`（7 张表 schema）
- `backend/app/acquisition/models.py`（枚举 + policy）
- `backend/app/acquisition/checkpoint.py`（续跑状态）
- `backend/app/acquisition/urlpolicy.py`（SSRF）
- `backend/app/worker/lease.py` + `registry.py` + `scheduler.py` + `state_machine.py`（Worker 底座）
- `backend/app/sandbox/runtime.py` + `policy.py`（沙箱边界）
- `backend/app/api/routes/acquisition.py`（API 边界）
- `backend/app/database/session.py` + `config/settings.py`（DB/连接）
- `backend/tests/conftest.py`（测试 DB 如何建）
- `backend/tests/test_phase_28_2_*.py`（期望行为的事实标准）

**2. 改 Acquisition 执行链必须一起检查**：`service.run_agent_operation` ↔ `worker_path.run_claimed` ↔ `claim_loop._claim_and_run` ↔ `claim.verify_owner` ↔ `models_db` 字段；任何一端改了状态机/字段都要同步。

**3. 改 DB model 后**：加 alembic 迁移（`alembic revision --autogenerate -m "acquisition ..."` 并 review 生成的 upgrade/downgrade）；同步更新 `models_db.py` 与任何 `Base.metadata.create_all` 测试；跑 `test_phase_28_2_*` + `test_phase_28_1_worker_path`。

**4. 改 cancellation 后必须跑**：`pytest tests/test_phase_28_2_cancellation.py tests/test_phase_28_2_browser_reaping.py -p no:cacheprovider`（含零证据、并发取消、真实 Chromium 回收）。

**5. 改 fencing 后必须跑**：`pytest tests/test_phase_28_2_claim_fencing.py -p no:cacheprovider`（并发 claim、陈旧 token 拒绝、recovery_count）。

**6. 改 browser runtime 后必须跑**：`pytest tests/test_phase_28_2_browser_reaping.py tests/test_phase_28_1_playwright_real.py -p no:cacheprovider`（真实 Chromium 资源泄漏）。

**7. 改 security policy 后必须跑**：`pytest tests/test_phase_28_2_security_regression.py -p no:cacheprovider`（SSRF/ lab 隔离/ capability）。

**8. 不应重新启用的 legacy API**：`AcquisitionService.create_and_run`（@deprecated，PENDING 同步，非持久）；无 `run-now`/`execute-now` 端点。

**9. 看似可简化但**不能**简化**：
- `cancel_aware` 的**独立连接轮询**不能去掉（跨进程取消 + SQLite 快照隔离规避都靠它）。
- `_finalize_cancelled` 的**独立 session** 不能改为复用 worker session（可能已 rollback/损坏）。
- `claim.py` 的 CAS `UPDATE ... WHERE status IN (...)` 不能改成先 SELECT 再 UPDATE（并发下失效）。
- fencing token **只存 hash** 不能存明文。
- `acquisition_runs.checkpoint` JSON 不能改回 lazy relationship（异步 worker 操作内禁止触 lazy）。

---

## V. OPEN QUESTIONS

1. acquisition 路由的认证/RBAC 依赖在哪一层挂载？（`acquisition.py` 未见 auth 依赖，README 称全平台 RBAC——是外层中间件还是未实现？）
2. 生产 worker 守护进程打算如何部署？（独立进程？与 API 同进程？K8s Job？当前无任何接线。）
3. `AcquisitionWorkerLoop` 是否已计划在 `tick()` 中接入 `reclaim_expired`？还是另有恢复调度？
4. acquisition 表的 alembic 迁移计划何时生成？（影响部署）
5. Redis 在 acquisition 路径的角色是什么？（README 提 Redis，但持久队列用 DB；是否有计划用 Redis 做取消信号/队列？）
6. 真实 OS 隔离 sandbox provider（OCI/gVisor/microVM）的实现计划与 owner？
7. `AcquisitionStatus` 枚举是否要补充 `CANCELLED`（统一状态比较）？
8. `RunTracker`/`LoopStats` 是否计划接 Prometheus/OTel？指标导出格式？
9. cancel 轮询的独立连接模式在 PostgreSQL 高并发下是否需要改为 listen/notify 或 lease heartbeat 驱动？
10. `AcquisitionService` 构造函数默认 `store_root=outputs/acquisition-objects`——生产对象存储应接哪（S3/MinIO）？当前 `LocalFilesystemEvidenceStore` 是否生产适用？

---

## W. AI HANDOFF SNAPSHOT

**CAP（Cyber Agent Platform）是 FastAPI/SQLAlchemy/PostgreSQL 企业安全编排控制平面。本文档聚焦其 Acquisition（受控数据获取）子系统，当前处于 Phase 28.2「Durable Execution」完成态（VERSION 1.0.0-rc1，代码与 46 个测试已落地）。**

**架构**：API 只做 enqueue——`POST /acquisitions` 把 run 以 `QUEUED` 写入 `acquisition_runs` 表并返回 202，绝不直跑、绝不 `create_task`。执行完全交给独立的 `AcquisitionWorkerLoop`：它从 DB 持久队列（`_next_batch` 直接 SELECT 表，无内存队列）有界轮询（poll_interval 防忙等、batch_size 限流），对每个 run 调 `AcquisitionClaimCoordinator.claim()` 做**原子 CAS**（`UPDATE ... WHERE status IN (QUEUED,CANCEL_REQUESTED)`，`rowcount==1` 即唯一赢家），绑定 `WorkerLeaseManager` 租约 + fencing token（**明文只存 sha256**）。获胜后 `runner=AcquisitionWorkerPath.run_claimed()` 在 Worker/Sandbox 边界执行：先 `verify_owner`（Fencing Critical Gate），再经 `PluginWorkerRuntime→WorkerRuntime→SandboxRuntime→MemorySandboxProvider` 跑 `AdaptiveDataAcquisitionAgent.acquire`（HTTP 走 `HTTPAdapter`、动态页走 `PlaywrightAcquisitionAdapter`，均强制 SSRF + 沙箱策略），结果 `_apply_payload`+commit。

**状态机**：QUEUED→RUNNING→(COMPLETE|PARTIAL|BLOCKED|FAILED|CANCELLED)；CANCEL_REQUESTED 为过渡态。Worker 生命周期由 `state_machine.validate_transition` 严格约束；Lease 由 ACTIVE→RELEASED/EXPIRED（version+fencing CAS）。

**取消（重点）**：`cancel()` 先把 run durably 置 CANCEL_REQUESTED（已 claim 时），再 terminate 实时 sandbox；worker 侧 `cancel_aware` 用**独立连接**每 50ms 轮询该标志，发现即 `operation_task.cancel()`+rollback，最终由 `_finalize_cancelled` 用**独立 session** 写 CANCELLED——保证「先请求→终止→定稿」，CANCELLED 后零证据写入，且跨进程可见。

**Checkpoint/Resume/Idempotency**：续跑状态存 `acquisition_runs.checkpoint` JSON（page_number/visited_urls/records_seen/evidence_refs），`requeue()` 保留游标从页 N 续跑不从 1；`idempotency_key` 唯一约束 + `request_fingerprint`，跨 session 幂等，冲突请求 409。

**Security boundaries**：SSRF（`URLPolicyValidator` 拦私有/loopback/metadata/DNS-rebinding，重定向再校验）为必经 Critical Gate；Sandbox policy fail-closed；Playwright 禁 cookie/header/credential/proxy；lab validator（`allow_private=True`）与生产的隔离；fencing 拒陈旧写入；worker capability 不可绕过；API 无 bypass/run-now/create_and_run 端点。

**测试**：76 个 acquisition 测试（28.2: 46；28.1: 30）。核心 6 套件 44 全绿；含 500-run 基准（零丢失零重复）、并发 claim（≥10 连接争 1）、陈旧 fencing 拒绝、取消零证据、真实 Chromium 回收。

**关键 invariant**：DB 即真相；每 epoch 唯一 owner（CAS）；fencing 明文不落库；stale 不可提交；CANCELLED 前必终止 operation 且零证据；checkpoint 与 run 同行事务；生产 SSRF 不被 lab 污染；浏览器 context 必释放。

**当前 technical debt（务必在意）**：① `AcquisitionWorkerLoop.tick()` **未调用 `reclaim_expired`**——RUNNING+过期租约的 run 会卡住（recovery 在 coordinator 层有但未接线）；② acquisition 表**无 alembic 迁移**（测试靠 create_all，生产 `alembic upgrade head` 不会建表）；③ **无 worker 守护进程**——loop 已实现但无进程启动它，run 创建后无进程执行；④ `AcquisitionStatus` 枚举缺 CANCELLED（状态字面量游离）。

**下一阶段起点**：(a) 在 `tick()` 接入 `reclaim_expired`（过期 RUNNING→重入队/ reclaim，递增 recovery_count）；(b) 生成 acquisition 表 alembic 迁移；(c) 实现 worker 守护进程（实例化 `AcquisitionWorkerLoop`，`runner=lambda rid,tok: wp.run_claimed(rid, worker_id, tok)`，注册 worker，`run_forever()`）；(d) 接 `RunTracker`/LoopStats 到 metrics；(e) PostgreSQL 端到端实测 reclaim/cancel；(f) 视需补充 `CANCELLED` 到枚举。

---

## X. FACT CHECK

自检（基于代码，非设计文档）：

1. **是否把设计文档当代码事实？** 否。状态机/字段/CAS/取消流程均直接读 `service.py`/`claim.py`/`claim_loop.py`/`worker_path.py`/`models_db.py`/`acquisition.py` 验证。报告数字（44/46）与代码 test 计数一致。
2. **路径不存在？** 已读全部引用文件，路径均存在（除 `backend/Makefile` 在仓库根而非 backend/，已修正）。
3. **函数/class 名写错？** `AcquisitionClaimCoordinator`、`AcquisitionWorkerLoop`、`AcquisitionWorkerPath`、`WorkerLeaseManager`、`URLPolicyValidator`、`MemorySandboxProvider`、`BrowserManager` 等均按源码。
4. **测试数字与仓库不一致？** 实测 28.2=46、6 核心=44、28.1=30，与报告一致。
5. **状态值非实际 enum？** 已指出 `CANCELLED` 是字符串而非 `AcquisitionStatus` 枚举成员（真实情况，非错误）。
6. **SQLite 结果误推 PostgreSQL？** 已显式标注：并发/fencing 在 SQLite 多连接验证，PostgreSQL 下 CAS 等价但隔离细节需实测；取消正确性靠"独立连接读已提交"这一代码层保证（两库成立）。
7. **at-least-once 误写 exactly-once？** 已区分：per-run 终态唯一（fencing + idempotent requeue），但单次 run 可能因 reclaim 执行两次（同一 run_id 续跑），非 exactly-once side effect。
8. **混淆 exactly-one claim owner 与 exactly-once side effect？** 已澄清：每 epoch 唯一 owner（CAS）；side effect 非 exactly-once。
9. **所有 stale-sensitive durable write 都过 fencing？** 是：`run_claimed` 入口与提交前各 `verify_owner`；`WorkerRuntime._commit_result` 也带 leasing fencing。已确认。
10. **cancellation 终态真发生在 operation 终止之后？** 是：`cancel_aware` 在 `operation_task.cancel()` 之后才 `_finalize_cancelled`；提交前再读 cancel 标志。已确认。
11. **未检查 bypass path？** 已枚举 API 路由（无 create_and_run/run-now/bypass），`execute()` legacy 无路由调用。已确认。
12. **新增发现（文档内已修正）**：`tick()` 未接 `reclaim_expired`、acquisition 表无迁移、无 worker 守护进程——均在 Q/R/S/W 中明确为 Confirmed gap，未掩盖。

**最终结论**：本文档以当前工作树代码为事实来源，核心执行链/状态机/fencing/取消/checkpoint/安全边界均与代码一致；唯一需重点交接的"未完成接线"已如实标注（reclaim 接线、迁移、worker 守护进程）。
