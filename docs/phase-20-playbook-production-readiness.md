# Phase 20 Playbook Production Integration Readiness

## 1. Status

Phase 20 的功能与应用测试门禁已通过，但仍是 **Architect Review Ready，不等于 Production Ready**。当前实现使用请求级 `InMemoryEventBus`、单进程运行路径和顺序执行；真实 PostgreSQL 容器未在本机启动，因此完整历史 Migration 的在线 `upgrade -> downgrade -> upgrade` 尚需在集成环境执行。

## 2. Production entry gates

上线前必须完成：

1. 在 PostgreSQL 16 隔离数据库执行全链 `alembic upgrade head`。
2. 验证五张 Playbook 表、外键、检查约束、唯一约束与索引。
3. 执行 `alembic downgrade 20260802_0017`，确认仅删除 Phase 20 五表。
4. 再次 `alembic upgrade head` 并运行 API smoke test。
5. 使用持久 Event Bus/Outbox 保证 `incident.created` 至少一次投递，并依赖幂等键去重。
6. 明确多副本执行 claim/lease/fencing；禁止两个 Runtime 并发推进同一 Execution。
7. 配置真实 IAM：runner、approver、Playbook author/publisher 权限分离。
8. 为高权限 Capability 建立独立 allowlist、审批矩阵和变更审计。
9. 完成负载、故障注入、数据库 failover、进程崩溃恢复和补偿演练。
10. 由 Architect/SOC/DBA/SRE 共同签署 Release Readiness Checklist。

## 3. DSL version and upgrade guide

当前 DSL 唯一版本为 `v1`。Playbook Version 保存原始 YAML、规范化 JSON、DSL Version 与 SHA-256 checksum；已运行 Execution 固定到创建时 Version。

升级规则：

- 兼容修改只能放宽非安全展示字段，不能静默改变执行语义。
- 新 Trigger/Node/Condition 操作符必须发布新 DSL 版本并保留 v1 parser。
- 不得原地修改已持久化 Version 的 YAML/document/checksum。
- 发布流程：disabled create -> validate -> test execution -> bounded enable。
- 回滚应用版本时必须保留读取旧 Version 与 Execution History 的能力。

## 4. Migration upgrade and rollback guide

升级：

```text
1. 备份 PostgreSQL 并记录当前 revision。
2. 停止创建新 Playbook Execution，等待 RUNNING/COMPENSATING 清空。
3. alembic upgrade head
4. 检查 head == 20260803_0018。
5. 检查五表、约束和索引。
6. 启动服务，创建 disabled smoke Playbook 并手动运行。
```

回滚：

```text
1. 禁止新执行并导出 Playbook/Version/Execution/Step History。
2. 确认没有 RUNNING、WAITING_APPROVAL、COMPENSATING Execution。
3. 回滚应用到不引用 Playbook 模型/API 的版本。
4. alembic downgrade 20260802_0017
5. 验证其他业务表未变化。
```

Downgrade 会永久删除五张 Playbook 表及其历史，必须先备份并取得 Architect/DBA 明确批准。外部 Response/Notification/Ticket 副作用不会因删表自动撤销。

## 5. Runbook

### Execution stuck RUNNING

- 查询 Execution/current_step/attempt/started_at；确认是否有活跃 Worker。
- 在没有 lease/claim 机制的 Phase 20 不自动由第二副本接管。
- 保护数据库快照，人工决定恢复或补偿，不直接改业务表。

### WAITING_APPROVAL

- 验证 approver 在平台 allowlist 且不同于 runner。
- 检查 Step/Playbook deadline；过期后不得强行 resume。
- 通过 Resume API 提交审批，保持原 input 与原 Version。

### TIMED_OUT / FAILED

- 检查 Step error、attempt 和 compensation_status。
- Response 核对 rollback evidence；Ticket 核对 CLOSED；Notification 明确为 IGNORED。
- 若 `COMPENSATION_FAILED`，升级给对应领域 owner，不能将 Execution 人工标记成功。

### Duplicate incident trigger

- 核对 `incident:{event.id}:{trigger.id}` idempotency key。
- 若同键属于其他 Playbook，视为数据冲突并停止自动化。

### Migration failure

- 在事务/备份策略允许时回到先前 revision。
- 不修改历史 Migration 迎合 SQLite；生产方言为 PostgreSQL。
- 收集 Alembic revision、DDL、数据库错误和表清单。

## 6. Monitoring metrics

至少暴露以下指标并按 Playbook/Trigger/Node/Capability 维度聚合，避免在 label 中写入敏感 input：

- `playbook_execution_total{status,trigger_type}`
- `playbook_execution_duration_seconds`
- `playbook_step_total{node_type,status}`
- `playbook_step_duration_seconds{node_type}`
- `playbook_step_retry_total{node_type}`
- `playbook_waiting_approval_total`
- `playbook_approval_wait_seconds`
- `playbook_timeout_total{scope}`
- `playbook_compensation_total{node_type,status}`
- `playbook_trigger_match_total{trigger_type}`
- `playbook_idempotency_replay_total`

告警建议：`COMPENSATION_FAILED > 0` 立即告警；WAITING_APPROVAL 超过阈值、TIMED_OUT/FAILED 比例突增、incident trigger 无消费、Execution 长时间 RUNNING 均告警。

## 7. Known limitations

1. 仅 `manual`、`incident.created` Trigger；其余 fail closed。
2. Delay/Parallel 不可执行，`max_parallel=1`。
3. 无通用 DAG、循环、脚本、动态连接器或任意代码节点。
4. Event Bus 为 request-scoped in-memory，不保证跨进程 durable delivery。
5. 尚无多副本 execution claim/lease/fencing 与崩溃自动接管。
6. Notification 补偿只能 Ignore，已发送消息无法撤回。
7. 真实 PostgreSQL 全历史 Migration 往返未在本机执行；离线 PostgreSQL DDL 与单一 head 门禁已通过。
8. Phase 20 API 身份目前使用平台既有 actor 注入方式，生产 IAM/RBAC 需集成环境配置。
