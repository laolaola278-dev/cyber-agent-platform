# Phase 20 Playbook Security Boundary, Safety Case and Trade-off Analysis

## 1. Security Boundary Analysis

```text
Manual / incident.created Trigger
              |
        PlaybookService
              |
Strict YAML -> Registry -> Planner -> Policy
              |
        Durable Runtime
              |
       Capability Executor
       /      |       \
Assessment Detection Response Notification/Ticket
              |
      existing domain authority
```

Playbook Plane 只拥有自己的定义、版本、Trigger、Execution 和 Step History 表。它没有直接写 Assessment、Detection、Response 或 Incident 业务表的 Repository；Executor 只通过既有领域 Service 及其 Typed Request 调用能力。Incident Service 仍拥有 Incident；Playbook 只订阅平台 `incident.created` 事件。

DSL 经 `yaml.safe_load` 和 `extra=forbid` Typed Model 解析。Condition 只解释白名单 AST 节点；禁止 `eval`、`exec`、函数调用、属性访问、动态导入、Shell、文件写入和任意代码生成。Context reference 只能沿字典键解析。

Policy 分两层：平台 Policy 约束 runner、approver、plugin、capability、timeout、retry、parallel；Playbook 文档再声明自身 allowlist。缺失、未知、保留或越界输入一律拒绝。

Approval 不是 Plugin 或 Playbook 自行决定的布尔值。Playbook Approval Step 由平台授权 approver，Runner/Approver 必须不同；Response Node 仍必须经过 Response Framework 的权威 Approval API 后才能执行。

## 2. Safety Case Analysis

| Hazard | Prevention | Detection | Recovery |
|---|---|---|---|
| 任意代码执行 | Safe YAML、严格 Model、安全 AST、无 Script Node | DSL 校验与专项安全测试 | 拒绝 Playbook，不产生 Execution |
| 未授权高权限响应 | Capability allowlist、平台 runner/approver、Response Approval | Execution/Audit 事件、Response 状态 | 停止并按显式 compensation rollback |
| 重复触发 | 全局 idempotency key；incident event + trigger 稳定键 | 查询返回原 Execution | 不重复创建或执行副作用 |
| 审批者与执行者相同 | Service 强制 distinct actor | Resume/Run fail closed | 更换授权 approver 后 resume |
| 审批无限等待 | Step 与 Playbook deadline 持久化 | Resume 时检查 elapsed time | 标记 TIMED_OUT 并补偿已完成 Step |
| Step 重试扩大副作用 | 有界 retry，Step 输出/checkpoint，领域服务继续负责幂等 | Attempt、error、status 持久化 | 终止并补偿；生产连接器需领域幂等键 |
| 部分成功 | 每 Step checkpoint，逆序显式补偿 | Execution/Step History | Response rollback、Notification ignore、Ticket close |
| 补偿失败 | 补偿独立 try/catch 与状态 | `COMPENSATION_FAILED` + output error | 人工 Runbook，不宣称已恢复 |
| 保留功能被误启用 | Reserved Trigger/Node 在 DSL 校验拒绝 | 创建 API 返回验证错误 | 新 ADR/版本后才可启用 |
| 直接修改 Incident | Playbook 无 Incident Repository/Service 写路径 | 代码扫描和迁移审计 | 由 Incident 平台流程处理事件结果 |

## 3. Architecture Trade-off Analysis

1. **声明式 DSL vs 通用脚本：** 选择可静态验证的安全子集，牺牲任意扩展性，避免第二执行平面。
2. **顺序执行 vs DAG/Parallel：** 选择 `max_parallel=1`，降低竞态、审批歧义与补偿复杂度；吞吐量较低。
3. **领域 Service 复用 vs 直接数据库写入：** 选择 Service 权威，保留领域 Policy/Audit；调用开销更高但边界清晰。
4. **持久化 checkpoint vs 内存工作流：** 选择五张表保存定义与历史，支持恢复和审计；增加 Migration 与运行维护成本。
5. **显式补偿 vs 分布式事务：** 外部副作用无法 ACID 回滚，因此采用 Saga；补偿可能失败，必须显式暴露。
6. **不可变版本 vs 原地编辑：** Execution 固定 Version/Checksum，保证历史可解释；更新需要新版本流程。
7. **事件订阅 vs Incident 直接调用：** 采用事件总线解耦领域；Phase 20 的 request-scoped in-memory bus 不是生产 durable broker。

## 4. 最终安全结论

Phase 20 证明 CAP 可以在不复制安全能力、不绕过 Approval、且不提供任意代码执行的前提下编排既有领域能力。它不证明分布式多副本恢复、持久事件投递、生产负载和真实 PostgreSQL 往返部署；这些必须作为上线前门禁，而不能由配置豁免。
