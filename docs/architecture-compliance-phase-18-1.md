# Phase 18.1 Architecture Compliance Report

- 项目：Cyber Agent Platform（CAP）
- 阶段：Phase 18.1 Worker & Sandbox Production Consistency
- 结论：Phase 18.1 修复范围满足实现级检查；真实生产隔离和在线数据库运维项保留为环境/后续 Provider 证据。
- Phase 19：未进入。

## Compliance Matrix

| 检查项 | 证据 | 结果 |
|---|---|---|
| Database 是 Worker 唯一 Source of Truth | `backend/app/repositories/worker.py`、`registry.py`、ADR-0039、缓存清空后重新读取测试 | PASS |
| Memory 只能作为可失效 Cache | Registry 写路径走 Repository；专项测试清空 `_cache` 后从 DB 读取 | PASS |
| Worker 严格状态机 | `backend/app/worker/state_machine.py`；非法跳转测试 | PASS |
| Worker 状态 CAS | `workers.state_version`、Repository 条件更新、心跳版本测试 | PASS |
| Lease 持久化字段 | `worker_leases.fencing_token`、`version`、迁移 0017 | PASS |
| Fencing Token 唯一且不可复用 | UUID、唯一索引、Renew 后旧 Version/Token 拒绝测试 | PASS |
| Result Commit 校验 | Lease ID、Owner、Token、Version、ACTIVE、Expiry、RUNNING 全量检查 | PASS |
| Sandbox Execution 全状态持久化 | RUNNING、FAILED、RECOVERED/终态记录与恢复关联测试 | PASS |
| Secret fail closed | `SecretNotFound`、`SecretPolicyViolation` 不静默返回空值；失败审计测试 | PASS |
| Manifest V1 兼容 | 正式 `plugins/**/manifest.yaml` 8/8 通过 V1 Loader | PASS |
| Manifest V2 严格边界 | `extra="forbid"` 和未知字段拒绝测试 | PASS |
| Provider Capability Contract | `SandboxProviderCapability` 与执行前 Capability Enforcement | PASS |
| Worker/Lease/Sandbox/Secret Audit | Event Contract、Transactional Audit、专项审计覆盖测试 | PASS |
| 迁移单一 Head | `alembic heads` 输出 `20260802_0017 (head)`；ScriptDirectory 测试 | PASS |
| 迁移范围保护 | 0017 仅涉及 workers、worker_leases、sandbox_executions；禁止领域表断言 | PASS |
| 升级 SQL 可生成 | Alembic offline `upgrade head --sql` 成功 | PASS |
| 降级 SQL 可生成 | Alembic offline `downgrade 0017:0016 --sql` 成功 | PASS |
| 真实在线 PostgreSQL upgrade/downgrade | Docker/PostgreSQL 服务当前不可用 | BLOCKED / NOT CLAIMED |
| 五领域兼容回归 | Assessment/Detection/Telemetry/Response/Notification：52 passed | PASS |
| 后端全量回归 | 按 `backend/pyproject.toml`：250 passed | PASS |
| Ruff | `backend/app`, `backend/tests`, `backend/alembic` | PASS |
| Black | 333 files unchanged | PASS |
| compileall | app/tests/alembic | PASS |
| 应用覆盖率 | `12909 statements / 644 missed / 95.0112%`，`--fail-under=95` 成功 | PASS |

## 数据库边界

迁移 `20260802_0017_worker_consistency.py` 只新增 Worker Control Plane 所需字段、索引和 Lease 外键：

- `workers.state_version`
- `worker_leases.fencing_token` 与唯一索引
- `sandbox_executions.lease_id`
- `sandbox_executions.lease_version`
- `sandbox_executions.attempt`
- `sandbox_executions.recovery_of_execution_id`

未修改 Assessment、Detection、Response、Incident 业务表。

## 评审结论

实现层 Critical/Major 修复证据齐全。Architect 需要重点裁决：是否接受在线 PostgreSQL 升降级在当前环境为 BLOCKED，以及是否接受 `MemorySandboxProvider.real_isolation = false` 作为本阶段明确的非生产隔离边界。