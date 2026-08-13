# GitHub Reference Analysis — Phase 3

## LangGraph

- 参考模块：StateGraph、Node、普通/条件 Edge、Checkpointer、durable execution。
- 采用：Definition/Runtime 分离、节点插件、条件边、每步 checkpoint、恢复语义。
- 未采用：循环图、Pregel superstep、Channel/Reducer、Memory、LLM Agent 模板。
- 原因：CAP Phase 3 明确采用可验证 DAG，禁止 Memory/LLM，当前不需要通用图计算运行时。

## OpenAI Agents SDK

- 参考模块：Runner、RunContext、Tool Call、Handoff、Tracing。
- 采用：集中 Runner/Runtime、显式上下文、执行边界和统一结果模型。
- 未采用：LLM 驱动 Tool Call/Handoff、Sessions、模型 Provider。
- 原因：CAP 调度必须由 Capability Registry、权限和确定性策略驱动，禁止引入 LLM。

## Temporal

- 参考模块：Workflow、Activity、Retry Policy、Event History、durable recovery。
- 采用：Workflow 与执行单元分离、Attempt 历史、重试/超时策略、持久化恢复。
- 未采用：完整 deterministic replay、Temporal Server/Worker 协议、长轮询和分布式 history service。
- 原因：Phase 3 先建立平台内最小可恢复引擎，直接引入 Temporal 会改变部署和一致性架构。

## Argo Workflows

- 参考模块：Workflow Spec、DAG、Task dependency、Retry Strategy、Template。
- 采用：声明式 YAML、DAG 校验、节点级重试/超时、Definition 与 Instance 分离。
- 未采用：Kubernetes CRD、Pod-per-step、artifact repository、controller reconciliation loop。
- 原因：CAP Runtime 已有 Agent/Tool 执行边界，不应把控制面绑定到 Kubernetes。

## Shuffle（SOAR）

- 参考模块：Security Workflow、App/Action Node、Automation、Worker execution。
- 采用：安全流程编排、节点能力插件、Workflow 与执行资源解耦的产品方向。
- 未采用：完整 SOAR App Store、触发器生态、案例管理、第三方安全集成。
- 原因：本阶段只建设平台大脑，不新增安全 Agent、连接器或 SOAR 业务能力。

## 综合决策

CAP 采用“DAG Definition + durable checkpoint + Capability-first dispatch + plugin Node Handler”。Definition 不保存 Agent 身份；Runtime 不执行任意代码；失败、超时、重试、取消和恢复均写入数据库并发布审计事件。
