# ADR-0042：Playbook 执行采用持久化顺序状态机与显式补偿

- 状态：Accepted for Phase 20
- 日期：2026-08-04

## 背景

安全自动化必须能在失败、超时、审批等待和进程恢复后解释“执行到哪里、做过什么、如何恢复”。仅在内存中串联 Service 调用会丢失 Execution History；把数据库事务扩展到外部 Response/Notification/Ticket 动作既不可行，也无法撤销现实世界副作用。

## 决策

每次 Playbook 运行持久化 `PlaybookExecution`，每个 Step 持久化 `PlaybookStepExecution`。Runtime 固定顺序执行并在关键状态提交 checkpoint，记录状态、Attempt、输入、输出、错误、开始/完成时间和补偿结果。

Runtime 支持有界 Retry、Step Timeout、Playbook Timeout、Skip、Failure、Approval Waiting/Resume。恢复时读取同一不可变 Playbook Version 和已有 Step History；已成功或跳过的 Step 不重复执行。幂等键防止同一触发重复创建执行。

失败或超时后按成功 Step 的逆序执行显式补偿：

- Response → 调用 Response Framework rollback；
- Notification → 记录 Ignore，不尝试撤回已发送消息；
- Ticket → 调用 Notification Service close ticket。

补偿失败被持久化为 `COMPENSATION_FAILED`，不得伪装成原动作已回滚。Approval 由平台 Policy 与 Response Framework 授权；Runner 与 Approver 必须不同。

## 后果

- 执行历史可查询，审批暂停后可在同一 Execution 上恢复。
- 外部副作用采用 Saga 风格补偿，而非不现实的分布式事务。
- 顺序执行牺牲吞吐量，换取确定性、较小爆炸半径和更清晰的审计链。
- Phase 20 的数据库 checkpoint 提供持久状态，但不等同于 Temporal 的完整 Event Sourcing Replay、Durable Timer 或分布式 Task Queue。
- 生产多副本、崩溃自动恢复、Outbox 和租约调度仍需后续独立架构审查。

## 参考

该决策吸收 Temporal 的 durable history/retry/compensation、Argo Workflows 的 DAG execution history，以及 StackStorm/n8n 对每次执行与节点结果的可观察性；Phase 20 只采用单并发顺序子集。
