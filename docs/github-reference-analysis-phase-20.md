# Phase 20 SOAR Playbook Engine Architecture Reference Analysis

## 1. 目的与边界

本分析为 Phase 20 的 Playbook Engine 提供架构基准。只吸收成熟产品关于声明式编排、Trigger、Action/Node、Execution History、Retry、人工审批和补偿的设计原则；CAP 不引入任何外部 SOAR、工作流服务、脚本运行时或新安全能力。

## 2. Shuffle SOAR

Shuffle 将 Playbook/Workflow、Trigger 和 Action/App 组合为自动化流程，并提供应用集成、执行记录与 IAM。CAP 采用其“流程定义与动作能力分离”的思路：Playbook 只声明既有 Capability，Executor 只调用 Assessment/Detection/Response/Notification Service。CAP 不采用用户自定义 Python/Script Action，避免在 Playbook 中形成任意代码执行路径。

## 3. StackStorm / Orquesta

StackStorm 以 Sensor/Trigger、Rule、Action 和 Orquesta Workflow 组织事件驱动自动化，并强调执行事件与审计。CAP 采用 `incident.created` 事件订阅、类型化 Trigger、Step History、Retry 和审计事件；不采用通用 fork/join、Shell action 或任意 Action provider。Phase 20 固定 `max_parallel=1`。

## 4. n8n

n8n 以 Node、Trigger 和 Execution 组织可视化流程，节点输出可供后续节点使用。CAP 采用 typed Node、受限 context reference、逐步输出和 Execution Detail API；不采用任意 JavaScript/Python Code Node、动态属性访问或不受限第三方节点。

## 5. Temporal

Temporal 的核心启发是 durable Workflow History、Worker 与控制面分离、Retry、Timeout、Signal/Resume 和 Saga/Compensation。CAP 采用数据库持久化 Execution/Step checkpoint、审批等待恢复、边界内 Retry/Timeout 与显式逆序 Compensation；不绑定 Temporal Server、Task Queue、Event Sourcing Replay 或 Durable Timer。

## 6. Argo Workflows

Argo 以 Workflow/DAG/Template/Node Status 表达有向执行图，并保留节点状态。CAP 采用 Playbook Version、Step 状态、输入/输出和执行详情，但 Phase 20 只实现确定性的顺序子集；Parallel/DAG 扩展保留并 fail closed。

## 7. CAP 采用结论

| 设计问题 | 参考原则 | CAP Phase 20 |
|---|---|---|
| 编排定义 | 声明式 Workflow/Playbook | YAML DSL v1、严格 Typed Model |
| 触发 | Sensor/Webhook/Manual/Schedule | `manual`、`incident.created` |
| 动作边界 | Action/App/Capability 分离 | 只调用既有领域 Service |
| 条件 | Workflow condition | 安全 AST 子集，禁止调用/属性访问 |
| 执行记录 | Durable execution history | Execution + Step Execution 持久化 |
| 失败 | Retry/Failure/Timeout | 有界 Retry、Step/Playbook Timeout |
| 人工介入 | Approval/Signal | Approval Waiting + Resume |
| 回滚 | Saga/Compensation | Response rollback、Notification ignore、Ticket close |
| 并发 | DAG/Parallel | 固定 `max_parallel=1` |

## 8. 不采用外部运行时

外部产品的通用脚本与连接器扩展适合广泛自动化，但不符合 CAP 高权限安全响应的最小权限边界。Phase 20 保持 Provider-neutral、Capability-only 和 fail-closed；新增能力必须回到对应领域框架并经过 Architect Review。
